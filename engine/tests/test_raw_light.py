# SPDX-License-Identifier: GPL-3.0-or-later
"""Light checks on every CC0 RAW format (no RawTherapee, no full-resolution render): exposure analysis, default
crop, embedded preview. Skipped when the test RAWs are not downloaded."""
from __future__ import annotations

import io
import math

import pytest
from PIL import Image

from conftest import raw_file

ROLES = ["sony_33mp", "canon", "nikon", "fuji", "high_iso", "high_iso_cr3"]
pytestmark = pytest.mark.needs_raw


@pytest.mark.parametrize("role", ROLES)
def test_exposure_analysis_every_format(role):
    from revelado.pipeline import raw
    a = raw.analyze_exposure(raw_file(role))
    assert math.isfinite(a["ev"]) and -2.0 <= a["ev"] <= 7.0
    assert a["iso"] > 0 and 0 <= a["raw_clipped_pct"] < 50


@pytest.mark.parametrize("role", ROLES)
def test_default_crop_and_embedded_preview(role):
    import rawpy
    from revelado.pipeline import raw
    p = raw_file(role)
    w, h = raw.camera_crop(p)
    assert w > 1000 and h > 1000
    with rawpy.imread(str(p)) as r:
        th = r.extract_thumb()
    assert th.format == rawpy.ThumbFormat.JPEG
    with Image.open(io.BytesIO(th.data)) as im:
        assert max(im.size) >= 1000


def test_high_iso_gets_strong_nr_dose():
    from revelado.pipeline import raw
    a = raw.analyze_exposure(raw_file("high_iso"))
    nr = raw.nr_params(a["iso"], a["ev"])
    assert nr["level"] >= 0.9 and nr["luma"] >= 20
