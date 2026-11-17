# SPDX-License-Identifier: GPL-3.0-or-later
"""Matrices y transferencias de color compartidas (lineal Rec.2020 D65 como espacio base)."""
import numpy as np

def _rgb2xyz(prim, white):
    xr, yr, xg, yg, xb, yb = prim
    xw, yw = white
    P = np.array([[xr/yr, xg/yg, xb/yb], [1, 1, 1], [(1-xr-yr)/yr, (1-xg-yg)/yg, (1-xb-yb)/yb]])
    W = np.array([xw/yw, 1.0, (1-xw-yw)/yw])
    S = np.linalg.solve(P, W)
    return P * S

D65 = (0.3127, 0.3290)
D50 = (0.3457, 0.3585)
REC2020_TO_XYZ = _rgb2xyz((0.708, 0.292, 0.170, 0.797, 0.131, 0.046), D65)
XYZ_TO_REC2020 = np.linalg.inv(REC2020_TO_XYZ)
SRGB_TO_XYZ = _rgb2xyz((0.64, 0.33, 0.30, 0.60, 0.15, 0.06), D65)
XYZ_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ)
PROPHOTO_TO_XYZ_D50 = _rgb2xyz((0.7347, 0.2653, 0.1596, 0.8404, 0.0366, 0.0001), D50)
BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])

def cat(src_white, dst_white):
    def xyz(w):
        return np.array([w[0]/w[1], 1.0, (1-w[0]-w[1])/w[1]])
    s = BRADFORD @ xyz(src_white); d = BRADFORD @ xyz(dst_white)
    return np.linalg.inv(BRADFORD) @ np.diag(d/s) @ BRADFORD

REC2020_TO_SRGB = XYZ_TO_SRGB @ REC2020_TO_XYZ
PROPHOTO_TO_REC2020 = XYZ_TO_REC2020 @ cat(D50, D65) @ PROPHOTO_TO_XYZ_D50
REC2020_LUMA = REC2020_TO_XYZ[1]  # Y weights (0.2627, 0.6780, 0.0593)

def srgb_decode(v):
    """Inversa de la TRC sRGB (IEC 61966-2-1), extendida simetricamente para v<0 y v>1."""
    v = np.asarray(v, dtype=np.float32)
    a = np.abs(v)
    out = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    return (np.sign(v) * out).astype(np.float32)

def srgb_encode(v):
    v = np.asarray(v, dtype=np.float32)
    a = np.abs(v)
    out = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1/2.4) - 0.055)
    return (np.sign(v) * out).astype(np.float32)

def apply_matrix(img, M):
    """img HxWx3 float32 @ M^T, en sitio por bloques para ahorrar RAM."""
    M = np.asarray(M, dtype=np.float32)
    out = np.empty_like(img, dtype=np.float32)
    step = 512
    for y in range(0, img.shape[0], step):
        out[y:y+step] = img[y:y+step] @ M.T
    return out
