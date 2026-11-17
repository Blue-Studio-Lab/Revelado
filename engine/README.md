# Revelado engine

Local Python engine behind the Revelado app (`pip` distribution **`revelado`**, import package `revelado`, CLI
`revelado`). It listens on 127.0.0.1:8790 and implements the v0.1 HTTP API contract (`/api/...`, unchanged paths).
It runs on macOS, Windows and Linux from source and needs **no private files**: the whole develop pipeline is bundled.

## Run

```bash
pip install -e 'engine[ai,dev]'         # Python >= 3.12
cp engine/.env.example engine/.env      # optional: local settings (gitignored)
revelado                                # or: engine/run_dev.sh (dev mode, token "dev" accepted)
```

External tools (found on `PATH` or in the usual install folders of each OS; override with `REVELADO_EXIFTOOL`,
`REVELADO_RAWTHERAPEE_CLI`):

| Tool | Needed for |
|---|---|
| `exiftool` | reading metadata (library), copying EXIF to outputs |
| `rawtherapee-cli` (RawTherapee ≥ 5.10) | developing RAW files |
| `jpegtran` (optional) | lossless crops when reframing without rotation |
| `swiftc` (optional, macOS) | builds the Apple Vision helpers on first use (better faces/landmarks, person matte) |

Face detection: macOS Vision helper (optional) → OpenCV **YuNet** (MIT model, downloaded once from the OpenCV Zoo
and verified by sha256) → OpenCV Haar cascades (downloaded the same way). `REVELADO_FACE_BACKEND=auto|vision|yunet|haar`,
`REVELADO_OFFLINE=1` disables downloads.

## Contract notes (v0.1 + this release)

- **Token**: random per start, written to `engine/.runtime_token` **and** to the per-user state folder (mode 0600):
  macOS `~/Library/Application Support/Revelado/runtime_token`, Windows `%LOCALAPPDATA%\Revelado\runtime_token`,
  Linux `~/.local/state/revelado/runtime_token` (`REVELADO_STATE_DIR` overrides). Send it as `X-Revelado-Token`
  or `?token=`. Dev mode also accepts `dev`.
- **Env vars** are `REVELADO_*` (no other prefix is read).
- `GET /api/health` → `capabilities: {develop_jpg, tiff16_rotation, raw_only_ok, jpg_only_ok}` (all `true`) and
  `develop_engine: {available, engine: "native"|"legacy", raw, reason, films}`.
- `GET /api/photos` → each photo has `has_raw`, `has_jpg`, `can_develop` and `develop_block_reason`
  (`"needs_raw"` | `null`).
- **Develop policy**: develop needs the RAW. A JPG-only photo is refused with `422 E_NEEDS_RAW`
  ("Se necesita el RAW…", `error.reason = "needs_raw"`, `error.photo_id`) unless the recipe explicitly sets
  `develop_source: "jpg"` (develops from the camera JPEG: no RAW NR/CA, lower quality, never chosen silently).
  RAW-only folders work: thumbnails/previews come from the RAW's embedded JPEG.
- **Recipe**: the default is **neutral** (`film: "none"`, no grain, no retouch, no reframing). `GET /api/recipe/presets`
  lists `neutral` and `editorial_ektar` (Ektar 100-inspired film emulation + editorial retouch, the former default; approximate, not affiliated with Kodak, see docs/technical/film-model.md). `nr`
  (`auto|soft|strong|off`) and `ca` (`auto|off`) are now applied. `hair` is **experimental**, `off` by default,
  macOS-only today (needs the Vision person matte) and always behind its safety gate.
- **Batch with per-photo overrides**: `POST /api/develop/batch` accepts
  `overrides: {"<photo_id>": {partial recipe}}`, merged over the batch `recipe` for that photo only (e.g. skin
  retouch only on the portrait). The response lists the resolved recipes of the overridden photos; the job's
  `result.recipes` has the resolved recipe of EVERY photo, and each output's `sidecar.json` stores the recipe used.
  An override for a photo not in `photo_ids` is a `400 E_BAD_INPUT`.
