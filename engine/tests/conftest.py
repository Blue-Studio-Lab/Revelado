# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures. Nothing here needs private data.

* ``test_raws``: the CC0 RAW files downloaded by ``python scripts/fetch_test_raws.py`` into ``<repo>/test-data``
  (override with REVELADO_TEST_DATA). Tests that need them are skipped when the files are missing.
* ``jpg_only_folder`` / ``raw_only_folder``: tiny libraries built on the fly in a temp dir.
* Detection runs on the cross-platform path in tests by default (no macOS Vision helper, no model download):
  REVELADO_NO_VISION=1, REVELADO_OFFLINE=1 unless the caller already set them.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

os.environ.setdefault("REVELADO_NO_VISION", "1")
os.environ.setdefault("REVELADO_OFFLINE", "1")

ENGINE = Path(__file__).resolve().parent.parent
REPO = ENGINE.parent
TEST_DATA = Path(os.environ.get("REVELADO_TEST_DATA") or REPO / "test-data")
MANIFEST = Path(__file__).resolve().parent / "data" / "test_raws.json"


def _manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text())["files"]


def raw_file(role: str) -> Path:
    """Path of the CC0 test RAW with this role (sony_33mp, canon, nikon, fuji, high_iso); skip if missing."""
    for f in _manifest():
        if f["role"] == role:
            p = TEST_DATA / f["filename"]
            if p.exists():
                return p
            pytest.skip(f"falta {f['filename']}: ejecuta python scripts/fetch_test_raws.py")
    pytest.skip(f"rol desconocido {role}")


@pytest.fixture(scope="session")
def test_raws() -> dict[str, Path]:
    out = {f["role"]: TEST_DATA / f["filename"] for f in _manifest() if (TEST_DATA / f["filename"]).exists()}
    if not out:
        pytest.skip("sin RAW de prueba: ejecuta python scripts/fetch_test_raws.py")
    return out


def synthetic_scene(w: int = 960, h: int = 640, seed: int = 3) -> np.ndarray:
    """uint8 RGB test scene: gradients, saturated patches, a bright window and fine texture."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack([x / w, y / h, 0.5 + 0.5 * np.sin(x / 37.0) * np.cos(y / 23.0)], -1) * 200 + 20
    img[h // 8:h // 3, w // 8:w // 3] = (200, 40, 40)
    img[h // 2:h // 2 + h // 6, w // 2:w // 2 + w // 5] = (40, 90, 210)
    img[h // 10:h // 5, 3 * w // 4:9 * w // 10] = 255
    img += rng.normal(0, 4, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def write_jpg(path: Path, rgb: np.ndarray, orientation: int = 1) -> Path:
    exif = Image.Exif()
    exif[0x0110] = "Synthetic"          # Model
    exif[0x010F] = "TestCam"            # Make
    exif[0x0112] = orientation
    Image.fromarray(rgb).save(path, "JPEG", quality=92, exif=exif)
    return path


@pytest.fixture
def jpg_only_folder(tmp_path) -> Path:
    d = tmp_path / "jpg_only"
    d.mkdir()
    for i in range(3):
        write_jpg(d / f"IMG_{i:04d}.JPG", synthetic_scene(seed=i))
    return d


@pytest.fixture
def raw_only_folder(tmp_path) -> Path:
    """One small CC0 RAW (the Canon file) copied alone into a folder."""
    src = raw_file("canon")
    d = tmp_path / "raw_only"
    d.mkdir()
    shutil.copyfile(src, d / ("RAWONLY" + src.suffix.upper()))
    return d


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from revelado.config import Settings, get_settings
    from revelado.tools import find_tool
    monkeypatch.setenv("REVELADO_STATE_DIR", str(tmp_path / "state"))
    s = Settings(cache_dir=tmp_path / "cache", films_dir=None, legacy_dir=None, legacy_python="python3",
                 host="127.0.0.1", port=8790, dev=True, heavy_rss_cap_gb=9, heavy_min_available_gb=0.5,
                 min_free_disk_gb=0.5, exiftool=find_tool("exiftool"), rawtherapee=find_tool("rawtherapee-cli"),
                 token_file=tmp_path / "runtime_token")
    s.ensure_dirs()
    get_settings.cache_clear()
    return s
