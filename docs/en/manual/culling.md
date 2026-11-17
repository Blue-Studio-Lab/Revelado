# Culling

*[Español](../../es/manual/seleccion.md)*

**By hand.** Keys: `0`–`5` set stars, `P` picks, `X` rejects, `U` unflags, `←`/`→` move, `Enter` opens the viewer. Press `?` for all shortcuts. Set a **target** (for example 150) to see how close you are.

**Technical cull** (free and local, always available). For each photo it measures:

- sharpness at the largest face, at the camera's AF point and over the whole frame;
- open or closed eyes on large faces;
- exposure: clipped highlights and crushed shadows;
- bursts: frames less than 2.5 s apart.

It scores each photo 0–100 and suggests keep, maybe or reject. It never rejects the best frame of a burst unless that frame is unusable. The suggestions are marks you review; nothing is deleted.

**Let Claude choose** (optional, needs an API key and your consent per run). Claude first pre-filters every frame from a small preview, then looks again at the finalists in more detail. Before it starts you see the number of photos and the **estimated cost**. Only downscaled JPEG copies, with no EXIF and no file names, are sent. See [AI options](ai.md).

When you are done, **Send to Develop**. Picks without a RAW are listed, because they cannot be developed.
