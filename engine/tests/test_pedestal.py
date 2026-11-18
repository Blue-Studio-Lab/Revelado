# SPDX-License-Identifier: GPL-3.0-or-later
"""High-ISO black-pedestal fix (raw.py pedestal_*) and the linear-mean restore of clean.py. Synthetic, no RAW files."""
from __future__ import annotations

import types

import numpy as np
import pytest


def _bayer(h, w, mu_rgb, sigma, black, seed=0):
    """RGGB mosaic (uint16-like float) with per-channel true signal mu (DN above black) and Gaussian noise."""
    rng = np.random.default_rng(seed)
    cols = np.zeros((h, w), np.uint8)
    cols[0::2, 1::2] = 1
    cols[1::2, 0::2] = 3
    cols[1::2, 1::2] = 2
    mu = np.array([mu_rgb[0], mu_rgb[1], mu_rgb[2], mu_rgb[1]], np.float32)[cols]
    raw = black + mu + rng.normal(0, sigma, (h, w)).astype(np.float32)
    return np.clip(np.round(raw), 0, 16383), cols


def test_preblack_removes_rectification_bias():
    from revelado.pipeline import raw as R
    black, sigma = 400.0, 100.0
    mu = (12.0, 30.0, 8.0)
    raw, cols = _bayer(1024, 1024, mu, sigma, black)
    sig, mu_d = R.dark_noise(raw, cols, np.full(4, black, np.float32))
    assert abs(sig - sigma) / sigma < 0.06
    K = float(min(black, np.ceil(4 * sig)))
    for c, m in ((0, mu[0]), (1, mu[1]), (2, mu[2])):
        v = raw[cols == c]
        old = np.maximum(v - black, 0).mean()                  # RawTherapee today: clip at the black level
        new = np.maximum(v - (black - K), 0).mean() - K       # PreBlack = -K, pedestal removed afterwards
        assert old - m > 25                                    # strongly rectified (~0.4 sigma at 0 signal)
        assert abs(old - m - R._rect_bias(m, sigma)) < 3       # bias model used by the gate
        assert abs(new - m) < 2.5                              # unbiased


def _fake_rawpy(monkeypatch, raw, cols, black, white=16383.0, flip=0, wb=(2.0, 1.0, 1.6, 1.0)):
    from revelado.pipeline import raw as R
    h, w = raw.shape

    class Fake:
        raw_pattern = np.array([[0, 1], [3, 2]])
        num_colors = 3
        raw_image_visible = raw.astype(np.uint16)
        raw_colors_visible = cols
        black_level_per_channel = [int(black)] * 4
        white_level = white
        camera_whitebalance = list(wb)
        rgb_xyz_matrix = np.vstack([np.eye(3) * 0.8 + 0.1, np.zeros((1, 3))])
        sizes = types.SimpleNamespace(crop_top_margin=0, top_margin=0, crop_left_margin=0, left_margin=0,
                                      crop_width=w, crop_height=h, flip=flip)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import rawpy
    monkeypatch.setattr(rawpy, "imread", lambda p: Fake())
    return R


def test_gate_off_for_low_noise_and_pp3_unchanged(monkeypatch):
    raw, cols = _bayer(512, 768, (60.0, 120.0, 50.0), 4.0, 512)        # normal ISO: sigma 4 DN, shadows >> sigma
    R = _fake_rawpy(monkeypatch, raw, cols, 512)
    p = R.pedestal_probe("x.arw", 1.5)
    assert p["applied"] is False and p["shift_est"] < R.PEDESTAL_SHIFT_MIN
    assert not any(k.startswith("_") for k in p)                       # nothing to fit -> old path
    nr = R.adjust_nr(R.nr_params(250, 1.5), "auto")
    txt = R._nr_pp3_text(nr)
    assert txt == R._nr_pp3_text(nr, preblack=None) and "PreBlack" not in txt
    assert "Enabled=true" in txt and "CMethod=AUT" in txt


def test_gate_on_for_high_noise(monkeypatch):
    raw, cols = _bayer(512, 768, (10.0, 25.0, 6.0), 120.0, 400)
    R = _fake_rawpy(monkeypatch, raw, cols, 400)
    p = R.pedestal_probe("x.nef", 2.4)
    assert p["applied"] is True and p["K"] == 400.0 and p["shift_est"] > R.PEDESTAL_SHIFT_MIN
    txt = R._nr_pp3_text(R.adjust_nr(R.nr_params(25600, 2.4), "auto"), preblack=p["K"])
    assert txt.count("=-400\n") == 4 and "PreBlack3=-400" in txt


