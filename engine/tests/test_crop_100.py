# SPDX-License-Identifier: GPL-3.0-or-later
"""100 % view (/crop) on RAW-only photos: the request is in DISPLAYED full-resolution coordinates, so a RAW whose
embedded preview is small must be cropped from a full-resolution render (cached), never from the preview. Also the
export destination's "~/" expansion. Light: the LibRaw render is replaced by a synthetic full-resolution scene."""
from __future__ import annotations

import io
import os
import types

import numpy as np
import pytest
from PIL import Image

from conftest import raw_file, synthetic_scene
from revelado.errors import ReveladoError
from revelado.imaging import ImageStore

# sensor (unrotated) 600x400; orientation 6 = portrait, displayed 400x600
SENSOR_W, SENSOR_H = 600, 400


def _decode(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("RGB")).astype(np.int16)


@pytest.fixture
def raw_only(tmp_path, monkeypatch):
    """A RAW-only photo whose embedded preview is ~1/4 size, with the LibRaw render faked (upright full-res scene)."""
    store = ImageStore(tmp_path / "cache")
    raw = tmp_path / "P1060804.dng"
    raw.write_bytes(b"not really a raw")
    os.utime(raw, (1_000_000, 1_000_000))
    upright = synthetic_scene(SENSOR_H, SENSOR_W, seed=7)          # displayed: 400 wide x 600 tall
    sensor = np.rot90(upright, 1)                                  # stored landscape; orientation 6 rotates it back
    assert sensor.shape[:2] == (SENSOR_H, SENSOR_W)
    pid = "rawonly1"
    Image.fromarray(sensor).resize((150, 100), Image.Resampling.LANCZOS).save(store.embedded / f"{pid}.jpg", "JPEG",
                                                                                quality=92)
    calls = []

    def fake_render(path: str) -> np.ndarray:
        calls.append(path)
        return upright.copy()

    monkeypatch.setattr(ImageStore, "_render_full_rgb", staticmethod(fake_render))
    photo = {"id": pid, "raw_path": str(raw), "jpg_path": None,
             "meta": {"width": SENSOR_W, "height": SENSOR_H, "orientation": 6}}
    return types.SimpleNamespace(store=store, photo=photo, upright=upright.astype(np.int16), calls=calls)


def test_raw_only_crop_is_full_resolution_and_right_region(raw_only):
    s, p, truth = raw_only.store, raw_only.photo, raw_only.upright
    assert s.display_size(p) == (400, 600)
    x, y, w, h = 37, 311, 149, 92
    data, mode, box = s.crop(p, x, y, w, h)
    img = _decode(data)
    assert img.shape[:2] == (h, w)                                 # exactly the requested size (was 338x1 before)
    assert box["source"] == "raw_full" and mode == "none" and not box["upscaled"]
    assert (box["full_w"], box["full_h"], box["source_w"], box["source_h"]) == (400, 600, 400, 600)
    assert (box["x"], box["y"], box["w"], box["h"]) == (x, y, w, h)
    err = np.abs(img - truth[y:y + h, x:x + w]).mean()
    shifted = np.abs(img - truth[y + 20:y + 20 + h, x + 20:x + 20 + w]).mean()
    assert err < 4 and shifted > 3 * err                           # same pixels (JPEG noise only), not a nearby area


def test_raw_full_render_is_cached(raw_only):
    s, p = raw_only.store, raw_only.photo
    s.crop(p, 0, 0, 64, 64)
    s.crop(p, 300, 500, 100, 100)                                  # clamped to the bottom-right corner
    _, _, box = s.crop(p, 399, 599, 50, 50)
    assert len(raw_only.calls) == 1                                # one render, reused by every crop
    assert (box["x"], box["y"], box["w"], box["h"]) == (399, 599, 1, 1)
    assert (s.embedded / f"{p['id']}.full.jpg").exists()


def test_full_res_embedded_is_used_directly_with_orientation(tmp_path, monkeypatch):
    """A full-size embedded JPEG (Canon CR2) needs no render; orientation from the catalog is respected."""
    store = ImageStore(tmp_path / "cache")
    raw = tmp_path / "_MG_8283.CR2"
    raw.write_bytes(b"x")
    os.utime(raw, (1_000_000, 1_000_000))
    upright = synthetic_scene(SENSOR_H, SENSOR_W, seed=11)
    Image.fromarray(np.rot90(upright, 1)).save(store.embedded / "cr2.jpg", "JPEG", quality=95)

    def boom(path: str):
        raise AssertionError("no render needed")

    monkeypatch.setattr(ImageStore, "_render_full_rgb", staticmethod(boom))
    p = {"id": "cr2", "raw_path": str(raw), "jpg_path": None, "meta": {"width": 600, "height": 400, "orientation": 6}}
    data, _, box = store.crop(p, 120, 410, 100, 70)
    img = _decode(data)
    assert img.shape[:2] == (70, 100) and box["source"] == "embedded" and box["scale"] == 1
    assert np.abs(img - upright[410:480, 120:220].astype(np.int16)).mean() < 4


