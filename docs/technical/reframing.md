# Reframing: thirds, φ and horizon pitfalls

> **Resumen (ES).** El reencuadre es conservador y no destructivo. Endereza solo inclinaciones reales (entre 0,3° y 5°) cuando las líneas están de acuerdo. Si son más de 5° o las líneas se contradicen, entiende que el ángulo es intencional y no lo toca. Coloca al sujeto (los ojos, la pareja como grupo, o la mano con el anillo en los detalles) en tercios o en líneas φ (0,382/0,618), deja aire hacia la mirada y evita cortar cabezas; con la pose de Vision de macOS también protege manos, pies y articulaciones (en otros sistemas solo hay caras y saliencia). Mantiene la proporción de la cámara y conserva al menos el 80 % del área. Si la foto ya está bien compuesta, la deja como está.

Source: [`engine/revelado/pipeline/reframe.py`](../../engine/revelado/pipeline/reframe.py).

## 1. Straightening

Line segments are detected with OpenCV's LSD detector on the web image, downscaled to 1280 px. People are masked out with the person matte (macOS Vision helper; without it no mask is applied), because arms and dresses are not horizons. Each candidate family votes for a tilt:

- near-horizontal lines: horizons, shorelines, piers;
- near-vertical lines: buildings, door frames.

A correction is applied only when **all** of these hold:

| Rule | Value | Why |
|---|---|---|
| tilt ≥ minimum | 0.3° | below that, the correction is invisible and still costs area |
| tilt ≤ maximum | 5° | larger angles are assumed intentional (Dutch angle) |
| agreement | MAD of the votes ≤ 0.45° | contradicting evidence means "do nothing" |

Rotation uses Lanczos resampling around the centre. Grain is already rendered at that point, so a rotation resamples it slightly. Crop-only results are lossless when `jpegtran` is installed and the crop origin falls on the 8 px JPEG block grid; otherwise the crop is re-encoded at q97 4:4:4.

### Horizon pitfalls (learned the hard way)

- **Curved shorelines.** A coast that bends toward the camera is not level, even if one section is. Use only the straight section, or nothing at all.
- **Car roofs, tables, railings seen in perspective** are not horizontal references.
- **Posts and verticals that disagree with the horizon.** Perspective and lens distortion make verticals converge. If verticals and the horizon disagree, do not rotate.
- **Window grids with low agreement:** abstain.
- **Lens distortion.** Barrel distortion bends straight lines near the edges. Correct distortion first; the develop stage does this.

## 2. Composition

The subject is, in order of preference:

1. the **eyes** of the main faces (a couple counts as one group);
2. **people** boxes;
3. for detail shots, a **hand or ring**, found through hand pose and saliency.

The crop optimiser minimises a weighted cost over candidate windows with the camera's aspect ratio. The terms are:

- distance of the subject to the nearest strong point (thirds or φ lines 0.382 / 0.618);
- room in the direction of the gaze;
- the horizon on a third;
- visual balance;
- headroom;
- a penalty for any **joint** (knee, wrist, elbow, hip, ankle) sitting on an edge;
- the **area lost**, weighted 0.45.

A crop must beat "no crop" by at least 0.03 in cost, and it must keep ≥ 80 % of the area. **No crop** is always a valid, and frequent, answer.

## 3. Hard constraints

- Never cut heads, hands (especially a hand with a ring), feet at the ankle, or any joint. Body and hand pose come from the Vision framework, so hands and joints are only protected on macOS with the Vision helper; elsewhere only faces (YuNet / Haar) and spectral saliency are available, and face boxes are extended to an estimated body.
- Keep the camera's aspect ratio (3:2 on most cameras) and orientation.
- Keep context: landscape and venue.

## 4. Output

Reframing is **non-destructive**. It stores `{angle_deg, x, y, w, h, keep_pct, reason}` in the output's sidecar and, with `reframe=apply`, applies it to the final full-resolution JPEG, the web JPEG and the 16-bit TIFF; with `reframe=suggest` it is only stored. The `reason` is a short human-readable sentence, shown in the UI.

| Metric (public set) | Value |
|---|---|
| Frames left uncropped | not yet measured |
| Frames straightened | not yet measured |
| Constraint violations in manual audit (heads/hands/joints cut) | not yet measured |
| Time of the reframe stage (analysis + JPG, web and TIFF16 output) | 1.71–2.86 s per photo on the CC0 benchmark files (Apple Silicon, 16 GB; results vary; see [BENCHMARKS.md](BENCHMARKS.md#3-performance-and-memory)) |
