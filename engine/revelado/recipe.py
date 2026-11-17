# SPDX-License-Identifier: GPL-3.0-or-later
"""v0.1 flat recipe (contract) and its mapping to the develop engine (bundled pipeline; legacy compare path).

The DEFAULT recipe is NEUTRAL (film "none": no film look, no grain, no retouch, no reframing). Named presets live in
``PRESETS``; "editorial_ektar" is the former default (Kodak Ektar 100 at full strength + editorial retouch). Engine
parameters are multipliers where 1.0 = the editorial preset: skin = skin_retouch/60, blem = blemishes/50,
subject = accent/40.
Unsupported values are accepted but reported as ignored, never silently pretended.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import ReveladoError

SCHEMA: dict[str, dict[str, Any]] = {
    "film": {"type": "string", "default": "none", "label_es": "Película", "label_en": "Film stock",
             "supported": True, "note": "'none' = neutro (sin película); kodak_ektar_100 es la única película entrenada"},
    "film_strength": {"type": "number", "min": 0.0, "max": 1.0, "step": 0.05, "default": 1.0,
                      "label_es": "Intensidad", "label_en": "Strength", "supported": True},
    "exposure_ev": {"type": "number", "min": -2.0, "max": 2.0, "step": 0.1, "default": 0.0,
                    "label_es": "Exposición", "label_en": "Exposure", "supported": True,
                    "note": "sobre la exposición automática por foto"},
    "grain": {"type": "number", "min": 0.0, "max": 1.5, "step": 0.05, "default": 0.0, "label_es": "Grano",
              "label_en": "Grain", "supported": True},
    "skin_retouch": {"type": "number", "min": 0, "max": 100, "step": 1, "default": 0, "label_es": "Retoque de piel",
                     "label_en": "Skin retouch", "supported": True},
    "blemishes": {"type": "number", "min": 0, "max": 100, "step": 1, "default": 0, "label_es": "Granitos",
                  "label_en": "Blemishes", "supported": True},
    "hair": {"type": "enum", "options": ["off", "suave", "medio", "fuerte"], "default": "off",
             "label_es": "Cabello", "label_en": "Hair clean-up", "supported": True,
             "note": "con puerta de seguridad: si el fondo tiene textura no se aplica y queda 'revisar a mano'"},
    "accent": {"type": "number", "min": 0, "max": 100, "step": 1, "default": 0, "label_es": "Acento sujeto",
               "label_en": "Subject accent", "supported": True},
    "reframe": {"type": "enum", "options": ["off", "suggest", "apply"], "default": "off",
                "label_es": "Encuadre", "label_en": "Reframing", "supported": True,
                "note": "'suggest' calcula el recorte y lo guarda sin aplicarlo"},
    "sharpen": {"type": "number", "min": 0.0, "max": 2.0, "step": 0.05, "default": 1.0, "label_es": "Nitidez",
                "label_en": "Sharpening", "supported": True},
    "nr": {"type": "enum", "options": ["auto", "soft", "strong", "off"], "default": "auto",
           "label_es": "Ruido", "label_en": "Noise reduction", "supported": True,
           "note": "auto = dosis según ISO y empuje; soft/strong la escalan; off desactiva la limpieza Python"},
    "ca": {"type": "enum", "options": ["auto", "off"], "default": "auto", "label_es": "Aberración",
           "label_en": "Chromatic aberration", "supported": True,
           "note": "auto = CA lateral en RAW + defringe axial; off desactiva ambos"},
    "develop_source": {"type": "enum", "options": ["raw", "jpg"], "default": "raw", "label_es": "Fuente del revelado",
                       "label_en": "Develop source", "supported": True,
                       "note": "'jpg' revela desde el JPG de cámara (sin RAW: sin NR ni CA); solo si se elige"},
}

DEFAULT = {k: v["default"] for k, v in SCHEMA.items()}
PRESETS: dict[str, dict] = {
    "neutral": dict(DEFAULT),
    "editorial_ektar": dict(DEFAULT, film="kodak_ektar_100", film_strength=1.0, grain=1.0, skin_retouch=60,
                            blemishes=50, accent=40, reframe="suggest", sharpen=1.0),
}
PRESET_LABELS = {"neutral": {"es": "Neutro", "en": "Neutral"},
                 "editorial_ektar": {"es": "Editorial · Kodak Ektar 100", "en": "Editorial · Kodak Ektar 100"}}
_HAIR_ALIASES = {True: "fuerte", False: "off", "on": "fuerte", "none": "off", "": "off", None: "off"}
_REFRAME_ALIASES = {True: "apply", False: "off", "on": "apply", None: "off"}


def normalise(recipe: dict | None) -> dict:
    """Validate + clamp a recipe (unknown keys are kept under '_extra' and reported)."""
    if recipe is None:
        recipe = {}
    if not isinstance(recipe, dict):
        raise ReveladoError("E_BAD_INPUT", "recipe debe ser un objeto")
    r = dict(DEFAULT)
    extra = {}
    for k, v in recipe.items():
        if k not in SCHEMA:
            extra[k] = v
            continue
        sc = SCHEMA[k]
        if sc["type"] == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ReveladoError("E_BAD_INPUT", f"'{k}' debe ser numérico") from None
            v = min(max(v, sc["min"]), sc["max"])
        elif sc["type"] == "enum":
            if k == "hair":
                v = _HAIR_ALIASES.get(v, v) if not isinstance(v, str) else _HAIR_ALIASES.get(v.lower(), v.lower())
            elif k == "reframe":
                v = _REFRAME_ALIASES.get(v, v) if not isinstance(v, str) else _REFRAME_ALIASES.get(v.lower(), v.lower())
            if isinstance(v, str):
                v = v.lower()
            if v not in sc["options"]:
                raise ReveladoError("E_BAD_INPUT", f"'{k}' debe ser uno de {sc['options']}")
        else:
            if not isinstance(v, str) or not v:
                raise ReveladoError("E_BAD_INPUT", f"'{k}' debe ser texto")
        r[k] = v
    if extra:
        r["_extra"] = extra
    return r


def key(recipe: dict) -> str:
    clean = {k: v for k, v in recipe.items() if not k.startswith("_")}
    return hashlib.sha1(json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def to_engine(recipe: dict) -> tuple[dict, list[str]]:
    """Recipe -> parameters of the bundled pipeline (revelado.worker). Returns (params, ignored notes)."""
    ignored = [f"'{k}' desconocido, ignorado" for k in (recipe.get("_extra") or {})]
    p = {
        "strength": float(recipe["film_strength"]),
        "ev_offset": float(recipe["exposure_ev"]),
        "grain": float(recipe["grain"]),
        "skin": float(recipe["skin_retouch"]) / 60.0,
        "blem": float(recipe["blemishes"]) / 50.0,
        "subject": float(recipe["accent"]) / 40.0,
        "sharp": float(recipe["sharpen"]),
        "hair": recipe["hair"],
        "reframe": recipe["reframe"],
        "nr": recipe.get("nr", "auto"),
        "ca": recipe.get("ca", "auto"),
        "film": recipe.get("film", "none"),
    }
    return p, ignored


def to_legacy(recipe: dict) -> tuple[dict, list[str]]:
    """Returns (legacy params, ignored notes). Only for the private compare path."""
    ignored = []
    if recipe.get("nr", "auto") != "auto":
        ignored.append(f"nr='{recipe['nr']}' ignorado: el motor actual usa NR automática por ISO y empuje")
    if recipe.get("ca", "auto") != "auto":
        ignored.append(f"ca='{recipe['ca']}' ignorado: el motor actual siempre corrige la CA")
    for k in (recipe.get("_extra") or {}):
        ignored.append(f"'{k}' desconocido, ignorado")
    p = {
        "strength": float(recipe["film_strength"]),
        "ev_offset": float(recipe["exposure_ev"]),
        "grain": float(recipe["grain"]),
        "skin": float(recipe["skin_retouch"]) / 60.0,
        "blem": float(recipe["blemishes"]) / 50.0,
        "subject": float(recipe["accent"]) / 40.0,
        "sharp": float(recipe["sharpen"]),
        "hair": recipe["hair"],
        "reframe": recipe["reframe"],
    }
    return p, ignored
