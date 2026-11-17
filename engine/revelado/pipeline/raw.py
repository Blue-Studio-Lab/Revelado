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


def _nr_pp3_text(nr: dict) -> str:
    sharp = ""
    if nr.get("deconv_contrast") is not None:
        sharp = f"""
[PostDemosaicSharpening]
AutoContrast=false
Contrast={nr['deconv_contrast']:.0f}
"""
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
{sharp}"""


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
    tmp_dir = Path(tmp_dir or out_tif.parent / "_tmp_rt")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_dir) as td:
        base_pp3 = _write_pp3(Path(td))
        nr_pp3 = Path(td) / "nr.pp3"
        nr_pp3.write_text(_nr_pp3_text(nr) + ("" if raw_ca else "\n[RAW]\nCA=false\n"))
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
    cinfo = {"clean": "off"}
    if clean:
        t_c = time.time()
        img, cinfo = cleanmod.clean(img, ev if ev is not None else 2.0, nr["level"], do_defringe=defringe)
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
            "pipeline": ("rawtherapee-cli AMaZE + rawCA(auto x3, avoid colour shift) + Coloropp HL + capture "
                         "sharpening + pyramid NR; python lin + clean.py (defringe, luma NLM, chroma guided NR) "
                         "+ distortion")}
    tifffile.imwrite(str(out_tif), img.astype(np.float16), photometric="rgb",
                     description=json.dumps(desc), metadata=None, software="revelado/pipeline/raw.py")
    res = {"file": arw.name, "out": str(out_tif), "shape": list(img.shape), "seconds_total": round(time.time() - t0, 2),
           "seconds_rt": round(t_rt, 2), "ev_suggested": ev, "nr": nr, "clean": cinfo, "lens": lens,
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
