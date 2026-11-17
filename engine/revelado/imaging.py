# SPDX-License-Identifier: GPL-3.0-or-later
"""Image access for the viewer: thumbnails, previews, 1:1 crops, with a disk cache and bounded memory.

Sources: the camera JPG when the photo is a RAW+JPG pair, otherwise the RAW's largest embedded
JPEG (extracted once with rawpy and cached), otherwise the JPG alone. The 1:1 crop needs full resolution: when that
source is smaller than the photo (a RAW-only photo usually embeds a ~1.6k preview), the RAW is rendered once at full
resolution with LibRaw (neutral, camera WB) and cached on disk; every later crop of that photo reuses it.

Display transform: when the picture profile is a log curve (S-Log3), the camera JPEG is flat. ``display=auto`` applies
a TECHNICAL transform (S-Log3 / S-Gamut3.Cine -> Rec.709 linear -> neutral highlight roll-off -> sRGB). It is not a
creative look; it only makes a log frame judgeable. ``display=camera`` returns untouched bytes.

Memory: thumbnails and previews decode with JPEG DCT scaling (``Image.draft``) so a 33 MP frame never decodes at full
size. Only the 1:1 crop decodes full resolution, through a 2-entry LRU (~100 MB each) guarded by a lock.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image

from .errors import ReveladoError
from .log import get_logger

log = get_logger("imaging")
MIN_EMBEDDED_PX = 1600          # smaller embedded previews are replaced by a quick neutral render
FULL_RES_TOL = 0.03             # a source within 3 % of the EXIF size counts as full resolution (LibRaw vs EXIF crop)
Image.MAX_IMAGE_PIXELS = 400_000_000

_TRANSPOSE = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
              5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
              8: Image.Transpose.ROTATE_90}

# ------------------------------------------------------------------------------------------------ display transform
# Sony S-Gamut3.Cine -> Rec.709 (linear), Sony technical summary for S-Gamut3.Cine/S-Log3.
SGAMUT3CINE_TO_709 = np.array([[1.6269474, -0.5401385, -0.0868089],
                               [-0.1785155, 1.4179409, -0.2394254],
                               [-0.0444361, -0.1959199, 1.2403560]], np.float32)


def _slog3_decode_lut() -> np.ndarray:
    v = np.arange(256, dtype=np.float64) / 255.0 * 1023.0
    lin = np.where(v >= 171.2102946929,
                   (10.0 ** ((v - 420.0) / 261.5)) * (0.18 + 0.01) - 0.01,
                   (v - 95.0) * 0.01125000 / (171.2102946929 - 95.0))
    return lin.astype(np.float32)


_SLOG3_LUT = _slog3_decode_lut()


def srgb_encode(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def slog3_to_display(rgb8: np.ndarray) -> np.ndarray:
    """uint8 HxWx3 S-Log3/S-Gamut3.Cine -> uint8 sRGB. Mid-grey 0.18 -> ~sRGB 118; highlights roll off (white 8.0)."""
    lin = _SLOG3_LUT[rgb8]                                    # HxWx3 float32
    lin = lin @ SGAMUT3CINE_TO_709.T
    np.maximum(lin, 0.0, out=lin)
    lin *= np.float32(1.18)                                   # compensate the roll-off at mid-grey
    w2 = np.float32(64.0)
    lin = lin * (1.0 + lin / w2) / (1.0 + lin)
    return (srgb_encode(lin) * 255.0 + 0.5).astype(np.uint8)


def needs_display_transform(meta: dict) -> str:
    return "slog3_to_rec709" if meta.get("picture_profile") in ("s-log3", "s-log2") else "none"


def apply_display(im: Image.Image, mode: str) -> Image.Image:
    if mode == "slog3_to_rec709":
        return Image.fromarray(slog3_to_display(np.asarray(im.convert("RGB"))))
    return im


# ------------------------------------------------------------------------------------------------ sources
class ImageStore:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.thumbs = cache_dir / "thumbs"
        self.previews = cache_dir / "previews"
        self.embedded = cache_dir / "embedded"
        for d in (self.thumbs, self.previews, self.embedded):
            d.mkdir(parents=True, exist_ok=True)
        self._sem = threading.BoundedSemaphore(4)          # concurrent reduced decodes
        self._full_lock = threading.Lock()                  # one full-resolution decode at a time
        self._full_lru: OrderedDict[str, Image.Image] = OrderedDict()
        self._embed_lock = threading.Lock()
        self._render_lock = threading.Lock()                # one full-resolution RAW render (100 % view) at a time

    # source JPEG path for a photo row
    def source_jpeg(self, photo: dict) -> Path:
        if photo.get("jpg_path") and Path(photo["jpg_path"]).exists():
            return Path(photo["jpg_path"])
        raw = photo.get("raw_path")
        if not raw or not Path(raw).exists():
            raise ReveladoError("E_NOT_FOUND", "el archivo original ya no está en la carpeta")
        out = self.embedded / f"{photo['id']}.jpg"
        rendered = self.embedded / f"{photo['id']}.render.jpg"     # quick neutral render (already oriented)
        for c in (out, rendered):
            if c.exists() and c.stat().st_mtime >= Path(raw).stat().st_mtime:
                return c
        with self._embed_lock:
            for c in (out, rendered):
                if c.exists() and c.stat().st_mtime >= Path(raw).stat().st_mtime:
                    return c
            try:
                import rawpy
                with rawpy.imread(raw) as r:
                    try:
                        th = r.extract_thumb()
                    except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                        th = None
                data = None
                if th is not None and th.format == rawpy.ThumbFormat.JPEG:
                    data = th.data
                    with Image.open(io.BytesIO(data)) as im:
                        small = max(im.size) < MIN_EMBEDDED_PX
                elif th is not None:
                    small = max(th.data.shape[:2]) < MIN_EMBEDDED_PX
                else:
                    small = True
                if small:
                    return self._quick_render(raw, rendered)
                tmp = out.with_suffix(".part")
                if data is not None:
                    tmp.write_bytes(data)
                else:
                    Image.fromarray(th.data).save(tmp, "JPEG", quality=95)
                os.replace(tmp, out)
            except ReveladoError:
                raise
            except Exception as e:  # noqa: BLE001 - any LibRaw failure is a decode error for the user
                raise ReveladoError("E_RAW_DECODE", f"sin vista previa embebida en el RAW ({type(e).__name__})") from e
        return out

    @staticmethod
    def _quick_render(raw: str, out: Path) -> Path:
        """Neutral half-size LibRaw render (camera WB, sRGB) when the RAW has no usable embedded preview."""
        import rawpy
        with rawpy.imread(raw) as r:
            rgb = r.postprocess(half_size=True, use_camera_wb=True, output_bps=8, no_auto_bright=False,
                                bright=1.0, user_flip=None)
        im = Image.fromarray(rgb)
        if max(im.size) > 2560:
            im.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
        tmp = out.with_suffix(".part")
        im.save(tmp, "JPEG", quality=92)
        os.replace(tmp, out)
        log.info("quick render preview for %s", Path(raw).name)
        return out

    def _orientation(self, photo: dict, src: Path) -> int:
        # the embedded JPEG extracted with rawpy carries no EXIF: use the RAW's orientation from the catalog;
        # the quick render and the full-resolution render are already upright
        if src.name.endswith((".render.jpg", ".full.jpg")):
            return 1
        return int((photo.get("meta") or {}).get("orientation") or 1)

    def preview_source(self, photo: dict) -> str:
        """'camera_jpg' | 'embedded' | 'quick_render' (for the X-Preview-Source header)."""
        src = self.source_jpeg(photo)
        if photo.get("jpg_path") and Path(photo["jpg_path"]) == src:
            return "camera_jpg"
        return "quick_render" if src.name.endswith(".render.jpg") else "embedded"

    def _key(self, photo: dict, src: Path, *parts) -> str:
        st = src.stat()
        h = hashlib.sha1(f"{photo['id']}:{st.st_mtime_ns}:{st.st_size}:{':'.join(map(str, parts))}".encode())
        return h.hexdigest()[:20]

    def reduced(self, photo: dict, long_side: int, display: str = "auto") -> tuple[Image.Image, str]:
        """Oriented RGB image with long side <= long_side (never upscaled). Returns (image, transform_name)."""
        src = self.source_jpeg(photo)
        mode = needs_display_transform(photo.get("meta") or {}) if display == "auto" else "none"
        with self._sem:
            try:
                with Image.open(src) as im:
                    im.draft("RGB", (long_side, long_side))
                    im = im.convert("RGB")
            except (OSError, SyntaxError) as e:
                raise ReveladoError("E_RAW_DECODE", f"no se pudo decodificar {src.name}") from e
            tr = _TRANSPOSE.get(self._orientation(photo, src))
            if tr is not None:
                im = im.transpose(tr)
            if max(im.size) > long_side:
                im.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
            im = apply_display(im, mode)
        return im, mode

    def cached_jpeg(self, photo: dict, kind: str, size: int, display: str) -> tuple[Path, str]:
        src = self.source_jpeg(photo)
        mode = needs_display_transform(photo.get("meta") or {}) if display == "auto" else "none"
        d = self.thumbs if kind == "thumb" else self.previews
        out = d / f"{photo['id']}_{size}_{mode}_{self._key(photo, src, kind, size, mode)}.jpg"
        if out.exists():
            return out, mode
        im, mode = self.reduced(photo, size, display)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85 if kind == "thumb" else 90, optimize=kind == "thumb")
        tmp = out.with_suffix(f".{threading.get_ident()}.part")
        tmp.write_bytes(buf.getvalue())
        os.replace(tmp, out)
        return out, mode

    # -------------------------------------------------------------------------------------------- 1:1 crop
    def _full(self, src: Path) -> Image.Image:
        k = f"{src}:{src.stat().st_mtime_ns}"
        im = self._full_lru.get(k)
        if im is not None:
            self._full_lru.move_to_end(k)
            return im
        try:
            with Image.open(src) as f:
                im = f.convert("RGB")
        except (OSError, SyntaxError) as e:
            raise ReveladoError("E_RAW_DECODE", f"no se pudo decodificar {src.name}") from e
        self._full_lru[k] = im
        while len(self._full_lru) > 2:
            self._full_lru.popitem(last=False)
        return im

    @staticmethod
    def _to_raw_rect(o: int, x: int, y: int, w: int, h: int, W: int, H: int) -> tuple[int, int, int, int]:
        """Map a rect in ORIENTED coordinates to the stored (unrotated) image of size W x H."""
        def inv(dx: float, dy: float) -> tuple[float, float]:
            if o == 2:
                return W - dx, dy
            if o == 3:
                return W - dx, H - dy
            if o == 4:
                return dx, H - dy
            if o == 5:
                return dy, dx
            if o == 6:
                return dy, H - dx
            if o == 7:
                return W - dy, H - dx
            if o == 8:
                return W - dy, dx
            return dx, dy
        xs, ys = zip(inv(x, y), inv(x + w, y + h))
        return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))

    @staticmethod
    def display_size(photo: dict) -> tuple[int, int] | None:
        """Full-resolution size as DISPLAYED (orientation applied): the coordinate space of ``crop``."""
        m = photo.get("meta") or {}
        try:
            w, h = int(m.get("width") or 0), int(m.get("height") or 0)
            o = int(m.get("orientation") or 1)
        except (TypeError, ValueError):
            return None
        if w <= 0 or h <= 0:
            return None
        return (h, w) if o in (5, 6, 7, 8) else (w, h)

    def _oriented_size(self, photo: dict, src: Path) -> tuple[int, int]:
        try:
            with Image.open(src) as im:           # header only, no decode
                W, H = im.size
        except (OSError, SyntaxError) as e:
            raise ReveladoError("E_RAW_DECODE", f"no se pudo decodificar {src.name}") from e
        return (H, W) if self._orientation(photo, src) in (5, 6, 7, 8) else (W, H)

    @staticmethod
    def _render_full_rgb(raw: str) -> np.ndarray:
        """Full-resolution neutral LibRaw render (AHD demosaic, camera WB, sRGB, upright as shot)."""
        import rawpy
        with rawpy.imread(raw) as r:
            return r.postprocess(half_size=False, use_camera_wb=True, output_bps=8, no_auto_bright=False,
                                 bright=1.0, user_flip=None)

    def full_render(self, photo: dict) -> Path:
        """Full-resolution neutral render of the RAW for the 100 % view, cached on disk (JPEG q95 4:4:4, already
        upright) and reused by every crop of that photo. One render at a time."""
        raw = photo.get("raw_path")
        if not raw or not Path(raw).exists():
            raise ReveladoError("E_NOT_FOUND", "el archivo original ya no está en la carpeta")
        out = self.embedded / f"{photo['id']}.full.jpg"

        def fresh() -> bool:
            return out.exists() and out.stat().st_mtime >= Path(raw).stat().st_mtime

        if fresh():
            return out
        with self._render_lock:
            if fresh():
                return out
            t0 = time.monotonic()
            try:
                rgb = self._render_full_rgb(str(raw))
            except Exception as e:  # noqa: BLE001 - any LibRaw failure is a decode error for the user
                raise ReveladoError("E_RAW_DECODE", f"no se pudo revelar el RAW al 100 % ({type(e).__name__})") from e
            tmp = out.with_suffix(f".{threading.get_ident()}.part")
            Image.fromarray(rgb).save(tmp, "JPEG", quality=95, subsampling=0)
            os.replace(tmp, out)
            log.info("100%% view: full-res render of %s (%dx%d) in %.1f s", Path(raw).name, rgb.shape[1], rgb.shape[0],
                     time.monotonic() - t0)
        return out

    def crop(self, photo: dict, x: int, y: int, w: int, h: int, display: str = "auto") -> tuple[bytes, str, dict]:
        """1:1 crop. (x, y, w, h) are in DISPLAYED full-resolution coordinates (``display_size``: EXIF size with the
        orientation applied; the source's own size when EXIF has none).

        Source: the camera JPG, or the RAW's embedded JPEG, when it is full resolution. When it is smaller (most
        RAW-only photos embed a ~1.6k preview) the RAW is rendered once at full resolution and cached on disk
        (``raw_full``). Only a JPG-only photo smaller than its EXIF size is upscaled (``upscaled`` says so). The
        image returned is always exactly the clamped w x h. Returns (jpeg, display_transform, box).
        """
        if w <= 0 or h <= 0:
            raise ReveladoError("E_BAD_INPUT", "w y h deben ser > 0")
        if w * h > 2048 * 2048:
            raise ReveladoError("E_BAD_INPUT", "recorte demasiado grande (máx 2048×2048)")
        src = self.source_jpeg(photo)
        source = self.preview_source(photo)
        mode = needs_display_transform(photo.get("meta") or {}) if display == "auto" else "none"
        SW, SH = self._oriented_size(photo, src)
        DW, DH = self.display_size(photo) or (SW, SH)
        if photo.get("raw_path") and (SW < DW * (1 - FULL_RES_TOL) or SH < DH * (1 - FULL_RES_TOL)):
            src = self.full_render(photo)
            source, mode = "raw_full", "none"           # a LibRaw render is display-referred (no log curve)
            SW, SH = self._oriented_size(photo, src)
        o = self._orientation(photo, src)
        x = max(0, min(int(x), DW - 1))
        y = max(0, min(int(y), DH - 1))
        w = max(1, min(int(w), DW - x))
        h = max(1, min(int(h), DH - y))
        sx, sy = SW / DW, SH / DH
        if abs(sx - 1) <= FULL_RES_TOL and abs(sy - 1) <= FULL_RES_TOL and w <= SW and h <= SH:
            # same resolution (LibRaw and EXIF sizes differ by a few px): true 1:1, centred, kept inside the source
            rx = round(min(max(0.0, x + (SW - DW) / 2), SW - w))
            ry = round(min(max(0.0, y + (SH - DH) / 2), SH - h))
            rect = (rx, ry, w, h)
        else:
            rect = (x * sx, y * sy, w * sx, h * sy)
        with self._full_lock:
            full = self._full(src)
            W, H = full.size
            x0, y0, x1, y1 = self._to_raw_rect(o, *rect, W, H)
            x0, y0 = min(max(0, x0), W - 1), min(max(0, y0), H - 1)
            c = full.crop((x0, y0, min(W, max(x1, x0 + 1)), min(H, max(y1, y0 + 1))))
        tr = _TRANSPOSE.get(o)
        if tr is not None:
            c = c.transpose(tr)
        if c.size != (w, h):
            c = c.resize((w, h), Image.Resampling.LANCZOS)
        c = apply_display(c, mode)
        buf = io.BytesIO()
        c.save(buf, "JPEG", quality=92, subsampling=0)
        scale = round(min(sx, sy), 4)
        return buf.getvalue(), mode, {"x": x, "y": y, "w": w, "h": h, "full_w": DW, "full_h": DH, "source": source,
                                      "source_w": SW, "source_h": SH, "scale": scale,
                                      "upscaled": scale < 1 - FULL_RES_TOL}
