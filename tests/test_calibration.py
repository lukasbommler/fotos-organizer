"""Calibration records (D20): stored at "Übernehmen", replayable without photos, voided by undo."""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fixtures.make_fixtures import make_jpeg

from fotoduenner.api.state import AppState
from fotoduenner.config import Config
from fotoduenner.paths import calibration_path
from fotoduenner.store import calibration


def _wait(state: AppState, album_id: str) -> None:
    session = state.session(album_id)
    deadline = time.monotonic() + 60
    while session.state == "running" or session.moving:
        assert time.monotonic() < deadline
        time.sleep(0.02)


def _open(state: AppState, album: Path) -> str:
    album_id = state.open_album(str(album), True)["album_id"]
    state.start_analysis(album_id)
    _wait(state, album_id)
    return album_id


def _apply(state: AppState, album_id: str) -> dict[str, Any]:
    preview = state.apply_preview(album_id)
    state.start_apply(album_id, preview["photo_ids"])
    _wait(state, album_id)
    return preview


@pytest.fixture
def album(tmp_path: Path) -> Path:
    root = tmp_path / "Album"
    for k in range(6):
        make_jpeg(root / f"{k}.jpg", seed=k, taken=f"2025:08:01 1{k}:00:00")
    for name in ("0", "1"):
        (root / "Kopie").mkdir(exist_ok=True)
        shutil.copyfile(root / f"{name}.jpg", root / "Kopie" / f"{name}.jpg")
    return root


def _ids(state: AppState, album_id: str) -> dict[str, int]:
    return {p["rel_path"]: p["id"] for p in state.view(album_id)["photos"]}


def test_record_holds_proposal_and_final_decision(album: Path) -> None:
    state = AppState(Config(analysis_workers=0))
    album_id = _open(state, album)
    ids = _ids(state, album_id)
    state.set_decision(album_id, [ids["Kopie/0.jpg"]], "keep")  # proposal overruled
    state.set_decision(album_id, [ids["5.jpg"]], "reject")
    preview = _apply(state, album_id)
    assert preview["recorded"] is True

    (record,) = calibration.load()
    rows = {r["rel_path"]: r for r in record.rows}
    assert len(rows) == 8 and record.settings.mode == "clean" and record.earlier_batches == 0
    copy, own = rows["Kopie/0.jpg"], rows["5.jpg"]
    assert (copy["auto_keep"], copy["final_keep"], copy["manual"]) == (0, 1, "keep")
    assert copy["auto_code"] == "duplicate" and copy["auto_ref"] == ids["0.jpg"]
    assert (own["auto_keep"], own["final_keep"], own["manual"]) == (1, 0, "reject")
    untouched = rows["Kopie/1.jpg"]
    assert (untouched["auto_keep"], untouched["final_keep"], untouched["manual"]) == (0, 0, None)
    assert {r["id"] for r in record.rows if not r["final_keep"]} == set(preview["photo_ids"])


def test_record_replays_the_proposal_without_photos_or_cache(
    album: Path, isolated_data_dir: Path
) -> None:
    state = AppState(Config(analysis_workers=0))
    album_id = _open(state, album)
    state.update_settings(album_id, {"mode": "quota", "keep_target": 0.4, "level": 3})
    _apply(state, album_id)
    shutil.rmtree(album)
    shutil.rmtree(isolated_data_dir / "albums")

    (record,) = calibration.load()
    assert record.cfg == Config(analysis_workers=0)  # tuples and dicts survive the JSON
    assert record.settings.mode == "quota" and record.settings.keep_target == 0.4
    decisions = record.engine().decide(record.settings)
    assert decisions.auto_keep.astype(int).tolist() == [r["auto_keep"] for r in record.rows]
    assert (~decisions.auto_keep).any()
    loose = record.engine(Config(motif_thresholds=(0.9,) * 5))  # another configuration
    assert len(loose.layout(0).groups) <= len(record.engine().layout(0).groups)


