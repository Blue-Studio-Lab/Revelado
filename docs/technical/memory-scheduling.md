# Memory and scheduling

> **Resumen (ES).** Revelar una foto de 33 MP a resolución completa usa varios GB de RAM. En un ordenador de 16 GB, dos revelados a la vez pueden colgar el sistema. Por eso Revelado ejecuta **un solo trabajo pesado a la vez**, y cada foto en su **propio proceso hijo**: al terminar, toda su memoria se libera. Antes de empezar comprueba que haya RAM y disco libres. Mientras trabaja, un vigilante suma la memoria de todo el árbol de procesos (incluido RawTherapee) cada medio segundo y lo detiene si pasa del límite. Si eso ocurre, la foto falla y el lote sigue; el reintento en "modo de poca memoria" está planificado.

Sources: [`engine/revelado/jobs.py`](../../engine/revelado/jobs.py) and the develop adapter/worker in [`engine/revelado/`](../../engine/revelado).

## 1. Job classes

| Class | Concurrency | Examples |
|---|---|---|
| heavy | **exactly 1** | develop sample, batch, export |
| light | 1 | technical cull, AI cull (network + small previews) |

A sample request jumps ahead of heavy jobs that are still queued (priority 1 against 5 for batch and export), but it never interrupts the running job. Priorities apply to whole jobs: if a batch has already started, the sample waits until the batch ends (or is cancelled). Jobs can be cancelled between photos, and a running heavy subprocess is killed on cancel.

## 2. One child process per photo

Each heavy item runs in a fresh Python interpreter started with `subprocess` (not a `multiprocessing` fork of the engine), so behaviour is the same on macOS, Windows and Linux. When the child exits, every full-resolution buffer is gone, so there is no fragmentation and no slow leaks across a 150-photo batch. Progress comes back as JSON lines on stdout.

## 3. Admission and the watchdog

Before a heavy item starts:

- **free disk** in the cache volume must be ≥ the configured minimum (default 6 GB);
- **available RAM** must be ≥ the configured minimum. The engine waits up to 2 minutes for it, then fails the item with `E_MEM_LOW` ("close some apps and retry").

While the item runs, a watchdog sums the **RSS of the whole process tree** (Python plus the RawTherapee child) every 0.5 s. On macOS, RSS sampling is the reliable mechanism; `RLIMIT_AS` is not. Above the cap, the tree is killed and the item fails with `E_MEM_GUARD`.

The recommended budget is

$$
\text{budget} = \operatorname{clamp}(0.45 \times \text{RAM},\; 3,\; 12)\ \text{GB}
$$

which gives about 7.2 GB on a 16 GB machine and leaves room for the OS, the browser and the user's other apps. The shipped defaults are fixed values: RSS cap 9 GB (`REVELADO_HEAVY_RSS_CAP_GB`), 3.5 GB of available RAM to start (`REVELADO_HEAVY_MIN_AVAILABLE_GB`) and 6 GB of free disk (`REVELADO_MIN_FREE_DISK_GB`). Set the cap from the formula above for your machine (see `engine/.env.example`).

## 4. Low-memory retry (planned)

> **Status:** not implemented in 0.2.0.dev0. Today `E_MEM_GUARD` fails the item (the error is marked retryable), the failure is recorded and the batch continues with the next photo. The design below is planned for v0.2.

After `E_MEM_GUARD`, one retry will run with:

- the optional heavy stages off (hair clean-up);
- other workers paused;
- caches released;
- OpenCV/BLAS threads limited to 2.

Stages that stay on produce identical pixels. If the retry also exceeds the cap, the item fails with "needs more memory" and the batch continues.

## 5. Inside the stages

- Large operations run in **bands** of 256–512 rows: colour-matrix products, scanning, distortion remapping, subject accent.
- Wide, low-frequency effects (halation, coarse chroma NR, subject masks) are computed at ¼ or ⅛ resolution and upsampled.
- Buffers are released explicitly (`del`) as soon as a stage is done.
- Previews and masks never decode the full frame. They use the embedded JPEG with DCT scaling, or a half-size superpixel RAW.

## 6. Measured

| Metric | Value |
|---|---|
| Peak RSS of the heavy tree, default (neutral) recipe | 2.81 GB at 12 MP (Sony ILME-FX3) · 4.15 GB at 33 MP (Sony ILCE-7M4) · 24 MP not yet measured |
| Peak RSS of the heavy tree, 33 MP, editorial Ektar preset (hair off) | 3.56 GB |
| Seconds per photo, end to end (neutral / editorial Ektar) | 33 MP: 35.4 s / 63.4 s · 12 MP sensor: 14.1 s / 21.4 s · 24 MP not yet measured |

Single runs with `scripts/benchmark_quality.py` on Apple Silicon, 10 CPU cores, 16 GB RAM, RawTherapee 5.13, including web JPEG, 16-bit TIFF and reframing; results vary. Details: [BENCHMARKS.md](BENCHMARKS.md#3-performance-and-memory). The heavy test suite enforces a budget of 1200 MB + 190 MB per megapixel for the process tree.

Target for v0.2 (strip-based renderer): **≤ 4 GB** for a whole heavy job at 33 MP.
