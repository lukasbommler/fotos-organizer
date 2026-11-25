"""Calibration records (D20): proposal and final decision of every photo at "Übernehmen".

One SQLite file in the data folder of the program, separate from the album caches, so clearing a
cache does not lose them. A record is self-contained: with the stored features, settings and
configuration the selection can be computed again without the photos (scripts/calibrate.py).
No image data is stored and nothing leaves this computer.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
from collections.abc import Collection, Iterator
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from fotoduenner import __version__
from fotoduenner.config import Config
from fotoduenner.grouping.sequences import Shift, ShiftFn
from fotoduenner.paths import calibration_path
from fotoduenner.selection.engine import Engine, input_from_rows
from fotoduenner.selection.modes import Settings

log = logging.getLogger(__name__)

FORMAT = 1
# columns of AlbumStore.selection_rows: the complete input of the selection
FEATURE_COLUMNS = (
    "id",
    "rel_path",
    "taken_at",
    "taken_at_source",
    "camera",
    "exposure_bias",
    "file_hash",
    "rating",
    "phash",
    "embedding",
    "sharpness_peak",
    "exposure_mean",
    "contrast",
    "clip_low",
    "clip_high",
    "aesthetic",
    "zeroshot",
    "faces",
)
DECISION_COLUMNS = ("auto_keep", "auto_review", "auto_code", "auto_ref", "manual", "final_keep")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS records(
  record_id TEXT PRIMARY KEY,   -- batch id of the apply journal
  format INTEGER, created_at REAL, program TEXT, algo_version INTEGER,
  album_id TEXT,
  earlier_batches INTEGER,      -- > 0: the album had already been thinned before
  mode TEXT, keep_target REAL, level INTEGER, low_singles INTEGER,
  config TEXT,                  -- JSON of the configuration that made the proposal
  reverted_at REAL              -- "Rückgängig": the decision was taken back
);
CREATE TABLE IF NOT EXISTS photos(
  record_id TEXT, {", ".join(FEATURE_COLUMNS)},
  {", ".join(DECISION_COLUMNS)},
  PRIMARY KEY(record_id, id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS shifts(  -- panorama check, measured on previews; dx NULL: no result
  record_id TEXT, a INTEGER, b INTEGER, dx REAL, dy REAL, peak REAL
);
"""

Shifts = dict[tuple[int, int], Shift | None]  # by photo id


@dataclass(slots=True)
class Snapshot:
    """State of the review at the moment of applying (Review.snapshot)."""

    settings: Settings
    rows: list[Any]  # AlbumStore.selection_rows
    decisions: dict[int, tuple[Any, ...]]  # photo id -> DECISION_COLUMNS without final_keep
    shifts: Shifts


@dataclass(slots=True)
class Record:
    record_id: str
    created_at: float
    album_id: str
    earlier_batches: int
    reverted: bool
    settings: Settings
    cfg: Config
    rows: list[sqlite3.Row]  # features + decisions, ordered by photo id
    shifts: Shifts

    def engine(self, cfg: Config | None = None) -> Engine:
        """Selection for this album again, with the recorded or another configuration."""
        cfg = cfg or self.cfg
        inp = input_from_rows(self.rows, cfg)

        def shift(a: int, b: int) -> Shift | None:
            return self.shifts.get((int(inp.ids[a]), int(inp.ids[b])))

        shift_fn: ShiftFn = shift
        return Engine(inp, cfg, shift_fn=shift_fn)


@contextlib.contextmanager
def _open(data_root: Path | None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(calibration_path(data_root), timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def save(
    record_id: str,
    snapshot: Snapshot,
    rejected: Collection[int],
    album_id: str,
    earlier_batches: int,
    cfg: Config,
    data_root: Path | None = None,
) -> None:
    rejected = set(rejected)
    settings = snapshot.settings
    photo_rows = [
        (
            record_id,
            *(row[c] for c in FEATURE_COLUMNS),
            *snapshot.decisions[row["id"]],
            int(row["id"] not in rejected),
        )
        for row in snapshot.rows
    ]
    columns = ("record_id", *FEATURE_COLUMNS, *DECISION_COLUMNS)
    with _open(data_root) as conn, conn:
        conn.execute(
            "INSERT INTO records VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                record_id,
                FORMAT,
                time.time(),
                __version__,
                cfg.algo_version,
                album_id,
                earlier_batches,
                settings.mode,
                settings.keep_target,
                settings.level,
                int(settings.low_singles),
                json.dumps(asdict(cfg)),
            ),
        )
        conn.executemany(
            f"INSERT INTO photos({', '.join(columns)}) VALUES({', '.join('?' * len(columns))})",
            photo_rows,
        )
        conn.executemany(
            "INSERT INTO shifts VALUES(?, ?, ?, ?, ?, ?)",
            [
                (record_id, a, b, *(shift or (None, None, None)))
                for (a, b), shift in snapshot.shifts.items()
            ],
        )
    log.info("calibration record %s: %d photos", record_id, len(photo_rows))


def mark_reverted(record_id: str, data_root: Path | None = None) -> None:
    """The batch was undone: the record no longer shows what the user finally wanted."""
    if not calibration_path(data_root).is_file():
        return
    with _open(data_root) as conn, conn:
        conn.execute("UPDATE records SET reverted_at=? WHERE record_id=?", (time.time(), record_id))


def _tuples(value: Any) -> Any:
    return tuple(_tuples(v) for v in value) if isinstance(value, list) else value


def _config(text: str) -> Config:
    known = {f.name for f in fields(Config)}
    return Config(**{k: _tuples(v) for k, v in json.loads(text).items() if k in known})


def load(data_root: Path | None = None, include_reverted: bool = False) -> list[Record]:
    if not calibration_path(data_root).is_file():
        return []
    records = []
    with _open(data_root) as conn:
        for r in conn.execute("SELECT * FROM records ORDER BY created_at").fetchall():
            if r["format"] != FORMAT or (r["reverted_at"] is not None and not include_reverted):
                continue
            rid = r["record_id"]
            records.append(
                Record(
                    record_id=rid,
                    created_at=r["created_at"],
                    album_id=r["album_id"],
                    earlier_batches=r["earlier_batches"],
                    reverted=r["reverted_at"] is not None,
                    settings=Settings(
                        mode=r["mode"],
                        keep_target=r["keep_target"],
                        level=r["level"],
                        low_singles=bool(r["low_singles"]),
                    ),
                    cfg=_config(r["config"]),
                    rows=conn.execute(
                        "SELECT * FROM photos WHERE record_id=? ORDER BY id", (rid,)
                    ).fetchall(),
                    shifts={
                        (s["a"], s["b"]): None if s["dx"] is None else (s["dx"], s["dy"], s["peak"])
                        for s in conn.execute("SELECT * FROM shifts WHERE record_id=?", (rid,))
                    },
                )
            )
    return records
