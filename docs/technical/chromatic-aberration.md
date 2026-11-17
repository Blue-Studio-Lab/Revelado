# Chromatic aberration: lateral and axial

> **Resumen (ES).** Hay dos aberraciones distintas y cada una se corrige una sola vez, en el lugar correcto. La **lateral** (los canales rojo y azul quedan un poco más grandes o más pequeños que el verde, hacia los bordes) se corrige en el RAW, **antes** del demosaico. La **axial** (franjas moradas o verdes junto a altas luces) se quita después, en OkLab: solo cerca de bordes brillantes de alto contraste, solo en los tonos morado y verde, y solo la *desviación* respecto al color del entorno. Así, un vestido morado de verdad no se toca.

Sources: [`engine/revelado/pipeline/raw.py`](../../engine/revelado/pipeline/raw.py) (lateral, through RawTherapee), [`engine/revelado/pipeline/clean.py`](../../engine/revelado/pipeline/clean.py) (`defringe`) and [`engine/revelado/diagnostics.py`](../../engine/revelado/diagnostics.py) (the CA mask).

## 1. Two different defects

| | Lateral (transverse, TCA) | Axial (longitudinal, LoCA) |
|---|---|---|
| Cause | magnification differs per wavelength | focus distance differs per wavelength |
| Looks like | red/cyan or blue/yellow edges that grow toward the corners | purple (and green) halos around bright edges, anywhere in the frame, worst wide open |
| Geometry | a **radial shift** of R and B relative to G | a **blur**, not a shift; it cannot be "re-registered" away |
| Correct fix | resample R and B (warp) | desaturate the fringe toward the local colour |

Each defect is corrected **once**. Some cameras store their own CA correction parameters in the RAW. Revelado's lateral correction already happens before demosaicing, so those embedded parameters are deliberately **not** applied again. Double correction creates the opposite fringe.

## 2. Lateral CA: correct before demosaicing

Lateral CA is a sub-pixel misregistration between the colour planes. If it is corrected *after* demosaicing, the demosaic has already interpolated using misaligned neighbours, and the errors (zipper artefacts and false colour at edges) are baked in. RawTherapee's raw CA correction works on the Bayer data, before demosaic:

1. It splits the frame into blocks and measures the local shift of the R and B sub-planes against G at edges.
2. It fits a smooth (polynomial) shift field across the frame.
3. It resamples R and B on the Bayer grid.

Revelado runs it with **3 automatic iterations** and **"avoid colour shift"** enabled. The iterations catch residual shift, and the colour-shift guard stops the correction from tinting large flat areas.

### Measuring the residual (the CA mask)

The viewer's CA mask, and the QC tooling, measure what is left:

- The frame is split into a 16 × 10 grid of tiles. Only tiles with enough edge energy count.
- For each such tile, the gradient magnitude of R and B (after a 1/2.2 gamma, to even out contrast) is **phase-correlated** against G under a Hann window. The result is a sub-pixel shift vector.
- On the half-size (superpixel) image there is a constant Bayer sampling offset common to all tiles. It is removed by subtracting the median vector.
- Lateral CA is radial, so what remains is projected on the direction from the image centre:
  $$\delta_r = \frac{\Delta\mathbf{x}\cdot(\mathbf{p} - \mathbf{c})}{\lVert\mathbf{p} - \mathbf{c}\rVert}$$
  and reported in **sensor pixels**.

