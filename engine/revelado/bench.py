# SPDX-License-Identifier: GPL-3.0-or-later
"""Measurement helpers shared by the tests and ``scripts/benchmark_quality.py``.

* ``run_worker``: run a develop worker module in a child process with a params dict; returns the worker's result,
  the wall time and the PEAK RSS of the whole process tree (Python + rawtherapee-cli), sampled every 0.1 s.
* image metrics: CIEDE2000 (ΔE00) between two sRGB 8-bit images, SSIM on luma, chroma noise / fringe scores.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ENGINE_DIR = Path(__file__).resolve().parent.parent


def run_worker(module: str, params: dict, python: str | None = None, log_path: Path | None = None,
               env_extra: dict | None = None) -> dict:
    import psutil
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(params, fh)
        ppath = fh.name
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)
    env.update(env_extra or {})
    t0 = time.time()
    proc = subprocess.Popen([python or sys.executable, "-u", "-m", module, ppath], cwd=str(ENGINE_DIR), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    ps = psutil.Process(proc.pid)
    peak = {"rss": 0}
    stop = threading.Event()

    def sample():
        while not stop.wait(0.1):
            try:
                tot = sum(p.memory_info().rss for p in [ps] + ps.children(recursive=True))
            except psutil.Error:
                continue
            peak["rss"] = max(peak["rss"], tot)
    th = threading.Thread(target=sample, daemon=True)
    th.start()
    result, stages, lines = {}, [], []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        if line.startswith("@@R "):
            result = json.loads(line[4:])
        elif line.startswith("@@P "):
            stages.append(json.loads(line[4:]))
    proc.wait()
    stop.set()
    th.join(1)
    os.unlink(ppath)
    if log_path:
        Path(log_path).write_text("".join(lines))
    result.setdefault("ok", False)
    if not result["ok"] and "error" not in result:
        result["error"] = "".join(lines[-20:])
    result["wall_s"] = round(time.time() - t0, 2)
    result["peak_tree_rss_mb"] = round(peak["rss"] / 1e6)
    return result


# ------------------------------------------------------------------------------------------------ colour metrics
def srgb8_to_lab(rgb8: np.ndarray) -> np.ndarray:
    """sRGB 8-bit -> CIELAB (D65), float32."""
    c = rgb8.astype(np.float32) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], np.float32)
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], -1).astype(np.float32)


def delta_e2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 colour difference per pixel (Sharma, Wu, Dalal 2005)."""
    L1, a1, b1 = (lab1[..., i].astype(np.float64) for i in range(3))
    L2, a2, b2 = (lab2[..., i].astype(np.float64) for i in range(3))
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cm ** 7 / (Cm ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(C1p * C2p == 0, 0, np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh)))
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lpm = (L1 + L2) / 2
    Cpm = (C1p + C2p) / 2
    hs = h1p + h2p
    hpm = np.where(C1p * C2p == 0, hs, np.where(np.abs(h1p - h2p) <= 180, hs / 2,
                                                np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2)))
    T = (1 - 0.17 * np.cos(np.radians(hpm - 30)) + 0.24 * np.cos(np.radians(2 * hpm))
         + 0.32 * np.cos(np.radians(3 * hpm + 6)) - 0.20 * np.cos(np.radians(4 * hpm - 63)))
    dtheta = 30 * np.exp(-(((hpm - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cpm ** 7 / (Cpm ** 7 + 25.0 ** 7))
    Sl = 1 + 0.015 * (Lpm - 50) ** 2 / np.sqrt(20 + (Lpm - 50) ** 2)
    Sc = 1 + 0.045 * Cpm
    Sh = 1 + 0.015 * Cpm * T
    Rt = -np.sin(np.radians(2 * dtheta)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh)).astype(np.float32)


def ssim_luma(a8: np.ndarray, b8: np.ndarray) -> float:
    """Mean SSIM on the Rec.709 luma of two 8-bit RGB images (gaussian window sigma 1.5)."""
    import cv2
    w = np.array([0.2126, 0.7152, 0.0722], np.float32)
    x, y = a8.astype(np.float32) @ w, b8.astype(np.float32) @ w
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    g = lambda z: cv2.GaussianBlur(z, (0, 0), 1.5)  # noqa: E731
    mx, my = g(x), g(y)
    sxx, syy, sxy = g(x * x) - mx * mx, g(y * y) - my * my, g(x * y) - mx * my
    s = ((2 * mx * my + C1) * (2 * sxy + C2)) / ((mx * mx + my * my + C1) * (sxx + syy + C2))
    return float(s.mean())


def compare_images(p1: Path, p2: Path, long_side: int | None = 2400) -> dict:
    """ΔE00 (mean, p95, p99) and SSIM between two JPEGs, optionally on an area-downscaled copy (grain-insensitive)."""
    import cv2
    from PIL import Image
    a = np.asarray(Image.open(p1).convert("RGB"))
    b = np.asarray(Image.open(p2).convert("RGB"))
    if a.shape != b.shape:
        return {"error": f"tamaños distintos {a.shape} vs {b.shape}"}
    out = {"size": [a.shape[1], a.shape[0]]}
    if long_side and max(a.shape[:2]) > long_side:
        f = long_side / max(a.shape[:2])
        sz = (round(a.shape[1] * f), round(a.shape[0] * f))
        a = cv2.resize(a, sz, interpolation=cv2.INTER_AREA)
        b = cv2.resize(b, sz, interpolation=cv2.INTER_AREA)
        out["compared_at"] = list(sz)
    de = delta_e2000(srgb8_to_lab(a), srgb8_to_lab(b))
    out.update(de00_mean=round(float(de.mean()), 3), de00_p95=round(float(np.percentile(de, 95)), 3),
               de00_p99=round(float(np.percentile(de, 99)), 3), ssim=round(ssim_luma(a, b), 4))
    return out
