#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible quality + performance benchmark of the Revelado develop pipeline on the CC0 test RAWs.

Needs: ``python scripts/fetch_test_raws.py`` first, rawtherapee-cli and exiftool installed. Runs ONE develop at a
time (each in its own process, as the app does) and writes JSON + Markdown into ``test-data/benchmarks/``.

What it measures
  1. Noise reduction (high-ISO files): develop with ``nr=off`` and ``nr=auto``; on the exposed neutral base
     (OkLab x100), residual std of L, a, b in the 16 flattest shadow/mid patches (plane-fit removed; same patches
     for both). Reported: before / after and % reduction.
  2. Chromatic aberration (sony_33mp, nikon, fuji): develop with ``ca=off`` and ``ca=auto``.
       * lateral CA: sub-pixel shift of R and B against G (phase correlation of high-passed channels) in 64 px
         tiles of the outer ring of the frame; reported as the 90th percentile shift in px.
       * fringe score: mean purple/green fringe energy in the high-contrast edge band (``oklab.fringe_map``) x1000.
  3. Performance: full chain (develop + film + web + TIFF16 + reframing) per stage seconds and PEAK RSS of the whole
     process tree (Python + rawtherapee-cli, sampled every 0.1 s) on the 33 MP Sony and on the high-ISO file, for
     the neutral default and the editorial Ektar preset.

    python scripts/benchmark_quality.py                 # everything (~10-15 min on a laptop)
    python scripts/benchmark_quality.py --only nr       # nr | ca | perf
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "engine"))

import numpy as np  # noqa: E402

from revelado import __version__, recipe  # noqa: E402
from revelado.bench import run_worker  # noqa: E402
from revelado.tools import find_tool  # noqa: E402

DATA = Path(os.environ.get("REVELADO_TEST_DATA") or REPO / "test-data")
MANIFEST = json.loads((REPO / "engine" / "tests" / "data" / "test_raws.json").read_text())["files"]


def raw(role: str) -> Path:
    f = next(f for f in MANIFEST if f["role"] == role)
    p = DATA / f["filename"]
    if not p.exists():
        sys.exit(f"falta {p.name}: python scripts/fetch_test_raws.py")
    return p


def develop_base(src: Path, work: Path, **rcp_over) -> tuple[np.ndarray, float, dict]:
    import tifffile
    rcp = recipe.normalise(rcp_over)
    ep, _ = recipe.to_engine(rcp)
    out = work / f"base_{src.stem}_{rcp['nr']}_{rcp['ca']}"
    r = run_worker("revelado.worker", {"source": "raw", "raw_path": str(src), "work_dir": str(work / "w"),
                                       "out_dir": str(out), "engine_params": ep, "recipe": rcp,
                                       "stop_after": "develop"})
    if not r["ok"]:
        sys.exit(f"develop falló: {r.get('error')}")
    img = tifffile.imread(str(out / "base.tif")).astype(np.float32)
    side = json.loads((out / "sidecar.json").read_text())
    for f in out.iterdir():
        f.unlink()
    out.rmdir()
    return img, float(r["ev"]), side


# ------------------------------------------------------------------------------------------------ 1. noise
def bench_nr(work: Path) -> list[dict]:
    from revelado.pipeline.oklab import chroma_noise, oklab, shadow_patches
    res = []
    for role in ("high_iso", "high_iso_cr3"):
        src = raw(role)
        off, ev, _ = develop_base(src, work, nr="off")
        on, ev2, side = develop_base(src, work, nr="auto")
        g = np.float32(2.0 ** ev)
        lab_on, lab_off = oklab(on * g), oklab(off * g)
        del on, off
        patches = shadow_patches(lab_on, n=16, size=48, lmax=0.62, lmin=0.08)
        a, b = chroma_noise(lab_off, patches), chroma_noise(lab_on, patches)
        red = {k: round(100 * (1 - b[k] / a[k]), 1) for k in ("std_L", "std_a", "std_b") if a[k] > 0}
        iso = next(f["iso"] for f in MANIFEST if f["role"] == role)
        res.append({"file": src.name, "iso": iso, "patches": a["n"], "nr_off_x100": a, "nr_auto_x100": b,
                    "reduction_pct": red, "nr_params": side["stages"]["develop"]["nr"]})
        print(f"NR  {src.name}: L {a['std_L']} -> {b['std_L']}  a {a['std_a']} -> {b['std_a']}  "
              f"b {a['std_b']} -> {b['std_b']}  ({red})", flush=True)
        del lab_on, lab_off
    return res


