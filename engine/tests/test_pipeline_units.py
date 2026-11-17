# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests per pipeline stage on small synthetic images (fast, no RAW, no network, no private data)."""
from __future__ import annotations

import json

import numpy as np
import pytest
import tifffile

from conftest import synthetic_scene, write_jpg


# ------------------------------------------------------------------------------------------------ config / tools
def test_env_revelado_name_then_dotenv_then_default(monkeypatch):
    from revelado import config
    monkeypatch.setenv("REVELADO_PORT", "9200")
    assert config._get({"REVELADO_PORT": "9300"}, "REVELADO_PORT", "1") == "9200"   # environment wins
    monkeypatch.delenv("REVELADO_PORT")
    assert config._get({"REVELADO_PORT": "9300"}, "REVELADO_PORT", "1") == "9300"   # then engine/.env
    assert config._get({}, "REVELADO_PORT", "1") == "1"                             # then the default


def test_tool_resolver_env_override(tmp_path, monkeypatch):
    from revelado import tools
    fake = tmp_path / "exiftool-fake"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    tools.find_tool.cache_clear()
    monkeypatch.setenv("REVELADO_EXIFTOOL", str(fake))
    assert tools.find_tool("exiftool") == str(fake)
    tools.find_tool.cache_clear()
    monkeypatch.delenv("REVELADO_EXIFTOOL")
    assert tools.find_tool("definitely-not-a-tool-xyz") is None
    tools.find_tool.cache_clear()


def test_user_state_dir_per_os(monkeypatch, tmp_path):
    from revelado import config
    monkeypatch.setenv("REVELADO_STATE_DIR", str(tmp_path / "s"))
    assert config.user_state_dir() == tmp_path / "s"


# ------------------------------------------------------------------------------------------------ colour
def test_oklab_roundtrip():
    from revelado.pipeline.oklab import oklab, oklab_inv
    rng = np.random.default_rng(0)
    x = rng.uniform(0.001, 2.0, (64, 64, 3)).astype(np.float32)
    assert np.allclose(oklab_inv(oklab(x)), x, rtol=2e-3, atol=2e-4)


def test_srgb_transfer_roundtrip():
    from revelado.pipeline.colorspaces import srgb_decode, srgb_encode
    v = np.linspace(-0.2, 1.5, 200, dtype=np.float32)
    assert np.allclose(srgb_encode(srgb_decode(v)), v, atol=1e-5)


