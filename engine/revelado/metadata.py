# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata reading with ONE exiftool process per batch and normalisation.

exiftool runs with ``-j`` (JSON) over an argument file of up to 200 paths, so 150 photos cost one or two Perl start-ups.
Maker notes are needed (Sony PictureProfile, FocusLocation), so ``-fast2`` is NOT used. Numeric tags use the ``#``
suffix (no print conversion) while PictureProfile keeps its human string (``Gamma S-Log3 (PP8 or PP9)``).
Serial numbers are hashed immediately (sha256(salt+value)[:16]); GPS and owner tags are never requested.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ReveladoError
from .log import get_logger

log = get_logger("metadata")
# Bump when normalise() changes what it extracts: catalog rows written by an older version are re-read on next open.
# 2: width/height prefer DefaultCropSize (a DNG's ExifImageWidth can describe a small preview, not the sensor)
META_VERSION = 2

TAGS = [
    "Make", "Model", "LensModel", "LensType2#", "LensID", "LensSpec", "LensMake",
    "ISO#", "FocalLength#", "FocalLengthIn35mmFormat#", "FNumber#", "ExposureTime#", "ExposureCompensation#",
    "DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal",
    "PictureProfile", "CreativeStyle", "WhiteBalance", "MeteringMode", "ExposureProgram", "Flash",
    "ExifImageWidth#", "ExifImageHeight#", "ImageWidth#", "ImageHeight#", "DefaultCropSize#", "Orientation#",
    "FocusLocation", "FocusMode", "AFAreaMode", "FocusDistance2#", "FocusFrameSize",
    "WhiteLevel", "BlackLevel", "BitsPerSample#", "Software",
    "DistortionCorrParams", "VignettingCorrParams", "ChromaticAberrationCorrParams",
    "SerialNumber", "InternalSerialNumber", "LensSerialNumber",
]
_SERIALS = ("SerialNumber", "InternalSerialNumber", "LensSerialNumber")
_MAKERS = {"SONY": "Sony", "NIKON CORPORATION": "Nikon", "NIKON": "Nikon", "CANON": "Canon", "FUJIFILM": "Fujifilm",
           "OLYMPUS IMAGING CORP.": "OM", "OM DIGITAL SOLUTIONS": "OM", "PANASONIC": "Panasonic",
           "RICOH IMAGING COMPANY, LTD.": "Pentax", "LEICA CAMERA AG": "Leica", "HASSELBLAD": "Hasselblad",
           "DJI": "DJI", "APPLE": "Apple", "GOOGLE": "Google", "SIGMA": "Sigma"}
_LOG_PATTERNS = [(r"s-?log ?3", "s-log3"), (r"s-?log ?2", "s-log2"), (r"hlg ?\d?", "hlg"), (r"c-?log ?3", "c-log3"),
                 (r"c-?log ?2", "c-log2"), (r"v-?log", "v-log"), (r"f-?log ?2", "f-log2"), (r"f-?log", "f-log"),
                 (r"n-?log", "n-log"), (r"d-?log", "d-log")]
LOG_PROFILES = {"s-log3", "s-log2", "c-log3", "c-log2", "v-log", "f-log", "f-log2", "n-log", "d-log"}


def read_batch(exiftool: str | None, paths: list[Path], salt: str, chunk: int = 200) -> dict[str, dict]:
    """Returns {str(path): normalised dict}. Missing/unreadable files map to {'error': ...}."""
    if not exiftool:
        raise ReveladoError("E_TOOL_MISSING", "exiftool no está instalado")
    out: dict[str, dict] = {}
    for i in range(0, len(paths), chunk):
        part = paths[i:i + chunk]
        with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(str(p) for p in part))
            argfile = fh.name
        try:
            cmd = [exiftool, "-j", "-q", "-q", "-charset", "filename=utf8", "-api", "LargeFileSupport=1"]
            cmd += [f"-{t}" for t in TAGS] + ["-@", argfile]
            pr = subprocess.run(cmd, capture_output=True, timeout=600)
            text = pr.stdout.decode("utf-8", "replace").strip()
            rows = json.loads(text) if text else []
        except subprocess.TimeoutExpired as e:
            raise ReveladoError("E_TOOL_MISSING", "exiftool tardó demasiado") from e
        except json.JSONDecodeError as e:
            log.error("exiftool returned invalid JSON (%s)", e)
            rows = []
        finally:
            Path(argfile).unlink(missing_ok=True)
        for r in rows:
            src = r.get("SourceFile")
            if src:
                out[str(Path(src))] = normalise(r, salt)
    for p in paths:
        out.setdefault(str(p), {"error": "no metadata"})
    return out


def _hash(salt: str, v: Any) -> str | None:
    if v in (None, ""):
        return None
    return hashlib.sha256(f"{salt}:{v}".encode()).hexdigest()[:16]