# ------------------------------------------------------------------------------------------------ 2. CA
def lateral_ca(img: np.ndarray, ev: float, tile: int = 64) -> dict:
    import cv2
    x = np.clip(img * np.float32(2.0 ** ev), 0, 4) ** (1 / 2.4)
    H, W = x.shape[:2]
    hp = [c - cv2.GaussianBlur(c, (0, 0), 3) for c in (x[..., 0], x[..., 1], x[..., 2])]
    win = cv2.createHanningWindow((tile, tile), cv2.CV_32F)
    shifts = {"r": [], "b": []}
    for ty in range(0, H - tile, tile):
        for tx in range(0, W - tile, tile):
            cx, cy = (tx + tile / 2 - W / 2) / (W / 2), (ty + tile / 2 - H / 2) / (H / 2)
            if max(abs(cx), abs(cy)) < 0.75:                 # outer ring of the frame only
                continue
            g = hp[1][ty:ty + tile, tx:tx + tile]
            if g.std() < 0.02:                                # needs structure to measure a shift
                continue
            for k, c in (("r", 0), ("b", 2)):
                (dx, dy), resp = cv2.phaseCorrelate(np.ascontiguousarray(g), np.ascontiguousarray(
                    hp[c][ty:ty + tile, tx:tx + tile]), win)
                if resp > 0.2 and abs(dx) < 4 and abs(dy) < 4:
                    shifts[k].append(float(np.hypot(dx, dy)))
    out = {"tiles": len(shifts["r"])}
    for k in ("r", "b"):
        out[f"{k}_shift_p90_px"] = round(float(np.percentile(shifts[k], 90)), 3) if shifts[k] else None
        out[f"{k}_shift_mean_px"] = round(float(np.mean(shifts[k])), 3) if shifts[k] else None
    return out


def fringe_score(img: np.ndarray, ev: float) -> float:
    from revelado.pipeline.oklab import fringe_map, oklab
    lab = oklab(img * np.float32(2.0 ** ev))
    f = fringe_map(lab)
    band = f > 0
    return round(float(f[band].mean() * 1000), 3) if band.any() else 0.0


def bench_ca(work: Path) -> list[dict]:
    res = []
    for role in ("sony_33mp", "nikon", "fuji"):
        src = raw(role)
        row = {"file": src.name}
        for mode in ("off", "auto"):
            img, ev, _ = develop_base(src, work, ca=mode)
            row[f"ca_{mode}"] = {"lateral": lateral_ca(img, ev), "fringe_x1000": fringe_score(img, ev)}
            del img
        print(f"CA  {src.name}: {json.dumps(row)}", flush=True)
        res.append(row)
    return res


# ------------------------------------------------------------------------------------------------ 3. perf
def bench_perf(work: Path) -> list[dict]:
    res = []
    for role in ("sony_33mp", "high_iso"):
        src = raw(role)
        for preset in ("neutral", "editorial_ektar"):
            rcp = recipe.normalise(dict(recipe.PRESETS[preset], reframe="apply"))
            ep, _ = recipe.to_engine(rcp)
            out = work / f"perf_{role}_{preset}"
            r = run_worker("revelado.worker", {"source": "raw", "raw_path": str(src), "work_dir": str(work / "w"),
                                               "out_dir": str(out), "engine_params": ep, "recipe": rcp,
                                               "tiff16": True})
            if not r["ok"]:
                sys.exit(f"render falló: {r.get('error')}")
            side = json.loads((out / "sidecar.json").read_text())
            row = {"file": src.name, "preset": preset, "size": side["size"],
                   "megapixels": round(side["size"][0] * side["size"][1] / 1e6, 1),
                   "stage_seconds": side["times"], "render_breakdown_s": side["stages"]["render"].get("timings"),
                   "wall_s": r["wall_s"], "peak_tree_rss_mb": r["peak_tree_rss_mb"],
                   "face_detector": side["stages"]["render"].get("face_detector")}
            print(f"PERF {src.name} {preset}: {row['wall_s']} s, pico {row['peak_tree_rss_mb']} MB, "
                  f"{row['stage_seconds']}", flush=True)
            res.append(row)
            for f in out.iterdir():
                f.unlink()
            out.rmdir()
    return res


