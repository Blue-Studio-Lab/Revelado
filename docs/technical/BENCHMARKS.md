# Benchmarks

> **Resumen (ES).** Aquí están todas las cifras medidas que cita la documentación técnica, con su definición exacta, la máquina y los archivos usados. Se midieron con `scripts/benchmark_quality.py` sobre RAW públicos CC0 (nunca fotos de clientes). Lo que todavía no se ha medido aparece como *not yet measured*; no se rellena con estimaciones. Los resultados varían según el equipo.

All measured numbers in the technical docs come from this page. Each one was printed by `scripts/benchmark_quality.py` and is kept verbatim in [`benchmark-results/benchmark_20261117-1022.json`](benchmark-results/benchmark_20261117-1022.json).

## Run

| | |
|---|---|
| Date | 2026-11-17 |
| Engine | `revelado` 0.2.0.dev0 |
| Machine | Apple Silicon (M-series), macOS (Darwin arm64), 10 CPU cores, 16 GB RAM |
| RAW developer | RawTherapee 5.13 (`rawtherapee-cli`) |
| Fixtures | CC0 RAW files from [raw.pixls.us](https://raw.pixls.us), pinned by sha256; see [SOURCES.md](../../engine/tests/data/SOURCES.md) |
| Mode | One develop at a time, each in its own process (as in the app); default settings (`REVELADO_DETERMINISTIC` defaults to 1; the JSON does not record the environment) |

**Results vary** with hardware, operating system, RawTherapee version and image content. Each configuration was run once; these are single measurements, not medians. The whole run (14 develops) took about 5 minutes: the report is stamped 10:17 at the start and was written at 10:22.

## Rules

- A number is published only if the benchmark script printed it on **public, licensed fixtures**. Client photos and third-party film scans are never used.
- Every number keeps its definition, fixture, engine version and machine (this page plus the JSON file).
- When a metric has not been measured, the docs say *not yet measured*. If a number cannot be reproduced, the sentence is removed rather than filled with an estimate.
- Constants quoted from the code (thresholds, radii, defaults) are design parameters, not measurements.

## How to reproduce

```bash
python scripts/fetch_test_raws.py               # ~150 MB of CC0 RAWs into test-data/, sha256-verified
python scripts/benchmark_quality.py             # everything (about 5 min on the machine above)
python scripts/benchmark_quality.py --only nr   # or: ca | perf
```

The script writes `test-data/benchmarks/benchmark_<date>.json` and `.md` (the `test-data/` folder is gitignored). To publish a new run, copy the JSON into [`benchmark-results/`](benchmark-results/) and update the tables below and the pages that cite them.

## 1. Noise reduction

**Method.** Each file is developed twice up to the linear base (`stop_after="develop"`): once with `nr=off` and once with `nr=auto`. The base is exposed with the automatic EV and converted to OkLab. The 16 flattest patches (48 px) with L between 0.08 and 0.62 are picked on the `auto` result and used for both runs. Inside each patch a plane is fitted and removed, and the residual standard deviation of L, a and b (×100) is averaged over the patches.

`nr=off` turns off RawTherapee's luma denoise and the Python clean stage. RawTherapee's automatic chroma denoise stays enabled in both runs, so the reduction shown is the extra effect of the `auto` dose.

| File | ISO | L σ off → auto | a σ off → auto | b σ off → auto | Reduction L / a / b | Dose chosen |
|---|---:|---|---|---|---|---|
| `sony_ilme-fx3_iso20000.arw` | 20000 | 3.772 → 0.867 | 0.528 → 0.023 | 0.772 → 0.021 | 77.0 % / 95.6 % / 97.3 % | noise_stops 8.53, level 1.0, RT luma 33.2, ldetail 63.4 |
| `canon_eos-r50_iso32000.cr3` | 32000 | 10.826 → 5.197 | 5.531 → 0.319 | 3.530 → 0.274 | 52.0 % / 94.2 % / 92.2 % | noise_stops 8.32, level 1.0, RT luma 31.9, ldetail 64.0 |

Combined chroma $\sqrt{\sigma_a^2+\sigma_b^2}$, computed from the mean σ<sub>a</sub> and σ<sub>b</sub> above: **0.935 → 0.031** (ILME-FX3) and **6.561 → 0.421** (EOS R50).

## 2. Chromatic aberration

**Method.** Each file is developed twice up to the linear base, with `ca=off` (no raw CA correction, no defringe) and `ca=auto`.

- **Lateral CA.** The exposed base is gamma-encoded (1/2.4) and high-passed. In 64 px tiles of the outer ring of the frame (tile centre beyond 75 % of the half-width or half-height) that have enough structure, R and B are phase-correlated against G under a Hann window. The shift magnitude is in pixels of the developed image. Reported: 90th percentile (p90) and mean.
- **Fringe score.** Mean purple / green-cyan chroma inside a band around high-contrast edges (`oklab.fringe_map`), ×1000.

| File | Tiles (auto) | R shift p90 off → auto (px) | B shift p90 off → auto (px) | R / B shift mean, auto (px) | Fringe ×1000 off → auto |
|---|---:|---|---|---|---|
| `sony_ilce-7m4_iso250.arw` | 2427 | 0.048 → 0.040 | 0.122 → 0.087 | 0.021 / 0.044 | 19.401 → 18.420 |
| `nikon_z6_iso100.nef` | 227 | 0.027 → 0.023 | 0.134 → 0.039 | 0.013 / 0.022 | 25.659 → 25.242 |
| `fujifilm_x-t3_iso160.raf` (X-Trans) | 77 | 0.679 → 0.679 | 2.692 → 2.692 | 0.425 / 1.472 | 28.155 → 28.092 |

RawTherapee's raw CA correction works on Bayer data, so the X-Trans file is unchanged: **lateral CA is not corrected on X-Trans today.** The fringe score on real files moves little because these frames contain few strong axial fringes; the synthetic chart in `test_defringe_removes_purple_fringe_but_keeps_purple_object` (fringe chroma must drop below 60 %, the purple object must stay) is the better check for the defringe, and it has not been turned into a published number yet.

## 3. Performance and memory

**Method.** Full chain per photo, each in its own process: develop, render, web JPEG, 16-bit TIFF and reframing (`reframe=apply`), for the default `neutral` preset and the `editorial_ektar` preset. Peak RSS is that of the whole process tree (Python + `rawtherapee-cli`), sampled every 0.1 s. "Output MP" is the image size after reframing.

| File | Sensor | Output MP | Preset | Exposure | Develop | Hair | Render | Reframe | Total | Wall | Peak RSS (tree) | Face detector |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `sony_ilce-7m4_iso250.arw` | 33 MP Bayer | 32.7 | neutral | 0.50 s | 21.82 s | 0 s (off) | 8.68 s | 2.38 s | 35.42 s | 35.57 s | 4148 MB | none |
| `sony_ilce-7m4_iso250.arw` | 33 MP Bayer | 32.7 | editorial_ektar | 0.51 s | 25.16 s | 0 s (off) | 32.49 s | 2.86 s | 63.38 s | 63.61 s | 3563 MB | Vision |
| `sony_ilme-fx3_iso20000.arw` | 12 MP Bayer | 9.6 | neutral | 0.31 s | 6.68 s | 0 s (off) | 2.69 s | 2.56 s | 14.10 s | 14.24 s | 2812 MB | none |
| `sony_ilme-fx3_iso20000.arw` | 12 MP Bayer | 12.0 | editorial_ektar | 0.30 s | 6.90 s | 0 s (off) | 10.75 s | 1.71 s | 21.39 s | 21.53 s | 2782 MB | Vision |

"Develop" is RawTherapee plus the Python clean stage and the distortion correction. "Total" is measured inside the child; "Wall" includes process start-up.

**Render breakdown** (seconds, as reported by the renderer, rounded to 0.1 s):

| Step | 7M4 neutral | 7M4 Ektar | FX3 neutral | FX3 Ektar |
|---|---:|---:|---:|---:|
| Subject exposure | — | 2.2 | — | 1.5 |
| Negative (MTF, halation, layers, H&D, DIR, adjacency) | — | 3.3 | — | 0.8 |
| Grain (fields + application) | — | 3.6 | — | 1.0 |
| Scan (print curve, levels) | — | 8.2 | — | 3.0 |
| Colour (OkLCh stage) | — | 3.5 | — | 1.0 |
| Neutral tone curve | 1.1 | — | 0.4 | — |
| Face detection | 0.0 | 0.0 | 0.0 | 0.0 |
| Subject accent | — | 0.6 | — | 0.2 |
| Residual NR | 1.0 | 0.8 | 0.2 | 0.2 |
| Skin | 0.2 | 2.4 | 0.1 | 0.5 |
| Sharpening | 1.1 | 1.5 | 0.3 | 0.3 |
| Blemishes | 0.0 | 0.0 | 0.0 | 0.0 |
| Gamut mapping | 1.4 | 2.0 | 0.4 | 0.7 |
| Web render | — | 0.6 | — | 0.4 |
| Dither + encode (JPEG + TIFF16) | 3.4 | 3.0 | 1.0 | 1.0 |

The heavy tests use a peak-memory budget of 1200 MB + 190 MB per megapixel for the process tree (`engine/tests/test_heavy_raw.py`).

## 4. Key index

Each value cited by the technical docs, where it is cited, and its status in this run.

| Key | Doc | Definition | Value (this run) |
|---|---|---|---|
| `noise_reduction.luma_shadow_before` / `_after` | [noise-reduction](noise-reduction.md) | L σ (OkLab ×100), 16 flattest patches with L 0.08–0.62, `nr=off` → `auto` (§1) | ILME-FX3 ISO 20000: 3.772 → 0.867 · EOS R50 ISO 32000: 10.826 → 5.197 |
| `noise_reduction.chroma_before` / `_after` | noise-reduction | $\sqrt{\sigma_a^2+\sigma_b^2}$ ×100 from the same patches (§1) | ILME-FX3: 0.935 → 0.031 · EOS R50: 6.561 → 0.421 |
| `noise_reduction.texture_retained` | noise-reduction | High-pass energy after / before on textured tiles | not yet measured |
| `noise_reduction.seconds_24mp` | noise-reduction | Wall time of `clean()` alone at 24 MP | not yet measured (included in "Develop", §3) |
| `ca.lateral_residual_px` | [chromatic-aberration](chromatic-aberration.md) | R / B shift magnitude vs G, outer ring, after `ca=auto` (§2) | mean R / B: 0.021 / 0.044 px (ILCE-7M4), 0.013 / 0.022 px (Z 6), 0.425 / 1.472 px (X-T3, not corrected) |
| `ca.defringe_removed_pct` | chromatic-aberration | Share of injected fringe chroma removed on the synthetic chart | not yet measured |
| `ca.purple_object_dC` | chromatic-aberration | Mean chroma change on the synthetic purple-object chart | not yet measured |
| `exposure.skin_L_spread_before` / `_after` | [exposure-log-profiles](exposure-log-profiles.md) | Lit-skin L spread across a public portrait set | not yet measured |
| `exposure.capped_pct` | exposure-log-profiles | Share of frames with `limited_by_highlights` | not yet measured |
| `exposure.seconds_per_frame` | exposure-log-profiles | Wall time of `analyze_exposure` per RAW (§3, "Exposure") | 0.50–0.51 s (33 MP), 0.30–0.31 s (12 MP) |
| `film.neutral_ramp_max_dev` | [film-model](film-model.md) | Max deviation of the grey ramp from the target curve, grain off | not yet measured |
| `film.neutral_ramp_max_chroma` | film-model | Max OkLCh C on the grey ramp, −3…+3 EV, grain off | not yet measured |
| `film.grain_sigma_mid` / `_hi` / `_black` | film-model | Grain σ in output codes at levels ≈ 100 / 225 / 16 | not yet measured |
| `film.grain_acf_1px` / `_2px` | film-model | Grain autocorrelation at 1 / 2 px | not yet measured |
| `film.seconds_24mp` | film-model | Film render time (§3, "Render", `editorial_ektar`) | 32.49 s at 32.7 MP, 10.75 s at 12.0 MP (24 MP not measured) |
| `retouch.blemishes_per_face` | [retouch](retouch.md) | Median healed spots per face, public portrait set | not yet measured |
| `retouch.false_heals` | retouch | Heals on protected features, manual audit | not yet measured |
| `retouch.texture_retained` | retouch | High-pass energy after / before in the skin mask | not yet measured |
| `hair.clearly_cleaner_pct` | [hair](hair.md) | Share clearly cleaner without manual help (blind A/B) | not yet measured |
| `hair.needs_manual_pct` | hair | Share needing manual boxes | not yet measured |
| `hair.gate_refused_pct` | hair | Share refused by the safety gate | not yet measured |
| `hair.peak_rss_gb` | hair | Peak RSS of the hair stage at 33 MP | not yet measured (hair was off in this run) |
| `reframe.uncropped_pct` | [reframing](reframing.md) | Share of frames left uncropped | not yet measured |
| `reframe.straightened_pct` | reframing | Share of frames rotated | not yet measured |
| `reframe.violations` | reframing | Heads, hands or joints cut, manual audit | not yet measured |
| `determinism.same_machine_max_diff` | [determinism-and-recipes](determinism-and-recipes.md) | Max 8-bit difference between two renders of the same recipe | not measured by this benchmark; the test suite asserts identical output (see the doc) |
| `determinism.cross_os_max_diff` | determinism-and-recipes | Max difference between macOS, Linux and Windows renders | not yet measured |
| `memory.peak_rss_24mp_gb` | [memory-scheduling](memory-scheduling.md) | Peak RSS of the process tree, default recipe (§3) | 24 MP not measured; 12 MP: 2.81 GB · 33 MP: 4.15 GB |
| `memory.peak_rss_33mp_gb` | memory-scheduling | Peak RSS of the process tree at 33 MP (§3) | 4.15 GB neutral, 3.56 GB editorial Ektar (hair off) |
| `memory.seconds_per_photo_24mp` | memory-scheduling | End-to-end seconds per photo (§3, "Total") | 24 MP not measured; 33 MP: 35.4 s / 63.4 s · 12 MP sensor: 14.1 s / 21.4 s (neutral / Ektar) |

Release check: the docs contain no benchmark placeholders any more (every old double-brace `BENCH:` marker was replaced by a measured value or by *not yet measured*). Rows marked *not yet measured* are filled in when the benchmark script grows the matching measurement.
