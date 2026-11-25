"""Übernehmen (02 §6): move rejected photos and their sidecars into ``<Album>\\_Aussortiert\\``.

Only ``os.rename`` is used. The target lies inside the album, i.e. on the same drive, so a move is
an atomic rename; on Windows it fails when the target exists, so nothing is ever overwritten
(T3). Every move is in the journal (state ``planned``) before it happens and is marked ``moved``
in a commit right after it. A crash in between is repaired from the file system (undo.recover).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from fotoduenner.apply import protocol
from fotoduenner.config import Config
from fotoduenner.paths import long_path
from fotoduenner.scanner.discover import REJECT_DIR
from fotoduenner.scanner.sidecars import JPEG_SUFFIXES, SIDECAR_SUFFIX
from fotoduenner.store.db import SQL_CHUNK, AlbumStore

log = logging.getLogger(__name__)

PENDING_LIST_LIMIT = 50  # failures listed in the dialog; the count is always complete
MSG_CHANGED = "Datei wurde seit der Analyse verändert – nicht verschoben. Bitte Album neu öffnen."
MSG_COMPANION = "Nicht verschoben, weil das zugehörige Bild nicht verschoben werden konnte."
MSG_CANCELLED = "Nicht verschoben – der Vorgang wurde unterbrochen."


class ApplyError(Exception):
    """A batch cannot start; the message is German and shown to the user."""


@dataclass(slots=True)
class Progress:
    kind: str  # 'apply' | 'undo'
    done: int
    total: int
    current: str | None = None


@dataclass(slots=True)
class Report:
    kind: str  # 'apply' | 'undo'
    batch_id: str
    photos: int = 0  # photos moved (apply) or brought back (undo)
    files: int = 0  # including sidecars
    failed: list[dict[str, str]] = field(default_factory=list)  # {"name", "error"}
    failed_count: int = 0
    notes: list[str] = field(default_factory=list)
    cancelled: int = 0

    def fail(self, rel: str, message: str) -> None:
        self.failed_count += 1
        if len(self.failed) < PENDING_LIST_LIMIT:
            self.failed.append({"name": rel, "error": message})

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


ProgressFn = Callable[[Progress], None]
Hook = Callable[[str, str], None]  # (event, rel path); tests simulate a crash here


# --- names and paths ---------------------------------------------------------------------


def is_photo(rel: str) -> bool:
    return PurePosixPath(rel).suffix.casefold() in JPEG_SUFFIXES


def base_stem(name: str) -> str:
    """Stem shared by a photo and its sidecars: IMG_1.jpg, IMG_1.xmp, IMG_1.jpg.xmp -> IMG_1."""
    path = PurePosixPath(name)
    stem = path.stem
    if path.suffix.casefold() == SIDECAR_SUFFIX and PurePosixPath(stem).suffix.casefold() in (
        JPEG_SUFFIXES
    ):
        stem = PurePosixPath(stem).stem
    return stem


def with_suffix(rel: str, stem: str, n: int | None) -> str:
    """``IMG_1.jpg`` -> ``IMG_1 (2).jpg``; sidecars keep matching: ``IMG_1 (2).jpg.xmp``."""
    if n is None:
        return rel
    path = PurePosixPath(rel)
    name = path.name
    if name.casefold().startswith(stem.casefold() + "."):
        new = f"{name[: len(stem)]} ({n}){name[len(stem) :]}"
    else:
        new = f"{path.stem} ({n}){path.suffix}"
    return str(path.with_name(new))


def abs_path(root: Path, rel: str) -> Path:
    return long_path(root).joinpath(*PurePosixPath(rel).parts)


def exists(root: Path, rel: str) -> bool:
    return os.path.lexists(abs_path(root, rel))


def free_names(
    root: Path, rels: Sequence[str], stem: str, taken: set[str], limit: int
) -> list[str]:
    """First suffix (none, 2, 3 …) for which no target exists on disk or is taken in this run.

    The same suffix for a photo and its sidecars keeps them recognisable as a pair.
    """
    for n in (None, *range(2, limit + 1)):
        names = [with_suffix(rel, stem, n) for rel in rels]
        if all(name.casefold() not in taken and not exists(root, name) for name in names):
            return names
    raise FileExistsError(f"no free name for {rels[0]}")


def rename(src: Path, dst: Path) -> None:
    """Move without ever replacing: os.rename fails on Windows if ``dst`` exists (T3)."""
    if os.path.lexists(dst):  # also on other platforms, where rename would replace
        raise FileExistsError(17, "target exists", str(dst))
    os.rename(src, dst)  # noqa: PTH104 – the one move primitive the spec allows (02 §6)


def failure_text(exc: OSError) -> str:
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, FileNotFoundError) or winerror in (2, 3):
        return "Datei nicht gefunden."
    if winerror in (32, 33):
        return "Datei ist in einem anderen Programm geöffnet – dort schließen und erneut versuchen."
    if isinstance(exc, PermissionError) or winerror == 5:
        return "Keine Berechtigung – Datei ist schreibgeschützt oder gesperrt."
    if winerror == 17:
        return "Ziel liegt auf einem anderen Laufwerk."
    if isinstance(exc, FileExistsError):
        return "Kein freier Dateiname im Zielordner."
    return f"Verschieben fehlgeschlagen: {exc.strerror or exc}"


def ids_query(conn: sqlite3.Connection, sql: str, ids: Sequence[int]) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for start in range(0, len(ids), SQL_CHUNK):
        chunk = ids[start : start + SQL_CHUNK]
        rows += conn.execute(sql.format(ids=",".join("?" * len(chunk))), chunk).fetchall()
    return rows


@contextlib.contextmanager
def durable(conn: sqlite3.Connection) -> Iterator[None]:
    """Transaction that is on the disk before anything is moved.

    With the store's default (WAL, synchronous=NORMAL) a power failure may lose the last
    commits. Harmless for "moved" (recover() looks at the files), not for the plan itself.
    """
    conn.execute("PRAGMA synchronous=FULL")
    try:
        with conn:
            yield
    finally:
        conn.execute("PRAGMA synchronous=NORMAL")


# --- album state after applying (T2) -------------------------------------------------------


def refresh_applied(conn: sqlite3.Connection, hold: bool | None = None) -> None:
    """Album status "übernommen" + original count from the oldest batch still active."""
    first = conn.execute(
        "SELECT count_before FROM batches WHERE state IN (?, ?, ?) ORDER BY created_at LIMIT 1",
        protocol.ACTIVE,
    ).fetchone()
    values: dict[str, str] = {}
    if first is None:
        values = {"applied": "0", "hold": "0"}
        conn.execute("DELETE FROM settings WHERE key='count_before'")
    else:
        values["applied"] = "1"
        if first["count_before"] is not None:
            values["count_before"] = str(first["count_before"])
        if hold is not None:
            values["hold"] = "1" if hold else "0"
    conn.executemany("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", values.items())


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Apply/undo state for the UI: album status and the batch "Rückgängig" would revert."""
    settings = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    count = settings.get("count_before")
    out: dict[str, Any] = {
        "applied": settings.get("applied") == "1",
        "hold": settings.get("hold") == "1",
        "count_before": int(count) if count and count.isdigit() else None,
        "batches": conn.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN (?, ?, ?)", protocol.ACTIVE
        ).fetchone()[0],
        "last": None,
    }
    last = conn.execute(
        "SELECT * FROM batches WHERE state IN (?, ?, ?) ORDER BY created_at DESC LIMIT 1",
        protocol.ACTIVE,
    ).fetchone()
    if last is not None:
        rows = conn.execute(
            "SELECT src, state, error FROM journal WHERE batch_id=? ORDER BY id",
            (last["batch_id"],),
        ).fetchall()
        moved = [r for r in rows if r["state"] == "moved"]
        pending = [r for r in rows if r["state"] in ("planned", "failed", "cancelled")]
        out["last"] = {
            "batch_id": last["batch_id"],
            "created_at": last["created_at"],
            "state": last["state"],
            "photos": sum(is_photo(r["src"]) for r in moved),
            "files": len(moved),
            "pending_count": len(pending),
            "pending": [
                {"name": r["src"], "error": r["error"] or MSG_CANCELLED}
                for r in pending[:PENDING_LIST_LIMIT]
            ],
        }
    return out


