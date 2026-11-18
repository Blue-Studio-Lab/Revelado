# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, minor releases may contain breaking changes; they are called out under **Changed**.

## [Unreleased]

## [0.2.0-beta.1] - 2026-11-18

First public **beta**. It installs from source (see the README Quick start); there is no installer yet. Feedback is welcome through the issue templates, including the new *Beta feedback* form.

### Fixed
- Extreme-ISO RAWs (for example ISO 25600 and above) no longer get magenta or purple shadows. The fix only switches on when the measured noise would clip at the black level; every other file develops exactly as before.
- The camera name no longer repeats the maker ("Canon Canon EOS 800D" became "Canon EOS 800D").
- Cameras without a curated profile get their sensor class from the EXIF crop factor, so APS-C bodies such as the ZV-E10 and the 800D are no longer labelled full-frame.
- Export jobs in the Oven say "Exporting i of n" and count the exported photos.
- Engine tests run on CI runners without exiftool, and on Windows.

### Security
- All open CodeQL alerts are closed:
  - Export destinations must resolve inside an allowed root (home, temp, external or NAS mounts). `REVELADO_EXPORT_ROOTS` adds more. Paths with `..`, control characters or symlinks that escape are rejected.
  - The engine serves web-app files only from inside the built folder.
  - Validation errors no longer echo exception text.
  - Files created by the copy fallback are no longer world-readable.
  - New catalog ids use SHA-256; ids already in a catalog are kept.
  - The web UI only opens or loads engine URLs from the same origin or from the engine's own origin.
  - The web UI's lookup records cannot be polluted through the prototype.
- Dependabot no longer proposes major-version updates. Those are migrated by hand, with tests.

### Added
- Public repository scaffolding:
  - README in English and Spanish;
  - user manual (EN/ES) and technical documentation;
  - contributing guide with DCO, Code of Conduct, security policy and governance;
  - citation file.
- `REVELADO_EXPORT_ROOTS` (engine): extra folders exports may be written into. See *Changed*.
- CI workflows:
  - Python lint/typecheck/tests on Ubuntu, macOS and Windows with Python 3.12–3.14;
  - web build and typecheck;
  - gitleaks, CodeQL, dependency review, pip-audit and npm audit, OpenSSF Scorecard;
  - release skeleton with build-provenance attestations.
- `scripts/setup_github_protection.sh`, which applies the recommended branch and tag rulesets. It is not run automatically.

### Changed
- The project is now named **Revelado** (formerly a working title). The Python distribution, import package and CLI are `revelado`.
- Renamed the internal identifiers that still used the former working title: the develop routes are `POST /api/develop/sample` and `POST /api/develop/batch`, the Develop screen is `#/develop`, the engine token header is only `X-Revelado-Token`, only `REVELADO_*` environment variables are read, and the error class is only `ReveladoError`. No compatibility shims, since no public release existed.
- Export destinations are now checked against allowed export locations after resolving symbolic links: home, temp,
  shared folders and external/network disks (macOS `/Volumes`, `/Users/Shared`; Linux `/media`, `/mnt`, `/run/media`,
  `/run/user/<uid>`, `/srv`, `/data`; Windows any drive and UNC shares except the system folders), plus
  `REVELADO_EXPORT_ROOTS` and the MCP server's `REVELADO_EXPORT_DIRS`. Destinations with `..`, control characters or
  Windows device paths are refused. Exports to other system locations now return 400 `E_BAD_INPUT`.
- Web toolchain moved to Vite 8 (Rolldown/Oxc), Vitest 5 and `@vitejs/plugin-react` 6; it needs Node.js 22.12+ or 24 LTS (the Debian/Ubuntu `nodejs` package is too old; the install docs now say where to get Node). React 18 and TypeScript 5 are unchanged, and so are the build layout (`apps/web/dist`), the dev proxy and the engine's static serving. The JS and CSS minifiers change (Oxc, Lightning CSS) and the default browser target rises to Vite 8's `baseline-widely-available` (Chrome/Edge 111, Firefox 114, Safari 16.4), so older browsers may not run the UI.

## [0.1.0] - unreleased

The first usable version from source:

- local engine (FastAPI on `127.0.0.1:8790`) with web UI;
- library import with RAW+JPG pairing and a persistent catalog;
- 100 % viewer with noise, CA and colour-shading masks;
- technical cull and optional Claude cull;
- develop sample, oven batch and export.

[Unreleased]: https://github.com/Blue-Studio-Lab/Revelado/compare/v0.2.0-beta.1...HEAD
[0.2.0-beta.1]: https://github.com/Blue-Studio-Lab/Revelado/releases/tag/v0.2.0-beta.1
[0.1.0]: https://github.com/Blue-Studio-Lab/Revelado/releases/tag/v0.1.0
