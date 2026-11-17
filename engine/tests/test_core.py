# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests that need no private data: recipe, catalog persistence, orientation maths, profiles, errors."""
from __future__ import annotations

import numpy as np
import pytest

from revelado import profiles, recipe
from revelado.catalog import Catalog, photo_id_for, session_id_for
from revelado.errors import ReveladoError
from revelado.imaging import ImageStore, slog3_to_display


def test_default_recipe_is_neutral():
    d = recipe.normalise({})
    assert d["film"] == "none" and d["grain"] == 0 and d["skin_retouch"] == 0 and d["blemishes"] == 0
    assert d["accent"] == 0 and d["hair"] == "off" and d["reframe"] == "off" and d["develop_source"] == "raw"
    ed = recipe.normalise(recipe.PRESETS["editorial_ektar"])
    assert ed["film"] == "kodak_ektar_100" and ed["film_strength"] == 1.0 and ed["skin_retouch"] == 60


def test_recipe_defaults_and_mapping():
    r = recipe.normalise(dict(recipe.PRESETS["editorial_ektar"], skin_retouch=120, hair=True, nr="strong", foo=1))
    assert r["skin_retouch"] == 100 and r["hair"] == "fuerte"
    p, ignored = recipe.to_engine(r)
    assert p["skin"] == pytest.approx(100 / 60) and p["blem"] == 1.0 and p["subject"] == 1.0
    assert p["nr"] == "strong" and p["film"] == "kodak_ektar_100"
    assert any("foo" in s for s in ignored)
    lp, lign = recipe.to_legacy(r)
    assert any("nr" in s for s in lign)
    with pytest.raises(ReveladoError):
        recipe.normalise({"reframe": "maybe"})
    assert recipe.key(recipe.normalise({})) == recipe.key(dict(recipe.DEFAULT))


def test_catalog_persists_ratings(tmp_path):
    db = tmp_path / "c.sqlite"
    c = Catalog(db)
    sid = session_id_for(tmp_path)
    pid = photo_id_for(sid, "dsc0001")
    row = {"id": pid, "stem_key": "dsc0001", "name": "DSC0001", "raw_path": None, "jpg_path": "/x.jpg", "mtime": 1.0,
           "meta": {}, "capture_ts": 1.0}
    c.upsert_photos(sid, [row])
    assert c.upsert_session(sid, tmp_path, "t", 1) is False
    c.set_rating(pid, 4, "pick", True)
    c2 = Catalog(db)                                   # "restart"
    c2.upsert_photos(sid, [row])                       # rescan keeps user data
    assert c2.upsert_session(sid, tmp_path, "t", 1) is True
    r = c2.photo(pid)
    assert r["rating"] == 4 and r["flag"] == "pick"


@pytest.mark.parametrize("o", [1, 3, 6, 8])
def test_crop_rect_inverse_orientation(o):
    W, H = 60, 40
    raw = np.arange(W * H).reshape(H, W)
    k = {1: 0, 3: 2, 6: -1, 8: 1}[o]                    # how the viewer rotates the stored image
    disp = np.rot90(raw, k)
    x, y, w, h = 5, 7, 11, 9
    x0, y0, x1, y1 = ImageStore._to_raw_rect(o, x, y, w, h, W, H)
    crop = np.rot90(raw[y0:y1, x0:x1], k)
    assert np.array_equal(crop, disp[y:y + h, x:x + w])


def test_slog3_display_midgrey():
    code = round(420 / 1023 * 255)                      # S-Log3 18 % grey
    out = slog3_to_display(np.full((2, 2, 3), code, np.uint8))
    assert 100 < int(out[0, 0, 0]) < 135


def test_profile_resolution_fx2():
    meta = {"make": "Sony", "model": "ILME-FX2", "lens": "FE 24-70mm F2.8 GM", "iso": 400, "focal": 70,
            "aperture": 2.8, "picture_profile": "s-log3", "is_log": True, "picture_profile_raw": "Gamma S-Log3",
            "embedded_corrections": {"DistortionCorrParams": True}}
    p = profiles.profile_for(meta)
    assert p["camera_profile"]["key"] == "sony/ilme-fx2"
    assert p["lens_profile"]["key"] == "sony/fe-24-70mm-f2.8-gm"
    assert p["recommended"]["ca"]["value"]["iterations"] == 3          # medium TCA at the long end
    assert p["recommended"]["nr_level"]["value"] == 0.0               # ISO 400: legacy anchor -> level 0
    gm2 = profiles.resolve_lens({"lens": "FE 24-70mm F2.8 GM II"})
    assert gm2["source"] == "generic"                                  # generation suffix is a hard token
