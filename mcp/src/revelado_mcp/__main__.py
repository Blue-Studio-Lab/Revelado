# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line: ``revelado-mcp [--allowed-dir DIR ...] [--export-dir DIR ...] [--engine-url URL] [--check]``.

Runs the MCP server over stdio (what Claude Code / Claude Desktop launch). Absolute imports on purpose: this
file is also the ``.mcpb`` bundle entry point.
"""
from __future__ import annotations

import argparse
import json
import sys

import anyio

from revelado_mcp import __version__
from revelado_mcp.config import ConfigError, load_config
from revelado_mcp.engine import EngineClient, EngineError
from revelado_mcp.naming import APP_NAME, DIST_NAME, env


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=DIST_NAME, description=f"MCP server for the local {APP_NAME} engine (stdio).")
    p.add_argument("--allowed-dir", "--allowed-dirs", dest="allowed", nargs="*", action="extend", default=[],
                   metavar="DIR", help=f"photo folder the server may open (repeatable; overrides {env('ALLOWED_DIRS')})")
    p.add_argument("--export-dir", "--export-dirs", dest="exports", nargs="*", action="extend", default=[],
                   metavar="DIR", help=f"folder exports may be written to (repeatable; overrides {env('EXPORT_DIRS')})")
    p.add_argument("--engine-url", default=None, help=f"engine URL (default http://127.0.0.1:8790; {env('ENGINE_URL')})")
    p.add_argument("--check", action="store_true", help="print the configuration and engine health, then exit")
    p.add_argument("--version", action="version", version=f"{DIST_NAME} {__version__}")
    return p


async def _check(cfg) -> int:
    client = EngineClient(cfg)
    out: dict = {"config": cfg.public()}
    code = 0
    try:
        out["engine"] = await client.get_json("/api/health")
    except EngineError as e:
        out["engine_error"] = str(e)
        code = 1
    finally:
        await client.aclose()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        # Empty strings can arrive from unset bundle/plugin variables; drop them.
        cfg = load_config(allowed_dirs=[a for a in args.allowed if a.strip()] or None,
                          export_dirs=[a for a in args.exports if a.strip()] or None, engine_url=args.engine_url)
    except ConfigError as e:
        print(f"{DIST_NAME}: configuration error: {e}", file=sys.stderr)
        return 2
    if args.check:
        return anyio.run(_check, cfg)
    from revelado_mcp.server import build_server
    for note in cfg.notes:
        print(f"{DIST_NAME}: note: {note}", file=sys.stderr)   # stdout is the MCP channel; never print there
    build_server(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
