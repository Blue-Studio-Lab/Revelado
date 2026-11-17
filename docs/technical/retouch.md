# Retouch: frequency separation and blemish detection

> **Resumen (ES).** El retoque de piel es opcional y está apagado en la receta Neutro. Separa la piel en frecuencias: la textura fina (poros) se conserva casi entera, y las manchas y el relieve medio se suavizan con un filtro que respeta los bordes. Pestañas, cejas, labios y pelo se protegen midiendo el contraste local. Los granos, puntos negros y rojeces pequeñas se detectan como "manchas" en varias escalas (diferencia de gaussianas), comparadas con la piel vecina, y se curan con un pincel corrector: el color y el tono salen del borde, y la textura de un parche donante.

Sources: `finish()` in [`engine/revelado/pipeline/film/ektar.py`](../../engine/revelado/pipeline/film/ektar.py) and [`engine/revelado/pipeline/blemish.py`](../../engine/revelado/pipeline/blemish.py).

Retouch runs in OkLab, **after** the film scan and **before** grain. Retouched areas therefore receive exactly the same grain as everything else, so there are no smooth "plastic" patches.

## 1. Skin mask

The skin mask starts from detected faces: macOS Vision, YuNet or Haar, whichever is available. It is then refined by a colour qualifier (skin hue and chroma ranges in OkLCh) and by facial landmarks where they exist. Detail is protected with the local standard deviation of L (σ = 2 px):

$$
\text{protect} = 1 - \operatorname{smoothstep}(0.018,\; 0.04,\; \operatorname{sd}_{2}(L))
$$

Eyelashes, brows, lip edges and hair have high local contrast, so they drop out of the mask. The mask is then softened (σ = 1.5 px).

## 2. Frequency separation (luminance)

Inside the mask, with face width $f_w$:

$$
\begin{aligned}
B &= G_{1.0} * L, \quad F = L - B && \text{(F = pores / micro-texture)}\\
S &= \operatorname{GF}(B,\; r = f_w/40,\; \varepsilon = 0.022^2) && \text{(blotches, mid relief removed)}\\
S_2 &= \operatorname{GF}(B,\; r/3,\; \varepsilon = 0.012^2) && \text{(fine imperfections)}\\
L_{\text{new}} &= S + 0.30\,(S_2 - S) + 0.12\,(B - S_2) + 0.55\,F
\end{aligned}
$$

GF is a self-guided filter, so it smooths without crossing strong edges. The weights keep **55 % of the pore texture** and 30 % of the mid-scale shape, but only 12 % of the finest blotchiness. The result is applied through the mask, $L \leftarrow L + m\,(L_{\text{new}} - L)$, and the radius scales with the face, so a small face in a group shot is not over-smoothed.

## 3. Colour unification

Red patches on the cheeks and uneven tone are measured against the **neighbouring skin**: a mask-weighted mean over about ⅓ (for a) or 1/7 (for b) of the face width. Positive deviations in a (redness) are reduced by 65 % and in b by 30 %. Negative deviations shrink by 20 %. Then two guards keep skin natural:

- hue is softly kept at or above about 44° in OkLCh, so skin is never pinkish or magenta;
- chroma has a soft knee at 0.082, so skin is never over-saturated.

## 4. Blemish detection (scale space)

Blemishes are small blobs that differ from the surrounding skin. Detection uses a **difference of Gaussians** at 6 scales between $r_{\min} = \max(2.5,\, 0.0028 f_w)$ and $r_{\max} = \max(2r_{\min},\, 0.015 f_w)$:

$$
\text{dark}(\sigma) = G_{2.83\sigma} * (G_\sigma * L) - G_\sigma * L, \qquad
\text{red}(\sigma) = G_\sigma * a - G_{2.83\sigma} * (G_\sigma * a)
$$

A positive "dark" response means a spot darker than its surround (blackheads, moles). A positive "red" response means a redder spot (pimples, small irritations). Each response keeps its best scale. The thresholds adapt to the person's skin: $t = \max(4 \cdot 1.48\,\mathrm{MAD},\, t_0)$ inside the mask. White pustules count only when they sit on a larger red base, so specular highlights and water drops are not "healed".

Rejections, which matter as much as the detections:

- **stubble or beard:** many small dark points in a window, so isolated dark-only points there are left alone;
- **structure:** a strong gradient at scale $r_{\min}$ (lip edges, nostrils, eye corners);
- **shape and size:** elongated or too-large components;
- **moles:** dark, round and stable across scales; they belong to the person and are kept.

## 5. Healing

Each accepted spot is replaced with a "healing brush":

- the **low frequency** (tone and colour) is reconstructed from the spot's border ring;
- the **high frequency** (pores) is copied from a nearby donor patch of clean skin.

The healed spot keeps real skin texture. It is never a smooth disc.

## 6. Determinism and honesty

- Detections are frozen into the resolved recipe ([determinism](determinism-and-recipes.md)), so re-rendering a photo heals exactly the same spots.
- Automatic retouch is **off** in the Neutral recipe. The UI labels it as automatic retouch and never as the "original".

| Metric (public CC-BY portrait set with model releases) | Value |
|---|---|
| Blemishes healed per face, median | not yet measured |
| False heals on protected features (manual audit) | not yet measured |
| Pore texture energy retained in the skin mask | not yet measured |

No public CC-BY portrait set has been benchmarked yet; see [BENCHMARKS.md](BENCHMARKS.md).
