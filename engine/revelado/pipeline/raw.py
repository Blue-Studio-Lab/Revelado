# SPDX-License-Identifier: GPL-3.0-or-later
"""
raw.py (antes develop.py) - Revelado base NEUTRO y scene-referred de un RAW para los motores de pelicula.

Portado del motor de referencia. Cambios: sin rutas fijas (herramientas via revelado.tools), recorte al
recorte por defecto de la camara (LibRaw crop_width/height) en vez de 7008x4672 fijo, cualquier camara que
LibRaw/RawTherapee lean; la correccion de distorsion embebida solo existe para Sony (SubIFD 0x7037).

Salida (contrato para todos los motores)
----------------------------------------
* TIFF RGB, 16-bit FLOAT (half, IEEE754 binary16), sin compresion, al recorte por defecto de la camara,
  orientacion ya aplicada (no hay que leer el tag Orientation).
* Espacio: Rec.2020 primarias, blanco D65, LINEAL (sin curva). Valores no acotados (>1.0 permitidos).
* Escala: 1.0 ~= saturacion del sensor en el canal verde con el balance de blancos de camara
  (la reconstruccion de altas luces puede dar valores >1, tipicamente hasta ~1.3-2).
* NO lleva la exposicion aplicada: cada imagen trae su sugerencia en _trabajo/exposure.json
  (campo "ev"): lineal_expuesto = base * 2**ev  (deja el sujeto/medios cerca de 0.18 sin quemar luces).
* Metadatos en el tag ImageDescription (JSON): encoding, iso, wb, nr, lens, ev...
* Lectura: usar load_base(path) de este modulo (cv2 no lee TIFF half-float; tifffile si).

Pipeline
--------
1. RawTherapee 5.13 (rawtherapee-cli, pp3 con rutas absolutas): AMaZE, CA lateral automatica en raw
   (pre-demosaico), WB de camara, reconstruccion de altas luces "Inpaint opposed" (Coloropp), matriz de
   camara (sin DCP/look table), working space Rec.2020, capture sharpening por deconvolucion (auto, suave),
   reduccion de ruido piramidal Lab proporcional al ruido efectivo (ISO + empuje de exposicion): croma
   automatica fuerte, luminancia suave conservando textura. Sin curvas, sin contraste, ClampOOG=false.
   Salida float32 con TRC sRGB (perfil RTv4_Rec2020).
   BASE V2: CA lateral raw auto con 3 iteraciones + "evitar desplazamiento de color"; NR de RT mas fuerte
   segun nr_level (luma 6*(stops-3) hasta 40, croma AUT) y umbral de deconvolucion mas alto con mucho ruido.
1b. clean.py (BASE V2) sobre la imagen expuesta en OkLab: ruido medido en la propia imagen, defringe de CA
   axial morada/verde junto a altas luces (protege objetos realmente morados/verdes), despeckle (solo ruido
   alto), NL-means de luma dosificado por tono (sombras > medios) y NR de croma fuerte con filtro guiado por L
   + escala gruesa con bilateral conjunto (sin sangrado de color). Resultado: sombras neutras sin motas.
2. Python: TRC sRGB -> lineal exacto (extendido >1), recorte central al recorte de camara, [clean], correccion de distorsion
   con los parametros embebidos por Sony en el ARW (SubIFD 0x7037), remuestreo Lanczos4, half-float.
   La vineta NO se corrige: el FX2 ya la entrega compensada en el raw (medido, ver informe); la CA
   embebida tampoco (ya la corrige RT antes del demosaico).

API:  develop(arw, out_tif, ev=None, ...) -> dict ; load_base(tif) -> float32 HxWx3 ; analyze_exposure(arw) -> dict
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .colorspaces import (REC2020_LUMA, REC2020_TO_XYZ, srgb_decode)
from . import clean as cleanmod
from ..tools import require_tool

DEFAULT_EXPOSURE_JSON = None
ENCODING = {
    "container": "TIFF RGB float16 (half) uncompressed",
    "primaries": "ITU-R BT.2020", "white": "D65", "transfer": "linear",
    "scale": "1.0 ~= green-channel sensor saturation at as-shot WB; values >1 = reconstructed highlights",
    "exposure": "NOT applied; multiply by 2**ev from exposure.json",
    "orientation": "applied (pixels upright)",
}

# ----------------------------------------------------------------------------------------------
# pp3 base (neutro). Se escribe a disco para que rawtherapee-cli lo reciba con ruta absoluta.
# ----------------------------------------------------------------------------------------------
BASE_PP3_TEXT = """[Version]
AppVersion=5.13
Version=351

[Exposure]
Auto=false
Clip=0
Compensation=0
Brightness=0
Contrast=0
Saturation=0
Black=0
HighlightCompr=0
HighlightComprThreshold=0
ShadowCompr=0
HistogramMatching=false
CurveFromHistogramMatching=false
ClampOOG=false
CurveMode=Standard
CurveMode2=Standard
Curve=0;
Curve2=0;

