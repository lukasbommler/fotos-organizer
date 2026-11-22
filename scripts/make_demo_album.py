"""Generate a demo holiday album with procedural landscapes (no real photos, D8).

    uv run python scripts/make_demo_album.py [TARGET] [--n 120]

Default target: %LOCALAPPDATA%\\FotoDuenner-dev\\demo\\Demo-Urlaub 2025 (outside Google Drive, A8).
Contains series of near-identical shots, portrait-orientation photos, three days, a rated photo
with .xmp sidecar, an exact copy and an unreadable file – enough to try every screen.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import piexif
from PIL import Image, ImageFilter

from fotoduenner.paths import dev_models_dir

SIZE = (1800, 1200)
PALETTES = [  # sky top, sky horizon, far mountains, near land, water
    ((40, 90, 170), (170, 205, 235), (95, 120, 150), (45, 70, 50), (40, 95, 140)),  # day
    ((35, 30, 80), (250, 150, 90), (90, 60, 90), (35, 25, 40), (120, 70, 80)),  # sunset
    ((15, 25, 60), (80, 110, 170), (45, 60, 95), (20, 25, 35), (30, 45, 80)),  # blue hour
    ((120, 160, 200), (230, 225, 210), (150, 150, 145), (110, 120, 80), (90, 140, 150)),  # haze
    ((60, 140, 210), (200, 230, 240), (110, 140, 120), (60, 110, 60), (30, 160, 170)),  # midday
    ((90, 110, 160), (255, 205, 130), (140, 110, 100), (80, 70, 50), (150, 120, 90)),  # golden
]


def ridge(rng: np.random.Generator, width: int, base: float, rough: float) -> np.ndarray:
    """1-D fractal mountain line (fraction of height per column)."""
    line = np.zeros(width)
    amp, freq = rough, 2
    while freq < width / 4:
        knots = rng.normal(0, amp, freq + 1)
        line += np.interp(np.linspace(0, freq, width), np.arange(freq + 1), knots)
        amp *= 0.55
        freq *= 2
    return base + line


def scene(seed: int, jitter: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    w, h = SIZE
    sky_top, horizon, far, near, water = (
        np.array(c, float) for c in PALETTES[seed % len(PALETTES)]
    )
    y = np.linspace(0, 1, h)[:, None, None]
    img = sky_top * (1 - y) + horizon * y
    img = np.broadcast_to(img, (h, w, 3)).copy()
    sun_x, sun_y = rng.uniform(0.2, 0.8) * w, rng.uniform(0.2, 0.45) * h
    yy, xx = np.mgrid[0:h, 0:w]
    glow = np.exp(-(((xx - sun_x) ** 2 + (yy - sun_y) ** 2) / (2 * (0.08 * w) ** 2)))[..., None]
    img += glow * np.array([255, 230, 180]) * 0.6
    sea = rng.uniform(0.62, 0.75)
    for layer, (color, base, rough) in enumerate(
        ((far, sea - 0.18, 0.06), ((far + near) / 2, sea - 0.08, 0.05), (near, sea, 0.03))
    ):
        line = ridge(np.random.default_rng(seed * 10 + layer), w, base, rough)
        line = np.roll(line, jitter * (layer + 1))
        mask = (yy / h) > line[None, :]
        img[mask] = img[mask] * 0.15 + color * 0.85
    shore = int(min(0.92, sea + 0.06) * h)
    water_rows = slice(shore, h)
    img[water_rows] = img[water_rows] * 0.35 + water * 0.65
    img += rng.normal(0, 3, img.shape)  # sensor noise
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    return out.filter(ImageFilter.GaussianBlur(0.6))


def exif(when: str, orientation: int = 1, make: str = "Fujifilm", model: str = "X-T30") -> bytes:
    return piexif.dump(
        {
            "0th": {
                piexif.ImageIFD.Make: make,
                piexif.ImageIFD.Model: model,
                piexif.ImageIFD.Orientation: orientation,
            },
            "Exif": {piexif.ExifIFD.DateTimeOriginal: when},
        }
    )


def build(target: Path, n: int) -> None:
    rng = np.random.default_rng(7)
    target.mkdir(parents=True, exist_ok=True)
    days = ["2025:08:04", "2025:08:05", "2025:08:07"]
    index, minute = 0, 0
    while index < n:
        day = days[min(len(days) - 1, index * len(days) // n)]
        series = int(rng.choice([1, 1, 1, 2, 3, 5]))
        seed = int(rng.integers(0, 10_000))
        portrait = rng.random() < 0.18
        folder = target / ("Handy" if rng.random() < 0.25 else f"Tag {days.index(day) + 1}")
        for shot in range(min(series, n - index)):
            index += 1
            minute += 1 if shot else int(rng.integers(3, 40))
            hh, mm = 8 + minute // 60 % 12, minute % 60
            when = f"{day} {hh:02d}:{mm:02d}:{(shot * 2) % 60:02d}"
            img = scene(seed, jitter=shot * 6)
            if portrait:
                img = img.crop((450, 0, 1250, 1200)).resize((800, 1200))
            name = (
                f"DSCF{1000 + index:04d}.jpg"
                if folder.name != "Handy"
                else f"IMG_2025{index:04d}.jpg"
            )
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if portrait:  # stored landscape + EXIF orientation 6, like a rotated camera
                img = img.transpose(Image.Transpose.ROTATE_90)
                img.save(path, "JPEG", quality=88, exif=exif(when, 6))
            else:
                img.save(path, "JPEG", quality=88, exif=exif(when))
    first = sorted(target.rglob("*.jpg"))[0]
    (first.parent / "Kopie von Strand.jpg").write_bytes(first.read_bytes())
    (first.with_suffix(".xmp")).write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/'
        '02/22-rdf-syntax-ns#"><rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        'xmp:Rating="4"/></rdf:RDF></x:xmpmeta>',
        encoding="utf-8",
    )
    (target / "Tag 3").mkdir(exist_ok=True)
    (target / "Tag 3" / "defekt.jpg").write_bytes(b"\xff\xd8\xff\xe0 kaputt")
    (target / "Tag 3" / "Video.mp4").write_bytes(b"\x00" * 1024)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        default=dev_models_dir().parent / "demo" / "Demo-Urlaub 2025",
    )
    parser.add_argument("--n", type=int, default=120)
    args = parser.parse_args()
    if args.target.exists() and any(args.target.iterdir()):
        print(f"{args.target} exists, not touching it", file=sys.stderr)
        return 1
    build(args.target, args.n)
    print(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
