# Governance

## Roles

| Role | Who | Can |
|---|---|---|
| **Lead maintainer** | Jorge Luis Peña Sanchez ([@bluestudiobrooks-gif](https://github.com/bluestudiobrooks-gif)) | Sets direction, merges, cuts releases, owns security response and Code of Conduct enforcement |
| **Maintainers** | Added by the lead maintainer after sustained, high-quality contributions | Review and merge PRs, triage issues, help with releases |
| **Contributors** | Anyone who opens an issue, discussion or PR | Propose changes; listed in the contributors graph |

Revelado was co-created with Claude (Anthropic's AI model). AI assistants can propose changes, but **only humans approve, merge, tag and release.**

## Decisions

- Day-to-day changes are decided in PR review, by lazy consensus: if no maintainer objects within a reasonable time, an approved PR can be merged.
- Larger changes need an issue or Discussion labelled `proposal` first, open for at least 7 days. This covers new dependencies, recipe/profile schema changes, AI features, licence questions and anything touching privacy. The lead maintainer decides when consensus is not reached.
- Privacy and safety rules are not up for a vote: no client photos in the repo, no telemetry, and AI only on explicit opt-in.

## Becoming a maintainer

After a pattern of helpful reviews and merged PRs (typically several months), a maintainer can nominate you. The lead maintainer decides. Maintainers must enable 2FA and sign their commits.

## Releases

1. All required checks are green on `main`, and `CHANGELOG.md` has the release section.
2. A maintainer creates a **signed** tag `vX.Y.Z` (`git tag -s`).
3. `release.yml` builds the artifacts, generates provenance attestations and creates a **draft** release.
4. A maintainer verifies the artifacts (`gh attestation verify`), smoke-tests on at least one OS, then publishes the draft.

## Repository protection (recommended settings)

These settings are applied once by the lead maintainer with [`scripts/setup_github_protection.sh`](scripts/setup_github_protection.sh). The script is reviewed in-tree and **never runs from CI**. Re-run it after changing this section, or audit the settings in **Settings → Rules**.

**Ruleset on the default branch (`main`):**

- changes only through pull requests;
- **≥ 1 approving review**, including a **code owner** (raise to 2 when there are more maintainers);
- stale approvals dismissed on new pushes; the most recent push must be approved by someone else;
- all review conversations resolved;
- required status checks: `ci-ok`, `gitleaks`, `dco`, `dependency-review`, CodeQL (`Analyze (python)`, `Analyze (javascript-typescript)`). Branches must be up to date.
- **signed commits required**; linear history;
- no force pushes, no deletion;
- bypass list empty. Admins follow the same rules, apart from the documented break-glass procedure below.

**Ruleset on tags `v*`:** only maintainers can create them; no updates and no deletion; signed tags only.

**Repository settings:**

- Private vulnerability reporting **on**.
- Dependabot alerts and security updates **on**; secret scanning and **push protection on**.
- Actions:
  - default `GITHUB_TOKEN` permission **read-only**;
  - "Allow GitHub Actions to create and approve pull requests" **off**;
  - approval required for workflows from **all outside collaborators**.
- A **`release` environment** with a required reviewer (the lead maintainer), restricted to `v*` tags. Signing secrets, if any are ever needed, live only there; repository-level secrets stay empty.
- 2FA required for the organisation.

**Break-glass procedure.** If `main` is broken and no second reviewer is available, the lead maintainer may temporarily add themselves to the bypass list, merge the fix, remove the bypass and open an issue labelled `break-glass` explaining why. Every use is public.

## Code of Conduct enforcement

Report privately to the lead maintainer: either through a GitHub private vulnerability report marked "Code of Conduct" or by GitHub direct contact. Reports are kept confidential. Enforcement follows the ladder in the [Code of Conduct](CODE_OF_CONDUCT.md): correction → warning → temporary ban → permanent ban. Moderation tools:

- lock or hide comments;
- temporary interaction limits (prior contributors only, for 24 h to 6 months) during spam waves;
- organisation-level blocks.

## Changes to this document

This document changes through a PR approved by the lead maintainer.