def to_markdown(rep: dict) -> str:
    L = [f"# Revelado benchmark ({rep['date']})", "",
         f"Engine {rep['version']} · {rep['machine']} · RawTherapee `{rep['rawtherapee']}` · "
         "files: CC0 RAWs from raw.pixls.us (see engine/tests/data/SOURCES.md)", ""]
    if rep.get("nr"):
        L += ["## Noise reduction (OkLab ×100, 16 flattest shadow/mid patches)", "",
              "| File | ISO | L off → auto | a off → auto | b off → auto | reduction L / a / b |", "|---|---|---|---|---|---|"]
        for r in rep["nr"]:
            a, b, d = r["nr_off_x100"], r["nr_auto_x100"], r["reduction_pct"]
            L.append(f"| {r['file']} | {r['iso']} | {a['std_L']} → {b['std_L']} | {a['std_a']} → {b['std_a']} | "
                     f"{a['std_b']} → {b['std_b']} | {d.get('std_L')} % / {d.get('std_a')} % / {d.get('std_b')} % |")
        L.append("")
    if rep.get("ca"):
        L += ["## Chromatic aberration (outer ring of the frame)", "",
              "| File | R shift p90 off → auto (px) | B shift p90 off → auto (px) | fringe ×1000 off → auto |",
              "|---|---|---|---|"]
        for r in rep["ca"]:
            o, a = r["ca_off"], r["ca_auto"]
            L.append(f"| {r['file']} | {o['lateral']['r_shift_p90_px']} → {a['lateral']['r_shift_p90_px']} | "
                     f"{o['lateral']['b_shift_p90_px']} → {a['lateral']['b_shift_p90_px']} | "
                     f"{o['fringe_x1000']} → {a['fringe_x1000']} |")
        L.append("")
    if rep.get("perf"):
        L += ["## Performance (full chain incl. web, TIFF16, reframing; one photo, own process)", "",
              "| File | MP | Preset | exposure | develop | render | reframe | total | peak RSS (tree) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in rep["perf"]:
            s = r["stage_seconds"]
            L.append(f"| {r['file']} | {r['megapixels']} | {r['preset']} | {s.get('exposure')} s | {s.get('develop')} s | "
                     f"{s.get('render')} s | {s.get('reframe')} s | {s.get('total')} s | {r['peak_tree_rss_mb']} MB |")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", choices=["nr", "ca", "perf"])
    ap.add_argument("--out", default=str(DATA / "benchmarks"))
    a = ap.parse_args(argv)
    if not find_tool("rawtherapee-cli"):
        sys.exit("rawtherapee-cli no encontrado")
    parts = a.only or ["nr", "ca", "perf"]
    import subprocess
    rt = subprocess.run([find_tool("rawtherapee-cli"), "-v"], capture_output=True, text=True).stdout.splitlines()
    rep = {"date": time.strftime("%Y-%m-%d %H:%M"), "version": __version__,
           "machine": f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPU",
           "rawtherapee": (rt[0] if rt else "?").strip()}
    try:
        import psutil
        rep["machine"] += f", {psutil.virtual_memory().total / 2**30:.0f} GB RAM"
    except ImportError:
        pass
    with tempfile.TemporaryDirectory(prefix="revelado-bench-") as td:
        work = Path(td)
        if "nr" in parts:
            rep["nr"] = bench_nr(work)
        if "ca" in parts:
            rep["ca"] = bench_ca(work)
        if "perf" in parts:
            rep["perf"] = bench_perf(work)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    (out / f"benchmark_{stamp}.json").write_text(json.dumps(rep, indent=1))
    md = to_markdown(rep)
    (out / f"benchmark_{stamp}.md").write_text(md)
    print("\n" + md)
    print(f"-> {out / f'benchmark_{stamp}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
