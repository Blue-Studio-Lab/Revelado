# SPDX-License-Identifier: GPL-3.0-or-later
"""Hardening against untrusted input: export destinations, static web files, validation messages, file modes and
catalog ids (sha256 for new rows, legacy sha1 ids kept). No private data needed."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
import types
from pathlib import Path

import pytest

from revelado.errors import ReveladoError


# ------------------------------------------------------------------------------------------------ export destination
def _ctx(tmp_path):
    return types.SimpleNamespace(settings=types.SimpleNamespace(cache_dir=(tmp_path / "cache").resolve()),
                                 catalog=types.SimpleNamespace(session=lambda sid: {"path": str(tmp_path / "raws")}))


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Home and temp folder inside tmp_path; tmp_path/outside is under no export root."""
    home = tmp_path / "home"
    tmpd = tmp_path / "tmpd"
    outside = tmp_path / "outside"
    for d in (home, tmpd, outside):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(tmpd))
    monkeypatch.delenv("REVELADO_EXPORT_ROOTS", raising=False)
    monkeypatch.delenv("REVELADO_EXPORT_DIRS", raising=False)
    return types.SimpleNamespace(home=home, tmpd=tmpd, outside=outside)


def test_export_dest_inside_home_is_accepted(tmp_path, roots):
    from revelado.develop import validate_dest
    d = validate_dest(_ctx(tmp_path), "~/Entrega/boda", [{"session_id": "s"}])
    assert d == (roots.home / "Entrega" / "boda").resolve() and d.is_dir()


@pytest.mark.parametrize("bad", ["~/Entrega\x00x", "~/a\nb", "~/Entrega/../../outside"])
def test_export_dest_rejects_nul_control_and_dotdot(tmp_path, roots, bad):
    from revelado.develop import validate_dest
    with pytest.raises(ReveladoError) as ei:
        validate_dest(_ctx(tmp_path), bad, [{"session_id": "s"}])
    assert ei.value.status == 400


def test_export_dest_outside_allowed_roots_is_rejected(tmp_path, roots):
    from revelado.develop import validate_dest
    with pytest.raises(ReveladoError):
        validate_dest(_ctx(tmp_path), str(roots.outside / "x"), [{"session_id": "s"}])
    assert not (roots.outside / "x").exists()                 # nothing created on rejection


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_export_dest_symlink_escape_is_rejected(tmp_path, roots):
    from revelado.develop import validate_dest
    (roots.home / "link").symlink_to(roots.outside, target_is_directory=True)
    with pytest.raises(ReveladoError):
        validate_dest(_ctx(tmp_path), "~/link/entrega", [{"session_id": "s"}])
    assert not (roots.outside / "entrega").exists()


def test_export_roots_env_extends_allow_list(tmp_path, roots, monkeypatch):
    from revelado.develop import validate_dest
    monkeypatch.setenv("REVELADO_EXPORT_ROOTS", str(roots.outside))
    d = validate_dest(_ctx(tmp_path), str(roots.outside / "entrega"), [{"session_id": "s"}])
    assert d == (roots.outside / "entrega").resolve() and d.is_dir()


def test_mcp_export_dirs_are_honoured_by_the_engine(tmp_path, roots, monkeypatch):
    """A folder the MCP server allows (REVELADO_EXPORT_DIRS) must not then be refused by the engine."""
    from revelado.develop import validate_dest
    monkeypatch.setenv("REVELADO_EXPORT_DIRS", str(roots.outside / "a") + os.pathsep + str(roots.outside / "b"))
    d = validate_dest(_ctx(tmp_path), str(roots.outside / "b" / "entrega"), [{"session_id": "s"}])
    assert d == (roots.outside / "b" / "entrega").resolve() and d.is_dir()
    with pytest.raises(ReveladoError):
        validate_dest(_ctx(tmp_path), str(roots.outside / "c"), [{"session_id": "s"}])


