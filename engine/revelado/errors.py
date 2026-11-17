# SPDX-License-Identifier: GPL-3.0-or-later
"""Error taxonomy. Every error has a code, ES/EN user messages, a technical detail and a
retryable flag, and maps to one HTTP status."""
from __future__ import annotations

from typing import Any

_CATALOG: dict[str, tuple[int, str, str, bool]] = {
    # code: (http status, message_es, message_en, retryable)
    "E_BAD_INPUT": (400, "Petición no válida.", "Invalid request.", False),
    "E_NOT_FOUND": (404, "No encontrado.", "Not found.", False),
    "E_SESSION_UNKNOWN": (404, "Sesión desconocida: abre la carpeta de nuevo.", "Unknown session: open the folder again.", False),
    "E_TOOL_MISSING": (503, "Falta una herramienta externa necesaria.", "A required external tool is missing.", False),
    "E_RAW_DECODE": (422, "No se pudo leer el archivo de imagen.", "Could not decode the image file.", False),
    "E_FILM_UNTRAINED": (409, "Perfil aún no entrenado para esta película.", "Film profile not trained yet.", False),
    "E_LEGACY_MISSING": (503, "El motor heredado de comparación no está configurado (REVELADO_LEGACY_ENGINE_DIR).",
                         "The legacy compare engine is not configured (REVELADO_LEGACY_ENGINE_DIR).", False),
    "E_NEEDS_RAW": (422, "Se necesita el RAW para revelar esta foto.",
                    "The RAW file is needed to develop this photo.", False),
    "E_MEM_GUARD": (500, "El revelado superó el límite de memoria y se detuvo.", "Develop exceeded the memory cap and was stopped.", True),
    "E_MEM_LOW": (503, "No hay memoria libre suficiente para revelar ahora. Cierra aplicaciones y reintenta.",
                  "Not enough free memory to develop now. Close apps and retry.", True),
    "E_DISK_LOW": (507, "Poco espacio libre en disco.", "Low free disk space.", True),
    "E_AI_NO_KEY": (503, "Se requiere una clave de API de Anthropic (ANTHROPIC_API_KEY).", "An Anthropic API key is required (ANTHROPIC_API_KEY).", False),
    "E_AI_CONSENT": (400, "Para usar Claude debes autorizar el envío de previsualizaciones (allow_cloud).",
                     "To use Claude you must allow sending previews (allow_cloud).", False),
    "E_AI_AUTH": (401, "La clave de API fue rechazada.", "The API key was rejected.", False),
    "E_AI_RATE": (429, "Límite de uso de la API alcanzado; reintenta más tarde.", "API rate limit reached; retry later.", True),
    "E_AI_REFUSAL": (422, "El modelo rechazó la petición.", "The model refused the request.", False),
    "E_AI_ERROR": (502, "Error al llamar a la API de Claude.", "Error calling the Claude API.", True),
    "E_NOT_IMPLEMENTED": (501, "Próximamente: esta función aún no está disponible.", "Coming soon: not available yet.", False),
    "E_CANCELLED": (409, "Cancelado.", "Cancelled.", False),
    "E_DEVELOP_FAILED": (500, "Falló el revelado.", "Develop failed.", True),
    "E_EXPORT_FAILED": (500, "Falló la exportación.", "Export failed.", True),
    "E_INTERNAL": (500, "Error interno del motor.", "Internal engine error.", True),
}


class ReveladoError(Exception):
    def __init__(self, code: str, detail: str = "", message_es: str | None = None, message_en: str | None = None,
                 extra: dict[str, Any] | None = None):
        status, es, en, retry = _CATALOG.get(code, _CATALOG["E_INTERNAL"])
        self.code = code
        self.status = status
        self.message_es = message_es or es
        self.message_en = message_en or en
        self.detail = detail
        self.retryable = retry
        self.extra = extra or {}
        super().__init__(f"{code}: {self.message_en} {detail}".strip())

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message_es": self.message_es, "message_en": self.message_en,
                "detail": self.detail, "retryable": self.retryable, **self.extra}

    def body(self) -> dict[str, Any]:
        msg = self.message_es + (f" ({self.detail})" if self.detail else "")
        return {"detail": msg, "error": self.to_dict()}