[HLRecovery]
Enabled=true
Method=Coloropp
Hlbl=0
Hlth=1

[White Balance]
Enabled=true
Setting=Camera
Equal=1

[Color Management]
InputProfile=(camera)
ToneCurve=false
ApplyLookTable=false
ApplyBaselineExposureOffset=false
ApplyHueSatMap=false
DCPIlluminant=0
WorkingProfile=Rec2020
OutputProfile=RTv4_Rec2020
OutputProfileIntent=Relative
OutputBPC=false

[RAW]
CA=true
CAAutoIterations=3
CAAvoidColourshift=true
HotPixelFilter=true
DeadPixelFilter=false

[RAW Bayer]
Method=amaze
Border=4
CcSteps=0
LineDenoise=0
GreenEqThreshold=0

[PostDemosaicSharpening]
Enabled=true
AutoContrast=true
Contrast=10
AutoRadius=true
DeconvRadius=0.75
DeconvRadiusOffset=0
DeconvIterCheck=true
DeconvIterations=20

[Sharpening]
Enabled=false

[Vibrance]
Enabled=false

[Dehaze]
Enabled=false

[LensProfile]
LcMode=none
UseDistortion=false
UseVignette=false
UseCA=false

[Vignetting Correction]
Amount=0
"""


def _write_pp3(d: Path) -> Path:
    """The base profile is written to a scratch dir per run (rawtherapee-cli wants a file path)."""
    p = Path(d) / "base_neutral.pp3"
    p.write_text(BASE_PP3_TEXT)
    return p


def noise_stops(iso: float, ev: float | None) -> float:
    """Ruido efectivo en 'stops sobre ISO100': ISO + empuje de exposicion por encima de lo normal (+1.5 EV)."""
    ev = 2.0 if ev is None else ev
    return float(np.log2(max(iso, 50) / 100.0) + max(0.0, ev - 1.5))


def nr_level(iso: float, ev: float | None) -> float:
    """0..1: dosis de limpieza (0 = ISO100-400 sin empuje, 1 = ISO1600 con +5 EV o equivalente)."""
    return float(np.clip((noise_stops(iso, ev) - 3.0) / 5.0, 0.0, 1.0))


def adjust_nr(nr: dict, mode: str) -> dict:
    """Recipe noise-reduction mode over the automatic dose: auto | soft | strong | off."""
    nr = dict(nr, mode=mode)
    if mode == "soft":
        nr["luma"] = round(nr["luma"] * 0.5, 1)
        nr["level"] = round(nr["level"] * 0.6, 3)
    elif mode == "strong":
        nr["luma"] = round(float(min(40.0, nr["luma"] * 1.5 + 6.0)), 1)
        nr["level"] = round(float(min(1.0, nr["level"] + 0.25)), 3)
    elif mode == "off":
        nr["luma"] = 0.0
        nr["level"] = 0.0
    return nr


def nr_params(iso: float, ev: float | None) -> dict:
    s = noise_stops(iso, ev)
    lv = nr_level(iso, ev)
    luma = float(np.clip(6.0 * (s - 3.0), 0, 40))           # RT pyramid luma (wavelets, multi-escala)
    ldetail = float(np.clip(80 - 0.5 * luma, 60, 80))        # conservar textura fina
    chroma = float(np.clip(15 + 25 * lv, 15, 40))            # solo se usa como base; CMethod=AUT la estima
    # capture sharpening: con mucho ruido, umbral de contraste mas alto para no "afilar" el ruido
    deconv_contrast = None if lv < 0.4 else float(np.clip(15 + 25 * (lv - 0.4) / 0.6, 15, 40))
    return {"noise_stops": round(s, 2), "level": round(lv, 3), "luma": round(luma, 1), "ldetail": round(ldetail, 1),
            "chroma": round(chroma, 1), "chroma_method": "AUT", "demosaic": "amaze",
            "deconv_contrast": deconv_contrast}


def _nr_pp3_text(nr: dict, preblack: float | None = None) -> str:
    """Partial pp3 (NR + demosaic). ``preblack`` = K DN: RawTherapee's black is lowered by K on all four CFA
    channels (PreBlack0..3 = -K) so high-ISO noise is not clipped at 0 before demosaic (see pedestal_probe);
    None leaves the text exactly as before. nr mode "off" disables RT's pyramid NR altogether."""
    if nr.get("mode") == "off":
        # "off" is really off: before, only luma/level were zeroed and RT's pyramid chroma (CMethod=AUT) still ran
        head = "[Directional Pyramid Denoising]\nEnabled=false\n"
    else:
        head = None
    sharp = ""
    if nr.get("deconv_contrast") is not None:
        sharp = f"""
[PostDemosaicSharpening]
AutoContrast=false
Contrast={nr['deconv_contrast']:.0f}
"""
    pb = "" if preblack is None else "".join(f"PreBlack{i}={-float(preblack):.0f}\n" for i in range(4))
    if head is not None:
        return f"""{head}
[RAW Bayer]
Method={nr['demosaic']}
{pb}{sharp}"""
    return f"""[Directional Pyramid Denoising]
Enabled=true
Enhance=false
Median=false
Luma={nr['luma']:.1f}
Ldetail={nr['ldetail']:.1f}
Chroma={nr['chroma']:.1f}
Method=Lab
LMethod=SLI
CMethod=AUT
C2Method=AUTO
SMethod=shal
Redchro=0
Bluechro=0
Gamma=1.7
Passes=1
LCurve=0;
CCCurve=0;

[RAW Bayer]
Method={nr['demosaic']}
{pb}{sharp}"""


