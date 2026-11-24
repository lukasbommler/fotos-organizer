"""Rückgängig and crash repair (02 §6).

``undo`` moves every ``moved`` file of a batch back, newest first, again only with ``os.rename``.
``recover`` runs whenever an album is opened: it imports batches known only from the protocol
copy (T4) and settles entries a crash left open, by looking at where each file actually is.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from pathlib import PurePosixPath

from fotoduenner.apply import protocol
from fotoduenner.apply.mover import (
    Hook,
    Progress,
    ProgressFn,
    Report,
    abs_path,
    base_stem,
    exists,
    failure_text,
    free_names,
    is_photo,
    refresh_applied,
    rename,
)
from fotoduenner.config import Config
from fotoduenner.store.db import AlbumStore

log = logging.getLogger(__name__)

MSG_MISSING = "Nicht mehr in „_Aussortiert“ – wurde die Datei dort gelöscht oder umbenannt?"
MSG_GONE = "Datei weder am alten Platz noch in „_Aussortiert“ gefunden."
MSG_INTERRUPTED = "Nicht verschoben – der Vorgang wurde unterbrochen."


class UndoError(Exception):
    """Nothing to undo; German message."""


def last_batch(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT batch_id FROM batches WHERE state IN (?, ?, ?) ORDER BY created_at DESC LIMIT 1",
        protocol.ACTIVE,
    ).fetchone()
    return None if row is None else row["batch_id"]


def _remove_empty_dirs(store: AlbumStore, dirs: set[PurePosixPath]) -> None:
    """Remove folders in _Aussortiert that became empty – os.rmdir only, never anything else."""
    for rel in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        path = rel
        while len(path.parts) > 1:  # stop below _Aussortiert itself
            try:
                os.rmdir(abs_path(store.root, str(path)))  # noqa: PTH106 – empty only
            except OSError:
                break  # not empty (or gone): leave it
            path = path.parent


def _remove_reject_dir_if_empty(store: AlbumStore) -> None:
    with contextlib.suppress(OSError):
        os.rmdir(protocol.reject_dir(store.root))  # noqa: PTH106 – fails unless empty


def undo(
    store: AlbumStore,
    batch_id: str,
    cfg: Config,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    hook: Hook | None = None,
) -> Report:
    """Bring a batch back. An occupied old name gets a suffix and a note, never a replace."""
    report = Report("undo", batch_id)
    with contextlib.closing(store.connect()) as conn:
        batch = conn.execute("SELECT state FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if batch is None or batch["state"] not in protocol.ACTIVE:
            raise UndoError("Dieser Vorgang ist bereits rückgängig gemacht.")
        with conn:
            conn.execute("UPDATE batches SET state='undoing' WHERE batch_id=?", (batch_id,))
        entries = conn.execute(
            "SELECT * FROM journal WHERE batch_id=? AND state='moved' ORDER BY id DESC",
            (batch_id,),
        ).fetchall()
        taken: set[str] = set()
        dirs: set[PurePosixPath] = set()
        total = len(entries)
        for done, e in enumerate(entries):
            if cancel is not None and cancel.is_set():
                break
            if on_progress:
                on_progress(Progress("undo", done, total, e["src"]))
            if not exists(store.root, e["dst"]):
                with conn:
                    conn.execute(
                        "UPDATE journal SET state='missing', error=? WHERE id=?",
                        (MSG_MISSING, e["id"]),
                    )
                report.fail(e["src"], MSG_MISSING)
                continue
            target = e["src"]
            if exists(store.root, target) or target.casefold() in taken:
                try:
                    target = free_names(
                        store.root,
                        [e["src"]],
                        base_stem(PurePosixPath(e["src"]).name),
                        taken,
                        cfg.name_suffix_limit,
                    )[0]
                except FileExistsError as exc:
                    report.fail(e["src"], failure_text(exc))
                    continue
                with conn:  # journal first: a crash right after the rename stays repairable
                    conn.execute("UPDATE journal SET restored=? WHERE id=?", (target, e["id"]))
            taken.add(target.casefold())
            try:
                abs_path(store.root, target).parent.mkdir(parents=True, exist_ok=True)
                rename(abs_path(store.root, e["dst"]), abs_path(store.root, target))
            except OSError as exc:
                message = failure_text(exc)
                log.info("cannot move back %s: %s", e["dst"], exc)
                with conn:
                    conn.execute("UPDATE journal SET error=? WHERE id=?", (message, e["id"]))
                report.fail(e["src"], message)
                continue
            if hook:
                hook("reverted", e["src"])
            with conn:
                conn.execute(
                    "UPDATE journal SET state='reverted', error=NULL WHERE id=?", (e["id"],)
                )
                if is_photo(e["src"]) and e["photo_id"] is not None and target == e["src"]:
                    conn.execute("UPDATE photos SET present=1 WHERE id=?", (e["photo_id"],))
            if target != e["src"]:
                new_name = PurePosixPath(target).name
                report.notes.append(
                    f"{e['src']} heißt jetzt „{new_name}“ – der alte Name war belegt."
                )
            dirs.add(PurePosixPath(e["dst"]).parent)
            report.files += 1
            report.photos += is_photo(e["src"])
        _remove_empty_dirs(store, dirs)
        with conn:
            left = conn.execute(
                "SELECT COUNT(*) FROM journal WHERE batch_id=? AND state='moved'", (batch_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE batches SET state=? WHERE batch_id=?",
                ("applied" if left else "reverted", batch_id),
            )
            refresh_applied(conn)
        protocol.write(store.root, conn)
        if not conn.execute(
            "SELECT 1 FROM batches WHERE state IN (?, ?, ?)", protocol.ACTIVE
        ).fetchone():
            _remove_reject_dir_if_empty(store)
        if on_progress:
            on_progress(Progress("undo", total, total))
    log.info(
        "batch %s undone: %d photos / %d files back, %d failed",
        batch_id,
        report.photos,
        report.files,
        report.failed_count,
    )
    return report


# --- crash repair and protocol import --------------------------------------------------------


def _import(store: AlbumStore, conn: sqlite3.Connection) -> list[str]:
    """Batches that only the protocol copy knows (cache deleted, other PC, renamed album)."""
    known = {r["batch_id"] for r in conn.execute("SELECT batch_id FROM batches")}
    imported = []
    for batch in protocol.read(store.root):
        if batch["batch_id"] in known or not batch["entries"]:
            continue
        with conn:
            conn.execute(
                "INSERT INTO batches(batch_id, created_at, state, count_before) VALUES(?, ?, ?, ?)",
                (batch["batch_id"], batch["created_at"], batch["state"], batch["count_before"]),
            )
            for e in batch["entries"]:
                row = conn.execute("SELECT id FROM photos WHERE rel_path=?", (e["src"],)).fetchone()
                conn.execute(
                    "INSERT INTO journal(batch_id, created_at, src, dst, state, photo_id, restored)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        batch["batch_id"],
                        batch["created_at"],
                        e["src"],
                        e["dst"],
                        e["state"],
                        row["id"] if row and is_photo(e["src"]) else None,
                        e["restored"],
                    ),
                )
        imported.append(batch["batch_id"])
        log.info("batch %s imported from the protocol copy", batch["batch_id"])
    return imported


def _settle(store: AlbumStore, conn: sqlite3.Connection, batch_id: str) -> None:
    """Set every entry of the batch to where its file really is."""
    entries = conn.execute(
        "SELECT * FROM journal WHERE batch_id=? ORDER BY id", (batch_id,)
    ).fetchall()
    with conn:
        for e in entries:
            at_src = exists(store.root, e["restored"] or e["src"])
            at_dst = exists(store.root, e["dst"])
            state, error = e["state"], e["error"]
            if state == "planned":
                if at_dst and not at_src:
                    state, error = "moved", None
                elif at_src:
                    state, error = "cancelled", MSG_INTERRUPTED
                else:
                    state, error = "failed", MSG_GONE
            elif state == "moved" and not at_dst:
                state, error = ("reverted", None) if at_src else ("missing", MSG_MISSING)
            elif (state == "reverted" and at_dst and not at_src) or (state == "missing" and at_dst):
                state, error = "moved", None  # the file is in _Aussortiert after all
            if (state, error) == (e["state"], e["error"]):
                continue
            conn.execute("UPDATE journal SET state=?, error=? WHERE id=?", (state, error, e["id"]))
            if e["photo_id"] is not None and is_photo(e["src"]) and state in ("moved", "reverted"):
                present = int(state == "reverted" and not e["restored"])
                conn.execute("UPDATE photos SET present=? WHERE id=?", (present, e["photo_id"]))
        counts = {
            r["state"]: r["n"]
            for r in conn.execute(
                "SELECT state, COUNT(*) AS n FROM journal WHERE batch_id=? GROUP BY state",
                (batch_id,),
            )
        }
        new_state = (
            "applied" if counts.get("moved") else "reverted" if counts.get("reverted") else "empty"
        )
        conn.execute("UPDATE batches SET state=? WHERE batch_id=?", (new_state, batch_id))
        refresh_applied(conn, hold=True if new_state == "applied" else None)
    log.info("batch %s settled: %s -> %s", batch_id, counts, new_state)


def recover(store: AlbumStore) -> list[str]:
    """Repair after a crash and import from the protocol copy; returns the batches touched."""
    with contextlib.closing(store.connect()) as conn:
        imported = _import(store, conn)
        open_batches = {
            r["batch_id"]
            for r in conn.execute(
                "SELECT batch_id FROM batches WHERE state IN ('running', 'undoing')"
                " UNION SELECT DISTINCT batch_id FROM journal WHERE state='planned'"
            )
        }
        touched = sorted(open_batches | set(imported))
        for batch_id in touched:
            _settle(store, conn, batch_id)
        if touched:
            try:
                protocol.write(store.root, conn)
            except OSError as exc:
                log.warning("cannot update the protocol copy: %s", exc)
            if not conn.execute(
                "SELECT 1 FROM batches WHERE state IN (?, ?, ?)", protocol.ACTIVE
            ).fetchone():
                _remove_reject_dir_if_empty(store)
    return touched
