# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate recipe patches against the schema the engine publishes (GET /api/recipe/schema).

The engine stays the source of truth (it normalises again); this layer gives the model a clear error before a
job is queued, instead of a silent clamp.
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_patch(patch: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ToolError("[E_BAD_INPUT] recipe patch must be an object {field: value}")
    problems: list[str] = []
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        spec = schema.get(key)
        if not isinstance(spec, dict):
            problems.append(f"unknown field '{key}' (known: {', '.join(sorted(schema))})")
            continue
        options = spec.get("options")
        if options:
            if value not in options:
                problems.append(f"'{key}' must be one of {options}, got {value!r}")
                continue
        else:
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None or hi is not None or spec.get("type") in ("number", "float", "int", "integer"):
                if not _is_number(value):
                    problems.append(f"'{key}' must be a number, got {value!r}")
                    continue
                if lo is not None and value < lo or hi is not None and value > hi:
                    problems.append(f"'{key}' must be within [{lo}, {hi}], got {value}")
                    continue
                if spec.get("type") in ("int", "integer") and float(value) != int(value):
                    problems.append(f"'{key}' must be an integer, got {value}")
                    continue
            elif spec.get("type") in ("bool", "boolean") and not isinstance(value, bool):
                problems.append(f"'{key}' must be true/false, got {value!r}")
                continue
            elif spec.get("type") in ("str", "string") and not (isinstance(value, str) or value is None):
                problems.append(f"'{key}' must be text, got {value!r}")
                continue
        clean[key] = value
    if problems:
        raise ToolError("[E_BAD_INPUT] invalid recipe patch: " + "; ".join(problems))
    return clean


def unsupported_fields(recipe: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Fields the current engine accepts but ignores (schema ``supported: false``) and that differ from default."""
    out = []
    for k, spec in schema.items():
        if isinstance(spec, dict) and spec.get("supported") is False and k in recipe and recipe[k] != spec.get("default"):
            out.append(k)
    return out
