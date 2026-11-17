#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Download the CC0 test RAW files used by the engine tests and benchmarks.

Source: https://raw.pixls.us (community RAW sample archive). Every file is pinned by sha256 in
``engine/tests/data/test_raws.json`` and is licensed CC0-1.0 (public domain dedication); see
``engine/tests/data/SOURCES.md``. Files land in ``<repo>/test-data/`` (gitignored) or ``--dest``.

    python scripts/fetch_test_raws.py               # all files (~150 MB)
    python scripts/fetch_test_raws.py --only high_iso canon
    python scripts/fetch_test_raws.py --check-licence   # also re-check the licence column on raw.pixls.us

A file whose sha256 does not match is deleted and the script fails: tests never run on unverified data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "engine" / "tests" / "data" / "test_raws.json"
UA = {"User-Agent": "revelado-test-fetch (+https://github.com/Blue-Studio-Lab/Revelado)"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def check_licences(files: list[dict]) -> None:
    """Re-read the repository listing and confirm each pinned file is still listed as CC0 with the same hash."""
    req = urllib.request.Request("https://raw.pixls.us/json/getrepository.php?set=all", headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))["data"]
    by_id = {}
    for row in data:
        m = re.search(r"getfile\.php/(\d+)/nice/", row[7])
        if m:
            by_id[int(m.group(1))] = row
    for f in files:
        row = by_id.get(f["raw_pixls_us_id"])
        if row is None:
            sys.exit(f"{f['filename']}: ya no aparece en raw.pixls.us")
        if "publicdomain/zero/1.0" not in row[5]:
            sys.exit(f"{f['filename']}: la licencia ya no es CC0 en raw.pixls.us")
        if f["sha256"] not in row[7]:
            sys.exit(f"{f['filename']}: el sha256 publicado cambió")
        print(f"  licencia OK (CC0): {f['filename']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default=os.environ.get("REVELADO_TEST_DATA") or str(REPO / "test-data"))
    ap.add_argument("--only", nargs="*", help="roles: " + ", ".join(f["role"] for f in
                                                                   json.loads(MANIFEST.read_text())["files"]))
    ap.add_argument("--check-licence", action="store_true")
    a = ap.parse_args(argv)
    files = json.loads(MANIFEST.read_text())["files"]
    if a.only:
        files = [f for f in files if f["role"] in a.only]
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    if a.check_licence:
        check_licences(files)
    for f in files:
        out = dest / f["filename"]
        if out.exists() and sha256(out) == f["sha256"]:
            print(f"ok      {f['filename']}")
            continue
        url = urllib.parse.quote(f["url"], safe=":/")
        print(f"bajando {f['filename']} ({f['size_mb']} MB, {f['make']} {f['model']}, ISO {f['iso']}) ...", flush=True)
        tmp = out.with_suffix(out.suffix + ".part")
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r, open(tmp, "wb") as fh:
            shutil.copyfileobj(r, fh, 1 << 20)
        got = sha256(tmp)
        if got != f["sha256"]:
            tmp.unlink()
            sys.exit(f"{f['filename']}: sha256 no coincide ({got}); archivo descartado")
        os.replace(tmp, out)
        print(f"ok      {f['filename']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
