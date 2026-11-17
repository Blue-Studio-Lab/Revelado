# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration: package defaults < engine/.env < environment variables (REVELADO_*).

No absolute user path is hard-coded here. Everything machine-specific comes from the environment or from
``engine/.env`` (gitignored; see ``engine/.env.example``).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent          # <repo>/engine
REPO_DIR = ENGINE_DIR.parent                                 # <repo>


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE, '#' comments, optional quotes). Values never override real env vars."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def _lookup(env: dict[str, str], key: str) -> str:
    v = os.environ.get(key)
    if v is None or v == "":
        v = env.get(key, "")
    return v or ""


def _get(env: dict[str, str], key: str, default: str = "") -> str:
    """REVELADO_X from the environment, then engine/.env."""
    v = _lookup(env, key)
    return v if v != "" else default


def env_value(key: str, default: str = "") -> str:
    """Public accessor (other modules): REVELADO_* from the environment, engine/.env included."""
    return _get(_load_dotenv(ENGINE_DIR / ".env"), key, default)


def _which(name: str) -> str | None:
    from .tools import find_tool
    return find_tool(name)


def user_state_dir() -> Path:
    """Per-user state folder: macOS ~/Library/Application Support/Revelado, Windows %LOCALAPPDATA%\\Revelado,
    Linux $XDG_STATE_HOME/revelado (default ~/.local/state/revelado). Override: REVELADO_STATE_DIR."""
    o = os.environ.get("REVELADO_STATE_DIR")
    if o:
        return Path(o).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Revelado"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Revelado"
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "revelado"


@dataclass
class Settings:
    cache_dir: Path
    films_dir: Path | None
    legacy_dir: Path | None
    legacy_python: str
    host: str
    port: int
    dev: bool
    heavy_rss_cap_gb: float
    heavy_min_available_gb: float
    min_free_disk_gb: float
    exiftool: str | None
    rawtherapee: str | None
    develop_engine: str = "native"            # "native" (bundled pipeline) | "legacy" (private compare path)
    token_file: Path | None = None            # default engine/.runtime_token
    _dotenv: dict[str, str] = field(default_factory=dict, repr=False)

    # The API key is never stored on the settings object (so it can't be dumped by accident).
    def anthropic_key(self) -> str | None:
        v = _get(self._dotenv, "ANTHROPIC_API_KEY")
        return v or None

    @property
    def catalog_path(self) -> Path:
        return self.cache_dir / "catalog.sqlite"

    @property
    def models_dir(self) -> Path:
        return self.cache_dir / "models"

    @property
    def log_dir(self) -> Path:
        return self.cache_dir / "logs"

    @property
    def runtime_token_path(self) -> Path:
        return self.token_file or ENGINE_DIR / ".runtime_token"

    @property
    def user_token_path(self) -> Path:
        """Per-user copy of the runtime token (read by local clients such as the MCP server)."""
        return user_state_dir() / "runtime_token"

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.log_dir, self.cache_dir / "thumbs", self.cache_dir / "previews",
                  self.cache_dir / "masks", self.cache_dir / "swatches", self.cache_dir / "develop",
                  self.cache_dir / "tmp", self.cache_dir / "models"):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = _load_dotenv(ENGINE_DIR / ".env")
    cache = Path(_get(env, "REVELADO_CACHE_DIR", str(REPO_DIR / "cache"))).expanduser().resolve()
    films = _get(env, "REVELADO_FILMS_DIR")
    legacy = _get(env, "REVELADO_LEGACY_ENGINE_DIR")
    s = Settings(
        cache_dir=cache,
        films_dir=Path(films).expanduser().resolve() if films else None,
        legacy_dir=Path(legacy).expanduser().resolve() if legacy else None,
        legacy_python=_get(env, "REVELADO_LEGACY_PYTHON", sys.executable),
        host=_get(env, "REVELADO_HOST", "127.0.0.1"),
        port=int(_get(env, "REVELADO_PORT", "8790")),
        dev=_get(env, "REVELADO_DEV", "0").lower() in ("1", "true", "yes"),
        heavy_rss_cap_gb=float(_get(env, "REVELADO_HEAVY_RSS_CAP_GB", "9")),
        heavy_min_available_gb=float(_get(env, "REVELADO_HEAVY_MIN_AVAILABLE_GB", "3.5")),
        min_free_disk_gb=float(_get(env, "REVELADO_MIN_FREE_DISK_GB", "6")),
        exiftool=_get(env, "REVELADO_EXIFTOOL") or _which("exiftool"),
        rawtherapee=_get(env, "REVELADO_RAWTHERAPEE_CLI") or _which("rawtherapee-cli"),
        develop_engine=_get(env, "REVELADO_DEVELOP_ENGINE", "native").lower(),
        _dotenv=env,
    )
    s.ensure_dirs()
    return s