@pytest.mark.parametrize("flip,k", [(0, 0), (3, 2), (6, 1), (5, 3)])
def test_pedestal_fit_recovers_pedestal_in_any_orientation(flip, k):
    from revelado.pipeline import raw as R
    B = R.PEDESTAL_FIT_BLOCK
    rng = np.random.default_rng(5)
    hb, wb_ = 12, 18
    K, rngDN = 300.0, 15983.0
    wb = np.array([1.53, 1.0, 1.93])
    scene = np.exp(rng.normal(np.log(1500), 1.0, (hb, wb_, 3))) * rng.uniform(0.6, 1.4, 3)
    Xb = (np.minimum(scene, 9000) + K) * wb                             # camera space, black lowered by K, WB'd
    M = np.array([[1.6, -0.5, -0.1], [-0.2, 1.4, -0.2], [0.0, -0.4, 1.4]])
    A_true = M / (rngDN + K)
    Yb = Xb @ A_true.T + 2e-6 * rng.normal(size=Xb.shape)
    sens = np.kron(Yb, np.ones((2 * B, 2 * B, 1))).astype(np.float32)    # full-res sensor-oriented RT output
    img = np.ascontiguousarray(np.rot90(sens, -k))                       # RT output is upright
    probe = {"K": K, "flip": flip, "_Xb": Xb, "_Cb": np.zeros((hb, wb_), bool), "_wb": wb,
             "_hw2": (hb * B, wb_ * B), "_P_analytic": np.zeros(3), "_alpha_analytic": 1.0}
    P, alpha, info = R.pedestal_fit(img, probe)
    assert info["method"] == "fit" and info["rot_k"] == k == info["expected_rot"]
    np.testing.assert_allclose(P, A_true @ (wb * K), rtol=1e-3)
    assert abs(alpha - rngDN / (rngDN + K)) < 1e-4
    assert info["fit_residual"] < 1e-4


def test_linear_mean_restore_keeps_linear_mean():
    from revelado.pipeline import clean as C
    rng = np.random.default_rng(1)
    h, w = 384, 512
    truth = np.zeros((h, w, 3), np.float32)
    truth[...] = (0.0020, 0.0016, 0.0012)                                # near-black, slightly warm
    truth[:, w // 2:] = (0.010, 0.010, 0.010)
    noisy = truth + rng.normal(0, 0.004, truth.shape).astype(np.float32)  # pedestal removed: zero-mean noise
    ev = 2.4
    out0, i0 = C.clean(noisy.copy(), ev, 1.0)
    out1, i1 = C.clean(noisy.copy(), ev, 1.0, restore_linear_mean=True)
    assert i1.get("linear_mean_restored") and "linear_mean_restored" not in i0
    sl = (slice(64, h - 64), slice(32, w // 2 - 64))                    # flat near-black area, away from the edge
    m_in, m0, m1 = (x[sl].reshape(-1, 3).mean(0) for x in (noisy, out0, out1))
    err0, err1 = np.abs(m0 - m_in).max(), np.abs(m1 - m_in).max()
    assert err1 < 0.2 * err0 and err1 < 1.5e-4                           # OkLab averaging bias cancelled
    assert abs(out1[sl].std() / out0[sl].std() - 1) < 0.05                # ... without giving the noise back


def test_restore_local_linear_mean_direct():
    from revelado.pipeline import clean as C
    rng = np.random.default_rng(2)
    x_in = rng.normal(0.01, 0.003, (203, 301, 3)).astype(np.float32)    # odd sizes: remainder rows / cols
    x_out = (x_in * 0.5 + 0.002).astype(np.float32)
    C.restore_local_linear_mean(x_in, x_out)
    np.testing.assert_allclose(x_out.mean((0, 1)), x_in.mean((0, 1)), atol=2e-4)


def test_nr_off_is_really_off():
    from revelado.pipeline import raw as R
    nr = R.adjust_nr(R.nr_params(25600, 2.4), "off")
    txt = R._nr_pp3_text(nr)
    assert "[Directional Pyramid Denoising]\nEnabled=false" in txt
    assert "CMethod" not in txt and "Chroma=" not in txt and "Method=amaze" in txt


def test_linear_mean_restore_no_halo_around_highlights():
    """A light whose value clean() legitimately changes must not bleed its difference into the dark surround."""
    from revelado.pipeline import clean as C
    rng = np.random.default_rng(3)
    h, w = 256, 384
    x_in = np.full((h, w, 3), 0.004, np.float32) + rng.normal(0, 0.001, (h, w, 3)).astype(np.float32)
    x_in[120:136, 180:196] = 8.0                                        # lamp
    x_out = x_in.copy()
    x_out[120:136, 180:196] = 5.0                                       # clean() changed the lamp (e.g. compression)
    x_out[x_in < 1] -= 0.0005                                          # small near-black bias to restore
    ring = np.zeros((h, w), bool)
    ring[90:166, 150:226] = True
    ring[116:140, 176:200] = False
    plain = C.restore_local_linear_mean(x_in, x_out.copy())
    dark = C.restore_local_linear_mean(x_in, x_out.copy(), dark_lo=0.05, dark_hi=0.15)
    assert (plain[ring] - x_in[ring]).mean() > 0.01                     # unweighted: halo
    assert abs((dark[ring] - x_in[ring]).mean()) < 2e-4                 # weighted: bias restored, no halo
    np.testing.assert_array_equal(dark[120:136, 180:196], x_out[120:136, 180:196])
