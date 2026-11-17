# SPDX-License-Identifier: GPL-3.0-or-later
"""Downloadable model files (fetched on first use, pinned by sha256) and the optional macOS Vision helpers.

Models are NOT bundled in the repository: they are downloaded once from their official upstream location into the
engine's models cache (``REVELADO_MODELS_DIR`` or ``<cache>/models``) and verified against a pinned sha256. A file
whose hash does not match is deleted and never used.

  * YuNet face detector (OpenCV Zoo, MIT licence, Shiqi Yu et al.)
  * Haar cascades (OpenCV, Intel License Agreement / BSD-style) - fallback face detector

macOS helpers (Apple Vision framework) are small Swift programs shipped as source in ``revelado/native/macos``; they
are compiled with ``swiftc`` on first use when available. They are optional: every caller has a cross-platform path.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from importlib import resources
from pathlib import Path

MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
              "face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "haar_frontal_alt2": ("haarcascade_frontalface_alt2.xml",
                          "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/"
                          "haarcascade_frontalface_alt2.xml",
                          "7b0c967d9abbdfbde025eb9c786947d151b6426040d07a8f9562ed8fd90724b4"),
    "haar_frontal_default": ("haarcascade_frontalface_default.xml",
                             "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/"
                             "haarcascade_frontalface_default.xml",
                             "0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0"),
    "haar_profile": ("haarcascade_profileface.xml",
                     "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_profileface.xml",
                     "b39a4a3be45539db146a7fc1d3e761a292c196eb88421185e6a615b3055e612d"),
}
_lock = threading.Lock()


def models_dir() -> Path:
    d = os.environ.get("REVELADO_MODELS_DIR")
    if d:
        p = Path(d).expanduser()
    else:
        try:
            from ..config import get_settings
            p = get_settings().models_dir
        except Exception:  # noqa: BLE001 - library use without the engine config
            p = Path(tempfile.gettempdir()) / "revelado-models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def model_path(key: str, download: bool = True) -> Path | None:
    """Local path of a pinned model, downloading it on first use. None when unavailable (offline, no download)."""
    name, url, digest = MODELS[key]
    p = models_dir() / name
    with _lock:
        if p.exists():
            if sha256(p) == digest:
                return p
            p.unlink()
        if not download or os.environ.get("REVELADO_OFFLINE") == "1":
            return None
        tmp = p.with_suffix(p.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "revelado-model-fetch"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
        except OSError:
            tmp.unlink(missing_ok=True)
            return None
        if sha256(tmp) != digest:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{name}: sha256 no coincide; descarga descartada")
        os.replace(tmp, p)
        return p


# ------------------------------------------------------------------------------------------------ macOS helpers
def macos_helper(name: str) -> Path | None:
    """Compiled Vision helper ``name`` (facevision | facelm | personseg | framing_vision), or None.

    Disabled with REVELADO_NO_VISION=1 (forces the cross-platform path, used by tests)."""
    if sys.platform != "darwin" or os.environ.get("REVELADO_NO_VISION") == "1":
        return None
    src = resources.files("revelado.native").joinpath("macos", f"{name}.swift")
    try:
        code = src.read_bytes()
    except (FileNotFoundError, OSError):
        return None
    tag = hashlib.sha256(code).hexdigest()[:12]
    out = models_dir() / "bin" / f"{name}-{tag}"
    if out.exists():
        return out
    from ..tools import find_tool
    swiftc = find_tool("swiftc")
    if not swiftc:
        return None
    with _lock:
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / f"{name}.swift"
            s.write_bytes(code)
            r = subprocess.run([swiftc, "-O", str(s), "-o", str(out.with_suffix(".part"))], capture_output=True,
                               text=True, timeout=600)
        if r.returncode != 0:
            return None
        os.replace(out.with_suffix(".part"), out)
    return out
