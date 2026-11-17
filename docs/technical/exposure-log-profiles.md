# Exposure for log profiles and subject-driven exposure

> **Resumen (ES).** Cuando se dispara en S-Log3, el RAW queda "subexpuesto" a propósito para proteger las altas luces, así que hay que subirlo 1–3 pasos al revelar. Revelado calcula esa subida por foto: mezcla la luz del **sujeto** (alrededor del punto de enfoque) con la clave global ponderada al centro, y la limita para no quemar las altas. Después, ya con la película aplicada, ajusta la exposición mirando la **piel iluminada** de las caras, para que quede en el tono correcto. Ese empuje también le dice a la reducción de ruido cuánto ruido esperar.

Sources: [`engine/revelado/pipeline/raw.py`](../../engine/revelado/pipeline/raw.py) (`analyze_exposure`), [`engine/revelado/pipeline/film/ektar.py`](../../engine/revelado/pipeline/film/ektar.py) (`subject_exposure`, `subject_accent`) and [`engine/revelado/imaging.py`](../../engine/revelado/imaging.py) (the S-Log3 display transform).

## 1. Why log captures look underexposed in the RAW

A log picture profile changes the **in-camera JPEG and the metering advice**, not the sensor. To fit the log curve, photographers typically expose 1–2 stops below a "normal" exposure. The RAW therefore holds a perfectly clean highlight range, but mid-tones and faces sit far below 18 % grey. The RAW developer must find the right push **per photo**, without burning the highlights it was protecting.

For browsing, the viewer applies a *technical* display transform to log JPEGs: S-Log3 / S-Gamut3.Cine → Rec.709 linear → neutral roll-off → sRGB. It uses Sony's published S-Log3 curve (code value $t$ in 0–1):

$$
x = \begin{cases}
10^{\left(\frac{1023\,t - 420}{261.5}\right)}\cdot 0.19 - 0.01 & 1023\,t \ge 171.21\\[4pt]
\dfrac{(1023\,t - 95)\cdot 0.01125}{171.21 - 95} & \text{otherwise}
\end{cases}
$$

with highlight roll-off $f(x) = x\,(1 + x/64)/(1 + x)$ after a ×1.18 gain. It is **not** a look: it only makes a flat frame possible to judge.

## 2. Per-photo exposure from the RAW (before develop)

The analysis is fast because it skips demosaicing. Each 2×2 Bayer block (6×6 on X-Trans) becomes one RGB "superpixel", in camera white balance and Rec.2020, scaled so that 1.0 = green saturation. Clipped photosites (≥ 98.5 % of white) are masked out. With $Y$ the Rec.2020 luminance and $\ell = \log_2 Y$:

1. **Global key, centre-weighted.** Use a Gaussian weight $w_c$ with σ = 0.38 × width/height, and keep only non-clipped pixels between the 1st and 99th percentiles of $\ell$:
   $$k_g = 2^{\sum w_c\,\ell / \sum w_c}, \qquad ev_g = \log_2(0.16 / k_g)$$
2. **Subject key at the AF point.** The camera's focus location (from maker notes) is mapped onto the superpixel grid. A square window reaching 6 % of the long side on each side of the AF point is trimmed to its 5–95 % percentiles, and its log-mean gives $k_s$.
3. **Blend.** The subject gets most of the say:
   $$ev_{\text{mid}} = 0.6\,\log_2(0.20 / k_s) + 0.4\,ev_g$$
   or simply $ev_g$ when there is no AF data.
4. **Highlight cap.** The 99.7th percentile of the (σ = 2 px blurred) non-clipped luminance should land no higher than **+4 EV over mid-grey**. That is where a film shoulder can still separate tones:
   $$ev_{\text{cap}} = \log_2\!\left(\frac{0.18\cdot 2^{4}}{Y_{99.7}}\right)$$
5. **Never sink the subject.** The cap may pull exposure down by at most 1 EV:
   $$ev = \operatorname{clip}\big(\min(ev_{\text{mid}},\ \max(ev_{\text{cap}},\ ev_{\text{mid}} - 1)),\ -2,\ 7\big)$$

   In backlit scenes, where the cap would like to go further, the result instead records `hl_ev_over_mid`: how far the highlights sit above grey. The film renderer then relies on its long shoulder rather than darkening the people.

The chosen $ev$ also feeds the [noise dosing](noise-reduction.md#2-dosing-how-much-noise-to-expect): a push above +1.5 EV counts as extra ISO.

## 3. Subject-driven exposure after the film (closing the loop)

RAW statistics cannot see what the *film* will do to a face, because toe, shoulder and colour crosstalk all move skin tones. The film stage therefore closes the loop on a ¼-scale preview of the actual film render:

1. Render the preview, detect faces (and people), and measure the **lit side of the skin**: the 80th percentile of OkLab L over skin-coloured pixels in each face box. With several faces, the level is 0.6 × the brightest face + 0.4 × the area-weighted mean. Large faces count; tiny background faces are ignored.
2. Target: $L^\* = 0.722$ in OkLab (≈ CIE L\* 69, sRGB ≈ 185), the lit side of well-exposed skin.
3. Update: a secant step on EV, starting from a slope of 0.075 L per stop (clamped to 0.03–0.12 per stop). The first step is scaled by the trust in the evidence: faces 1.0, people boxes 0.85, global 0.7. There are at most 4 iterations, the result stays within **[−1.2, +1.75] EV**, and the loop stops when |error| < 0.008.
4. With no faces, the median L of the frame is driven toward 0.56. If people are detected, their mid-tone is kept from falling below the frame's.

After exposure, a gentle **subject accent** lifts faces that were left in shadow, by up to +0.45 EV each according to their deficit. It also adds about +0.15 EV on people and −0.2 EV on bright, distracting background areas. The masks are very soft and fade above L 0.80, so nothing looks HDR.

## 4. Results

| Metric | Value |
|---|---|
| Lit-skin L\* spread across a public portrait set, before → after subject exposure | not yet measured |
| Frames whose highlights were capped (public set) | not yet measured |
| Analysis time per frame (superpixel) | 0.50–0.51 s on a 33 MP RAW (Sony ILCE-7M4), 0.30–0.31 s on a 12 MP RAW (Sony ILME-FX3) |

Times from `scripts/benchmark_quality.py` on Apple Silicon, 16 GB; results vary. See [BENCHMARKS.md](BENCHMARKS.md#3-performance-and-memory).

## 5. Pitfalls

- **No AF data.** Without AF data (other makes, manual focus), the subject term disappears and exposure falls back to the centre-weighted key.
- **Coloured light.** Under strongly coloured light the skin classifier can fail. The level then falls back to the mid-to-high tones of the face box.
- **Tiny faces.** In wide group shots, faces are too small to measure reliably. The trust weights keep the correction small.
