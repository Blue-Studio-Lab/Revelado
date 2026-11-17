# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and path safety.

Precedence: command-line flags > environment variables (``REVELADO_*``) > JSON config file > defaults.

The server never touches a path that is not inside an allow-listed folder:

* ``allowed_dirs`` - folders whose photos may be opened (read-only for this server; the engine never modifies
  originals).
* ``export_dirs``  - the only folders ``export`` may write into. Exports into a photo source folder are refused.

Paths are made absolute and resolved (symlinks followed) *before* the containment check, so a symlink that
points outside an allowed folder is rejected.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .naming import USER_DIR_NAME, USER_DIR_NAME_XDG, env

DEFAULT_ENGINE_URL = "http://127.0.0.1:8790"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class ConfigError(ValueError):
    """Invalid configuration (bad engine URL, relative allow-list entry, ...)."""


# ------------------------------------------------------------------------------------------------ per-user folders
def user_config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / USER_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / USER_DIR_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / USER_DIR_NAME_XDG


def user_state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / USER_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / USER_DIR_NAME
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / USER_DIR_NAME_XDG


# ------------------------------------------------------------------------------------------------ path helpers
def canonical(path: str | os.PathLike[str]) -> Path:
    """Absolute, user-expanded, symlink-resolved path. Raises ConfigError for relative or malformed input."""
    s = os.fspath(path)
    if not s or "\x00" in s:
        raise ConfigError("empty or malformed path")
    p = Path(s).expanduser()
    if not p.is_absolute():
        raise ConfigError(f"path must be absolute: {s!r}")
    return p.resolve(strict=False)


def is_within(child: Path, root: Path) -> bool:
    """True if ``child`` is ``root`` or below it (case-insensitive where the OS is)."""
    c = os.path.normcase(str(child))
    r = os.path.normcase(str(root)).rstrip("\\/")
    sep = os.sep
    return c == r or c.startswith(r + sep) or (sep != "/" and c.startswith(r + "/"))


def _is_fs_root(p: Path) -> bool:
    return p == Path(p.anchor)


def _split_paths(value: str) -> list[str]:
    return [x.strip() for x in value.split(os.pathsep) if x.strip()]


def _roots(values: list[str], what: str) -> tuple[Path, ...]:
    out: list[Path] = []
    for v in values:
        p = canonical(v)
        if _is_fs_root(p):
            raise ConfigError(f"{what}: a whole drive / filesystem root is not allowed ({p})")
        if p not in out:
            out.append(p)
    return tuple(out)


# ------------------------------------------------------------------------------------------------ config
@dataclass(frozen=True)
class Config:
    engine_url: str = DEFAULT_ENGINE_URL
    token: str | None = None                       # explicit token (env); otherwise read from token_files
    token_files: tuple[Path, ...] = ()
    allowed_dirs: tuple[Path, ...] = ()
    export_dirs: tuple[Path, ...] = ()
    max_batch: int = 500
    max_wait_s: float = 600.0
    poll_interval_s: float = 1.0
    request_timeout_s: float = 120.0
    config_file: Path | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    # ---- safety checks used by the tools
    def check_source(self, path: str) -> Path:
        """Return the canonical path if it is inside an allowed photo folder, else raise ConfigError."""
        if not self.allowed_dirs:
            raise ConfigError(
                f"No photo folders are allow-listed. Set {env('ALLOWED_DIRS')} (paths separated by "
                f"'{os.pathsep}') or 'allowed_dirs' in the config file, then restart the MCP server.")
        p = canonical(path)
        if not any(is_within(p, r) for r in self.allowed_dirs):
            raise ConfigError(f"{p} is outside the allow-listed photo folders: "
                              + ", ".join(str(r) for r in self.allowed_dirs))
        return p

    def check_export(self, path: str, source_dirs: list[Path]) -> Path:
        """Return the canonical export folder if allowed; never inside a photo source folder."""
        if not self.export_dirs:
            raise ConfigError(
                f"No export folders are allow-listed. Set {env('EXPORT_DIRS')} or 'export_dirs' in the "
                "config file, then restart the MCP server.")
        p = canonical(path)
        if not any(is_within(p, r) for r in self.export_dirs):
            raise ConfigError(f"{p} is outside the allow-listed export folders: "
                              + ", ".join(str(r) for r in self.export_dirs))
        for s in source_dirs:
            if is_within(p, s):
                raise ConfigError(f"refusing to export inside a photo source folder ({s}); originals are never "
                                  "written to")
        return p

    def public(self) -> dict:
        return {"engine_url": self.engine_url, "allowed_dirs": [str(p) for p in self.allowed_dirs],
                "export_dirs": [str(p) for p in self.export_dirs], "max_batch": self.max_batch,
                "token_source": "env" if self.token else ("file" if self.token_files else "none"),
                "config_file": str(self.config_file) if self.config_file else None, "notes": list(self.notes)}


