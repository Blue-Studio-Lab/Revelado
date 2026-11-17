# SPDX-License-Identifier: GPL-3.0-or-later
"""PRIVATE golden test: bundled pipeline vs the private reference engine on the maintainer's own RAW files.

Skipped unless both are set (nothing private is in the repository):
  REVELADO_PRIVATE_GOLDEN_DIR   folder with a few RAW files (the first 3 in name order are used)
  REVELADO_LEGACY_ENGINE_DIR    the private reference engine (read-only)
Both engines run the same recipe ("editorial_ektar", reframing and hair off) with the SAME grain seed (the reference
engine's name-based seed), one photo at a time, each in its own process. Compared on the final full-resolution JPEG,
area-downscaled to 2400 px (grain-insensitive): ΔE00 mean / p95 and luma SSIM.

Run:  pytest -m private_golden -s tests/test_private_golden.py   (heavy: ~6 full renders)

EXPECTED DIFFERENCES vs the reference engine (intentional fix, thresholds NOT relaxed): the finish stage is now
local to detected people (see tests/test_finish_locality.py). The reference engine (1) measured the lab "cast" on
the median of ALL midtones, so content colour (green foliage, grass) became global magenta/blue gains; (2) with no
person, pushed the frame median to L 0.56 (up to +1.75 EV); (3) used "skin by colour" as subject / skin mask when
no person was found. Photos where none of this fired (the 3 golden photos: ΔE00 mean <= 0.003) are unchanged.
Photos where it fired differ by design, e.g. a portrait in front of green palms (1 face): ΔE00 mean 6.4 / p95 11.0
vs the old output (5.6 from removing the false magenta cast, the rest from the face exposure no longer being skewed
by it); no-person photos: a white dog on foliage ΔE00 16.7, a beach skyline 0.33. If a future golden photo trips the
thresholds for this reason, document it here with its numbers rather than widening MAX_DE00_*.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

GOLDEN = os.environ.get("REVELADO_PRIVATE_GOLDEN_DIR")
LEGACY = os.environ.get("REVELADO_LEGACY_ENGINE_DIR")
RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".raf", ".dng", ".orf", ".rw2"}

# thresholds (full-res render compared at 2400 px): same pipeline, only porting/refactor differences allowed
MAX_DE00_MEAN = 1.0
MAX_DE00_P95 = 2.5
MIN_SSIM = 0.98

pytestmark = [pytest.mark.private_golden, pytest.mark.heavy,
              pytest.mark.skipif(not (GOLDEN and LEGACY), reason="REVELADO_PRIVATE_GOLDEN_DIR / LEGACY no definidos")]


def _raws():
    return sorted(p for p in Path(GOLDEN).iterdir() if p.suffix.lower() in RAW_EXT)[:3] if GOLDEN else []


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_ported_matches_reference(idx, tmp_path):
    from revelado import recipe
    from revelado.bench import compare_images, run_worker
    from revelado.pipeline.film.ektar import seed_for
    raws = _raws()
    if idx >= len(raws):
        pytest.skip("menos de 3 RAW en la carpeta golden")
    raw = raws[idx]
    rcp = recipe.normalise(dict(recipe.PRESETS["editorial_ektar"], reframe="off", hair="off"))
    lp, _ = recipe.to_legacy(rcp)
    ep, _ = recipe.to_engine(rcp)
    leg = run_worker("revelado.legacy_worker", {
        "legacy_dir": LEGACY, "raw_path": str(raw), "stem": raw.stem, "work_dir": str(tmp_path / "lw"),
        "out_dir": str(tmp_path / "legacy"), "legacy_params": lp, "recipe": rcp, "tiff16": False})
    assert leg["ok"], leg.get("error")
    nat = run_worker("revelado.worker", {
        "source": "raw", "raw_path": str(raw), "stem": raw.stem, "work_dir": str(tmp_path / "nw"),
        "out_dir": str(tmp_path / "native"), "engine_params": ep, "recipe": rcp, "tiff16": False,
        "seed": seed_for(raw.stem)}, env_extra={"REVELADO_NO_VISION": "0", "REVELADO_OFFLINE": "0"})
    assert nat["ok"], nat.get("error")
    m = compare_images(tmp_path / "legacy" / "full.jpg", tmp_path / "native" / "full.jpg")
    mw = compare_images(tmp_path / "legacy" / "web.jpg", tmp_path / "native" / "web.jpg", long_side=None)
    sc_l = json.loads((tmp_path / "legacy" / "sidecar.json").read_text())
    sc_n = json.loads((tmp_path / "native" / "sidecar.json").read_text())
    report = {"photo": f"golden_{idx + 1}", "full": m, "web": mw,
              "ev": [sc_l.get("ev_auto"), sc_n.get("ev_auto")],
              "legacy": {"wall_s": leg["wall_s"], "peak_mb": leg["peak_tree_rss_mb"], "times": leg.get("times")},
              "native": {"wall_s": nat["wall_s"], "peak_mb": nat["peak_tree_rss_mb"], "times": nat.get("times")}}
    print("GOLDEN " + json.dumps(report))
    out = os.environ.get("REVELADO_GOLDEN_REPORT")
    if out:
        with open(out, "a") as fh:
            fh.write(json.dumps(report) + "\n")
    assert m["de00_mean"] <= MAX_DE00_MEAN and m["de00_p95"] <= MAX_DE00_P95 and m["ssim"] >= MIN_SSIM, m