# ----------------------------------------------------------------------------------------------
# Metadatos Sony
# ----------------------------------------------------------------------------------------------
def read_meta(arw: str | Path) -> dict:
    tags = ["-ISO", "-FocalLength#", "-FNumber#", "-ExposureTime#", "-LensModel", "-Orientation#",
            "-SubIFD:DistortionCorrParams", "-SubIFD:VignettingCorrParams",
            "-SubIFD:ChromaticAberrationCorrParams", "-FocusLocation", "-Model"]
    out = subprocess.run([require_tool("exiftool"), "-j", "-n", *tags, str(arw)], capture_output=True, text=True,
                         check=True)
    d = json.loads(out.stdout)[0]

    def ints(v):
        if v is None:
            return None
        return [int(x) for x in str(v).split()]
    return {
        "iso": float(d.get("ISO", 100)), "focal": d.get("FocalLength"), "fnumber": d.get("FNumber"),
        "shutter": d.get("ExposureTime"), "lens": d.get("LensModel"), "model": d.get("Model"),
        "orientation": d.get("Orientation"),
        "dist": ints(d.get("DistortionCorrParams")), "vig": ints(d.get("VignettingCorrParams")),
        "ca": ints(d.get("ChromaticAberrationCorrParams")), "focus": ints(d.get("FocusLocation")),
    }


def sony_distortion_curve(params):
    """Devuelve (knots, cor) con cor(r) = r_fuente / r_destino; r normalizado a media diagonal.
    Formato SubIFD 0x7037: N, d0..d(N-1); cor = 1 + d * 2^-14; knots equiespaciados en [0,1]."""
    if not params or params[0] < 2 or len(params) < params[0] + 1:
        return None
    n = params[0]
    d = np.array(params[1:n + 1], dtype=np.float64)
    if np.all(d == 0):
        return None
    knots = np.arange(n) / (n - 1)
    return knots, 1.0 + d * 2.0 ** -14


def correct_distortion(img: np.ndarray, params) -> tuple[np.ndarray, dict]:
    import cv2
    cur = sony_distortion_curve(params)
    if cur is None:
        return img, {"distortion": "none"}
    knots, cor = cur
    k = min(1.0, 1.0 / cor.max())                      # zoom minimo para no traer bordes negros (cojin)
    h, w = img.shape[:2]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    rnorm = 1.0 / np.hypot(w / 2.0, h / 2.0)
    out = np.empty_like(img)
    band = 512
    xs = (np.arange(w, dtype=np.float32) - cx)
    for y0 in range(0, h, band):
        y1 = min(h, y0 + band)
        ys = (np.arange(y0, y1, dtype=np.float32) - cy)[:, None]
        dx = np.broadcast_to(xs[None, :], (y1 - y0, w))
        r = np.sqrt(dx * dx + ys * ys) * rnorm * k
        f = (k * np.interp(r, knots, cor)).astype(np.float32)
        mapx = (cx + dx * f).astype(np.float32)
        mapy = (cy + ys * f).astype(np.float32)
        # remap de la banda completa contra la imagen entera
        out[y0:y1] = cv2.remap(img, mapx, mapy, interpolation=cv2.INTER_LANCZOS4,
                               borderMode=cv2.BORDER_REFLECT)
    return out, {"distortion": "sony_embedded", "zoom": round(1 / k, 5),
                 "max_shift_pct": round(float(np.abs(cor - 1).max() * 100), 3)}


# ----------------------------------------------------------------------------------------------
# Exposicion (analisis rapido sobre el raw, sin demosaico: superpixel 2x2)
# ----------------------------------------------------------------------------------------------
def _cam_to_rec2020(raw) -> np.ndarray:
    M = np.array(raw.rgb_xyz_matrix[:3], dtype=np.float64)          # XYZ(D65) -> cam
    cam_rgb = M @ REC2020_TO_XYZ
    cam_rgb = cam_rgb / cam_rgb.sum(axis=1, keepdims=True)
    return np.linalg.inv(cam_rgb)


