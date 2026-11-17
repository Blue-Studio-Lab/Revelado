# SPDX-License-Identifier: GPL-3.0-or-later
"""Face / person detection behind one interface, with interchangeable backends.

Order (``REVELADO_FACE_BACKEND`` = auto | vision | yunet | haar; default auto):
  1. ``vision``  macOS Vision framework helper (faces + 76-point landmarks + person boxes). Optional, macOS only.
  2. ``yunet``   OpenCV YuNet DNN (cross-platform; MIT model downloaded on first use, sha256-pinned).
  3. ``haar``    OpenCV Haar cascades (consensus of two frontal cascades + profile), last resort.

All coordinates are pixels of the image passed in, origin top-left. Landmarks (``lm``) are only provided by the
Vision backend; the other backends return ``None`` and the callers fall back to box geometry (as the reference
engine did with Haar).

Extra macOS-only capabilities (each returns ``None`` when unavailable, and the caller degrades gracefully):
  ``landmarks_full`` (facelm), ``person_matte`` (personseg), ``framing_analysis`` (body/hand pose, saliency).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import cv2
import numpy as np

from . import assets

F32 = np.float32


def _write_jpeg(rgb8: np.ndarray, path: str, q: int = 93) -> None:
    cv2.imwrite(path, np.ascontiguousarray(rgb8[..., ::-1]), [cv2.IMWRITE_JPEG_QUALITY, q])


def backend_order() -> list[str]:
    b = (os.environ.get("REVELADO_FACE_BACKEND") or "auto").lower()
    return ["vision", "yunet", "haar"] if b == "auto" else [b]


# ------------------------------------------------------------------------------------------------ vision
def vision_people(rgb8: np.ndarray, out_size: tuple[int, int] | None = None) -> dict | None:
    """Faces (+landmarks) and person boxes with the macOS Vision helper. None if unavailable or failed.
    Coordinates are returned in pixels of ``out_size`` (W, H) (default: the size of rgb8)."""
    exe = assets.macos_helper("facevision")
    if exe is None:
        return None
    W, H = out_size or (rgb8.shape[1], rgb8.shape[0])
    try:
        with tempfile.TemporaryDirectory() as td:
            pth = os.path.join(td, "prev.jpg")
            _write_jpeg(rgb8, pth)
            r = subprocess.run([str(exe), pth], capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None
    if "error" in d:
        return None
    out = dict(faces=[], lm=[], humans=[], src="vision")
    WH = np.array([W, H], F32)
    for fc in d.get("faces", []):
        x, y, w, h = fc["box"]
        if fc.get("conf", 1.0) < 0.5 or w * W < 0.012 * max(H, W):
            continue
        out["faces"].append((x * W, y * H, w * W, h * H))
        out["lm"].append({k: np.array(v, F32) * WH for k, v in fc.items()
                          if isinstance(v, list) and v and isinstance(v[0], list)})
    for hb in d.get("humans", []):
        if hb[4] >= 0.4:
            out["humans"].append((hb[0] * W, hb[1] * H, hb[2] * W, hb[3] * H))
    return out


# ------------------------------------------------------------------------------------------------ yunet
_YUNET = {}


def yunet_faces(rgb8: np.ndarray, score: float = 0.6, long_side: int = 1600) -> list[tuple] | None:
    p = assets.model_path("yunet")
    if p is None or not hasattr(cv2, "FaceDetectorYN"):
        return None
    H, W = rgb8.shape[:2]
    f = min(1.0, long_side / max(H, W))
    img = cv2.resize(rgb8, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA) if f < 1 else rgb8
    h, w = img.shape[:2]
    det = _YUNET.get(str(p))
    if det is None:
        det = cv2.FaceDetectorYN.create(str(p), "", (w, h), score, 0.3, 5000)
        _YUNET[str(p)] = det
    det.setInputSize((w, h))
    det.setScoreThreshold(score)
    _, faces = det.detect(np.ascontiguousarray(img[..., ::-1]))
    out = []
    for r in (faces if faces is not None else []):
        x, y, bw, bh = (float(v) / f for v in r[:4])
        if bw < 0.012 * max(H, W):
            continue
        out.append((max(0.0, x), max(0.0, y), bw, bh))
    return out


# ------------------------------------------------------------------------------------------------ haar
def haar_boxes(gray: np.ndarray) -> list[tuple]:
    """Reference-engine Haar logic: consensus of the two frontal cascades + strict profile cascade (and mirror)."""
    dets = []
    for key in ("haar_frontal_alt2", "haar_frontal_default"):
        p = assets.model_path(key)
        if p is None:
            dets.append([])
            continue
        c = cv2.CascadeClassifier(str(p))
        r = c.detectMultiScale(gray, scaleFactor=1.06, minNeighbors=3, minSize=(22, 22))
        dets.append([tuple(int(v) for v in b) for b in (r if len(r) else [])])
    boxes = []
    for (x, y, w, hh) in dets[0]:
        for (x2, y2, w2, h2) in dets[1]:
            if x <= x2 + w2 / 2 <= x + w and y <= y2 + h2 / 2 <= y + hh and 0.7 < w2 / w < 1.4:
                boxes.append((x, y, w, hh))
                break
    pp = assets.model_path("haar_profile")
    if pp is not None:
        c = cv2.CascadeClassifier(str(pp))
        Wg = gray.shape[1]
        for flip in (False, True):
            g2 = cv2.flip(gray, 1) if flip else gray
            r = c.detectMultiScale(g2, scaleFactor=1.06, minNeighbors=6, minSize=(40, 40))
            for b in (r if len(r) else []):
                x, y, w, hh = (int(v) for v in b)
                if flip:
                    x = Wg - x - w
                boxes.append((x, y, w, hh))
    return boxes


# ------------------------------------------------------------------------------------------------ macOS extras
def landmarks_full(rgb8: np.ndarray) -> list[dict] | None:
    """All Vision landmarks per face (pixels of rgb8), keys leftEye, rightEye, outerLips, nose, ... and 'box'.
    None when the helper is not available (hair module then skips)."""
    exe = assets.macos_helper("facelm")
    if exe is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "f.jpg")
        _write_jpeg(rgb8, p, 95)
        r = subprocess.run([str(exe), p], capture_output=True, text=True, timeout=120)
    d = json.loads(r.stdout.strip().splitlines()[-1])
    if "error" in d:
        raise RuntimeError(d["error"])
    H, W = rgb8.shape[:2]
    faces = []
    for f in d.get("faces", []):
        g = {k: (np.array(v, np.float64) * [W, H]) for k, v in f.items() if isinstance(v, list) and v
             and isinstance(v[0], list)}
        g["box"] = np.array(f["box"]) * [W, H, W, H]
        for k in ("yaw", "roll", "pitch", "conf"):
            if k in f:
                g[k] = float(f[k])
        faces.append(g)
    return faces


def person_matte(rgb8: np.ndarray) -> np.ndarray | None:
    """Person matte 0..1 float32 at the size of rgb8 (Vision 'accurate'). None when unavailable."""
    exe = assets.macos_helper("personseg")
    if exe is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        pin, pout = os.path.join(td, "i.jpg"), os.path.join(td, "m.png")
        _write_jpeg(rgb8, pin, 95)
        subprocess.run([str(exe), pin, pout], capture_output=True, text=True, timeout=180)
        m = cv2.imread(pout, cv2.IMREAD_GRAYSCALE)
    if m is None:
        return None
    m = cv2.resize(m, (rgb8.shape[1], rgb8.shape[0]), interpolation=cv2.INTER_CUBIC)
    return np.clip(m.astype(F32) / 255.0, 0, 1)


def framing_analysis(jpeg_path: str) -> dict | None:
    """Faces/eyes, person boxes, body & hand pose and saliency (normalised coords) for the framing module."""
    exe = assets.macos_helper("framing_vision")
    if exe is None:
        return None
    try:
        r = subprocess.run([str(exe), str(jpeg_path)], capture_output=True, text=True, timeout=300)
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None
    return None if "error" in d else d


def framing_fallback(rgb8: np.ndarray) -> dict:
    """Cross-platform stand-in for framing_analysis: faces only (YuNet, then Haar), normalised coordinates."""
    H, W = rgb8.shape[:2]
    boxes = yunet_faces(rgb8)
    if boxes is None:
        g = cv2.cvtColor(rgb8, cv2.COLOR_RGB2GRAY)
        boxes = [tuple(map(float, b)) for b in haar_boxes(cv2.equalizeHist(g))]
    return {"faces": [{"box": [x / W, y / H, w / W, h / H], "conf": 1.0} for x, y, w, h in boxes],
            "humans": [], "bodies": [], "hands": [], "salient": [], "src": "yunet/haar"}
