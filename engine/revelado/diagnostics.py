# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic masks: noise, chromatic aberration, colour shading.

Source image
  RAW present: LibRaw half-size (each 2×2 Bayer block -> one RGB pixel, NO demosaic, no in-camera NR, no in-camera CA
  correction), camera white balance, linear sRGB primaries. This is what the developer will actually see.
  JPG only: the camera JPEG, linearised (S-Log3 decoded when the picture profile is log). In-camera NR and CA
  correction are already baked in, which the stats report as ``source: camera_jpg``.
  Exposure is normalised so the median luminance sits at 0.18 (gain clamped to ×0.25…×16), the same neighbourhood
  the develop push brings the subject to.

All work happens on the half-size image (≈3500 px for 33 MP) or smaller, never on the full frame. Only one mask is
computed at a time (lock), and the last decoded half-size image is kept (float16, ~50 MB) so the three masks of the
same photo decode the RAW once. Output: an 8-bit overlay at <= ``size`` px (long side), oriented for display, with a
legend strip, plus a stats dict (sent as the X-Mask-Stats header).

Colour maps are colour-blind safe: cividis for magnitudes; Okabe-Ito reddish purple (#CC79A7) and bluish green
(#009E73) for purple / green fringes and magenta / green casts.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .errors import ReveladoError
from .imaging import SGAMUT3CINE_TO_709, _SLOG3_LUT, srgb_encode
from .log import get_logger

log = get_logger("diagnostics")
F32 = np.float32
KINDS = ("noise", "ca", "shading")
VERSION = "1"
PURPLE = np.array([167, 121, 204], np.uint8)     # #CC79A7 as BGR
GREEN = np.array([115, 158, 0], np.uint8)        # #009E73 as BGR
_ROT = {3: 2, 6: 3, 8: 1}                          # EXIF orientation -> np.rot90 k (counter-clockwise turns)


# ------------------------------------------------------------------------------------------------ colour helpers
def lin_srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                   [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]], F32)
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                   [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]], F32)
    lms = np.cbrt(np.maximum(rgb @ m1.T, 0.0))
    return lms @ m2.T


def _orient(img: np.ndarray, o: int) -> np.ndarray:
    if o in _ROT:
        img = np.rot90(img, _ROT[o])
    elif o == 2:
        img = img[:, ::-1]
    elif o == 4:
        img = img[::-1]
    elif o == 5:
        img = np.transpose(img, (1, 0, 2)) if img.ndim == 3 else img.T
    elif o == 7:
        img = np.rot90(np.transpose(img, (1, 0, 2)) if img.ndim == 3 else img.T, 2)
    return np.ascontiguousarray(img)


