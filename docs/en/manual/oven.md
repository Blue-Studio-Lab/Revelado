# Oven (batch)

*[Español](../../es/manual/horno.md)*

The Oven develops your selection with the recipe from Develop, **one photo at a time**.

- You see live progress: the current photo and stage, time per photo, an ETA, and failures with their reasons.
- **Cancel** stops after the current photo. A sample you request in the meantime jumps ahead of the queue, but it never interrupts a photo in progress.
- Each photo runs in its own process. If it exceeds the memory cap, it is stopped and retried once in low-memory mode, without the optional hair stage. The sidecar notes anything left pending, for example "hair: pending manual".
- Before each photo, Revelado checks free RAM and free disk. If either is short, it waits and then asks you to free some space or memory.

Why one at a time? A full-resolution develop uses several GB of memory, and two at once can freeze a 16 GB computer. See [memory and scheduling](../../technical/memory-scheduling.md).
