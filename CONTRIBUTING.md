# Contributing to Revelado

Thanks for helping! Revelado is a photographer's tool built in the open. Bug reports, camera and lens data, docs, translations, tests and code are all welcome.

> **The one rule that matters most: never commit, attach or paste photos of real people who have not agreed to be published.** That covers clients, family and strangers, including their names, file names from a client shoot and folder paths. Use the synthetic generators in the tests, or images under a permissive licence with written attribution (see [Fixtures](#test-images-and-fixtures)).

## Ways to help

- **Report a bug** with the issue form. Include the redacted diagnostic output, not your photos.
- **Add camera or lens support** with the "Camera / lens support" form. A sample RAW under a CC0 licence (for example from raw.pixls.us) helps a lot.
- **Improve the docs.** The user manual is in English and Spanish, so a translation fix is a great first PR.
- Look for issues labelled `good first issue` or `help wanted`.

## Development setup

Prerequisites: Python 3.12–3.14, Node.js 20 LTS or newer, git and ExifTool. RawTherapee is optional; you only need it to run real develops.

```bash
git clone https://github.com/Blue-Studio-Lab/Revelado.git && cd Revelado
python3 -m venv .venv && source .venv/bin/activate       # Windows: .\.venv\Scripts\Activate.ps1
pip install -e "engine[dev]"
pip install pre-commit && pre-commit install --hook-type pre-commit --hook-type commit-msg
cd apps/web && npm ci && cd ../..
```

Run what CI runs:

```bash
cd engine && python -m pytest -q                       # engine tests: no network, no private data
ruff check engine                                        # lint
cd apps/web && npm run typecheck && npm run build        # web
pre-commit run --all-files                               # hygiene + secret scan
```

**Heavy jobs.** A full develop of a 33 MP frame uses several GB of RAM. Tests must never run a full-resolution develop. Use small synthetic arrays, and keep any single test under about 500 MB. Never run several heavy develops in parallel on a 16 GB machine.

## Making a change

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Create a branch from `main` and keep each PR focused on one topic.
3. Add or update tests. Image-processing changes need a test on synthetic data that pins the behaviour, for example "a purple object is not desaturated by the defringe".
4. Update the docs (manual and/or `docs/technical/`) and add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
5. Use [Conventional Commits](https://www.conventionalcommits.org) for commit titles: `fix(engine): …`, `feat(web): …`, `docs: …`.
6. **Sign off every commit** (DCO, see below).
7. Open the PR and fill in the checklist.

### Coding conventions

- **Python:** type hints, `ruff` clean, English comments and docstrings, no absolute user paths, and configuration from the environment or `.env` only. Never hard-code tool paths: find them with `shutil.which` and an environment override.
- **TypeScript/React:** strict mode, no `any` without a comment explaining why, and visible strings in `strings.ts` (both ES and EN).
- **Determinism:** randomness (grain, sampling) must come from a seeded generator whose seed is derived from the recipe and the photo. See [determinism](docs/technical/determinism-and-recipes.md).
- **Licence headers:** new source files start with `# SPDX-License-Identifier: GPL-3.0-or-later` (or `//` in TS).

## Developer Certificate of Origin (DCO)

Revelado uses the [Developer Certificate of Origin 1.1](https://developercertificate.org) instead of a CLA. Contributions stay under the same licence they came in under (GPL-3.0-or-later), and no single party can later take them proprietary.

By adding a `Signed-off-by` line to a commit, you certify that you wrote the change or otherwise have the right to submit it under the project licence:

```bash
git commit -s -m "fix(engine): clamp negative EV in exposure prior"
# → Signed-off-by: Your Name <your-handle@users.noreply.github.com>
```

Fix a commit you forgot to sign: `git commit --amend -s --no-edit`. For a whole branch: `git rebase --signoff main`.

**AI-assisted contributions are welcome.** The human who submits the change signs off, because only a person can certify the DCO. Credit the assistant with a co-author trailer after the sign-off, for example:

```
Signed-off-by: Your Name <your-handle@users.noreply.github.com>
Co-Authored-By: Claude <noreply@anthropic.com>
```

You are responsible for reviewing and understanding every line you submit.

## Test images and fixtures

- Images may live only under `engine/tests/fixtures/` and `docs/assets/`.
- Every image needs an entry in `engine/tests/fixtures/ATTRIBUTION.md` with source, author, licence and URL. Accepted licences are CC0 and CC BY 4.0; for identifiable people, a model release must be on file with the maintainers.
- Fixtures must contain no GPS, no body or lens serial numbers and no owner or artist fields (unless the artist is the licensed author). Strip them with `exiftool -all= -tagsFromFile @ -Orientation file.jpg`.
- Keep fixtures small: ≤ 1 MB each, downscaled.
- Prefer **synthetic** inputs generated in the test itself (noise patches, fringes, gradients). They are free of licence issues and perfectly reproducible.
- Stock-photo sites (Unsplash, Pexels and similar) are **not** acceptable: their licences restrict redistribution.

Film scans are a special case. Revelado ships **code that builds a film profile from your own scans on your machine**. Do not commit scans, or statistics derived from scans, unless you own the scans and license them explicitly.

## Reviews

Maintainers aim to give a first response within a week. A PR needs a passing CI, a DCO sign-off on every commit, and an approving review from a code owner. Security-sensitive paths (`.github/`, `scripts/`, packaging, dependency lockfiles) always need a maintainer review. See [GOVERNANCE.md](GOVERNANCE.md).

## Code of Conduct

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
