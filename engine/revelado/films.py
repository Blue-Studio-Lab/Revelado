# SPDX-License-Identifier: GPL-3.0-or-later
"""Film stocks from the user's scan library: ``<REVELADO_FILMS_DIR>/<stock_id>/scans/*.tif``.

The scans are private and stay local; only a small swatch (center crop of the first scan, ~600 px) is cached.
Tier by scan count (counts only; patch / roll checks come with the calibration step):
  1-2 scans -> provisional, 3-5 -> bronze, >= 6 -> silver_candidate (silver needs >= 2 rolls, not verified yet).
``engine_supported`` is True only for stocks the CURRENT render engine can actually develop (kodak_ektar_100).
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from .errors import ReveladoError
from .log import get_logger

log = get_logger("films")
SUPPORTED_BY_ENGINE = {"none", "kodak_ektar_100"}
_BW_HINTS = ("ilford", "delta", "hp5", "fp4", "tri_x", "trix", "tmax", "t_max", "pan_f", "bw", "acros", "fomapan")
_SCAN_EXT = {".tif", ".tiff", ".jpg", ".jpeg", ".png"}
_lock = threading.Lock()


def _pretty(stock: str) -> str:
    words = []
    for w in stock.split("_"):
        if re.fullmatch(r"\d+[a-z]", w):
            words.append(w.upper())
        elif w.isdigit():
            words.append(w)
        else:
            words.append(w.capitalize())
    return " ".join(words)


def _tier(n: int) -> str:
    return "provisional" if n <= 2 else "bronze" if n < 6 else "silver_candidate"


def _scans(d: Path) -> list[Path]:
    sd = d / "scans"
    if not sd.is_dir():
        return []
    return sorted(p for p in sd.iterdir() if p.suffix.lower() in _SCAN_EXT and not p.name.startswith("."))


def list_films(films_dir: Path | None) -> list[dict]:
    out = []
    if films_dir and films_dir.is_dir():
        for d in sorted(films_dir.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            scans = _scans(d)
            sid = d.name
            out.append({
                "id": sid, "name": _pretty(sid), "stock": sid,
                "type": "bw" if any(h in sid.lower() for h in _BW_HINTS) else "color",
                "scans": len(scans), "tier": _tier(len(scans)) if scans else "none",
                "swatch_url": f"/api/films/{sid}/swatch" if scans else None,
                "engine_supported": sid in SUPPORTED_BY_ENGINE,
                "status": "trained" if sid in SUPPORTED_BY_ENGINE else "untrained",
                "status_es": "Revelado disponible" if sid in SUPPORTED_BY_ENGINE else "Perfil aún no entrenado",
            })
    out.insert(0, {"id": "none", "name": "Neutro (sin película)", "stock": "none", "type": "neutral", "scans": 0,
                   "tier": "builtin", "swatch_url": None, "engine_supported": True, "status": "trained",
                   "status_es": "Revelado disponible"})
    # Ektar is always offered when the engine supports it, even without scans in the library
    if not any(f["id"] == "kodak_ektar_100" for f in out):
        out.insert(1, {"id": "kodak_ektar_100", "name": "Kodak Ektar 100", "stock": "kodak_ektar_100", "type": "color",
                       "scans": 0, "tier": "spec", "swatch_url": None, "engine_supported": True, "status": "trained",
                       "status_es": "Revelado disponible"})
    return out


def swatch(films_dir: Path | None, film_id: str, cache_dir: Path, size: int = 600) -> Path:
    if not films_dir or not re.fullmatch(r"[A-Za-z0-9_\-]+", film_id or ""):
        raise ReveladoError("E_NOT_FOUND", "película desconocida")
    d = films_dir / film_id
    scans = _scans(d)
    if not scans:
        raise ReveladoError("E_NOT_FOUND", "esta película no tiene escaneos")
    src = scans[0]
    st = src.stat()
    out = cache_dir / "swatches" / f"{film_id}_{size}_{int(st.st_mtime)}_{st.st_size}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with _lock:                                   # one full scan decode (~130 MB) at a time
        if out.exists():
            return out
        try:
            # OpenCV/libtiff reads LZW 16-bit TIFFs and decodes at 1/4 scale straight to 8-bit (low memory)
            import cv2
            bgr = cv2.imread(str(src), cv2.IMREAD_REDUCED_COLOR_4)
            if bgr is None:
                with Image.open(src) as im:
                    a = np.asarray(im.convert("RGB"))
            else:
                a = bgr[..., ::-1]
        except Exception as e:  # noqa: BLE001
            raise ReveladoError("E_RAW_DECODE", f"no se pudo leer el escaneo ({type(e).__name__})") from e
        if a.ndim == 3 and a.shape[2] > 3:
            a = a[..., :3]
        h, w = a.shape[:2]
        ch, cw = int(h * 0.6), int(w * 0.6)
        y0, x0 = (h - ch) // 2, (w - cw) // 2
        a = a[y0:y0 + ch, x0:x0 + cw]
        if a.dtype == np.uint16:
            a = (a.astype(np.float32) / 257.0 + 0.5).astype(np.uint8)
        elif a.dtype != np.uint8:
            a = np.clip(a, 0, 255).astype(np.uint8)
        im = Image.fromarray(np.ascontiguousarray(a)).convert("RGB")
        del a
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        tmp = out.with_suffix(".part")
        im.save(tmp, "JPEG", quality=90)
        os.replace(tmp, out)
        log.info("swatch %s from %s", film_id, src.name)
    return out
