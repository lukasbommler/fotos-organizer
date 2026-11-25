"""Übernehmen und Rückgängig (05 M6): moving, conflicts, locked files, crash, undo without cache."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fotoduenner.apply import mover, protocol
from fotoduenner.apply.undo import last_batch, recover, undo
from fotoduenner.config import Config
from fotoduenner.scanner.discover import scan_album
from fotoduenner.store.db import AlbumStore

CFG = Config()
ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, seed: int, size: int = 2000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(hashlib.sha256(str(seed).encode()).digest() * (size // 32))


def _listing(root: Path) -> dict[str, tuple[int, str]]:
    """All files below root: relative path -> (size, SHA-256) – the M6 invariant."""
    out = {}
    for path in root.rglob("*"):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = (
                path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return out


def _album(root: Path, names: list[str]) -> AlbumStore:
    for k, name in enumerate(names):
        _write(root / name, k, 2000 + 64 * k)
    store = AlbumStore(root)
    store.sync(scan_album(root, CFG), CFG)
    return store


def _ids(store: AlbumStore, *rels: str) -> list[int]:
    with contextlib.closing(store.connect()) as conn:
        rows = {r["rel_path"]: r["id"] for r in conn.execute("SELECT id, rel_path FROM photos")}
    return [rows[r] for r in rels]


def _apply(store: AlbumStore, rels: list[str], **kw: object) -> mover.Report:
    batch = mover.start(store, _ids(store, *rels), 99, CFG)
    return mover.run(store, batch, CFG, **kw)  # type: ignore[arg-type]


def _journal(store: AlbumStore) -> list[tuple[str, str, str]]:
    with contextlib.closing(store.connect()) as conn:
        return [
            (r["src"], r["dst"], r["state"])
            for r in conn.execute("SELECT src, dst, state FROM journal ORDER BY id")
        ]


def _status(store: AlbumStore) -> dict:
    with contextlib.closing(store.connect()) as conn:
        return mover.status(conn)


ALBUM = ["a.jpg", "a.xmp", "Tag 2/b.jpg", "Tag 2/b.jpg.xmp", "Tag 2/c.JPG", "d.jpg"]


def test_apply_moves_sidecars_keeps_folders_and_undo_restores_bytes(tmp_path: Path) -> None:
    root = tmp_path / "Südtirol 2024"
    store = _album(root, ALBUM)
    before = _listing(root)
    report = _apply(store, ["a.jpg", "Tag 2/b.jpg"])
    assert (report.photos, report.files, report.failed_count) == (2, 4, 0)
    after = _listing(root)
    for rel in ("a.jpg", "a.xmp", "Tag 2/b.jpg", "Tag 2/b.jpg.xmp"):
        assert rel not in after and after[f"_Aussortiert/{rel}"] == before[rel]
    assert after["Tag 2/c.JPG"] == before["Tag 2/c.JPG"] and "d.jpg" in after
    assert (root / "_Aussortiert" / protocol.PROTOCOL_NAME).is_file()
    # the rejected folder is never scanned: the moved photos are gone from the album
    assert sorted(p.rel_path for p in scan_album(root, CFG).photos) == ["Tag 2/c.JPG", "d.jpg"]
    with contextlib.closing(store.connect()) as conn:
        present = dict(conn.execute("SELECT rel_path, present FROM photos").fetchall())
    assert present == {"a.jpg": 0, "Tag 2/b.jpg": 0, "Tag 2/c.JPG": 1, "d.jpg": 1}
    st = _status(store)
    assert st["applied"] and st["hold"] and st["count_before"] == 99
    assert st["last"]["photos"] == 2 and st["last"]["files"] == 4

    back = undo(store, last_batch_of(store), CFG)
    assert (back.photos, back.files, back.failed_count) == (2, 4, 0)
    assert _listing(root) == before  # byte-identical, _Aussortiert and the protocol are gone
    assert not (root / "_Aussortiert").exists()
    st = _status(store)
    assert not st["applied"] and not st["hold"] and st["last"] is None


def last_batch_of(store: AlbumStore) -> str:
    with contextlib.closing(store.connect()) as conn:
        batch = last_batch(conn)
    assert batch is not None
    return batch


def test_taken_names_get_a_suffix_and_nothing_is_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "Album"
    store = _album(root, ALBUM)
    _write(root / "_Aussortiert" / "a.jpg", 100)  # from an earlier clean-up of the user
    _write(root / "_Aussortiert" / "a.xmp", 101)
    before = _listing(root)
    _apply(store, ["a.jpg"])
    after = _listing(root)
    assert after["_Aussortiert/a.jpg"] == before["_Aussortiert/a.jpg"]  # untouched
    assert after["_Aussortiert/a (2).jpg"] == before["a.jpg"]
    assert after["_Aussortiert/a (2).xmp"] == before["a.xmp"]  # same suffix as its photo

    _write(root / "a.jpg", 200)  # a new photo with the old name appeared meanwhile
    new = _listing(root)["a.jpg"]
    report = undo(store, last_batch_of(store), CFG)
    final = _listing(root)
    assert final["a.jpg"] == new and final["a (2).jpg"] == before["a.jpg"]
    assert final["a.xmp"] == before["a.xmp"]
    assert report.notes and "a (2).jpg" in report.notes[0]
    assert final["_Aussortiert/a.jpg"] == before["_Aussortiert/a.jpg"]  # user files stay
    assert not (root / "_Aussortiert" / protocol.PROTOCOL_NAME).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows file locking")
def test_locked_file_fails_the_rest_is_moved_retry_moves_it(tmp_path: Path) -> None:
    root = tmp_path / "Album"
    store = _album(root, ALBUM)
    with (root / "Tag 2" / "b.jpg").open("rb"):  # opened in "another program"
        report = _apply(store, ["a.jpg", "Tag 2/b.jpg", "d.jpg"])
    assert report.photos == 2 and report.failed_count == 1
    assert report.failed[0]["name"] == "Tag 2/b.jpg" and "geöffnet" in report.failed[0]["error"]
    states = {src: state for src, _, state in _journal(store)}
    assert states["Tag 2/b.jpg"] == "failed" and states["Tag 2/b.jpg.xmp"] == "cancelled"
    assert (root / "Tag 2" / "b.jpg.xmp").is_file()  # the sidecar stays with its photo
    assert _status(store)["last"]["pending_count"] == 2

    again = mover.retry(store, last_batch_of(store), CFG)
    assert (again.photos, again.files, again.failed_count) == (1, 2, 0)
    assert (root / "_Aussortiert" / "Tag 2" / "b.jpg").is_file()
    assert _status(store)["last"]["pending_count"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows file locking")
def test_everything_locked_leaves_the_album_as_it_was(tmp_path: Path) -> None:
    root = tmp_path / "Album"
    store = _album(root, ALBUM)
    before = _listing(root)
    with (root / "a.jpg").open("rb"), (root / "Tag 2" / "b.jpg").open("rb"):
        report = _apply(store, ["a.jpg", "Tag 2/b.jpg"])
    assert report.files == 0 and report.failed_count == 2 and len(report.failed) == 2
    assert _listing(root) == before and not (root / "_Aussortiert").exists()
    st = _status(store)
    assert not st["applied"] and not st["hold"] and st["last"] is None


def test_plan_is_on_disk_before_moving(tmp_path: Path) -> None:
    """Power failure: the plan commit must be synchronous=FULL, the rest may stay NORMAL."""
    store = _album(tmp_path / "Album", ALBUM)
    with contextlib.closing(store.connect()) as conn:
        with mover.durable(conn):
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
            conn.execute("INSERT INTO settings(key, value) VALUES('x', '1')")
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    with contextlib.closing(store.connect()) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='x'").fetchone()[0] == "1"


def test_changed_file_is_not_moved(tmp_path: Path) -> None:
    root = tmp_path / "Album"
    store = _album(root, ALBUM)
    _write(root / "d.jpg", 300, 5000)  # edited after the analysis
    report = _apply(store, ["a.jpg", "d.jpg"])
    assert report.photos == 1 and report.failed[0]["name"] == "d.jpg"
    assert (root / "d.jpg").is_file()


CRASH_SCRIPT = """
import os, sys
from pathlib import Path
from fotoduenner.apply import mover
from fotoduenner.apply.undo import last_batch, undo
from fotoduenner.config import Config
from fotoduenner.store.db import AlbumStore
import contextlib