- **Previews without a camera JPG**: thumbnails, previews (the Develop "before") and 1:1 crops of RAW-only photos
  come from the RAW's embedded JPEG; when it is missing or smaller than 1600 px, a quick neutral LibRaw render is
  used instead. `GET /api/photos/{id}/preview` reports which in `X-Preview-Source: camera_jpg | embedded | quick_render`.
- **TIFF16 + reframing**: the 16-bit TIFF now receives exactly the same rotation + crop as the JPG (Lanczos in
  16 bit).

## Pipeline (`revelado/pipeline`)

| Stage | Module | Notes |
|---|---|---|
| Exposure analysis | `raw.analyze_exposure` | half-res superpixel from the RAW; subject (AF point when the maker notes have it) + centre-weighted key, highlight cap |
| RAW develop | `raw.develop` | rawtherapee-cli: AMaZE, raw lateral CA (auto ×3), highlight reconstruction, capture sharpening, pyramid NR dosed by ISO + push; crop to the camera's default crop; Sony embedded distortion |
| Clean-up | `clean.clean` | noise measured in the image; axial CA defringe (purple/green next to highlights, protects real purple/green objects); despeckle; luma NL-means by tone; chroma NR with L-guided filter + coarse joint bilateral |
| Film | `film/ektar.py`, `film/neutral.py` | physical Kodak Ektar 100 model (negative H&D curves, DIR couplers, halation, grain clouds, scanner) with the spec bundled as data; neutral render for `film: "none"` |
| Retouch | `film/ektar.finish`, `blemish.py` | residual NR, skin (frequency separation, protected features), blemish healing, halo-free sharpening |
| Hair (experimental) | `hair.py` | v4.2 + safety gate; off by default |
| Framing | `reframe.py` | straighten + thirds/phi composition, never cuts heads/hands/joints (pose needs macOS Vision); JPG, web and TIFF16 get the same geometry |
| Faces | `faces.py`, `assets.py` | Vision (optional) / YuNet / Haar behind one interface; pinned model downloads |

**Determinism**: same file + same recipe → identical pixels (tested). rawtherapee-cli is run single-threaded for
that (its multithreaded stages differ run-to-run by ~1e-4 on ~0.02 % of pixels); `REVELADO_DETERMINISTIC=0` trades
reproducibility for ~5 s less per 33 MP photo. Grain seeds come from the source file **content** (renaming a file changes nothing; same file + same recipe → identical
pixels). Spatial parameters scale with the frame (`REF_LONG_SIDE_PX` = a 36 mm frame at 5.14 µm/px), not with a
fixed camera size.

Develop jobs run ONE at a time, each photo in its own process (`revelado.worker`) with admission control (free RAM /
disk) and a watchdog on the RSS of the whole process tree (`REVELADO_HEAVY_RSS_CAP_GB`).

`REVELADO_DEVELOP_ENGINE=legacy` + `REVELADO_LEGACY_ENGINE_DIR` select the maintainer's private reference engine
instead (compare path only; never needed).

## Tests

```bash
python scripts/fetch_test_raws.py                 # CC0 RAWs from raw.pixls.us -> test-data/ (sha256-pinned)
cd engine && python -m pytest -q                  # fast suite (no heavy image processing, no network)
python -m pytest -m heavy -k determinism          # heavy tests: run ONE at a time
python -m pytest -m heavy -k "develop_each_format and nikon"
python -m pytest -m heavy -k memory_budget
python ../scripts/benchmark_quality.py            # NR / CA / timing / memory figures for the docs
```

The private golden test (`tests/test_private_golden.py`) compares the bundled pipeline with the private reference
engine; it is skipped unless `REVELADO_PRIVATE_GOLDEN_DIR` and `REVELADO_LEGACY_ENGINE_DIR` are set.

## Not yet

- Film stocks other than `kodak_ektar_100` (and `none`) return `E_FILM_UNTRAINED`.
- Lens distortion correction exists only for Sony's embedded parameters (Lensfun: planned).
- Hair clean-up and joint/hand-aware framing need the macOS Vision helpers; other systems get a safe fallback.
- Claude story assembly (stage 4) and Message Batches are not implemented.