def _num(v: Any) -> float | None:
    try:
        if v in (None, "", "undef"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _crop_size(v: Any) -> tuple[float | None, float | None]:
    """DNG/ARW DefaultCropSize ("6000 4000" or [6000, 4000]): the final image size of the RAW."""
    parts = v if isinstance(v, (list, tuple)) else str(v or "").split()
    nums = [_num(x) for x in parts[:2]]
    if len(nums) == 2 and all(n and n > 0 for n in nums):
        return nums[0], nums[1]
    return None, None


def shutter_str(t: float | None) -> str | None:
    if not t:
        return None
    if t >= 1:
        return f"{t:g}s"
    return f"1/{round(1 / t)}"


def picture_profile(raw: dict) -> str | None:
    txt = " ".join(str(raw.get(k, "")) for k in ("PictureProfile", "CreativeStyle")).lower()
    for pat, name in _LOG_PATTERNS:
        if re.search(pat, txt):
            return name
    pp = str(raw.get("PictureProfile") or "").strip()
    return pp.lower() if pp and pp.lower() not in ("off", "n/a") else None


def capture_dt(raw: dict) -> tuple[str | None, float | None]:
    dto = raw.get("DateTimeOriginal")
    if not dto or not isinstance(dto, str):
        return None, None
    try:
        d = datetime.strptime(dto[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, None
    sub = raw.get("SubSecTimeOriginal")
    frac = 0.0
    if sub not in (None, ""):
        s = str(sub).strip()
        if s.isdigit():
            frac = int(s) / (10 ** len(s))
    ts = d.timestamp() + frac
    iso = d.isoformat() + (f".{str(sub).strip()}" if sub not in (None, "") else "")
    return iso, ts


def normalise(raw: dict, salt: str) -> dict:
    make_raw = str(raw.get("Make") or "").strip()
    make = _MAKERS.get(make_raw.upper(), make_raw.title() if make_raw else None)
    model = str(raw.get("Model") or "").strip() or None
    lens = raw.get("LensModel") or raw.get("LensID") or raw.get("LensSpec")
    lens = str(lens).strip() if lens not in (None, "", "Unknown", "----") else None
    w, h = _crop_size(raw.get("DefaultCropSize"))
    if not (w and h):
        w = _num(raw.get("ExifImageWidth")) or _num(raw.get("ImageWidth"))
        h = _num(raw.get("ExifImageHeight")) or _num(raw.get("ImageHeight"))
    orient = int(_num(raw.get("Orientation")) or 1)
    dt_iso, ts = capture_dt(raw)
    pp = picture_profile(raw)
    t = _num(raw.get("ExposureTime"))
    focus = None
    fl = raw.get("FocusLocation")
    if isinstance(fl, str):
        nums = [int(x) for x in re.findall(r"-?\d+", fl)]
        if len(nums) == 4 and nums[0] > 0 and nums[1] > 0:
            focus = {"frame_w": nums[0], "frame_h": nums[1], "x": nums[2], "y": nums[3]}
    ffs = raw.get("FocusFrameSize")
    if focus and isinstance(ffs, str) and "x" in ffs:
        try:
            fw, fh = (int(v) for v in ffs.lower().split("x")[:2])
            focus.update(box_w=fw, box_h=fh)
        except ValueError:
            pass
    return {
        "make": make, "model": model, "camera": " ".join(x for x in (make, model) if x) or None,
        "lens": lens, "lens_type2": raw.get("LensType2"), "lens_id": raw.get("LensID"),
        "iso": _num(raw.get("ISO")), "focal": _num(raw.get("FocalLength")),
        "focal35": _num(raw.get("FocalLengthIn35mmFormat")), "aperture": _num(raw.get("FNumber")),
        "exposure_time": t, "shutter": shutter_str(t), "exposure_comp": _num(raw.get("ExposureCompensation")),
        "datetime": dt_iso, "capture_ts": ts, "offset": raw.get("OffsetTimeOriginal"),
        "picture_profile": pp, "is_log": pp in LOG_PROFILES,
        "picture_profile_raw": raw.get("PictureProfile"),
        "white_balance": raw.get("WhiteBalance"), "metering": raw.get("MeteringMode"),
        "program": raw.get("ExposureProgram"), "flash": raw.get("Flash"),
        "width": int(w) if w else None, "height": int(h) if h else None, "orientation": orient,
        "meta_v": META_VERSION,
        "focus": focus, "focus_mode": raw.get("FocusMode"), "af_area_mode": raw.get("AFAreaMode"),
        "focus_distance_m": _num(raw.get("FocusDistance2")),
        "white_level": raw.get("WhiteLevel"), "black_level": raw.get("BlackLevel"),
        "bits": _num(raw.get("BitsPerSample")), "software": raw.get("Software"),
        "embedded_corrections": {k: bool(raw.get(k)) for k in
                                 ("DistortionCorrParams", "VignettingCorrParams", "ChromaticAberrationCorrParams")},
        "body_hash": _hash(salt, raw.get("SerialNumber") or raw.get("InternalSerialNumber")),
        "lens_hash": _hash(salt, raw.get("LensSerialNumber")),
    }


def full_exif(exiftool: str | None, path: Path) -> dict:
    """Readable dump for the EXIF panel: groups kept, GPS/owner/serial tags removed."""
    if not exiftool:
        raise ReveladoError("E_TOOL_MISSING", "exiftool no está instalado")
    pr = subprocess.run([exiftool, "-j", "-G1", "-q", "-q", "-x", "GPS:all", "-x", "OwnerName", "-x", "Artist",
                         "-x", "Copyright", "-x", "SerialNumber", "-x", "InternalSerialNumber",
                         "-x", "LensSerialNumber", "-x", "ThumbnailImage", "-x", "PreviewImage", "-x", "JpgFromRaw",
                         "-x", "Directory", "-x", "SourceFile", "-x", "FileName", "-x", "FilePermissions",
                         "-x", "FileAccessDate", "-x", "FileInodeChangeDate", str(path)],
                        capture_output=True, timeout=60)
    try:
        d = json.loads(pr.stdout.decode("utf-8", "replace"))[0]
    except (json.JSONDecodeError, IndexError) as e:
        raise ReveladoError("E_RAW_DECODE", f"exiftool no pudo leer {path.name}") from e
    drop = re.compile(r"(?i)(gps|serial|owner|binary)")
    return {k: v for k, v in d.items() if not drop.search(k) and not (isinstance(v, str) and "(Binary data" in v)}
