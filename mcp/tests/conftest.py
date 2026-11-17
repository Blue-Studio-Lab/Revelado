# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fake_engine import FakeEngine

from revelado_mcp.config import load_config


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    photos = tmp_path / "photos"
    shoot = photos / "shoot_a"
    shoot.mkdir(parents=True)
    exports = tmp_path / "exports"
    exports.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    token = tmp_path / "state" / "runtime_token"
    token.parent.mkdir()
    token.write_text("tok-1")
    return {"root": tmp_path, "photos": photos, "shoot": shoot, "exports": exports, "outside": outside,
            "token": token}


@pytest.fixture
def config(dirs):
    env = {"REVELADO_ALLOWED_DIRS": str(dirs["photos"]), "REVELADO_EXPORT_DIRS": str(dirs["exports"]),
           "REVELADO_TOKEN_FILE": str(dirs["token"]), "REVELADO_POLL_INTERVAL_S": "0",
           "REVELADO_MCP_CONFIG": str(dirs["root"] / "no-config.json")}
    return load_config(environ=env)


@pytest.fixture
def engine():
    return FakeEngine()
