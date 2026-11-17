# Technical documentation

> **Resumen (ES).** Esta sección explica *por qué* funciona cada parte del motor, con fórmulas y diagramas sacados del código real. Cada documento empieza con un resumen en español. Las cifras medidas salen del script de benchmarks sobre RAW públicos CC0 y están reunidas en [BENCHMARKS.md](BENCHMARKS.md). Lo que aún no se ha medido aparece como *not yet measured*.

Authors: Jorge Luis Peña Sanchez and Claude (Anthropic), co-author of the technical write-up.

| Document | What it explains |
|---|---|
| [Noise reduction](noise-reduction.md) | Dosing by ISO + exposure push, noise measured on the image, luma/chroma separation in OkLab, multi-scale chroma NR, despeckle, luma-to-target |
| [Chromatic aberration](chromatic-aberration.md) | Lateral CA corrected before demosaic and measured by phase correlation; axial defringe with protection of real purple/green objects |
| [Exposure for log profiles](exposure-log-profiles.md) | S-Log3 underexposure, subject + centre-weighted key, highlight cap, subject-driven exposure after the film |
| [Film model](film-model.md) | Layer sensitivity, H&D curves, DIR couplers, adjacency, grain in density space, scanner print curve and auto levels |
| [Retouch](retouch.md) | Frequency separation with edge protection, colour unification, scale-space blemish detection and healing |
| [Hair clean-up](hair.md) | Strand removal on smooth backgrounds, the safety gate, honest limits |
| [Reframing](reframing.md) | Straightening rules, thirds/φ composition, hard constraints, horizon pitfalls |
| [Determinism and recipes](determinism-and-recipes.md) | Recipes, content-derived seeds, frozen automatic values, sidecars |
| [Memory and scheduling](memory-scheduling.md) | One heavy job, a child process per photo, admission control, the RSS watchdog, the planned low-memory retry |
| [Benchmarks](BENCHMARKS.md) | Every measured number, its definition, the machine and fixtures, and how to reproduce it with `scripts/benchmark_quality.py` |

The whole pipeline in one picture, with the order of the stages and the technology behind each one, is in the [architecture overview](../ARCHITECTURE.md) and in the [README](../../README.md#how-it-works).

## Conventions

- **OkLab / OkLCh** (Björn Ottosson) is used throughout for perceptual work: L ≈ lightness 0–1, and a, b are opponent colour axes. Noise is expressed in "OkLab units × 100".
- **Scene-linear Rec.2020** is the working space between develop and film. A scale of 1.0 is green-channel sensor saturation, and exposure is applied as $\times 2^{ev}$.
- $\operatorname{smoothstep}(e_0, e_1, x)$ is the cubic Hermite step $t^2(3-2t)$ with $t = \operatorname{clip}((x-e_0)/(e_1-e_0), 0, 1)$. $G_\sigma$ is a Gaussian blur, and GF is OpenCV's guided filter.
- Numbers are only published when the benchmark script reproduces them on **public, licensed fixtures**. Constants quoted from the code are design parameters, not measurements.