A well-corrected lens shows a residual shift well under a pixel at the periphery. On the public Bayer fixtures, after `ca=auto`, the mean R / B shift magnitude in the outer ring of the frame is 0.021 / 0.044 px (Sony ILCE-7M4) and 0.013 / 0.022 px (Nikon Z 6); see [BENCHMARKS.md](BENCHMARKS.md#2-chromatic-aberration). The benchmark measures the shift magnitude per tile rather than the radial projection above.

**X-Trans.** RawTherapee's raw CA correction only works on Bayer data. On the Fujifilm X-T3 fixture the shift is unchanged (B p90 2.692 px with `ca=off` and with `ca=auto`), so **lateral CA is not corrected on X-Trans files today**. The axial defringe below still runs.

## 3. Axial CA: defringe with protection

Axial fringes are blurred colour, so they cannot be shifted back. The only correct treatment is to pull the fringe's colour toward the colour it *should* have, which is the colour of its surroundings. The difficulty is not damaging real purple and green objects. `defringe` works on (a, b) in OkLab, on the exposed image. The candidate weight is a product of four soft conditions:

$$
w_{\text{cand}} = w_{\text{hl}}\cdot w_{\text{ct}}\cdot w_{\text{hue}}\cdot w_{C}
$$

| Term | Definition | Meaning |
|---|---|---|
| $w_{\text{hl}}$ | $\operatorname{smoothstep}(B;\,0.45,\,1.2)$, $B = \operatorname{dilate}_{r=7}(Y)$ | a highlight within 7 px (exposed scale: 0.18 = mid-grey) |
| $w_{\text{ct}}$ | $\operatorname{smoothstep}(B/(Y+10^{-3});\,1.6,\,4)$ | strong local contrast: dark pixel next to a bright one |
| $w_{\text{hue}}$ | $\max\big(w_{\text{purple}},\; 0.8\,w_{\text{green}}\big)$ | purple window centred at 310° (soft edges 35°–55° away); green window centred at 140° (22°–40°) |
| $w_C$ | $\operatorname{smoothstep}(C;\,0.008,\,0.025)$ | there is visible colour to remove |

### The reference colour, without the fringe

The colour each candidate should have is estimated by **normalised convolution** that *excludes the candidates themselves*:

$$
w_{\text{ref}} = 1 - \operatorname{smoothstep}(w_{\text{cand}};\,0.05,\,0.3),\qquad
\bar a = \frac{G_\sigma * (a\,w_{\text{ref}})}{G_\sigma * w_{\text{ref}}},\quad \sigma = 1.6\,r
$$

and the same for $\bar b$. Where there is too little clean neighbourhood ($G_\sigma * w_{\text{ref}}$ small), the reference fades to neutral. The reference is then **never allowed to have more chroma than the pixel already has**:
$$(\bar a, \bar b) \leftarrow (\bar a, \bar b)\cdot\min\!\left(1, \frac{C}{\lVert(\bar a,\bar b)\rVert}\right)$$
so defringing can only desaturate, never add colour.

### Protection of real purple/green objects

The last gate is the **deviation** from the reference:

$$
w_{\text{dev}} = \operatorname{smoothstep}\big(\lVert(a,b) - (\bar a,\bar b)\rVert;\;0.006,\;0.02\big)
$$

A purple dress next to a white window has *purple surroundings*, so its reference is purple, its deviation is close to zero and it is left alone. A purple fringe on a dark-brown branch against the sky has a brown or neutral reference, so the deviation is large and it is corrected. The final weight is softened by a 0.8 px blur, and the colour is moved:

$$
(a', b') = (a, b) + w\,\big((\bar a, \bar b) - (a, b)\big), \qquad w = \operatorname{clip}(w_{\text{cand}}\,w_{\text{dev}}\,s,\;0,\;1)
$$

L is never modified.

## 4. Results

Measured with `scripts/benchmark_quality.py` on CC0 RAWs, `ca=off` → `ca=auto` (Apple Silicon, 16 GB, RawTherapee 5.13; results vary). Method and raw output: [BENCHMARKS.md](BENCHMARKS.md#2-chromatic-aberration).

| Metric | Sony ILCE-7M4 (ARW) | Nikon Z 6 (NEF) | Fujifilm X-T3 (RAF, X-Trans) |
|---|---|---|---|
| Lateral CA, R shift p90 at the periphery (px) | 0.048 → 0.040 | 0.027 → 0.023 | 0.679 → 0.679 |
| Lateral CA, B shift p90 at the periphery (px) | 0.122 → 0.087 | 0.134 → 0.039 | 2.692 → 2.692 (not corrected) |
| Residual mean R / B shift after `auto` (px) | 0.021 / 0.044 | 0.013 / 0.022 | 0.425 / 1.472 |
| Fringe score in high-contrast edge bands (×1000) | 19.401 → 18.420 | 25.659 → 25.242 | 28.155 → 28.092 |
| Fringe pixels removed on the synthetic fringe chart | not yet measured | | |
| Chroma change on the synthetic *purple object* chart (must be ≈ 0) | not yet measured | | |

The synthetic chart is generated by the test suite (`test_defringe_removes_purple_fringe_but_keeps_purple_object`): a bright edge with a known purple fringe, and a genuinely purple patch away from highlights. The test requires the fringe chroma to drop below 60 % of its input while the purple object is kept; the exact percentages are not published yet.

## 5. Limits

- Very wide fringes (> ~10 px) on extremely fast lenses can exceed the 7 px highlight neighbourhood.
- A purple object that is *only* a thin line next to a highlight, such as a violet ribbon edge, has no purple surroundings to protect it and may be desaturated.
- The colour-shading (lens cast) mask is experimental. From a single frame it cannot tell a lens cast from a coloured scene.
- Lateral CA is not corrected on X-Trans (Fujifilm) files, because the raw CA step in RawTherapee is Bayer-only.
