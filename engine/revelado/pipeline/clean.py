# SPDX-License-Identifier: GPL-3.0-or-later
"""clean.py - etapa de limpieza de la BASE V2 (despues de RawTherapee, antes de la distorsion).

Trabaja sobre la imagen lineal Rec.2020 EXPUESTA (base * 2**ev) en OkLab y devuelve la base sin exponer:
  1. Ruido estimado en la propia imagen (MAD del paso-alto en zonas planas) -> dosifica todo lo demas.
  2. Defringe (CA axial/longitudinal morada y verde): solo en pixeles junto a un borde de alto contraste con
     una alta luz al lado, con tono morado/violeta o verde, y solo la DESVIACION de color respecto al color de
     referencia del entorno (calculado excluyendo los candidatos). Los objetos realmente morados/verdes
     tienen referencia morada/verde -> desviacion ~0 -> protegidos. Nunca aumenta la croma.
  3. Croma: mediana 3/5 (motas) + filtro guiado en dos escalas con guia [L, a', b'] (a',b' = croma ya
     pre-limpiada): elimina motas y manchas de baja frecuencia sin sangrar color a traves de bordes.
  4. Luma: NL-means sobre L (OkLab) solo si el ruido medido supera el objetivo, mezclado para llegar al
     objetivo (no plastifica: conserva parte de la textura original).
L (luminancia) no se toca salvo en el paso 4; a/b solo en 2-3.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from .oklab import oklab, oklab_inv

LUMA_W = np.array([0.2627, 0.6780, 0.0593], np.float32)


def _smoothstep(x, e0, e1):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def _tile_sigma(ch, ys, xs, tile):
    hp = ch - cv2.blur(ch, (3, 3))
    vals = []
    for ty, tx in zip(ys[:400], xs[:400]):
        p = hp[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile]
        vals.append(np.median(np.abs(p - np.median(p))) * 1.4826 / 0.9428)  # 0.9428: (1-1/9) corr. varianza
    return round(float(np.median(vals)) * 100, 4) if vals else 0.0


def estimate_noise(lab: np.ndarray, tile: int = 64) -> dict:
    """sigma (x100, unidades OkLab) de L, a, b: MAD del residuo (x - media 3x3) en las teselas mas planas.
    Ademas L_shadow (L 0.10-0.40) y L_mid (0.40-0.90) para dosificar la NR de luma por tono."""
    h, w = lab.shape[:2]
    sig = {}
    L = lab[..., 0]
    Lb = cv2.GaussianBlur(L, (0, 0), 3)
    gx = cv2.Sobel(Lb, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(Lb, cv2.CV_32F, 0, 1)
    g = cv2.resize(np.hypot(gx, gy), (w // tile, h // tile), interpolation=cv2.INTER_AREA)
    Lm = cv2.resize(L, (w // tile, h // tile), interpolation=cv2.INTER_AREA)

    def pick(lo, hi):
        ok = (Lm > lo) & (Lm < hi)
        if ok.sum() < 8:
            return None
        thr = np.percentile(g[ok], 20)
        return np.nonzero(ok & (g <= thr))
    sel = pick(0.12, 0.9) or np.nonzero(g <= np.percentile(g, 15))
    for c, name in enumerate("Lab"):
        sig[name] = _tile_sigma(lab[..., c], sel[0], sel[1], tile)
    sh = pick(0.10, 0.40)
    md = pick(0.40, 0.90)
    sig["L_shadow"] = _tile_sigma(L, sh[0], sh[1], tile) if sh else sig["L"]
    sig["L_mid"] = _tile_sigma(L, md[0], md[1], tile) if md else sig["L"]
    sig["tiles"] = int(len(sel[0]))
    return sig


def defringe(lab: np.ndarray, Y: np.ndarray, strength: float = 1.0, radius: int = 7) -> tuple[np.ndarray, dict]:
    """Quita franjas moradas/verdes junto a altas luces. lab se modifica en sitio (solo a,b)."""
    a, b = lab[..., 1], lab[..., 2]
    C = np.hypot(a, b)
    hue = np.degrees(np.arctan2(b, a)) % 360
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    bright = cv2.dilate(Y, k)
    # proximidad a alta luz (expuesto: 0.18 = gris medio) y contraste local fuerte
    w_hl = _smoothstep(bright, 0.45, 1.2)
    w_ct = _smoothstep(bright / (np.maximum(Y, 0) + 1e-3), 1.6, 4.0)
    # ventana de tono: morado-violeta-magenta (OkLab ~ 265..355 deg) y verde/amarillo-verde (~110..170)
    dp = np.abs(((hue - 310 + 180) % 360) - 180)
    dg = np.abs(((hue - 140 + 180) % 360) - 180)
    w_purple = 1 - _smoothstep(dp, 35, 55)
    w_green = 1 - _smoothstep(dg, 22, 40)
    w_hue = np.maximum(w_purple, 0.8 * w_green)
    w_chroma = _smoothstep(C, 0.008, 0.025)
    cand = w_hl * w_ct * w_hue * w_chroma
    del w_hue
    # color de referencia del entorno excluyendo candidatos (convolucion normalizada)
    sig = radius * 1.6
    wref = (1 - _smoothstep(cand, 0.05, 0.3)).astype(np.float32)
    den = cv2.GaussianBlur(wref, (0, 0), sig) + 1e-6
    ra = cv2.GaussianBlur(a * wref, (0, 0), sig) / den
    rb = cv2.GaussianBlur(b * wref, (0, 0), sig) / den
    # donde no hay ninguna referencia valida -> neutro
    trust = _smoothstep(den, 0.05, 0.3)
    ra *= trust
    rb *= trust
    # nunca subir croma: escalar la referencia a como mucho la croma actual
    rc = np.hypot(ra, rb) + 1e-6
    sc = np.minimum(1.0, C / rc)
    ra *= sc
    rb *= sc
    # desviacion de tono respecto a la referencia: si la referencia ya es morada/verde (objeto real), poca accion
    dev = np.hypot(a - ra, b - rb)
    w_dev = _smoothstep(dev, 0.006, 0.02)
    w = np.clip(cand * w_dev * strength, 0, 1)
    w = cv2.GaussianBlur(w, (0, 0), 0.8)
    na = a + w * (ra - a)
    nb = b + w * (rb - b)
    info = {"defringe_px_pct": round(float((w > 0.25).mean() * 100), 4),
            "defringe_mean_dC": round(float((C - np.hypot(na, nb))[w > 0.25].mean() * 100) if (w > 0.25).any() else 0.0, 3)}
    lab[..., 1] = na
    lab[..., 2] = nb
    return lab, info


def chroma_denoise(lab: np.ndarray, sigma_c: float, sigma_L: float, level: float) -> tuple[np.ndarray, dict]:
    """sigma_* en unidades x100 OkLab. level 0..1 (de ISO/empuje): fija radios.
    - mediana 3/5 sobre a,b (motas impulsivas)
    - filtro guiado con guia L SOLA (preserva el color correlado con la luminancia: mechones, tramas)
    - bordes de color isoluminantes (p.ej. vaquero azul junto a abrigo rosado de igual brillo): zona dilatada
      donde se usa un radio pequeno para no sangrar color
    - escala gruesa (manchas de baja frecuencia) a 1/4 de resolucion con bilateral conjunto en L,a,b
      (tampoco cruza bordes de color)."""
    L = np.ascontiguousarray(lab[..., 0])
    a = np.ascontiguousarray(lab[..., 1])
    b = np.ascontiguousarray(lab[..., 2])
    km = 5 if (sigma_c > 0.12 or level > 0.5) else 3
    am = cv2.medianBlur(a, km)
    bm = cv2.medianBlur(b, km)
    src = np.dstack([am, bm])
    sL = max(sigma_L, 0.3) / 100.0
    eps1 = float(((2.5 + 3.0 * level) * sL) ** 2 + 2e-6)
    r1 = int(round(4 + 8 * level))
    out = cv2.ximgproc.guidedFilter(L, src, r1, eps1)
    small_r = cv2.ximgproc.guidedFilter(L, src, 2, eps1)
    # bordes de color: umbral relativo al suelo de ruido de la propia imagen
    ap = cv2.GaussianBlur(am, (0, 0), 2)
    bp = cv2.GaussianBlur(bm, (0, 0), 2)
    ce = np.hypot(cv2.Sobel(ap, cv2.CV_32F, 1, 0), cv2.Sobel(ap, cv2.CV_32F, 0, 1)) + \
        np.hypot(cv2.Sobel(bp, cv2.CV_32F, 1, 0), cv2.Sobel(bp, cv2.CV_32F, 0, 1))
    del ap, bp
    floor = float(np.median(ce[::4, ::4]))
    t1 = max(0.010, 4.0 * floor)
    wk = _smoothstep(ce, t1, 2.5 * t1)
    del ce
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r1 + 1, 2 * r1 + 1))
    wk = cv2.GaussianBlur(cv2.dilate(wk, k), (0, 0), r1 / 2.0)[..., None]
    out = out * (1 - wk) + small_r * wk
    del small_r
    coarse = level > 0.2
    if coarse:
        h, w = L.shape
        f = 4
        sw, sh = w // f, h // f
        small = cv2.resize(out, (sw, sh), interpolation=cv2.INTER_AREA)
        Ls = cv2.resize(L, (sw, sh), interpolation=cv2.INTER_AREA)
        joint = np.dstack([Ls, small[..., 0] * 2.0, small[..., 1] * 2.0]).astype(np.float32)
        s3 = np.dstack([small, np.zeros((sh, sw), np.float32)]).astype(np.float32)
        r2 = int(round(3 + 5 * level))
        sc = float(0.012 + 3 * sL / f)
        lo = cv2.ximgproc.jointBilateralFilter(joint, s3, 2 * r2 + 1, sc, r2)[..., :2]
        corr = cv2.resize(lo - small, (w, h), interpolation=cv2.INTER_LINEAR)
        mix = float(np.clip((level - 0.2) / 0.5, 0, 1))
        out += mix * corr
        del corr, small, lo, joint, s3
    lab[..., 1] = out[..., 0]
    lab[..., 2] = out[..., 1]
    return lab, {"median": km, "r1": r1, "eps1": round(eps1, 7), "coarse": bool(coarse), "edge_t1": round(t1, 4),
                 "edge_protect_pct": round(float((wk > 0.5).mean() * 100), 3)}


def despeckle(lab: np.ndarray, sigma_L: float, k: float = 5.0) -> tuple[np.ndarray, dict]:
    """Puntos aislados (pimienta/sal) en L: si el pixel se sale del rango min..max de sus 8 vecinos por mas de
    k*sigma se sustituye por la mediana 3x3. No toca lineas ni texturas (siempre tienen vecinos parecidos)."""
    L = np.ascontiguousarray(lab[..., 0])
    ker = np.ones((3, 3), np.uint8)
    ker[1, 1] = 0
    mx = cv2.dilate(L, ker)
    mn = cv2.erode(L, ker)
    t = k * max(sigma_L, 0.2) / 100.0
    # solo en zonas lisas: la desviacion de los vecinos (5x5) debe ser pequena, si no es textura real
    # (trama de vaquero, brillos en el agua, pestanas) y no se toca
    mu = cv2.blur(L, (5, 5))
    sd = np.sqrt(np.maximum(cv2.blur(L * L, (5, 5)) - mu * mu, 0))
    bad = ((L > mx + t) | (L < mn - t)) & (sd < 0.6 * t + 0.5 * np.abs(L - mu))
    med = cv2.medianBlur(L, 3)
    lab[..., 0] = np.where(bad, med, L)
    return lab, {"despeckle_px": int(bad.sum())}


def luma_denoise(lab: np.ndarray, nz: dict, target: float = 0.45, max_h: float = 4.0) -> tuple[np.ndarray, dict]:
    """NL-means sobre L solo si el ruido medido (x100) supera el objetivo. Se calcula con h para el ruido de
    sombras y se mezcla por pixel conservando una fraccion del residuo m(L) = objetivo / sigma(L): sombras mas
    limpias, medios/luces casi intactos (no plastifica)."""
    s_sh, s_md = nz["L_shadow"], nz["L_mid"]
    smax = max(s_sh, s_md)
    if smax <= target * 1.1:
        return lab, {"luma_nlm": False}
    L = np.ascontiguousarray(lab[..., 0])
    lo, hi = -0.05, 1.6
    scale = 65535.0 / (hi - lo)
    u = np.clip((L - lo) * scale, 0, 65535).astype(np.uint16)
    h = float(min(max_h, 1.15 * smax)) / 100.0 * scale
    den = cv2.fastNlMeansDenoising(u, h=[h], templateWindowSize=5, searchWindowSize=17, normType=cv2.NORM_L1)
    Ld = den.astype(np.float32) / scale + lo
    del den, u
    Lsm = cv2.GaussianBlur(L, (0, 0), 4)
    sig_px = s_sh + (s_md - s_sh) * _smoothstep(Lsm, 0.25, 0.55)
    m = np.clip(target / np.maximum(sig_px, 1e-3), 0.3, 1.0).astype(np.float32)
    lab[..., 0] = Ld + m * (L - Ld)
    return lab, {"luma_nlm": True, "h_x100": round(h / scale * 100, 3),
                 "keep_residual_shadow": round(float(min(1, max(0.3, target / s_sh))), 3),
                 "keep_residual_mid": round(float(min(1, max(0.3, target / s_md))), 3)}


def clean(img: np.ndarray, ev: float, level: float, do_defringe: bool = True,
          luma_target: float = 0.45) -> tuple[np.ndarray, dict]:
    """img: base lineal Rec.2020 sin exponer (float32 HxWx3). Devuelve (img_limpia, info)."""
    t0 = time.time()
    gain = np.float32(2.0 ** (ev if ev is not None else 2.0))
    x = img * gain
    lab = oklab(x)
    Y = x @ LUMA_W
    del x
    info = {"level": round(level, 3)}
    nz = estimate_noise(lab)
    info["noise_in_x100"] = nz
    if do_defringe:
        lab, di = defringe(lab, Y)
        info.update(di)
    del Y
    # luma primero: la guia L limpia evita que el filtro guiado "copie" ruido de croma correlado con L
    if level >= 0.5:   # solo con mucho ruido (pimienta del ISO alto + empuje); a ISO bajo seria textura real
        lab, sp = despeckle(lab, nz["L_shadow"])
        info.update(sp)
    else:
        info["despeckle_px"] = 0
    lab, li = luma_denoise(lab, nz, target=luma_target)
    info["luma"] = li
    sc = max(nz["a"], nz["b"])
    sL_after = min(nz["L"], luma_target) if li.get("luma_nlm") else nz["L"]
    lab, ci = chroma_denoise(lab, sc, sL_after, level)
    info["chroma"] = ci
    out = oklab_inv(lab)
    del lab
    out /= gain
    info["seconds"] = round(time.time() - t0, 2)
    return out, info