def test_export_root_prefix_is_not_a_sibling_match(tmp_path, roots, monkeypatch):
    """'<root>2' is not inside '<root>'."""
    from revelado.develop import validate_dest
    monkeypatch.setenv("REVELADO_EXPORT_ROOTS", str(roots.outside / "nas"))
    with pytest.raises(ReveladoError):
        validate_dest(_ctx(tmp_path), str(roots.outside / "nas2" / "x"), [{"session_id": "s"}])
    assert not (roots.outside / "nas2").exists()


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="case-insensitive filesystems only")
def test_export_dest_matches_root_case_insensitively(tmp_path, roots):
    from revelado.develop import validate_dest
    probe = tmp_path / "CaseProbe"
    probe.mkdir()
    if not (tmp_path / "caseprobe").exists():
        pytest.skip("case-sensitive volume")
    home = os.path.realpath(roots.home)
    d = validate_dest(_ctx(tmp_path), home.swapcase() + os.sep + "Entrega", [{"session_id": "s"}])
    assert str(d).startswith(home)                            # the root keeps its real spelling
    assert (roots.home / "Entrega").is_dir()


def test_export_dest_inside_cache_or_originals_is_rejected(tmp_path, roots, monkeypatch):
    from revelado.develop import validate_dest
    monkeypatch.setenv("REVELADO_EXPORT_ROOTS", str(tmp_path))
    for bad in (tmp_path / "cache" / "x", tmp_path / "raws" / "x"):
        with pytest.raises(ReveladoError):
            validate_dest(_ctx(tmp_path), str(bad), [{"session_id": "s"}])
        assert not bad.exists()


@pytest.mark.parametrize("bad", ["\\\\?\\C:\\Windows\\x", "\\\\.\\PhysicalDrive0", "//?/C:/x"])
def test_export_dest_rejects_device_paths(tmp_path, roots, bad):
    from revelado.develop import validate_dest
    with pytest.raises(ReveladoError):
        validate_dest(_ctx(tmp_path), bad, [{"session_id": "s"}])


def test_default_export_roots_cover_shared_and_network_folders(monkeypatch):
    from revelado import develop
    monkeypatch.delenv("REVELADO_EXPORT_ROOTS", raising=False)
    monkeypatch.delenv("REVELADO_EXPORT_DIRS", raising=False)
    roots = develop.export_roots()
    if sys.platform == "darwin":
        assert "/Volumes" in roots and "/Users/Shared" in roots
    elif sys.platform == "win32":
        assert "\\\\" in roots and any(r.upper() == "C:" for r in roots)
    else:
        assert {"/media", "/mnt", "/srv"} <= set(roots) and f"/run/user/{os.getuid()}" in roots


@pytest.mark.skipif(sys.platform != "win32", reason="Windows system folders")
def test_windows_system_folders_are_refused(tmp_path, roots):
    from revelado.develop import validate_dest
    sysroot = os.environ.get("SystemRoot", "C:\\Windows")
    with pytest.raises(ReveladoError):
        validate_dest(_ctx(tmp_path), sysroot.lower() + "\\revelado-test", [{"session_id": "s"}])


# ------------------------------------------------------------------------------------------------ file placement
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.parametrize("mode", [0o600, 0o640])
def test_place_copy_fallback_keeps_temp_file_mode(tmp_path, monkeypatch, mode):
    from revelado import develop
    tmp = tmp_path / "t.jpg"
    tmp.write_bytes(b"jpeg")
    os.chmod(tmp, mode)

    def no_link(*a, **k):
        raise OSError("no hard links here")
    monkeypatch.setattr(develop.os, "link", no_link)
    dest = tmp_path / "out"
    dest.mkdir()
    old = os.umask(0o022)
    try:
        out = develop._place(tmp, dest, "foto", ".jpg")
    finally:
        os.umask(old)
    assert out.read_bytes() == b"jpeg" and not tmp.exists()
    assert stat.S_IMODE(out.stat().st_mode) == mode & ~0o022


