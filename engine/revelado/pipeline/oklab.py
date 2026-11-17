# SPDX-License-Identifier: GPL-3.0-or-later
"""oklab.py (antes qc_base.py) - OkLab y control de calidad de las bases (ruido de croma y franjas de CA).

Funciones:
  oklab(lin_rec2020)              -> HxWx3 OkLab
  display(lin, ev)                -> uint8 sRGB (curva suave de visionado, solo para revisar)
  shadow_patches(lab, n)          -> lista de (y,x,size) de parches planos oscuros
  chroma_noise(lab, patches)      -> std media de a*,b* (OkLab*100) en esos parches
  fringe_map(lab)                 -> mapa de puntuacion de franja (morado/verde junto a bordes)
"""
from __future__ import annotations

import numpy as np
import cv2

from .colorspaces import REC2020_TO_XYZ, REC2020_TO_SRGB, srgb_encode

_M1 = np.array([[0.8189330101, 0.3618667424, -0.1288597137],
                [0.0329845436, 0.9293118715, 0.0361456387],
                [0.0482003018, 0.2643662691, 0.6338517070]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])
LMS_FROM_REC2020 = (_M1 @ REC2020_TO_XYZ).astype(np.float32)
REC2020_FROM_LMS = np.linalg.inv(_M1 @ REC2020_TO_XYZ).astype(np.float32)
M2 = _M2.astype(np.float32)
M2_INV = np.linalg.inv(_M2).astype(np.float32)


def oklab(lin: np.ndarray) -> np.ndarray:
    lms = lin.reshape(-1, 3) @ LMS_FROM_REC2020.T
    lms = np.cbrt(lms)
    return (lms @ M2.T).reshape(lin.shape).astype(np.float32)


def oklab_inv(lab: np.ndarray) -> np.ndarray:
    lms = lab.reshape(-1, 3) @ M2_INV.T
    lms = lms * lms * lms
    return (lms @ REC2020_FROM_LMS.T).reshape(lab.shape).astype(np.float32)


def display(lin: np.ndarray, ev: float) -> np.ndarray:
    x = lin.astype(np.float32) * np.float32(2.0 ** ev)
    x = x.reshape(-1, 3) @ REC2020_TO_SRGB.astype(np.float32).T
    x = x.reshape(lin.shape)
    x = np.maximum(x, 0)
    x = x * 1.25 / (1 + 0.25 * x)
    x = srgb_encode(np.clip(x, 0, 1))
    return (x * 255 + 0.5).astype(np.uint8)[..., ::-1]  # BGR para cv2


def shadow_patches(lab: np.ndarray, n: int = 12, size: int = 48, lmax: float = 0.42, lmin: float = 0.08,
                   seed_exclude=None):
    """Parches planos (estructura baja a escala gruesa) y oscuros. lab debe estar EXPUESTO."""
    L = lab[..., 0]
    Lb = cv2.GaussianBlur(L, (0, 0), 6)
    gx = cv2.Sobel(Lb, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(Lb, cv2.CV_32F, 0, 1, ksize=3)
    g = cv2.blur(np.hypot(gx, gy), (size, size))
    Lm = cv2.blur(L, (size, size))
    h, w = L.shape
    cands = []
    step = size
    for y in range(size, h - 2 * size, step):
        for x in range(size, w - 2 * size, step):
            cy, cx = y + size // 2, x + size // 2
            if lmin < Lm[cy, cx] < lmax:
                cands.append((g[cy, cx], y, x))
    cands.sort()
    out = []
    for gval, y, x in cands:
        if all(abs(y - oy) > 4 * size or abs(x - ox) > 4 * size for oy, ox, _ in out):
            out.append((y, x, size))
        if len(out) >= n:
            break
    return out


def chroma_noise(lab: np.ndarray, patches) -> dict:
    """std de a y b (x100) en cada parche, tras quitar la media (y una rampa lineal) del parche."""
    sa, sb, sl = [], [], []
    for y, x, s in patches:
        p = lab[y:y + s, x:x + s].reshape(-1, 3).astype(np.float64)
        yy, xx = np.mgrid[0:s, 0:s]
        A = np.c_[np.ones(s * s), yy.ravel(), xx.ravel()]
        res = p - A @ np.linalg.lstsq(A, p, rcond=None)[0]
        sl.append(res[:, 0].std() * 100)
        sa.append(res[:, 1].std() * 100)
        sb.append(res[:, 2].std() * 100)
    return {"std_a": round(float(np.mean(sa)), 3), "std_b": round(float(np.mean(sb)), 3),
            "std_L": round(float(np.mean(sl)), 3), "n": len(patches)}


def fringe_map(lab: np.ndarray) -> np.ndarray:
    """Puntuacion de franja: croma morada/verde-cian dentro de una banda de borde de alto contraste."""
    L = lab[..., 0]
    gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
    g = np.hypot(gx, gy)
    band = cv2.dilate((g > 0.12).astype(np.uint8), np.ones((9, 9), np.uint8)).astype(np.float32)
    a, b = lab[..., 1], lab[..., 2]
    purple = np.maximum(0, a) * np.maximum(0, -b) ** 0.5 * 10   # magenta/violeta
    purple = np.maximum(purple, np.maximum(0, -b - np.abs(a)) * 0.5)  # azul-violeta
    green = np.maximum(0, -a) * np.clip(1 - np.abs(b) / (np.abs(a) + 1e-4) * 0.3, 0, 1)
    return band * (purple + green)