# ------------------------------------------------------------------------------------------------ clean (NR / defringe)
def _noisy_linear(seed=1, sigma=0.02, h=384, w=512):
    rng = np.random.default_rng(seed)
    base = np.full((h, w, 3), 0.05, np.float32)
    base[:, w // 2:] = (0.08, 0.06, 0.04)
    noise = rng.normal(0, sigma, (h, w, 3)).astype(np.float32) * base.mean()
    return np.maximum(base + noise * 4, 1e-4).astype(np.float32), base


def test_clean_reduces_chroma_noise():
    from revelado.pipeline import clean
    from revelado.pipeline.oklab import oklab
    img, _ = _noisy_linear()
    out, info = clean.clean(img, ev=2.0, level=0.8)
    a0, a1 = oklab(img * 4.0), oklab(out * 4.0)
    flat = (slice(40, -40), slice(40, 200))                    # inside one flat patch, away from the colour edge
    s0 = (a0[flat][..., 1].std() + a0[flat][..., 2].std())
    s1 = (a1[flat][..., 1].std() + a1[flat][..., 2].std())
    assert s1 < 0.5 * s0, (s0, s1)
    assert info["chroma"]["median"] in (3, 5)
    # mean colour is preserved (no colour shift from the NR)
    assert np.allclose(out.mean((0, 1)), img.mean((0, 1)), rtol=0.03)


def test_defringe_removes_purple_fringe_but_keeps_purple_object():
    from revelado.pipeline import clean
    from revelado.pipeline.oklab import oklab
    h, w = 200, 300
    img = np.full((h, w, 3), 0.04, np.float32)
    img[:, 150:] = 2.0                                   # bright highlight (window)
    img[:, 144:150] = (0.08, 0.02, 0.12)                 # purple fringe next to it
    img[20:60, 20:80] = (0.08, 0.02, 0.12)               # a genuinely purple object far from highlights
    out, info = clean.clean(img, ev=0.0, level=0.0)
    lab_in, lab_out = oklab(img), oklab(out)
    c = lambda L, sl: float(np.hypot(L[sl][..., 1], L[sl][..., 2]).mean())  # noqa: E731
    fringe = (slice(80, 120), slice(145, 149))
    obj = (slice(30, 50), slice(30, 70))
    assert c(lab_out, fringe) < 0.6 * c(lab_in, fringe)
    assert c(lab_out, obj) > 0.85 * c(lab_in, obj)
    assert info["defringe_px_pct"] > 0


# ------------------------------------------------------------------------------------------------ develop helpers
def test_nr_modes_scale_the_auto_dose():
    from revelado.pipeline import raw
    base = raw.nr_params(6400, 3.0)
    assert raw.adjust_nr(base, "soft")["luma"] < base["luma"] < raw.adjust_nr(base, "strong")["luma"]
    assert raw.adjust_nr(base, "off")["level"] == 0.0
    assert raw.nr_params(100, 1.0)["level"] == 0.0      # ISO 100, no push: no clean-up


def test_sony_distortion_curve_identity_when_zero():
    from revelado.pipeline import raw
    img = np.random.default_rng(0).random((64, 96, 3)).astype(np.float32)
    out, info = raw.correct_distortion(img, [16] + [0] * 16)
    assert info["distortion"] == "none" and out is img


# ------------------------------------------------------------------------------------------------ film (Ektar)
def test_ektar_spec_is_package_data_and_curves_monotonic():
    from revelado.pipeline.film import ektar
    assert ektar.SPEC["name"].startswith("Kodak Ektar 100")
    assert np.all(np.diff(ektar.PRINT_D) > 0)
    u = np.linspace(-4, 3, 100, dtype=np.float32)
    for c in range(3):
        assert np.all(np.diff(ektar.neg_curve(u, c)) > 0)
    code = ektar.print_curve(np.array([ektar.neutral_neg_curve(np.float32(0.0))], np.float32))[0]
    assert 110 < code < 125                                   # mid grey ~118 (spec tone curve)


def test_grain_seed_from_content_not_name(tmp_path):
    from revelado.pipeline.film import ektar
    a, b, c = tmp_path / "a.bin", tmp_path / "b.bin", tmp_path / "c.bin"
    a.write_bytes(b"same pixels")
    b.write_bytes(b"same pixels")
    c.write_bytes(b"other pixels")
    assert ektar.seed_from_file(a) == ektar.seed_from_file(b) != ektar.seed_from_file(c)


def _base(tmp_path, w=900, h=600, seed=0):
    from revelado.worker import jpg_to_base
    j = write_jpg(tmp_path / f"s{seed}.jpg", synthetic_scene(w, h, seed))
    out = tmp_path / f"base{seed}.tif"
    jpg_to_base(j, out)
    return out


@pytest.mark.parametrize("film", ["kodak_ektar_100", "none"])
def test_render_is_deterministic(tmp_path, film):
    from revelado.pipeline.film import ektar, neutral
    base = _base(tmp_path)
    r = ektar.render if film != "none" else neutral.render
    kw = dict(ev=0.0, web=True, save_tiff16=True, seed=1234)
    r1 = r(str(base), str(tmp_path / "r1" / "o.jpg"), **kw)
    r2 = r(str(base), str(tmp_path / "r2" / "o.jpg"), **kw)
    for name in ("o.jpg", "o.tif", "web2560/o.jpg"):
        x = (tmp_path / "r1" / name).read_bytes()
        y = (tmp_path / "r2" / name).read_bytes()
        assert x == y, name
    assert r1["seed"] == r2["seed"] == 1234


def test_render_grain_changes_with_seed(tmp_path):
    from PIL import Image
    from revelado.pipeline.film import ektar
    base = _base(tmp_path)
    ektar.render(str(base), str(tmp_path / "a.jpg"), ev=0.0, web=False, seed=1, subject=0)
    ektar.render(str(base), str(tmp_path / "b.jpg"), ev=0.0, web=False, seed=2, subject=0)
    a = np.asarray(Image.open(tmp_path / "a.jpg"), np.int16)
    b = np.asarray(Image.open(tmp_path / "b.jpg"), np.int16)
    d = np.abs(a - b)
    assert 0.2 < d.mean() < 6                                  # grain differs, image does not


def test_neutral_shoulder_never_clips_and_keeps_hue():
    from revelado.pipeline.film.neutral import shoulder
    x = np.array([[[4.0, 2.0, 1.0], [0.3, 0.2, 0.1]]], np.float32)
    y = shoulder(x)
    assert y.max() < 1.0
    assert np.allclose(y[0, 0] / y[0, 0].max(), x[0, 0] / x[0, 0].max(), atol=1e-6)
    assert np.allclose(y[0, 1], x[0, 1])


# ------------------------------------------------------------------------------------------------ blemish
def test_blemish_heals_red_spot_on_flat_skin():
    from revelado.pipeline import blemish
    h, w = 400, 400
    lab = np.zeros((h, w, 3), np.float32)
    lab[..., 0], lab[..., 1], lab[..., 2] = 0.70, 0.035, 0.045
    rng = np.random.default_rng(0)
    lab[..., 0] += rng.normal(0, 0.003, (h, w)).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    spot = np.exp(-((yy - 200) ** 2 + (xx - 200) ** 2) / (2 * 3.0 ** 2)).astype(np.float32)
    lab[..., 1] += 0.04 * spot
    lab[..., 0] -= 0.03 * spot
    mask = np.ones((h, w), np.float32)
    before = float(lab[195:205, 195:205, 1].mean())
    info = blemish.heal_blemishes(lab, mask, fw=300.0)
    assert info["blemishes"] >= 1
    assert float(lab[195:205, 195:205, 1].mean()) < before - 0.01


# ------------------------------------------------------------------------------------------------ hair gate
def test_hair_gate_rejects_textured_background_and_accepts_smooth():
    from revelado.pipeline import hair
    rep = {"status": "applied", "zones": [{"focus": 0.3}]}
    ok, _ = hair.hair_gate(rep, {"changed_px": 50000, "frac_tex": 0.02, "frac_edge": 0.0, "frac_sin_fondo": 0.01})
    assert ok
    bad, why = hair.hair_gate(rep, {"changed_px": 50000, "frac_tex": 0.4, "frac_edge": 0.1, "frac_sin_fondo": 0.0})
    assert not bad and "textura" in why
    no, why = hair.hair_gate({"status": "skipped", "reason": "x"}, {})
    assert not no


def test_hair_without_matte_skips_cleanly():
    from revelado.pipeline import hair
    img = np.full((300, 400, 3), 0.1, np.float32)
    out, info = hair.clean_gated(img, strength="fuerte", ev=1.0)
    assert out is None and info["status"] == "skipped" and info["experimental"]


# ------------------------------------------------------------------------------------------------ reframe / TIFF16
def _params(w, h, angle, x, y, cw, ch):
    return {"angle_deg": angle, "x": x, "y": y, "w": cw, "h": ch, "full_size": [w, h]}


@pytest.mark.parametrize("angle", [0.0, 1.7, -3.2])
def test_tiff16_gets_same_rotation_and_crop_as_jpg(tmp_path, angle):
    """Regression: with rotation, the 16-bit TIFF used to keep the unrotated frame. Both must match now."""
    from PIL import Image
    from revelado.pipeline import reframe
    W, H = 600, 400
    rgb8 = synthetic_scene(W, H, 7)
    rgb16 = (rgb8.astype(np.uint16) * 257)
    jpg = tmp_path / "full.jpg"
    Image.fromarray(rgb8).save(jpg, "JPEG", quality=100, subsampling=0)
    tif = tmp_path / "full.tif"
    tifffile.imwrite(str(tif), rgb16, photometric="rgb")
    p = _params(W, H, angle, 24, 16, 540, 360)
    reframe.apply(jpg, dict(p), tmp_path / "crop.jpg")
    reframe.apply_tiff16(tif, dict(p), tmp_path / "crop.tif")
    import cv2
    a8 = np.asarray(Image.open(tmp_path / "crop.jpg"), np.float32)
    a16 = tifffile.imread(str(tmp_path / "crop.tif")).astype(np.float32) / 257.0
    assert a16.shape == a8.shape == (360, 540, 3)
    assert tifffile.imread(str(tmp_path / "crop.tif")).dtype == np.uint16
    lp = lambda a: cv2.GaussianBlur(a, (0, 0), 2.0)  # noqa: E731  geometry check, insensitive to JPEG noise
    d = np.abs(lp(a16) - lp(a8))
    assert d.mean() < 0.8 and np.percentile(d, 99) < 4, (d.mean(), np.percentile(d, 99))
    if angle:   # control: the OLD behaviour (crop without rotation) is clearly detected by the same check
        old = rgb16[16:376, 24:564].astype(np.float32) / 257.0
        assert np.abs(lp(old) - lp(a8)).mean() > 3 * d.mean() + 1.0
    # and the 16-bit path really is 16-bit (not an 8-bit image scaled up) when rotated
    if angle:
        assert len(np.unique(tifffile.imread(str(tmp_path / "crop.tif"))[..., 1] % 257)) > 10


def test_transform_array_identity():
    from revelado.pipeline import reframe
    a = np.arange(5 * 7 * 3, dtype=np.uint16).reshape(5, 7, 3)
    assert np.array_equal(reframe.transform_array(a, _params(7, 5, 0.0, 0, 0, 7, 5)), a)


def test_framing_analyze_runs_without_vision():
    import cv2
    from revelado.pipeline import reframe
    img = synthetic_scene(1280, 853, 4)[..., ::-1].copy()
    cv2.line(img, (0, 300), (1279, 330), (255, 255, 255), 3)          # a slightly tilted horizon
    p = reframe.analyze(img, full_size=(2560, 1706))
    assert p["full_size"] == [2560, 1706] and p["w"] <= 2560 and p["h"] <= 1706
    assert abs(p["angle_deg"]) <= 5.0
    json.dumps({k: v for k, v in p.items() if k != "_dbg"})


# ------------------------------------------------------------------------------------------------ faces
def test_face_backend_selection(monkeypatch):
    from revelado.pipeline import faces
    monkeypatch.setenv("REVELADO_FACE_BACKEND", "haar")
    assert faces.backend_order() == ["haar"]
    monkeypatch.setenv("REVELADO_FACE_BACKEND", "auto")
    assert faces.backend_order() == ["vision", "yunet", "haar"]


def test_model_hash_pinning(tmp_path, monkeypatch):
    from revelado.pipeline import assets
    monkeypatch.setenv("REVELADO_MODELS_DIR", str(tmp_path))
    (tmp_path / "face_detection_yunet_2023mar.onnx").write_bytes(b"tampered")
    assert assets.model_path("yunet", download=False) is None          # wrong hash -> deleted, not used
    assert not (tmp_path / "face_detection_yunet_2023mar.onnx").exists()


# ------------------------------------------------------------------------------------------------ metrics
def test_ciede2000_reference_pairs():
    """Reference values from Sharma, Wu & Dalal (2005), table 1."""
    from revelado.bench import delta_e2000
    pairs = [((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
             ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
             ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
             ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082)]
    a = np.array([[p[0] for p in pairs]], np.float32)
    b = np.array([[p[1] for p in pairs]], np.float32)
    assert np.allclose(delta_e2000(a, b)[0], [p[2] for p in pairs], atol=1e-3)


def test_ssim_identity_and_drop():
    from revelado.bench import ssim_luma
    a = synthetic_scene(200, 150, 1)
    assert ssim_luma(a, a) == pytest.approx(1.0)
    noisy = np.clip(a.astype(int) + np.random.default_rng(0).integers(-40, 40, a.shape), 0, 255).astype(np.uint8)
    assert ssim_luma(a, noisy) < 0.9


@pytest.mark.network
@pytest.mark.skipif(__import__("os").environ.get("REVELADO_TEST_NETWORK") != "1", reason="REVELADO_TEST_NETWORK=1")
def test_models_download_and_yunet_runs(tmp_path, monkeypatch):
    from revelado.pipeline import assets, faces
    monkeypatch.setenv("REVELADO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("REVELADO_OFFLINE", "0")
    for k in assets.MODELS:
        assert assets.model_path(k) is not None
    img = (np.random.default_rng(0).random((480, 640, 3)) * 255).astype(np.uint8)
    assert faces.yunet_faces(img) == []