def half_linear(arw: str | Path):
    """Imagen lineal Rec.2020 a media resolucion (superpixel), escala 1.0 = saturacion verde (como la base).
    Devuelve (rgb HxWx3 float32 sin rotar, mascara de clip HxW, info)."""
    import rawpy
    with rawpy.imread(str(arw)) as r:
        raw = r.raw_image_visible.astype(np.float32)
        cols = r.raw_colors_visible
        black = np.array(r.black_level_per_channel, dtype=np.float32)
        white = float(r.white_level)
        wb = np.array(r.camera_whitebalance[:3], dtype=np.float64)
        wb = wb / wb[1]
        m = _cam_to_rec2020(r)
        s = r.sizes
        pat = np.asarray(r.raw_pattern) if r.raw_pattern is not None else np.zeros((2, 2))
        k = 2 if pat.shape == (2, 2) else int(pat.shape[0])     # Bayer: 2x2 superpixel; X-Trans: 6x6 block
        h2, w2 = raw.shape[0] // k, raw.shape[1] // k
        raw = raw[:h2 * k, :w2 * k]
        cols = cols[:h2 * k, :w2 * k]
        chans = np.zeros((h2, w2, 3), np.float32)
        clip = np.zeros((h2, w2), bool)
        cnt = np.zeros(3, np.float32)
        for dy in range(k):
            for dx in range(k):
                c = int(cols[dy, dx])
                cc = 1 if c == 3 else c
                v = raw[dy::k, dx::k] - black[c]
                clip |= raw[dy::k, dx::k] >= white * 0.985
                chans[..., cc] += v
                cnt[cc] += 1
        chans /= cnt
        scale = 1.0 / (white - black.mean())
        chans *= (wb * scale).astype(np.float32)
        rgb = chans @ m.T.astype(np.float32)
        info = {"crop_left": s.crop_left_margin, "crop_top": s.crop_top_margin, "flip": s.flip,
                "crop_w": s.crop_width, "crop_h": s.crop_height, "bin": k}
    return np.maximum(rgb, 0), clip, info


# ----------------------------------------------------------------------------------------------
# Pedestal fix (very high ISO): RawTherapee subtracts the black level and clips negatives to 0 BEFORE demosaic.
# When the dark noise is comparable to the shadow signal (D850 ISO 25600: sigma ~90-145 DN vs 10-70 DN of signal)
# that rectifies the noise: E[max(x, 0)] > E[x], channel means rise by tens of DN and the WB gains (R 1.5, B 1.9)
# turn the lifted shadows magenta/purple. Fix: RT's black is lowered by K = min(black, 4 sigma_dark) (PreBlack),
# so almost nothing is clipped, and the known pedestal is removed afterwards in linear light:
#     img = (img_rt - P) / alpha,   alpha = 1 - K * mean(rowsum(A))
# with RT's response fitted per 16x16-superpixel block against an unclipped rawpy superpixel X0 (camera space, WB
# applied, LibRaw black):  Y_rt = A @ X0 + P  (3x3 + intercept, all unclipped blocks). The intercept is measured
# where it matters (dark blocks included) instead of extrapolated: a slope-only fit on bright blocks gives
# P = A @ (wb*K), but A is poorly constrained off the neutral axis and wb*K is magenta (R50: green P +6 %, shadows
# 2.9 L too dark); the LibRaw-matrix analytic pedestal is wrong when RT's matrix/white differ (D850: +3.7..7.5 L).
# The intercept fit was within ~1 L / ~1.5 a,b of the raw truth on both (see the fix report).
#
# GATE (keeps normal ISO pixel-identical to the old path; only the timing fields of the metadata differ): the expected rectification bias of the darkest 1 % of
# the frame (32x32 blocks of one green CFA plane), in exposed linear units,
#     shift = bias(mu_dark, sigma_dark) / (white - black) * 2**ev,   bias(mu, s) = s*phi(mu/s) - mu*(1 - Phi(mu/s))
# must reach PEDESTAL_SHIFT_MIN; otherwise (or for non-Bayer sensors / black 0 / K < 1 DN) nothing changes: no
# PreBlack in the pp3, no pedestal removal, no linear-mean restore in clean(). sigma_dark = MAD of vertical
# same-colour differences in those blocks (robust to scene gradients).
# ----------------------------------------------------------------------------------------------
PEDESTAL_SHIFT_MIN = 3.0e-3        # exposed linear, green. Measured: D850 ISO25600 0.0107, R50 ISO32000 0.0100
#                                    (both fixed); FX3 ISO20000 0.00095, K-3 III ISO12800 0, ISO 100-400 files 0
#                                    (all unchanged). ~3x margin on both sides.
PEDESTAL_K_SIGMAS = 4.0
PEDESTAL_DARK_BLOCK = 32           # px of one CFA plane for the dark-noise estimate
PEDESTAL_FIT_BLOCK = 16            # superpixels per fit block
PEDESTAL_MIN_BLOCKS = 40           # below this (or poor orientation match) the analytic pedestal is used
PEDESTAL_FIT_MAX = 0.6             # blocks brighter than this (base units) are left out of the fit
_FLIP_TO_K = {0: 0, 3: 2, 6: 1, 5: 3}  # LibRaw flip -> np.rot90 k that takes RT's (upright) output back to sensor


