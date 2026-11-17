# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional COMPARE path to the private reference engine (never needed by the public app).

Selected with REVELADO_DEVELOP_ENGINE=legacy plus REVELADO_LEGACY_ENGINE_DIR. Used
by the private golden test to compare the bundled pipeline with the reference outputs. The worker is
``revelado.legacy_worker``; the process handling lives in ``revelado.runner``.
"""
from __future__ import annotations

from .config import Settings
from .errors import ReveladoError
from .films import SUPPORTED_BY_ENGINE

REQUIRED = ("_snap_v0/engine_A-fisico.py", "_snap_v0/develop.py", "lote.py", "cabello.py", "encuadre.py")


def check_config(settings: Settings) -> dict:
    d = settings.legacy_dir
    if not d:
        return {"ok": False, "reason": "REVELADO_LEGACY_ENGINE_DIR no configurado"}
    missing = [f for f in REQUIRED if not (d / f).exists()]
    if missing:
        return {"ok": False, "reason": f"faltan archivos del motor: {', '.join(missing)}"}
    if not settings.rawtherapee:
        return {"ok": False, "reason": "rawtherapee-cli no encontrado"}
    return {"ok": True, "engine": "legacy", "raw": True}


def ensure_film(recipe: dict) -> None:
    film = recipe.get("film")
    if film not in SUPPORTED_BY_ENGINE:
        raise ReveladoError("E_FILM_UNTRAINED", f"'{film}': perfil aún no entrenado; disponible: kodak_ektar_100",
                            extra={"film": film, "supported": sorted(SUPPORTED_BY_ENGINE)})