def test_jpg_only_smaller_than_exif_is_upscaled_and_says_so(tmp_path):
    store = ImageStore(tmp_path / "cache")
    jpg = tmp_path / "IMG_0001.JPG"
    Image.fromarray(synthetic_scene(300, 200)).save(jpg, "JPEG", quality=92)
    p = {"id": "j", "raw_path": None, "jpg_path": str(jpg), "meta": {"width": 600, "height": 400}}
    data, _, box = store.crop(p, 100, 100, 200, 120)
    assert _decode(data).shape[:2] == (120, 200)
    assert box["source"] == "camera_jpg" and box["upscaled"] and box["scale"] == 0.5


def test_crop_endpoint_reports_source(tmp_path, settings, jpg_only_folder):
    from fastapi.testclient import TestClient
    from revelado.api import app as appmod
    appmod.CTX = appmod.build_ctx(settings)
    try:
        with TestClient(appmod.app) as c:
            c.headers["X-Revelado-Token"] = appmod.CTX.token
            sid = c.post("/api/library/open", json={"path": str(jpg_only_folder)}).json()["session_id"]
            p = c.get("/api/photos", params={"session_id": sid}).json()[0]
            r = c.get(f"/api/photos/{p['id']}/crop", params=dict(x=10, y=20, w=100, h=80))
            assert r.status_code == 200 and r.headers["x-preview-source"] == "camera_jpg"
            import json
            box = json.loads(r.headers["x-crop-box"])
            assert (box["w"], box["h"]) == (100, 80) and _decode(r.content).shape[:2] == (80, 100)
            assert "home" in c.get("/api/health").json()
    finally:
        appmod.CTX = None


# ------------------------------------------------------------------------------------------------ export destination
def _fake_ctx(tmp_path):
    return types.SimpleNamespace(settings=types.SimpleNamespace(cache_dir=tmp_path / "cache"),
                                 catalog=types.SimpleNamespace(session=lambda sid: {"path": str(tmp_path / "raws")}))


def test_export_dest_expands_home(tmp_path, monkeypatch):
    from revelado.develop import validate_dest
    monkeypatch.setenv("HOME", str(tmp_path))
    rows = [{"session_id": "s"}]
    d = validate_dest(_fake_ctx(tmp_path), "~/revelado-demo/raws_Entrega", rows)
    assert d == (tmp_path / "revelado-demo" / "raws_Entrega").resolve() and d.is_dir()
    with pytest.raises(ReveladoError):
        validate_dest(_fake_ctx(tmp_path), "~root/x", rows)          # only the user's own home
    with pytest.raises(ReveladoError):
        validate_dest(_fake_ctx(tmp_path), "relative/dir", rows)
    (tmp_path / "raws").mkdir()
    with pytest.raises(ReveladoError):
        validate_dest(_fake_ctx(tmp_path), "~/raws/out", rows)       # inside the originals, after expansion


@pytest.mark.heavy
@pytest.mark.needs_raw
def test_real_raw_only_crop_full_res(tmp_path):
    """Real Sony ARW (embedded preview 1616 px): the 100 % crop comes from a full-resolution LibRaw render."""
    import rawpy
    src = raw_file("sony_33mp")
    with rawpy.imread(str(src)) as r:
        W, H = r.sizes.width, r.sizes.height
    store = ImageStore(tmp_path / "cache")
    p = {"id": "sony", "raw_path": str(src), "jpg_path": None, "meta": {"width": W, "height": H, "orientation": 1}}
    data, _, box = store.crop(p, W // 2, H // 2, 800, 600)
    assert _decode(data).shape[:2] == (600, 800) and box["source"] == "raw_full" and not box["upscaled"]


# ------------------------------------------------------------------------------------------------ metadata size
def test_dng_size_prefers_default_crop_size():
    """A Panasonic DNG reports ExifImageWidth 1920 (its preview); the photo is 6000x4000 (DefaultCropSize)."""
    from revelado import library, metadata
    m = metadata.normalise({"DefaultCropSize": "6000 4000", "ExifImageWidth": 1920, "ExifImageHeight": 1280}, "s")
    assert (m["width"], m["height"]) == (6000, 4000) and m["meta_v"] == metadata.META_VERSION
    m = metadata.normalise({"ExifImageWidth": 6000, "ExifImageHeight": 4000}, "s")      # CR2: no DefaultCropSize
    assert (m["width"], m["height"]) == (6000, 4000)
    import json
    assert library._meta_current(json.dumps(m)) and not library._meta_current('{"width": 1920}')
