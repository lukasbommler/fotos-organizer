"""Protocol copy ``_Aussortiert\\_FotoDuenner_Protokoll.json`` (T4).

The journal lives in the album database under %LOCALAPPDATA%. This copy next to the moved files
lets "Rückgängig" work even when that cache is gone (other PC, cache deleted, album renamed).
Paths are relative to the album, so a moved or renamed album folder keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path, PurePosixPath
from typing import Any

from fotoduenner import __version__
from fotoduenner.paths import long_path
from fotoduenner.scanner.discover import REJECT_DIR

log = logging.getLogger(__name__)

PROTOCOL_NAME = "_FotoDuenner_Protokoll.json"
FORMAT = 1
ACTIVE = ("running", "applied", "undoing")  # batches whose files may still be in _Aussortiert
ENTRY_STATES = frozenset({"planned", "moved", "reverted", "failed", "cancelled", "missing"})
NOTE = (
    "Protokoll von FotoDünner: welche Dateien in diesen Ordner verschoben wurden. "
    "Damit lässt sich das Verschieben rückgängig machen. Bitte nicht bearbeiten."
)


def reject_dir(root: Path) -> Path:
    return long_path(root) / REJECT_DIR


def protocol_path(root: Path) -> Path:
    return reject_dir(root) / PROTOCOL_NAME


def safe_rel(rel: str, in_reject_dir: bool) -> bool:
    """Relative POSIX path that stays inside the album; inside or outside ``_Aussortiert``.

    Guards the undo against a tampered protocol (``..``, drive letters, absolute paths).
    """
    if not rel or "\\" in rel or ":" in rel:
        return False
    path = PurePosixPath(rel)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return False
    inside = path.parts[0].casefold() == REJECT_DIR.casefold()
    if in_reject_dir:
        return inside and len(path.parts) > 1 and path.name != PROTOCOL_NAME
    return not inside


def write(root: Path, conn: sqlite3.Connection) -> None:
    """Rewrite the copy from the journal; remove it once no batch is active any more.

    The copy is a file of this program, the only one it writes into the album (CLAUDE.md
    rule 2), so replacing and finally removing it is allowed (02 §6).
    """
    batches = conn.execute(
        "SELECT * FROM batches WHERE state IN (?, ?, ?) ORDER BY created_at",
        ACTIVE,
    ).fetchall()
    path = protocol_path(root)
    if not batches:
        if path.is_file():
            path.unlink()  # own protocol copy, nothing left to undo
        return
    data: dict[str, Any] = {
        "program": "FotoDünner",
        "version": __version__,
        "format": FORMAT,
        "note": NOTE,
        "written_at": time.time(),
        "batches": [
            {
                "batch_id": b["batch_id"],
                "created_at": b["created_at"],
                "created_text": time.strftime("%d.%m.%Y %H:%M", time.localtime(b["created_at"])),
                "state": b["state"],
                "count_before": b["count_before"],
                "entries": [
                    {
                        "src": e["src"],
                        "dst": e["dst"],
                        "state": e["state"],
                        "restored": e["restored"],
                    }
                    for e in conn.execute(
                        "SELECT src, dst, state, restored FROM journal"
                        " WHERE batch_id=? ORDER BY id",
                        (b["batch_id"],),
                    )
                ],
            }
            for b in batches
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(PROTOCOL_NAME + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
        handle.flush()
        os.fsync(handle.fileno())  # survives a power failure
    tmp.replace(path)  # own file: atomic replace of the previous copy


def read(root: Path) -> list[dict[str, Any]]:
    """Batches of the copy with valid entries only; [] when missing or unreadable."""
    path = protocol_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("protocol copy unreadable: %s", exc)
        return []
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        log.warning("protocol copy has an unknown format")
        return []
    batches = []
    for batch in data.get("batches") or []:
        if not isinstance(batch, dict) or not isinstance(batch.get("batch_id"), str):
            continue
        entries = []
        for entry in batch.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            src, dst = entry.get("src"), entry.get("dst")
            state = entry.get("state")
            restored = entry.get("restored")
            if not (isinstance(src, str) and isinstance(dst, str) and state in ENTRY_STATES):
                continue
            if not safe_rel(src, False) or not safe_rel(dst, True):
                log.warning("protocol entry outside the album ignored: %r -> %r", src, dst)
                continue
            if restored is not None and not (
                isinstance(restored, str) and safe_rel(restored, False)
            ):
                restored = None
            entries.append({"src": src, "dst": dst, "state": state, "restored": restored})
        created = batch.get("created_at")
        count = batch.get("count_before")
        batches.append(
            {
                "batch_id": batch["batch_id"],
                "created_at": float(created) if isinstance(created, int | float) else time.time(),
                "state": batch.get("state") if batch.get("state") in ACTIVE else "applied",
                "count_before": int(count) if isinstance(count, int) else None,
                "entries": entries,
            }
        )
    return batches
