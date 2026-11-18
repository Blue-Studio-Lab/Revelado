# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, minor releases may contain breaking changes; they are called out under **Changed**.

## [Unreleased]

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

[Unreleased]: https://github.com/Blue-Studio-Lab/Revelado/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Blue-Studio-Lab/Revelado/releases/tag/v0.1.0
