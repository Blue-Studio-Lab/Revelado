# Security policy

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub: go to **Security → Report a vulnerability** on <https://github.com/Blue-Studio-Lab/Revelado/security/advisories/new>. This is GitHub's Private Vulnerability Reporting, and it reaches only the maintainers.

Please include:

- the affected version or commit;
- your OS;
- steps to reproduce;
- the impact as you understand it.

**Do not attach real people's photos.** If a crafted file triggers the bug, share a minimal synthetic file instead.

What to expect:

| Step | Target |
|---|---|
| Acknowledgement | within 72 hours |
| First assessment (severity, affected versions) | within 7 days |
| Fix or mitigation for high/critical issues | as fast as possible, normally within 30 days |
| Public advisory + credit (if you want it) | when a fixed release is available |

We follow coordinated disclosure. Please give us a reasonable window before publishing.

## Supported versions

Revelado is in alpha. Only the latest release and `main` receive security fixes.

| Version | Supported |
|---|---|
| `main` / latest 0.x | yes |
| older 0.x | no |

## Scope

In scope:

- **Local engine API.** The engine binds to `127.0.0.1`. In scope: token handling, `Host` and `Origin` checks, path traversal through any route, and cross-site requests from a browser tab.
- **API key handling.** `ANTHROPIC_API_KEY` must never be logged, sent to the web UI, written outside the user's config `.env` (mode 0600), or passed on a command line.
- **Untrusted input parsing.** RAW, TIFF and JPEG files and metadata, opened through LibRaw/rawpy, ExifTool, Pillow, tifffile and OpenCV.
- **Privacy guarantees for AI features.** Only downscaled, EXIF-stripped previews with opaque ids may leave the machine. File names, paths, GPS and serial numbers must not.
- **Export safety.** Exports are atomic, never overwrite and never write inside the source folder.
- **Supply chain.** CI workflows, release artifacts, attestations and the integrity of downloaded dependencies.

Out of scope:

- vulnerabilities in third-party tools themselves (report them upstream, though we would like to hear about them so we can pin or mitigate);
- attacks that require an attacker who already controls the user's account;
- denial of service by feeding the app enormous files on your own machine.

## Verifying releases

Release artifacts carry GitHub artifact attestations (Sigstore-backed SLSA build provenance). Verify with:

```bash
gh attestation verify <downloaded-file> --repo Blue-Studio-Lab/Revelado
```

## If a secret leaks

This runbook is for maintainers.

1. **Revoke the credential first.** For an Anthropic key, use the Claude Console. Rewriting git history does *not* un-leak a key.
2. Remove the secret from the tree, then purge it from history with `git filter-repo` if it was pushed. Ask GitHub Support to clear cached views.
3. If personal images or names were exposed, notify the affected people.
4. Publish a short advisory describing what leaked, for how long, and what was done.

GitHub secret scanning with push protection is enabled on this repository, and CI runs gitleaks on every push and pull request.
