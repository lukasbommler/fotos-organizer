"""Preview images, JPEG (T10): small for tiles, large for the detail view."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from fotoduenner.config import Config

RESAMPLE = Image.Resampling.LANCZOS
REDUCING_GAP = 2.0  # Pillow pre-reduces by an integer factor first: fast and still sharp


def thumb_paths(thumb_dir: Path, photo_id: int) -> tuple[Path, Path]:
    return thumb_dir / f"{photo_id}_s.jpg", thumb_dir / f"{photo_id}_l.jpg"


def fit(img: Image.Image, long_edge: int) -> Image.Image:
    """Downscale so the long edge is at most ``long_edge``; never upscales."""
    width, height = img.size
    if max(width, height) <= long_edge:
        return img
    factor = long_edge / max(width, height)
    size = (max(1, round(width * factor)), max(1, round(height * factor)))
    return img.resize(size, RESAMPLE, reducing_gap=REDUCING_GAP)


def save_jpeg(img: Image.Image, path: Path, quality: int, icc_profile: bytes | None) -> None:
    """Write atomically: a crash never leaves a half-written preview behind (cache file)."""
    tmp = path.with_name(path.name + ".tmp")
    kwargs: dict[str, object] = {"quality": quality}
    if icc_profile:
        kwargs["icc_profile"] = icc_profile  # keeps Adobe RGB etc. correct in WebView2
    img.save(tmp, "JPEG", **kwargs)
    tmp.replace(path)


def write_thumbs(
    img: Image.Image, thumb_dir: Path, photo_id: int, cfg: Config, icc_profile: bytes | None
) -> Image.Image:
    """Write both previews; returns the large working image for later stages."""
    small_path, large_path = thumb_paths(thumb_dir, photo_id)
    large = fit(img, cfg.thumb_large)
    save_jpeg(large, large_path, cfg.thumb_jpeg_quality, icc_profile)
    save_jpeg(fit(large, cfg.thumb_small), small_path, cfg.thumb_jpeg_quality, icc_profile)
    return large


def load_gray(path: Path, long_edge: int) -> np.ndarray | None:
    """Small greyscale version of a preview (panorama shifts, 03 §5.0); None if unreadable."""
    try:
        with Image.open(path) as img:
            gray = fit(img.convert("L"), long_edge)
            return np.asarray(gray, dtype=np.float32)
    except OSError:
        return None
