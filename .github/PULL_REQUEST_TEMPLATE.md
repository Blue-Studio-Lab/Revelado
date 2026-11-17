## What and why

<!-- One or two sentences. Link the issue: "Closes #123". -->

## How it was tested

<!-- Commands you ran, synthetic inputs used. Never real people's photos. -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`, see CONTRIBUTING.md → DCO)
- [ ] Tests added or updated; `python -m pytest -q` and `npm run build` pass locally
- [ ] Docs updated (manual EN + ES, and/or `docs/technical/`), and a line added under **Unreleased** in CHANGELOG.md
- [ ] **No photos of real people, client names, client file names, absolute paths (`/Users/…`, `C:\Users\…`) or keys** in code, tests, docs or screenshots
- [ ] Any new image, model or data file has its licence recorded (fixtures: `engine/tests/fixtures/ATTRIBUTION.md`)
- [ ] New dependencies are justified in the description (licence compatible with GPL-3.0-or-later)
- [ ] Image-processing changes stay deterministic (seeded RNG) and keep the one-heavy-job memory rule
- [ ] If AI-assisted: I reviewed and understand every line, and credited the assistant with a `Co-Authored-By:` trailer
