"""Synthetic test photos (docs/05 "Testdaten"). No real private photos, ever (D8).

Library use: ``make_jpeg(path, ...)``. CLI: ``uv run python tests/fixtures/make_fixtures.py``
writes a small demo album to ``tests/fixtures/generated/demo`` (git-ignored).
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import piexif
from PIL import Image

GENERATED = Path(__file__).resolve().parent / "generated"


def pattern(width: int, height: int, seed: int = 0) -> np.ndarray:
    """Smooth colour field plus fine noise: compresses like a photo, unique per seed."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (max(2, height // 64), max(2, width // 64), 3), dtype=np.uint8)
    base = np.asarray(
        Image.fromarray(coarse).resize((width, height), Image.Resampling.BICUBIC), dtype=np.int16
    )
    noise = rng.integers(-12, 13, (height, width, 3), dtype=np.int16)
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def _rational(value: float) -> tuple[int, int]:
    frac = Fraction(value).limit_denominator(1000)
    return frac.numerator, frac.denominator


def exif_bytes(
    *,
    taken: str | None = None,
    subsec: str | None = None,
    make: str | None = None,
    model: str | None = None,
    orientation: int | None = None,
    exposure_bias: float | None = None,
    rating: int | None = None,
) -> bytes:
    zeroth: dict[int, object] = {}
    exif: dict[int, object] = {}
    if make:
        zeroth[piexif.ImageIFD.Make] = make
    if model:
        zeroth[piexif.ImageIFD.Model] = model
    if orientation:
        zeroth[piexif.ImageIFD.Orientation] = orientation
    if rating is not None:
        zeroth[piexif.ImageIFD.Rating] = rating
    if taken:
        exif[piexif.ExifIFD.DateTimeOriginal] = taken
    if subsec:
        exif[piexif.ExifIFD.SubSecTimeOriginal] = subsec
    if exposure_bias is not None:
        num, den = _rational(exposure_bias)
        exif[piexif.ExifIFD.ExposureBiasValue] = (num, den)
    return piexif.dump({"0th": zeroth, "Exif": exif})


def make_jpeg(
    path: Path,
    size: tuple[int, int] = (640, 480),
    *,
    seed: int = 0,
    pixels: np.ndarray | None = None,
    quality: int = 90,
    xmp: bytes | None = None,
    **exif: object,
) -> Path:
    """Write a JPEG with optional EXIF fields (see ``exif_bytes``) and embedded XMP."""
    data = pixels if pixels is not None else pattern(size[0], size[1], seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {"quality": quality}
    if exif:
        kwargs["exif"] = exif_bytes(**exif)  # type: ignore[arg-type]
    if xmp:
        kwargs["xmp"] = xmp
    Image.fromarray(data).save(path, "JPEG", **kwargs)
    return path


def xmp_packet(rating: int) -> bytes:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        f'xmp:Rating="{rating}"/></rdf:RDF></x:xmpmeta>'
    ).encode()


def write_sidecar(path: Path, rating: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(xmp_packet(rating))
    return path


def make_demo_album(root: Path) -> Path:
    """A few photos covering the M1 cases, for manual checks."""
    make_jpeg(
        root / "Tag 1" / "IMG_0001.jpg",
        seed=1,
        taken="2024:07:15 10:00:00",
        make="Canon",
        model="Canon EOS R6",
    )
    make_jpeg(root / "Tag 1" / "IMG_0002.jpg", seed=2, taken="2024:07:15 10:00:01", orientation=6)
    make_jpeg(root / "Tag 2" / "Südtirol_Ü.jpg", seed=3, taken="2024:07:16 09:30:00")
    write_sidecar(root / "Tag 2" / "Südtirol_Ü.xmp", 3)
    (root / "Tag 2" / "kaputt.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    return root


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else GENERATED / "demo"
    print(make_demo_album(target))
