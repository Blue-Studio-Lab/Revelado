# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from revelado_mcp.config import ConfigError, canonical, is_within, load_config


def base_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"REVELADO_MCP_CONFIG": str(tmp_path / "none.json"), **extra}


def test_defaults_without_allow_list(tmp_path):
    cfg = load_config(environ=base_env(tmp_path))
    assert cfg.engine_url == "http://127.0.0.1:8790"
    assert cfg.allowed_dirs == () and cfg.export_dirs == ()
    assert any("ALLOWED_DIRS" in n for n in cfg.notes)
    with pytest.raises(ConfigError, match="No photo folders"):
        cfg.check_source(str(tmp_path))
    with pytest.raises(ConfigError, match="No export folders"):
        cfg.check_export(str(tmp_path), [])


@pytest.mark.parametrize("url", ["http://192.168.1.5:8790", "https://example.com", "ftp://127.0.0.1"])
def test_engine_url_must_be_loopback(tmp_path, url):
    with pytest.raises(ConfigError):
        load_config(environ=base_env(tmp_path, REVELADO_ENGINE_URL=url))


def test_env_list_and_cli_override(tmp_path):
    a, b, c = (tmp_path / n for n in "abc")
    for d in (a, b, c):
        d.mkdir()
    cfg = load_config(environ=base_env(tmp_path, REVELADO_ALLOWED_DIRS=f"{a}{os.pathsep}{b}"))
    assert cfg.allowed_dirs == (a.resolve(), b.resolve())
    cfg2 = load_config(allowed_dirs=[str(c)], environ=base_env(tmp_path, REVELADO_ALLOWED_DIRS=str(a)))
    assert cfg2.allowed_dirs == (c.resolve(),)


def test_config_file(tmp_path):
    d = tmp_path / "pics"
    d.mkdir()
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"allowed_dirs": [str(d)], "max_batch": 7}))
    cfg = load_config(environ={"REVELADO_MCP_CONFIG": str(f)})
    assert cfg.allowed_dirs == (d.resolve(),) and cfg.max_batch == 7 and cfg.config_file == f


def test_relative_and_root_rejected(tmp_path):
    with pytest.raises(ConfigError, match="absolute"):
        load_config(environ=base_env(tmp_path, REVELADO_ALLOWED_DIRS="relative/dir"))
    with pytest.raises(ConfigError, match="root"):
        load_config(environ=base_env(tmp_path, REVELADO_ALLOWED_DIRS=Path(tmp_path.anchor).as_posix()))


def test_containment_and_traversal(dirs, config):
    assert config.check_source(str(dirs["shoot"])) == dirs["shoot"].resolve()
    with pytest.raises(ConfigError, match="outside"):
        config.check_source(str(dirs["outside"]))
    with pytest.raises(ConfigError, match="outside"):
        config.check_source(str(dirs["shoot"] / ".." / ".." / "outside"))
    # sibling with a common prefix is not "inside"
    sib = dirs["root"] / "photos_evil"
    sib.mkdir()
    with pytest.raises(ConfigError):
        config.check_source(str(sib))


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_escape_rejected(dirs, config):
    link = dirs["photos"] / "link"
    link.symlink_to(dirs["outside"], target_is_directory=True)
    with pytest.raises(ConfigError, match="outside"):
        config.check_source(str(link))


def test_export_never_inside_source(dirs, config):
    inside = dirs["exports"] / "shoot_a"
    assert config.check_export(str(inside), [dirs["shoot"]]) == inside.resolve()
    with pytest.raises(ConfigError, match="source"):
        load_config(environ={"REVELADO_MCP_CONFIG": str(dirs["root"] / "x.json"),
                             "REVELADO_ALLOWED_DIRS": str(dirs["photos"]),
                             "REVELADO_EXPORT_DIRS": str(dirs["photos"])}).check_export(
            str(dirs["shoot"] / "out"), [dirs["shoot"]])


def test_is_within_basic():
    root = canonical(os.path.abspath("/tmp/a"))
    assert is_within(root, root)
    assert is_within(root / "b", root)
    assert not is_within(canonical(os.path.abspath("/tmp/ab")), root)
