#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Privacy gate for commits (pre-commit hook; also usable by hand).

Fails when a staged file contains:
  * an absolute home-directory path (/Users/<name>/, /home/<name>/, C:\\Users\\<name>\\),
  * an Anthropic-style API key (sk-ant-...),
  * an image / RAW / TIFF file outside the allowed folders (engine/tests/fixtures/, docs/assets/),
  * any term from the PRIVATE denylist kept OUTSIDE the repo:
        ~/.config/revelado/private-terms.txt   (one term per line; '#' comments)
    Put client names, venue names, private folder names, NAS hostnames and Drive IDs there.
    Never commit that file: a public denylist of client names would leak them.

Usage:
  python3 scripts/check_privacy.py FILE...           # check files (pre-commit passes staged files)
  python3 scripts/check_privacy.py --all             # check every tracked or committable (not ignored) file
  python3 scripts/check_privacy.py --commit-msg MSG  # commit-msg hook: require a DCO Signed-off-by line
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ALLOWED_MEDIA_DIRS = ("engine/tests/fixtures/", "docs/assets/")
MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".gif",
    ".arw", ".cr2", ".cr3", ".nef", ".nrw", ".raf", ".orf", ".rw2", ".pef", ".srw", ".dng", ".iiq", ".3fr",
}
PATTERNS = [
    (re.compile(r"/Users/(?!Shared/)[A-Za-z0-9._-]+/"), "absolute macOS home path"),
    (re.compile(r"/home/(?!runner/)[a-z_][a-z0-9_-]*/"), "absolute Linux home path"),
    (re.compile(r"[A-Za-z]:\\\\?Users\\\\?[^\\\s]+\\"), "absolute Windows home path"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "Anthropic API key"),
]
# Generic, non-identifying placeholders that docs may legitimately show.
ALLOW_LINE = re.compile(r"/Users/(you|me|name|user|<[^>]+>)/|/home/(you|user|<[^>]+>)/|privacy-allow")
DENYLIST = Path.home() / ".config" / "revelado" / "private-terms.txt"
SELF = "scripts/check_privacy.py"


def load_denylist() -> list[str]:
    try:
        lines = DENYLIST.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return []
    return [t.strip().lower() for t in lines if t.strip() and not t.lstrip().startswith("#")]


def check_file(path: str, deny: list[str]) -> list[str]:
    p = Path(path)
    rel = p.as_posix()
    problems: list[str] = []
    if p.suffix.lower() in MEDIA_EXT and not rel.startswith(ALLOWED_MEDIA_DIRS):
        problems.append(f"{rel}: image/RAW file outside {', '.join(ALLOWED_MEDIA_DIRS)}")
        return problems
    if rel == SELF or not p.is_file():
        return problems
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return problems  # binary: covered by the size / media checks
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_LINE.search(line):
            continue
        for rx, what in PATTERNS:
            if rx.search(line):
                problems.append(f"{rel}:{n}: {what}")
        low = line.lower()
        for term in deny:
            if term in low:
                problems.append(f"{rel}:{n}: private denylist term")  # never echo the term itself
    return problems


def check_commit_msg(msg_file: str) -> int:
    text = Path(msg_file).read_text(encoding="utf-8")
    if re.search(r"^Signed-off-by: .+ <[^>]+>$", text, re.MULTILINE):
        return 0
    print("Commit message has no 'Signed-off-by:' line. Use `git commit -s` (see CONTRIBUTING.md -> DCO).")
    return 1


def main(argv: list[str]) -> int:
    if argv[:1] == ["--commit-msg"]:
        return check_commit_msg(argv[1])
    files = argv
    if argv[:1] == ["--all"]:
        # Tracked files plus untracked files that are not ignored (everything a `git add -A` would commit).
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, check=True).stdout
        files = [f for f in out.split("\0") if f]
    deny = load_denylist()
    if not deny:
        print(f"note: no private denylist at {DENYLIST.as_posix().replace(Path.home().as_posix(), '~', 1)}; "
              "client/venue names are NOT being checked.", file=sys.stderr)
    problems = [msg for f in files for msg in check_file(f, deny)]
    for msg in problems:
        print(msg)
    if problems:
        print(f"\n{len(problems)} privacy problem(s). Remove them, or (for a generic example) add 'privacy-allow' on the line.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
