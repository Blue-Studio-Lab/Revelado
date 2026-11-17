# SPDX-License-Identifier: GPL-3.0-or-later
"""Every user-visible name in one place, so a rename touches only this module (plus the manifests/README)."""
from __future__ import annotations

APP_NAME = "Revelado"
SERVER_NAME = "revelado"                  # MCP server name shown by clients
DIST_NAME = "revelado-mcp"                # PyPI distribution / console script
ENV_PREFIX = "REVELADO_"                  # environment variables read by this server
USER_DIR_NAME = "Revelado"                # per-user config/state folder (macOS / Windows)
USER_DIR_NAME_XDG = "revelado"            # per-user config/state folder (Linux, XDG)

# The engine authenticates every request with a per-start random token sent in this header.
TOKEN_HEADER = "X-Revelado-Token"


def env(name: str) -> str:
    """Full environment variable name, e.g. ``env("ALLOWED_DIRS") -> "REVELADO_ALLOWED_DIRS"``."""
    return ENV_PREFIX + name