def _check_engine_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ConfigError(f"engine URL must be http(s)://host:port, got {url!r}")
    if u.hostname not in LOOPBACK_HOSTS:
        # The token and the photos must never leave this machine.
        raise ConfigError(f"engine URL must point to this machine (127.0.0.1 / localhost / ::1), got {u.hostname!r}")
    return url.rstrip("/")


def _read_config_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a JSON object")
    return data


def load_config(*, allowed_dirs: list[str] | None = None, export_dirs: list[str] | None = None,
                engine_url: str | None = None, environ: dict[str, str] | None = None) -> Config:
    e = os.environ if environ is None else environ
    notes: list[str] = []

    cfg_path_s = e.get(env("MCP_CONFIG"))
    cfg_path = Path(cfg_path_s).expanduser() if cfg_path_s else user_config_dir() / "mcp.json"
    file_cfg = _read_config_file(cfg_path)
    used_file = cfg_path if file_cfg else None

    def pick_list(cli: list[str] | None, env_name: str, key: str) -> list[str]:
        if cli:
            return [x for x in cli if x]
        if e.get(env(env_name)):
            return _split_paths(e[env(env_name)])
        v = file_cfg.get(key) or []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ConfigError(f"'{key}' in {cfg_path} must be a list of paths")
        return v

    url = _check_engine_url(engine_url or e.get(env("ENGINE_URL")) or file_cfg.get("engine_url") or DEFAULT_ENGINE_URL)

    token_files: list[Path] = []
    if e.get(env("TOKEN_FILE")):
        token_files.append(Path(e[env("TOKEN_FILE")]).expanduser())
    if e.get(env("ENGINE_DIR")):
        token_files.append(Path(e[env("ENGINE_DIR")]).expanduser() / ".runtime_token")
    if file_cfg.get("token_file"):
        token_files.append(Path(str(file_cfg["token_file"])).expanduser())
    token_files.append(user_state_dir() / "runtime_token")

    def num(env_name: str, key: str, default: float) -> float:
        raw = e.get(env(env_name)) or file_cfg.get(key)
        if raw in (None, ""):
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{env(env_name)} / '{key}' must be a number") from None

    allowed = _roots(pick_list(allowed_dirs, "ALLOWED_DIRS", "allowed_dirs"), "allowed_dirs")
    exports = _roots(pick_list(export_dirs, "EXPORT_DIRS", "export_dirs"), "export_dirs")
    if not allowed:
        notes.append(f"no photo folders allow-listed ({env('ALLOWED_DIRS')}); open_folder will refuse")
    if not exports:
        notes.append(f"no export folders allow-listed ({env('EXPORT_DIRS')}); export will refuse")

    return Config(
        engine_url=url,
        token=e.get(env("ENGINE_TOKEN")) or None,
        token_files=tuple(token_files),
        allowed_dirs=allowed,
        export_dirs=exports,
        max_batch=max(1, int(num("MAX_BATCH", "max_batch", 500))),
        max_wait_s=max(0.0, num("MAX_WAIT_S", "max_wait_s", 600.0)),
        poll_interval_s=max(0.0, num("POLL_INTERVAL_S", "poll_interval_s", 1.0)),
        request_timeout_s=max(5.0, num("REQUEST_TIMEOUT_S", "request_timeout_s", 120.0)),
        config_file=used_file,
        notes=tuple(notes),
    )