def test_panorama_shifts_are_part_of_the_record(tmp_path: Path, monkeypatch: Any) -> None:
    from fotoduenner.api import review

    monkeypatch.setattr(review, "phase_shift", lambda _a, _b: (0.3, 0.0, 0.9))
    root = tmp_path / "Pano"
    for k in range(4):
        make_jpeg(root / f"{k}.jpg", seed=k, taken=f"2025:08:01 10:00:0{k}")
    state = AppState(Config(analysis_workers=0))
    album_id = _open(state, root)
    ids = _ids(state, album_id)
    state.set_decision(album_id, [ids["3.jpg"]], "reject")
    engine = state.session(album_id).review._ensure()  # type: ignore[union-attr]
    expected = [(s.kind, tuple(s.members)) for s in engine.sequences]
    assert expected and expected[0][0] == "pano"
    _apply(state, album_id)
    (record,) = calibration.load()
    assert record.shifts and all(v == (0.3, 0.0, 0.9) for v in record.shifts.values())
    assert [(s.kind, tuple(s.members)) for s in record.engine().sequences] == expected


def test_undo_voids_the_record_and_applying_again_makes_a_new_one(album: Path) -> None:
    state = AppState(Config(analysis_workers=0))
    album_id = _open(state, album)
    _apply(state, album_id)
    assert len(calibration.load()) == 1
    state.start_undo(album_id, None)
    _wait(state, album_id)
    assert calibration.load() == []
    assert [r.reverted for r in calibration.load(include_reverted=True)] == [True]
    _apply(state, album_id)
    assert len(calibration.load()) == 1


def test_no_record_without_a_proposal_or_when_switched_off(album: Path) -> None:
    state = AppState(Config(analysis_workers=0))
    album_id = _open(state, album)
    _apply(state, album_id)
    # "übernommen": everything left counts as kept, so there is no proposal to compare with
    state.set_decision(album_id, [_ids(state, album_id)["5.jpg"]], "reject")
    assert _apply(state, album_id)["recorded"] is False
    assert len(calibration.load()) == 1

    off = AppState(Config(analysis_workers=0, calibration_records=False))
    album_id = off.open_album(str(album), True)["album_id"]
    off.rethin(album_id)
    off.set_decision(album_id, [_ids(off, album_id)["4.jpg"]], "reject")
    assert _apply(off, album_id)["recorded"] is False
    assert len(calibration.load()) == 1


def test_a_failing_record_never_blocks_moving(album: Path, monkeypatch: Any) -> None:
    def broken(*_args: Any, **_kw: Any) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(calibration, "save", broken)
    state = AppState(Config(analysis_workers=0))
    album_id = _open(state, album)
    _apply(state, album_id)
    job = state.session(album_id).job
    assert job is not None and job.state == "done" and job.report["photos"] == 2  # type: ignore[index]
    assert (album / "_Aussortiert" / "Kopie" / "0.jpg").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows file locking")
def test_nothing_moved_voids_the_record(album: Path) -> None:
    state = AppState(Config(analysis_workers=0))
    album_id = _open(state, album)
    with (album / "Kopie" / "0.jpg").open("rb"), (album / "Kopie" / "1.jpg").open("rb"):
        _apply(state, album_id)
    assert calibration.load() == []
    _apply(state, album_id)
    assert len(calibration.load()) == 1


def test_record_contains_no_image_data_and_stays_in_the_data_folder(
    album: Path, isolated_data_dir: Path
) -> None:
    state = AppState(Config(analysis_workers=0))
    _apply(state, _open(state, album))
    path = calibration_path()
    assert isolated_data_dir in path.parents
    with sqlite3.connect(path) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
        blobs = conn.execute("SELECT MAX(LENGTH(embedding)), MAX(LENGTH(phash)) FROM photos")
        sizes = blobs.fetchone()
    conn.close()
    assert not {"thumb", "image", "data"} & columns
    assert np.max(sizes) <= 2048