def remove_empty_dirs(store: AlbumStore, dirs: set[PurePosixPath]) -> None:
    """Remove folders in _Aussortiert that became empty – os.rmdir only, never anything else."""
    for rel in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        path = rel
        while len(path.parts) > 1:  # stop below _Aussortiert itself
            try:
                os.rmdir(abs_path(store.root, str(path)))  # noqa: PTH106 – empty only
            except OSError:
                break  # not empty (or gone): leave it
            path = path.parent


def remove_reject_dir_if_empty(store: AlbumStore) -> None:
    with contextlib.suppress(OSError):
        os.rmdir(protocol.reject_dir(store.root))  # noqa: PTH106 – fails unless empty


# --- applying ------------------------------------------------------------------------------


def start(store: AlbumStore, photo_ids: Sequence[int], count_before: int, cfg: Config) -> str:
    """Plan a batch: every file (photo first, then its sidecars) as ``planned``, committed,
    and the protocol copy written – before anything is moved (02 §6 steps 1–3)."""
    batch_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    now = time.time()
    with contextlib.closing(store.connect()) as conn:
        rows = ids_query(
            conn,
            "SELECT id, rel_path, companions FROM photos WHERE present=1 AND id IN ({ids})",
            list(photo_ids),
        )
        rows.sort(key=lambda r: r["rel_path"].casefold())
        taken: set[str] = set()
        entries: list[tuple[Any, ...]] = []
        for row in rows:
            files = [row["rel_path"], *json.loads(row["companions"] or "[]")]
            targets = free_names(
                store.root,
                [f"{REJECT_DIR}/{f}" for f in files],
                PurePosixPath(row["rel_path"]).stem,
                taken,
                cfg.name_suffix_limit,
            )
            taken.update(t.casefold() for t in targets)
            entries += [
                (batch_id, now, src, dst, "planned", row["id"])
                for src, dst in zip(files, targets, strict=True)
            ]
        if not entries:
            raise ApplyError("Keine Bilder zum Verschieben.")
        with durable(conn):
            conn.execute(
                "INSERT INTO batches(batch_id, created_at, state, count_before)"
                " VALUES(?, ?, 'running', ?)",
                (batch_id, now, count_before),
            )
            conn.executemany(
                "INSERT INTO journal(batch_id, created_at, src, dst, state, photo_id)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                entries,
            )
        try:
            protocol.write(store.root, conn)
        except OSError as exc:
            with conn:
                conn.execute(
                    "UPDATE journal SET state='cancelled', error=? WHERE batch_id=?",
                    (MSG_CANCELLED, batch_id),
                )
                conn.execute("UPDATE batches SET state='empty' WHERE batch_id=?", (batch_id,))
            log.warning("cannot write the protocol copy: %s", exc)
            raise ApplyError(
                f"Der Ordner „{REJECT_DIR}“ kann nicht angelegt werden ({failure_text(exc)}) "
                "– es wurde nichts verschoben."
            ) from exc
    log.info("batch %s planned: %d photos, %d files", batch_id, len(rows), len(entries))
    return batch_id