def _fit(img: np.ndarray, size: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = size / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def _grey_base(lin: np.ndarray, size: int) -> np.ndarray:
    """Dimmed, desaturated display of the image (BGR uint8) so overlays read clearly."""
    small = _fit(lin, size)
    y = small @ np.array([0.2126, 0.7152, 0.0722], F32)
    g = (srgb_encode(np.clip(y, 0, 1)) * 255 * 0.55 + 25).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def _legend(img: np.ndarray, label: str, vmin: float, vmax: float, cmap: int | None = None,
            stops: list[tuple[np.ndarray, str]] | None = None) -> np.ndarray:
    """Append a 34-px legend strip below the overlay (gradient bar or swatches)."""
    h, w = img.shape[:2]
    strip = np.full((34, w, 3), 18, np.uint8)
    x0, x1 = 10, min(w - 10, 10 + max(160, w // 3))
    if cmap is not None:
        bar = np.tile(np.linspace(0, 255, x1 - x0).astype(np.uint8), (12, 1))
        strip[4:16, x0:x1] = cv2.applyColorMap(bar, cmap)
        cv2.putText(strip, f"{vmin:g}", (x0, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 220), 1, cv2.LINE_AA)
        cv2.putText(strip, f"{vmax:g}", (x1 - 28, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 220), 1, cv2.LINE_AA)
        cv2.putText(strip, label, (x1 + 12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1, cv2.LINE_AA)
    else:
        x = x0
        for col, name in stops or []:
            strip[6:22, x:x + 16] = col
            cv2.putText(strip, name, (x + 22, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1, cv2.LINE_AA)
            x += 30 + 9 * len(name)
        cv2.putText(strip, label, (x + 8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
    return np.vstack([img, strip])


# ------------------------------------------------------------------------------------------------ source decoding
class _Source:
    def __init__(self):
        self.key: str | None = None
        self.img: np.ndarray | None = None   # float16 linear sRGB, normalised exposure
        self.info: dict = {}


_lock = threading.Lock()
_src = _Source()


def _load_linear(photo: dict, jpeg_path: Path | None) -> tuple[np.ndarray, dict]:
    meta = photo.get("meta") or {}
    raw = photo.get("raw_path")
    info: dict = {}
    if raw and Path(raw).exists():
        import rawpy
        try:
            with rawpy.imread(raw) as r:
                rgb = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True, output_bps=16,
                                    gamma=(1, 1), output_color=rawpy.ColorSpace.sRGB, user_flip=0)
        except Exception as e:  # noqa: BLE001
            raise ReveladoError("E_RAW_DECODE", f"LibRaw no pudo leer el RAW ({type(e).__name__})") from e
        lin = rgb.astype(F32) / F32(65535.0)
        del rgb
        info.update(source="raw_half_size", note="RAW a 1/2 (bloques 2×2, sin demosaico ni NR de cámara)",
                    px_scale=2.0)
    elif jpeg_path is not None:
        bgr = cv2.imread(str(jpeg_path), cv2.IMREAD_REDUCED_COLOR_2 | cv2.IMREAD_IGNORE_ORIENTATION)
        if bgr is None:
            raise ReveladoError("E_RAW_DECODE", "no se pudo leer el JPG")
        rgb8 = bgr[..., ::-1]
        if meta.get("picture_profile") in ("s-log3", "s-log2"):
            lin = _SLOG3_LUT[rgb8] @ SGAMUT3CINE_TO_709.T
        else:
            x = rgb8.astype(F32) / 255
            lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(F32)
        info.update(source="camera_jpg", note="JPG de cámara (ya lleva NR y corrección de CA de cámara)", px_scale=2.0)
    else:
        raise ReveladoError("E_NOT_FOUND", "sin archivo fuente")
    y = lin[::4, ::4] @ np.array([0.2126, 0.7152, 0.0722], F32)
    med = float(np.median(y))
    gain = float(np.clip(0.18 / max(med, 1e-5), 0.25, 16.0))
    lin *= F32(gain)
    info["exposure_gain_ev"] = round(float(np.log2(gain)), 2)
    info["half_size"] = list(lin.shape[1::-1])
    return lin.astype(np.float16), info


def _get_source(photo: dict, jpeg_path: Path | None) -> tuple[np.ndarray, dict]:
    raw = photo.get("raw_path") or ""
    k = f"{photo['id']}:{raw}:{jpeg_path}"
    if _src.key != k:
        _src.img, _src.info, _src.key = None, {}, None
        img, info = _load_linear(photo, jpeg_path)
        _src.img, _src.info, _src.key = img, info, k
    return _src.img.astype(F32), dict(_src.info)


# ------------------------------------------------------------------------------------------------ noise
def noise_mask(lin: np.ndarray, size: int, info: dict) -> tuple[np.ndarray, dict]:
    """Local noise σ in OkLab units ×100.

    1. OkLab of the exposure-normalised linear image.
    2. Edge-aware smoothing of L with a guided filter (self-guided, r=4, eps=0.02²): it removes noise but keeps edges.
    3. Residual r = L − smooth(L); local σ = sqrt(box15(r²)). Chroma σ likewise on a/b, guided by L.
    4. Texture/edges (gradient of the smoothed L > max(0.012, 2.5×median)) are excluded: there the residual is
       detail, not noise. They are left grey in the overlay and out of the stats.
    5. Stats on flat pixels per tonal band; target 0.45 is the legacy clean.py luma NLM target.
    """
    lab = lin_srgb_to_oklab(np.clip(lin, 0, None))
    L = np.ascontiguousarray(lab[..., 0])
    gf = cv2.ximgproc.guidedFilter
    sL = gf(L, L, 4, 0.02 ** 2)
    rL = L - sL
    sigL = np.sqrt(cv2.boxFilter(rL * rL, -1, (15, 15))) * 100
    ra = lab[..., 1] - gf(L, np.ascontiguousarray(lab[..., 1]), 4, 0.02 ** 2)
    rb = lab[..., 2] - gf(L, np.ascontiguousarray(lab[..., 2]), 4, 0.02 ** 2)
    sigC = np.sqrt(cv2.boxFilter(ra * ra + rb * rb, -1, (15, 15))) * 100
    del ra, rb, rL
    gsm = cv2.GaussianBlur(sL, (0, 0), 1.5)
    grad = np.hypot(cv2.Sobel(gsm, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gsm, cv2.CV_32F, 0, 1, ksize=3)) / 8
    thr = max(0.012, 2.5 * float(np.median(grad)))
    texture = cv2.dilate((grad > thr).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    flat = ~texture & (L > 0.08) & (L < 0.97)
    st: dict = {"unit": "OkLab L ×100 (σ local)", "target_luma": 0.45, "flat_fraction": round(float(flat.mean()), 3)}
    if flat.any():
        v = sigL[flat]
        st.update(luma_sigma_median=round(float(np.median(v)), 3), luma_sigma_p90=round(float(np.percentile(v, 90)), 3),
                  chroma_sigma_median=round(float(np.median(sigC[flat])), 3))
        for name, lo, hi in (("shadows", 0.0, 0.45), ("midtones", 0.45, 0.75), ("highlights", 0.75, 1.01)):
            m = flat & (L >= lo) & (L < hi)
            st[f"luma_sigma_{name}"] = round(float(np.median(sigL[m])), 3) if m.sum() > 500 else None
        med = st["luma_sigma_median"]
        st["verdict"] = "bajo" if med < 0.45 else "moderado" if med < 1.0 else "alto"
        st["verdict_en"] = {"bajo": "low", "moderado": "moderate", "alto": "high"}[st["verdict"]]
    vmax = 2.0
    base = _grey_base(lin, size)
    h, w = base.shape[:2]
    sig_s = cv2.resize(sigL, (w, h), interpolation=cv2.INTER_AREA)
    flat_s = cv2.resize(flat.astype(F32), (w, h), interpolation=cv2.INTER_AREA) > 0.5
    col = cv2.applyColorMap(np.clip(sig_s / vmax * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_CIVIDIS)
    out = base.copy()
    out[flat_s] = (0.25 * base[flat_s] + 0.75 * col[flat_s]).astype(np.uint8)
    st["legend"] = {"cmap": "cividis", "min": 0, "max": vmax, "unit": "σ OkLab L×100", "grey": "textura/bordes (excluido)"}
    return out, st, [("ruido (gris = textura)", 0, vmax, cv2.COLORMAP_CIVIDIS, None)]


# ------------------------------------------------------------------------------------------------ chromatic aberration
def ca_mask(lin: np.ndarray, size: int, info: dict) -> tuple[np.ndarray, dict]:
    """Lateral CA (R/B vs G misregistration) per tile + axial (purple/green) fringe pixels.

    Lateral: the frame is split in a 16×10 grid. In tiles with enough edge energy, the gradient magnitude of R and B
    (gamma 1/2.2 to equalise contrast) is phase-correlated against G (Hann window). The constant part of the shift
    (Bayer sampling offset of the half-size image, identical in every tile) is removed by subtracting the median
    vector; what remains is projected on the radial direction from the image centre: lateral CA is radial.
    Shifts are reported in SENSOR pixels (×2 for the half-size source).

    Axial: OkLab of the display-encoded image. A pixel is a fringe candidate when it is within ~7 px of a
    high-contrast highlight edge (L > 0.85 next to strong gradient), its hue is in the purple window (290°–345°) or
    green window (110°–165°), its chroma is > 0.05 and exceeds the local median chroma by > 0.03 (protects genuinely
    purple/green objects, the same idea as clean.defringe's deviation from the local reference colour).
    """
    H, W = lin.shape[:2]
    g = np.power(np.clip(lin, 0, 1), 1 / 2.2).astype(F32)
    grads = []
    for c in range(3):
        sm = cv2.GaussianBlur(g[..., c], (0, 0), 1.0)
        grads.append(np.hypot(cv2.Sobel(sm, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(sm, cv2.CV_32F, 0, 1, ksize=3)))
    gx, gy = 16, 10
    th, tw = H // gy, W // gx
    tiles = []
    gmean = float(np.mean(grads[1]))
    win = cv2.createHanningWindow((tw, th), cv2.CV_32F)
    for j in range(gy):
        for i in range(gx):
            sl = (slice(j * th, (j + 1) * th), slice(i * tw, (i + 1) * tw))
            G = grads[1][sl]
            energy = float(G.mean())
            if energy < 1.5 * gmean or float(np.percentile(G, 99)) < 0.15:
                continue
            (rdx, rdy), rresp = cv2.phaseCorrelate(G, grads[0][sl], win)
            (bdx, bdy), bresp = cv2.phaseCorrelate(G, grads[2][sl], win)
            if min(rresp, bresp) < 0.03 or max(abs(rdx), abs(rdy), abs(bdx), abs(bdy)) > 6:
                continue
            tiles.append((i, j, rdx, rdy, bdx, bdy, (i + 0.5) * tw - W / 2, (j + 0.5) * th - H / 2))
    st: dict = {"tiles_measured": len(tiles), "grid": [gx, gy], "unit": "px de sensor"}
    base = _grey_base(lin, size)
    oh, ow = base.shape[:2]
    sc = ow / W
    out = base.copy()
    vmax = 2.0
    if len(tiles) >= 4:
        arr = np.array(tiles, F32)
        off = np.median(arr[:, 2:6], axis=0)
        arr[:, 2:6] -= off
        rad = np.hypot(arr[:, 6], arr[:, 7]) + 1e-6
        ux, uy = arr[:, 6] / rad, arr[:, 7] / rad
        r_rad = (arr[:, 2] * ux + arr[:, 3] * uy) * info.get("px_scale", 2.0)
        b_rad = (arr[:, 4] * ux + arr[:, 5] * uy) * info.get("px_scale", 2.0)
        rnorm = rad / np.hypot(W / 2, H / 2)
        outer = rnorm > 0.6
        st.update(r_shift_median=round(float(np.median(r_rad)), 2), b_shift_median=round(float(np.median(b_rad)), 2),
                  r_shift_outer=round(float(np.median(r_rad[outer])), 2) if outer.any() else None,
                  b_shift_outer=round(float(np.median(b_rad[outer])), 2) if outer.any() else None,
                  max_abs_shift=round(float(np.max(np.abs(np.concatenate([r_rad, b_rad])))), 2))
        worst = max(abs(st["r_shift_outer"] or 0), abs(st["b_shift_outer"] or 0))
        st["lateral_verdict"] = "despreciable" if worst < 0.3 else "leve" if worst < 1.0 else "visible"
        overlay = out.copy()
        for k, (i, j, *_r) in enumerate(tiles):
            m = max(abs(r_rad[k]), abs(b_rad[k]))
            c = cv2.applyColorMap(np.array([[int(min(m / vmax, 1) * 255)]], np.uint8), cv2.COLORMAP_CIVIDIS)[0, 0]
            p0 = (int(i * tw * sc), int(j * th * sc))
            p1 = (int((i + 1) * tw * sc) - 1, int((j + 1) * th * sc) - 1)
            cv2.rectangle(overlay, p0, p1, tuple(int(v) for v in c), -1)
        out = cv2.addWeighted(overlay, 0.55, out, 0.45, 0)
        for k, (i, j, *_r) in enumerate(tiles):
            cx, cy = (i + 0.5) * tw * sc, (j + 0.5) * th * sc
            L = r_rad[k] * 12
            cv2.arrowedLine(out, (int(cx), int(cy)), (int(cx + ux[k] * L), int(cy + uy[k] * L)), (235, 235, 235), 1,
                            cv2.LINE_AA, tipLength=0.3)
    else:
        st["lateral_verdict"] = "sin bordes suficientes para medir"
    # axial fringes
    disp = srgb_encode(np.clip(lin, 0, 1)).astype(F32)
    lab = lin_srgb_to_oklab(np.where(disp <= 0.04045, disp / 12.92, ((disp + 0.055) / 1.055) ** 2.4).astype(F32))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    hue = (np.degrees(np.arctan2(b, a)) + 360) % 360
    Lg = cv2.GaussianBlur(L, (0, 0), 1.0)
    gradL = np.hypot(cv2.Sobel(Lg, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(Lg, cv2.CV_32F, 0, 1, ksize=3)) / 8
    hl_edge = ((L > 0.85) & (gradL > 0.04)).astype(np.uint8)
    near = cv2.dilate(hl_edge, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))) > 0
    Cref = cv2.medianBlur(np.clip(C * 255 / 0.4, 0, 255).astype(np.uint8), 15).astype(F32) * 0.4 / 255
    dev = (C > 0.05) & (C - Cref > 0.03) & near
    purple = dev & (hue >= 290) & (hue <= 345)
    green = dev & (hue >= 110) & (hue <= 165)
    st.update(fringe_purple_pct=round(float(purple.mean() * 100), 4), fringe_green_pct=round(float(green.mean() * 100), 4))
    st["axial_verdict"] = "visible" if (st["fringe_purple_pct"] + st["fringe_green_pct"]) > 0.05 else \
        "leve" if (st["fringe_purple_pct"] + st["fringe_green_pct"]) > 0.005 else "despreciable"
    pm = cv2.resize(purple.astype(np.uint8) * 255, (ow, oh), interpolation=cv2.INTER_AREA) > 30
    gm = cv2.resize(green.astype(np.uint8) * 255, (ow, oh), interpolation=cv2.INTER_AREA) > 30
    out[pm] = PURPLE
    out[gm] = GREEN
    if info.get("source") == "camera_jpg":
        st["warning"] = "JPG de cámara: la CA lateral ya viene corregida en cámara; esto mide el residuo"
    st["legend"] = {"cmap": "cividis", "min": 0, "max": vmax, "unit": "desplazamiento radial px",
                    "purple": "#CC79A7 franja púrpura", "green": "#009E73 franja verde",
                    "arrows": "dirección/magnitud de R respecto a G"}
    return out, st, [("CA lateral (px)", 0, vmax, cv2.COLORMAP_CIVIDIS, None),
                     ("", 0, 0, None, [(PURPLE, "purpura"), (GREEN, "verde")])]


# ------------------------------------------------------------------------------------------------ colour shading
def shading_mask(lin: np.ndarray, size: int, info: dict) -> tuple[np.ndarray, dict]:
    """Low-frequency colour cast across the frame (e.g. magenta or green corners)

    On a ~480 px version: OkLab; near-neutral pixels (C < 0.035, 0.3 < L < 0.92) are the only evidence (coloured
    objects are not shading). a and b are blurred with normalised convolution (weights = neutral mask,
    σ = 8 % of the diagonal), and the frame's weighted median is subtracted, so the global white balance is ignored
    and only SPATIAL variation remains. Areas with too little neutral evidence stay grey. Per-corner means
    (10 % patches) vs the centre give the verdict. Experimental: a single frame can't separate a lens cast from a
    coloured scene; repeated across frames of the same lens it becomes evidence.
    """
    small = _fit(lin, 480)
    lab = lin_srgb_to_oklab(np.clip(small, 0, None))
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    wgt = ((C < 0.035) & (L > 0.3) & (L < 0.92)).astype(F32)
    h, w = L.shape
    sig = 0.08 * float(np.hypot(h, w))
    den = cv2.GaussianBlur(wgt, (0, 0), sig)
    ok = den > 0.05
    da = cv2.GaussianBlur(a * wgt, (0, 0), sig) / np.maximum(den, 1e-4)
    db = cv2.GaussianBlur(b * wgt, (0, 0), sig) / np.maximum(den, 1e-4)
    st: dict = {"neutral_coverage": round(float(wgt.mean()), 3), "unit": "OkLab a/b ×1000 relativo al centro",
                "experimental": True}
    if wgt.sum() < 200:
        st["verdict"] = "sin zonas neutras suficientes"
        da[:] = 0
        db[:] = 0
        ok[:] = False
    else:
        ma = float(np.median(a[wgt > 0]))
        mb = float(np.median(b[wgt > 0]))
        da -= ma
        db -= mb
        ph, pw = max(2, h // 10), max(2, w // 10)
        cen = (slice(h // 2 - ph // 2, h // 2 + ph // 2), slice(w // 2 - pw // 2, w // 2 + pw // 2))
        corners = {"tl": (slice(0, ph), slice(0, pw)), "tr": (slice(0, ph), slice(w - pw, w)),
                   "bl": (slice(h - ph, h), slice(0, pw)), "br": (slice(h - ph, h), slice(w - pw, w))}

        def mean_ok(arr, s):
            m = ok[s]
            return float(arr[s][m].mean()) if m.any() else None
        ca0, cb0 = mean_ok(da, cen), mean_ok(db, cen)
        cs = {}
        for k, s in corners.items():
            va, vb = mean_ok(da, s), mean_ok(db, s)
            cs[k] = None if va is None or ca0 is None else {"a": round((va - ca0) * 1000, 2), "b": round((vb - cb0) * 1000, 2)}
        st["corners"] = cs
        vals = [v["a"] for v in cs.values() if v]
        if not vals:
            st["verdict"] = "sin datos en las esquinas"
        else:
            m = float(np.mean(vals))
            st["corner_a_mean"] = round(m, 2)
            st["verdict"] = "esquinas magenta" if m > 4 else "esquinas verdes" if m < -4 else "uniforme"
        st["verdict_en"] = {"esquinas magenta": "magenta corners", "esquinas verdes": "green corners",
                            "uniforme": "uniform"}.get(st["verdict"], st["verdict"])
    base = _grey_base(lin, size)
    oh, ow = base.shape[:2]
    das = cv2.resize(da, (ow, oh), interpolation=cv2.INTER_CUBIC)
    oks = cv2.resize(ok.astype(F32), (ow, oh), interpolation=cv2.INTER_LINEAR) > 0.5
    t = np.clip(np.abs(das) / 0.012, 0, 1)[..., None]
    col = np.where((das > 0)[..., None], PURPLE[None, None, :], GREEN[None, None, :]).astype(F32)
    out = base.astype(F32)
    blend = out * (1 - 0.8 * t) + col * 0.8 * t
    out = np.where(oks[..., None], blend, out).astype(np.uint8)
    st["legend"] = {"magenta": "#CC79A7", "green": "#009E73", "full_scale": 0.012, "grey": "sin evidencia neutra"}
    return out, st, [("dominante (gris = sin zonas neutras)", 0, 0, None, [(PURPLE, "magenta"), (GREEN, "verde")])]


_FUNCS = {"noise": noise_mask, "ca": ca_mask, "shading": shading_mask}


def compute(photo: dict, kind: str, size: int, cache_dir: Path, jpeg_path: Path | None) -> tuple[Path, dict]:
    if kind not in _FUNCS:
        raise ReveladoError("E_BAD_INPUT", f"tipo de máscara desconocido: {kind} (noise|ca|shading)")
    size = int(min(max(size, 256), 2048))
    src = Path(photo.get("raw_path") or jpeg_path or "")
    try:
        stt = src.stat()
        sig = f"{stt.st_mtime_ns}:{stt.st_size}"
    except OSError as e:
        raise ReveladoError("E_NOT_FOUND", "el archivo original ya no está") from e
    key = hashlib.sha1(f"{photo['id']}:{kind}:{size}:{sig}:{VERSION}".encode()).hexdigest()[:16]
    d = cache_dir / "masks"
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{photo['id']}_{kind}_{size}_{key}.jpg"
    meta_p = out.with_suffix(".json")
    if out.exists() and meta_p.exists():
        return out, json.loads(meta_p.read_text())
    with _lock:
        if out.exists() and meta_p.exists():
            return out, json.loads(meta_p.read_text())
        t0 = time.time()
        lin, info = _get_source(photo, jpeg_path)
        body, st, legends = _FUNCS[kind](lin, size, info)
        del lin
        # orientation is applied to the overlay body; legend strips are appended below, unrotated
        img = _orient(body, int((photo.get("meta") or {}).get("orientation") or 1))
        for label, vmin, vmax, cmap, stops in legends:
            img = _legend(img, label, vmin, vmax, cmap, stops)
        nleg = 34 * len(legends)
        st.update(kind=kind, source=info.get("source"), source_note=info.get("note"),
                  exposure_gain_ev=info.get("exposure_gain_ev"), seconds=round(time.time() - t0, 2),
                  size=[int(img.shape[1]), int(img.shape[0])], legend_px=nleg, version=VERSION)
        tmp = out.with_suffix(".part.jpg")
        cv2.imwrite(str(tmp), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        os.replace(tmp, out)
        meta_p.write_text(json.dumps(st))
        log.info("mask %s %s in %.2fs", kind, photo["id"], st["seconds"])
        return out, st
