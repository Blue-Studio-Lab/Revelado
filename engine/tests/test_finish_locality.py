# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression: the finish stage (skin retouch, blemishes, subject accent / subject exposure, sharpening) must be
LOCAL to detected people and colour-neutral everywhere else.

Bug it guards against: a white husky on green grass (no person) rendered with the editorial preset came out pink /
lavender and ~+1.75 EV brighter than with skin/subject off, because (1) the lab "cast" correction measured the
median colour of ALL midtones (green content read as a green cast -> global magenta gains), (2) with no person the
subject exposure pushed the frame median to L 0.56, (3) the subject accent and the skin mask fell back to "skin by
colour", which picks up light fur, warm clouds and rock.
"""
from __future__ import annotations

import numpy as np
import pytest
import tifffile

F32 = np.float32


def _fur_scene(path, w=900, h=600, seed=7):
    """Linear Rec.2020 base: dark green/yellow grass (strong content colour) + a white, slightly warm fur blob with
    peach-tinted shadow fur (skin-like hue) and fine texture. No faces, no people."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(F32)
    tex = 1.0 + 0.10 * rng.standard_normal((h, w)).astype(F32)
    img = np.empty((h, w, 3), F32)
    img[...] = (0.030, 0.055, 0.012)                                   # grass
    img *= (tex * (0.8 + 0.4 * (yy / h)))[..., None]
    r = ((xx - 0.5 * w) / (0.22 * w)) ** 2 + ((yy - 0.48 * h) / (0.36 * h)) ** 2
    fur = r < 1.0
    shade = np.clip(0.55 + 0.45 * (1 - r), 0, 1)
    img[fur] = (np.array([0.30, 0.29, 0.27], F32) * (shade * tex)[fur][:, None])
    peach = fur & (yy > 0.62 * h)                                      # warm, skin-coloured fur on the chest
    img[peach] = np.array([0.20, 0.15, 0.11], F32) * tex[peach][:, None]
    tifffile.imwrite(str(path), img.astype(np.float16), photometric="rgb")
    return fur, peach


def _oklab_of(jpg):
    from PIL import Image
    from revelado.pipeline.colorspaces import srgb_decode
    from revelado.pipeline.film.ektar import lin_to_oklab
    a = np.asarray(Image.open(jpg)).astype(F32) / 255.0
    return lin_to_oklab(srgb_decode(a).reshape(-1, 3)).reshape(a.shape)


def _hue_deg(a, b):
    return float(np.degrees(np.arctan2(b, a)))


def test_white_fur_no_faces_keeps_hue_and_exposure(tmp_path):
    from revelado.pipeline.film import ektar
    base = tmp_path / "fur.tif"
    fur, peach = _fur_scene(base)
    kw = dict(ev=1.0, grain=0.0, web=False, seed=11)
    off = ektar.render(str(base), str(tmp_path / "off.jpg"), skin=0, blem=0, subject=0, sharp=0, **kw)
    on = ektar.render(str(base), str(tmp_path / "on.jpg"), skin=1, blem=1, subject=1, sharp=1, **kw)
    assert on["finish"]["faces"] == 0
    assert on["finish"].get("skin_retouch") == "skipped: no faces"
    assert on["finish"].get("subj_ev", 0.0) == 0.0          # no person: global exposure kept
    assert on["ev"] == off["ev"]
    lo, ln = _oklab_of(tmp_path / "off.jpg"), _oklab_of(tmp_path / "on.jpg")
    from PIL import Image
    ro = np.asarray(Image.open(tmp_path / "off.jpg")).astype(F32)
    rn = np.asarray(Image.open(tmp_path / "on.jpg")).astype(F32)
    for name, m in (("fur", fur & ~peach), ("peach fur", peach), ("grass", ~fur)):
        mo, mn = lo[m].mean(0), ln[m].mean(0)
        Co, Cn = float(np.hypot(*mo[1:])), float(np.hypot(*mn[1:]))
        assert abs(mn[0] - mo[0]) < 0.006, (name, "L", mo, mn)            # no lift (sharpening is zero-mean)
        assert np.hypot(*(mn[1:] - mo[1:])) < 0.003, (name, "ab", mo, mn)  # no tint
        assert abs(Cn - Co) < 0.003, (name, "C", Co, Cn)
        if Co > 0.02:                                                      # hue defined: must not rotate
            dh = (_hue_deg(*mn[1:]) - _hue_deg(*mo[1:]) + 180) % 360 - 180
            assert abs(dh) < 3.0, (name, "hue", dh)
        d = (rn[m] - ro[m]).mean(0)
        assert np.all(np.abs(d) < 1.5), (name, "mean RGB shift", d)


