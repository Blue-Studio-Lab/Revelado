# Viewer and diagnostic masks

*[Español](../../es/manual/visor.md)*

Open a photo from the grid (Enter). The viewer shows the camera JPG, or the preview embedded in the RAW, and **1:1 crops** cut from the full-resolution image. Use these to judge focus at real pixel size.

**Log footage.** Photos shot in S-Log3 look flat. The viewer applies a *technical* S-Log3 → Rec.709 display transform so you can judge them. It is not a look, and you can switch it off (display: camera).

## Masks

| Mask | What it shows | How to read it |
|---|---|---|
| **Noise** | local noise level in flat areas (cividis colour map); textured areas are grey | dark = clean, bright = noisy. The stats give the median for shadows, mid-tones and highlights and a verdict: low, moderate or high. |
| **Chromatic aberration** | lateral CA per tile (R/B misregistration, in sensor pixels) and axial purple/green fringe pixels | reddish purple = purple fringes, bluish green = green fringes. Large lateral values at the corners mean the lens needs correction. |
| **Colour shading** | slow colour casts across the frame (for example magenta or green corners) | experimental: a single photo cannot always separate a lens cast from a coloured scene |

The colours are colour-blind safe (Okabe-Ito and cividis). The masks are computed from the RAW at half size, *before* any in-camera noise reduction, so they show what the developer will actually face. See the technical docs for the algorithms: [noise](../../technical/noise-reduction.md) and [chromatic aberration](../../technical/chromatic-aberration.md).
