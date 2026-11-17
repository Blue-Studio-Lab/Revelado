# Determinism and recipes

> **Resumen (ES).** Todo ajuste es una **receta**: un archivo de datos (JSON/YAML), nunca código. La misma receta, aplicada al mismo RAW con la misma versión del motor, da los mismos píxeles en la app, en la línea de comandos y en scripts. El grano usa una semilla que sale del **contenido** de la imagen, así que renombrar el archivo no cambia nada. Los valores automáticos (exposición, dosis de ruido, caras, encuadre) se calculan con código determinista y quedan registrados en el sidecar de cada foto, que se guarda junto a la salida. Hoy un nuevo revelado los vuelve a calcular (con el mismo resultado); reutilizarlos desde el sidecar está planificado.

## 1. The recipe

A recipe is a flat, validated document. It has 13 fields: `film`, `film_strength`, `exposure_ev`, `grain`, `skin_retouch`, `blemishes`, `hair`, `accent`, `reframe`, `sharpen`, `nr`, `ca` and `develop_source` ([`recipe.py`](../../engine/revelado/recipe.py)). Numbers are clamped to their documented range and enum values are validated. Unknown keys are **accepted and reported as ignored**, never silently pretended. The schema (ranges, defaults and labels in ES/EN) is served by the engine (`GET /api/recipe/schema`), so the web UI and the MCP server use one source of truth.

The default recipe is **Neutral**: no film, no retouch. Film looks are presets that you choose.

Resolution layers implemented today, from lowest to highest priority:

1. schema defaults (`recipe.DEFAULT`);
2. the chosen preset (`neutral` or `editorial_ektar`) and the recipe sent with the request;
3. per-photo overrides in a batch.

`recipe.normalise()` merges and validates them, and the resolved recipe of every photo is stored with its output. Planned, not implemented: film-recommended values, camera and lens recommendations applied to the recipe (today the profile recommendations are only shown), and automatic values frozen from a previous develop.

## 2. Sources of non-determinism, and how they are handled

| Source | Handling |
|---|---|
| Grain RNG | The seed is derived from the **content** of the source file: the first 32 bits of the sha256 of its bytes (`ektar.seed_from_file`). A caller can also pass an explicit seed. The same file always gives the same grain; renaming it changes nothing. |
| RawTherapee threads | With `REVELADO_DETERMINISTIC=1` (default) `rawtherapee-cli` runs with one OpenMP thread, because its multithreaded stages can differ slightly from run to run. Set it to `0` to trade reproducibility for speed. |
| Automatic measurements (exposure EV, auto levels, subject exposure) | Computed by deterministic code on every develop; the automatic EV, the highlight guard and the NR dose are recorded in the sidecar. A re-render recomputes them and gets the same values with the same file and engine version. Reading them back from the sidecar is planned. |
| Detections (faces, blemishes, reframe) | Recomputed on every develop by deterministic code; faces and reframe parameters are recorded in the sidecar. With the macOS Vision helper, detections depend on the OS version. |
| Multithreaded floating point (OpenCV, BLAS) | Results can differ in the last bits across CPUs and OSes. Byte-identical output is claimed only for the same machine, OS and tool versions (that is what the tests assert); cross-platform differences have not been measured. |
| External tools (RawTherapee, ExifTool) | A different RawTherapee version can change the base develop. Their versions are not yet recorded in the sidecar (see below). |

## 3. Sidecars

Every delivered photo gets a JSON sidecar with:

- the resolved recipe and the engine parameters derived from it;
- per-stage results: automatic EV, noise-reduction dose, clean-up statistics, lens correction, detected faces, reframe parameters;
- the grain seed;
- stage timings;
- peak memory;
- notes and anything left pending, such as the hair or reframe stage needing manual review.

Tool versions are not written to the sidecar yet; record the RawTherapee and ExifTool versions yourself when you need a long-term reproducible archive.

With the same source file, engine version and tool versions, the resolved recipe in the sidecar is enough to reproduce the image.

## 4. Scripting

The recipe is also the scripting interface. See the [Recipes and CLI](../en/manual/recipes-cli.md) chapter of the manual.

| Metric | Value |
|---|---|
| Re-render of the same recipe: max pixel difference, same machine | Not part of the benchmark run. Identical output is asserted by `test_render_is_deterministic` (film and neutral render, byte-identical JPEG, web JPEG and TIFF16; runs in CI) and by the heavy test `test_determinism_same_recipe_identical_pixels` (full RAW develop; run locally with `pytest -m heavy -k determinism`). |
| Max pixel difference across macOS / Linux / Windows CI | not yet measured |

See [BENCHMARKS.md](BENCHMARKS.md) for the measured numbers and how to reproduce them.
