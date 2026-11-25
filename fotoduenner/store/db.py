"""Per-album SQLite store (02 §4): schema, scan sync, results, fingerprint reuse (T13)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fotoduenner.config import Config
from fotoduenner.paths import albums_dir, long_path
from fotoduenner.pipeline.analyze import FaceJob, FaceResult, Result
from fotoduenner.pipeline.thumbs import thumb_paths
from fotoduenner.scanner.discover import FoundPhoto, ScanResult
from fotoduenner.scanner.exif import read_exif_dt
from fotoduenner.scanner.sidecars import combine_ratings

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3
REUSE_MIN_SCHEMA = 2  # oldest schema whose photos/features columns match (v3: journal only)
SQL_CHUNK = 500  # stay below SQLite's bound-parameter limit

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos(
  id INTEGER PRIMARY KEY,
  rel_path TEXT NOT NULL UNIQUE,         -- POSIX separators, relative to the album
  companions TEXT NOT NULL DEFAULT '[]', -- JSON list of sidecar rel paths moved along
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,             -- cache key with size + algo_version
  fp_key TEXT NOT NULL,                  -- casefold(name)|size|mtime_ns (T13, lookup part)
  exif_dt TEXT,                          -- raw capture time (T13, confirmation part)
  file_hash BLOB,                        -- SHA-256 of the content (exact duplicates, A13)
  taken_at REAL,
  taken_at_source TEXT,                  -- 'exif' | 'mtime'
  camera TEXT,
  exposure_bias REAL,
  width INTEGER, height INTEGER,         -- as displayed (orientation applied)
  orientation INTEGER,
  rating_embedded INTEGER,               -- EXIF/embedded XMP
  rating_sidecar INTEGER,                -- .xmp sidecar, re-read on every scan
  rating INTEGER,                        -- combined (sidecars.combine_ratings)
  offline INTEGER NOT NULL DEFAULT 0,    -- cloud placeholder
  present INTEGER NOT NULL DEFAULT 1,    -- 0: gone from the album (moved, applied); kept as cache
  status TEXT NOT NULL DEFAULT 'pending',-- 'pending' | 'done' | 'error'
  error TEXT,
  algo_version INTEGER
);
CREATE INDEX IF NOT EXISTS photos_fp ON photos(fp_key);
CREATE INDEX IF NOT EXISTS photos_hash ON photos(file_hash);
CREATE TABLE IF NOT EXISTS features(
  photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
  phash BLOB,
  embedding BLOB,
  sharpness_global REAL, sharpness_peak REAL, motion_blur REAL,
  exposure_mean REAL, clip_low REAL, clip_high REAL, contrast REAL,
  aesthetic REAL,
  zeroshot TEXT,
  face_count INTEGER,
  faces TEXT,                            -- JSON list of faces (pipeline/faces.py Face.to_json)
  faces_native INTEGER NOT NULL DEFAULT 0 -- T6 second pass: 0 open, 1 done, -1 failed
);
CREATE TABLE IF NOT EXISTS decisions(
  photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
  manual TEXT,
  locked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS journal(
  id INTEGER PRIMARY KEY, batch_id TEXT, created_at REAL,
  src TEXT, dst TEXT,                    -- relative to the album, POSIX separators
  state TEXT,              -- 'planned' | 'moved' | 'reverted' | 'failed' | 'cancelled' | 'missing'
  photo_id INTEGER,                      -- NULL for entries imported from the protocol copy
  error TEXT,                            -- German message for the user (failures, notes)
  restored TEXT                          -- undo: actual target when the old name was taken
);
CREATE INDEX IF NOT EXISTS journal_batch ON journal(batch_id);
CREATE TABLE IF NOT EXISTS batches(
  batch_id TEXT PRIMARY KEY,
  created_at REAL,
  state TEXT,                            -- 'running' | 'applied' | 'undoing' | 'reverted' | 'empty'
  count_before INTEGER                   -- photos in the album before this batch (T2)
);
CREATE TABLE IF NOT EXISTS seen(          -- looked at by the user (D20), local only
  photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
  how INTEGER NOT NULL,                  -- bits: 1 open group, 2 tab, 4 large view, 8 confirmed
  shown INTEGER NOT NULL                 -- proposal shown at that moment: 1 keep, 0 sort out
);
CREATE TABLE IF NOT EXISTS done_groups(   -- stacks the user has confirmed (A35)
  sig TEXT PRIMARY KEY,                  -- members + proposals, computed by the frontend
  at REAL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

# Schema upgrades, applied in order to databases with an older user_version.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: ("ALTER TABLE features ADD COLUMN faces_native INTEGER NOT NULL DEFAULT 0",),
    3: (
        "ALTER TABLE journal ADD COLUMN photo_id INTEGER",
        "ALTER TABLE journal ADD COLUMN error TEXT",
        "ALTER TABLE journal ADD COLUMN restored TEXT",
    ),
}

# Columns copied when a photo is found again via its fingerprint.
REUSE_COLUMNS = (
    "file_hash",
    "exif_dt",
    "taken_at",
    "taken_at_source",
    "camera",
    "exposure_bias",
    "width",
    "height",
    "orientation",
    "rating_embedded",
    "status",
    "error",
    "algo_version",
)


def normalize_root(root: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(root)))  # noqa: PTH100 – lexical


def album_id_for(root: Path) -> str:
    """SHA1 of the normalised absolute folder path (02 §4)."""
    return hashlib.sha1(normalize_root(root).encode("utf-8")).hexdigest()


def fingerprint_key(name: str, size: int, mtime_ns: int) -> str:
    return f"{name.casefold()}|{size}|{mtime_ns}"


def connect(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade an existing database in place; a fresh one gets the full schema instead."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='features'").fetchone()
    if not exists or version >= SCHEMA_VERSION:
        return
    for target in range(version + 1, SCHEMA_VERSION + 1):
        for statement in MIGRATIONS.get(target, ()):
            conn.execute(statement)
        log.info("album database upgraded to schema %d", target)


def _chunks(items: Sequence, size: int = SQL_CHUNK) -> Iterator[Sequence]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass(slots=True)
class SyncReport:
    total: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    reused: int = 0  # found again via fingerprint (renamed/moved album or file)
    missing: int = 0  # in the cache, no longer in the album
    pending: int = 0  # to be analysed


class AlbumStore:
    """Cache of one album under %LOCALAPPDATA%\\FotoDuenner\\albums\\<album_id>\\."""

    def __init__(self, root: Path, data_root: Path | None = None) -> None:
        self.root = Path(os.path.abspath(root))  # noqa: PTH100 – lexical, keeps subst drives
        self.album_id = album_id_for(self.root)
        self.albums_root = albums_dir(data_root)
        self.dir = self.albums_root / self.album_id
        self.thumbs_dir = self.dir / "thumbs"
        self.db_path = self.dir / "album.db"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self.connect()) as conn, conn:
            _migrate(conn)
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES('root_path', ?)",
                (str(self.root),),
            )
        self._register()

    def connect(self) -> sqlite3.Connection:
        """New connection; SQLite connections must not cross threads."""
        return connect(self.db_path)

    def abs_path(self, rel_path: str) -> Path:
        return long_path(self.root).joinpath(*PurePosixPath(rel_path).parts)

    def _register(self) -> None:
        """Update albums\\index.json (path -> id, last opened) for "Zuletzt geöffnet"."""
        index_path = self.albums_root / "index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = {}
        index[self.album_id] = {"path": str(self.root), "last_opened": time.time()}
        tmp = index_path.with_name("index.json.tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(index_path)

    # --- scan sync ---------------------------------------------------------------------

    def sync(self, scan: ScanResult, cfg: Config) -> SyncReport:
        """Bring the photo table in line with the scan. Cache key: path + size + mtime + algo."""
        report = SyncReport(total=len(scan.photos))
        with contextlib.closing(self.connect()) as conn, conn:
            rows = {r["rel_path"]: r for r in conn.execute("SELECT * FROM photos")}
            seen: set[int] = set()
            new: list[tuple[int, FoundPhoto]] = []
            for found in scan.photos:
                row = rows.get(found.rel_path)
                key = fingerprint_key(found.name, found.size, found.mtime_ns)
                common = (
                    json.dumps(found.companions, ensure_ascii=False),
                    found.sidecar_rating,
                    int(found.offline),
                )
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO photos(rel_path, companions, rating_sidecar, offline, size,"
                        " mtime_ns, fp_key, rating) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            found.rel_path,
                            *common,
                            found.size,
                            found.mtime_ns,
                            key,
                            combine_ratings(found.sidecar_rating),
                        ),
                    )
                    new.append((int(cur.lastrowid or 0), found))
                    continue
                seen.add(row["id"])
                same_file = row["size"] == found.size and row["mtime_ns"] == found.mtime_ns
                rating = combine_ratings(row["rating_embedded"], found.sidecar_rating)
                if same_file:
                    current = (
                        row["status"] in ("done", "error")
                        and row["algo_version"] == cfg.algo_version
                    )
                    conn.execute(
                        "UPDATE photos SET companions=?, rating_sidecar=?, offline=?, rating=?,"
                        " present=1, status=? WHERE id=?",
                        (*common, rating, row["status"] if current else "pending", row["id"]),
                    )
                    report.unchanged += current
                    continue
                report.changed += 1
                conn.execute(
                    "UPDATE photos SET companions=?, rating_sidecar=?, offline=?, size=?,"
                    " mtime_ns=?, fp_key=?, present=1, status='pending', error=NULL,"
                    " file_hash=NULL, exif_dt=NULL, rating_embedded=NULL, rating=?,"
                    " algo_version=NULL WHERE id=?",
                    (
                        *common,
                        found.size,
                        found.mtime_ns,
                        key,
                        combine_ratings(found.sidecar_rating),
                        row["id"],
                    ),
                )
                conn.execute("DELETE FROM features WHERE photo_id=?", (row["id"],))

            gone = [r["id"] for r in rows.values() if r["id"] not in seen and r["present"]]
            for chunk in _chunks(gone):
                conn.execute(
                    f"UPDATE photos SET present=0 WHERE id IN ({','.join('?' * len(chunk))})",
                    chunk,
                )
            report.new = len(new)
            if new:
                report.reused = self._reuse_by_fingerprint(conn, new, cfg)
                report.new -= report.reused
            report.missing = conn.execute("SELECT COUNT(*) FROM photos WHERE present=0").fetchone()[
                0
            ]
            report.pending = conn.execute(
                "SELECT COUNT(*) FROM photos WHERE present=1 AND status='pending'"
            ).fetchone()[0]
        return report

    def _reuse_by_fingerprint(
        self, conn: sqlite3.Connection, new: list[tuple[int, FoundPhoto]], cfg: Config
    ) -> int:
        """Find new photos again in this album (moved file) or another one (renamed album).

        Lookup by name + size + mtime, confirmed by the EXIF capture time read from the
        file header (T13). Only fully analysed entries of the current algo version count.
        """
        by_key: dict[str, list[tuple[int, FoundPhoto]]] = {}
        for item in new:
            found = item[1]
            by_key.setdefault(fingerprint_key(found.name, found.size, found.mtime_ns), []).append(
                item
            )
        reused = 0

        # 1) Same album: a file moved between subfolders keeps its row (and decisions).
        for key, candidates in list(by_key.items()):
            old = conn.execute(
                "SELECT * FROM photos WHERE fp_key=? AND present=0 AND status='done'"
                " AND algo_version=?",
                (key, cfg.algo_version),
            ).fetchall()
            for old_row in old:
                if not candidates:
                    break
                new_id, found = candidates[0]
                if read_exif_dt(str(self.abs_path(found.rel_path))) != old_row["exif_dt"]:
                    continue
                candidates.pop(0)
                conn.execute("DELETE FROM photos WHERE id=?", (new_id,))
                conn.execute(
                    "UPDATE photos SET rel_path=?, companions=?, rating_sidecar=?, rating=?,"
                    " offline=?, present=1 WHERE id=?",
                    (
                        found.rel_path,
                        json.dumps(found.companions, ensure_ascii=False),
                        found.sidecar_rating,
                        combine_ratings(old_row["rating_embedded"], found.sidecar_rating),
                        int(found.offline),
                        old_row["id"],
                    ),
                )
                reused += 1
            if not candidates:
                del by_key[key]

        # 2) Other albums of this user (e.g. the album folder was renamed).
        if by_key:
            reused += self._reuse_from_other_albums(conn, by_key, cfg)
        return reused

    def _other_album_dbs(self) -> Iterable[Path]:
        for child in self.albums_root.iterdir():
            db = child / "album.db"
            if child.name != self.album_id and db.is_file():
                yield db

    def _reuse_from_other_albums(
        self,
        conn: sqlite3.Connection,
        by_key: dict[str, list[tuple[int, FoundPhoto]]],
        cfg: Config,
    ) -> int:
        reused = 0
        feature_cols = [r["name"] for r in conn.execute("PRAGMA table_info(features)")]
        for db_path in self._other_album_dbs():
            if not by_key:
                break
            try:
                other = connect(db_path, read_only=True)
            except sqlite3.Error:
                continue
            with contextlib.closing(other):
                try:
                    if other.execute("PRAGMA user_version").fetchone()[0] < REUSE_MIN_SCHEMA:
                        continue
                    keys = list(by_key)
                    matches: list[sqlite3.Row] = []
                    for chunk in _chunks(keys):
                        matches += other.execute(
                            "SELECT * FROM photos WHERE status='done' AND algo_version=?"
                            f" AND fp_key IN ({','.join('?' * len(chunk))})",
                            (cfg.algo_version, *chunk),
                        ).fetchall()
                except sqlite3.Error as exc:
                    log.info("skipping album cache %s: %s", db_path.parent.name, exc)
                    continue
                for src in matches:
                    candidates = by_key.get(src["fp_key"])
                    if not candidates:
                        continue
                    new_id, found = candidates[0]
                    if read_exif_dt(str(self.abs_path(found.rel_path))) != src["exif_dt"]:
                        continue
                    if not self._copy_thumbs(db_path.parent / "thumbs", src["id"], new_id):
                        continue
                    candidates.pop(0)
                    if not candidates:
                        del by_key[src["fp_key"]]
                    values = [src[c] for c in REUSE_COLUMNS]
                    rating = combine_ratings(src["rating_embedded"], found.sidecar_rating)
                    conn.execute(
                        f"UPDATE photos SET {', '.join(f'{c}=?' for c in REUSE_COLUMNS)},"
                        " rating=? WHERE id=?",
                        (*values, rating, new_id),
                    )
                    feature = other.execute(
                        "SELECT * FROM features WHERE photo_id=?", (src["id"],)
                    ).fetchone()
                    if feature is not None:
                        cols = [c for c in feature.keys() if c in feature_cols and c != "photo_id"]  # noqa: SIM118 – Row iterates values
                        conn.execute(
                            f"INSERT OR REPLACE INTO features(photo_id, {', '.join(cols)})"
                            f" VALUES(?{', ?' * len(cols)})",
                            (new_id, *(feature[c] for c in cols)),
                        )
                    reused += 1
        return reused

    def _copy_thumbs(self, src_dir: Path, src_id: int, dst_id: int) -> bool:
        pairs = list(
            zip(thumb_paths(src_dir, src_id), thumb_paths(self.thumbs_dir, dst_id), strict=True)
        )
        if not all(src.is_file() for src, _ in pairs):
            return False
        try:
            for src, dst in pairs:
                shutil.copyfile(src, dst)  # both are cache files of this program
        except OSError:
            return False
        return True

    # --- analysis bookkeeping ----------------------------------------------------------

    def pending(self, include_offline: bool = True) -> list[tuple[int, str]]:
        """Photos still to analyse; cloud placeholders can be left out (02 §7.3)."""
        offline = "" if include_offline else " AND offline=0"
        with contextlib.closing(self.connect()) as conn:
            return [
                (r["id"], r["rel_path"])
                for r in conn.execute(
                    f"SELECT id, rel_path FROM photos WHERE present=1 AND status='pending'{offline}"
                    " ORDER BY rel_path COLLATE NOCASE"
                )
            ]

    @staticmethod
    def save_results(conn: sqlite3.Connection, results: Iterable[Result], cfg: Config) -> None:
        """Write a batch of worker results in one transaction (02 §5)."""
        with conn:
            for res in results:
                if not res.ok or res.meta is None:
                    conn.execute(
                        "UPDATE photos SET status='error', error=?, algo_version=? WHERE id=?",
                        (res.error, cfg.algo_version, res.photo_id),
                    )
                    continue
                meta = res.meta
                row = conn.execute(
                    "SELECT rating_sidecar FROM photos WHERE id=?", (res.photo_id,)
                ).fetchone()
                if row is None:
                    continue
                conn.execute(
                    "UPDATE photos SET file_hash=?, exif_dt=?,"
                    " taken_at=COALESCE(?, mtime_ns / 1e9), taken_at_source=?, camera=?,"
                    " exposure_bias=?, width=?, height=?, orientation=?, rating_embedded=?,"
                    " rating=?, status='done', error=NULL, algo_version=? WHERE id=?",
                    (
                        res.file_hash,
                        meta.exif_dt,
                        meta.taken_at,
                        "exif" if meta.taken_at is not None else "mtime",
                        meta.camera,
                        meta.exposure_bias,
                        meta.width,
                        meta.height,
                        meta.orientation,
                        meta.rating,
                        combine_ratings(meta.rating, row["rating_sidecar"]),
                        cfg.algo_version,
                        res.photo_id,
                    ),
                )
                feat = res.features
                if feat is None:
                    conn.execute("DELETE FROM features WHERE photo_id=?", (res.photo_id,))
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO features(photo_id, phash, embedding,"
                    " sharpness_global, sharpness_peak, exposure_mean, clip_low, clip_high,"
                    " contrast, aesthetic, zeroshot, face_count, faces, faces_native)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (
                        res.photo_id,
                        feat.phash,
                        feat.embedding,
                        feat.sharpness_global,
                        feat.sharpness_peak,
                        feat.exposure_mean,
                        feat.clip_low,
                        feat.clip_high,
                        feat.contrast,
                        feat.aesthetic,
                        json.dumps(feat.zeroshot, separators=(",", ":")),
                        feat.face_count,
                        json.dumps(feat.faces, separators=(",", ":")),
                    ),
                )

    def status_counts(self) -> dict[str, int]:
        with contextlib.closing(self.connect()) as conn:
            return {
                r["status"]: r["n"]
                for r in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM photos WHERE present=1 GROUP BY status"
                )
            }

    # --- second pass (T6) --------------------------------------------------------------

    def series_inputs(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        """Rows needed to find series (03 §5.1) among analysed photos still in the album."""
        return conn.execute(
            "SELECT p.id, p.rel_path, p.taken_at, p.taken_at_source, f.phash, f.embedding,"
            " f.face_count, f.faces, f.faces_native FROM photos p JOIN features f"
            " ON f.photo_id = p.id WHERE p.present=1 AND p.status='done'"
            " AND f.phash IS NOT NULL AND f.embedding IS NOT NULL ORDER BY p.id"
        ).fetchall()

    def face_jobs(self, rows: Iterable[sqlite3.Row]) -> list[FaceJob]:
        return [
            FaceJob(r["id"], str(self.abs_path(r["rel_path"])), r["rel_path"], r["faces"])
            for r in rows
        ]

    @staticmethod
    def save_face_results(conn: sqlite3.Connection, results: Iterable[FaceResult]) -> None:
        """Store native face sharpness. A failed second pass keeps the first-pass values."""
        with conn:
            for res in results:
                if res.ok:
                    conn.execute(
                        "UPDATE features SET faces=?, faces_native=1 WHERE photo_id=?",
                        (res.faces_json, res.photo_id),
                    )
                else:
                    conn.execute(
                        "UPDATE features SET faces_native=-1 WHERE photo_id=?", (res.photo_id,)
                    )

    # --- selection (03 §5–§8): manual decisions and settings ---------------------------

    @staticmethod
    def selection_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        """Analysed photos still in the album, with their features (input of the selection)."""
        return conn.execute(
            "SELECT p.id, p.rel_path, p.taken_at, p.taken_at_source, p.camera, p.exposure_bias,"
            " p.file_hash, p.rating, f.phash, f.embedding, f.sharpness_peak, f.exposure_mean,"
            " f.contrast, f.clip_low, f.clip_high, f.aesthetic, f.zeroshot, f.faces"
            " FROM photos p JOIN features f ON f.photo_id = p.id"
            " WHERE p.present=1 AND p.status='done'"
            " AND f.phash IS NOT NULL AND f.embedding IS NOT NULL ORDER BY p.id"
        ).fetchall()

    @staticmethod
    def manual_decisions(conn: sqlite3.Connection) -> dict[int, str]:
        return {
            r["photo_id"]: r["manual"]
            for r in conn.execute("SELECT photo_id, manual FROM decisions WHERE manual IS NOT NULL")
        }

    @staticmethod
    def set_manual(conn: sqlite3.Connection, photo_id: int, manual: str | None) -> None:
        """'keep' / 'reject' / None (back to the automatic proposal)."""
        with conn:
            conn.execute(
                "INSERT INTO decisions(photo_id, manual) VALUES(?, ?)"
                " ON CONFLICT(photo_id) DO UPDATE SET manual=excluded.manual",
                (photo_id, manual),
            )

    @staticmethod
    def done_groups(conn: sqlite3.Connection) -> list[str]:
        return [r["sig"] for r in conn.execute("SELECT sig FROM done_groups ORDER BY at")]

    @staticmethod
    def set_group_done(conn: sqlite3.Connection, sig: str, done: bool) -> None:
        with conn:
            if done:
                conn.execute(
                    "INSERT OR REPLACE INTO done_groups(sig, at) VALUES(?, ?)", (sig, time.time())
                )
            else:
                conn.execute("DELETE FROM done_groups WHERE sig=?", (sig,))

    @staticmethod
    def add_seen(conn: sqlite3.Connection, items: Iterable[tuple[int, int, bool]]) -> None:
        """Photos the user looked at: (photo id, how, shown proposal). Unknown ids are ignored.

        With another proposal than before, the earlier looks do not count any more.
        """
        with conn:
            conn.executemany(
                "INSERT INTO seen(photo_id, how, shown) SELECT id, ?, ? FROM photos WHERE id=?"
                " ON CONFLICT(photo_id) DO UPDATE SET how = CASE WHEN shown = excluded.shown"
                " THEN how | excluded.how ELSE excluded.how END, shown = excluded.shown",
                [(how, int(shown), photo_id) for photo_id, how, shown in items],
            )

    @staticmethod
    def seen(conn: sqlite3.Connection) -> dict[int, tuple[int, int]]:
        return {
            r["photo_id"]: (r["how"], r["shown"])
            for r in conn.execute("SELECT photo_id, how, shown FROM seen")
        }

    @staticmethod
    def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}

    @staticmethod
    def put_settings(conn: sqlite3.Connection, values: dict[str, str]) -> None:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", values.items()
            )
