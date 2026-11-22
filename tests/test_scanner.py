from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest
from fixtures.make_fixtures import make_jpeg, write_sidecar, xmp_packet
from PIL import Image

from fotoduenner.config import Config
from fotoduenner.pipeline.decode import choose_draft_scale
from fotoduenner.scanner.discover import scan_album
from fotoduenner.scanner.exif import PhotoMeta, parse_exif_datetime, read_meta
from fotoduenner.scanner.sidecars import combine_ratings, match_sidecars, parse_xmp_rating

CFG = Config()


def _hide(path: Path) -> None:
    if os.name != "nt":
        pytest.skip("hidden attribute is Windows-specific")
    assert ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)


def test_scan_exclusions(tmp_path: Path) -> None:
    album = tmp_path / "Album"
    make_jpeg(album / "a.jpg")
    make_jpeg(album / "B.JPEG")
    make_jpeg(album / "Tag 2" / "c.jpg")
    make_jpeg(album / "_Aussortiert" / "old.jpg")
    make_jpeg(album / "Tag 2" / "_aussortiert" / "old2.jpg")
    make_jpeg(album / ".hidden_dir" / "x.jpg")
    make_jpeg(album / ".dot.jpg")
    hidden = make_jpeg(album / "versteckt.jpg")
    _hide(hidden)
    hidden_dir = album / "Versteckter Ordner"
    make_jpeg(hidden_dir / "y.jpg")
    _hide(hidden_dir)
    (album / "Thumbs.db").write_bytes(b"x")
    (album / "clip.mp4").write_bytes(b"x")
    (album / "raw.CR2").write_bytes(b"x")
    Image.new("RGB", (8, 8)).save(album / "grafik.png")

    scan = scan_album(album, CFG)
    assert [p.rel_path for p in scan.photos] == ["a.jpg", "B.JPEG", "Tag 2/c.jpg"]
    assert scan.ignored_files == 6  # dot, hidden, Thumbs.db, mp4, CR2, png
    assert scan.offline_count == 0

    flat = scan_album(album, Config(include_subdirs=False))
    assert [p.rel_path for p in flat.photos] == ["a.jpg", "B.JPEG"]


def test_sidecar_matching() -> None:
    names = [
        "IMG_1.jpg",
        "IMG_1.xmp",  # Lightroom style
        "IMG_2.jpg",
        "IMG_2.CR2",
        "IMG_2.xmp",  # belongs to the RAW
        "IMG_3.jpg",
        "IMG_3.jpg.xmp",  # darktable style
        "IMG_4.JPG",
        "img_4.XMP",  # case-insensitive
        "IMG_5.jpg",
        "IMG_5.jpeg",
        "IMG_5.xmp",  # ambiguous
        "lonely.xmp",
    ]
    m = match_sidecars(names)
    assert m["IMG_1.jpg"].companions == ["IMG_1.xmp"]
    assert m["IMG_2.jpg"].companions == []
    assert m["IMG_2.jpg"].rating_sources == ["IMG_2.xmp"]
    assert m["IMG_3.jpg"].companions == ["IMG_3.jpg.xmp"]
    assert m["IMG_4.JPG"].companions == ["img_4.XMP"]
    assert m["IMG_5.jpg"].companions == m["IMG_5.jpeg"].companions == []
    assert m["IMG_5.jpg"].rating_sources == ["IMG_5.xmp"]
    assert set(m) == {"IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "IMG_4.JPG", "IMG_5.jpg", "IMG_5.jpeg"}


def test_scan_reads_sidecar_ratings(tmp_path: Path) -> None:
    make_jpeg(tmp_path / "IMG_1.jpg")
    write_sidecar(tmp_path / "IMG_1.xmp", 4)
    make_jpeg(tmp_path / "IMG_2.jpg")
    (tmp_path / "IMG_2.NEF").write_bytes(b"raw")
    write_sidecar(tmp_path / "IMG_2.xmp", 2)
    photos = {p.rel_path: p for p in scan_album(tmp_path, CFG).photos}
    assert photos["IMG_1.jpg"].companions == ["IMG_1.xmp"]
    assert photos["IMG_1.jpg"].sidecar_rating == 4
    assert photos["IMG_2.jpg"].companions == []
    assert photos["IMG_2.jpg"].sidecar_rating == 2


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (xmp_packet(3), 3),
        (b"<xmp:Rating>5</xmp:Rating>", 5),
        (b'xap:Rating="-1"', -1),
        (b"xmp:Rating='2.0'", 2),
        (b'xmp:Rating="9"', 5),
        (b"<rdf:Description/>", None),
    ],
)
def test_parse_xmp_rating(data: bytes, expected: int | None) -> None:
    assert parse_xmp_rating(data) == expected


def test_combine_ratings_errs_on_keeping() -> None:
    assert combine_ratings(None, None) is None
    assert combine_ratings(0, None) == 0
    assert combine_ratings(-1, 3) == 3
    assert combine_ratings(-1, 0) == -1
    assert combine_ratings(None, 2) == 2


def _read(tmp_path: Path, size: tuple[int, int] = (64, 48), **exif: object) -> PhotoMeta:
    path = make_jpeg(tmp_path / "m.jpg", size, **exif)
    with Image.open(path) as img:
        return read_meta(img)


def test_exif_fields(tmp_path: Path) -> None:
    meta = _read(
        tmp_path,
        taken="2024:07:15 14:03:22",
        subsec="25",
        make="Canon",
        model="Canon EOS R6",
        exposure_bias=-0.7,
        rating=4,
    )
    assert meta.exif_dt == "2024:07:15 14:03:22.25"
    assert meta.taken_at == pytest.approx(parse_exif_datetime("2024:07:15 14:03:22") + 0.25)
    assert meta.camera == "Canon EOS R6"
    assert meta.exposure_bias == pytest.approx(-0.7)
    assert meta.rating == 4
    assert (meta.width, meta.height, meta.orientation) == (64, 48, 1)


def test_exif_orientation_swaps_dimensions(tmp_path: Path) -> None:
    for orientation in (6, 8):
        meta = _read(tmp_path, orientation=orientation)
        assert (meta.width, meta.height, meta.orientation) == (48, 64, orientation)


def test_exif_missing_or_invalid_time(tmp_path: Path) -> None:
    assert _read(tmp_path).taken_at is None
    assert _read(tmp_path, taken="0000:00:00 00:00:00").taken_at is None
    assert _read(tmp_path, make="Apple", model="iPhone 15").camera == "Apple iPhone 15"


def test_embedded_xmp_rating(tmp_path: Path) -> None:
    assert _read(tmp_path, xmp=xmp_packet(5)).rating == 5


def test_parse_exif_datetime_variants() -> None:
    base = parse_exif_datetime("2024:07:15 14:03:22")
    assert base is not None
    assert parse_exif_datetime("2024-07-15T14:03:22") == base
    assert parse_exif_datetime("2024:07:15 14:03:22", "5") == base + 0.5
    assert parse_exif_datetime("garbage") is None
    assert parse_exif_datetime("2024:13:01 00:00:00") is None


@pytest.mark.parametrize(
    ("size", "scale"),
    [((6000, 4000), 4), ((4000, 3000), 2), ((1000, 800), 1), ((24000, 1500), 8), ((3000, 6000), 4)],
)
def test_draft_scale(size: tuple[int, int], scale: int) -> None:
    assert choose_draft_scale(*size, CFG.decode_min_long_edge) == scale