# ------------------------------------------------------------------------------------------------ built web app
@pytest.fixture
def dist(tmp_path, monkeypatch):
    from revelado.api import app as appmod
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>index</html>")
    (d / "assets" / "a.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("secret")
    monkeypatch.setattr(appmod, "_DIST", d)
    return d


def test_dist_file_serves_only_files_inside_dist(dist, tmp_path):
    from revelado.api.app import _dist_file
    assert _dist_file("assets/a.js") == os.path.realpath(dist / "assets" / "a.js")
    for bad in ("", "../secret.txt", "assets/../../secret.txt", "/etc/passwd", "assets/a.js\x00.png",
                "assets\\..\\..\\secret.txt", "assets//a.js", "./index.html", "assets", "missing.js"):
        assert _dist_file(bad) is None, bad


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_dist_file_rejects_symlink_escape(dist, tmp_path):
    from revelado.api.app import _dist_file
    (dist / "leak.txt").symlink_to(tmp_path / "secret.txt")
    assert _dist_file("leak.txt") is None


def test_web_app_falls_back_to_index_on_traversal(dist, settings):
    from fastapi.testclient import TestClient
    from revelado.api import app as appmod
    appmod.CTX = appmod.build_ctx(settings)
    try:
        with TestClient(appmod.app) as c:
            assert c.get("/assets/a.js").text == "console.log(1)"
            assert c.get("/assets/..%2F..%2Fsecret.txt").text == "<html>index</html>"
    finally:
        appmod.CTX = None


# ------------------------------------------------------------------------------------------------ error bodies
def test_validation_message_never_echoes_exception_text():
    from revelado.api.app import _val_msg

    class Broken:
        def errors(self):
            raise RuntimeError("Traceback ... /private/path/secret")

        def __str__(self):
            return "Traceback ... /private/path/secret"
    msg = _val_msg(Broken())
    assert "secret" not in msg and "Traceback" not in msg and msg


# ------------------------------------------------------------------------------------------------ catalog ids
def _legacy_sid(p: Path) -> str:
    return hashlib.sha1(str(p.resolve()).encode()).hexdigest()[:12]


def _legacy_pid(sid: str, key: str) -> str:
    return hashlib.sha1(f"{sid}:{key}".encode()).hexdigest()[:16]


def test_new_ids_are_sha256(tmp_path):
    from revelado.catalog import photo_id_for, session_id_for
    sid = session_id_for(tmp_path)
    assert sid == hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()[:12]
    assert photo_id_for(sid, "img_0001") == hashlib.sha256(f"{sid}:img_0001".encode()).hexdigest()[:16]


def test_legacy_sha1_ids_survive_reopen(settings, jpg_only_folder):
    """A catalog written with the old sha1 ids keeps its session/photo ids (and the user's ratings) on reopen."""
    from revelado import library
    from revelado.catalog import Catalog
    root = jpg_only_folder.resolve()
    cat = Catalog(settings.catalog_path)
    sid = _legacy_sid(root)
    rows = [{"id": _legacy_pid(sid, f"img_{i:04d}"), "stem_key": f"img_{i:04d}", "name": f"IMG_{i:04d}",
             "raw_path": None, "jpg_path": str(root / f"IMG_{i:04d}.JPG"), "mtime": 0.0, "meta": {},
             "capture_ts": float(i)} for i in range(2)]
    cat.upsert_photos(sid, rows)
    cat.upsert_session(sid, root, root.name, len(rows))
    cat.set_rating(rows[0]["id"], 5, "pick", True)

    keys = set(library.scan(root)[0])
    assert {r["stem_key"] for r in rows} <= keys, keys       # the legacy rows use the scanner's own keys

    res = library.open_library(cat, settings, str(root))
    assert res["session_id"] == sid and res["restored"] is True
    ids = {r["stem_key"]: r["id"] for r in cat.photos(sid)}
    assert ids["img_0000"] == rows[0]["id"] and ids["img_0001"] == rows[1]["id"]
    assert cat.photo(rows[0]["id"])["rating"] == 5
    # a file that was not catalogued before gets a new (sha256) id inside the legacy session
    from revelado.catalog import photo_id_for
    new = (keys - {r["stem_key"] for r in rows}).pop()
    assert ids[new] == photo_id_for(sid, new)
    assert len(cat.sessions()) == 1