def test_cast_estimate_ignores_content_colour_but_sees_illuminant():
    from revelado.pipeline.film import ektar
    rng = np.random.default_rng(0)
    h, w = 200, 300
    lab = np.zeros((h, w, 3), F32)
    lab[..., 0] = 0.6 + 0.05 * rng.standard_normal((h, w))
    lab[..., 1], lab[..., 2] = -0.07, 0.06                                # 75 % saturated foliage
    lab[:, : w // 4, 1:] = 0.0                                            # 25 % neutral (white fur, grey rock)
    lab[..., 1:] += 0.003 * rng.standard_normal((h, w, 2))
    g, mag = ektar.cast_gains(lab)
    assert g is None and mag < 0.01                                       # content colour is not a cast
    lab2 = lab.copy()
    lab2[..., 1] += 0.02
    lab2[..., 2] += 0.05                                                  # tungsten: EVERYTHING shifts warm
    g2, mag2 = ektar.cast_gains(lab2)
    assert g2 is not None and mag2 > 0.045


def test_skin_and_subject_masks_empty_without_people():
    from revelado.pipeline.film import ektar
    lab = np.zeros((240, 360, 3), F32)
    lab[..., 0], lab[..., 1], lab[..., 2] = 0.72, 0.035, 0.045            # skin-coloured everywhere
    assert float(ektar.build_skin_mask(lab, []).max()) == 0.0
    nobody = dict(faces=[], lm=[], humans=[], src="haar")
    assert float(ektar.subject_mask((240, 360), nobody, lab).max()) == 0.0
    info = {}
    assert ektar.subject_accent(lab.copy(), nobody, 1.0, info) is None and info["accent"] == 0


def test_skin_mask_stays_inside_people():
    from revelado.pipeline.film import ektar
    H, W = 480, 720
    lab = np.zeros((H, W, 3), F32)
    lab[..., 0], lab[..., 1], lab[..., 2] = 0.72, 0.035, 0.045            # skin-coloured wall behind the person
    face = (300.0, 80.0, 80.0, 100.0)
    m = ektar.build_skin_mask(lab, [face])
    assert m[120, 340] > 0.3                                              # on the face
    assert float(m[:, :120].max()) < 0.02 and float(m[:, -150:].max()) < 0.02   # far wall untouched
    pr = ektar.person_region((H, W), [face], humans=[(260.0, 60.0, 160.0, 400.0)], f=1.0)
    assert pr[300, 340] > 0.9 and float(pr[:, :200].max()) < 0.05


@pytest.mark.parametrize("amt", [0.0, 1.0])
def test_sharpening_is_luminance_only(amt):
    from revelado.pipeline.film import ektar
    rng = np.random.default_rng(3)
    lab = np.zeros((256, 256, 3), F32)
    lab[..., 0] = 0.5 + 0.1 * (rng.random((256, 256)) > 0.5)
    lab[..., 1], lab[..., 2] = 0.02, -0.03
    lab[..., 0] = ektar.gblur(lab[..., 0], 1.0)
    ab_before = lab[..., 1:].copy()
    ektar.finish(lab, 0.05, [], {}, skin_amt=0.0, sharp_amt=amt, blem_amt=0.0)
    # the residual chroma filter is guided by L; the sharpening itself must not add chroma
    assert np.abs(lab[..., 1:] - ab_before).max() < 1e-3


# ------------------------------------------------------------------------------------------------------------------
# Subject exposure is ASYMMETRIC: brighten a dim face to the target, keep a well-exposed one, darken only when the
# lit-side skin exceeds the highlight band (capped at -0.3 EV) or the face is about to clip.
# Bug it guards against: a well-exposed portrait (lit skin L* ~78, nothing clipped) was pulled DOWN to the L* 69
# target: -0.64 EV, neutral but dull.
# ------------------------------------------------------------------------------------------------------------------
_FACE = (390.0, 170.0, 150.0, 190.0)


def _face_scene(k, w=900, h=600, seed=5):
    """Linear Rec.2020 base: grey wall + one skin-toned face (lit from the right) at exposure factor ``k`` + dark
    clothing. Returns the image and the face-box mask."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(F32)
    tex = 1.0 + 0.04 * rng.standard_normal((h, w)).astype(F32)
    img = np.empty((h, w, 3), F32)
    img[...] = (0.16, 0.17, 0.15)
    img *= (tex * (0.7 + 0.6 * (xx / w)))[..., None]
    x, y, fw, fh = _FACE
    r = ((xx - (x + fw / 2)) / (0.5 * fw)) ** 2 + ((yy - (y + fh * 0.5)) / (0.55 * fh)) ** 2
    m = r < 1.0
    light = np.clip(0.55 + 0.6 * (xx - x) / fw, 0.5, 1.15)
    img[m] = np.array([0.42, 0.30, 0.22], F32) * F32(k) * (light * tex)[m][:, None]
    body = (np.abs(xx - (x + fw / 2)) < 0.9 * fw) & (yy > y + 1.05 * fh)
    img[body] = np.array([0.05, 0.05, 0.06], F32) * tex[body][:, None]
    return img, m


@pytest.fixture
def one_face(monkeypatch):
    from revelado.pipeline.film import ektar
    monkeypatch.setattr(ektar, "detect_people",
                        lambda lab, long_side=2200: dict(faces=[_FACE], lm=[None], humans=[], src="test"))
    return ektar


def _subj(ektar, k):
    img, _ = _face_scene(k)
    corr, info, _ = ektar.subject_exposure(img, 900 / ektar.REF_LONG_SIDE_PX, 1.0, np.random.default_rng(1))
    return corr, info


@pytest.mark.parametrize("k", [1.0, 1.4])
def test_well_exposed_face_is_not_darkened(one_face, tmp_path, k):
    ektar = one_face
    corr, info = _subj(ektar, k)
    assert ektar.SUBJ_TARGET_L - 0.01 <= info["subj_L_before"] <= ektar.SUBJ_HIGH_L, info   # scene really is "well exposed"
    assert info["face_p99_before"] < ektar.SUBJ_CLIP_L, info
    assert corr >= -0.3, info
    # end to end: the subject stage must not pull the frame down by more than 0.3 EV vs subject off
    img, face = _face_scene(k)
    base = tmp_path / "face.tif"
    tifffile.imwrite(str(base), img.astype(np.float16), photometric="rgb")
    kw = dict(ev=0.0, grain=0.0, web=False, seed=11, skin=0, blem=0, sharp=0)
    off = ektar.render(str(base), str(tmp_path / "off.jpg"), subject=0, **kw)
    on = ektar.render(str(base), str(tmp_path / "on.jpg"), subject=1, **kw)
    assert on["ev"] - off["ev"] >= -0.3 - 1e-6, (on["ev"], off["ev"])
    lo, ln = _oklab_of(tmp_path / "off.jpg"), _oklab_of(tmp_path / "on.jpg")
    assert float(ln[face][:, 0].mean()) >= float(lo[face][:, 0].mean()) - 0.005      # face not dimmer
    dab = ln[face][:, 1:].mean(0) - lo[face][:, 1:].mean(0)
    assert float(np.hypot(*dab)) < 0.004                                             # accent stays hue-neutral


def test_dim_face_is_brightened_toward_target(one_face):
    corr, info = _subj(one_face, 0.6)
    assert info["subj_mode"] == "aclarar" and corr > 0.3, info
    assert abs(info["subj_L_after"] - one_face.SUBJ_TARGET_L) < 0.02, info


def test_hot_face_darkening_is_capped_unless_clipping(one_face):
    ektar = one_face
    corr, info = _subj(ektar, 3.0)          # lit skin above the band, face p99 not yet at the paper shoulder
    assert info["subj_L_before"] > ektar.SUBJ_HIGH_L and info["subj_mode"] == "proteger", info
    assert ektar.SUBJ_DARKEN_CAP - 1e-6 <= corr < 0, info
    corr, info = _subj(ektar, 5.0)          # face about to clip: protection may go further than the cap
    assert info["face_p99_before"] > ektar.SUBJ_CLIP_L, info
    assert corr < ektar.SUBJ_DARKEN_CAP and info["face_p99_after"] <= ektar.SUBJ_CLIP_L, info
