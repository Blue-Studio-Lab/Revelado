# SPDX-License-Identifier: GPL-3.0-or-later
"""ENCUADRE (reencuadre + enderezado) como paso de tratamiento NO destructivo (antes encuadre.py).

Que hace, por foto:
  1) ENDEREZAR solo inclinaciones reales (horizonte, lineas de agua/muelle, verticales de edificios) con LSD (OpenCV)
     sobre la imagen web 8 bit, excluyendo a las personas. Se corrige entre 0.3 y 5 grados y solo con evidencia
     consistente; >5 grados o lineas contradictorias = intencional -> no se toca.
  2) COMPOSICION: sujeto principal (ojos de las caras; la pareja como grupo; anillo en detalles) sobre tercios o
     lineas phi; aire hacia la mirada; horizonte en un tercio; peso visual equilibrado.
  3) NUNCA cortar cabezas, manos, pies por el tobillo ni articulaciones (con pose de cuerpo/mano cuando existe).
  4) Mantiene la proporcion de camara. Conservador: >= 80 % del area; si la composicion ya es buena, sin recorte.
  5) Parametros no destructivos {angle_deg, x, y, w, h (px del render completo), keep_pct, reason}.

Deteccion: ayudante Vision de macOS (caras, pose de cuerpo y mano, saliencia, mate de personas) cuando existe; en
cualquier otro sistema, solo caras (YuNet/Haar) + saliencia espectral de OpenCV: sin articulaciones ni manos.

API: analyze(img_web_bgr, web_path=..., full_size=(W, H), overrides=None) -> params
     apply(full_jpg, params, out_jpg, web_src=..., web_out=..., raw_path=...)   (JPG final, q97 4:4:4)
     apply_tiff16(full_tif, params, out_tif)                                   (MISMO giro+recorte en 16 bit)
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import faces as faces_mod
from ..tools import find_tool

PHI = 0.381966

# ------------------------------------------------------------------------------------------------
# Parametros globales
# ------------------------------------------------------------------------------------------------
P = dict(
    tilt_min=0.3,            # grados: por debajo no se corrige (imperceptible y cuesta area)
    tilt_max=5.0,            # grados: por encima se asume intencional (holandes)
    tilt_mad_max=0.45,       # dispersion maxima de las lineas que votan
    min_keep=0.80,           # area minima conservada (salvo override)
    improve_min=0.030,       # mejora minima de coste para preferir un recorte a "sin recorte"
    w_area=0.45,             # coste por area perdida
    w_subj_x=1.0, w_subj_y=1.0,
    w_horizon=0.6,
    w_lead=0.6,
    w_balance=0.25,
    w_headroom=1.0,
    w_joint_edge=0.12,       # coste si una articulacion queda pegada a un borde
    joint_tol=0.022,
    joint_tol_leg=0.055,     # rodillas, tobillos y cadera: el borde debe quedar claramente a media pierna/muslo         # fraccion de la altura/anchura: borde prohibido alrededor de una articulacion
    protect_margin=0.012,    # margen minimo entre zona protegida y borde (fraccion de la dimension)
)

# Decisiones de editor por foto: se pasan en analyze(..., overrides=...). Claves:
#   mode: "auto" | "none" (sin recorte)   no_rotate: True   angle: forzar grados
#   centred_ok: bool  horizon: False | y_norm   min_keep: float   x_targets / y_targets: listas
#   subject: "faces" | "group" | "ring"   note: texto para el reason   expect: decision esperada para usar 'note'


# ------------------------------------------------------------------------------------------------
# Deteccion (Vision de macOS: caras, pose de cuerpo y mano, saliencia; mate de personas)
# ------------------------------------------------------------------------------------------------
def run_vision(web_path: Path, img_rgb: np.ndarray | None = None) -> dict:
    d = faces_mod.framing_analysis(str(web_path))
    if d is None:
        if img_rgb is None:
            img_rgb = cv2.imread(str(web_path))[..., ::-1]
        d = faces_mod.framing_fallback(img_rgb)
    return d


def person_mask(web_path: Path, shape, img_rgb: np.ndarray | None = None) -> np.ndarray:
    """Mate de personas 0..1 al tamano de la web (Vision accurate). Sin Vision: ceros."""
    H, W = shape[:2]
    if img_rgb is None:
        b = cv2.imread(str(web_path))
        img_rgb = None if b is None else b[..., ::-1]
    m = faces_mod.person_matte(np.ascontiguousarray(img_rgb)) if img_rgb is not None else None
    if m is None:
        return np.zeros((H, W), np.float32)
    return cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32)


BODY_JOINTS = {  # nombre Vision -> tipo
    "left_foot_joint": "ankle", "right_foot_joint": "ankle",
    "left_leg_joint": "knee", "right_leg_joint": "knee",
    "left_upLeg_joint": "hip", "right_upLeg_joint": "hip",
    "left_hand_joint": "wrist", "right_hand_joint": "wrist",
    "left_forearm_joint": "elbow", "right_forearm_joint": "elbow",
}


def build_subjects(vis: dict, img: np.ndarray, mask: np.ndarray) -> dict:
    """Caras, cabezas protegidas, manos, articulaciones, anillo. Todo en px de la web."""
    H, W = img.shape[:2]
    faces = []
    for f in vis.get("faces", []):
        x, y, w, h = f["box"]
        if f.get("conf", 1) < 0.5 or w * W < 18:
            continue
        eyes = f.get("eyes") or []
        if len(eyes) >= 1:
            ex = float(np.mean([e[0] for e in eyes])) * W
            ey = float(np.mean([e[1] for e in eyes])) * H
        else:
            ex, ey = (x + w / 2) * W, (y + 0.42 * h) * H
        faces.append(dict(box=[x * W, y * H, w * W, h * H], eye=[ex, ey], yaw=float(f.get("yaw", 0) or 0),
                          src="vision"))
    # Respaldo: cabezas a partir de la pose de cuerpo (caras de perfil o pequenas)
    for b in vis.get("bodies", []):
        pts = [b[k] for k in ("nose_joint", "left_eye_joint", "right_eye_joint", "left_ear_joint", "right_ear_joint",
                              "head_joint") if k in b and b[k][2] > 0.25]
        if len(pts) < 2:
            continue
        cx = float(np.mean([p[0] for p in pts])) * W
        cy = float(np.mean([p[1] for p in pts])) * H
        if any(fc["box"][0] - 0.2 * fc["box"][2] <= cx <= fc["box"][0] + 1.2 * fc["box"][2] and
               fc["box"][1] - 0.2 * fc["box"][3] <= cy <= fc["box"][1] + 1.2 * fc["box"][3] for fc in faces):
            continue
        sh = [b[k] for k in ("left_shoulder_1_joint", "right_shoulder_1_joint") if k in b]
        if len(sh) == 2:
            size = max(abs(sh[0][0] - sh[1][0]) * W, abs(sh[0][1] - sh[1][1]) * H) * 0.62
        else:
            xs = [p[0] * W for p in pts]
            size = max(max(xs) - min(xs), 0.02 * max(H, W)) * 1.8
        size = max(size, 0.015 * max(H, W))
        faces.append(dict(box=[cx - size / 2, cy - size * 0.45, size, size * 1.2], eye=[cx, cy], yaw=0.0,
                          src="pose"))
    if not faces:
        hp = faces_mod.assets.model_path("haar_frontal_default")
        if hp is not None:
            g = cv2.cvtColor(cv2.resize(img, (W // 2, H // 2)), cv2.COLOR_BGR2GRAY)
            for (x, y, w, h) in cv2.CascadeClassifier(str(hp)).detectMultiScale(g, 1.1, 6, minSize=(30, 30)):
                faces.append(dict(box=[2 * x, 2 * y, 2 * w, 2 * h], eye=[2 * x + w, 2 * y + 0.8 * h], yaw=0.0,
                                  src="haar"))

    # Sujetos principales: las caras de fondo (transeuntes pequenos) no cuentan ni se protegen
    background = []
    if faces:
        big = max(f["box"][2] for f in faces)
        background = [f for f in faces if f["box"][2] < 0.45 * big]
        faces = [f for f in faces if f["box"][2] >= 0.45 * big]
    main_w = max([f["box"][2] for f in faces], default=0.0)

    heads = []
    for f in faces:
        x, y, w, h = f["box"]
        heads.append([x - 0.30 * w, y - 0.70 * h, x + 1.30 * w, y + 1.20 * h])

    hands, ring = [], None
    for hd in vis.get("hands", []):
        pts = np.array([[v[0] * W, v[1] * H] for v in hd.values() if v[2] > 0.3])
        if len(pts) < 5:
            continue
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        if main_w and math.hypot(x1 - x0, y1 - y0) < 0.4 * main_w:
            continue                                    # mano de un transeunte del fondo
        m = 0.18 * max(x1 - x0, y1 - y0) + 0.01 * max(H, W)
        hands.append([x0 - m, y0 - m, x1 + m, y1 + m])
        if "VNHLKRMCP" in hd and "VNHLKRPIP" in hd:
            a, b = np.array(hd["VNHLKRMCP"][:2]), np.array(hd["VNHLKRPIP"][:2])
            r = (0.6 * a + 0.4 * b) * [W, H]
            area = (x1 - x0) * (y1 - y0) / (W * H)
            if ring is None or area > ring[2]:
                ring = [float(r[0]), float(r[1]), float(area)]

    joints = []
    def _main_body(b):
        hp = [b[k] for k in ("nose_joint", "neck_1_joint", "head_joint", "left_eye_joint", "right_eye_joint",
                             "left_ear_joint", "right_ear_joint") if k in b and b[k][2] > 0.2]
        if hp:
            for hx, hy, _ in hp:
                for hb in heads:
                    cw, ch = hb[2] - hb[0], hb[3] - hb[1]
                    if hb[0] - 0.5 * cw <= hx * W <= hb[2] + 0.5 * cw and hb[1] - 0.5 * ch <= hy * H <= hb[3] + ch:
                        return True
            return not heads
        ys = [v[1] for v in b.values() if v[2] > 0.3]
        return bool(ys) and (max(ys) - min(ys)) > 0.45
    for b in vis.get("bodies", []):
        if not _main_body(b):
            continue
        for k, t in BODY_JOINTS.items():
            if k in b and b[k][2] >= 0.3:
                joints.append(dict(type=t, x=b[k][0] * W, y=b[k][1] * H))
        for k in ("left_hand_joint", "right_hand_joint"):
            if k in b and b[k][2] >= 0.3:
                s = 0.035 * max(H, W)
                hands.append([b[k][0] * W - s, b[k][1] * H - s, b[k][0] * W + s, b[k][1] * H + s])
    feet = []
    for j in joints:
        if j["type"] == "ankle":
            feet.append([j["x"] - 0.03 * W, j["y"] - 0.01 * H, j["x"] + 0.03 * W, j["y"] + 0.05 * H])

    sal = [[s[0] * W, s[1] * H, (s[0] + s[2]) * W, (s[1] + s[3]) * H, s[4]] for s in vis.get("salient", [])]
    return dict(faces=faces, heads=heads, hands=hands, feet=feet, joints=joints, ring=ring, salient=sal,
                background=background)


# ------------------------------------------------------------------------------------------------
# Inclinacion (LSD)
# ------------------------------------------------------------------------------------------------
def _wmedian(v, w):
    o = np.argsort(v)
    v, w = np.asarray(v)[o], np.asarray(w)[o]
    c = np.cumsum(w)
    return float(v[np.searchsorted(c, c[-1] / 2)])


def detect_tilt(img: np.ndarray, mask: np.ndarray) -> dict:
    """Estimacion del giro de camara. Convencion: angle_deg > 0 = girar la imagen en sentido antihorario
    (cv2.getRotationMatrix2D) para nivelar. Devuelve tambien la altura del horizonte (fraccion) si lo hay."""
    H, W = img.shape[:2]
    f = 1280 / max(H, W)
    sm = cv2.resize(img, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA)
    h, w = sm.shape[:2]
    g = cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(g)[0]
    res = dict(h_angle=None, h_len=0.0, h_mad=None, v_angle=None, v_len=0.0, v_mad=None, horizon_y=None,
               h_segs=[], v_segs=[])
    if lines is None:
        return res
    pm = cv2.dilate((cv2.resize(mask, (w, h)) > 0.35).astype(np.uint8), np.ones((15, 15), np.uint8))
    hs, vs = [], []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        if ln < 0.05 * max(h, w):
            continue
        # fuera de las personas (los cuerpos no son referencia de nivel)
        n = max(int(ln / 4), 2)
        xs = np.clip(np.linspace(x1, x2, n).astype(int), 0, w - 1)
        ys = np.clip(np.linspace(y1, y2, n).astype(int), 0, h - 1)
        if pm[ys, xs].mean() > 0.2:
            continue
        a = math.degrees(math.atan2(dy, dx))
        a = (a + 90) % 180 - 90                     # (-90, 90]
        if abs(a) < 8 and ln > 0.07 * w:
            hs.append((a, ln, (y1 + y2) / 2 / h, (x1 + x2) / 2 / w, (x1 / w, y1 / h, x2 / w, y2 / h)))
        elif abs(abs(a) - 90) < 8 and ln > 0.06 * h:
            dev = a - 90 if a > 0 else a + 90       # desviacion respecto a la vertical
            # vertical inclinada: borde superior hacia la derecha -> dev; para nivelar giro = -(-dev) ...
            vs.append((dev, ln, (x1 + x2) / 2 / w, (x1 / w, y1 / h, x2 / w, y2 / h)))
    if hs:
        a_all = np.array([s[0] for s in hs]); ln_all = np.array([s[1] for s in hs])
        ys_all = np.array([s[2] for s in hs])
        # 1) HORIZONTE LEJANO (orilla del fondo, linea agua/tierra): fila con mas longitud acumulada en los 2/3
        #    superiores. Es la unica referencia horizontal fiable: las tablas de un muelle o los bordillos estan
        #    en primer plano y su angulo depende de la perspectiva (guinada de camara), no del nivel.
        hist = np.zeros(200)
        for y, l in zip(ys_all, ln_all):
            if y < 0.66:
                hist[min(int(y * 200), 199)] += l
        hist = np.convolve(hist, np.ones(5), "same")
        k = int(np.argmax(hist))
        sel = None
        if hist[k] > 0.35 * w:
            res["horizon_y"] = (k + 0.5) / 200
            sel = np.abs(ys_all - res["horizon_y"]) < 0.03
            res["h_src"] = "horizonte"
        if sel is None or ln_all[sel].sum() < 0.30 * w:
            # sin horizonte: todas las horizontales, pero las del tercio inferior (primer plano) pesan poco
            sel = np.ones(len(hs), bool)
            res["h_src"] = "lineas horizontales"
        a, ln = a_all[sel], ln_all[sel] * np.where(ys_all[sel] > 0.66, 0.25, 1.0)
        med = _wmedian(a, ln)
        keep = np.abs(a - med) < 1.5
        a, ln2 = a[keep], ln[keep]
        res["h_angle"] = _wmedian(a, ln2)
        res["h_len"] = float(ln2.sum() / w)
        res["h_mad"] = float(_wmedian(np.abs(a - res["h_angle"]), ln2))
        # acuerdo: fraccion de la longitud seleccionada (sin filtrar) que coincide con la estimacion (+-0.4 grados)
        a_s, l_s = a_all[sel], ln_all[sel]
        res["h_agree"] = float(l_s[np.abs(a_s - res["h_angle"]) <= 0.4].sum() / max(l_s.sum(), 1e-6))
        # izquierda vs derecha: si discrepan, la linea esta curvada (orilla que se acerca, distorsion de la optica)
        # y no es una referencia de nivel fiable
        xc_s = np.array([s[3] for s in hs])[sel]
        lft, rgt = xc_s < 0.5, xc_s >= 0.5
        if lft.any() and rgt.any():
            res["h_lr"] = [round(_wmedian(a_s[lft], l_s[lft]), 2), round(_wmedian(a_s[rgt], l_s[rgt]), 2)]
        idx = np.flatnonzero(sel)[keep]
        res["h_segs"] = [hs[i][4] for i in idx]
    if vs:
        d = np.array([s[0] for s in vs]); ln = np.array([s[1] for s in vs]); xc = np.array([s[2] for s in vs])
        # las verticales lejos del centro sufren de convergencia (keystone): pesan menos
        wgt = ln * np.clip(1.2 - 1.6 * np.abs(xc - 0.5), 0.15, 1.0)
        med = _wmedian(d, wgt)
        keep = np.abs(d - med) < 1.5
        res["v_angle"] = _wmedian(d[keep], wgt[keep])
        res["v_len"] = float(ln[keep].sum() / h)
        res["v_mad"] = float(_wmedian(np.abs(d[keep] - res["v_angle"]), wgt[keep]))
        res["v_agree"] = float(wgt[np.abs(d - res["v_angle"]) <= 0.4].sum() / max(wgt.sum(), 1e-6))
        res["v_segs"] = [s[3] for s, k in zip(vs, keep) if k]
    return res


def decide_angle(t: dict, ov: dict) -> tuple[float, str]:
    if "angle" in ov:
        return float(ov["angle"]), f"giro fijado por editor {ov['angle']:+.2f} grados"
    if ov.get("no_rotate"):
        return 0.0, "sin giro (decision de editor)"
    # Lineas horizontales: un segmento que baja hacia la derecha tiene angulo > 0 en coordenadas de imagen;
    # nivelar = girar antihorario ese mismo angulo (cv2: positivo = antihorario).
    cand = []
    need = 0.3 if t.get("h_src") == "horizonte" else 0.8
    if t["h_angle"] is not None and t["h_len"] >= need and t["h_mad"] <= P["tilt_mad_max"] and \
            t.get("h_agree", 0) >= 0.6 and not ("h_lr" in t and abs(t["h_lr"][0] - t["h_lr"][1]) > 0.7):
        cand.append((t.get("h_src", "horizontales"), t["h_angle"], t["h_len"] * (3 if t.get("h_src") == "horizonte" else 1)))
    # Verticales: dev>0 = la linea se inclina de forma que su extremo inferior va a la derecha... se mide con el
    # mismo convenio que las horizontales (angulo de la linea respecto a su eje): nivelar = girar dev.
    if t["v_angle"] is not None and t["v_len"] >= 1.2 and t["v_mad"] <= P["tilt_mad_max"] and \
            t.get("v_agree", 0) >= 0.6:
        cand.append(("verticales", t["v_angle"], t["v_len"]))
    if not cand:
        if "h_lr" in t and abs(t["h_lr"][0] - t["h_lr"][1]) > 0.7:
            return 0.0, (f"horizonte curvado/ambiguo (izq {t['h_lr'][0]:+.2f}, der {t['h_lr'][1]:+.2f} grados): "
                         f"no se gira")
        return 0.0, "sin referencias de nivel fiables (no se gira)"
    if len(cand) == 2 and abs(cand[0][1] - cand[1][1]) > 0.8:
        # contradiccion: perspectiva de las verticales; manda el horizonte
        cand = cand[:1]
    ang = float(np.average([c[1] for c in cand], weights=[c[2] for c in cand]))
    src = " + ".join(c[0] for c in cand)
    if abs(ang) < P["tilt_min"]:
        return 0.0, f"nivel correcto ({src}: {ang:+.2f} grados, bajo umbral)"
    if abs(ang) > P["tilt_max"]:
        return 0.0, f"inclinacion {ang:+.1f} grados > {P['tilt_max']}: se asume intencional"
    return round(ang, 2), f"enderezado por {src} ({ang:+.2f} grados)"


# ------------------------------------------------------------------------------------------------
# Geometria del giro
# ------------------------------------------------------------------------------------------------
def rot_matrix(W, H, angle):
    return cv2.getRotationMatrix2D((W / 2.0, H / 2.0), angle, 1.0)


def transform_pts(pts, W, H, angle):
    if not angle:
        return np.asarray(pts, float)
    M = rot_matrix(W, H, angle)
    p = np.asarray(pts, float)
    return p @ M[:, :2].T + M[:, 2]


def rect_inside_rotated(x, y, w, h, W, H, angle) -> bool:
    """El rectangulo (en la imagen girada, mismo lienzo) cae entero dentro de la imagen original valida."""
    if not angle:
        return x >= -1e-6 and y >= -1e-6 and x + w <= W + 1e-6 and y + h <= H + 1e-6
    Minv = cv2.invertAffineTransform(rot_matrix(W, H, angle))
    c = np.array([[x, y], [x + w, y], [x, y + h], [x + w, y + h]], float)
    o = c @ Minv[:, :2].T + Minv[:, 2]
    return bool((o[:, 0] >= -0.5).all() and (o[:, 0] <= W + 0.5).all() and (o[:, 1] >= -0.5).all() and
                (o[:, 1] <= H + 0.5).all())


def max_centered_scale(W, H, angle):
    t = math.radians(abs(angle))
    return min(1.0, W / (W * math.cos(t) + H * math.sin(t)), H / (W * math.sin(t) + H * math.cos(t)))


def _box_xform(b, W, H, angle):
    p = transform_pts([[b[0], b[1]], [b[2], b[1]], [b[0], b[3]], [b[2], b[3]]], W, H, angle)
    return [p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()]


# ------------------------------------------------------------------------------------------------
# Composicion
# ------------------------------------------------------------------------------------------------
def _sal_map(img):
    sm = cv2.resize(img, (320, round(320 * img.shape[0] / img.shape[1])), interpolation=cv2.INTER_AREA)
    try:
        s = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, m = s.computeSaliency(sm)
        m = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 3)
    except Exception:
        m = np.ones(sm.shape[:2], np.float32)
    return m / (m.sum() + 1e-9)


def analyze(img_web: np.ndarray, stem: str = "", web_path: Path | None = None, full_size=None,
            overrides: dict | None = None) -> dict:
    """img_web: BGR 8 bit (web 2560). Devuelve params (px del JPG completo) + datos de depuracion en '_dbg'."""
    ov = dict(overrides or {})
    H, W = img_web.shape[:2]
    if full_size is None:
        full_size = (W, H)
    FW, FH = full_size
    rgb = np.ascontiguousarray(img_web[..., ::-1])
    mask = person_mask(web_path, img_web.shape, rgb) if web_path else np.zeros((H, W), np.float32)
    vis = run_vision(web_path, rgb) if web_path else faces_mod.framing_fallback(rgb)
    S = build_subjects(vis, img_web, mask)
    tilt = detect_tilt(img_web, mask)
    angle, ang_reason = decide_angle(tilt, ov)

    # --- todo pasado al lienzo girado (mismo tamano, giro alrededor del centro) ---
    X = lambda b: _box_xform(b, W, H, angle)  # noqa: E731
    heads = [X(b) for b in S["heads"]]
    hands = [X(b) for b in S["hands"]]
    feet = [X(b) for b in S["feet"]]
    joints = [dict(j, x=p[0], y=p[1]) for j, p in
              zip(S["joints"], transform_pts([[j["x"], j["y"]] for j in S["joints"]] or np.zeros((0, 2)), W, H, angle))]
    eyes = transform_pts([f["eye"] for f in S["faces"]] or np.zeros((0, 2)), W, H, angle)
    ring = transform_pts([S["ring"][:2]], W, H, angle)[0] if S["ring"] else None
    sal = _sal_map(img_web)
    if angle:
        sal = cv2.warpAffine(sal, rot_matrix(sal.shape[1], sal.shape[0], angle), (sal.shape[1], sal.shape[0]))
    sal_h, sal_w = sal.shape
    sal_cum = sal.cumsum(0).cumsum(1)
    sal_xc = (sal * np.arange(sal_w)[None, :]).cumsum(0).cumsum(1)
    sal_yc = (sal * np.arange(sal_h)[:, None]).cumsum(0).cumsum(1)

    # --- sujeto principal ---
    mode = ov.get("subject")
    if mode is None:
        if S["ring"] is not None and S["ring"][2] > 0.05:
            mode = "ring"
        elif len(eyes) >= 2:
            mode = "group"
        elif len(eyes) == 1:
            mode = "faces"
        else:
            mode = "salient"
    if mode == "ring" and ring is not None:
        subj = np.array(ring, float)
    elif len(eyes):
        xs = eyes[:, 0]
        subj = np.array([(xs.min() + xs.max()) / 2, float(np.mean(eyes[:, 1]))])
    elif S["salient"]:
        s = max(S["salient"], key=lambda s: (s[2] - s[0]) * (s[3] - s[1]))
        subj = np.array(transform_pts([[(s[0] + s[2]) / 2, (s[1] + s[3]) / 2]], W, H, angle)[0])
    else:
        subj = np.array([W / 2, H / 2])
    head_top = min([b[1] for b in heads], default=None)

    # mirada (yaw de Vision: >0 la cara mira hacia la izquierda de la imagen)
    gaze = 0.0
    if mode == "faces" and len(S["faces"]) == 1:
        yw = S["faces"][0]["yaw"]
        gaze = -1.0 if yw > 0.35 else (1.0 if yw < -0.35 else 0.0)
    centred_ok = ov.get("centred_ok", mode in ("group",) or (mode == "ring"))
    if ov.get("keep_x"):   # conservar la posicion horizontal original del sujeto (solo se corrige la vertical)
        ov["x_targets"] = [float(subj[0] / W)]
    x_t = ov.get("x_targets") or ([1 / 3, PHI, 0.5, 1 - PHI, 2 / 3] if centred_ok else [1 / 3, PHI, 1 - PHI, 2 / 3])
    y_t = ov.get("y_targets") or ([1 / 3, PHI] if mode != "ring" else [1 / 3, PHI, 0.5, 1 - PHI, 2 / 3])
    hor = ov.get("horizon", "auto")
    horizon_y = tilt["horizon_y"] * H if (hor == "auto" and tilt["horizon_y"] is not None) else (
        hor * H if isinstance(hor, (int, float)) and not isinstance(hor, bool) else None)
    if horizon_y is not None:
        horizon_y = float(transform_pts([[W / 2, horizon_y]], W, H, angle)[0][1])
        # horizonte que atraviesa caras = no manda
        if any(b[1] <= horizon_y <= b[3] for b in heads):
            horizon_y = None
    min_keep = ov.get("min_keep", P["min_keep"])

    protect = [(b, "cabeza") for b in heads] + [(b, "mano") for b in hands] + [(b, "pie") for b in feet]

    s_max = max_centered_scale(W, H, angle)

    def cost(x, y, w, h, detail=False):
        u = (subj[0] - x) / w
        v = (subj[1] - y) / h
        c_sx = min(abs(u - t) for t in x_t)
        c_sy = min(abs(v - t) for t in y_t)
        c = P["w_subj_x"] * c_sx + P["w_subj_y"] * c_sy
        c_h = 0.0
        if horizon_y is not None:
            vh = (horizon_y - y) / h
            c_h = P["w_horizon"] * min(abs(vh - 1 / 3), abs(vh - 2 / 3))
        c_l = P["w_lead"] * max(0.0, gaze * (u - 0.5)) if gaze else 0.0
        # peso visual
        fx0, fy0 = int(x / W * sal_w), int(y / H * sal_h)
        fx1, fy1 = min(int((x + w) / W * sal_w), sal_w) - 1, min(int((y + h) / H * sal_h), sal_h) - 1

        def rs(A):
            r = A[fy1, fx1]
            if fx0 > 0: r -= A[fy1, fx0 - 1]
            if fy0 > 0: r -= A[fy0 - 1, fx1]
            if fx0 > 0 and fy0 > 0: r += A[fy0 - 1, fx0 - 1]
            return r
        m = rs(sal_cum) + 1e-9
        bx = (rs(sal_xc) / m - fx0) / max(fx1 - fx0, 1) - 0.5
        by = (rs(sal_yc) / m - fy0) / max(fy1 - fy0, 1) - 0.5
        c_b = P["w_balance"] * (abs(bx) + 0.5 * abs(by))
        c_hr = 0.0
        if head_top is not None and mode != "ring":
            hr = (head_top - y) / h
            if hr > 0.22:
                c_hr = P["w_headroom"] * (hr - 0.22)
        # articulacion pegada a un borde (justo dentro): corte "en la articulacion" -> penaliza (vale tambien
        # para el encuadre original, p.ej. un borde inferior que corta justo bajo las rodillas)
        c_j = 0.0
        for j in joints:
            if x <= j["x"] <= x + w and y <= j["y"] <= y + h:
                dy_e = min(j["y"] - y, y + h - j["y"]) / h
                dx_e = min(j["x"] - x, x + w - j["x"]) / w
                if dy_e < 0.05 or dx_e < 0.035:
                    c_j = P["w_joint_edge"]
                    break
        keep = (w * h) / (W * H)
        c_a = P["w_area"] * (1 - keep) + 4.0 * max(0.0, min_keep - keep)
        tot = c + c_h + c_l + c_b + c_hr + c_a + c_j
        if detail:
            return dict(total=round(tot, 4), subj_x=round(c_sx, 4), subj_y=round(c_sy, 4), horizon=round(c_h, 4),
                        lead=round(c_l, 4), joint_edge=round(c_j, 4), balance=round(c_b, 4), headroom=round(c_hr, 4), area=round(c_a, 4),
                        u=round(u, 3), v=round(v, 3), keep=round(keep, 4))
        return tot

    def legal(x, y, w, h, ident=False):
        if not rect_inside_rotated(x, y, w, h, W, H, angle):
            return False
        if ident:
            return True
        mx, my = P["protect_margin"] * w, P["protect_margin"] * h
        for b, _ in protect:
            # zona protegida recortada al marco valido original (lo que ya estaba cortado no exige nada)
            bx0, by0, bx1, by1 = max(b[0], 0), max(b[1], 0), min(b[2], W), min(b[3], H)
            if bx1 <= bx0 or by1 <= by0:
                continue
            if bx0 < x + (mx if bx0 > 1 else 0) or by0 < y + (my if by0 > 1 else 0) or \
               bx1 > x + w - (mx if bx1 < W - 1 else 0) or by1 > y + h - (my if by1 < H - 1 else 0):
                return False
        for j in joints:
            k = P["joint_tol_leg"] if j["type"] in ("knee", "ankle", "hip") else P["joint_tol"]
            tol_y, tol_x = k * H, k * W
            if x <= j["x"] <= x + w:
                for e in (y, y + h):
                    if 1 < e < H - 1 and abs(j["y"] - e) < tol_y:
                        return False
            if y <= j["y"] <= y + h:
                for e in (x, x + w):
                    if 1 < e < W - 1 and abs(j["x"] - e) < tol_x:
                        return False
        return True

    # identidad: sin recorte (o el mayor recorte centrado valido si hay giro)
    iw, ih = W * s_max, H * s_max
    ident = ((W - iw) / 2, (H - ih) / 2, iw, ih)
    c_ident = cost(*ident)
    best, c_best = ident, c_ident
    if ov.get("mode", "auto") != "none":
        s_lo = math.sqrt(min(min_keep, 0.98)) - 0.02
        for s in np.arange(s_max, s_lo, -0.005):
            w, h = W * s, H * s
            for x in np.linspace(0, W - w, max(int((W - w) / (0.004 * W)), 1) + 1):
                for y in np.linspace(0, H - h, max(int((H - h) / (0.004 * H)), 1) + 1):
                    c = cost(x, y, w, h)
                    if c < c_best - 1e-9 and legal(x, y, w, h):
                        best, c_best = (x, y, w, h), c
        if c_ident - c_best < ov.get("improve_min", P["improve_min"]):
            best, c_best = ident, c_ident

    x, y, w, h = best
    cropped = abs(w - W) > 1 or abs(h - H) > 1
    # a px del JPG completo; 3:2 exacto, h par
    k = FW / W
    fh = int(round(h * k / 2) * 2)
    fw = int(round(fh * FW / FH))
    fx = int(round(x * k)); fy = int(round(y * k))
    fx = min(max(fx, 0), FW - fw); fy = min(max(fy, 0), FH - fh)
    if not cropped and not angle:
        fx, fy, fw, fh = 0, 0, FW, FH
    elif not angle:
        # origen alineado a bloques JPEG de 8 px (4:4:4) -> recorte SIN PERDIDAS con jpegtran (desplazamiento < 8 px)
        fx = min(int(round(fx / 8) * 8), (FW - fw) // 8 * 8)
        fy = min(int(round(fy / 8) * 8), (FH - fh) // 8 * 8)
    if angle:
        while not rect_inside_rotated(fx, fy, fw, fh, FW, FH, angle):
            fh -= 2; fw = int(round(fh * FW / FH)); fx += 1; fy += 1
    keep_pct = round(100 * fw * fh / (FW * FH), 1)
    if not cropped and not angle:
        decision = "none"
    elif angle and not (abs(w - iw) > 1 or abs(x - ident[0]) > 1 or abs(y - ident[1]) > 1):
        decision = "straighten"
    elif angle:
        decision = "straighten+crop"
    else:
        decision = "crop"
    d_best, d_ident = cost(*best, detail=True), cost(*ident, detail=True)
    reason = (ov.get("note") if decision == ov.get("expect", decision) else None) or \
        _auto_reason(decision, d_ident, d_best, mode)
    params = dict(angle_deg=float(angle), x=fx, y=fy, w=fw, h=fh, keep_pct=keep_pct, decision=decision,
                  reason=f"{reason}. Nivel: {ang_reason}.", full_size=[FW, FH], subject_mode=mode,
                  cost_before=d_ident["total"], cost_after=d_best["total"])
    params["_dbg"] = dict(S=S, tilt=tilt, web_rect=best, ident=ident, subj=subj.tolist(), horizon_y=horizon_y,
                          heads=heads, hands=hands, feet=feet, joints=joints, d_best=d_best, d_ident=d_ident,
                          angle=angle, W=W, H=H, x_t=x_t, y_t=y_t)
    return params


def _auto_reason(decision, di, db, mode):
    who = {"group": "la pareja/grupo (linea de ojos)", "faces": "la cara (ojos)", "ring": "el anillo",
           "salient": "el elemento destacado"}.get(mode, mode)
    if decision in ("none", "straighten"):
        return (f"Composicion ya correcta: {who} en u={di['u']:.2f}, v={di['v']:.2f}; ningun recorte mejora lo "
                f"suficiente sin perder resolucion o contexto")
    return (f"{who} pasa de u={di['u']:.2f},v={di['v']:.2f} a u={db['u']:.2f},v={db['v']:.2f} "
            f"(tercios/phi); coste {di['total']:.3f} -> {db['total']:.3f}")


# ------------------------------------------------------------------------------------------------
# Overlay de depuracion
# ------------------------------------------------------------------------------------------------
def _grid(img, x, y, w, h, thick=2):
    for f, col in ((1 / 3, (255, 255, 255)), (2 / 3, (255, 255, 255)), (PHI, (0, 215, 255)), (1 - PHI, (0, 215, 255))):
        cv2.line(img, (int(x + f * w), int(y)), (int(x + f * w), int(y + h)), col, thick, cv2.LINE_AA)
        cv2.line(img, (int(x), int(y + f * h)), (int(x + w), int(y + f * h)), col, thick, cv2.LINE_AA)


def _label(img, text, org, scale=1.0):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 6, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)


def debug_overlay(img_web, params, out_path):
    d = params["_dbg"]
    W, H, angle = d["W"], d["H"], d["angle"]
    before = img_web.copy()
    for s in d["tilt"]["h_segs"]:
        cv2.line(before, (int(s[0] * W), int(s[1] * H)), (int(s[2] * W), int(s[3] * H)), (255, 120, 0), 3)
    for s in d["tilt"]["v_segs"]:
        cv2.line(before, (int(s[0] * W), int(s[1] * H)), (int(s[2] * W), int(s[3] * H)), (255, 0, 200), 3)
    _grid(before, 0, 0, W, H, 1)
    rot = cv2.warpAffine(img_web, rot_matrix(W, H, angle), (W, H), flags=cv2.INTER_LANCZOS4) if angle else img_web.copy()
    # rectangulo de recorte sobre el "antes" (en coordenadas del original)
    x, y, w, h = d["web_rect"]
    Minv = cv2.invertAffineTransform(rot_matrix(W, H, angle)) if angle else None
    c = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], float)
    if angle:
        c = c @ Minv[:, :2].T + Minv[:, 2]
    cv2.polylines(before, [c.astype(np.int32)], True, (0, 0, 255), 6, cv2.LINE_AA)
    after = rot[int(round(y)):int(round(y + h)), int(round(x)):int(round(x + w))].copy()
    ah, aw = after.shape[:2]
    off = np.array([x, y])

    def box(img, b, col, o=(0, 0)):
        cv2.rectangle(img, (int(b[0] - o[0]), int(b[1] - o[1])), (int(b[2] - o[0]), int(b[3] - o[1])), col, 3)
    for b in d["heads"]:
        box(after, b, (0, 255, 0), off)
    for b in d["hands"]:
        box(after, b, (0, 165, 255), off)
    for b in d["feet"]:
        box(after, b, (255, 255, 0), off)
    for j in d["joints"]:
        cv2.circle(after, (int(j["x"] - x), int(j["y"] - y)), 9, (200, 0, 255), -1)
    _grid(after, 0, 0, aw, ah, 2)
    if d["horizon_y"] is not None:
        cv2.line(after, (0, int(d["horizon_y"] - y)), (aw, int(d["horizon_y"] - y)), (255, 120, 0), 3)
    sx, sy = d["subj"]
    cv2.drawMarker(after, (int(sx - x), int(sy - y)), (0, 0, 255), cv2.MARKER_CROSS, 60, 6)
    # escalar ambos a la misma altura
    th = 1100
    b2 = cv2.resize(before, (round(W * th / H), th), interpolation=cv2.INTER_AREA)
    a2 = cv2.resize(after, (round(aw * th / ah), th), interpolation=cv2.INTER_AREA)
    gap = np.full((th, 24, 3), 30, np.uint8)
    canvas = np.concatenate([b2, gap, a2], 1)
    bar = np.full((150, canvas.shape[1], 3), 25, np.uint8)
    t = d["tilt"]
    _label(bar, f"{params.get('stem', '')}  decision={params['decision']}  giro={params['angle_deg']:+.2f}  "
                f"conserva={params['keep_pct']}%  sujeto={params['subject_mode']}", (12, 40), 0.9)
    hv = (f"h({t.get('h_src', '')})={t['h_angle']:+.2f}/len{t['h_len']:.1f}/acuerdo{t.get('h_agree', 0):.2f}"
          if t["h_angle"] is not None else "h=-")
    vv = (f"v={t['v_angle']:+.2f}/len{t['v_len']:.1f}/acuerdo{t.get('v_agree', 0):.2f}"
          if t["v_angle"] is not None else "v=-")
    _label(bar, f"lineas {hv}  {vv}  horizonte={t['horizon_y']}", (12, 85), 0.75)
    db, di = d["d_best"], d["d_ident"]
    _label(bar, f"coste antes {di['total']:.3f} (u{di['u']:.2f} v{di['v']:.2f})  despues {db['total']:.3f} "
                f"(u{db['u']:.2f} v{db['v']:.2f})  ANTES | DESPUES", (12, 130), 0.75)
    canvas = np.concatenate([bar, canvas], 0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])


# ------------------------------------------------------------------------------------------------
# Aplicar al JPG final
# ------------------------------------------------------------------------------------------------
def _exif_from_raw(raw_path, dst, w, h):
    exiftool = find_tool("exiftool")
    if not exiftool or not raw_path or not Path(raw_path).exists():
        return
    subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-tagsFromFile", str(raw_path), "-all:all",
                    "-Orientation=1", "-n", f"-ExifIFD:ExifImageWidth={w}", f"-ExifIFD:ExifImageHeight={h}",
                    "-IFD1:all=", str(dst)], capture_output=True)
    subprocess.run([exiftool, "-m", "-q", "-q", "-overwrite_original", "-GPS:all=", str(dst)], capture_output=True)


def _cut(im: Image.Image, angle, x, y, w, h) -> Image.Image:
    if angle:
        a = np.asarray(im)
        Hh, Ww = a.shape[:2]
        a = cv2.warpAffine(a, rot_matrix(Ww, Hh, angle), (Ww, Hh), flags=cv2.INTER_LANCZOS4,
                           borderMode=cv2.BORDER_REPLICATE)
        return Image.fromarray(a[y:y + h, x:x + w])
    return im.crop((x, y, x + w, y + h))


def apply(full_jpg_path, params, out_path, web_src=None, web_out=None, raw_path=None):
    """Recorta (y gira si hace falta) el JPG FINAL. Recorte puro = sin remuestreo, el grano queda intacto.
    Guarda q97 4:4:4 con el perfil ICC original y copia EXIF del ARW (Orientation=1)."""
    out_path = Path(out_path)
    JPEGTRAN = find_tool("jpegtran")
    im = Image.open(full_jpg_path)
    icc = im.info.get("icc_profile")
    im = im.convert("RGB")
    FW, FH = im.size
    if [FW, FH] != list(params["full_size"]):
        raise RuntimeError(f"tamano inesperado {FW}x{FH} vs {params['full_size']}")
    ang, x, y, w, h = params["angle_deg"], params["x"], params["y"], params["w"], params["h"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.jpg")
    full = (x, y, w, h) == (0, 0, FW, FH) and not ang
    if full:
        # sin recorte ni giro: copia exacta del final (cero perdida)
        del im
        shutil.copyfile(full_jpg_path, tmp)
        params["method"] = "copia exacta (sin recorte)"
    elif not ang and JPEGTRAN and x % 8 == 0 and y % 8 == 0:
        # recorte sin perdidas en el dominio DCT: ni remuestreo ni recompresion, el grano queda intacto
        del im
        r = subprocess.run([JPEGTRAN, "-copy", "all", "-optimize", "-crop", f"{w}x{h}+{x}+{y}",
                            "-outfile", str(tmp), str(full_jpg_path)], capture_output=True, text=True)
        if r.returncode != 0 or Image.open(tmp).size != (w, h):
            raise RuntimeError(f"jpegtran fallo: {r.stderr[-300:]}")
        params["method"] = "recorte sin perdidas (jpegtran, bloques DCT intactos)"
    else:
        out = _cut(im, ang, x, y, w, h)
        del im
        out.save(tmp, "JPEG", quality=97, subsampling=0, icc_profile=icc, optimize=True)
        del out
        params["method"] = ("giro Lanczos + recorte, JPEG q97 4:4:4 (el giro remuestrea levemente el grano)"
                            if ang else "recorte, JPEG q97 4:4:4")
    _exif_from_raw(raw_path, tmp, w, h)
    os.replace(tmp, out_path)
    # version web: se parte de la web2560 del motor (su grano/nitidez de salida) con el mismo giro y recorte
    if web_src and web_out:
        web_out = Path(web_out)
        web_out.parent.mkdir(parents=True, exist_ok=True)
        wi = Image.open(web_src)
        wicc = wi.info.get("icc_profile")
        wi = wi.convert("RGB")
        f = wi.size[0] / FW
        if full:
            ww, wh = wi.size
            wi.close()
            shutil.copyfile(web_src, web_out.with_suffix(".tmp.jpg"))
            tmp = web_out.with_suffix(".tmp.jpg")
            _exif_from_raw(raw_path, tmp, ww, wh)
            os.replace(tmp, web_out)
            return out_path
        wc = _cut(wi, ang, int(round(x * f)), int(round(y * f)), int(round(w * f)), int(round(h * f)))
        L = max(wi.size)
        tw, th = (L, round(L * h / w)) if w >= h else (round(L * w / h), L)
        if wc.size != (tw, th):
            wc = wc.resize((tw, th), Image.LANCZOS)
        web_out.parent.mkdir(parents=True, exist_ok=True)
        tmp = web_out.with_suffix(".tmp.jpg")
        wc.save(tmp, "JPEG", quality=95, subsampling=0, icc_profile=wicc, optimize=True)
        _exif_from_raw(raw_path, tmp, tw, th)
        os.replace(tmp, web_out)
    return out_path


# ------------------------------------------------------------------------------------------------
# TIFF 16 bit: el MISMO giro + recorte que el JPG
# ------------------------------------------------------------------------------------------------
def transform_array(a: np.ndarray, params: dict) -> np.ndarray:
    """Aplica giro (alrededor del centro, Lanczos 4) + recorte de params a un array HxWxC de cualquier tipo
    (uint8, uint16, float32). Misma geometria exacta que apply() sobre el JPG."""
    FH, FW = a.shape[:2]
    if [FW, FH] != list(params["full_size"]):
        raise RuntimeError(f"tamano inesperado {FW}x{FH} vs {params['full_size']}")
    ang, x, y, w, h = params["angle_deg"], int(params["x"]), int(params["y"]), int(params["w"]), int(params["h"])
    if ang:
        dt = a.dtype
        src = a.astype(np.float32) if dt not in (np.uint8, np.uint16, np.float32) else a
        r = cv2.warpAffine(src, rot_matrix(FW, FH, ang), (FW, FH), flags=cv2.INTER_LANCZOS4,
                           borderMode=cv2.BORDER_REPLICATE)
        a = r.astype(dt) if r.dtype != dt else r
    return np.ascontiguousarray(a[y:y + h, x:x + w])


def apply_tiff16(full_tif_path, params, out_path, raw_path=None):
    """Giro + recorte del TIFF 16 bit con interpolacion de alta calidad (Lanczos 4 en 16 bit, sin pasar por 8 bit).
    Escritura atomica; conserva el perfil ICC si lo hay."""
    import tifffile
    out_path = Path(out_path)
    with tifffile.TiffFile(str(full_tif_path)) as tf:
        a = tf.asarray()
        icc = tf.pages[0].tags.get(34675)
        icc = icc.value if icc is not None else None
    if (params["x"], params["y"], params["w"], params["h"]) == (0, 0, a.shape[1], a.shape[0]) and \
            not params["angle_deg"]:
        if Path(full_tif_path) != out_path:
            shutil.copyfile(full_tif_path, out_path)
        return out_path
    o = transform_array(a, params)
    del a
    tmp = out_path.with_suffix(".tmp.tif")
    extratags = [(34675, 7, len(icc), icc, True)] if icc else []
    tifffile.imwrite(str(tmp), o, photometric="rgb", extratags=extratags)
    _exif_from_raw(raw_path, tmp, o.shape[1], o.shape[0])
    os.replace(tmp, out_path)
    return out_path