def _groups(entries: Iterable[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    """Photo + its sidecars (consecutive in the journal) form one group."""
    groups: list[list[sqlite3.Row]] = []
    for e in entries:
        pid = e["photo_id"]
        if groups and pid is not None and groups[-1][0]["photo_id"] == pid:
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


class _Run:
    def __init__(
        self,
        store: AlbumStore,
        conn: sqlite3.Connection,
        batch_id: str,
        cfg: Config,
        report: Report,
        hook: Hook | None,
    ) -> None:
        self.store = store
        self.root = store.root
        self.conn = conn
        self.batch_id = batch_id
        self.cfg = cfg
        self.report = report
        self.hook = hook

    def mark(self, entry_id: int, state: str, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE journal SET state=?, error=? WHERE id=?", (state, error, entry_id)
            )

    def changed_since_scan(self, entry: sqlite3.Row, photos: dict[int, sqlite3.Row]) -> bool:
        row = photos.get(entry["photo_id"])
        if row is None:
            return False
        try:
            st = abs_path(self.root, entry["src"]).stat()
        except OSError:
            return False  # the rename reports it
        return st.st_size != row["size"] or st.st_mtime_ns != row["mtime_ns"]

    def free_targets(self, group: list[sqlite3.Row], taken: set[str]) -> list[str]:
        """Re-pick targets if one appeared since planning; journal first, then move."""
        targets = [e["dst"] for e in group]
        if not any(exists(self.root, t) for t in targets):
            return targets
        own = {t.casefold() for t in targets}
        targets = free_names(
            self.root,
            [f"{REJECT_DIR}/{e['src']}" for e in group],
            base_stem(PurePosixPath(group[0]["src"]).name),
            taken - own,
            self.cfg.name_suffix_limit,
        )
        with durable(self.conn):
            self.conn.executemany(
                "UPDATE journal SET dst=? WHERE id=?",
                [(t, e["id"]) for t, e in zip(targets, group, strict=True)],
            )
        taken.difference_update(own)
        taken.update(t.casefold() for t in targets)
        return targets

    def move_group(
        self, group: list[sqlite3.Row], photos: dict[int, sqlite3.Row], taken: set[str]
    ) -> None:
        head = group[0]
        if is_photo(head["src"]) and self.changed_since_scan(head, photos):
            self.mark(head["id"], "failed", MSG_CHANGED)
            self.report.fail(head["src"], MSG_CHANGED)
            for e in group[1:]:
                self.mark(e["id"], "cancelled", MSG_COMPANION)
            return
        try:
            targets = self.free_targets(group, taken)
        except FileExistsError as exc:
            for e in group:
                self.mark(e["id"], "failed", failure_text(exc))
                self.report.fail(e["src"], failure_text(exc))
            return
        photo_failed = False
        for e, target in zip(group, targets, strict=True):
            if photo_failed:
                self.mark(e["id"], "cancelled", MSG_COMPANION)
                continue
            src, dst = abs_path(self.root, e["src"]), abs_path(self.root, target)
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                rename(src, dst)
            except OSError as exc:
                message = failure_text(exc)
                log.info("cannot move %s: %s", e["src"], exc)
                self.mark(e["id"], "failed", message)
                self.report.fail(e["src"], message)
                photo_failed = is_photo(e["src"])
                continue
            if self.hook:
                self.hook("moved", e["src"])
            with self.conn:
                self.conn.execute(
                    "UPDATE journal SET state='moved', error=NULL WHERE id=?", (e["id"],)
                )
                if is_photo(e["src"]) and e["photo_id"] is not None:
                    self.conn.execute("UPDATE photos SET present=0 WHERE id=?", (e["photo_id"],))
            self.report.files += 1
            self.report.photos += is_photo(e["src"])


def run(
    store: AlbumStore,
    batch_id: str,
    cfg: Config,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    hook: Hook | None = None,
) -> Report:
    """Move all ``planned`` files of the batch (02 §6 steps 4–7). Failures never stop the rest."""
    report = Report("apply", batch_id)
    with contextlib.closing(store.connect()) as conn:
        with conn:
            conn.execute("UPDATE batches SET state='running' WHERE batch_id=?", (batch_id,))
        planned = conn.execute(
            "SELECT * FROM journal WHERE batch_id=? AND state='planned' ORDER BY id", (batch_id,)
        ).fetchall()
        photo_ids = sorted({e["photo_id"] for e in planned if e["photo_id"] is not None})
        photos = {
            r["id"]: r
            for r in ids_query(
                conn, "SELECT id, size, mtime_ns FROM photos WHERE id IN ({ids})", photo_ids
            )
        }
        taken = {e["dst"].casefold() for e in planned}
        work = _Run(store, conn, batch_id, cfg, report, hook)
        total, done = len(planned), 0
        for group in _groups(planned):
            if cancel is not None and cancel.is_set():
                break
            if on_progress:
                on_progress(Progress("apply", done, total, group[0]["src"]))
            work.move_group(group, photos, taken)
            done += len(group)
        with conn:
            report.cancelled = conn.execute(
                "UPDATE journal SET state='cancelled', error=?"
                " WHERE batch_id=? AND state='planned'",
                (MSG_CANCELLED, batch_id),
            ).rowcount
            moved = conn.execute(
                "SELECT COUNT(*) FROM journal WHERE batch_id=? AND state='moved'", (batch_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE batches SET state=? WHERE batch_id=?",
                ("applied" if moved else "empty", batch_id),
            )
            refresh_applied(conn, hold=True if moved else None)
        protocol.write(store.root, conn)
        if not moved:  # leave no empty folders behind
            remove_empty_dirs(store, {PurePosixPath(e["dst"]).parent for e in planned})
            remove_reject_dir_if_empty(store)
        if on_progress:
            on_progress(Progress("apply", total, total))
    log.info(
        "batch %s: %d photos / %d files moved, %d failed, %d cancelled",
        batch_id,
        report.photos,
        report.files,
        report.failed_count,
        report.cancelled,
    )
    return report


def retry(
    store: AlbumStore,
    batch_id: str,
    cfg: Config,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> Report:
    """ "Erneut versuchen": failed and interrupted entries of the batch once more."""
    with contextlib.closing(store.connect()) as conn, durable(conn):
        conn.execute("UPDATE batches SET state='running' WHERE batch_id=?", (batch_id,))
        conn.execute(
            "UPDATE journal SET state='planned', error=NULL"
            " WHERE batch_id=? AND state IN ('failed', 'cancelled')",
            (batch_id,),
        )
    return run(store, batch_id, cfg, on_progress, cancel)
