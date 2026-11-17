# SPDX-License-Identifier: GPL-3.0-or-later
"""
ektar.py (antes engine_A-fisico.py) - Emulacion FISICA de Kodak Ektar 100 (negativo + escaneo) sobre las bases neutras.

Portado del motor de referencia. Cambios: especificacion como datos del paquete (importlib.resources), deteccion de
caras via revelado.pipeline.faces (Vision opcional / YuNet / Haar), semilla del grano por contenido (no por nombre),
escala fisica explicita (REF_LONG_SIDE_PX), sin rutas fijas ni CLI en paralelo.

Modelo (todo en float32, salida 8-bit solo al final con dither):
  1. Exposicion de escena: base Rec.2020 lineal * 2**ev (analisis de exposicion) + guardia de altas (p99.8 <= +4.5 EV).
  2. Optica / emulsion: MTF de la pelicula (gauss 0.55 px) y halacion (luz que rebota en la base y vuelve a
     exponer sobre todo la capa roja; Ektar tiene antihalo => casi invisible, solo en fuentes > +3.5 EV).
  3. Sensibilidad espectral de las capas: matriz 3x3 (filas suman 1) Rec.2020 -> exposicion de capa R/G/B.
  4. Curvas H&D por capa: log10(E) -> densidad. gamma 0.65, pie (toe) suave, hombro muy lejano. La capa
     sensible al rojo tiene el pie un poco mas alto => cruce de color: sombras levemente teal (rasgo Ektar).
  5. Acopladores DIR (efecto interimagen): matriz en DENSIDAD con filas que suman 1 (no mueve neutros) =>
     separacion/saturacion tipo Ektar sin tocar los grises.
  6. Efecto de adyacencia del revelado (bordes de Eberhard/Mackie): mascara de enfoque en DENSIDAD
     (radio 1.4 px) = acutancia de negativo + escaner, sin halos de nitidez digital.
  7. GRANO fisico por capa: cada capa tiene sub-capa rapida (nubes de tinte grandes, sigma 1.5 px) y lenta
     (nubes finas, sigma 0.65 px). El numero de nubes es proporcional a la densidad (ruido de disparo de
     Poisson filtrado por la forma de la nube => varianza ~ densidad). Agrupamiento (clumping) con campo de
     baja frecuencia. El escaner (Frontier) fusiona casi todo el grano de color en luminancia.
     Como el grano esta en la densidad del negativo, el escaneo lo comprime solo en altas (hombro) y en el
     negro profundo (pie): no hace falta "proteger" a mano, es la fisica.
  8. Escaneo/inversion: densidad -> codigo de salida por canal (curva de papel/escaner) calibrada para que el
     neutro siga exactamente la curva tonal de ektar_spec.json (negro 10, gris 120, asintota 253: nunca quema).
     Mezcla canal-a-canal (fisico) con preservacion de razon de color para controlar el giro de matiz.
  9. Tintes del escaneo en OkLCh (bandas de la espec, escaladas porque el modelo fisico ya satura),
     tratamiento de piel, split toning leve, desaturacion de altas, compresion de croma y mapeo a gamut sRGB.
 10. sRGB 8-bit con dither TPDF, JPEG q97 4:4:4 con perfil sRGB.

API:  render(base_tiff_path, out_path, ev=None, strength=1.0, grain=1.0, ...) -> dict de metricas
CLI:  engine_A-fisico.py BASE.tif [...] --out DIR [--ev X] [--strength 1] [--grain 1] [--workers 2] [--crops]
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from importlib import resources
from pathlib import Path

import numpy as np
import cv2

from ..colorspaces import REC2020_LUMA, REC2020_TO_SRGB, srgb_encode, srgb_decode
from .. import blemish
from .. import faces as faces_mod

SPEC = json.loads(resources.files("revelado.pipeline.data").joinpath("kodak_ektar_100.json").read_text("utf-8"))
# Physical scale: every spatial parameter (grain clouds, MTF, halation, adjacency) is expressed in pixels of a 36 mm
# film frame sampled at 5.14 um/px, i.e. a 7008 px long side. A render at N px uses scale = N / REF_LONG_SIDE_PX,
# so grain size stays constant relative to the FRAME (as on film), whatever the camera's megapixels.
REF_LONG_SIDE_PX = 7008.0
F32 = np.float32
_LOG = logging.getLogger("revelado.film.ektar")
LUMA = np.asarray(REC2020_LUMA, F32)

# ------------------------------------------------------------------------------------------------
# Parametros fisicos
# ------------------------------------------------------------------------------------------------
P = dict(
    grey=0.18,
    # curva neutra negativo+escaner (stops vs codigo). Version "vibrante": negros profundos y limpios
    # (no lechosos), contraste solido en medios, mismo hombro largo de la espec (+4 EV -> 240, nunca 255).
    tone_points=[[-9, 2.5], [-8, 3.2], [-7, 4.3], [-6, 6.3], [-5, 10.0], [-4, 17.5], [-3, 31.0],
                 [-2, 53.0], [-1, 83.0], [0, 118.0], [0.5, 137.0], [1, 156.0], [1.5, 175.0], [2, 193.0],
                 [2.5, 209.0], [3, 222.0], [3.5, 232.5], [4, 240.0], [4.5, 245.0], [5, 248.5],
                 [6, 251.5], [7, 253.0]],
    # H&D por capa (R, G, B): gamma, posicion del pie (log10 relativo a gris), suavidad del pie,
    # hombro (log10) y suavidad. Ektar: curvas casi paralelas, latitud enorme en sobreexposicion.
    neg_gamma=(0.655, 0.65, 0.645),
    neg_toe=(-1.67, -1.62, -1.635),
    neg_toe_soft=(0.34, 0.34, 0.34),
    neg_shoulder=(2.4, 2.4, 2.4),
    neg_shoulder_soft=0.5,
    # acopladores DIR en densidad: d' = (1+2k) d - k (otras dos)
    dir_k=0.08,
    # mezcla del escaneo: 1 = canal a canal puro (fisico), 0 = preserva razones de color
    chan_mix=0.55,
    # MTF de la pelicula y adyacencia
    film_blur=0.55, adj_radius=1.4, adj_amount=0.30, adj_threshold_d=0.006,
    # halacion
    hal_threshold=2.0, hal_r1=22.0, hal_r2=60.0, hal_strength=0.018, hal_color=(1.0, 0.35, 0.12),
    # grano (densidad). sigma de nube en px a REF_LONG_SIDE_PX de ancho (fotograma de 36 mm)
    g_fine_sigma=0.55, g_coarse_sigma=1.3,
    g_global=0.85,
    g_fog=0.12,                # densidad de velo quimico (grano presente aun sin exposicion)
    g_slow_dmax=2.4,           # densidad maxima de la sub-capa lenta (fina)
    g_fast_dmax=0.35,          # densidad que aporta la sub-capa rapida (grano grueso) antes de saturar
    g_a_fine=0.0016,           # densidad optica por "nube" equivalente (fija la granularidad)
    g_a_coarse=0.0011,
    g_clump_sigma=7.0, g_clump_depth=0.22,
    g_chroma=0.05,          # grano casi monocromo: nunca reintroduce ruido de color
    g_w_coarse=0.6, g_w_fine=1.0,             # fraccion de grano independiente por capa que deja pasar el escaner
    g_layer_gain=(1.10, 0.90, 1.15),
    # bandas OkLCh de la espec escaladas (el modelo fisico ya aporta parte de la saturacion)
    band_strength=0.8, hue_strength=0.8,
    split_strength=0.6,
    # vibrancia de pelicula: colores apagados (dia nublado) ganan croma, los ya saturados casi no; piel excluida
    vib_amt=0.14,
    # niveles automaticos del escaner (por fotograma)
    auto_levels=True, lv_black_target=9.0, lv_black_keep=0.2, lv_black_max=0.06,
    lv_white_target=236.0, lv_gain_max=1.28,
)


# ------------------------------------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------------------------------------
def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def softplus(z):
    return np.logaddexp(F32(0), z).astype(F32)


def _neg_raw(u, g, ut, st, us, ss):
    return (g * (st * softplus((u - ut) / st) - ss * softplus((u - us) / ss))).astype(F32)


def neg_curve(u, c):
    """densidad sobre base (D - Dmin) para log10(E/gris)=u, capa c. El escaner equilibra las tres capas
    en el gris medio (u=0); las diferencias de pie y gamma entre capas producen el cruce de color fisico:
    sombras levemente teal (capa roja con pie mas alto / gamma mayor) y altas levemente calidas."""
    g = P["neg_gamma"][c]; ut = P["neg_toe"][c]; st = P["neg_toe_soft"][c]
    us = P["neg_shoulder"][c]; ss = P["neg_shoulder_soft"]
    raw = _neg_raw(u, g, ut, st, us, ss)
    zero = _neg_raw(F32(0.0), g, ut, st, us, ss)
    return (raw - zero + neutral_neg_curve(F32(0.0))).astype(F32)


def neutral_neg_curve(u):
    return _neg_raw(u, P["neg_gamma"][1], P["neg_toe"][1], P["neg_toe_soft"][1], P["neg_shoulder"][1],
                    P["neg_shoulder_soft"])


def spec_tone(stops):
    from scipy.interpolate import PchipInterpolator
    pts = np.array(P["tone_points"], dtype=np.float64)
    f = PchipInterpolator(pts[:, 0], pts[:, 1], extrapolate=False)
    s = np.asarray(stops, np.float64)
    y = f(np.clip(s, pts[0, 0], pts[-1, 0]))
    # extension suave: por debajo de -9 stops hacia ~7 (negro de escaner), por encima de +7 hacia 253.5
    lo = s < pts[0, 0]
    y[lo] = pts[0, 1] * np.exp((s[lo] - pts[0, 0]) * 0.45)
    hi = s > pts[-1, 0]
    y[hi] = 253.5 - (253.5 - pts[-1, 1]) * np.exp(-(s[hi] - pts[-1, 0]) * 0.6)
    return y


def build_print_lut():
    """Curva de 'papel/escaner': densidad neutra sobre base -> codigo sRGB 0-255, tal que la cadena
    neutra negativo+escaner reproduce exactamente la curva tonal de la espec."""
    u = np.linspace(-5.0, 4.0, 6000)
    d = neutral_neg_curve(u.astype(F32)).astype(np.float64)
    code = spec_tone(u / np.log10(2.0))
    # garantizar monotonia estricta
    d = np.maximum.accumulate(d + np.arange(d.size) * 1e-9)
    return d.astype(F32), code.astype(F32)


PRINT_D, PRINT_CODE = build_print_lut()


def print_curve(d):
    return np.interp(d, PRINT_D, PRINT_CODE).astype(F32)


# ---- OkLab (Ottosson) desde sRGB lineal -------------------------------------------------------
M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
               [0.2119034982, 0.6806995451, 0.1073969566],
               [0.0883024619, 0.2817188376, 0.6299787005]], F32)
M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
               [1.9779984951, -2.4285922050, 0.4505937099],
               [0.0259040371, 0.7827717662, -0.8086757660]], F32)
M1i = np.linalg.inv(M1).astype(F32)
M2i = np.linalg.inv(M2).astype(F32)


def lin_to_oklab(rgb):
    lms = rgb @ M1.T
    return (np.cbrt(lms) @ M2.T).astype(F32)


def oklab_to_lin(lab):
    lms = lab @ M2i.T
    return ((lms * lms * lms) @ M1i.T).astype(F32)


def band_interp(h, values):
    v = np.asarray(values, F32)
    x = (h % 360.0) / 22.5
    i0 = np.floor(x).astype(np.int32) % 16
    i1 = (i0 + 1) % 16
    t = x - np.floor(x)
    t = (1 - np.cos(np.pi * t)) * 0.5
    return (v[i0] * (1 - t) + v[i1] * t).astype(F32)


def hue_dist(h, c):
    return (h - c + 180.0) % 360.0 - 180.0


# ------------------------------------------------------------------------------------------------
# Grano fisico
# ------------------------------------------------------------------------------------------------
def grain_fields(d, rng, scale):
    """Estructura fisica del grano por capa. d: densidad sobre base HxWx3.
    Cada capa = sub-capa rapida (nubes grandes, sigma 1.5 px) + lenta (nubes finas, 0.65 px). La rapida
    satura primero (pocas densidades), asi que en sombras del negativo (= altas de la escena NO, ojo:
    densidad baja = sombras de la escena) domina el grano grueso y en medios/altas el fino. Varianza de cada
    sub-capa ~ densidad que aporta (ruido de disparo de nubes de tinte), agrupamiento por campo de 7 px.
    Devuelve campo HxWx3 de desviacion ~1 (en luminancia), con el croma ya fusionado por el escaner;
    la amplitud final se calibra en codigos de salida en scan() con la tabla medida de la espec."""
    H, W, _ = d.shape
    sf = P["g_fine_sigma"] * scale
    sc = P["g_coarse_sigma"] * scale
    cl = rng.standard_normal((H, W), dtype=F32)
    cl = cv2.GaussianBlur(cl, (0, 0), P["g_clump_sigma"] * scale)
    cl *= F32(1.0 / (cl.std() + 1e-8))
    clump = np.clip(1.0 + P["g_clump_depth"] * cl, 0.6, 1.4).astype(F32)
    del cl
    # normalizacion de cada kernel para que una unidad de "densidad aportada" de varianza comparable
    nf = F32(np.sqrt(4 * np.pi) * sf); nc = F32(np.sqrt(4 * np.pi) * sc)
    out = np.empty_like(d)
    dfm = P["g_fast_dmax"]
    for c in range(3):
        dc = np.maximum(d[..., c], 0.0) + F32(P["g_fog"])
        d_fast = (dfm * (1.0 - np.exp(-dc / dfm))).astype(F32)
        d_slow = (dc - d_fast).astype(F32)
        tot = dc
        nfast = cv2.GaussianBlur(rng.standard_normal((H, W), dtype=F32), (0, 0), sc) * nc
        nslow = cv2.GaussianBlur(rng.standard_normal((H, W), dtype=F32), (0, 0), sf) * nf
        # pesos ~ sqrt(fraccion de densidad) (varianzas suman la densidad total, normalizada)
        wf = np.sqrt(d_fast / tot) * F32(P["g_w_coarse"]); ws = np.sqrt(d_slow / tot) * F32(P["g_w_fine"])
        g = (wf * nfast + ws * nslow) / np.sqrt(wf * wf + ws * ws + 1e-8)
        # no linealidad leve (nubes discretas, no gaussiano) y agrupamiento
        g = np.sign(g) * np.abs(g) ** F32(0.9)
        out[..., c] = g * clump * F32(P["g_layer_gain"][c])
        del nfast, nslow, wf, ws, g, d_fast, d_slow, dc
    gm = out @ LUMA
    out = gm[..., None] + F32(P["g_chroma"]) * (out - gm[..., None])
    out *= F32(1.0 / (float(gm[::7, ::7].std()) + 1e-8))
    return out.astype(F32)


# ------------------------------------------------------------------------------------------------
# Etapa OkLCh (tintes del escaneo, piel, split, altas, gamut)
# ------------------------------------------------------------------------------------------------
HB = SPEC["hue_bands"]
SAT_MULT = np.array(HB["sat_mult"], F32)
HUE_SHIFT = np.array(HB["hue_shift_deg"], F32)
WL_PTS = np.array(HB["lightness_dependence"]["w_L_points"], F32)


def k_subtractive(h):
    k = np.full(h.shape, 0.12, F32)
    k = np.where((h >= 215) & (h <= 275), 0.35, k)
    k = np.where((h < 35) | (h > 350), 0.25, k)
    k = np.where((h >= 120) & (h <= 180), 0.20, k)
    return k.astype(F32)


def skin_mask_raw(L, C, h):
    mh = np.exp(-0.5 * (hue_dist(h, 45.0) / 15.0) ** 2)
    mc = smoothstep(0.02, 0.04, C) * (1 - smoothstep(0.17, 0.22, C))
    ml = smoothstep(0.25, 0.35, L) * (1 - smoothstep(0.93, 0.97, L))
    return (mh * mc * ml).astype(F32)


def oklch_stage(lab, skin, strength):
    L = lab[:, 0]; a = lab[:, 1]; b = lab[:, 2]
    C = np.sqrt(a * a + b * b); h = np.degrees(np.arctan2(b, a)) % 360.0
    wL = np.interp(L, WL_PTS[:, 0], WL_PTS[:, 1]).astype(F32)
    wC = smoothstep(0.008, 0.04, C)
    bs = P["band_strength"] * strength
    sat = 1.0 + (band_interp(h, SAT_MULT) - 1.0) * wL * wC * bs
    sat = sat * (1.0 + P["vib_amt"] * strength * wC * wL * (1 - smoothstep(0.04, 0.12, C)) * (1 - skin))
    dh = band_interp(h, HUE_SHIFT) * wC * (0.4 + 0.6 * wL) * P["hue_strength"] * strength
    # piel: saturacion max x1.06, matiz hacia 46 (nunca < 40), en luz alta crema dorado (h~64, C<=0.065)
    sat = sat * (1 - skin) + np.minimum(sat, 1.0 + 0.06 * strength) * skin
    h_sk = h + np.clip(46.0 - h, -11.0, 8.0) * 0.75 * strength
    lit = smoothstep(0.78, 0.92, L)
    h_sk = h_sk * (1 - lit * 0.6) + 64.0 * lit * 0.6
    # suelo de matiz continuo (sin saltos: un np.where producia parches naranjas en piel con luz roja)
    h_sk = h_sk + smoothstep(12.0, 34.0, h) * np.maximum(41.0 - h_sk, 0.0) * min(strength, 1.0)
    h_new = (h + dh) * (1 - skin) + h_sk * skin
    C_new = C * sat
    # techo de croma de piel SUAVE (un minimo duro dejaba mesetas planas = parches naranjas/rojos)
    kn_s = 0.072 - 0.03 * lit; rg = 0.033 - 0.008 * lit
    C_sk = np.where(C_new > kn_s, kn_s + (C_new - kn_s) / (1 + (C_new - kn_s) / rg), C_new)
    C_new = C_new * (1 - skin) + C_sk * skin
    # densidad sustractiva (colores saturados se oscurecen como tintes), nunca en piel
    k = k_subtractive(h_new) * (1 - skin)
    L_new = L - k * np.maximum(C_new - C, 0) * strength - 0.04 * np.maximum(C_new - 0.06, 0) * (1 - skin) * strength
    # compresion de croma
    kn, mx = 0.16, 0.24
    over = C_new > kn
    C_new = np.where(over, kn + (C_new - kn) / (1 + (C_new - kn) / (mx - kn)), C_new)
    # desaturacion de altas (hombro)
    C_new = C_new * (1 - 0.6 * smoothstep(0.86, 0.99, L_new))
    hr = np.radians(h_new)
    a2 = C_new * np.cos(hr); b2 = C_new * np.sin(hr)
    # split toning en neutros
    ss = P["split_strength"] * strength
    wn = (1 - np.minimum(C_new / 0.06, 1.0) * 0.6) * ss
    st = SPEC["split_toning_oklab"]
    w_sh = 1 - smoothstep(0.26, 0.38, L_new)
    w_hi = smoothstep(0.62, 0.74, L_new) * (1 - smoothstep(0.84, 0.96, L_new))
    w_top = smoothstep(0.84, 0.96, L_new)
    w_mid = np.clip(1 - w_sh - w_hi - w_top, 0, 1)
    for w, key in ((w_sh, "shadows"), (w_mid, "mids"), (w_hi, "highlights"), (w_top, "top")):
        a2 = a2 + wn * w * st[key]["a"]
        b2 = b2 + wn * w * st[key]["b"]
    return np.stack([L_new, a2, b2], axis=1).astype(F32)


GAMUT_TOP = 0.955      # sRGB lineal maximo por canal (= codigo ~250): ningun canal saturado se quema


def gamut_map(lab):
    """Reduce croma a L y h constantes hasta que sRGB lineal quede en [0,1]."""
    U = F32(GAMUT_TOP)
    rgb = oklab_to_lin(lab)
    bad = np.any((rgb < -1e-5) | (rgb > U + 1e-5), axis=1)
    if bad.any():
        sub = lab[bad].copy()
        mx = np.maximum(rgb[bad].max(axis=1), 1e-4)
        # la mitad del exceso se resuelve bajando L (densidad de tinte), el resto reduciendo croma
        sub[:, 0] *= np.where(mx > U, np.power(U / mx, F32(1 / 6)), 1.0).astype(F32)
        sub[:, 0] = np.clip(sub[:, 0], 0.0, float(np.cbrt(U)) - 1e-4)
        lo = np.zeros(sub.shape[0], F32); hi = np.ones(sub.shape[0], F32)
        for _ in range(12):
            mid = (lo + hi) * 0.5
            t = sub.copy(); t[:, 1:] *= mid[:, None]
            r = oklab_to_lin(t)
            ok = np.all((r >= -1e-5) & (r <= U + 1e-5), axis=1)
            lo = np.where(ok, mid, lo); hi = np.where(ok, hi, mid)
        sub[:, 1:] *= lo[:, None]
        rgb[bad] = oklab_to_lin(sub)
    return np.clip(rgb, 0.0, float(U))


# ------------------------------------------------------------------------------------------------
# Render
# ------------------------------------------------------------------------------------------------
def load_base(path):
    import tifffile
    return tifffile.imread(str(path)).astype(F32)


def seed_for(name):
    """Seed of the reference engine (hash of the file NAME). Kept only for golden comparisons."""
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16)


def seed_from_file(path, chunk=1 << 20):
    """Deterministic grain seed from the CONTENT of a file (same pixels -> same grain, renaming changes nothing)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return int(h.hexdigest()[:8], 16)


