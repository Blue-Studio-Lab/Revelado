# SPDX-License-Identifier: GPL-3.0-or-later
"""Library: recursive scan, RAW+JPG pairing, metadata, persistent session.

Pairing key = relative directory (with format folders such as ``JPG/``, ``RAW/``, ``ARW/`` removed) + file stem,
case-insensitive. So ``JPG/IMG_0001.JPG`` pairs with ``RAW/IMG_0001.ARW``, and ``_rejects/JPG/X.JPG`` stays
separate from ``JPG/X.JPG``.

Folders whose name starts with ``.`` or ``_`` are skipped by default (system / derived folders such as galleries
or rejects); the response lists them so the user knows, and ``include_hidden`` scans them too.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from .catalog import Catalog, photo_id_for, session_id_for
from .config import Settings
from .errors import ReveladoError
from .log import get_logger
from . import metadata

log = get_logger("library")

RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".nrw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr", ".iiq",
           ".x3f", ".erf", ".kdc", ".mrw", ".rwl"}
JPG_EXT = {".jpg", ".jpeg"}
FORMAT_DIRS = {"jpg", "jpeg", "raw", "raws", "arw", "cr2", "cr3", "nef", "dng", "raf", "orf"}


def _meta_current(meta_json: str) -> bool:
    """False for catalog metadata written by an older normalise() (re-read it once)."""
    try:
        return int(json.loads(meta_json).get("meta_v") or 1) >= metadata.META_VERSION
    except (ValueError, TypeError, AttributeError):
        return False


def _salt(settings: Settings) -> str:
    p = settings.cache_dir / ".salt"
    try:
        return p.read_text().strip()
    except FileNotFoundError:
        s = secrets.token_hex(16)
        p.write_text(s)
        os.chmod(p, 0o600)
        return s


def scan(root: Path, include_hidden: bool = False) -> tuple[dict[str, dict], list[str]]:
    groups: dict[str, dict] = {}
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in sorted(dirnames):
            if not include_hidden and (d.startswith(".") or d.startswith("_")):
                skipped.append(str(Path(dirpath, d).relative_to(root)))
            else:
                keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if fn.startswith("."):
                continue
            p = Path(dirpath, fn)
            ext = p.suffix.lower()
            kind = "raw" if ext in RAW_EXT else "jpg" if ext in JPG_EXT else None
            if not kind:
                continue
            rel_parts = [x for x in p.parent.relative_to(root).parts if x.lower() not in FORMAT_DIRS]
            key = "/".join(rel_parts + [p.stem]).lower()
            g = groups.setdefault(key, {"raw": None, "jpg": None, "name": p.stem})
            if g[kind] is None:
                g[kind] = p
            else:   # duplicate stem in the same group: keep the first in sorted order, log the rest
                log.warning("duplicate %s for key %s: %s ignored", kind, key, p.name)
    return groups, skipped


def open_library(cat: Catalog, settings: Settings, path_str: str, include_hidden: bool = False) -> dict:
    if not path_str or not isinstance(path_str, str):
        raise ReveladoError("E_BAD_INPUT", "falta 'path'")
    root = Path(path_str).expanduser()
    if not root.exists() or not root.is_dir():
        raise ReveladoError("E_NOT_FOUND", "la carpeta no existe o no es una carpeta")
    root = root.resolve()
    if root == settings.cache_dir or settings.cache_dir in root.parents:
        raise ReveladoError("E_BAD_INPUT", "no se puede abrir la carpeta de caché del motor")
    t0 = time.time()
    groups, skipped = scan(root, include_hidden)
    sid = cat.existing_session_id(root) or session_id_for(root)    # reuse ids minted by older versions
    known = cat.known_mtimes(sid)
    known_ids = cat.photo_ids(sid)
    salt = _salt(settings)

    # read metadata only for new/changed files (from the RAW when present: profiles resolve from the RAW)
    to_read: list[Path] = []
    rows: list[dict] = []
    for key, g in groups.items():
        src = g["raw"] or g["jpg"]
        mtime = max((p.stat().st_mtime for p in (g["raw"], g["jpg"]) if p), default=0.0)
        g["mtime"] = mtime
        old = known.get(key)
        if old and abs((old[0] or 0) - mtime) < 1e-3 and old[1] and _meta_current(old[1]):
            g["meta_json"] = old[1]
        else:
            to_read.append(src)
    meta_new = metadata.read_batch(settings.exiftool, to_read, salt) if to_read else {}
    import json
    for key, g in groups.items():
        src = g["raw"] or g["jpg"]
        meta = json.loads(g["meta_json"]) if "meta_json" in g else meta_new.get(str(src), {"error": "no metadata"})
        if g["jpg"] and g["raw"] and not meta.get("width"):
            meta.setdefault("note", "dimensiones desconocidas")
        rows.append({"id": known_ids.get(key) or photo_id_for(sid, key), "stem_key": key, "name": g["name"],
                     "raw_path": str(g["raw"]) if g["raw"] else None, "jpg_path": str(g["jpg"]) if g["jpg"] else None,
                     "mtime": g["mtime"], "meta": meta,
                     "capture_ts": meta.get("capture_ts") or g["mtime"]})
    cat.upsert_photos(sid, rows)
    restored = cat.upsert_session(sid, root, root.name, len(rows))
    pairs = sum(1 for g in groups.values() if g["raw"] and g["jpg"])
    raw_only = sum(1 for g in groups.values() if g["raw"] and not g["jpg"])
    jpg_only = sum(1 for g in groups.values() if g["jpg"] and not g["raw"])
    res = {"session_id": sid, "count": len(rows), "pairs": pairs, "raw_only": raw_only, "jpg_only": jpg_only,
           "restored": restored, "path": str(root), "name": root.name, "skipped_dirs": skipped,
           "metadata_read": len(to_read), "seconds": round(time.time() - t0, 2)}
    log.info("library open %s: %d photos (%d pairs) in %.2fs, %d metadata reads, restored=%s",
             root.name, len(rows), pairs, res["seconds"], len(to_read), restored)
    return res
