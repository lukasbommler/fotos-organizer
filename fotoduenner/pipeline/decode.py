"""Fast reduced decoding via JPEG draft mode (03 §2, T9)."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

from fotoduenner.config import Config
from fotoduenner.scanner.exif import PhotoMeta, read_meta

DRAFT_SCALES = (8, 4, 2)


@dataclass(slots=True)
class Decoded:
    image: Image.Image  # RGB, orientation applied
    meta: PhotoMeta
    draft_scale: int
    icc_profile: bytes | None


def choose_draft_scale(width: int, height: int, min_long_edge: int) -> int:
    """Largest DCT scale factor that keeps the long edge >= min_long_edge."""
    long_edge = max(width, height)
    for scale in DRAFT_SCALES:
        if long_edge // scale >= min_long_edge:
            return scale
    return 1


def decode_bytes(data: bytes, cfg: Config) -> Decoded:
    """Decode a photo from memory. Raises on unreadable or truncated files."""
    img = Image.open(io.BytesIO(data))
    meta = read_meta(img)
    scale = 1
    if img.format == "JPEG":
        width, height = img.size
        scale = choose_draft_scale(width, height, cfg.decode_min_long_edge)
        if scale > 1:
            # Pillow picks the largest scale whose result still covers the requested size.
            img.draft("RGB", (max(1, width // scale), max(1, height // scale)))
    img.load()
    icc = img.info.get("icc_profile")
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return Decoded(image=img, meta=meta, draft_scale=scale, icc_profile=icc or None)
