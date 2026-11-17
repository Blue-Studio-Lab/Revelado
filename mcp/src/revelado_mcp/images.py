# SPDX-License-Identifier: GPL-3.0-or-later
"""Make engine JPEGs safe and small enough to hand to a model: <= max_px on the long edge, no metadata."""
from __future__ import annotations

import io

from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image as PILImage
from PIL import ImageOps

MAX_PREVIEW_PX = 1024


def prepare_jpeg(data: bytes, max_px: int = MAX_PREVIEW_PX, quality: int = 85) -> tuple[bytes, tuple[int, int]]:
    """Downscale to ``max_px`` (never upscale) and re-encode WITHOUT EXIF/GPS/XMP/ICC metadata."""
    max_px = max(64, min(int(max_px), MAX_PREVIEW_PX))
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            im.draft("RGB", (max_px, max_px))              # fast JPEG decode at reduced scale
            im = ImageOps.exif_transpose(im)               # bake orientation in before metadata is dropped
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px), PILImage.Resampling.LANCZOS)
            clean = PILImage.new("RGB", im.size)
            clean.paste(im)
    except (OSError, ValueError, PILImage.DecompressionBombError) as e:
        raise ToolError(f"[E_IMAGE] the engine returned an image that could not be decoded: {e}") from None
    out = io.BytesIO()
    clean.save(out, format="JPEG", quality=quality, optimize=True)   # no exif=, no icc_profile=
    return out.getvalue(), clean.size
