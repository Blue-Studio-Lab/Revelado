# SPDX-License-Identifier: GPL-3.0-or-later
"""
hair.py (antes cabello.py v4.2) - LIMPIEZA DE CABELLO, sobre la BASE lineal. Sin IA generativa, sin torch.

EXPERIMENTAL y DESACTIVADO por defecto. Necesita el mate de personas y los puntos de referencia de cara; hoy solo
existen con el ayudante Vision de macOS: en otros sistemas el modulo devuelve status='skipped' (nunca bloquea).
Incluye la PUERTA DE SEGURIDAD del lote de referencia (hair_metrics + hair_gate): el retoque solo se aplica si cae
sobre fondo liso; si no, la foto queda marcada 'cabello_pendiente_manual'.

Donde va:  ARW -> develop -> editorial -> **cabello** -> motor (look, piel, grano...). Al ir antes del look y del grano,
las zonas retocadas reciben el mismo color y el mismo grano que el resto.

Metodo (v4; nace de cabello_A "silueta primero" + la idea de cabello_B de estimar el fondo sin la hebra)
-----------------------------------------------------------------------------------------------------------------
Por foto: caras (Vision, models/facelm) + mate de personas (Vision, models/personseg) a baja resolucion. Las cabezas se
agrupan en zonas y cada zona se analiza a ESCALA DE DETECCION (cara <= U_DET px, recorte <= MAX_DET_PX), nunca la
imagen entera en float; el resultado se aplica a resolucion completa por franjas.
 1. MASA T (lo que no se toca) = densidad de microtextura enfocada (peinado, incluso el 'peine' con cielo entre
    mechones) | nucleo del mate | masa solida (lo que una mediana conserva y es distinto del fondo de fuera).
    Cierre + apertura: la FUERZA fija el umbral de densidad y la apertura (fuerte = silueta mas ajustada).
 2. HEBRAS = residuo de mediana (radio R, calculada SIN la masa) fuera de T que se desvia del fondo HACIA el color
    del pelo cercano, con histeresis; componentes que nacen en el pelo (o sueltos muy cerca) se quitan ENTEROS, de
    la punta a la masa, con rampa corta al llegar a T y un residuo pequeno de contraste (borde natural, no casco).
 3. RELLENO = mediana grande suavizada (conserva nubes/montes desenfocados) + correccion de baja frecuencia medida en
    pixeles limpios + ruido fino medido alrededor. En la vecindad de lo retirado se quitan tambien halos y mechones
    desenfocados anchos (mate por diferencia, suave) -> sin parches.
 4. FONDO CON ESTRUCTURA (edificios, monte/agua enfocados, ramas): alli solo hebras finas y nitidas nacidas en el
    pelo, rellenas con la mediana local (rough_mode='thin'), o nada (rough_mode='skip').
 5. Hebras sobre la PIEL: lineas finas y largas dentro de la piel de cada cara que nacen en el pelo o en el borde;
    relleno push-pull desde la piel vecina + ruido. Ojos/pestanas, cejas, labios y nariz protegidos.
Nunca se tocan: rasgos protegidos, piel fuera de hebras, barba/menton, ropa/borrego/manos (masa que no es pelo y su
borde), fondo lejano. Limites conocidos: hebras cruzadas SOBRE la masa de pelo y las que pasan por encima de cejas/ojos.

Ajustes por foto (argumento overrides de clean()):
  {"skip": bool, "strength": "suave|medio|fuerte", "skin": bool, "params": {...STRENGTH...},
              "exclude": [[x0,y0,x1,y1],...]  (px completos: no tocar),
              "force":   [[x0,y0,x1,y1],...]  (px completos: quitar todo lo fino aunque no sea 'pelo'/'liso')}

API:  clean(base_lin, strength='fuerte', debug_dir=None, ev=None, stem='img', overrides=None, crops=None)
          -> (base_out, report)
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from .colorspaces import REC2020_TO_SRGB, srgb_encode
from . import faces as faces_mod

F32 = np.float32
VERSION = "4.2"   # 4.2 (QA): relleno de islas encerradas por el relleno (parametro 'island')
LUMA = np.array([0.2627, 0.6780, 0.0593], F32)


# ----------------------------------------------------------------------------------------------
# helpers from the reference engine's editorial module (only what this module uses)
# ----------------------------------------------------------------------------------------------
class Enc:
    """Codificacion perceptual log2 con 'pie' (eps 6 stops bajo el gris medio expuesto) de la base lineal."""

    def __init__(self, ev):
        self.ev = float(ev)
        self.eps = F32(0.18 * 2.0 ** (-self.ev - 6.0))

    def enc(self, x):
        return np.log2(np.maximum(x, 0) + self.eps).astype(F32)

    def dec(self, p):
        return (np.exp2(p) - self.eps).astype(F32)

    def disp(self, x):
        """Lineal -> sRGB 8 bit para ver/detectar (exposicion ev, hombro suave)."""
        y = np.maximum(x * F32(2.0 ** self.ev), 0) @ REC2020_TO_SRGB.T.astype(F32)
        y = np.maximum(y, 0)
        y = y * (1 + y / 16.0) / (1 + y)
        return np.clip(srgb_encode(np.clip(y, 0, 1)) * 255 + 0.5, 0, 255).astype(np.uint8)


def robust_sigma(v):
    v = v[np.isfinite(v)]
    if v.size == 0:
        return 0.0
    return float(np.median(np.abs(v - np.median(v))) * 1.4826)


def _dilate_ed(m, r):
    if r <= 0:
        return m.copy()
    r = max(1, int(round(r)))
    return cv2.dilate(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0


def _protect_from(faces, shape, origin=(0, 0), up=1.0):
    pm = np.zeros(shape, np.uint8)
    ox, oy = origin
    for f in faces:
        if "leftEye" not in f or "rightEye" not in f:
            continue
        iod = float(np.hypot(*(f["leftEye"].mean(0) - f["rightEye"].mean(0)))) / up
        for kname, grow in (("leftEye", 0.22), ("rightEye", 0.22), ("leftEyebrow", 0.12), ("rightEyebrow", 0.12),
                            ("outerLips", 0.10), ("nose", 0.06)):
            if kname not in f or len(f[kname]) < 3:
                continue
            pts = (f[kname] / up - [ox, oy]).astype(np.float32)
            if (pts[:, 0].max() < -50 or pts[:, 1].max() < -50 or pts[:, 0].min() > shape[1] + 50
                    or pts[:, 1].min() > shape[0] + 50):
                continue
            hull = cv2.convexHull(pts)
            one = np.zeros(shape, np.uint8)
            cv2.fillConvexPoly(one, np.round(hull).astype(np.int32), 1)
            w = float(pts[:, 0].max() - pts[:, 0].min())
            r = grow * max(w, 0.5 * iod) if kname != "nose" else grow * max(iod, w)
            pm |= _dilate_ed(one > 0, max(2, r)).astype(np.uint8)
    return pm


def run_facelm(rgb8):
    f = faces_mod.landmarks_full(rgb8)
    if f is None:
        raise RuntimeError("sin puntos de referencia de cara (ayudante Vision de macOS no disponible)")
    return f
MAX_DET_PX = 6.5e6      # tope de pixeles del recorte de analisis (memoria/tiempo)
U_DET = 800.0          # la cara se lleva como mucho a este tamano (px) para ANALIZAR

# Longitudes en fracciones de u = tamano de la caja de cara.
#   close: cierre (lo que se tolera de zona rala dentro del peinado)   open: mechon minimo que se conserva (radio)
#   ramp: rampa de desvanecido al llegar a la silueta   resid: contraste que queda pegado al borde
#   k_dev: umbral de evidencia (MADs)   z_lo/z_hi: histeresis de crestas   reach: alcance desde el pelo
#   skin_len: longitud minima de hebra sobre piel   skin_z: umbral de cresta sobre piel
STRENGTH = {
    "suave":  dict(dens_t=0.30, win=0.020, med=0.012, close=0.016, open=0.008, ramp=0.016, resid=0.45, z_lo=3.6,
                   z_hi=7.0, reach=0.40, skin_len=0.30, skin_z=5.0, skin_free=9.0),
    "medio":  dict(dens_t=0.38, win=0.020, med=0.018, close=0.014, open=0.012, ramp=0.012, resid=0.28, taper=1.4, z_lo=2.9,
                   z_hi=5.8, reach=0.70, skin_len=0.20, skin_z=4.2, skin_free=3.0),
    "fuerte": dict(dens_t=0.52, win=0.020, med=0.030, close=0.012, open=0.025, ramp=0.016, resid=0.10, taper=1.8, z_lo=2.3,
                   z_hi=4.6, reach=1.00, skin_len=0.12, skin_z=3.2, skin_free=2.5),
}


# ----------------------------------------------------------------------------------------------
# utilidades
# ----------------------------------------------------------------------------------------------
def disk(r):
    r = max(1, int(round(r)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def dil(m, r):
    return cv2.dilate(m.astype(np.uint8), disk(r)) > 0 if r >= 1 else m.astype(bool)


def ero(m, r):
    return cv2.erode(m.astype(np.uint8), disk(r)) > 0 if r >= 1 else m.astype(bool)


def mclose(m, r):
    if r < 1:
        return m.astype(bool)
    r = int(round(r))
    p = cv2.copyMakeBorder(m.astype(np.uint8), r, r, r, r, cv2.BORDER_CONSTANT, value=0)
    return cv2.morphologyEx(p, cv2.MORPH_CLOSE, disk(r))[r:-r, r:-r] > 0


def mopen(m, r):
    return cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, disk(r)) > 0 if r >= 1 else m.astype(bool)


def luma(p):
    return (p @ LUMA).astype(F32)


def pushpull(P, w, conf=1.0):
    """Relleno por convolucion normalizada piramidal. P HxW[xC] float32, w HxW (1 = dato valido).
    Los validos se devuelven intactos; los huecos se interpolan (suave) desde los validos mas cercanos."""
    P = P.astype(F32)
    w = w.astype(F32)
    if P.ndim == 2:
        return pushpull(P[..., None], w, conf)[..., 0]
    Ps, Ws = [P * w[..., None]], [w]
    while min(Ws[-1].shape) > 2:
        h, wd = Ws[-1].shape
        sz = ((wd + 1) // 2, (h + 1) // 2)
        Pn = cv2.resize(Ps[-1], sz, interpolation=cv2.INTER_AREA)
        Ps.append(Pn if Pn.ndim == 3 else Pn[..., None])
        Ws.append(cv2.resize(Ws[-1], sz, interpolation=cv2.INTER_AREA))
    Fl = Ps[-1] / np.maximum(Ws[-1], 1e-6)[..., None]
    for l in range(len(Ps) - 2, -1, -1):
        h, wd = Ws[l].shape
        up = cv2.resize(Fl, (wd, h), interpolation=cv2.INTER_LINEAR)
        if up.ndim == 2:
            up = up[..., None]
        c = np.clip(Ws[l] * conf, 0, 1)[..., None]
        Fl = c * (Ps[l] / np.maximum(Ws[l], 1e-6)[..., None]) + (1 - c) * up
    return Fl


def block_stats(v, valid, bs):
    """Mediana y MAD-sigma por bloques de v sobre 'valid', extrapoladas (push-pull) y suavizadas -> mapas HxW."""
    H, W = v.shape
    nh, nw = max(1, H // bs), max(1, W // bs)
    Hc, Wc = nh * bs, nw * bs
    a = v[:Hc, :Wc].reshape(nh, bs, nw, bs).transpose(0, 2, 1, 3).reshape(nh, nw, bs * bs).astype(np.float32)
    m = valid[:Hc, :Wc].reshape(nh, bs, nw, bs).transpose(0, 2, 1, 3).reshape(nh, nw, bs * bs)
    a = np.where(m, a, np.nan)
    ok = m.sum(-1) >= 0.35 * bs * bs
    with np.errstate(all="ignore"):
        med = np.nanmedian(a, -1)
        mad = np.nanmedian(np.abs(a - med[..., None]), -1) * 1.4826
    med = np.where(ok, med, 0).astype(F32)
    mad = np.where(ok, mad, 0).astype(F32)
    if not ok.any():
        g = float(np.median(v))
        s = float(np.median(np.abs(v - g)) * 1.4826)
        return np.full((H, W), g, F32), np.full((H, W), max(s, 1e-4), F32)
    okf = ok.astype(F32)
    med = cv2.GaussianBlur(pushpull(med, okf), (0, 0), 1.0)
    mad = cv2.GaussianBlur(pushpull(mad, okf), (0, 0), 1.0)
    med = cv2.resize(med, (W, H), interpolation=cv2.INTER_CUBIC)
    mad = cv2.resize(mad, (W, H), interpolation=cv2.INTER_CUBIC)
    return med, np.maximum(mad, 1e-4)


def hysteresis(z, lo, hi):
    """Componentes de z>lo que contienen algun z>hi."""
    low = (z > lo).astype(np.uint8)
    n, lab = cv2.connectedComponents(low, connectivity=8)
    if n <= 1:
        return np.zeros(z.shape, bool)
    keep = np.zeros(n, bool)
    keep[np.unique(lab[z > hi])] = True
    keep[0] = False
    return keep[lab]


def connected_to(mask, seeds):
    """Componentes de mask (8-conexos) que contienen algun pixel de seeds."""
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros(mask.shape, bool)
    ids = np.unique(lab[seeds & mask])
    keep = np.zeros(n, bool)
    keep[ids] = True
    keep[0] = False
    return keep[lab]


def auto_ev(img):
    L = img[::8, ::8] @ LUMA
    return float(np.clip(np.log2(0.14 / max(float(np.median(L)), 1e-5)), -2, 8))


def boxes_mask(boxes, roi, sd, shape):
    """Cajas [x0,y0,x1,y1] en px completos -> mascara a escala de deteccion del ROI."""
    m = np.zeros(shape, bool)
    x0, y0 = roi[0], roi[1]
    for b in boxes or []:
        bx0, by0, bx1, by1 = [(v - o) * sd for v, o in zip(b, (x0, y0, x0, y0))]
        bx0, by0 = int(max(0, np.floor(bx0))), int(max(0, np.floor(by0)))
        bx1, by1 = int(min(shape[1], np.ceil(bx1))), int(min(shape[0], np.ceil(by1)))
        if bx1 > bx0 and by1 > by0:
            m[by0:by1, bx0:bx1] = True
    return m


# ----------------------------------------------------------------------------------------------
# Vision: mate de personas, geometria de caras
# ----------------------------------------------------------------------------------------------
def person_matte(rgb8):
    """Mate de personas (0..1 float32, tamano de rgb8) con Vision de macOS. None si no esta disponible."""
    return faces_mod.person_matte(rgb8)


def face_geom(f):
    """Centro, tamano u, direccion 'arriba' (de labios a ojos) de una cara de Vision (px completos)."""
    x, y, w, h = [float(v) for v in f["box"]]
    c = np.array([x + w / 2, y + h / 2])
    u = max(w, h)
    up = np.array([0.0, -1.0])
    try:
        eyes = (f["leftEye"].mean(0) + f["rightEye"].mean(0)) / 2
        lips = f["outerLips"].mean(0)
        v = eyes - lips
        n = float(np.hypot(*v))
        if n > 0.1 * u:
            up = v / n
    except Exception:
        pass
    return c, u, up


def fallback_heads(matte_s, faces_geo, fs):
    """Personas del mate SIN cara detectada (de espaldas, cara tapada): cabeza ~ extremo superior del componente."""
    out = []
    n, lab, st, _ = cv2.connectedComponentsWithStats((matte_s > 0.5).astype(np.uint8), connectivity=8)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 0.002 * matte_s.size or h < 40:
            continue
        comp = lab == i
        compd = dil(comp, 5)
        has = False
        for (c, u, up, _f) in faces_geo:
            cx, cy = int(round(c[0] * fs)), int(round(c[1] * fs))
            if 0 <= cy < comp.shape[0] and 0 <= cx < comp.shape[1] and compd[cy, cx]:
                has = True
        if has:
            continue
        touches = (y + h) >= matte_s.shape[0] - 2
        uh = h * (0.30 if touches else 0.135)
        ys, xs = np.nonzero(comp[y:y + int(max(4, uh))])
        if len(xs) < 20:
            continue
        c = np.array([xs.mean(), y + ys.mean() + 0.15 * uh]) / fs
        out.append((c, uh / fs * 0.9, np.array([0.0, -1.0]), None))
    return out


# ----------------------------------------------------------------------------------------------
# analisis a escala de deteccion
# ----------------------------------------------------------------------------------------------
def tophats(Ls, r):
    se = disk(r)
    cl = cv2.morphologyEx(Ls, cv2.MORPH_CLOSE, se)
    op = cv2.morphologyEx(Ls, cv2.MORPH_OPEN, se)
    return (cl - Ls).max(-1), (Ls - op).max(-1)


def skin_masks(Pd, core, heads, protect, texture):
    """Piel por cara (croma log cercana a la muestra mejillas/nariz, poco texturada, conectada a la cara) y
    'parecido a piel' en todo el recorte (manos, cuello). Devuelve (K, skinlike, L_piel_mediana|None)."""
    H, W = core.shape
    L = luma(Pd)
    c1 = Pd[..., 0] - Pd[..., 1]
    c2 = Pd[..., 2] - Pd[..., 1]
    K = np.zeros((H, W), bool)
    like = np.zeros((H, W), bool)
    yy, xx = np.mgrid[0:H, 0:W]
    mls = []
    for hd in heads:
        f = hd["face_d"]
        if f is None:
            continue
        pts = [f[k] for k in ("leftEye", "rightEye", "outerLips", "nose") if k in f and len(f[k]) >= 2]
        if len(pts) < 3:
            continue
        hull = cv2.convexHull(np.vstack(pts).astype(np.float32))
        samp = np.zeros((H, W), np.uint8)
        cv2.fillConvexPoly(samp, np.round(hull).astype(np.int32), 1)
        samp = (samp > 0) & core & ~protect
        if samp.sum() < 50:
            continue
        m1, m2, ml = np.median(c1[samp]), np.median(c2[samp]), np.median(L[samp])
        s1 = max(robust_sigma(c1[samp]), 0.03)
        s2 = max(robust_sigma(c2[samp]), 0.03)
        sl = max(robust_sigma(L[samp]), 0.15)
        d2 = ((c1 - m1) / (3.2 * s1)) ** 2 + ((c2 - m2) / (3.2 * s2)) ** 2
        cx, cy = hd["c_d"]
        u = hd["u_d"]
        base = (d2 < 1) & (L > ml - 4.0 * sl) & (L < ml + 3.0 * sl) & core & (texture < 0.22)
        like |= mopen(base, max(2, 0.006 * u))
        zone = (xx - cx) ** 2 + (yy - cy) ** 2 < (1.15 * u) ** 2
        k = mopen(base & zone, max(2, 0.006 * u)) | samp
        k = connected_to(k, samp)
        k = mclose(k, max(2, 0.012 * u))
        K |= k
        mls.append(float(ml))
    return K, like, (float(np.median(mls)) if mls else None)


def hair_mask(Pd, body, K, skinlike, heads, texture, l_skin):
    """Pelo por cabeza: crecimiento por color (histograma L,c1,c2 actualizado en 3 pasadas) desde la coronilla dentro
    de 'body', sin piel, sin blancos/cremas (mas claros que la piel: borrego, abrigos), con textura de mechones."""
    from scipy.ndimage import gaussian_filter
    H, W = body.shape
    L = luma(Pd)
    c1 = Pd[..., 0] - Pd[..., 1]
    c2 = Pd[..., 2] - Pd[..., 1]
    yy, xx = np.mgrid[0:H, 0:W]
    Hm = np.zeros((H, W), bool)
    NB = 24
    q = np.stack([np.clip((L + 12) / 12 * NB, 0, NB - 0.01), np.clip((c1 + 1.5) / 3 * NB, 0, NB - 0.01),
                  np.clip((c2 + 1.5) / 3 * NB, 0, NB - 0.01)], -1).astype(np.int32)
    idx = (q[..., 0] * NB + q[..., 1]) * NB + q[..., 2]
    bright = (L > l_skin + 0.55) if l_skin is not None else np.zeros((H, W), bool)
    for hd in heads:
        cx, cy = hd["c_d"]
        u = hd["u_d"]
        up = hd["up"]
        along = (xx - cx) * up[0] + (yy - cy) * up[1]          # >0 hacia la coronilla
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        if hd["face_d"] is None:
            Hm |= body & (r2 < (1.6 * u) ** 2)
            continue
        cap = body & ~K & ~bright & (r2 < (1.3 * u) ** 2) & (along > -0.10 * u)
        seeds = cap & (along > 0.30 * u) & ~skinlike
        if seeds.sum() < 80:
            seeds = cap
        if seeds.sum() < 30:
            continue
        reach = (r2 < (3.6 * u) ** 2) & ((along > -3.4 * u))
        allowed = body & ~K & ~bright & reach
        R = seeds
        for it in range(3):
            hist = np.bincount(idx[R], minlength=NB ** 3).astype(F32).reshape(NB, NB, NB)
            hist = gaussian_filter(hist, 1.0)
            hist /= max(hist.max(), 1e-6)
            like = hist.reshape(-1)[idx] > 0.015
            cand = allowed & ((like & (texture > 0.03) & ~skinlike) | cap)
            cand = mopen(cand, max(1, 0.004 * u)) | seeds
            R = connected_to(cand, seeds)
        Hm |= R
    return Hm


def protect_tight(faces, shape):
    """Proteccion AJUSTADA de rasgos (para hebras sobre piel): ojos+pestanas, cejas, labios y nariz casi sin margen."""
    pm = np.zeros(shape, np.uint8)
    for f in faces:
        for kname, grow in (("leftEye", 0.20), ("rightEye", 0.20), ("leftEyebrow", 0.03), ("rightEyebrow", 0.03),
                            ("outerLips", 0.05), ("nose", 0.0)):
            if kname not in f or len(f[kname]) < 3:
                continue
            pts = f[kname].astype(np.float32)
            if (pts[:, 0].max() < -50 or pts[:, 1].max() < -50 or pts[:, 0].min() > shape[1] + 50
                    or pts[:, 1].min() > shape[0] + 50):
                continue
            one = np.zeros(shape, np.uint8)
            cv2.fillConvexPoly(one, np.round(cv2.convexHull(pts)).astype(np.int32), 1)
            w = float(pts[:, 0].max() - pts[:, 0].min())
            pm |= dil(one > 0, max(1.5, grow * w)).astype(np.uint8)
    return pm > 0


def noise_map(L, hp):
    """Sigma del ruido fino por nivel de luminancia (p10 de la desviacion local por tramo de L) -> mapa HxW."""
    s = np.sqrt(cv2.blur(hp * hp, (7, 7)))
    Lb = cv2.blur(L, (7, 7))
    l, sv = Lb[::3, ::3].ravel(), s[::3, ::3].ravel()
    ed_ = np.quantile(l, np.linspace(0, 1, 13))
    xs, ys = [], []
    for a_, b_ in zip(ed_[:-1], ed_[1:]):
        m = (l >= a_) & (l <= b_)
        if m.sum() > 200:
            xs.append(0.5 * (a_ + b_))
            ys.append(float(np.percentile(sv[m], 10)) * 1.25)
    if not xs:
        return np.full(L.shape, 0.02, F32)
    return np.clip(np.interp(Lb, xs, ys), 0.006, 0.09).astype(F32)


def median_bg(Pd, R):
    """Mediana de radio R (ventana 2R+1) por canal sobre una cuantizacion a 8 bit del recorte: borra todo lo mas fino
    que ~R (hebras, mechones finos) y conserva masas y degradados. Devuelve (Mm float32, paso de cuantizacion)."""
    lo, hi = np.percentile(Pd[::4, ::4], (0.05, 99.95))
    step = max(float(hi - lo), 1e-3) / 255.0
    q = np.clip((Pd - lo) / step + 0.5, 0, 255).astype(np.uint8)
    k = 2 * int(round(R)) + 1
    m = cv2.medianBlur(q, k)
    return m.astype(F32) * F32(step) + F32(lo), step


def analyze(Pd, matte_d, heads, prm, protect, excl, force, want_skin=True, protect_t=None):
    """Analisis a escala de deteccion. Devuelve dict con T, alpha (retirada sobre fondo), a_skin, fill, etc.
      E   = microtextura (paso alto fino sobre el ruido)  -> densidad -> MASA T (pelo enfocado, ropa, etc.)
      D   = residuo de mediana (lo fino que una mediana de radio R borra) -> HEBRAS candidatas fuera de T
    """
    H, W = matte_d.shape
    u = float(np.median([h["u_d"] for h in heads]))
    info = {}
    core = matte_d > 0.5
    L = luma(Pd)
    hp = L - cv2.GaussianBlur(L, (0, 0), 1.0)
    sn = noise_map(L, hp)

    # --- masa T: densidad de microtextura | nucleo del mate | masa solida (lo que la mediana conserva dentro del mate
    #     y es distinto del fondo extrapolado desde fuera: pelo liso sin textura, ropa)
    R = max(4.0, prm["med"] * u)
    R2 = max(R * 2.0, prm.get("med2", 0.07) * u)
    Ls = cv2.GaussianBlur(Pd, (0, 0), 0.7)
    Mm, step = median_bg(Pd, R)
    E = (np.abs(hp) > prm.get("k_e", 2.6) * sn).astype(F32)
    sw = max(6.0, prm["win"] * u)
    dens = cv2.GaussianBlur(E, (0, 0), sw)
    deep = ero(core, max(4, prm.get("deep", 0.045) * u))
    outside = ~dil(matte_d > 0.08, max(3, 0.03 * u))
    if outside.mean() > 0.002:
        Bout = pushpull(Mm, outside.astype(F32))
        solid = core & (np.abs(Mm - Bout).max(-1) > prm.get("solid_t", 0.22))
        del Bout
    else:
        solid = core
    dense = dens > prm["dens_t"]
    del dens, hp
    # enfoque de las cabezas: fraccion de microtextura en el nucleo del mate alrededor de cada cabeza
    yy, xx = np.mgrid[0:H, 0:W].astype(F32)
    hz = np.zeros((H, W), bool)
    for hd in heads:
        hz |= (xx - hd["c_d"][0]) ** 2 + (yy - hd["c_d"][1]) ** 2 < (1.3 * hd["u_d"]) ** 2
    hz &= core
    focus = float(E[hz].mean()) if hz.any() else 0.0
    info["focus"] = round(focus, 3)
    del hz
    if focus < prm.get("focus_min", 0.0):
        z = np.zeros((H, W), bool)
        zf = np.zeros((H, W), F32)
        info.update(skipped="cabezas desenfocadas", cand_comp=0, removed_comp=0, skin_strands=0)
        return dict(T=core, alpha=zf, src=z, K=z, Hm=z, smooth=~z, protect=protect, info=info, fill=Pd, thin=z,
                    a_skin=zf)
    rc = max(2, prm["close"] * u)
    ro = max(2, prm["open"] * u)
    T = mopen(mclose(dense | deep | solid, rc), ro) | deep
    T = connected_to(T, deep)
    info.update(sw=round(sw, 1), rc=round(rc, 1), ro=round(ro, 1))

    # --- medianas SIN la masa (T se sustituye por el fondo extrapolado: la mediana junto al pelo ya no 've' pelo):
    #     R1 borra hebras -> residuo D;  R2 (a media resolucion) borra tambien mechones/halos anchos -> fondo limpio
    Tm = dil(T, 2)
    Pb = Pd.copy()
    if Tm.any() and not Tm.all():
        Pb[Tm] = pushpull(Pd, (~Tm).astype(F32))[Tm]
    Mm, step = median_bg(Pb, R)
    Ph = cv2.resize(Pb, (max(4, W // 2), max(4, H // 2)), interpolation=cv2.INTER_AREA)
    Mh, _ = median_bg(Ph, R2 / 2)
    Mm2 = cv2.resize(Mh, (W, H), interpolation=cv2.INTER_LINEAR)
    del Ph, Mh, Pb
    dv3 = Ls - Mm
    Dv = np.abs(dv3).max(-1)
    zD = Dv / np.maximum(sn, 0.6 * step)
    floor = max(prm.get("d_floor", 0.05), 1.2 * step)
    thin = hysteresis(np.where((Dv > floor) & ~T, zD, 0), prm["z_lo"], prm["z_hi"])
    weak = (zD > 0.6 * prm["z_lo"]) & (Dv > 0.6 * floor) & ~T
    info.update(R=round(R, 1), R2=round(R2, 1), q_step=round(step, 4))

    # --- textura, piel y pelo
    texture = cv2.GaussianBlur(E, (0, 0), max(3.0, 0.012 * u))
    K, skinlike, l_skin = skin_masks(Pd, core, heads, protect, texture * 0.4)
    Hm = hair_mask(Pd, T, K, skinlike, heads, texture, l_skin)
    hairT = Hm & T
    # guarda de barba/menton: nada de lo que nace bajo la cara (no es peinado)
    beard = np.zeros((H, W), bool)
    facez = np.zeros((H, W), bool)                               # zona de cara (para hebras sobre piel)
    lowface = np.zeros((H, W), bool)                             # mitad baja: surcos de sonrisa, bigote, barba
    for hd in heads:
        if hd["face_d"] is None:
            continue
        cx, cy = hd["c_d"]
        ud = hd["u_d"]
        up = hd["up"]
        al = (xx - cx) * up[0] + (yy - cy) * up[1]
        la = (xx - cx) * up[1] - (yy - cy) * up[0]
        beard |= ((al + 0.55 * ud) / (0.55 * ud)) ** 2 + (la / (0.60 * ud)) ** 2 < 1
        facez |= ((xx - cx) ** 2 + (yy - cy) ** 2 < (0.75 * ud) ** 2) & (al > -0.42 * ud)
        lowface |= ((xx - cx) ** 2 + (yy - cy) ** 2 < (0.9 * ud) ** 2) & (al < -0.02 * ud)
    hairT &= ~beard

    # --- candidatos fuera de T, cerca del pelo: residuo fino que se desvia del fondo HACIA el color del pelo cercano
    #     (pixel a pixel: ni brillos, ni huecos de cielo entre mechones, ni marcos claros)
    dT = cv2.distanceTransform((~T).astype(np.uint8), cv2.DIST_L2, 5)
    dH = cv2.distanceTransform((~(hairT | force)).astype(np.uint8), cv2.DIST_L2, 5)
    band = (dH < prm["reach"] * u) & ~T
    nonhair = T & ~dil(hairT, max(3, 0.02 * u))                   # masa que no es pelo: piel, ropa, borrego, manos
    block = dil(protect, max(2, 0.035 * u)) | excl | K | (beard & ~force) | (dil(nonhair, max(3, 0.004 * u)) & ~force)
    Hc = pushpull(Pd, ero(hairT, 2).astype(F32)) if ero(hairT, 2).any() else Mm - 1.0
    proj = (dv3 * (Hc - Mm)).sum(-1)
    # si el pelo cercano y el fondo son de color parecido la direccion no dice nada (brillos de hebras claras
    # sobre fondo oscuro): alli vale cualquier polaridad y deciden la finura y la raiz en el pelo
    amb = np.abs(Hc - Mm).max(-1) < prm.get("dir_amb", 0.35)
    okdir = (proj > 0) | amb | force
    del amb
    cand = thin & band & ~block & okdir
    weakc = weak & okdir & band & ~block
    # fondo con estructura (edificios, ramas, agua, monte enfocado) = residuo fino que NO va hacia el pelo, o
    # microtextura que no es de las hebras candidatas. Alli NO se toca nada (se salta), salvo 'force'.
    #   (se mide FUERA del borde del pelo -a mas de 0.03u de T- y se normaliza: el propio borde no cuenta)
    sr = max(8.0, 0.04 * u)
    Mk = (~dil(T, max(3, 0.03 * u)) & ~dil(cand | weakc, 2)).astype(F32)
    den = np.maximum(cv2.GaussianBlur(Mk, (0, 0), sr), 1e-3)
    wden = np.clip(den / 0.15, 0, 1)                             # junto a la cabeza no hay medida: se extrapola
    anti = pushpull(cv2.GaussianBlur((thin & ~okdir).astype(F32) * Mk, (0, 0), sr) / den, wden)
    tex = pushpull(cv2.GaussianBlur(E * Mk, (0, 0), sr) / den, wden)
    okden = np.ones((H, W), bool)
    del wden
    #   + bordes que SOBREVIVEN a la mediana grande (marcos, aristas de edificios, horizonte enfocado)
    Lg = cv2.GaussianBlur(luma(Mm2), (0, 0), 1.5)
    G = np.hypot(cv2.Sobel(Lg, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(Lg, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
    edge = cv2.GaussianBlur(((G > prm.get("g_t", 0.035)) & ~T).astype(F32), (0, 0), max(4.0, 0.02 * u))
    rough = (okden & ((anti > prm.get("anti_t", 0.03)) | (tex > prm.get("rough_t", 0.12)))) \
        | (edge > prm.get("edge_t", 0.10))
    info["rough_parts"] = [round(float(((anti > prm.get("anti_t", 0.03)) & okden & band).mean() * 100), 1),
                           round(float(((tex > prm.get("rough_t", 0.12)) & okden & band).mean() * 100), 1),
                           round(float(((edge > prm.get("edge_t", 0.10)) & band).mean() * 100), 1)]
    rough = dil(rough, max(2, 0.01 * u)) & ~force
    del Lg, G, edge, anti, den, Mk
    smooth = ~rough
    info["rough_pct"] = round(float((rough & band).sum()) / max(1, int(band.sum())) * 100, 1)
    # sobre fondo con estructura: 'skip' = no tocar | 'thin' = solo hebras finas (no manchas ni marcos anchos),
    # cortas de alcance y nacidas en el pelo; se rellenan con la mediana local (conserva la textura del fondo)
    rmode = prm.get("rough_mode", "thin")
    if rmode == "thin":
        blob = dil(mopen(thin, max(2, 0.005 * u)), 2)
        texr = dil(okden & (tex > prm.get("rough_t", 0.12)), max(2, 0.01 * u))      # textura ENFOCADA (agua, monte,
        okr = ~blob & ~texr & (dH < prm.get("soft_reach", 0.8) * u) & dil(E > 0, 1)  # ramas): se salta. Desenfocada
        #                                                           con estructura: solo hebra fina y nitida
        cand &= smooth | okr
        weakc &= smooth
    else:
        cand &= smooth
        weakc &= smooth
    candc = (dil(cand, 2) | (weakc & dil(cand, 4))) & band & ~block
    if rmode != "thin":
        candc &= smooth
    n, lab, st, _ = cv2.connectedComponentsWithStats(candc.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    if n > 1:
        flat = lab.ravel()
        area = np.maximum(st[:, 4], 1)
        root = np.zeros(n, bool)
        root[np.unique(lab[dil(hairT | force, 3) & candc])] = True
        dmin = np.full(n, 1e9, F32)
        np.minimum.at(dmin, flat, dH.ravel())
        smf = np.bincount(flat, weights=smooth.ravel().astype(np.float64), minlength=n) / area
        ext = np.maximum(st[:, 2], st[:, 3])
        floating = (dmin < prm.get("float_d", 0.35) * u) & (ext >= max(5, 0.010 * u))
        notroot = np.zeros(n, bool)                               # tocan masa que NO es pelo (ropa, borrego, manos)
        notroot[np.unique(lab[dil(T & ~dil(hairT, max(3, 0.02 * u)), 3) & candc])] = True
        keep = root | (floating & ~notroot)
        # sobre fondo rugoso: solo nacidas en el pelo y alargadas
        keep &= (smf > 0.7) | (root & (ext >= max(8, 0.04 * u)))
        keep[0] = False
        if prm.get("debug_comps"):
            order = np.argsort(-area[1:])[:14] + 1
            info["comps"] = [dict(i=int(i), xywh=[int(v) for v in st[i, :4]], area=int(area[i]), root=bool(root[i]),
                                  dmin=round(float(dmin[i]), 1), smf=round(float(smf[i]), 2), keep=bool(keep[i]))
                             for i in order]
    rem = keep[lab] & candc
    info.update(cand_comp=int(n - 1), removed_comp=int(keep.sum()))

    # --- raices: salientes estrechos de T (mas finos que taper*2*ro) de donde nacen mechones retirados -> se van
    #     con ellos (sin munones); la silueta final es T sin esos salientes
    tp = prm.get("taper", 0.0)
    if tp > 1.0 and rem.any():
        T2 = mopen(T, tp * ro) | deep
        T2 = connected_to(T2, deep)
        prot = T & ~T2 & ~block & dil(hairT, 2)
        n2, lab2 = cv2.connectedComponents(prot.astype(np.uint8), connectivity=8)
        if n2 > 1:
            d2T = cv2.distanceTransform((~T2).astype(np.uint8), cv2.DIST_L2, 5)
            mx = np.zeros(n2, F32)
            np.maximum.at(mx, lab2.ravel(), d2T.ravel())
            tch = np.zeros(n2, bool)
            tch[np.unique(lab2[dil(rem, 3) & prot])] = True
            kp = tch & (mx >= prm.get("taper_len", 0.8) * ro)
            kp[0] = False
            info["roots_dbg"] = [int(n2 - 1), int(tch.sum()), round(float(np.sort(mx[1:])[-3:].mean()), 1), round(float(ro), 1)]
            protk = kp[lab2]
            if protk.any():
                T = T & ~protk
                rem = rem | protk
                dT = cv2.distanceTransform((~T).astype(np.uint8), cv2.DIST_L2, 5)
                info["roots"] = int(kp.sum())

    # --- peso de retirada: rampa desde T y residuo de contraste pegado al borde
    ramp = max(2.0, prm["ramp"] * u)
    t = np.clip(dT / ramp, 0, 1)
    a = t * t * (3 - 2 * t)
    a *= 1 - prm["resid"] * np.exp(-dT / (2.0 * ramp))
    hole = dil(rem, 1) & ~T
    # relleno = mediana grande suavizada (conserva la estructura del fondo desenfocado: nubes, montes) + correccion de
    # baja frecuencia medida SOLO en pixeles limpios (sin evidencia, lejos del hueco y de su halo, fuera de T)
    Mb = cv2.GaussianBlur(Mm2, (0, 0), max(3.0, R2 / 5))
    d2 = cv2.GaussianBlur(np.abs(Pd - Mb).max(-1), (0, 0), 1.0)
    t_cl = max(0.5 * floor, 1.3 * step)
    away = ((Pd - Mb) * (Hc - Mb)).sum(-1) < 0                    # mas 'fondo' que el fondo estimado (huecos de cielo)
    src = ~dil(T, 2) & ~dil(thin, 2) & ~dil(hole, max(3, 0.007 * u)) & ((d2 < np.maximum(t_cl, 0.7 * sn)) | away)
    res = pushpull(Pd - Mb, src.astype(F32))
    fill = Mb + cv2.GaussianBlur(res, (0, 0), max(2.0, R / 4))
    del res
    if rough.any() and (hole & rough).any():
        srr = ~dil(thin, 2) & ~dil(T, 2)
        fr = Mm + cv2.GaussianBlur(pushpull(Pd - Mm, srr.astype(F32)), (0, 0), 2.0)
        fill = np.where(rough[..., None], fr, fill)
        del fr
    # vecindad N de lo retirado: alli se va tambien lo que se desvia del fondo hacia el pelo (halos, mechones
    # desenfocados anchos) con peso suave -> sin parches ni bordes alrededor del hueco
    rN = max(3, prm.get("halo", 0.03) * u)
    N = dil(hole, rN) & ~T & ~block & band & ~(dil(nonhair, max(4, 0.012 * u)) & ~force)
    N &= smooth
    df3 = Ls - fill
    dev = cv2.GaussianBlur(np.abs(df3).max(-1), (0, 0), 1.2)
    toward = cv2.GaussianBlur(((df3 * (Hc - fill)).sum(-1) > 0).astype(F32), (0, 0), 1.2)
    t0 = np.maximum(prm.get("dev_lo", 0.3) * floor, 0.3 * sn)
    a2 = np.clip((dev - t0) / np.maximum(0.02, 0.3 * sn), 0, 1)
    gate = np.clip(toward * 2 - 0.4, 0, 1)                        # desviaciones grandes: solo si van hacia el pelo
    # pequenas (huecos de cielo, halos): todo, queda uniforme -pero SOLO pegado a lo retirado (el relleno lejos del
    # hueco es menos fiable y un error suyo se veria como una mancha)
    dhole = cv2.distanceTransform((~hole).astype(np.uint8), cv2.DIST_L2, 3)
    nearh = np.clip(1.5 - dhole / max(3.0, 0.010 * u), 0, 1)
    gate = gate + (1 - gate) * np.clip((0.12 - dev) / 0.06, 0, 1) * nearh
    if rough.any():                                               # desvanecer hacia el fondo con estructura
        drough = cv2.distanceTransform((~rough).astype(np.uint8), cv2.DIST_L2, 3)
        gate = gate * np.clip(drough / max(4.0, 0.02 * u), 0, 1)
        del drough
    del dhole, nearh
    a2 = a2 * a2 * (3 - 2 * a2) * gate
    a2 *= np.clip(cv2.GaussianBlur(ero(N, 1).astype(F32), (0, 0), 2.0) * 1.3, 0, 1)
    wrem = np.maximum(np.where(hole, 1.0, 0.0).astype(F32), np.where(N, a2, 0).astype(F32))
    # --- islas (v4.2, QA): pixeles sin tocar ENCERRADOS por relleno (entre mechones cruzados). El relleno nunca iguala
    #     del todo al fondo original (velo de los mechones desenfocados), asi que una isla se ve como una manchita
    #     oscura/clara con canto. Se rellenan las islas pequenas (< (island*u)^2) y las bahias de boca estrecha, salvo
    #     las que tocan piel/protegidos/exclusiones/masa que no es pelo o salen del alcance. Pegadas a T valen: la
    #     rampa 'a' ya las desvanece hacia la silueta.  island=0 lo desactiva.
    isl = prm.get("island", 0.12)
    if isl > 0 and hole.any():
        sup = wrem > 0.5
        supc = mclose(sup, max(2, prm.get("island_close", 0.012) * u))
        bad = (block | ~band) & ~T
        n3, lab3, st3, _ = cv2.connectedComponentsWithStats((~supc & ~T).astype(np.uint8), connectivity=4)
        add = supc & ~sup
        if n3 > 1:
            okc = st3[:, 4] < (isl * u) ** 2
            okc &= np.bincount(lab3.ravel(), weights=bad.ravel().astype(np.float64), minlength=n3) == 0
            brd = np.zeros(n3, bool)
            brd[np.unique(np.concatenate([lab3[0], lab3[-1], lab3[:, 0], lab3[:, -1]]))] = True
            okc &= ~brd
            okc[0] = False
            add = add | okc[lab3]
        add &= ~bad & ~T & ~sup
        info["islands_px"] = int(add.sum())
        wrem = np.maximum(wrem, add.astype(F32))
        del sup, supc, bad, lab3, add
    alpha = wrem * a
    del wrem
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.0)
    alpha[protect | excl | T] = 0
    del d2, df3, dev, toward, a2, gate, Mb, Mm2, proj, dv3

    out = dict(T=T, alpha=alpha, src=src, K=K, Hm=Hm, smooth=smooth, protect=protect, info=info, fill=fill,
               thin=thin)

    # --- hebras sobre la piel
    a_skin = np.zeros((H, W), F32)
    nskin = 0
    if want_skin and K.any():
        Ke = ero(K, max(2, 0.008 * u)) & facez                   # solo cara: ni cuello, ni manos, ni ropa color piel
        k_edge = dil(~K, max(3, 0.02 * u))                       # borde de la piel (mandibula, perfil): no es hebra
        Rs = max(3.0, prm.get("skin_med", 0.012) * u)
        Ms, _ = median_bg(Pd, Rs)
        ds3 = Ls - Ms
        Ds = np.abs(ds3).max(-1)
        zs = Ds / np.maximum(sn, 0.6 * step)
        fl_s = max(prm.get("skin_floor", 0.045), 1.2 * step)
        rs = hysteresis(np.where((Ds > fl_s) & Ke, zs, 0), prm["skin_z"], prm["skin_z"] + 2.0)
        rs &= Ke & ~excl
        if prm.get("debug_comps"):
            out["dbg_skin"] = dict(Ds=Ds, zs=zs, Ke=Ke, rs=rs, sn=sn, fl=fl_s, step=step)
        n, lab, st, _ = cv2.connectedComponentsWithStats(rs.astype(np.uint8), connectivity=8)
        hb = cv2.morphologyEx(hairT.astype(np.uint8), cv2.MORPH_OPEN, disk(max(2, 0.03 * u))) > 0   # pelo de verdad
        hair_near = dil(hb | ~core, max(3, 0.03 * u))            # borde de la piel con pelo o con el fondo
        prot_l = dil(protect, max(2, 0.012 * u))                 # holgada: si la linea vive dentro, es un rasgo
        prot_d = dil(protect_t, max(1, 0.004 * u)) if protect_t is not None else prot_l
        Lmin = prm["skin_len"] * u
        wmax = prm.get("skin_w", 0.007) * u + 2.5
        hole_skin = np.zeros((H, W), bool)
        for i in range(1, n):
            x, y, w, h, area = st[i]
            if area < 12 or max(w, h) < 0.7 * Lmin:
                continue
            comp = lab[y:y + h, x:x + w] == i
            pts = cv2.findNonZero(comp.astype(np.uint8))
            (_, _), (rw, rh), _ = cv2.minAreaRect(pts)
            length = max(rw, rh)
            width = area / max(length, 1.0)
            if length < Lmin or width > wmax or length / max(min(rw, rh), 1.0) < 2.2:
                continue
            if (comp & prot_l[y:y + h, x:x + w]).sum() > prm.get("skin_prot", 0.5) * area:
                continue
            if (comp & k_edge[y:y + h, x:x + w]).sum() > 0.5 * area:
                continue                                         # corre a lo largo del borde de la piel
            if (comp & lowface[y:y + h, x:x + w]).sum() > 0.4 * area and length < prm.get("skin_low", 4.5) * Lmin:
                continue                                         # surco de la sonrisa / barba: solo si es muy larga
            touches = (comp & hair_near[y:y + h, x:x + w]).any()
            if not touches and length < prm["skin_free"] * Lmin:
                continue
            hole_skin[y:y + h, x:x + w] |= comp
            nskin += 1
        if nskin:
            hole_skin = dil(hole_skin, 1) & K & ~prot_d
            a_skin = np.clip(cv2.GaussianBlur(hole_skin.astype(F32), (0, 0), 0.8) * 1.7, 0, 1)
            a_skin *= 1 - 0.5 * prm["resid"]
            Ksrc = K & ~dil(hole_skin, 2) & ~protect & ~dil(rs, 1)
            fk = cv2.GaussianBlur(pushpull(Pd, Ksrc.astype(F32)), (0, 0), 1.0)
            m3 = (a_skin > alpha)[..., None]
            out["fill"] = np.where(m3, fk, fill)
    out.update(a_skin=a_skin)
    info.update(skin_strands=nskin)
    return out


# ----------------------------------------------------------------------------------------------
# limpieza
# ----------------------------------------------------------------------------------------------
def clean(base_lin, strength="fuerte", debug_dir=None, ev=None, stem="img", overrides=None, crops=None, seed=7):
    """Limpia cabellos sueltos de una base lineal Rec.2020 (float32 HxWx3). Devuelve (base_out, report).
    overrides: dict de esta foto (ver cabecera). crops: [(nombre, x0, y0, x1, y1)] recortes extra de depuracion."""
    t0 = time.time()
    ov = dict(overrides or {})
    strength = ov.get("strength", strength)
    if strength not in STRENGTH:
        raise ValueError(f"strength debe ser {list(STRENGTH)}")
    prm = dict(STRENGTH[strength])
    prm.update(ov.get("params", {}))
    img = np.asarray(base_lin, F32)
    H, W = img.shape[:2]
    if ev is None:
        ev = auto_ev(img)
    enc = Enc(ev)
    rep = dict(stem=stem, strength=strength, version=VERSION, ev=round(float(ev), 3), heads=[], zones=[],
               overrides=ov)
    if ov.get("skip"):
        rep.update(status="skipped", reason=ov.get("reason", "override skip"), px_bg=0, px_skin=0,
                   seconds=round(time.time() - t0, 1))
        return img, rep

    # --- caras + mate a baja resolucion
    fs = min(1.0, 2400.0 / max(H, W))
    small = cv2.resize(img, (round(W * fs), round(H * fs)), interpolation=cv2.INTER_AREA)
    small8 = enc.disp(small)
    del small
    try:
        faces = run_facelm(small8)
        for fc in faces:
            for k in list(fc):
                if isinstance(fc[k], np.ndarray):
                    fc[k] = fc[k] / fs
    except Exception as e:  # noqa
        faces = []
        rep["facelm_error"] = repr(e)
    matte_s = person_matte(small8)
    del small8
    if matte_s is None:
        rep.update(status="skipped", reason="sin mate de personas (requiere el ayudante Vision de macOS)", px_bg=0,
                   px_skin=0,
                   seconds=round(time.time() - t0, 1))
        return img, rep
    geo = [face_geom(f) + (f,) for f in faces]
    geo = [g for g in geo if matte_s[int(np.clip(g[0][1] * fs, 0, matte_s.shape[0] - 1)),
                                     int(np.clip(g[0][0] * fs, 0, matte_s.shape[1] - 1))] > 0.3]
    if ov.get("fallback_heads", True):
        geo += fallback_heads(matte_s, geo, fs)
    if not geo:
        rep.update(status="skipped", reason="sin personas/caras", px_bg=0, px_skin=0,
                   seconds=round(time.time() - t0, 1))
        return img, rep
    rep["t_vision"] = round(time.time() - t0, 2)

    # --- agrupar cabezas en zonas por cercania y tamano parecido
    heads = [dict(c=c, u=u, up=up, face=f) for (c, u, up, f) in geo]
    for h in heads:
        rep["heads"].append(dict(c=[int(h["c"][0]), int(h["c"][1])], u=int(h["u"]), face=h["face"] is not None))
    groups = []
    for h in sorted(heads, key=lambda q: -q["u"]):
        for g in groups:
            if any(np.hypot(*(h["c"] - o["c"])) < 4.5 * max(h["u"], o["u"]) and 0.5 < h["u"] / o["u"] < 2.0 for o in g):
                g.append(h)
                break
        else:
            groups.append([h])

    out = img.copy()
    rng = np.random.default_rng(seed)
    dbg_maps = []
    for g in groups:
        tz = time.time()
        u = float(np.median([h["u"] for h in g]))
        x0 = int(max(0, min(h["c"][0] - 2.6 * h["u"] for h in g)))
        x1 = int(min(W, max(h["c"][0] + 2.6 * h["u"] for h in g)))
        y0 = int(max(0, min(h["c"][1] - 2.4 * h["u"] for h in g)))
        y1 = int(min(H, max(h["c"][1] + 3.8 * h["u"] for h in g)))
        sd = min(1.0, U_DET / u, float(np.sqrt(MAX_DET_PX / max(1, (y1 - y0) * (x1 - x0)))))
        X = out[y0:y1, x0:x1]                                    # vista de out
        hd, wd = max(8, round((y1 - y0) * sd)), max(8, round((x1 - x0) * sd))
        Xd = cv2.resize(X, (wd, hd), interpolation=cv2.INTER_AREA) if sd < 1 else X
        Pd = enc.enc(Xd)
        m = matte_s[int(y0 * fs):max(int(y0 * fs) + 2, int(np.ceil(y1 * fs))),
                    int(x0 * fs):max(int(x0 * fs) + 2, int(np.ceil(x1 * fs)))]
        matte_d = np.clip(cv2.resize(m, (wd, hd), interpolation=cv2.INTER_CUBIC), 0, 1)
        hs = []
        for h in g:
            fd = None
            if h["face"] is not None:
                fd = {k: (v - [x0, y0]) * sd for k, v in h["face"].items() if isinstance(v, np.ndarray) and v.ndim == 2}
            hs.append(dict(c_d=(h["c"] - [x0, y0]) * sd, u_d=h["u"] * sd, up=h["up"], face_d=fd))
        allf = [{k: (v - [x0, y0]) * sd for k, v in f.items() if isinstance(v, np.ndarray) and v.ndim == 2} for f in faces]
        protect = _protect_from(allf, (hd, wd)) > 0
        roi = (x0, y0, x1, y1)
        excl = boxes_mask(ov.get("exclude"), roi, sd, (hd, wd))
        force = boxes_mask(ov.get("force"), roi, sd, (hd, wd))
        A = analyze(Pd, matte_d, hs, prm, protect, excl, force, want_skin=ov.get("skin", True),
                    protect_t=protect_tight(allf, (hd, wd)))
        del Pd
        info = A["info"]
        atot = np.maximum(A["alpha"], A["a_skin"])
        npx = int((A["alpha"] > 0.5).sum() / (sd * sd))
        npx_skin = int((A["a_skin"] > 0.5).sum() / (sd * sd))

        # --- aplicar a resolucion completa, solo en la caja de lo que cambia
        ys, xs = np.nonzero(atot > 0.003)
        if len(xs):
            pad = 6
            dx0, dx1 = max(0, xs.min() - pad), min(wd, xs.max() + pad + 1)
            dy0, dy1 = max(0, ys.min() - pad), min(hd, ys.max() + pad + 1)
            fx0, fx1 = int(round(dx0 / sd)), min(x1 - x0, int(round(dx1 / sd)))
            fy0, fy1 = int(round(dy0 / sd)), min(y1 - y0, int(round(dy1 / sd)))
            # por franjas horizontales para acotar la memoria
            step = 1024
            for sy in range(fy0, fy1, step):
                ey = min(fy1, sy + step)
                tw, th = fx1 - fx0, ey - sy
                # mapa afin deteccion -> completo para esta franja
                M = np.array([[1 / sd, 0, fx0 + 0.5 - 0.5 / sd], [0, 1 / sd, sy + 0.5 - 0.5 / sd]], np.float64)
                # warpAffine con WARP_INVERSE_MAP: dst(x,y) = src(M*[x,y,1]) con M = completo->deteccion
                Mi = np.array([[sd, 0, (fx0 + 0.5) * sd - 0.5], [0, sd, (sy + 0.5) * sd - 0.5]], np.float64)
                del M

                def up_(mm):
                    return cv2.warpAffine(mm.astype(F32), Mi, (tw, th), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                          borderMode=cv2.BORDER_REPLICATE)
                a = up_(atot)
                if not (a > 0.003).any():
                    continue
                ask = up_(A["a_skin"])
                Fu = up_(A["fill"])
                Xt = X[sy:ey, fx0:fx1]
                P = enc.enc(Xt)
                L = luma(P)
                hp = L - cv2.GaussianBlur(L, (0, 0), 1.0)
                srcu = up_(A["src"]) > 0.99
                Ku = (up_(A["K"]) > 0.99) & (a < 0.01)
                s_bg = robust_sigma(hp[srcu][::7]) if srcu.sum() > 500 else 0.0
                s_k = robust_sigma(hp[Ku][::7]) if Ku.sum() > 500 else s_bg
                nz = cv2.GaussianBlur(rng.standard_normal((th, tw)).astype(F32), (0, 0), 0.5)
                nz /= max(float(nz.std()), 1e-6)
                sig = np.where(ask > 0.5 * a, F32(s_k), F32(s_bg)).astype(F32)
                R = P + a[..., None] * (Fu + (sig * nz)[..., None] - P)
                ch = a > 0.003
                Xo = enc.dec(R)
                Xt[ch] = Xo[ch]
                del a, ask, Fu, P, L, hp, srcu, Ku, nz, sig, R, Xo, ch
        z = dict(roi=[x0, y0, x1, y1], u=int(u), det_scale=round(sd, 3), px_bg=npx, px_skin=npx_skin,
                 seconds=round(time.time() - tz, 2), **info)
        rep["zones"].append(z)
        if debug_dir is not None:
            dbg_maps.append(dict(roi=roi, sd=sd, A={k: A[k] for k in ("T", "Hm", "K", "protect", "alpha", "a_skin",
                                                                      "smooth")}))
        del A

    rep["px_bg"] = int(sum(z["px_bg"] for z in rep["zones"]))
    rep["px_skin"] = int(sum(z["px_skin"] for z in rep["zones"]))
    rep["status"] = "applied" if rep["px_bg"] + rep["px_skin"] > 0 else "nothing"
    rep["seconds"] = round(time.time() - t0, 1)
    if debug_dir is not None:
        try:
            save_debug(Path(debug_dir), stem, strength, img, out, enc, dbg_maps, heads, crops)
        except Exception as e:  # noqa
            rep["debug_error"] = repr(e)
    return out, rep


# ----------------------------------------------------------------------------------------------
# depuracion: tripticos antes | despues | mascara
# ----------------------------------------------------------------------------------------------
def mask_panel(before8, x0, y0, x1, y1, maps):
    o = (before8.astype(F32) * 0.55)
    for mp in maps:
        rx0, ry0, rx1, ry1 = mp["roi"]
        ix0, iy0, ix1, iy1 = max(x0, rx0), max(y0, ry0), min(x1, rx1), min(y1, ry1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        A, sd = mp["A"], mp["sd"]
        tw, th = ix1 - ix0, iy1 - iy0
        Mi = np.array([[sd, 0, (ix0 - rx0 + 0.5) * sd - 0.5], [0, sd, (iy0 - ry0 + 0.5) * sd - 0.5]], np.float64)

        def g(mm):
            return cv2.warpAffine(mm.astype(F32), Mi, (tw, th), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                  borderMode=cv2.BORDER_REPLICATE)
        sub = o[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]
        rg = (1 - g(A["smooth"]))[..., None] * 0.18
        sub = sub * (1 - rg) + np.array([255, 120, 0], F32) * rg                # fondo rugoso: tinte naranja
        a = g(A["alpha"])[..., None]
        sub = sub * (1 - a) + np.array([255, 40, 200], F32) * a                 # retirada sobre fondo: magenta
        ak = g(A["a_skin"])[..., None]
        sub = sub * (1 - ak) + np.array([40, 255, 80], F32) * ak                # hebras sobre piel: verde
        pr = g(A["protect"])[..., None] * 0.35
        sub = sub * (1 - pr) + np.array([0, 220, 255], F32) * pr                # protegido: cian
        sub = np.clip(sub, 0, 255).astype(np.uint8)
        for mm, col in ((A["T"], (255, 255, 0)), (A["Hm"], (255, 140, 0)), (A["K"], (0, 160, 255))):
            cn, _ = cv2.findContours((g(mm) > 0.5).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(sub, cn, -1, col, 1)
        o[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = sub
    return np.clip(o, 0, 255).astype(np.uint8)


def triptych(path, img, out, enc, maps, box, max_w=3000, label=""):
    x0, y0, x1, y1 = [int(v) for v in box]
    def d8(im):
        return np.vstack([enc.disp(im[y:min(y1, y + 512), x0:x1]) for y in range(y0, y1, 512)])
    b8 = d8(img)
    a8 = d8(out)
    m8 = mask_panel(b8, x0, y0, x1, y1, maps)
    k = min(1.0, max_w / (3.0 * (x1 - x0)))
    panels = []
    for p, lab in ((b8, "ANTES"), (a8, "DESPUES " + label),
                   (m8, "MASCARA magenta=quita verde=piel amarillo=silueta naranja=pelo azul=piel cian=protegido")):
        if k < 1:
            p = cv2.resize(p, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        p = cv2.copyMakeBorder(p, 30, 0, 0, 0, cv2.BORDER_CONSTANT, value=(24, 24, 24))
        cv2.putText(p, lab, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1, cv2.LINE_AA)
        panels.append(p)
    sep = np.full((panels[0].shape[0], 6, 3), 255, np.uint8)
    cv2.imwrite(str(path), cv2.cvtColor(np.hstack([panels[0], sep, panels[1], sep, panels[2]]), cv2.COLOR_RGB2BGR))


def save_debug(ddir, stem, strength, img, out, enc, maps, heads, crops=None):
    ddir.mkdir(parents=True, exist_ok=True)
    H, W = img.shape[:2]
    boxes = []
    for i, h in enumerate(heads):
        c, u = h["c"], h["u"]
        boxes.append((f"cabeza{i + 1}", max(0, c[0] - 1.5 * u), max(0, c[1] - 1.7 * u), min(W, c[0] + 1.5 * u),
                      min(H, c[1] + 1.3 * u)))
    for c in (crops or []):
        boxes.append(tuple(c))
    for name, x0, y0, x1, y1 in boxes:
        triptych(ddir / f"{stem}_{strength}_{name}.png", img, out, enc, maps, (x0, y0, x1, y1), label=f"[{strength}]")


# ----------------------------------------------------------------------------------------------
# puerta de seguridad (del lote de referencia): aplicar SOLO sobre fondo liso
# ----------------------------------------------------------------------------------------------
# Medida en la base ORIGINAL a 1/4 de resolucion, en log2 de luminancia expuesta, EXCLUYENDO los propios pixeles
# retocados (convolucion normalizada): textura fina y bordes de estructura del fondo donde se ha retocado.
GATE = dict(win=8.0,            # px a 1/4 res: ventana de medida del fondo alrededor del retoque
            tex_t=0.060,        # stops: desviacion local del paso alto > esto = fondo con textura
            edge_t=0.050,       # stops/px (1/4 res): gradiente suavizado > esto = bordes/estructura
            max_tex=0.12,       # fraccion maxima del retoque sobre fondo con textura
            max_edge=0.03,      # fraccion maxima del retoque junto a bordes de estructura (lo mas peligroso)
            max_unk=0.06,       # fraccion maxima del retoque sin fondo medible alrededor (pegado a la persona)
            min_focus=0.12,     # enfoque minimo de las cabezas
            min_px=2000)        # menos que esto: cambio irrelevante -> no se aplica


def hair_metrics(img, out, ev):
    """Donde ha cambiado la base (out != img) y que textura/estructura tiene el FONDO ORIGINAL alli."""
    H, W = img.shape[:2]
    ch = np.zeros((H, W), bool)
    for y in range(0, H, 1024):
        ch[y:y + 1024] = np.any(out[y:y + 1024] != img[y:y + 1024], axis=2)
    n = int(ch.sum())
    m = dict(changed_px=n)
    if n == 0:
        return m
    f = 4
    sw, sh = W // f, H // f
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    Y = small @ LUMA
    Lg = np.log2(np.maximum(Y, 0) * F32(2.0 ** ev) + F32(1 / 64)).astype(F32)
    chs = cv2.resize(ch.astype(np.uint8) * 255, (sw, sh), interpolation=cv2.INTER_AREA) > 40
    del ch
    matte = person_matte(Enc(ev).disp(small))
    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    pers = (matte > 0.15) if matte is not None else np.zeros((sh, sw), bool)
    pers = cv2.dilate(pers.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0
    w = ((cv2.dilate(chs.astype(np.uint8), k5) == 0) & ~pers).astype(F32)
    hp = Lg - cv2.GaussianBlur(Lg, (0, 0), 1.5)
    S = GATE["win"]
    den = cv2.GaussianBlur(w, (0, 0), S)
    dd = np.maximum(den, 1e-4)
    tex = np.sqrt(cv2.GaussianBlur(hp * hp * w, (0, 0), S) / dd)
    Ls = cv2.GaussianBlur(Lg, (0, 0), 2.0)
    G = np.hypot(cv2.Sobel(Ls, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(Ls, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
    edge = cv2.GaussianBlur(G * w, (0, 0), S) / dd
    vt, ve, vd = tex[chs], edge[chs], den[chs]
    if vt.size == 0:
        return m
    unk = vd < 0.04
    rough = (vt > GATE["tex_t"]) | (ve > GATE["edge_t"]) | unk
    ys, xs = np.nonzero(chs)
    m.update(tex_p50=round(float(np.median(vt)), 4), tex_p90=round(float(np.percentile(vt, 90)), 4),
             edge_p50=round(float(np.median(ve)), 4), edge_p90=round(float(np.percentile(ve, 90)), 4),
             frac_tex=round(float((vt > GATE["tex_t"]).mean()), 3), frac_edge=round(float((ve > GATE["edge_t"]).mean()), 3),
             frac_rough=round(float(rough.mean()), 3), frac_sin_fondo=round(float(unk.mean()), 3),
             mate=matte is not None,
             bbox=[int(xs.min() * f), int(ys.min() * f), int((xs.max() + 1) * f), int((ys.max() + 1) * f)],
             tex_photo_p10=round(float(np.percentile(tex, 10)), 4))
    return m


def hair_gate(rep, met):
    """(aplicar?, motivo). Conservador: calibrado para que solo pase el retoque sobre fondo liso y nitido."""
    if rep.get("status") != "applied":
        return False, f"modulo: {rep.get('status')} ({rep.get('reason', '')})".strip()
    if met.get("changed_px", 0) < GATE["min_px"]:
        return False, f"cambio minimo ({met.get('changed_px', 0)} px)"
    foc = max([z.get("focus") or 0 for z in rep.get("zones", [])] or [0])
    why = []
    if foc < GATE["min_focus"]:
        why.append(f"cabezas poco nitidas (focus {foc:.2f} < {GATE['min_focus']})")
    if met.get("frac_tex", 1) > GATE["max_tex"]:
        why.append(f"{met['frac_tex'] * 100:.0f}% sobre fondo con textura (max {GATE['max_tex'] * 100:.0f}%)")
    if met.get("frac_edge", 1) > GATE["max_edge"]:
        why.append(f"{met['frac_edge'] * 100:.0f}% junto a bordes del fondo (max {GATE['max_edge'] * 100:.0f}%)")
    if met.get("frac_sin_fondo", 1) > GATE["max_unk"]:
        why.append(f"{met['frac_sin_fondo'] * 100:.0f}% pegado a la persona sin fondo medible "
                   f"(max {GATE['max_unk'] * 100:.0f}%)")
    if why:
        return False, "; ".join(why)
    return True, (f"retoque sobre fondo liso (textura {met['frac_tex'] * 100:.0f}%, bordes {met['frac_edge'] * 100:.0f}%, "
                  f"focus {foc:.2f})")


def clean_gated(img, strength="fuerte", ev=None, overrides=None):
    """clean() + puerta de seguridad. Devuelve (img_out | None si no se aplica, info)."""
    out, rep = clean(img, strength=strength, ev=ev, overrides=dict(overrides or {"skin": False}))
    met = hair_metrics(img, out, ev if ev is not None else auto_ev(img)) if rep.get("status") == "applied" else {}
    ok, why = hair_gate(rep, met)
    info = {"version": VERSION, "strength": strength, "status": rep.get("status"), "applied": ok, "reason": why,
            "metrics": met, "module_reason": rep.get("reason"), "experimental": True,
            "zones": [{k: z.get(k) for k in ("u", "focus", "rough_pct", "removed_comp", "islands_px", "skipped")}
                      for z in rep.get("zones", [])]}
    pending = (not ok) and (rep.get("status") == "applied" or (rep.get("status") == "skipped"
                                                                and "sin personas" not in str(rep.get("reason", ""))
                                                                and "Vision" not in str(rep.get("reason", ""))))
    info["pending_manual"] = bool(pending)
    return (out if ok else None), info
