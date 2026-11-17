# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera + lens profile resolution and develop recommendations.

Resolution chain implemented in v0.1 (higher wins, field by field; every field records its source):
  camera: curated seed DB (exact make/model or alias)  ->  generic class by sensor format (from EXIF dims / maker)
  lens:   embedded maker data present in THIS frame (Sony arrays)  ->  curated seed DB (exact name, generation
          suffixes are hard tokens, ``not`` list honoured; Sony LensType2 id)  ->  generic class by max aperture.
Lensfun, darktable noise profiles and local learning are listed as "próximamente" in the response; they are not
pretended.

``recommend()`` is a pure function (no I/O) that reproduces the legacy calibration anchor exactly:
  noise_stops = log2(ISO/100) + max(0, ev - 1.5)          (ev = expected push; S-Log prior +1.0 EV)
  nr_level    = clip((noise_stops - 3) / 5, 0, 1)
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data" / "seed.json"


@lru_cache(maxsize=1)
def seed() -> dict:
    return json.loads(_DATA.read_text(encoding="utf-8"))


def _norm_lens(s: str) -> str:
    s = s.lower().replace("f/", "f").replace("ƒ/", "f")
    s = re.sub(r"\bmark\s*ii\b", "ii", s)
    s = re.sub(r"^(sony|canon|nikon|sigma|tamron|fujifilm|fujinon)\s+", "", s)
    s = re.sub(r"(\d)\s*mm", r"\1mm", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    return _norm_lens(s).split(" ")


def resolve_camera(meta: dict) -> dict:
    make, model = (meta.get("make") or ""), (meta.get("model") or "")
    for c in seed()["cameras"]:
        names = [c["model"].lower()] + [a.lower() for a in c.get("aliases", [])]
        if make.lower() == c["maker"].lower() and model.lower() in names:
            return {**c, "source": "revelado-db", "confidence": 0.85, "matched_by": "make+model"}
    # generic class
    fmt = "full-frame"
    w = meta.get("width") or 0
    if w and w < 5000 and (meta.get("focal35") and meta.get("focal")
                           and meta["focal35"] / max(meta["focal"], 1e-6) > 1.4):
        fmt = "aps-c"
    g = seed()["generic"]["sensor_by_format"][fmt]
    key = f"{make.lower()}/{model.lower().replace(' ', '-')}" if make or model else "unknown"
    return {"key": key, "maker": make or None, "model": model or None, "sensor": {"format": fmt, "width_mm": g["width_mm"]},
            "source": "generic", "confidence": 0.3, "matched_by": "sensor-class",
            "license": None, "notes": "Cámara sin perfil curado: valores genéricos por formato de sensor."}


def resolve_lens(meta: dict) -> dict:
    name = meta.get("lens") or ""
    lt2 = meta.get("lens_type2")
    emb = meta.get("embedded_corrections") or {}
    has_embedded = any(emb.values())
    cand = None
    if name:
        nt = _norm_lens(name)
        for l in seed()["lenses"]:
            if any(_norm_lens(n) == nt for n in l["match"].get("not", [])):
                continue
            if any(_norm_lens(n) == nt for n in l["match"]["exif"]):
                cand = {**l, "source": "revelado-db", "matched_by": "exif-name", "confidence": 0.95}
                break
    if cand is None and lt2 is not None:
        for l in seed()["lenses"]:
            ids = l["match"].get("sony_lens_type2") or []
            try:
                if int(lt2) in ids:
                    cand = {**l, "source": "revelado-db", "matched_by": "sony-lens-type2", "confidence": 0.9}
                    break
            except (TypeError, ValueError):
                pass
    if cand is None:
        maxap = _max_aperture_from_name(name) or meta.get("aperture")
        loca = "low"
        for ap, cls in seed()["generic"]["loca_by_max_aperture"]:
            if maxap and maxap <= ap:
                loca = cls
                break
        cand = {"key": f"generic/{_norm_lens(name).replace(' ', '-')}" if name else "unknown",
                "model": name or None, "max_aperture": maxap, "loca": {"class": loca}, "tca": {"class": "unknown"},
                "source": "generic", "matched_by": "max-aperture-class", "confidence": 0.3,
                "notes": "Objetivo sin perfil curado (Lensfun: próximamente)."}
    cand["embedded_corrections"] = emb
    cand["geometry_source"] = "embedded" if emb.get("DistortionCorrParams") else (
        "lensfun (próximamente)" if cand["source"] == "generic" else (cand.get("geometry") or {}).get("preferred"))
    cand["has_embedded_data"] = has_embedded
    return cand


def _max_aperture_from_name(name: str) -> float | None:
    m = re.search(r"f\s*/?\s*(\d+(?:\.\d+)?)", name.lower())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def noise_stops(iso: float, ev: float) -> float:
    return math.log2(max(iso, 1) / 100.0) + max(0.0, ev - 1.5)


def recommend(cam: dict, lens: dict, meta: dict) -> dict[str, Any]:
    """Pure function. Every output: {value, range?, why, sources[]}."""
    pp = meta.get("picture_profile")
    ev_prior = seed()["generic"]["log_ev_prior"].get(pp or "", 0.0)
    iso = float(meta.get("iso") or 100)
    fmt_off = 0.0
    if cam.get("source") == "generic":
        fmt_off = seed()["generic"]["sensor_by_format"].get(cam["sensor"]["format"], {}).get("noise_iso_offset_stops", 0)
    # the legacy anchor: expected push on S-Log3 frames ~ +1.0 EV prior (measured +0.7..+1.2 on the reference set)
    expected_ev = 1.0 if pp in ("s-log3", "s-log2") else 0.5
    ns = noise_stops(iso, expected_ev) + fmt_off
    level = min(max((ns - 3.0) / 5.0, 0.0), 1.0)
    rt_luma = min(max(6.0 * (ns - 3.0), 0.0), 40.0)
    # chromatic aberration
    focal = meta.get("focal") or 0
    fr = lens.get("focal_mm") or []
    at_zoom_end = bool(fr and len(fr) == 2 and fr[0] != fr[1] and (focal <= fr[0] * 1.05 or focal >= fr[1] * 0.95))
    tca_cls = (lens.get("tca") or {}).get("class", "unknown")
    ca_iter = 3 if (tca_cls in ("medium", "high") and at_zoom_end) else 2
    # defringe (axial CA)
    loca_cls = (lens.get("loca") or {}).get("class", "medium")
    loca_k = {"low": 0.4, "medium": 0.7, "high": 1.0}.get(loca_cls, 0.7)
    maxap = lens.get("max_aperture") or meta.get("aperture") or 2.8
    ap = meta.get("aperture") or maxap
    stops_down = 2 * math.log2(max(ap, maxap) / maxap) if maxap else 0
    ap_k = 1.0 - min(max(stops_down, 0.0), 2.0) / 2.0 * 0.65
    defringe = round(loca_k * ap_k, 2)
    # capture sharpening radius
    sensor = cam.get("sensor") or {}
    px_w = (sensor.get("raw_px") or [meta.get("width") or 6000])[0]
    pitch = (sensor.get("width_mm") or 36.0) * 1000 / max(px_w, 1)
    airy = 2.44 * 0.55 * (ap or 2.8) / pitch
    radius = min(max(0.55 + 0.12 * airy + (0.1 if sensor.get("olpf") else 0.0), 0.5), 1.3)
    src_cam = f"{cam.get('source')}:{cam.get('key')}"
    src_lens = f"{lens.get('source')}:{lens.get('key')}"
    return {
        "nr_level": {"value": round(level, 3), "range": [0.0, 1.0],
                     "why": f"ISO {iso:g} + empuje esperado {expected_ev:+.1f} EV -> {ns:.1f} noise-stops"
                            + (f" (+{fmt_off:g} por formato)" if fmt_off else ""),
                     "rt_luma_nr": round(rt_luma, 1), "noise_stops": round(ns, 2), "sources": [src_cam, "legacy-anchor"]},
        "ca": {"value": {"raw_ca_auto": True, "iterations": ca_iter, "avoid_colour_shift": True}, "range": [1, 3],
               "why": f"TCA {tca_cls}" + (", extremo del zoom" if at_zoom_end else ""), "sources": [src_lens]},
        "defringe": {"value": defringe, "range": [0.0, 1.0],
                     "why": f"LoCA {loca_cls} × apertura f/{ap:g} (máx f/{maxap:g})", "sources": [src_lens]},
        "sharpen": {"value": {"deconv_radius": round(radius, 2), "auto_radius": True}, "range": [0.5, 1.3],
                    "why": f"disco de Airy {airy:.2f} px a f/{ap:g}, pitch {pitch:.1f} µm", "sources": [src_cam]},
        "exposure_hint": {"value": round(ev_prior, 2), "range": [-2.0, 2.0] if not ev_prior else [0.5, 1.7],
                          "why": ("perfil log: el RAW sale ~1 EV más oscuro; el análisis por foto decide el valor final"
                                  if ev_prior else "sin perfil log: sin empuje previo"),
                          "sources": ["picture_profile"]},
        "log_profile": {"value": pp if meta.get("is_log") else None,
                        "display_transform": "slog3_to_rec709" if pp in ("s-log3", "s-log2") else None,
                        "why": f"PictureProfile = {meta.get('picture_profile_raw')}" if pp else "sin perfil de imagen log"},
        "distortion": {"value": "embedded" if (lens.get("embedded_corrections") or {}).get("DistortionCorrParams")
                       else "off", "why": "datos de corrección del fabricante en el archivo"
                       if (lens.get("embedded_corrections") or {}).get("DistortionCorrParams") else "sin datos (Lensfun: próximamente)"},
        "vignetting": {"value": "off" if (cam.get("lens_corrections") or {}).get("raw_vignetting_precompensated") else "auto",
                       "why": "RAW ya compensado en esta cámara (medido; pendiente de verificar con flat field)"
                       if (cam.get("lens_corrections") or {}).get("raw_vignetting_precompensated") else "según perfil"},
    }


def profile_for(meta: dict) -> dict:
    cam = resolve_camera(meta)
    lens = resolve_lens(meta)
    rec = recommend(cam, lens, meta)
    pub_cam = {k: cam.get(k) for k in ("key", "maker", "model", "sensor", "levels", "lens_corrections", "quirks",
                                       "source", "confidence", "matched_by", "license", "provenance", "notes")
               if cam.get(k) is not None}
    pub_lens = {k: lens.get(k) for k in ("key", "maker", "model", "focal_mm", "max_aperture", "tca", "loca",
                                         "vignetting", "sharpness", "geometry_source", "has_embedded_data",
                                         "source", "confidence", "matched_by", "license", "provenance", "notes")
                if lens.get(k) is not None}
    return {"camera_profile": pub_cam, "lens_profile": pub_lens, "recommended": rec,
            "not_yet": ["lensfun", "darktable-noise-profiles", "local-learning", "flat-field"],
            "schema": "revelado.develop_recommendation/1"}
