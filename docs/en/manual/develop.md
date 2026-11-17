# Develop

*[Español](../../es/manual/cuarto-oscuro.md)*

The Develop screen is where you decide the look **before** you develop the whole selection.

1. **Choose a film stock.** The default is **Neutral (no film)**. An *Ektar 100-inspired* look (internal id `kodak_ektar_100`, preset `editorial_ektar`) is the first physical film model: an approximate emulation, not affiliated with or endorsed by Kodak (see [the film model](../../technical/film-model.md#provenance-of-the-ektar-inspired-profile)). Other stocks appear in **Film stocks** as you add your own scans; training them is planned for v0.2.
2. **Pick a sample photo:** a typical frame of the session, with faces if there are people.
3. **Develop 1 sample.** This is a real, full-resolution develop: about 45 s for a 24–33 MP file. The result is exactly what the batch will produce.
4. **Compare** before/after with a split divider, side by side, or with a toggle.
5. **Adjust the recipe** and develop the sample again. When the recipe changes, the sample is marked stale.

## Recipe controls

| Control | Range | Notes |
|---|---|---|
| Strength | 0–1 | how much of the film look is applied |
| Exposure | −2 … +2 EV | on top of the automatic per-photo exposure |
| Grain | 0–1.5 | grain is always the last step, rendered at native resolution |
| Skin retouch | 0–100 | frequency-separation retouch, off in Neutral |
| Blemishes | 0–100 | automatic spot healing, off in Neutral |
| Hair | off / soft / medium / strong | experimental, macOS only, safety-gated |
| Subject accent | 0–100 | gentle lift of faces left in shadow |
| Reframe | off / suggest / apply | straightening and crop suggestions |
| Sharpen | 0–2 | halo-free sharpening |
| NR, CA | auto | automatic in v0.1; other values are accepted and reported as ignored |

The recipe is saved with every developed photo (see [Recipes and CLI](recipes-cli.md)).
