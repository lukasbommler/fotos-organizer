"""M1 acceptance: scan + decode + previews + cache (docs/05 M1)."""

from __future__ import annotations

import contextlib
import ctypes
import os
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fixtures.make_fixtures import make_jpeg, write_sidecar
from PIL import Image

from fotoduenner.config import Config
from fotoduenner.paths import long_path
from fotoduenner.pipeline.decode import decode_bytes
from fotoduenner.pipeline.runner import Progress, analyze_album
from fotoduenner.pipeline.thumbs import thumb_paths
from fotoduenner.store.db import AlbumStore

CFG = Config()


def _rows(store: AlbumStore) -> dict[str, dict]:
    with contextlib.closing(store.connect()) as conn:
        return {r["rel_path"]: dict(r) for r in conn.execute("SELECT * FROM photos")}


def _marked(width: int, height: int) -> np.ndarray:
    """Grey image with a red block in the stored top-left corner."""
    pixels = np.full((height, width, 3), 128, np.uint8)
    pixels[: height // 4, : width // 4] = (255, 0, 0)
    return pixels


def _is_red(rgb: tuple[int, ...]) -> bool:
    return rgb[0] > 200 and rgb[1] < 60 and rgb[2] < 60


def test_mixed_album_end_to_end(tmp_path: Path) -> None:
    album = tmp_path / "Südtirol 2024 – Ärger & Spaß"
    make_jpeg(album / "Tag 1" / "Bergsee.jpg", seed=1, taken="2024:07:15 10:00:00")
    make_jpeg(
        album / "Tag 1" / "Hochformat.jpg", (400, 300), pixels=_marked(400, 300), orientation=6
    )
    make_jpeg(
        album / "Tag 1" / "Hochformat8.jpg", (400, 300), pixels=_marked(400, 300), orientation=8
    )
    make_jpeg(album / "Tag 2" / "Gipfel.jpg", seed=2)
    write_sidecar(album / "Tag 2" / "Gipfel.xmp", 3)
    (album / "Tag 2" / "Kopie.jpg").write_bytes((album / "Tag 2" / "Gipfel.jpg").read_bytes())
    (album / "kaputt.jpg").write_bytes(b"\xff\xd8\xff\xe0 this is not a jpeg")
    truncated = (album / "Tag 1" / "Bergsee.jpg").read_bytes()
    (album / "abgeschnitten.jpg").write_bytes(truncated[: len(truncated) // 3])
    deep = album.joinpath(*(["Ein sehr langer Unterordnername äöü"] * 7))
    make_jpeg(long_path(deep / "tief.jpg"), seed=3)

    summary = analyze_album(album, CFG, workers=0)
    store = AlbumStore(album)
    rows = _rows(store)
    deep_rel = next(r for r in rows if r.endswith("tief.jpg"))
    assert len(str(album / deep_rel)) > 260
    assert summary.sync.total == len(rows) == 8
    assert summary.processed == 8

    ok = {k: v for k, v in rows.items() if v["status"] == "done"}
    assert set(rows) - set(ok) == {"kaputt.jpg", "abgeschnitten.jpg"}
    assert all(rows[k]["error"] for k in ("kaputt.jpg", "abgeschnitten.jpg"))

    assert rows["Tag 1/Bergsee.jpg"]["taken_at_source"] == "exif"
    assert rows["Tag 2/Gipfel.jpg"]["taken_at_source"] == "mtime"
    assert rows["Tag 2/Gipfel.jpg"]["rating"] == 3
    assert rows["Tag 2/Gipfel.jpg"]["companions"] == '["Tag 2/Gipfel.xmp"]'
    assert rows["Tag 2/Kopie.jpg"]["file_hash"] == rows["Tag 2/Gipfel.jpg"]["file_hash"]
    assert rows["Tag 1/Bergsee.jpg"]["file_hash"] != rows["Tag 2/Gipfel.jpg"]["file_hash"]

    for rel, row in ok.items():
        small, large = thumb_paths(store.thumbs_dir, row["id"])
        with Image.open(small) as s, Image.open(large) as lg:
            assert max(s.size) <= CFG.thumb_small, rel
            assert (lg.width > lg.height) == (row["width"] > row["height"]), rel

    # Orientation 6 = rotate 90° clockwise: stored top-left ends up top-right; 8: bottom-left.
    for rel, orientation, corner in (
        ("Tag 1/Hochformat.jpg", 6, "tr"),
        ("Tag 1/Hochformat8.jpg", 8, "bl"),
    ):
        row = rows[rel]
        assert (row["width"], row["height"], row["orientation"]) == (300, 400, orientation)
        with Image.open(thumb_paths(store.thumbs_dir, row["id"])[1]) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            spots = {"tl": (5, 5), "tr": (w - 6, 5), "bl": (5, h - 6), "br": (w - 6, h - 6)}
            assert _is_red(rgb.getpixel(spots[corner])), rel
            assert not _is_red(rgb.getpixel(spots["tl"])), rel


def test_draft_decoding_keeps_enough_pixels(tmp_path: Path) -> None:
    path = make_jpeg(tmp_path / "gross.jpg", (4000, 3000))
    decoded = decode_bytes(path.read_bytes(), CFG)
    assert decoded.draft_scale == 2
    assert decoded.image.size == (2000, 1500)
    assert decoded.meta.width == 4000  # metadata keeps the original size


def _album(root: Path, count: int) -> Path:
    for i in range(count):
        make_jpeg(
            root / f"IMG_{i:04d}.jpg",
            (320, 240),
            seed=i,
            taken=f"2024:07:15 10:{i // 60:02d}:{i % 60:02d}",
        )
    return root


def test_cancel_at_half_and_resume(tmp_path: Path) -> None:
    album = _album(tmp_path / "Album", 16)
    cancel = threading.Event()

    def stop_at_half(p: Progress) -> None:
        if p.stage == "analyze" and p.done >= p.total // 2:
            cancel.set()

    first = analyze_album(album, CFG, cancel=cancel, on_progress=stop_at_half, workers=2)
    assert first.cancelled
    assert 8 <= first.processed < 16
    store = AlbumStore(album)
    assert store.status_counts() == {"done": first.processed, "pending": 16 - first.processed}

    second = analyze_album(album, CFG, workers=2)
    assert not second.cancelled
    assert second.sync.unchanged == first.processed
    assert second.processed == 16 - first.processed
    assert store.status_counts() == {"done": 16}

    third = analyze_album(album, CFG, workers=0)
    assert third.processed == 0
    assert third.sync.unchanged == 16


def test_renamed_album_reuses_features(tmp_path: Path) -> None:
    album = _album(tmp_path / "Urlaub", 4)
    write_sidecar(album / "IMG_0001.xmp", 2)
    analyze_album(album, CFG, workers=0)
    renamed = album.rename(tmp_path / "Urlaub Kroatien")

    summary = analyze_album(renamed, CFG, workers=0)
    assert summary.sync.reused == 4
    assert summary.processed == 0
    store = AlbumStore(renamed)
    rows = _rows(store)
    assert all(r["status"] == "done" and r["file_hash"] for r in rows.values())
    assert rows["IMG_0001.jpg"]["rating"] == 2
    for row in rows.values():
        assert all(p.is_file() for p in thumb_paths(store.thumbs_dir, row["id"]))


def test_fingerprint_requires_same_capture_time(tmp_path: Path) -> None:
    first = _album(tmp_path / "A", 1)
    analyze_album(first, CFG, workers=0)
    src = first / "IMG_0000.jpg"
    data = src.read_bytes()
    assert b"2024:07:15 10:00:00" in data
    # Same name, size and mtime, but a different capture time -> must not be reused.
    clone = tmp_path / "B" / "IMG_0000.jpg"
    clone.parent.mkdir()
    clone.write_bytes(data.replace(b"2024:07:15 10:00:00", b"2023:01:01 00:00:00"))
    stat = src.stat()
    os.utime(clone, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert analyze_album(clone.parent, CFG, workers=0).sync.reused == 0

    exact = tmp_path / "C" / "IMG_0000.jpg"
    exact.parent.mkdir()
    exact.write_bytes(data)
    os.utime(exact, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert analyze_album(exact.parent, CFG, workers=0).sync.reused == 1


def test_moved_file_keeps_its_row(tmp_path: Path) -> None:
    album = _album(tmp_path / "Album", 2)
    analyze_album(album, CFG, workers=0)
    before = _rows(AlbumStore(album))["IMG_0001.jpg"]["id"]
    (album / "Tag 2").mkdir()
    (album / "IMG_0001.jpg").rename(album / "Tag 2" / "IMG_0001.jpg")

    summary = analyze_album(album, CFG, workers=0)
    assert summary.sync.reused == 1
    assert summary.processed == 0
    rows = _rows(AlbumStore(album))
    assert rows["Tag 2/IMG_0001.jpg"]["id"] == before
    assert "IMG_0001.jpg" not in rows


def test_vanished_and_returning_file(tmp_path: Path) -> None:
    album = _album(tmp_path / "Album", 2)
    analyze_album(album, CFG, workers=0)
    (album / "_Aussortiert").mkdir()
    (album / "IMG_0000.jpg").rename(album / "_Aussortiert" / "IMG_0000.jpg")
    gone = analyze_album(album, CFG, workers=0)
    assert gone.sync.total == 1 and gone.sync.missing == 1
    (album / "_Aussortiert" / "IMG_0000.jpg").rename(album / "IMG_0000.jpg")
    back = analyze_album(album, CFG, workers=0)
    assert back.sync.unchanged == 2 and back.processed == 0


def test_changed_file_and_algo_bump_are_reanalysed(tmp_path: Path) -> None:
    album = _album(tmp_path / "Album", 3)
    analyze_album(album, CFG, workers=0)
    make_jpeg(album / "IMG_0002.jpg", (640, 480), seed=42)  # edited in another program
    summary = analyze_album(album, CFG, workers=0)
    assert summary.sync.changed == 1 and summary.processed == 1

    bumped = replace(CFG, algo_version=CFG.algo_version + 1)
    assert analyze_album(album, bumped, workers=0).processed == 3


def test_sidecar_rating_change_is_picked_up_without_reanalysis(tmp_path: Path) -> None:
    album = _album(tmp_path / "Album", 1)
    analyze_album(album, CFG, workers=0)
    write_sidecar(album / "IMG_0000.xmp", 5)
    summary = analyze_album(album, CFG, workers=0)
    assert summary.processed == 0
    assert _rows(AlbumStore(album))["IMG_0000.jpg"]["rating"] == 5


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes")
def test_offline_placeholder_is_flagged(tmp_path: Path) -> None:
    album = _album(tmp_path / "Album", 2)
    target = album / "IMG_0001.jpg"
    if not ctypes.windll.kernel32.SetFileAttributesW(str(target), 0x1000):  # OFFLINE
        pytest.skip("cannot set FILE_ATTRIBUTE_OFFLINE here")
    try:
        summary = analyze_album(album, CFG, workers=0)
        assert summary.scan.offline_count == 1
    finally:
        ctypes.windll.kernel32.SetFileAttributesW(str(target), 0x80)  # NORMAL


def test_worker_crash_marks_only_the_culprit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from fixtures.crashy import CRASH_NAME, crash_on_marker

    from fotoduenner.pipeline.analyze import Job, Result
    from fotoduenner.pipeline.runner import CRASH_MESSAGE, _run_pool

    album = _album(tmp_path / "Album", 10)
    make_jpeg(album / CRASH_NAME)
    jobs = [
        Job(i, str(p), p.name, str(tmp_path / "thumbs"))
        for i, p in enumerate(sorted(album.iterdir()))
    ]
    (tmp_path / "thumbs").mkdir()
    results: list[Result] = []
    _run_pool(jobs, CFG, 2, threading.Event(), results.append, task=crash_on_marker)

    by_name = {r.rel_path: r for r in results}
    assert len(results) == len(jobs)
    assert by_name[CRASH_NAME].error == CRASH_MESSAGE
    assert all(r.ok for name, r in by_name.items() if name != CRASH_NAME)
    # Usually other photos are in flight ("isolating"); if they finished first, the culprit
    # is identified directly. Both paths must give the same results.
    assert "isolating" in caplog.text or "worker crashed on" in caplog.text