root, kind, limit = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
store, cfg, count = AlbumStore(root), Config(), [0]

def hook(event, rel):
    count[0] += 1
    if count[0] >= limit:
        os._exit(3)  # killed right after a rename, before the journal commit

if kind == "apply":
    ids = [int(x) for x in sys.argv[4].split(",")]
    mover.run(store, mover.start(store, ids, len(ids), cfg), cfg, hook=hook)
else:
    with contextlib.closing(store.connect()) as conn:
        batch = last_batch(conn)
    undo(store, batch, cfg, hook=hook)
"""


def _crash(root: Path, kind: str, limit: int, ids: list[int] | None = None) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    args = [sys.executable, "-c", CRASH_SCRIPT, str(root), kind, str(limit)]
    if ids:
        args.append(",".join(map(str, ids)))
    res = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert res.returncode == 3, res.stderr


def test_crash_at_30_percent_is_repaired_and_undo_restores_everything(tmp_path: Path) -> None:
    root = tmp_path / "Album mit Ümlaut"
    names = [f"Tag {k % 3}/IMG_{k:04d}.jpg" for k in range(20)]
    names += [f"Tag {k % 3}/IMG_{k:04d}.xmp" for k in range(0, 20, 4)]
    store = _album(root, names)
    before = _listing(root)
    ids = _ids(store, *[n for n in names if n.endswith(".jpg")])
    _crash(root, "apply", 8, ids)  # 8 of 25 files ≈ 30 %
    with contextlib.closing(store.connect()) as conn:
        committed = conn.execute("SELECT COUNT(*) FROM journal WHERE state='moved'").fetchone()[0]
    assert committed == 7  # the 8th rename happened, its commit did not

    assert recover(store)  # "Neustart": opening the album repairs the journal
    states = [s for _, _, s in _journal(store)]
    assert states.count("moved") == 8 and states.count("cancelled") == 17
    assert "planned" not in states
    moved = {dst for _, dst, s in _journal(store) if s == "moved"}
    on_disk = {k for k in _listing(root) if k.startswith("_Aussortiert/") and "Protokoll" not in k}
    assert moved == on_disk
    proto = json.loads((root / "_Aussortiert" / protocol.PROTOCOL_NAME).read_text("utf-8"))
    assert [e["state"] for e in proto["batches"][0]["entries"]].count("moved") == 8
    st = _status(store)
    assert st["applied"] and st["last"]["pending_count"] == 17

    # interrupted undo is repaired as well; the second undo finishes the job
    _crash(root, "undo", 3)
    assert recover(store)
    undo(store, last_batch_of(store), CFG)
    assert _listing(root) == before


def test_undo_without_cache_only_from_the_protocol_copy(tmp_path: Path) -> None:
    root = tmp_path / "Album"
    store = _album(root, ALBUM)
    before = _listing(root)
    _apply(store, ["a.jpg", "Tag 2/b.jpg", "Tag 2/c.JPG"])
    renamed = tmp_path / "Album umbenannt"
    root.rename(renamed)  # album folder renamed as well: protocol paths are relative
    shutil.rmtree(os.environ["FOTODUENNER_DATA"])  # program cache deleted

    fresh = AlbumStore(renamed)
    assert len(recover(fresh)) == 1
    st = _status(fresh)
    assert st["applied"] and st["hold"] and st["last"]["photos"] == 3
    report = undo(fresh, last_batch_of(fresh), CFG)
    assert report.photos == 3 and report.failed_count == 0
    assert _listing(renamed) == before


def test_protocol_entries_outside_the_album_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "Album"
    (root / "_Aussortiert").mkdir(parents=True)
    entries = [
        {"src": "../fremd.jpg", "dst": "_Aussortiert/x.jpg", "state": "moved"},
        {"src": "a.jpg", "dst": "../../Windows/x.jpg", "state": "moved"},
        {"src": "C:/x.jpg", "dst": "_Aussortiert/x.jpg", "state": "moved"},
        {"src": "a.jpg", "dst": "_Aussortiert/a.jpg", "state": "moved"},
    ]
    data = {"format": 1, "batches": [{"batch_id": "b1", "entries": entries}]}
    (root / "_Aussortiert" / protocol.PROTOCOL_NAME).write_text(json.dumps(data), "utf-8")
    batches = protocol.read(root)
    assert [(e["src"], e["dst"]) for e in batches[0]["entries"]] == [
        ("a.jpg", "_Aussortiert/a.jpg")
    ]


def test_suffix_names() -> None:
    assert (
        mover.with_suffix("_Aussortiert/T/IMG_1.jpg", "IMG_1", 2) == "_Aussortiert/T/IMG_1 (2).jpg"
    )
    assert mover.with_suffix("x/IMG_1.jpg.xmp", "IMG_1", 3) == "x/IMG_1 (3).jpg.xmp"
    assert mover.with_suffix("x/IMG_1.xmp", "IMG_1", 2) == "x/IMG_1 (2).xmp"
    assert mover.base_stem("IMG_1.JPG.xmp") == "IMG_1" and mover.base_stem("a.b.jpg") == "a.b"


# Code review check of 05 M6: no delete operation may touch user files.
ALLOWED_DELETES = {
    ("apply/protocol.py", "path.unlink()"),  # own protocol copy
    ("apply/mover.py", "os.rmdir("),  # empty folders only
    ("instance.py", "self.info_path.unlink()"),  # program lock file
}
DELETE_RE = re.compile(r"os\.remove\(|os\.unlink\(|\.unlink\(|rmtree|send2trash|os\.rmdir\(")


def test_no_delete_operation_on_user_files() -> None:
    package = ROOT / "fotoduenner"
    violations = []
    for path in package.rglob("*.py"):
        rel = path.relative_to(package).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            if DELETE_RE.search(code) and not any(
                f == rel and call in code for f, call in ALLOWED_DELETES
            ):
                violations.append(f"{rel}: {code.strip()}")
    assert not violations
