# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral render (film = "none"): the default look. No film emulation, no grain, no colour styling.

Scene-linear Rec.2020 base * 2**ev -> sRGB primaries -> soft highlight shoulder (hue preserving, never clips
hard) -> OkLab -> base tone curve on L (black point from the frame's own darkest 0.3 %, gentle mid-tone S-curve: the
"camera default" contrast of a neutral developer, not a look) -> optional finishing shared with the film engine (residual NR, skin retouch, blemishes,
sharpening; all at 0 by default in the neutral recipe) -> gamut mapping at constant L/h -> sRGB 8 bit with TPDF
dither (+ optional 16-bit TIFF). Same outputs and return keys as ``ektar.render``.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from ..colorspaces import REC2020_LUMA, REC2020_TO_SRGB
from . import ektar as E

F32 = np.float32


def shoulder(rgb: np.ndarray, knee: float = 0.75) -> np.ndarray:
    """Hue-preserving soft roll-off of the max channel above ``knee`` (linear sRGB), asymptote 1.0."""
    m = rgb.max(axis=-1, keepdims=True)
    k = F32(knee)
    over = np.maximum(m - k, 0)
    mm = np.where(m > k, k + (1 - k) * (1 - np.exp(-over / (1 - k))), m)
    return (rgb * (mm / np.maximum(m, 1e-6))).astype(F32)


def base_curve(lab: np.ndarray, contrast: float = 1.0, black_L: float = 0.12) -> dict:
    """Neutral base tone curve on OkLab L (in place).

    Black point: when the darkest 0.3 % of the frame sits above ``black_L`` (haze, flat light), half of the excess
    is removed (at most 0.08 L) with a TOE that fades out towards white (cubic in 1-L): shadows get depth, mid-tones
    move < 0.025 L, white stays put. Then a gentle S-curve around L = 0.55 (slope +9 % at mid-grey). Chroma follows
    the change of L (square root) so colours keep their saturation. Returns the parameters used."""
    L = lab[..., 0]
    lo = float(np.percentile(L[::4, ::4], 0.3))
    d = float(min(0.5 * max(lo - black_L, 0.0), 0.08))
    k = F32(0.35 * contrast)
    for y in range(0, lab.shape[0], 256):
        blk = lab[y:y + 256]
        L0 = blk[..., 0]
        L1 = L0 - F32(d) * np.clip((1 - L0) / max(1 - lo, 1e-3), 0, 1) ** 3 if d > 0 else L0
        L1 = np.clip(L1, 0, None)
        L2 = L1 + k * (L1 - F32(0.55)) * np.clip(L1, 0, 1) * np.clip(1 - L1, 0, 1)
        s = np.where(L0 > 1e-4, L2 / np.maximum(L0, 1e-4), 1.0).astype(F32)
        blk[..., 1:] *= np.clip(s, 0.5, 2.0)[..., None] ** F32(0.5)
        blk[..., 0] = L2
    return {"black_in": round(lo, 4), "black_lift_removed": round(d, 4), "contrast": round(float(contrast), 2)}


def render(base_tiff_path, out_path, ev=None, ev_offset=0.0, skin=0.0, sharp=0.0, blem=0.0, web=True,
           save_tiff16=False, seed=None, web_long_side=2560, **_ignored):
    t0 = time.time()
    timings: dict = {}
    img = E.load_base(base_tiff_path)
    H, W, _ = img.shape
    scale = max(H, W) / E.REF_LONG_SIDE_PX
    if ev is None:
        Ys = img[::8, ::8] @ np.asarray(REC2020_LUMA, F32)
        ev = float(np.log2(0.18 / max(float(np.median(Ys)), 1e-6)))
    ev = float(ev) + float(ev_offset)
    if seed is None:
        seed = E.seed_from_file(base_tiff_path)
    rng = np.random.default_rng(int(seed))
    t = time.time()
    M = np.asarray(REC2020_TO_SRGB, F32)
    g = F32(2.0 ** ev)
    lab = np.empty_like(img)
    for y in range(0, H, 256):
        blk = np.maximum((img[y:y + 256] * g) @ M.T, 0)
        lab[y:y + 256] = E.lin_to_oklab(shoulder(blk))
    del img
    curve = base_curve(lab)
    timings["tono"] = time.time() - t
    finfo = {}
    if skin > 0 or blem > 0 or sharp > 0:
        t = time.time()
        people = E.detect_people(lab) if (skin > 0 or blem > 0) else dict(faces=[], lm=[], humans=[], src="none")
        timings["caras"] = time.time() - t
        finfo = E.finish(lab, scale, people["faces"], timings, skin_amt=skin, sharp_amt=sharp, dbg={}, subj=None,
                         blem_amt=blem, lms=people["lm"])
        finfo["det"] = people["src"]
    t = time.time()
    clean = E.lab_to_code(lab)
    del lab
    timings["gamut"] = time.time() - t
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if web:
        wdir = out_path.parent / "web2560"
        wdir.mkdir(exist_ok=True)
        E.save_jpeg(E.make_web(clean, np.random.default_rng(int(seed) + 7), 0.0, long_side=web_long_side),
                    wdir / out_path.name, quality=95)
    t = time.time()
    tp = (rng.random((H, W, 3), dtype=F32) - rng.random((H, W, 3), dtype=F32))
    o8 = np.clip(np.rint(clean + tp), 0, 255).astype(np.uint8)
    del tp
    E.save_jpeg(o8, out_path)
    if save_tiff16:
        o16 = np.clip(np.rint(clean * 257.0), 0, 65535).astype(np.uint16)
        cv2.imwrite(str(out_path.with_suffix(".tif")), o16[..., ::-1])
        del o16
    del clean
    timings["guardar"] = time.time() - t
    sub = o8[::2, ::2]
    clip_any = float(np.mean(np.any(sub >= 254, axis=2)) * 100)
    luma8 = sub.astype(F32) @ E.YW
    return dict(file=Path(base_tiff_path).stem, ev=round(ev, 3), clip_any_pct=round(clip_any, 4),
                luma_p0_5=round(float(np.percentile(luma8, 0.5)), 1),
                luma_p99_9=round(float(np.percentile(luma8, 99.9)), 1), finish=finfo, tone=curve, size=[W, H], seed=int(seed),
                seconds=round(time.time() - t0, 1), timings={k: round(v, 1) for k, v in timings.items()})