def _rect_bias(mu: float, sigma: float) -> float:
    """E[max(X, 0)] - mu for X ~ N(mu, sigma): mean lift caused by clipping negatives at 0."""
    import math
    if sigma <= 0:
        return 0.0
    t = mu / sigma
    phi = math.exp(-0.5 * t * t) / math.sqrt(2 * math.pi)
    Phi = 0.5 * (1 + math.erf(t / math.sqrt(2)))
    return float(sigma * phi - mu * (1 - Phi))


def dark_noise(raw: np.ndarray, cols: np.ndarray, black: np.ndarray, block: int = PEDESTAL_DARK_BLOCK,
               frac: float = 0.01) -> tuple[float, float]:
    """(sigma_dark, mu_dark) in DN on one green CFA plane: robust noise (MAD of vertical same-colour differences)
    and median signal above black of the darkest ``frac`` of ``block``x``block`` tiles."""
    pos = [(dy, dx) for dy in range(2) for dx in range(2) if int(cols[dy, dx]) in (1, 3)]
    dy, dx = pos[0] if pos else (0, 0)
    c = int(cols[dy, dx])
    g = raw[dy::2, dx::2]
    h, w = (g.shape[0] // block) * block, (g.shape[1] // block) * block
    t = g[:h, :w].astype(np.float32).reshape(h // block, block, w // block, block).transpose(0, 2, 1, 3)
    mu = t.mean((2, 3)).ravel()
    n = max(8, int(round(mu.size * frac)))
    idx = np.argsort(mu, kind="stable")[:n]
    tt = t.reshape(-1, block, block)[idx]
    d = (tt[:, 1:, :] - tt[:, :-1, :]).ravel()
    sig = float(np.median(np.abs(d - np.median(d))) * 1.4826 / np.sqrt(2))
    return sig, float(np.median(mu[idx]) - float(black[c]))


def pedestal_probe(raw_path: str | Path, ev: float | None) -> dict:
    """Decide (gate) the pedestal fix for this RAW and, when applied, keep the small per-block statistics of an
    unclipped superpixel for pedestal_fit(). Keys starting with '_' are internal (not JSON)."""
    import rawpy
    gain = 2.0 ** (ev if ev is not None else 2.0)
    info: dict = {"applied": False}
    with rawpy.imread(str(raw_path)) as r:
        pat = np.asarray(r.raw_pattern) if r.raw_pattern is not None else None
        if pat is None or pat.shape != (2, 2) or r.num_colors != 3:
            info["reason"] = "not a 2x2 Bayer sensor"
            return info
        raw = r.raw_image_visible
        cols = r.raw_colors_visible
        black = np.array(r.black_level_per_channel, np.float32)
        white = float(r.white_level)
        rng = white - float(black.mean())
        sig, mu = dark_noise(raw, cols, black)
        K = float(min(float(black.min()), np.ceil(PEDESTAL_K_SIGMAS * sig)))
        shift = _rect_bias(mu, sig) / rng * gain
        info.update({"sigma_dark": round(sig, 2), "mu_dark": round(mu, 2), "black": round(float(black.mean()), 1),
                     "white": white, "K": K, "shift_est": round(shift, 6), "threshold": PEDESTAL_SHIFT_MIN})
        if K < 1 or shift < PEDESTAL_SHIFT_MIN:
            info["reason"] = "black level 0" if K < 1 else "dark noise small vs shadow signal"
            return info
        info["applied"] = True
        wb = np.array(r.camera_whitebalance[:3], np.float64)
        if not (np.all(np.isfinite(wb)) and np.all(wb > 0)):
            wb = np.ones(3)                          # missing as-shot WB: the 3x3 fit absorbs any channel scale
        wb = wb / wb[1]
        s = r.sizes
        oy, ox = s.crop_top_margin - s.top_margin, s.crop_left_margin - s.left_margin
        ch, cw = (s.crop_height, s.crop_width) if (s.crop_width and s.crop_height) else raw.shape
        if oy < 0 or ox < 0 or oy + ch > raw.shape[0] or ox + cw > raw.shape[1]:
            oy, ox, (ch, cw) = 0, 0, raw.shape
        oy, ox = oy - oy % 2, ox - ox % 2            # keep the CFA phase of cols[0:2, 0:2]
        h2, w2 = ch // 2, cw // 2
        B = PEDESTAL_FIT_BLOCK
        hb, wb_ = h2 // B, w2 // B
        Xb = np.zeros((hb, wb_, 3), np.float64)
        Cb = np.zeros((hb, wb_), bool)
        for dy in range(2):
            for dx in range(2):
                c = int(cols[oy + dy, ox + dx])
                cc = 1 if c == 3 else c
                v = raw[oy + dy:oy + 2 * h2:2, ox + dx:ox + 2 * w2:2][:hb * B, :wb_ * B].astype(np.float32)
                Cb |= (v >= white * 0.95).reshape(hb, B, wb_, B).any((1, 3))
                m = v.reshape(hb, B, wb_, B).mean((1, 3), dtype=np.float64)
                Xb[..., cc] += (m - (black[c] - K)) * (0.5 if cc == 1 else 1.0)
                del v
        Xb *= wb
        info.update({"flip": int(s.flip), "_Xb": Xb, "_Cb": Cb, "_wb": wb, "_hw2": (h2, w2),
                     "_P_analytic": (_cam_to_rec2020(r) @ (wb * K)) / (rng + K), "_alpha_analytic": rng / (rng + K)})
    return info


def pedestal_fit(img: np.ndarray, probe: dict) -> tuple[np.ndarray, float, dict]:
    """Fit RT's response on the block grid of pedestal_probe() -> (P (3,), alpha, info). img: RT output (upright,
    camera crop, linear). Orientation: the RT image is brought back to sensor orientation by trying the rotations
    compatible with its aspect ratio (LibRaw's flip is the expected one) and keeping the best block correlation."""
    import cv2
    Xb, Cb, wb = probe["_Xb"], probe["_Cb"], probe["_wb"]
    h2, w2 = probe["_hw2"]
    K = probe["K"]
    B = PEDESTAL_FIT_BLOCK
    hb, wbk = Xb.shape[:2]
    H, W = img.shape[:2]
    lx = np.log(np.maximum(Xb @ np.array([0.25, 0.5, 0.25]), 1.0)).ravel()          # DN
    best = None
    for k in (0, 1, 2, 3):
        oh, ow = (h2, w2) if k % 2 == 0 else (w2, h2)
        if abs(np.log((H / W) / (oh / ow))) > 0.05:
            continue
        small = cv2.resize(img, (ow, oh), interpolation=cv2.INTER_AREA)
        sens = np.ascontiguousarray(np.rot90(small, k))[:hb * B, :wbk * B]
        Yb = sens.reshape(hb, B, wbk, B, 3).mean((1, 3), dtype=np.float64)
        ly = np.log(np.maximum(Yb @ np.array([0.25, 0.5, 0.25]), 1e-5)).ravel()
        corr = float(np.corrcoef(lx, ly)[0, 1])
        if best is None or corr > best[0]:
            best = (corr, k, Yb)
    finfo = {"flip": probe.get("flip"), "expected_rot": _FLIP_TO_K.get(probe.get("flip"))}
    P_an, a_an = np.asarray(probe["_P_analytic"], np.float64), float(probe["_alpha_analytic"])
    finfo["P_analytic"] = [round(float(v), 6) for v in P_an]
    finfo["alpha_analytic"] = round(a_an, 6)
    if best is not None:
        corr, k, Yb = best
        finfo.update({"rot_k": k, "orientation_corr": round(corr, 4)})
        X0, Yf, Cf = Xb.reshape(-1, 3) - wb * K, Yb.reshape(-1, 3), Cb.ravel()
        ok = (~Cf) & (Yf.max(1) < PEDESTAL_FIT_MAX)
        n = int(ok.sum())
        finfo["fit_blocks"] = n
        if n >= PEDESTAL_MIN_BLOCKS and corr >= 0.9:
            Ai = np.linalg.lstsq(np.c_[X0[ok], np.ones(n)], Yf[ok], rcond=None)[0].T
            A, P = Ai[:, :3], Ai[:, 3]
            res = Yf[ok] - X0[ok] @ A.T - P
            # relative RMS of the block fit (noise + RT's local, non-linear steps; ~1-2 % at ISO 25600-32000)
            finfo["fit_residual"] = round(float(np.sqrt((res ** 2).mean()) / np.sqrt((Yf[ok] ** 2).mean())), 5)
            alpha = float(1.0 - K * np.mean(A.sum(1)))
            if 0.5 < alpha <= 1.0 and np.all(np.isfinite(P)):
                finfo["method"] = "fit"
                return P.astype(np.float32), alpha, finfo
    finfo["method"] = "analytic (fit unavailable)"
    return P_an.astype(np.float32), a_an, finfo


HL_CEIL = 0.18 * 2 ** 4.0
MAX_UNDER = 1.0


def analyze_exposure(arw: str | Path, meta: dict | None = None) -> dict:
    """EV sugerido: sujeto (punto AF) + medios ponderados al centro, limitado por las altas luces."""
    import cv2
    meta = meta or read_meta(arw)
    rgb, clip, info = half_linear(arw)
    Y = (rgb @ REC2020_LUMA.astype(np.float32))
    h, w = Y.shape
    eps = 1e-5
    lY = np.log2(Y + eps)
    # ponderacion central (gaussiana) para la clave global
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    wc = np.exp(-(((xx - w / 2) / (0.38 * w)) ** 2 + ((yy - h / 2) / (0.38 * h)) ** 2))
    valid = ~clip
    lo, hi = np.percentile(lY[valid], [1, 99])
    m = valid & (lY > lo) & (lY < hi)
    key_global = float(2 ** (np.sum(lY[m] * wc[m]) / np.sum(wc[m])))
    # sujeto: zona del punto AF (coordenadas en el recorte por defecto de la camara, sin rotar)
    key_subject = None
    if meta.get("focus") and len(meta["focus"]) >= 4 and meta["focus"][2] > 0:
        fx = (meta["focus"][2] + info["crop_left"]) / float(info["bin"])
        fy = (meta["focus"][3] + info["crop_top"]) / float(info["bin"])
        rr = int(0.06 * max(h, w))
        x0, x1 = int(max(0, fx - rr)), int(min(w, fx + rr))
        y0, y1 = int(max(0, fy - rr)), int(min(h, fy + rr))
        sub = lY[y0:y1, x0:x1][valid[y0:y1, x0:x1]]
        if sub.size > 100:
            p = np.percentile(sub, [5, 95])
            sub = sub[(sub > p[0]) & (sub < p[1])]
            key_subject = float(2 ** sub.mean())
    ev_global = float(np.log2(0.16 / key_global))
    ev_mid = ev_global if key_subject is None else float(np.log2(0.20 / key_subject) * 0.6 + ev_global * 0.4)
    # techo de altas luces: el p99.7 de lo NO recortado no deberia pasar de HL_CEIL (+4 EV sobre gris 18%,
    # zona que el hombro de la pelicula todavia separa). Para no hundir al sujeto, el techo nunca baja el EV
    # mas de MAX_UNDER por debajo de ev_mid: en esas escenas (contraluz/cielo) el motor debe usar hombro largo
    # (ver hl_ev_over_mid).
    Ys = cv2.GaussianBlur(Y, (0, 0), 2)
    hl_ref = float(np.percentile(Ys[valid], 99.7))
    ev_cap = float(np.log2(HL_CEIL / max(hl_ref, eps)))
    ev = float(np.clip(min(ev_mid, max(ev_cap, ev_mid - MAX_UNDER)), -2.0, 7.0))
    hl_over = float(np.log2(max(hl_ref, eps) * 2 ** ev / 0.18))
    clipped_pct = float(clip.mean() * 100)
    return {
        "file": Path(arw).name, "ev": round(ev, 3), "ev_mid": round(ev_mid, 3), "ev_global": round(ev_global, 3),
        "ev_cap_highlights": round(ev_cap, 3), "limited_by_highlights": bool(ev_cap < ev_mid),
        "key_global": round(key_global, 5), "key_subject": None if key_subject is None else round(key_subject, 5),
        "highlight_p99_7": round(hl_ref, 4), "hl_ev_over_mid": round(hl_over, 2), "raw_clipped_pct": round(clipped_pct, 3),
        "iso": meta["iso"], "noise_stops": round(noise_stops(meta["iso"], ev), 2),
        "lens": meta.get("lens"), "focal": meta.get("focal"),
    }


# ----------------------------------------------------------------------------------------------
# Revelado completo
# ----------------------------------------------------------------------------------------------
def _lookup_ev(arw, exposure_json):
    try:
        d = json.loads(Path(exposure_json).read_text())
        return d.get(Path(arw).name, {}).get("ev")
    except Exception:
        return None


def deterministic() -> bool:
    """Bit-reproducible develops (default on). REVELADO_DETERMINISTIC=0 trades that for speed."""
    return (os.environ.get("REVELADO_DETERMINISTIC") or "1") != "0"


def camera_crop(raw_path: str | Path) -> tuple[int, int]:
    """(width, height) of the camera's default crop (LibRaw), unrotated; (0, 0) when the format has none."""
    try:
        import rawpy
        with rawpy.imread(str(raw_path)) as r:
            s = r.sizes
            return int(s.crop_width or 0), int(s.crop_height or 0)
    except Exception:  # noqa: BLE001
        return 0, 0


def develop(arw: str | Path, out_tif: str | Path, ev: float | None = None, exposure_json=DEFAULT_EXPOSURE_JSON,
            distortion: bool = True, tmp_dir: str | Path | None = None, clean: bool = True,
            nr_mode: str = "auto", defringe: bool = True, raw_ca: bool = True) -> dict:
    """ARW -> TIFF half-float lineal Rec.2020 (ver docstring del modulo). Devuelve dict con metadatos y tiempos."""
    import tifffile
    t0 = time.time()
    arw = Path(arw).resolve()
    out_tif = Path(out_tif).resolve()
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    meta = read_meta(arw)
    if ev is None and exposure_json:
        ev = _lookup_ev(arw, exposure_json)
    nr = nr_params(meta["iso"], ev)
    nr = adjust_nr(nr, nr_mode)
    try:
        ped = pedestal_probe(arw, ev)                    # gate: decided on the RAW before RawTherapee runs
    except Exception as e:  # noqa: BLE001  (LibRaw cannot read it but RawTherapee may: old path, unchanged)
        ped = {"applied": False, "reason": f"probe failed: {type(e).__name__}"}
    tmp_dir = Path(tmp_dir or out_tif.parent / "_tmp_rt")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_dir) as td:
        base_pp3 = _write_pp3(Path(td))
        nr_pp3 = Path(td) / "nr.pp3"
        nr_pp3.write_text(_nr_pp3_text(nr, preblack=ped["K"] if ped["applied"] else None)
                          + ("" if raw_ca else "\n[RAW]\nCA=false\n"))
        rt_out = Path(td) / (arw.stem + ".tif")
        cmd = [require_tool("rawtherapee-cli"), "-q", "-Y", "-t", "-b32", "-p", str(base_pp3.resolve()),
               "-p", str(nr_pp3.resolve()),
               "-o", str(rt_out), "-c", str(arw)]
        env = dict(os.environ)
        if deterministic():
            # rawtherapee-cli's multithreaded stages give run-to-run differences (~1e-4 on ~0.02 % of pixels,
            # measured with 5.13): one thread makes the develop bit-reproducible (~5 s slower at 33 MP).
            env["OMP_NUM_THREADS"] = "1"
        pr = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if pr.returncode != 0 or not rt_out.exists():
            raise RuntimeError(f"rawtherapee-cli fallo en {arw.name}: {pr.stderr[-800:]}")
        t_rt = time.time() - t0
        img = tifffile.imread(str(rt_out))
    img = srgb_decode(img)                               # TRC sRGB -> lineal (extendido), float32
    h, w = img.shape[:2]
    cw, ch = camera_crop(arw)                            # recorte por defecto de la camara (sin orientar)
    if cw and ch:
        tw, th = (min(cw, ch), max(cw, ch)) if h > w else (max(cw, ch), min(cw, ch))
        th, tw = min(th, h), min(tw, w)
        oy, ox = (h - th) // 2, (w - tw) // 2
        img = np.ascontiguousarray(img[oy:oy + th, ox:ox + tw])
    pinfo = {k: v for k, v in ped.items() if not k.startswith("_")}
    if ped["applied"]:
        try:
            P, alpha, finfo = pedestal_fit(img, ped)
        except Exception as e:  # noqa: BLE001  (PreBlack was applied: the pedestal must still come off)
            P = np.asarray(ped["_P_analytic"], np.float32)
            alpha = float(ped["_alpha_analytic"])
            finfo = {"method": f"analytic (fit failed: {type(e).__name__})"}
        img -= P
        img /= np.float32(alpha)
        pinfo.update(finfo, P=[round(float(v), 6) for v in P], alpha=round(alpha, 6))
    del ped
    cinfo = {"clean": "off"}
    if clean:
        t_c = time.time()
        img, cinfo = cleanmod.clean(img, ev if ev is not None else 2.0, nr["level"], do_defringe=defringe,
                                    restore_linear_mean=pinfo["applied"])
        cinfo["seconds"] = round(time.time() - t_c, 2)
    lens = {"distortion": "off"}
    if distortion:
        img, lens = correct_distortion(img, meta.get("dist"))
    np.maximum(img, -0.01, out=img)                      # recorta solo el undershoot residual del Lanczos
    stats = {
        "max": round(float(img.max()), 4),
        "pct_over_1": round(float((img.max(axis=2) > 1.0).mean() * 100), 4),
        "neg_pct": round(float((img.min(axis=2) < 0).mean() * 100), 4),
    }
    desc = {"encoding": ENCODING, "source": arw.name, "iso": meta["iso"], "lens": meta["lens"],
            "focal": meta["focal"], "fnumber": meta["fnumber"], "shutter": meta["shutter"],
            "ev_suggested": ev, "nr": nr, "clean": cinfo, "lens_correction": lens, "base_version": 2,
            **({"pedestal": pinfo} if pinfo["applied"] else {}),     # absent when gated off (byte-identical)
            "pipeline": ("rawtherapee-cli AMaZE + rawCA(auto x3, avoid colour shift) + Coloropp HL + capture "
                         "sharpening + pyramid NR; python lin + clean.py (defringe, luma NLM, chroma guided NR) "
                         "+ distortion")}
    tifffile.imwrite(str(out_tif), img.astype(np.float16), photometric="rgb",
                     description=json.dumps(desc), metadata=None, software="revelado/pipeline/raw.py")
    res = {"file": arw.name, "out": str(out_tif), "shape": list(img.shape), "seconds_total": round(time.time() - t0, 2),
           "seconds_rt": round(t_rt, 2), "ev_suggested": ev, "nr": nr, "clean": cinfo, "lens": lens,
           "pedestal": pinfo,
           "stats": stats}
    return res


def load_base(path: str | Path) -> np.ndarray:
    """Lee una base -> float32 HxWx3 lineal Rec.2020 (sin exposicion aplicada)."""
    import tifffile
    return tifffile.imread(str(path)).astype(np.float32)


def load_base_meta(path: str | Path) -> dict:
    import tifffile
    with tifffile.TiffFile(str(path)) as t:
        try:
            return json.loads(t.pages[0].description)
        except Exception:
            return {}
