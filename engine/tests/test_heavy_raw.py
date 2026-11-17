# SPDX-License-Identifier: GPL-3.0-or-later
"""HEAVY tests on the CC0 RAW files: full develop per camera format, determinism and a peak-memory budget.

Excluded by default (``-m 'not heavy'`` in pyproject). Run them ONE AT A TIME (16 GB machines):
    pytest -m heavy -k "develop_each_format and canon"
    pytest -m heavy -k determinism
    pytest -m heavy -k memory_budget
Each develop runs in its own process (as in the app) and the peak RSS of the whole process tree is measured.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from conftest import raw_file
from revelado.tools import find_tool

pytestmark = [pytest.mark.heavy, pytest.mark.needs_raw, pytest.mark.needs_rawtherapee,
              pytest.mark.skipif(find_tool("rawtherapee-cli") is None, reason="rawtherapee-cli no instalado")]

# peak RSS of the process tree: fixed overhead + per-megapixel cost (measured: ~2.3 GB at 12 MP, see benchmarks)
BUDGET_BASE_MB = 1200
BUDGET_PER_MP_MB = 190


def _develop(tmp_path, src, preset="editorial_ektar", tag="a", **over):
    from revelado import recipe
    from revelado.bench import run_worker
    rcp = recipe.normalise(dict(recipe.PRESETS[preset], **over))
    ep, _ = recipe.to_engine(rcp)
    out = tmp_path / f"out_{tag}"
    r = run_worker("revelado.worker", {"source": "raw", "raw_path": str(src), "work_dir": str(tmp_path / f"w_{tag}"),
                                       "out_dir": str(out), "engine_params": ep, "recipe": rcp, "tiff16": True})
    assert r["ok"], r.get("error")
    return r, out


@pytest.mark.parametrize("role", ["canon", "nikon", "fuji", "high_iso", "high_iso_cr3", "sony_33mp"])
def test_develop_each_format(role, tmp_path):
    src = raw_file(role)
    r, out = _develop(tmp_path, src, reframe="apply")
    side = json.loads((out / "sidecar.json").read_text())
    w, h = side["size"]
    with Image.open(out / "full.jpg") as im:
        assert im.size == (w, h)
        a = np.asarray(im.reduce(8))
    assert 20 < a.mean() < 235 and a.std() > 8                  # a real, exposed picture (not black / flat)
    with Image.open(out / "web.jpg") as im:
        assert max(im.size) == 2560 or max(im.size) == max(w, h)
    t = __import__("tifffile").imread(str(out / "full.tif"))
    assert t.dtype == np.uint16 and t.shape[:2] == (h, w)       # TIFF16 has the same geometry as the JPG
    print(role, json.dumps({"times": r["times"], "peak_tree_rss_mb": r["peak_tree_rss_mb"], "size": side["size"]}))


def test_determinism_same_recipe_identical_pixels(tmp_path):
    src = raw_file("high_iso")
    _, o1 = _develop(tmp_path, src, tag="1")
    _, o2 = _develop(tmp_path, src, tag="2")
    for name in ("full.jpg", "web.jpg"):
        a = np.asarray(Image.open(o1 / name))
        b = np.asarray(Image.open(o2 / name))
        assert np.array_equal(a, b), name
    import tifffile
    assert np.array_equal(tifffile.imread(str(o1 / "full.tif")), tifffile.imread(str(o2 / "full.tif")))


@pytest.mark.parametrize("role", ["high_iso", "sony_33mp"])
def test_memory_budget(role, tmp_path):
    src = raw_file(role)
    r, out = _develop(tmp_path, src, reframe="apply")
    w, h = json.loads((out / "sidecar.json").read_text())["size"]
    mp = w * h / 1e6
    budget = BUDGET_BASE_MB + BUDGET_PER_MP_MB * mp
    print(f"{role}: {mp:.1f} MP, peak tree RSS {r['peak_tree_rss_mb']} MB (budget {budget:.0f} MB)")
    assert r["peak_tree_rss_mb"] < budget
