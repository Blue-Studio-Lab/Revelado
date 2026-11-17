# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-platform resolver for the external command-line tools (rawtherapee-cli, exiftool, jpegtran, swiftc).

Search order for a tool ``name``:
  1. an explicit path from the environment (``REVELADO_<NAME>``),
  2. ``PATH`` (``shutil.which``; on Windows this also tries ``name.exe``),
  3. the usual install locations of each OS (Homebrew on macOS, ``/usr/bin`` / Flatpak on Linux,
     ``Program Files`` on Windows).
Nothing here is specific to the author's machine.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

# env var suffix for each tool
ENV_KEYS = {"rawtherapee-cli": "RAWTHERAPEE_CLI", "exiftool": "EXIFTOOL", "jpegtran": "JPEGTRAN", "swiftc": "SWIFTC"}


def _candidates(name: str) -> list[str]:
    exe = name + (".exe" if sys.platform == "win32" else "")
    out: list[str] = []
    if sys.platform == "darwin":
        out += [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}"]
        if name == "rawtherapee-cli":
            out += ["/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli"]
    elif sys.platform == "win32":
        pf = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for base in filter(None, pf):
            if name == "rawtherapee-cli":
                out += sorted(glob.glob(os.path.join(base, "RawTherapee", "*", exe)), reverse=True)
                out += [os.path.join(base, "RawTherapee", exe)]
            elif name == "exiftool":
                out += [os.path.join(base, "ExifTool", "exiftool.exe"), os.path.join(base, "exiftool", "exiftool.exe"),
                        os.path.join(base, "ExifTool", "exiftool(-k).exe")]
            elif name == "jpegtran":
                out += sorted(glob.glob(os.path.join(base, "libjpeg-turbo*", "bin", exe)))
    else:
        out += [f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/snap/bin/{name}"]
    return out


@lru_cache(maxsize=None)
def find_tool(name: str, explicit: str | None = None) -> str | None:
    """Absolute path of an executable tool, or None when it is not installed."""
    key = ENV_KEYS.get(name, name.upper().replace("-", "_"))
    for v in (explicit, os.environ.get(f"REVELADO_{key}")):
        if v and Path(v).exists():
            return str(Path(v))
    p = shutil.which(name)
    if p:
        return p
    for c in _candidates(name):
        if Path(c).exists() and os.access(c, os.X_OK):
            return c
    return None


def require_tool(name: str, explicit: str | None = None) -> str:
    from .errors import ReveladoError
    p = find_tool(name, explicit)
    if not p:
        hint = {"rawtherapee-cli": "instala RawTherapee 5.10+ (https://rawtherapee.com)",
                "exiftool": "instala ExifTool (https://exiftool.org)"}.get(name, "instálalo y ponlo en el PATH")
        raise ReveladoError("E_TOOL_MISSING", f"{name} no encontrado: {hint}")
    return p
