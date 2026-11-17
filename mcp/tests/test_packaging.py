# SPDX-License-Identifier: GPL-3.0-or-later
"""The plugin, the .mcpb manifest and the server must agree (tool names, versions, server name)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from revelado_mcp import __version__
from revelado_mcp.__main__ import main
from revelado_mcp.naming import DIST_NAME, SERVER_NAME
from revelado_mcp.server import TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "revelado"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_mcpb_manifest_matches_server():
    m = load(ROOT / "manifest.json")
    assert m["name"] == SERVER_NAME and m["version"] == __version__
    assert [t["name"] for t in m["tools"]] == list(TOOL_NAMES)
    assert (ROOT / m["server"]["entry_point"]).is_file()
    assert DIST_NAME in m["server"]["mcp_config"]["args"]


def test_plugin_manifest_and_mcp_json():
    p = load(PLUGIN / ".claude-plugin" / "plugin.json")
    assert p["name"] == SERVER_NAME and p["version"] == __version__
    servers = load(PLUGIN / ".mcp.json")["mcpServers"]
    assert list(servers) == [SERVER_NAME] and servers[SERVER_NAME]["args"] == [DIST_NAME]
    mk = load(ROOT / "plugin" / "marketplace.json")
    assert mk["plugins"][0]["version"] == __version__


@pytest.mark.parametrize("skill", ["revelar-sesion", "rescate", "seleccionar"])
def test_skills_have_frontmatter_and_only_real_tools(skill):
    text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    fm = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", text)
    assert fm and fm.group(1) == skill and len(fm.group(2)) > 40
    used = set(re.findall(r"`([a-z_]+)\(", text)) | set(re.findall(r"`([a-z_]+)`", text))
    tool_like = {u for u in used if "_" in u and u in TOOL_NAMES or u.endswith(("_photos", "_sample", "_batch"))}
    assert tool_like <= set(TOOL_NAMES)


def test_pyproject_version_matches():
    assert f'version = "{__version__}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_cli_rejects_remote_engine(capsys):
    assert main(["--engine-url", "http://10.0.0.2:8790", "--check"]) == 2
    assert "configuration error" in capsys.readouterr().err
