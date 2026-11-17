# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent catalog (SQLite, WAL). Survives restarts: reopening a folder restores ratings, flags, cull results
and developed outputs.

Identity:
  session_id = sha1(resolved folder path)[:12]          stable across restarts
  photo_id   = sha1(session_id + relative stem key)[:16] stable for the same file inside the same folder
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT, count INTEGER DEFAULT 0,
  created_at REAL, opened_at REAL
);
CREATE TABLE IF NOT EXISTS photos (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, stem_key TEXT NOT NULL, name TEXT NOT NULL,
  raw_path TEXT, jpg_path TEXT, mtime REAL, meta_json TEXT, present INTEGER DEFAULT 1,
  rating INTEGER DEFAULT 0, flag TEXT, cull_json TEXT, claude_json TEXT, capture_ts REAL,
  burst_id TEXT, updated_at REAL,
  UNIQUE(session_id, stem_key)
);
CREATE INDEX IF NOT EXISTS ix_photos_session ON photos(session_id, capture_ts);
CREATE TABLE IF NOT EXISTS outputs (
  output_id TEXT PRIMARY KEY, photo_id TEXT NOT NULL, job_id TEXT, recipe_json TEXT, recipe_key TEXT,
  dir TEXT NOT NULL, full_path TEXT, web_path TEXT, tiff_path TEXT, width INTEGER, height INTEGER,
  sidecar_json TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS ix_outputs_photo ON outputs(photo_id, created_at);
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, kind TEXT, state TEXT, payload_json TEXT, result_json TEXT,
  created_at REAL, finished_at REAL
);
"""


def session_id_for(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]


def photo_id_for(session_id: str, stem_key: str) -> str:
    return hashlib.sha1(f"{session_id}:{stem_key}".encode()).hexdigest()[:16]


class Catalog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._wlock = threading.RLock()
        with self.tx() as c:
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
            # jobs that were running when the engine died are marked interrupted
            c.execute("UPDATE jobs SET state='error', result_json=json_set(COALESCE(result_json,'{}'),"
                      "'$.interrupted', 1) WHERE state IN ('queued','running')")

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.c = c
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self._conn()
        with self._wlock:
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, args).fetchall()

    def q1(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, args).fetchone()

    # ---------------------------------------------------------------- sessions
    def upsert_session(self, sid: str, path: Path, name: str, count: int) -> bool:
        """Returns True when the session already existed (restored)."""
        now = time.time()
        with self.tx() as c:
            existed = c.execute("SELECT 1 FROM sessions WHERE session_id=?", (sid,)).fetchone() is not None
            c.execute("INSERT INTO sessions(session_id, path, name, count, created_at, opened_at) VALUES(?,?,?,?,?,?) "
                      "ON CONFLICT(session_id) DO UPDATE SET count=excluded.count, opened_at=excluded.opened_at, "
                      "name=excluded.name", (sid, str(path), name, count, now, now))
        return existed

    def session(self, sid: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM sessions WHERE session_id=?", (sid,))

    def sessions(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM sessions ORDER BY opened_at DESC")]

    # ---------------------------------------------------------------- photos
    def upsert_photos(self, sid: str, rows: list[dict]) -> None:
        """rows: {id, stem_key, name, raw_path, jpg_path, mtime, meta, capture_ts}. Keeps rating/flag/cull."""
        now = time.time()
        with self.tx() as c:
            c.execute("UPDATE photos SET present=0 WHERE session_id=?", (sid,))
            c.executemany(
                "INSERT INTO photos(id, session_id, stem_key, name, raw_path, jpg_path, mtime, meta_json, present, "
                "capture_ts, updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, raw_path=excluded.raw_path, jpg_path=excluded.jpg_path, mtime=excluded.mtime, "
                "meta_json=excluded.meta_json, present=1, capture_ts=excluded.capture_ts, updated_at=excluded.updated_at",
                [(r["id"], sid, r["stem_key"], r["name"], r["raw_path"], r["jpg_path"], r["mtime"],
                  json.dumps(r["meta"], ensure_ascii=False, default=str), r["capture_ts"], now) for r in rows])

    def photo(self, pid: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM photos WHERE id=?", (pid,))

    def photos(self, sid: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM photos WHERE session_id=? AND present=1 ORDER BY capture_ts, name", (sid,))

    def known_mtimes(self, sid: str) -> dict[str, tuple[float, str]]:
        return {r["stem_key"]: (r["mtime"], r["meta_json"]) for r in
                self.q("SELECT stem_key, mtime, meta_json FROM photos WHERE session_id=?", (sid,))}

    def set_rating(self, pid: str, rating: int | None, flag: str | None, set_flag: bool) -> None:
        with self.tx() as c:
            if rating is not None:
                c.execute("UPDATE photos SET rating=?, updated_at=? WHERE id=?", (rating, time.time(), pid))
            if set_flag:
                c.execute("UPDATE photos SET flag=?, updated_at=? WHERE id=?", (flag, time.time(), pid))

    def set_cull(self, pid: str, technical: dict | None = None, claude: dict | None = None,
                 burst_id: str | None = None) -> None:
        with self.tx() as c:
            if technical is not None:
                c.execute("UPDATE photos SET cull_json=?, burst_id=? WHERE id=?",
                          (json.dumps(technical, default=str), burst_id, pid))
            if claude is not None:
                c.execute("UPDATE photos SET claude_json=? WHERE id=?", (json.dumps(claude, default=str), pid))

    # ---------------------------------------------------------------- outputs
    def add_output(self, row: dict) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO outputs(output_id, photo_id, job_id, recipe_json, recipe_key, dir, "
                      "full_path, web_path, tiff_path, width, height, sidecar_json, created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (row["output_id"], row["photo_id"], row.get("job_id"), json.dumps(row.get("recipe") or {}),
                       row.get("recipe_key"), row["dir"], row.get("full_path"), row.get("web_path"),
                       row.get("tiff_path"), row.get("width"), row.get("height"),
                       json.dumps(row.get("sidecar") or {}, default=str), time.time()))

    def outputs(self, pid: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM outputs WHERE photo_id=? ORDER BY created_at DESC", (pid,))

    def output(self, oid: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM outputs WHERE output_id=?", (oid,))

    def output_counts(self, sid: str) -> dict[str, int]:
        return {r[0]: r[1] for r in self.q(
            "SELECT o.photo_id, COUNT(*) FROM outputs o JOIN photos p ON p.id=o.photo_id WHERE p.session_id=? "
            "GROUP BY o.photo_id", (sid,))}

    # ---------------------------------------------------------------- jobs (history only; live state is in memory)
    def save_job(self, job_id: str, kind: str, state: str, payload: dict, result: dict | None,
                 finished: bool) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO jobs(job_id, kind, state, payload_json, result_json, created_at, finished_at) "
                      "VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET state=excluded.state, "
                      "result_json=excluded.result_json, finished_at=excluded.finished_at",
                      (job_id, kind, state, json.dumps(payload, default=str), json.dumps(result or {}, default=str),
                       time.time(), time.time() if finished else None))

    def job(self, job_id: str) -> dict[str, Any] | None:
        r = self.q1("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return dict(r) if r else None
