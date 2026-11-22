"""Faces (03 §3.5), features in the DB and the second pass (T6) on licensed test portraits."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import piexif
import pytest
from PIL import Image

from fotoduenner.config import Config
from fotoduenner.paths import models_dir
from fotoduenner.pipeline import analyze
from fotoduenner.pipeline.faces import Face, analyze_faces, face_sharpness, native_sharpness
from fotoduenner.pipeline.models import CLIP_FILE
from fotoduenner.pipeline.quality import luma
from fotoduenner.pipeline.runner import analyze_album, second_pass_jobs
from fotoduenner.store.db import AlbumStore

FACES = Path(__file__).parent / "fixtures" / "faces"
CFG = Config()

pytestmark = pytest.mark.skipif(
    not (models_dir() / CLIP_FILE).exists(),
    reason="models not fetched (scripts/fetch_models.py)",
)


@pytest.fixture(scope="module")
def models():
    analyze.init_worker(CFG, low_priority=False)
    return analyze._models


def load(name: str) -> np.ndarray:
    return np.asarray(Image.open(FACES / name).convert("RGB"))


def test_detects_one_face_per_portrait(models) -> None:
    for path in sorted(FACES.glob("closed_*.jpg")):
        faces = analyze_faces(models, np.asarray(Image.open(path).convert("RGB")), CFG)
        assert len(faces) == 1, path.name
        face = faces[0]
        assert 0.05 < face.size_rel < 0.8
        assert face.conf >= CFG.face_score_threshold
        x, y, w, h = face.box
        assert 0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0
        assert len(face.landmarks) == 5
        assert face.sharpness is not None and face.sharpness > 0


def test_eyes_open_and_closed(models) -> None:
    for name in ("closed_dreadlocks.jpg", "closed_blue_hair.jpg", "closed_arm_raised.jpg"):
        closed = analyze_faces(models, load(name), CFG)[0]
        assert closed.eyes_open is not None and closed.eyes_open < CFG.eyes_open_threshold, name
        assert closed.presence is not None and closed.presence > CFG.eyes_min_presence
    group = analyze_faces(models, load("open_group_four.jpg"), CFG)
    assert len(group) == 4
    assert all(f.eyes_open is not None and f.eyes_open >= CFG.eyes_open_threshold for f in group)


def test_small_faces_get_no_eye_verdict(models) -> None:
    """A face below EYES_MIN_SIZE_REL keeps eyes_open = None (unreliable)."""
    img = load("closed_glasses_side.jpg")
    canvas = np.full((img.shape[0] * 4, img.shape[1] * 4, 3), 90, np.uint8)
    canvas[: img.shape[0], : img.shape[1]] = img
    faces = analyze_faces(models, canvas, CFG)
    assert faces and all(f.size_rel < CFG.eyes_min_size_rel for f in faces)
    assert all(f.eyes_open is None for f in faces)


def test_face_sharpness_drops_with_blur(models) -> None:
    img = load("closed_dreadlocks.jpg")
    face = analyze_faces(models, img, CFG)[0]
    sharp = face_sharpness(luma(img), face)
    blurred = face_sharpness(luma(cv2.GaussianBlur(img, (0, 0), 3)), face)
    assert sharp is not None and blurred is not None
    assert blurred < 0.3 * sharp


def test_native_sharpness_and_json_roundtrip(models) -> None:
    img = load("closed_glasses_side.jpg")
    faces = analyze_faces(models, img, CFG)
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    native_sharpness(luma(big), faces)
    assert faces[0].sharpness_native is not None
    back = Face.from_json(json.loads(json.dumps(faces[0].to_json())))
    assert back.box == pytest.approx(faces[0].box, abs=1e-4)
    assert back.eyes_open == pytest.approx(faces[0].eyes_open, abs=1e-3)
    assert back.sharpness_native == pytest.approx(faces[0].sharpness_native, rel=1e-3)


def _copy_with_time(src: Path, dst: Path, taken: str, shift: int = 0) -> None:
    """Re-encode a portrait with a capture time; ``shift`` px makes a near-identical burst."""
    img = np.asarray(Image.open(src).convert("RGB"))
    if shift:
        img = np.roll(img, shift, axis=1)
    exif = piexif.dump({"Exif": {piexif.ExifIFD.DateTimeOriginal: taken}})
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=92, exif=exif)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(buf.getvalue())


def test_features_stored_and_second_pass_for_series_with_faces(tmp_path: Path) -> None:
    album = tmp_path / "Porträts äöü"
    src = FACES / "closed_dreadlocks.jpg"
    for i in range(3):  # a burst: same scene, seconds apart
        _copy_with_time(src, album / f"Serie_{i}.jpg", f"2024:07:15 10:00:0{i}", shift=i * 2)
    _copy_with_time(FACES / "closed_glasses_side.jpg", album / "Einzeln.jpg", "2024:07:15 12:00:00")
    shutil.copyfile(Path(__file__).parent / "fixtures" / "faces" / "LICENSES.md", album / "x.md")

    summary = analyze_album(album, CFG, workers=0)
    assert summary.errors == 0
    assert summary.faces_native == 3
    store = AlbumStore(album)
    with contextlib.closing(store.connect()) as conn:
        rows = {
            r["rel_path"]: dict(r)
            for r in conn.execute(
                "SELECT p.rel_path, f.* FROM photos p JOIN features f ON f.photo_id = p.id"
            )
        }
    assert set(rows) == {"Serie_0.jpg", "Serie_1.jpg", "Serie_2.jpg", "Einzeln.jpg"}
    for name, row in rows.items():
        assert len(row["phash"]) == 8
        assert len(row["embedding"]) == 1024
        assert row["sharpness_peak"] > 0 and 0 <= row["clip_high"] <= 1
        assert 1.0 < row["aesthetic"] < 10.0
        assert set(json.loads(row["zeroshot"])) == {
            "person_back", "screenshot", "document_sign", "blurry"
        }  # fmt: skip
        assert row["face_count"] == 1
        faces = json.loads(row["faces"])
        native = row["faces_native"]
        if name.startswith("Serie"):
            assert native == 1 and faces[0]["sharpness_native"] is not None
        else:
            assert native == 0 and faces[0]["sharpness_native"] is None
    # nothing left for a second run; a new run does not redo the second pass
    assert second_pass_jobs(store, CFG) == []
    again = analyze_album(album, CFG, workers=0)
    assert again.processed == 0 and again.faces_native == 0


def test_second_pass_can_be_switched_off(tmp_path: Path) -> None:
    album = tmp_path / "album"
    for i in range(2):
        _copy_with_time(FACES / "closed_face_up.jpg", album / f"{i}.jpg", f"2024:07:15 10:00:0{i}")
    summary = analyze_album(album, Config(face_second_pass=False), workers=0)
    assert summary.faces_native == 0
    assert len(second_pass_jobs(AlbumStore(album), CFG)) == 2


def test_clip_stage_matches_clip_in_worker(tmp_path: Path) -> None:
    """T11: the separate CLIP stage gives the same features as CLIP inside the worker."""
    from dataclasses import replace

    from fotoduenner.pipeline.embed import from_blob

    album = tmp_path / "album"
    for i, name in enumerate(("closed_dreadlocks.jpg", "open_group_four.jpg")):
        _copy_with_time(FACES / name, album / f"{i}.jpg", f"2024:07:15 1{i}:00:00")
    rows = {}
    for mode in (True, False):
        data = tmp_path / f"data_{mode}"
        analyze_album(album, replace(CFG, clip_in_worker=mode), workers=0, data_root=data)
        store = AlbumStore(album, data)
        with contextlib.closing(store.connect()) as conn:
            rows[mode] = {
                r["rel_path"]: (
                    from_blob(r["embedding"]),
                    r["aesthetic"],
                    json.loads(r["zeroshot"]),
                )
                for r in conn.execute(
                    "SELECT p.rel_path, f.embedding, f.aesthetic, f.zeroshot FROM photos p"
                    " JOIN features f ON f.photo_id = p.id"
                )
            }
    assert set(rows[True]) == set(rows[False]) == {"0.jpg", "1.jpg"}
    for name, (emb, aes, zs) in rows[True].items():
        emb2, aes2, zs2 = rows[False][name]
        assert float(emb @ emb2) > 0.999
        assert aes == pytest.approx(aes2, abs=0.01)
        assert zs.keys() == zs2.keys()
