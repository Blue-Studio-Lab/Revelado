# Hair clean-up: what it does and its honest limits

> **Resumen (ES).** El módulo de cabello quita pelos sueltos (hebras) sobre fondos **lisos**, sin IA generativa. Es **experimental y está apagado por defecto**. Hoy necesita el ayudante Vision de macOS (mate de personas y puntos de la cara); en otros sistemas se salta sin bloquear nada. Tiene una **puerta de seguridad**: si el retoque cae sobre fondo con textura o estructura (agua, hojas, edificios), no se aplica y la foto queda marcada para retoque manual. No puede reconstruir fondos complejos: eso necesitaría relleno generativo.

Source: [`engine/revelado/pipeline/hair.py`](../../engine/revelado/pipeline/hair.py).

## 1. Where it runs

The module runs on the **linear base**, before the film look and before grain, so retouched areas get the same colour response and grain as the rest of the frame. It analyses each head region at a reduced *detection scale* (face ≤ 800 px, crop ≤ 6.5 MP) and applies the result at full resolution in strips. It never holds the whole frame in float at full size.

## 2. Method

1. **Mass to keep (T).** This is the union of:
   - dense, in-focus micro-texture (the hairstyle itself, including "comb" regions with sky between strands);
   - the core of the person matte;
   - solid mass that a median keeps and that differs from the outside background.

   A morphological close and open then shape it. The *strength* setting moves the density threshold and the opening.
2. **Strands.** The median residual (computed *without* the mass) outside T, which deviates from the background *toward* the nearby hair colour, with hysteresis. Connected components that start in the hair are removed **whole**, from tip to mass, with a short ramp at the silhouette and a little contrast left, so the edge looks natural rather than like a helmet.
3. **Fill.** A large, smoothed median (which keeps defocused clouds and hills), plus a low-frequency correction measured on clean pixels, plus fine noise measured around the area. Wide defocused wisps near removed strands are faded too, so no patches remain.
4. **Structured background** (buildings, focused water or mountains, branches): only thin, sharp strands that start in the hair are removed, filled with the local median, or nothing is done at all.
5. **Strands over skin:** thin, long lines inside the face skin that start at the hair or the face border. They are filled push-pull from neighbouring skin plus noise. Eyes, lashes, brows, lips and nose are protected.

## 3. The safety gate

After the clean-up, the gate measures the **original** base at ¼ resolution, around the changed pixels only (normalised convolution that excludes them):

- **texture:** the deviation of the high-pass signal, in stops;
- **structure:** the smoothed gradient;
- **unknown:** changed pixels with no measurable background nearby;
- **focus:** whether the heads are in focus at all.

The change is applied only when it falls almost entirely on smooth background and is large enough to matter. Otherwise the original base is kept and the sidecar's `pending_manual` list gets `cabello_pendiente_manual` ("hair: manual review pending"), which the Oven shows to the user.

## 4. Honest limits

- **No generative fill.** It cannot invent a brick wall, foliage or text behind a strand. On such backgrounds the gate refuses, by design.
- Strands **crossing over** the hair mass, or passing over brows and eyes, are not removed.
- It is **macOS-only** for now, because the person matte and landmarks come from the Vision framework. Cross-platform mattes are on the roadmap.
- It is conservative: on real portraits, only a minority of frames come out clearly cleaner without manual help. The shares that come out clearly cleaner, need manual boxes or are refused by the gate on a public portrait set are **not yet measured** (see [BENCHMARKS.md](BENCHMARKS.md)).
- Memory: it is the heaviest optional stage. Its peak RSS at 33 MP is **not yet measured**; the published benchmark ran with hair off.
