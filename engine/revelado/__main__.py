# SPDX-License-Identifier: GPL-3.0-or-later
"""``python -m revelado`` - start the engine API (127.0.0.1 only)."""
from __future__ import annotations

import argparse

from . import __version__


def main() -> int:
    import uvicorn
    from .config import get_settings
    s = get_settings()
    ap = argparse.ArgumentParser(prog="revelado", description="Revelado engine (API local en 127.0.0.1)")
    ap.add_argument("--host", default=s.host)
    ap.add_argument("--port", type=int, default=s.port)
    ap.add_argument("--version", action="version", version=f"revelado {__version__}")
    a = ap.parse_args()
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("El motor solo escucha en localhost (127.0.0.1).")
    uvicorn.run("revelado.api.app:app", host=a.host, port=a.port, log_level="info", workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