def film_negative(E, scale, grain, rng, timings):
    """E: exposicion de escena Rec.2020 lineal HxWx3 (se modifica). Devuelve densidad sobre base HxWx3."""
    t = time.time()
    # MTF de la pelicula
    E = cv2.GaussianBlur(E, (0, 0), P["film_blur"] * scale)
    # halacion (a 1/4 de resolucion, es un halo ancho)
    Y = E @ LUMA
    if P["hal_strength"] > 0 and (Y > P["hal_threshold"]).any():
        H, W = Y.shape
        small = cv2.resize(Y, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
        ex = np.maximum(small - P["hal_threshold"], 0)
        halo = 0.7 * cv2.GaussianBlur(ex, (0, 0), P["hal_r1"] * scale / 4) + \
            0.3 * cv2.GaussianBlur(ex, (0, 0), P["hal_r2"] * scale / 4)
        halo = cv2.resize(halo, (W, H), interpolation=cv2.INTER_LINEAR)
        E += (P["hal_strength"] * halo)[..., None] * np.asarray(P["hal_color"], F32)
        del halo, small, ex
    del Y
    # sensibilidad espectral de capas
    Mc = np.asarray(SPEC["color_matrix_linear"]["matrix"], F32)
    for y in range(0, E.shape[0], 512):
        E[y:y + 512] = E[y:y + 512] @ Mc.T
    np.maximum(E, F32(1e-5), out=E)
    # log exposicion -> densidad por capa (H&D)
    u = np.log10(E / F32(P["grey"]))
    del E
    d = np.empty_like(u)
    for c in range(3):
        d[..., c] = neg_curve(u[..., c], c)
    del u
    # acopladores DIR (interimagen) en densidad, filas suman 1
    k = P["dir_k"]
    Md = np.full((3, 3), -k, F32); np.fill_diagonal(Md, 1 + 2 * k)
    for y in range(0, d.shape[0], 512):
        d[y:y + 512] = d[y:y + 512] @ Md.T
    # efecto de adyacencia (borde de revelado) en densidad, con umbral suave
    bl = cv2.GaussianBlur(d, (0, 0), P["adj_radius"] * scale)
    bl = d - bl
    th = P["adj_threshold_d"]
    bl *= smoothstep(0.0, 2 * th, np.abs(bl))
    d += F32(P["adj_amount"]) * bl
    del bl
    timings["negativo"] = time.time() - t
    t = time.time()
    gf = grain_fields(d, rng, scale) if grain > 0 else None
    timings["grano"] = time.time() - t
    return d, gf


def _soft_sub(x, b):
    """x - b con pie suave (nunca negativo): equivale a restar el velo/flare del escaner."""
    if b <= 0:
        return x
    e = F32(0.35 * b)
    z = x - F32(b)
    return ((z + np.sqrt(z * z + 4 * e * e)) * 0.5).astype(F32)


def scanner_levels(d):
    """Niveles automaticos por fotograma, como hace el operador de un Frontier/Noritsu: punto negro (resta
    el velo para negros profundos y limpios, nunca lechosos) y punto blanco (escenas nubladas sin blancos
    se llevan a blancos limpios, con hombro que nunca quema). Devuelve dict de parametros."""
    if not P.get("auto_levels", True):
        return dict(bl=0.0, g=1.0)
    s = d[::6, ::6]
    dn = s @ LUMA
    lin = srgb_decode(print_curve(dn) / F32(255.0))
    code = print_curve(dn)
    p_lo = float(np.percentile(code, 0.5))
    tgt_lo = P["lv_black_target"] + P["lv_black_keep"] * max(p_lo - P["lv_black_target"], 0.0)
    bl = 0.0
    if p_lo > tgt_lo + 0.5:
        a = float(srgb_decode(np.float32(p_lo / 255.0))); b_ = float(srgb_decode(np.float32(tgt_lo / 255.0)))
        bl = min((a - b_) / max(1.0 - b_, 1e-3), P["lv_black_max"])
    lin2 = _soft_sub(lin, bl) / F32(max(1.0 - bl, 1e-3))
    p_hi = float(np.percentile(lin2, 99.9))
    t_hi = float(srgb_decode(np.float32(P["lv_white_target"] / 255.0)))
    g = 1.0
    if p_hi < t_hi:
        # resolver g * x / (1 + (g-1) x^2) = t_hi
        x = p_hi
        lo, hi = 1.0, P["lv_gain_max"]
        for _ in range(30):
            m = 0.5 * (lo + hi)
            if m * x / (1 + (m - 1) * x * x) < t_hi:
                lo = m
            else:
                hi = m
        g = lo
    return dict(bl=round(bl, 5), g=round(g, 4), p_lo=round(p_lo, 1), p_hi_code=round(
        float(srgb_encode(np.float32(min(p_hi, 1.0)))) * 255, 1))


def apply_levels(lin, lv):
    bl, g = lv["bl"], lv["g"]
    if bl > 0:
        lin = _soft_sub(lin, bl) / F32(1.0 - bl)
    if g > 1.0:
        Y = np.clip(lin @ np.asarray(REC2020_LUMA, F32), 1e-6, None)
        f = (F32(g) * Y / (1 + F32(g - 1) * Y * Y)) / Y
        lin = lin * f[..., None]
    return lin.astype(F32)


def scan(d, strength, timings):
    """densidad sobre base -> OkLab de salida (tras tintes del escaneo, ANTES de gamut/grano). HxWx3."""
    t = time.time()
    H, W, _ = d.shape
    gam_n = float(P["neg_gamma"][1])
    mix = P["chan_mix"]
    M = np.asarray(REC2020_TO_SRGB, F32)
    lab = np.empty_like(d)
    lv = scanner_levels(d)
    for y in range(0, H, 256):
        dc = d[y:y + 256]
        per = srgb_decode(print_curve(dc) / F32(255.0))
        dn = dc @ LUMA
        ln = srgb_decode(print_curve(dn) / F32(255.0))
        ratio = np.power(F32(10.0), np.clip((dc - dn[..., None]) / F32(gam_n), -3, 3)).astype(F32)
        lin = mix * per + (1 - mix) * ln[..., None] * ratio
        lin = apply_levels(lin, lv)
        # display Rec.2020 lineal -> sRGB lineal -> OkLab
        lab[y:y + 256] = lin_to_oklab(lin @ M.T)
    timings["escaneo"] = time.time() - t
    timings["_niveles"] = lv
    t = time.time()
    # mascara de piel de color (para los tintes) suavizada espacialmente
    L = lab[..., 0]; C = np.hypot(lab[..., 1], lab[..., 2]); h = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
    skin = cv2.GaussianBlur(skin_mask_raw(L, C, h), (0, 0), 3.0)
    del C, h
    if strength > 0:
        for y in range(0, H, 256):
            blk = lab[y:y + 256].reshape(-1, 3)
            sk = skin[y:y + 256].reshape(-1)
            lab[y:y + 256] = oklch_stage(blk, sk, strength).reshape(-1, W, 3)
    timings["color"] = time.time() - t
    return lab


def lab_to_code(lab, step=256):
    """OkLab -> sRGB codificado 0-255 float32 con mapeo de gamut (reduce croma a L,h constantes)."""
    H, W, _ = lab.shape
    out = np.empty_like(lab)
    for y in range(0, H, step):
        blk = lab[y:y + step].reshape(-1, 3)
        out[y:y + step] = (srgb_encode(gamut_map(blk)) * 255.0).reshape(-1, W, 3)
    return out


# ------------------------------------------------------------------------------------------------
# Acabado: NR residual, croma limpia, retoque de piel automatico, nitidez sin halos
# ------------------------------------------------------------------------------------------------
GF = cv2.ximgproc.guidedFilter


def gblur(x, s):
    return cv2.GaussianBlur(x, (0, 0), float(s))


def blur_big(x, s):
    """Desenfoque gaussiano ancho (sigma > 24 px) calculado a resolucion reducida: mismo resultado, 50x mas rapido."""
    if s < 24:
        return gblur(x, s)
    k = int(max(2, s // 8))
    H, W = x.shape[:2]
    sm = cv2.resize(x, (max(1, W // k), max(1, H // k)), interpolation=cv2.INTER_AREA)
    sm = gblur(sm, s / k)
    return cv2.resize(sm, (W, H), interpolation=cv2.INTER_LINEAR)


def estimate_noise(L, scale):
    """sigma del ruido de luminancia (unidades OkLab L) medido en las zonas mas planas: MAD del paso alto
    fino por baldosas de 64 px, percentil 10 de las baldosas (zonas planas = solo ruido)."""
    d = L - gblur(L, 0.8 * scale)
    H, W = d.shape
    t = 64
    hh, ww = H // t, W // t
    a = np.abs(d[:hh * t, :ww * t]).reshape(hh, t, ww, t)
    mad = np.median(a.transpose(0, 2, 1, 3).reshape(hh, ww, -1)[::2, ::2], axis=2)
    Lt = L[:hh * t, :ww * t].reshape(hh, t, ww, t).mean(axis=(1, 3))[::2, ::2]
    ok = (Lt > 0.25) & (Lt < 0.85)
    v = mad[ok] if ok.sum() > 20 else mad.ravel()
    return float(np.percentile(v, 10) * 1.4826)


def _merge_boxes(boxes):
    boxes = sorted(boxes, key=lambda b: -b[2] * b[3])
    keep = []
    for b in boxes:
        x, y, w, h = b
        cx, cy = x + w / 2, y + h / 2
        if any(k[0] <= cx <= k[0] + k[2] and k[1] <= cy <= k[1] + k[3] for k in keep):
            continue
        keep.append(b)
    return keep


def _lab_to_rgb8(sm):
    rgb = srgb_encode(np.clip(oklab_to_lin(sm.reshape(-1, 3)), 0, 1)).reshape(sm.shape)
    return np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)


def detect_faces(lab, long_side=1800):
    """Caras frontales validadas por color de piel en el centro de la caja (YuNet si esta disponible; si no, Haar con
    consenso de dos cascadas + perfil, como el motor de referencia). Cajas (x, y, w, h) a resolucion completa."""
    H, W, _ = lab.shape
    f = long_side / max(H, W)
    sm = cv2.resize(lab, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA)
    rgb8 = _lab_to_rgb8(sm)
    boxes = None
    if "yunet" in faces_mod.backend_order():
        yb = faces_mod.yunet_faces(rgb8)
        boxes = [tuple(int(round(v)) for v in b) for b in yb] if yb is not None else None
    if boxes is None:
        gray = cv2.equalizeHist(cv2.cvtColor(rgb8, cv2.COLOR_RGB2GRAY))
        boxes = faces_mod.haar_boxes(gray)
    L = sm[..., 0]; C = np.hypot(sm[..., 1], sm[..., 2]); h = np.degrees(np.arctan2(sm[..., 2], sm[..., 1])) % 360
    q = skin_mask_raw(L, C, h)
    out = []
    for (x, y, w, hh) in _merge_boxes(boxes):
        core = q[y + hh // 4: y + 3 * hh // 4, x + w // 4: x + 3 * w // 4]
        if w < 0.035 * long_side:          # cajas diminutas = falsos positivos (texturas de tela)
            continue
        if core.size == 0 or core.mean() < 0.25:
            continue
        out.append((x / f, y / f, w / f, hh / f))
    return out


def detect_people(lab, long_side=2200):
    """Caras (con puntos de referencia) y personas con el ayudante Vision de macOS si existe; si no, YuNet / Haar
    (sin puntos de referencia). Devuelve dict faces [(x,y,w,h)], lm [dict|None], humans [(x,y,w,h)], src."""
    H, W, _ = lab.shape
    if "vision" in faces_mod.backend_order():
        f = min(1.0, long_side / max(H, W))
        sm = cv2.resize(lab, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA) if f < 1 else lab
        d = faces_mod.vision_people(_lab_to_rgb8(sm), out_size=(W, H))
        if d is not None:
            return d
    fc = detect_faces(lab, long_side=min(1800, max(H, W)))
    return dict(faces=fc, lm=[None] * len(fc), humans=[], src="yunet" if faces_mod.assets.model_path(
        "yunet", download=False) else "haar")


def scale_people(pp, k):
    return dict(faces=[tuple(v * k for v in b) for b in pp["faces"]],
                lm=[None if l is None else {a: v * k for a, v in l.items()} for l in pp["lm"]],
                humans=[tuple(v * k for v in b) for b in pp["humans"]], src=pp["src"])


def _ellipse(shape, cx, cy, rx, ry):
    H, W = shape
    yy, xx = np.ogrid[:H, :W]
    r = ((xx - cx) / max(rx, 1)) ** 2 + ((yy - cy) / max(ry, 1)) ** 2
    return (1.0 - smoothstep(0.6, 1.0, r.astype(F32))).astype(F32)


def person_region(shape, faces, humans=(), f=0.25):
    """Zona de las PERSONAS detectadas a escala ``f`` (0..1, bordes suaves): cajas de persona (Vision) ampliadas un
    8 %; sin cajas de persona, cabeza/cuello y cuerpo estimados desde cada cara. Fuera de esta zona el retoque de
    piel no actua nunca (madera, pelaje, arena o roca de color piel no son piel)."""
    H, W = shape
    m = np.zeros((H, W), F32)
    for (x, y, w, hh) in faces:
        x, y, w, hh = x * f, y * f, w * f, hh * f
        m = np.maximum(m, _ellipse((H, W), x + w / 2, y + 0.6 * hh, 1.25 * w, 1.6 * hh))      # cabeza + cuello
        if not humans:
            m = np.maximum(m, _ellipse((H, W), x + w / 2, y + 2.9 * hh, 2.3 * w, 2.9 * hh))    # torso, brazos
    for (x, y, w, hh) in humans:
        x, y, w, hh = x * f, y * f, w * f, hh * f
        x0, y0 = int(max(x - 0.08 * w, 0)), int(max(y - 0.08 * hh, 0))
        x1, y1 = int(min(x + 1.08 * w, W)), int(min(y + 1.08 * hh, H))
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = 1.0
    if m.max() > 0:
        m = np.clip(gblur(m, max(1.0, 0.004 * max(H, W))), 0, 1)
    return m.astype(F32)


def build_skin_mask(lab, faces, dbg=None, lms=None, humans=()):
    """Mascara suave de piel a 1/4 de resolucion -> resolucion completa.
    Con caras: modelo de color aprendido de las mejillas/nariz (Mahalanobis en a,b + rango de L), peso 1 en
    la cara y 0.35 en el resto de la PERSONA (person_region); ojos, cejas y boca excluidos por geometria.
    Sin caras: mascara nula (el retoque de piel no actua: no hay personas)."""
    H, W, _ = lab.shape
    if not faces:
        if dbg is not None:
            dbg["mask_small"] = np.zeros((max(1, round(H * 0.25)), max(1, round(W * 0.25))), F32)
        return np.zeros((H, W), F32)
    f = 0.25
    w4, h4 = round(W * f), round(H * f)
    sm = cv2.resize(lab, (w4, h4), interpolation=cv2.INTER_AREA)
    L = sm[..., 0]; a = sm[..., 1]; b = sm[..., 2]
    C = np.hypot(a, b); h = np.degrees(np.arctan2(b, a)) % 360
    g = skin_mask_raw(L, C, h)
    g *= smoothstep(0.30, 0.42, L)
    if faces:
        samp = []
        for (x, y, w, hh) in faces:
            x0, x1 = int((x + 0.25 * w) * f), int((x + 0.75 * w) * f)
            y0, y1 = int((y + 0.48 * hh) * f), int((y + 0.75 * hh) * f)
            sub = sm[y0:y1, x0:x1].reshape(-1, 3)
            gs = g[y0:y1, x0:x1].reshape(-1)
            sel = sub[gs > 0.35]
            if len(sel) < 0.2 * len(sub):      # luz de color (interior rojo): la piel sale del calificador
                Ls_ = sub[:, 0]
                sel = sub[(Ls_ > np.percentile(Ls_, 30)) & (Ls_ < np.percentile(Ls_, 95))] if len(sub) else sel
            samp.append(sel)
        samp = np.concatenate(samp) if samp else np.zeros((0, 3), F32)
    if faces and len(samp) > 50:
        mu = samp[:, 1:].mean(0)
        cov = np.cov(samp[:, 1:].T) * 1.6 + np.eye(2) * (0.006 ** 2)
        ci = np.linalg.inv(cov)
        da = a - mu[0]; db = b - mu[1]
        d2 = ci[0, 0] * da * da + 2 * ci[0, 1] * da * db + ci[1, 1] * db * db
        mcol = np.exp(-0.5 * d2 / 1.5).astype(F32)
        Lmu, Lsd = float(np.median(samp[:, 0])), float(samp[:, 0].std())
        mL = np.exp(-0.5 * ((L - Lmu) / (2.5 * Lsd + 0.07)) ** 2).astype(F32)
        q = np.sqrt(np.maximum(g, 0.25 * mcol)) * mcol * mL
        region = F32(0.35) * person_region((h4, w4), faces, humans or (), f)
        excl = np.ones((h4, w4), F32)
        lms = lms if lms is not None else [None] * len(faces)
        for (x, y, w, hh), lm in zip(faces, lms):
            x, y, w, hh = x * f, y * f, w * f, hh * f
            if lm is not None:
                # caja de Vision (cejas-barbilla): elipse ampliada (frente, mandibula, cuello cercano)
                region = np.maximum(region, _ellipse((h4, w4), x + w / 2, y + 0.42 * hh, 0.85 * w, 1.05 * hh))
                pm = np.zeros((h4, w4), np.uint8)
                for key, grow, rad in (("leftEye", 1.7, 0.035), ("rightEye", 1.7, 0.035),
                                       ("leftEyebrow", 1.25, 0.03), ("rightEyebrow", 1.25, 0.03),
                                       ("outerLips", 1.12, 0.012)):
                    pts = lm.get(key)
                    if pts is None or len(pts) < 3:
                        continue
                    c0 = pts.mean(0)
                    hull = cv2.convexHull(((c0 + (pts - c0) * grow) * f).astype(np.int32))
                    cv2.fillConvexPoly(pm, hull, 1)
                    cv2.polylines(pm, [hull], True, 1, thickness=max(1, int(rad * w)))
                nose = lm.get("nose")
                if nose is not None and len(nose) >= 3:   # orificios: parte baja de la nariz
                    lo = nose[nose[:, 1] > np.quantile(nose[:, 1], 0.6)]
                    if len(lo) >= 3:
                        cv2.fillConvexPoly(pm, cv2.convexHull((lo * f).astype(np.int32)), 1)
                excl = np.minimum(excl, 1 - np.clip(gblur(pm.astype(F32), max(1.0, 0.012 * w)) * 1.6, 0, 1))
                continue
            region = np.maximum(region, _ellipse((h4, w4), x + w / 2, y + 0.55 * hh, 0.62 * w, 0.78 * hh))
            for ex in (0.30, 0.70):   # ojos (con pestanas)
                excl = np.minimum(excl, 1 - _ellipse((h4, w4), x + ex * w, y + 0.40 * hh, 0.15 * w, 0.085 * hh))
                excl = np.minimum(excl, 1 - 0.8 * _ellipse((h4, w4), x + ex * w, y + 0.29 * hh, 0.16 * w, 0.05 * hh))
            excl = np.minimum(excl, 1 - 0.65 * _ellipse((h4, w4), x + 0.5 * w, y + 0.80 * hh, 0.20 * w, 0.085 * hh))
            excl = np.minimum(excl, 1 - 0.5 * _ellipse((h4, w4), x + 0.5 * w, y + 0.64 * hh, 0.09 * w, 0.05 * hh))
        q = q * region * excl
    else:
        # caras sin muestra de piel fiable: calificador estricto (croma de piel real, no pelo castano ni telas
        # beige), suave y SOLO dentro de las personas detectadas
        q = g * smoothstep(0.055, 0.075, C) * (1 - smoothstep(0.12, 0.15, C)) * smoothstep(0.45, 0.55, L) * 0.25
        q = q * person_region((h4, w4), faces, humans or (), f)
    q = gblur(q.astype(F32), 2.0)
    if dbg is not None:
        dbg["mask_small"] = q.copy()
    return cv2.resize(q, (W, H), interpolation=cv2.INTER_LINEAR)


def finish(lab, scale, faces, timings, skin_amt=1.0, sharp_amt=1.0, dbg=None, subj=None, blem_amt=1.0, lms=None,
           humans=()):
    """Acabado sobre la imagen limpia en OkLab (in place). El retoque de piel (alisado, rojeces, granos) solo
    actua dentro de la mascara de piel de las PERSONAS detectadas; sin caras es un no-op. La nitidez solo toca L."""
    t = time.time()
    L = np.ascontiguousarray(lab[..., 0])
    sig = estimate_noise(L, scale)
    info = {"noise_L_x100_in": round(sig * 100, 3)}
    # 1. NR residual de luminancia: filtro guiado auto-guiado (alisa solo donde la varianza local < ruido)
    w_nr = float(smoothstep(0.0025, 0.007, np.float32(sig)))
    if w_nr > 0:
        r = max(1, round(2 * scale))
        Lg = GF(L, L, r, float((2.6 * sig) ** 2))
        L += F32(0.9 * w_nr) * (Lg - L)
        # segunda pasada mas ancha solo en sombras (donde el empuje de exposicion deja mas ruido)
        Lg = GF(L, L, max(2, round(4 * scale)), float((2.0 * sig) ** 2))
        wsh = (1 - smoothstep(0.25, 0.45, L)) * F32(0.7 * w_nr)
        L += wsh * (Lg - L)
        del Lg, wsh
    # 2. croma residual: filtro guiado por L (el color sigue a los bordes de luminancia, sin motas)
    for c in (1, 2):
        ch = np.ascontiguousarray(lab[..., c])
        f1 = GF(L, ch, max(2, round(3 * scale)), 0.004 ** 2)
        lab[..., c] = ch + F32(0.85) * (f1 - ch)
    timings["nr_residual"] = time.time() - t
    t = time.time()
    # 3. retoque de piel (separacion de frecuencias, bordes protegidos)
    if not faces and (skin_amt > 0 or blem_amt > 0):
        _LOG.info("no faces: skin retouch skipped")
        info["skin_retouch"] = "skipped: no faces"
    mask = build_skin_mask(lab, faces, dbg, lms, humans) * F32(skin_amt)
    fw = float(np.median([b[2] for b in faces])) if faces else 0.07 * max(L.shape)
    m1 = gblur(L, 2.0 * scale); m2 = gblur(L * L, 2.0 * scale)
    lstd = np.sqrt(np.maximum(m2 - m1 * m1, 0))
    del m1, m2
    protect = 1 - smoothstep(0.018, 0.04, lstd)        # pestanas, cejas, bordes de labios, pelo
    del lstd
    mask *= protect
    mask = gblur(mask, 1.5 * scale)
    del protect
    info.update(faces=len(faces), face_w=round(fw), skin_px_pct=round(float((mask > 0.3).mean() * 100), 2))
    if float(mask.max()) > 0.05:
        ys, xs = np.where(mask[::8, ::8] > 0.02)
        pad = int(fw * 0.3) + 64
        y0, y1 = max(ys.min() * 8 - pad, 0), min(ys.max() * 8 + pad, L.shape[0])
        x0, x1 = max(xs.min() * 8 - pad, 0), min(xs.max() * 8 + pad, L.shape[1])
        Ls = np.ascontiguousarray(L[y0:y1, x0:x1]); ms = mask[y0:y1, x0:x1]
        base = gblur(Ls, 1.0 * scale)
        fine = Ls - base                                         # poros/micro textura
        r = max(4, round(fw / 40))
        S = GF(base, base, r, 0.022 ** 2)                        # manchas y relieve medio, bordes respetados
        S2 = GF(base, base, max(2, r // 3), 0.012 ** 2)          # imperfecciones finas
        Lnew = S + F32(0.30) * (S2 - S) + F32(0.12) * (base - S2) + F32(0.55) * fine
        Ls += ms * (Lnew - Ls)
        L[y0:y1, x0:x1] = Ls
        # color: unificar manchas rojizas (cachetes) respecto al tono medio de la cara
        wn_ = blur_big(ms, max(8.0, fw / 3.5)) + F32(1e-3)
        wn2_ = blur_big(ms, max(8.0, fw / 7)) + F32(1e-3)
        for c, red_k, big, wn in ((1, 0.65, max(8.0, fw / 3.5), wn_), (2, 0.30, max(8.0, fw / 7), wn2_)):
            ch = np.ascontiguousarray(lab[y0:y1, x0:x1, c])
            sm_ = GF(base, ch, r * 2, 0.03 ** 2)
            cb = blur_big(sm_ * ms, big) / wn        # referencia = piel vecina (media ponderada por la mascara)
            cb = np.where(wn > 0.05, cb, sm_)
            dev = sm_ - cb
            dev = np.where(dev > 0, dev * F32(1 - red_k), dev * F32(0.8))
            new = cb + dev + F32(0.35) * (ch - sm_)
            lab[y0:y1, x0:x1, c] = ch + ms * (new - ch)
        # tono de piel natural: nunca rojizo/magenta (matiz >= ~43) ni sobresaturado (croma suave <= ~0.09)
        a_ = lab[y0:y1, x0:x1, 1]; b_ = lab[y0:y1, x0:x1, 2]
        C_ = np.hypot(a_, b_); h_ = np.degrees(np.arctan2(b_, a_))
        mc = np.clip(ms * 1.6, 0, 1) * smoothstep(0.0, 12.0, h_) * (1 - smoothstep(70.0, 90.0, h_))
        h2 = h_ + F32(0.8) * np.maximum(F32(44.0) - h_, 0) * mc
        kn_ = F32(0.082)
        C2 = np.where(C_ > kn_, kn_ + (C_ - kn_) / (1 + (C_ - kn_) / F32(0.03)), C_)
        C2 = C_ + mc * (C2 - C_)
        hr_ = np.radians(h2)
        lab[y0:y1, x0:x1, 1] = C2 * np.cos(hr_); lab[y0:y1, x0:x1, 2] = C2 * np.sin(hr_)
        del base, fine, S, S2, Lnew, Ls, a_, b_, C_, h_, mc, h2, C2, hr_
    timings["piel"] = time.time() - t
    t = time.time()
    # 4. nitidez: deconvolucion aproximada (2 bandas finas con umbral de ruido y limite de sobreoscilacion)
    #    + contraste local de frecuencias medias con base de filtro guiado (sin halos). Piel reducida.
    if sharp_amt > 0:
        g07 = gblur(L, 0.7 * scale); g20 = gblur(L, 1.8 * scale)
        d1 = L - g07; d2 = g07 - g20
        t1 = 2.2 * sig + 2e-4; t2 = 1.6 * sig + 2e-4
        d1 *= 1 - np.exp(-(d1 / F32(t1)) ** 2)
        d2 *= 1 - np.exp(-(d2 / F32(t2)) ** 2)
        base = GF(L, L, max(4, round(14 * scale)), 0.03 ** 2)
        lc = g20 - base
        del g07, g20, base
        ksk = 1 - F32(0.75) * mask
        hl = 1 - smoothstep(0.88, 0.985, L)
        sh = F32(0.35) + F32(0.65) * smoothstep(0.08, 0.22, L)
        fine_d = F32(sharp_amt) * (F32(0.75) * d1 + F32(0.45) * d2) * ksk * sh
        del d1, d2
        fine_d = np.where(fine_d > 0, fine_d * hl, fine_d)
        mx = cv2.dilate(L, np.ones((3, 3), np.uint8)); mn = cv2.erode(L, np.ones((3, 3), np.uint8))
        Ln = np.clip(L + fine_d, mn - F32(0.010), mx + F32(0.010))
        del mx, mn, fine_d
        lcd = F32(0.20 * sharp_amt) * lc * (1 - F32(0.6) * mask) * sh
        if subj is not None:   # un toque mas de claridad en el sujeto (personas/objeto principal), no en la piel
            lcd *= 1 + F32(0.6) * subj * (1 - mask)
        lcd = np.where(lcd > 0, lcd * hl, lcd)
        L[:] = Ln + lcd
        del Ln, lcd, lc
    lab[..., 0] = L
    info["noise_L_x100_out"] = round(estimate_noise(L, scale) * 100, 3)
    timings["nitidez"] = time.time() - t
    # 5. ULTIMO paso de retoque (antes del grano): granos, puntos negros y rojeces pequenas (spot healing)
    t = time.time()
    if blem_amt > 0 and faces:
        hdbg = {} if dbg is not None else None
        info.update(blemish.heal_blemishes(lab, mask, fw, strength=blem_amt, dbg=hdbg))
        if hdbg:
            dbg["heal"] = hdbg.get("heal")
    timings["granos"] = time.time() - t
    if dbg is not None:
        dbg["mask"] = mask
    return info


# ------------------------------------------------------------------------------------------------
# Grano a resolucion completa y version web con grano propio
# ------------------------------------------------------------------------------------------------
GRAIN_TAB = np.array(SPEC["grain"]["amplitude_vs_luma"]["points"], F32)
YW = np.array([0.2126, 0.7152, 0.0722], F32)


def apply_grain(o, gf, gscale):
    H = o.shape[0]
    for y in range(0, H, 256):
        blk = o[y:y + 256]
        amp = np.interp(blk @ YW, GRAIN_TAB[:, 0], GRAIN_TAB[:, 1]).astype(F32) * F32(gscale)
        blk += gf[y:y + 256] * amp[..., None]
        np.clip(blk, 0.0, 254.5, out=blk)


def web_grain_field(H, W, rng):
    """Grano re-sintetizado a escala web (no es el grano completo reducido, que se vuelve ruido grueso):
    estructura de 0.5-0.9 px con agrupamiento leve, 96 % luminancia."""
    n1 = gblur(rng.standard_normal((H, W), dtype=F32), 0.5)
    n2 = gblur(rng.standard_normal((H, W), dtype=F32), 0.9)
    g = n1 / n1.std() + 0.5 * n2 / n2.std()
    cl = gblur(rng.standard_normal((H, W), dtype=F32), 3.0)
    g *= np.clip(1 + 0.18 * cl / cl.std(), 0.7, 1.3)
    g = np.sign(g) * np.abs(g) ** F32(0.9)
    g /= g.std()
    ch = np.stack([gblur(rng.standard_normal((H, W), dtype=F32), 0.6) for _ in range(3)], -1)
    ch -= (ch @ LUMA)[..., None]
    ch /= ch.std() + 1e-8
    return (g[..., None] + F32(0.04) * ch).astype(F32)


def make_web(clean, rng, grain, long_side=2560):
    H, W, _ = clean.shape
    f = long_side / max(H, W)
    w, h = round(W * f), round(H * f)
    sm = cv2.resize(clean, (w, h), interpolation=cv2.INTER_AREA)
    # nitidez de salida suave, solo luminancia
    Y = sm @ YW
    d = Y - gblur(Y, 0.6)
    d *= 1 - np.exp(-(d / 0.8) ** 2)
    sm += F32(0.35) * d[..., None]
    np.clip(sm, 0, 254.5, out=sm)
    if grain > 0:
        apply_grain(sm, web_grain_field(h, w, rng), 0.42 * grain * P["g_global"])
    tp = rng.random(sm.shape, dtype=F32) - rng.random(sm.shape, dtype=F32)
    return np.clip(np.rint(sm + tp), 0, 255).astype(np.uint8)



# ------------------------------------------------------------------------------------------------
# Exposicion guiada por el SUJETO y acento del sujeto
# ------------------------------------------------------------------------------------------------
SUBJ_TARGET_L = 0.722      # OkLab L de la piel en el lado iluminado (~ L* 69, sRGB ~185): suelo para aclarar
SUBJ_HIGH_L = 0.83         # techo de la banda de altas de la piel iluminada (~ L* 80): solo por ENCIMA se oscurece
SUBJ_CLIP_L = 0.91         # p99 de la cara a ~1/3 de stop del hombro del papel (sRGB ~225; max. ~240): quemado
SUBJ_DARKEN_CAP = -0.3     # oscurecimiento maximo por la banda de altas (sin riesgo de quemado)
SUBJ_EV_RANGE = (-1.2, 1.75)


def _face_levels(lab, faces):
    """Por cara: (percentil 80 de L de la piel = lado iluminado, area, p99 de L de la caja = brillos de la cara).
    Piel por color; si la luz es de color y el calificador falla, tonos medios-altos de la caja. El p99 se mide en
    toda la caja (la piel quemada pierde croma y deja de calificar como piel)."""
    L = lab[..., 0]
    out = []
    H, W = L.shape
    for (x, y, w, hh) in faces:
        x0, x1 = max(int(x + 0.15 * w), 0), min(int(x + 0.85 * w), W)
        y0, y1 = max(int(y + 0.10 * hh), 0), min(int(y + 0.95 * hh), H)
        if x1 - x0 < 4 or y1 - y0 < 4:
            out.append(None); continue
        sub = L[y0:y1, x0:x1]
        a = lab[y0:y1, x0:x1, 1]; b = lab[y0:y1, x0:x1, 2]
        q = skin_mask_raw(sub, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360)
        v = sub[q > 0.3]
        if v.size < 0.15 * sub.size:
            lo, hi = np.percentile(sub, [35, 98])
            v = sub[(sub >= lo) & (sub <= hi)]
        out.append((float(np.percentile(v, 80)), float(w * hh), float(np.percentile(sub, 99))))
    return out


def _subject_level(lab, people):
    """Nivel del sujeto, brillos de las caras y objetivo: (nivel, p99_caras | None, objetivo, fuente). Con caras:
    lado iluminado de la piel (domina la cara mas luminosa para no quemarla; las caras en sombra las levanta
    despues el acento local). Sin caras: clave global luminosa y, si hay personas detectadas, que su tono medio
    no quede por debajo del gris medio."""
    fl = [v for v in _face_levels(lab, people["faces"]) if v]
    if fl:
        amax = max(a for _, a, _ in fl)
        fl = [v for v in fl if v[1] >= 0.2 * amax]
        wsum = sum(a for _, a, _ in fl)
        mean = sum(v * a for v, a, _ in fl) / wsum
        top = max(v for v, _, _ in fl)
        return 0.6 * top + 0.4 * mean, max(p for _, _, p in fl), SUBJ_TARGET_L, "caras"
    L = lab[..., 0]
    lvl = float(np.median(L[::2, ::2])); tgt = 0.56; src = "global"
    hv = []
    H, W = L.shape
    for (x, y, w, hh) in people["humans"]:
        if w * hh > 0.6 * H * W:
            continue
        sub = L[max(int(y + 0.1 * hh), 0):int(y + 0.9 * hh), max(int(x + 0.2 * w), 0):int(x + 0.8 * w)]
        if sub.size > 50:
            hv.append(float(np.median(sub)))
    if hv:
        hl = float(np.mean(hv))
        err_h = min(0.54 - hl, (tgt - lvl) + 0.04)
        if err_h > tgt - lvl:
            return tgt - err_h, None, tgt, "personas"
    return lvl, None, tgt, src


CAST_NEUTRAL_PCT = 20.0    # la dominante se mide en el 20 % menos cromatico de los tonos medios


def cast_estimate(lab):
    """Dominante de ILUMINANTE (a, b) medida solo en los tonos medios casi neutros: el CAST_NEUTRAL_PCT % de
    pixeles de menor croma. Una dominante de luz (tungsteno, paredes de color) desplaza tambien a los neutros;
    el color del CONTENIDO (follaje verde, cesped, un ramo amarillo) no. La mediana de TODOS los tonos medios
    (version anterior) confundia contenido con iluminante: una escena verde se 'corregia' hacia magenta y el
    pelaje blanco de un perro sobre cesped quedaba lila. Devuelve (ab, n) o (None, 0)."""
    s_ = lab[::2, ::2].reshape(-1, 3)
    m = (s_[:, 0] > 0.35) & (s_[:, 0] < 0.92)
    if m.sum() < 100:
        return None, 0
    ab = s_[m, 1:]
    C = np.hypot(ab[:, 0], ab[:, 1])
    sel = ab[C <= np.percentile(C, CAST_NEUTRAL_PCT)]
    if len(sel) < 50:
        return None, 0
    return np.median(sel, axis=0), len(sel)


def cast_gains(lab):
    """Balance de color del laboratorio: solo si la LUZ de la escena tiene una dominante fuerte (interior con luz
    rebotada de paredes de color, tungsteno). Mide la dominante en los neutros (cast_estimate); por debajo de
    0.03 no hace nada. Corrige el 60 % del exceso. Devuelve ganancias Rec.2020 (luminancia constante) o None."""
    ab, _ = cast_estimate(lab)
    if ab is None:
        return None, 0.0
    mag = float(np.hypot(*ab))
    if mag <= 0.03:
        return None, mag
    c = ab / mag * min(0.6 * (mag - 0.03), 0.035)
    rgb = oklab_to_lin(np.array([[0.7, c[0], c[1]]], F32))[0] @ np.linalg.inv(np.asarray(REC2020_TO_SRGB, F32)).T
    g = 1.0 / np.maximum(rgb, 1e-3)
    g = g / float((g * rgb / rgb.sum()).sum() if False else (LUMA @ (g * rgb)) / (LUMA @ rgb))
    g = g / float(LUMA @ g)
    return g.astype(F32), mag


def _secant(hist, j, default, lo, hi):
    """Pendiente (unidades de L por stop) de la columna j del historial (corr, nivel, p99) entre las dos ultimas
    vistas previas; ``default`` si aun no hay dos exposiciones distintas o la medida no existe."""
    if len(hist) >= 2 and abs(hist[-1][0] - hist[-2][0]) > 1e-3 and hist[-1][j] is not None \
            and hist[-2][j] is not None:
        return float(np.clip((hist[-1][j] - hist[-2][j]) / (hist[-1][0] - hist[-2][0]), lo, hi))
    return default


def subject_exposure(E, scale, strength, rng):
    """Vista previa fisica a 1/4 de resolucion (mismo negativo + escaneo) para medir al sujeto y corregir la
    exposicion. La exposicion del sujeto es ASIMETRICA (imagen luminosa, sujeto acentuado, altas protegidas):

    * piel iluminada por debajo de SUBJ_TARGET_L (L* ~69): se ACLARA hasta el objetivo, sin llevar los brillos
      de la cara (p99) al hombro del papel;
    * entre el objetivo y SUBJ_HIGH_L (L* ~80): bien expuesta, se conserva la exposicion global del RAW;
    * solo se OSCURECE si la piel iluminada supera la banda de altas (hasta SUBJ_DARKEN_CAP = -0.3 EV) o si el
      p99 de la cara roza el quemado (SUBJ_CLIP_L; aqui se permite bajar lo necesario, hasta -1.2 EV).

    Antes se llevaba siempre la piel a L 0.722 tambien hacia abajo: un retrato bien expuesto (piel L* ~78, sin
    nada quemado) se apagaba -0.64 EV y quedaba neutro pero sin luz. Personas sin cara: solo se aclara.
    Devuelve (dEV, info, personas)."""
    H, W, _ = E.shape
    k = 4 if max(H, W) > 3000 else 1
    small = cv2.resize(E, (W // k, H // k), interpolation=cv2.INTER_AREA) if k > 1 else E.copy()
    corr = 0.0; info = {}
    people = None
    hist = []
    mode = "mantener"
    for it in range(5):
        tm = {}
        d, _ = film_negative(small * F32(2.0 ** corr), scale / k, 0, rng, tm)
        lab = scan(d, strength, tm)
        del d
        if it == 0:
            gains, cmag = cast_gains(lab)
            info["cast"] = round(cmag, 3)
            if gains is not None:
                info["cast_gains"] = [round(float(v), 3) for v in gains]
                small *= gains
                d, _ = film_negative(small * F32(2.0 ** corr), scale / k, 0, rng, tm)
                lab = scan(d, strength, tm)
                del d
            people = detect_people(lab)
        lvl, fhi, tgt, src = _subject_level(lab, people)
        trust = {"caras": 1.0, "personas": 0.85, "global": 0.7}[src]
        hist.append((corr, lvl, fhi))
        if src == "global":
            # sin personas no hay sujeto que medir: se conserva la exposicion global (analisis de exposicion del
            # RAW, que ya pondera el elemento destacado). Antes se llevaba la mediana de L a 0.56, lo que
            # levantaba todo el fotograma hasta +1.75 EV en escenas oscuras sin personas.
            break
        if it == 0:
            if src == "caras" and (lvl > SUBJ_HIGH_L or (fhi is not None and fhi > SUBJ_CLIP_L)):
                mode = "proteger"
            elif lvl < tgt - 0.008:
                mode = "aclarar"
            else:
                break            # piel dentro de la banda [objetivo, altas]: exposicion global del RAW
        s_l = _secant(hist, 1, 0.075, 0.03, 0.12)     # ~0.075 L por stop en la zona de piel de la curva Ektar
        s_h = _secant(hist, 2, 0.06, 0.02, 0.12)      # los brillos se comprimen en el hombro: mas EV por L
        to_clip = (SUBJ_CLIP_L - 0.005 - fhi) / s_h if fhi is not None else np.inf
        if mode == "aclarar":
            new = float(np.clip(corr + (tgt - lvl) / s_l * (trust if it == 0 else 1.0), 0.0, SUBJ_EV_RANGE[1]))
            new = float(min(new, max(corr + to_clip, SUBJ_EV_RANGE[0])))  # no aclarar hasta quemar la cara
        else:
            # proteger: la MAYOR exposicion <= 0 que deja la piel iluminada en la banda (como mucho
            # SUBJ_DARKEN_CAP) y el p99 de la cara por debajo del quemado (este puede pedir mas, hasta -1.2 EV)
            band = corr + (SUBJ_HIGH_L - 0.01 - lvl) / s_l if hist[0][1] > SUBJ_HIGH_L else 0.0
            new = float(min(max(band, SUBJ_DARKEN_CAP), max(corr + to_clip, SUBJ_EV_RANGE[0]), 0.0))
        if abs(new - corr) < 0.02:
            break
        corr = new
    info.update(subj_src=src, det=people["src"], subj_mode=mode, subj_L_before=round(hist[0][1], 3),
                subj_L_after=round(hist[-1][1], 3), subj_ev=round(corr, 3), subj_target=round(tgt, 3),
                subj_high=SUBJ_HIGH_L)
    if hist[0][2] is not None:
        info.update(face_p99_before=round(hist[0][2], 3), face_p99_after=round(hist[-1][2], 3))
    return corr, info, scale_people(people, float(k))


def subject_mask(shape, people, lab_small=None, f=0.125):
    """Mascara suave del sujeto: caras + cuerpos (personas de Vision o cuerpo estimado desde la cara).
    Sin personas detectadas: mascara vacia (no hay acento; la exposicion queda global)."""
    H, W = shape
    faces = people["faces"]; humans = [h for h in people["humans"]]
    h8, w8 = max(1, round(H * f)), max(1, round(W * f))
    m = np.zeros((h8, w8), F32)
    for (x, y, w, hh) in faces:
        x, y, w, hh = x * f, y * f, w * f, hh * f
        m = np.maximum(m, _ellipse((h8, w8), x + w / 2, y + hh * 0.45, 0.95 * w, 1.1 * hh))
        if not humans:
            m = np.maximum(m, 0.85 * _ellipse((h8, w8), x + w / 2, y + hh * 2.9, 1.7 * w, 2.4 * hh))
    for (x, y, w, hh) in humans:
        x, y, w, hh = x * f, y * f, w * f, hh * f
        m = np.maximum(m, 0.85 * _ellipse((h8, w8), x + w / 2, y + hh / 2, 0.56 * w, 0.56 * hh))
    # Sin personas detectadas NO hay sujeto: la antigua alternativa "piel por color" tomaba como sujeto cualquier
    # cosa de color piel (pelaje claro, nubes al atardecer, roca calida) y la aclaraba. ``lab_small`` se ignora.
    fw = float(np.median([b[2] for b in faces])) * f if faces else 0.02 * max(h8, w8)
    m = gblur(m, max(2.0, 0.45 * fw))
    if m.max() > 0:
        m = np.clip(m / max(float(m.max()), 1e-3), 0, 1)
    return m


def subject_accent(lab, people, amt, info):
    """Acento sutil del sujeto: +0.2 EV en personas, hasta +0.45 EV extra en caras que quedaron en sombra
    (cada cara segun su deficit respecto al objetivo), -0.2 EV en zonas claras del fondo que distraen.
    Mascaras muy suaves y proteccion de altas: sin halos ni aspecto HDR."""
    H, W, _ = lab.shape
    f = 0.125
    sm = cv2.resize(lab, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA)
    m8 = subject_mask((H, W), people, sm, f)
    if float(m8.max()) < 0.05 or amt <= 0:
        info["accent"] = 0
        return None
    h8, w8 = m8.shape
    fl = _face_levels(sm, [tuple(v * f for v in b) for b in people["faces"]])
    face_lift = np.zeros_like(m8)
    lifts = []
    for (x, y, w, hh), v in zip(people["faces"], fl):
        if not v:
            continue
        dz = float(np.clip((SUBJ_TARGET_L - 0.01 - v[0]) / 0.075, 0.0, 0.6))
        lifts.append(round(dz, 2))
        if dz > 0.02:
            x, y, w, hh = x * f, y * f, w * f, hh * f
            e = gblur(_ellipse((h8, w8), x + w / 2, y + 0.45 * hh, 1.0 * w, 1.25 * hh), max(1.5, 0.3 * w))
            face_lift = np.maximum(face_lift, dz * e)
    Lb = gblur(sm[..., 0], 0.02 * max(sm.shape[:2]))
    bright = smoothstep(0.72, 0.92, Lb) * (1 - m8)
    ev_map = (F32(amt) * np.minimum(F32(0.15) * m8 + face_lift, 0.65) - F32(0.20 * amt) * bright).astype(F32)
    ev_map = cv2.resize(ev_map, (W, H), interpolation=cv2.INTER_LINEAR)
    m = cv2.resize(m8, (W, H), interpolation=cv2.INTER_LINEAR)
    for y in range(0, H, 512):
        L = lab[y:y + 512, :, 0]
        e = ev_map[y:y + 512]
        # proteccion de altas: el realce se desvanece por encima de L 0.82 (piel muy clara, vestido blanco)
        e = np.where(e > 0, e * (1 - smoothstep(0.80, 0.95, L)), e)
        g = np.power(F32(2.0), e / F32(3.0))         # OkLab L ~ Y^(1/3)
        lab[y:y + 512, :, 0] = L * g
        lab[y:y + 512, :, 1:] *= g[..., None]
    info["accent"] = round(float((m8 > 0.5).mean() * 100), 1)
    info["face_lift_ev"] = lifts
    fl2 = _face_levels(lab[::4, ::4], [tuple(v * 0.25 for v in b) for b in people["faces"]])
    info["face_L_final"] = [round(v[0], 3) for v in fl2 if v]
    return m


def render(base_tiff_path, out_path, ev=None, strength=1.0, grain=1.0, crops_dir=None, ev_offset=0.0,
           save_tiff16=False, params=None, skin=1.0, sharp=1.0, web=True, dbg_dir=None, subject=1.0, blem=1.0,
           downscale=1, seed=None, web_long_side=2560):
    """Render a neutral base to the film look. ``seed``: grain seed (default: from the base file CONTENT)."""
    global PRINT_D, PRINT_CODE
    if params:
        saved = dict(P)
        P.update(params)
        PRINT_D, PRINT_CODE = build_print_lut()
        try:
            return _render(base_tiff_path, out_path, ev, strength, grain, crops_dir, ev_offset, save_tiff16, skin,
                           sharp, web, dbg_dir, subject, blem, downscale, seed, web_long_side)
        finally:
            P.clear()
            P.update(saved)
            PRINT_D, PRINT_CODE = build_print_lut()
    return _render(base_tiff_path, out_path, ev, strength, grain, crops_dir, ev_offset, save_tiff16, skin, sharp, web,
                   dbg_dir, subject, blem, downscale, seed, web_long_side)


def _render(base_tiff_path, out_path, ev, strength, grain, crops_dir, ev_offset, save_tiff16, skin, sharp, web,
            dbg_dir, subject, blem, downscale, seed, web_long_side):
    t0 = time.time()
    timings = {}
    base_tiff_path = Path(base_tiff_path)
    name = base_tiff_path.stem
    img = load_base(base_tiff_path)
    if downscale and downscale > 1:     # prueba rapida a resolucion reducida (todo el modelo escala con ella)
        img = cv2.resize(img, (img.shape[1] // downscale, img.shape[0] // downscale), interpolation=cv2.INTER_AREA)
        web = False
    H, W, _ = img.shape
    scale = max(H, W) / REF_LONG_SIDE_PX
    if ev is None:
        Ys = img[::8, ::8] @ LUMA
        ev = float(np.log2(0.18 / max(np.median(Ys), 1e-6)))
    ev = float(ev) + ev_offset
    E = img * F32(2.0 ** ev)
    del img
    # guardia de altas: p99.8 de luminancia de escena <= 4.0 (= +4.5 EV sobre gris)
    Ys = E[::4, ::4] @ LUMA
    p998 = float(np.percentile(Ys, 99.8))
    ev_guard = 0.0
    if p998 > 4.0:
        ev_guard = -float(np.log2(p998 / 4.0))
        E *= F32(2.0 ** ev_guard)
    # metricas de la base (sRGB simple a la misma exposicion)
    Mb = np.asarray(REC2020_TO_SRGB, F32)
    bs = E[::4, ::4] @ Mb.T
    base_code = srgb_encode(np.clip(bs, 0, None)) * 255
    base_clip = float(np.mean(np.any(base_code >= 254, axis=2)) * 100)
    blab = lin_to_oklab(np.clip(bs, 0, 1).reshape(-1, 3))
    base_C = float(np.mean(np.hypot(blab[:, 1], blab[:, 2])))
    del bs, base_code, blab, Ys
    if seed is None:
        seed = seed_from_file(base_tiff_path)
    seed = int(seed)
    rng = np.random.default_rng(seed)
    sinfo = {}
    people = None
    if subject > 0:
        t = time.time()
        corr, sinfo, people = subject_exposure(E, scale, strength, np.random.default_rng(1))
        corr *= subject
        if "cast_gains" in sinfo:
            E *= np.asarray(sinfo["cast_gains"], F32)
        if abs(corr) > 0.01:
            E *= F32(2.0 ** corr)
        ev += corr
        timings["exposicion_sujeto"] = time.time() - t
    d, gf = film_negative(E, scale, grain, rng, timings)
    lab = scan(d, strength, timings)
    del d
    t = time.time()
    if people is None:
        people = detect_people(lab)
    faces = people["faces"]
    timings["caras"] = time.time() - t
    t = time.time()
    subj = subject_accent(lab, people, subject, sinfo) if subject > 0 else None
    timings["acento"] = time.time() - t
    dbg = {}
    finfo = finish(lab, scale, faces, timings, skin_amt=skin, sharp_amt=sharp, dbg=dbg, subj=subj, blem_amt=blem,
                   lms=people["lm"], humans=people.get("humans") or ())
    finfo.update(sinfo)
    del subj
    t = time.time()
    clean = lab_to_code(lab)
    del lab
    timings["gamut"] = time.time() - t
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if web:
        t = time.time()
        wdir = out_path.parent / "web2560"
        wdir.mkdir(exist_ok=True)
        save_jpeg(make_web(clean, np.random.default_rng(seed + 7), grain, long_side=web_long_side),
                  wdir / out_path.name, quality=95)
        timings["web"] = time.time() - t
    skin_small = cv2.resize(dbg["mask"], (max(W // 16, 1), max(H // 16, 1)), interpolation=cv2.INTER_AREA)
    if dbg_dir:
        Path(dbg_dir).mkdir(parents=True, exist_ok=True)
        m = dbg["mask"]
        sm = cv2.resize(clean, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
        ms = cv2.resize(m, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
        vis = sm * (1 - 0.6 * ms[..., None]) + np.array([255, 0, 255], F32) * 0.6 * ms[..., None]
        for (x, y, w, hh) in faces:
            cv2.rectangle(vis, (int(x / 4), int(y / 4)), (int((x + w) / 4), int((y + hh) / 4)), (0, 255, 0), 2)
        save_jpeg(np.clip(vis, 0, 255).astype(np.uint8), Path(dbg_dir) / f"{name}_mask.jpg", quality=85)
    # grano a resolucion completa (sobre la imagen limpia ya retocada: uniforme en piel y fondo)
    if gf is not None:
        t = time.time()
        apply_grain(clean, gf, grain * P["g_global"] * (1 + 0.08 * min(max(ev - 3.0, 0.0), 2.0)))
        timings["grano"] = timings.get("grano", 0) + time.time() - t
    del gf, dbg
    out = clean
    # dither TPDF y cuantizacion
    t = time.time()
    tp = (rng.random((H, W, 3), dtype=F32) - rng.random((H, W, 3), dtype=F32))
    o8 = np.clip(np.rint(out + tp), 0, 255).astype(np.uint8)
    del tp
    save_jpeg(o8, out_path)
    if save_tiff16:
        o16 = np.clip(np.rint(out * 257.0), 0, 65535).astype(np.uint16)
        cv2.imwrite(str(out_path.with_suffix(".tif")), o16[..., ::-1])
        del o16
    del out, clean
    timings["guardar"] = time.time() - t
    # metricas
    sub = o8[::2, ::2]
    clip_any = float(np.mean(np.any(sub >= 254, axis=2)) * 100)
    lin = srgb_decode(sub.reshape(-1, 3).astype(F32) / 255)
    labm = lin_to_oklab(lin)
    meanC = float(np.mean(np.hypot(labm[:, 1], labm[:, 2])))
    hsv = cv2.cvtColor(sub, cv2.COLOR_RGB2HSV)
    luma8 = sub.astype(F32) @ YW
    res = dict(file=name, ev=round(ev, 3), ev_guard=round(ev_guard, 3), p998_scene=round(p998, 3),
               clip_any_pct=round(clip_any, 4), base_clip_any_pct=round(base_clip, 4),
               mean_okC=round(meanC, 4), base_mean_okC=round(base_C, 4),
               mean_hsv_sat=round(float(hsv[..., 1].mean() / 255), 4),
               luma_p0_5=round(float(np.percentile(luma8, 0.5)), 1),
               luma_p99_9=round(float(np.percentile(luma8, 99.9)), 1),
               finish=finfo, levels=timings.pop("_niveles", None), size=[W, H], seed=seed,
               seconds=round(time.time() - t0, 1),
               timings={k: round(v, 1) for k, v in timings.items()})
    if crops_dir:
        res["crops"] = save_crops(o8, labm.reshape(sub.shape), name, Path(crops_dir), faces, skin_small)
    return res


_ICC = None


def srgb_icc() -> bytes:
    """sRGB ICC profile with the creation date zeroed (byte-identical files for identical pixels)."""
    global _ICC
    if _ICC is None:
        from PIL import ImageCms
        b = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
        b[24:36] = bytes(12)                       # ICC header dateTimeNumber
        _ICC = bytes(b)
    return _ICC


def save_jpeg(o8, path, quality=97):
    from PIL import Image
    Image.fromarray(o8).save(str(path), "JPEG", quality=quality, subsampling=0, optimize=True, icc_profile=srgb_icc())


def save_crops(o8, lab_half, name, crops_dir, faces=(), skin_small=None):
    """Recortes 1:1 de 1200x1200: piel, cielo/altas, medio plano. lab_half es OkLab a 1/2 resolucion."""
    crops_dir.mkdir(parents=True, exist_ok=True)
    H, W, _ = o8.shape
    S = 1200
    f = 16  # analisis a 1/16 de resolucion completa (lab_half esta a 1/2)
    lab = cv2.resize(lab_half, (W // f, H // f), interpolation=cv2.INTER_AREA)
    L = lab[..., 0]; C = np.hypot(lab[..., 1], lab[..., 2]); h = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
    win = S // f
    def best(score):
        sc = cv2.boxFilter(score.astype(F32), -1, (win, win), normalize=True, borderType=cv2.BORDER_CONSTANT)
        m = win // 2
        sc[:m, :] = -1e9; sc[-m:, :] = -1e9; sc[:, :m] = -1e9; sc[:, -m:] = -1e9
        y, x = np.unravel_index(np.argmax(sc), sc.shape)
        y0 = int(np.clip(y * f - S // 2, 0, H - S)); x0 = int(np.clip(x * f - S // 2, 0, W - S))
        return y0, x0
    skin = skin_mask_raw(L, C, h) * smoothstep(0.45, 0.6, L)
    if skin_small is not None and float(skin_small.max()) > 0.2:
        skin = cv2.resize(skin_small, (L.shape[1], L.shape[0]), interpolation=cv2.INTER_LINEAR)
    sky = smoothstep(0.75, 0.95, L)
    gx = np.abs(cv2.Sobel(L, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(L, cv2.CV_32F, 0, 1))
    flat = np.exp(-gx / 0.02) * np.exp(-0.5 * ((L - 0.55) / 0.12) ** 2)
    out = {}
    for area, score in (("piel", skin), ("altas", sky), ("medios", flat)):
        y0, x0 = best(score)
        if area == "piel" and faces and (skin_small is None or float(skin_small.max()) <= 0.2):
            x, y, w, hh = max(faces, key=lambda b: b[2])
            y0 = int(np.clip(y + hh / 2 - S // 2, 0, H - S)); x0 = int(np.clip(x + w / 2 - S // 2, 0, W - S))
        crop = o8[y0:y0 + S, x0:x0 + S]
        p = crops_dir / f"{name}_{area}.jpg"
        save_jpeg(np.ascontiguousarray(crop), p)
        out[area] = [x0, y0]
    return out
