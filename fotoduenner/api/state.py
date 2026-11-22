"""Application state behind the API: opened albums, the running analysis, window hooks."""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from fotoduenner.config import DEFAULT, Config
from fotoduenner.paths import albums_dir
from fotoduenner.pipeline.runner import Progress, RunSummary, analyze_album, default_workers
from fotoduenner.scanner.discover import scan_album
from fotoduenner.store.db import AlbumStore, connect
from fotoduenner.winplatform import drive_kind

log = logging.getLogger(__name__)

RunState = Literal["idle", "running", "done", "cancelled", "failed"]
RECENT_LIMIT = 8
JOIN_TIMEOUT_S = 60.0


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message  # German, shown to the user


@dataclass
class AlbumSession:
    store: AlbumStore
    cfg: Config
    state: RunState = "idle"
    progress: Progress | None = None
    finished_ids: list[int] = field(default_factory=list)
    version: int = 0  # bumped on every change, drives the SSE stream
    error: str | None = None
    summary: RunSummary | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def album_id(self) -> str:
        return self.store.album_id

    def snapshot(self, sent_ids: int) -> tuple[int, dict[str, Any], int]:
        with self.lock:
            new_ids = self.finished_ids[sent_ids:]
            payload: dict[str, Any] = {
                "state": self.state,
                "progress": asdict(self.progress) if self.progress else None,
                "finished": new_ids,
                "error": self.error,
            }
            return self.version, payload, sent_ids + len(new_ids)


class AppState:
    def __init__(self, cfg: Config = DEFAULT, data_root: Path | None = None) -> None:
        self.cfg = cfg
        self.data_root = data_root
        self.sessions: dict[str, AlbumSession] = {}
        self._lock = threading.Lock()
        # Hooks set by the launcher; None when running without a window (tests, --no-window).
        self.folder_picker: Callable[[], str | None] | None = None
        self.on_focus: Callable[[], None] | None = None
        self.on_shutdown: Callable[[], None] | None = None

    # --- albums -------------------------------------------------------------------------

    def open_album(self, path: str, recursive: bool) -> dict[str, Any]:
        root = Path(path)
        if not path or not root.is_dir():
            raise ApiError(404, "Ordner nicht gefunden.")
        cfg = replace(self.cfg, include_subdirs=recursive)
        scan = scan_album(root, cfg)
        store = AlbumStore(root, self.data_root)
        report = store.sync(scan, cfg)
        with contextlib.closing(store.connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES('include_subdirs', ?)",
                ("1" if recursive else "0",),
            )
        with self._lock:
            old = self.sessions.get(store.album_id)
            if old and old.state == "running":
                raise ApiError(409, "Für dieses Album läuft bereits eine Analyse.")
            session = AlbumSession(store=store, cfg=cfg)
            self.sessions[store.album_id] = session

        workers = max(1, default_workers(cfg))
        estimate = 0.0
        if report.pending:
            estimate = (
                report.pending * cfg.estimate_ms_per_photo / 1000 / workers + cfg.estimate_startup_s
            )
        return {
            "album_id": store.album_id,
            "name": store.root.name or str(store.root),
            "path": str(store.root),
            "recursive": recursive,
            "photos": report.total,
            "ignored": scan.ignored_files,
            "total_bytes": sum(p.size for p in scan.photos),
            "offline": scan.offline_count,
            "drive": drive_kind(store.root),
            "few": report.total < cfg.few_photos_warning,
            "cached": report.total - report.pending,
            "pending": report.pending,
            "estimate_seconds": round(estimate),
            "unreadable_dirs": scan.unreadable_dirs,
        }

    def session(self, album_id: str) -> AlbumSession:
        session = self.sessions.get(album_id)
        if session is None:
            raise ApiError(404, "Album ist nicht geöffnet.")
        return session

    def start_analysis(self, album_id: str, skip_offline: bool = False) -> None:
        session = self.session(album_id)
        with self._lock:
            if any(s.state == "running" for s in self.sessions.values()):
                raise ApiError(409, "Es läuft bereits eine Analyse.")
            session.cancel = threading.Event()
            session.state = "running"
            session.error = None
            session.finished_ids = []
            session.progress = None
            session.version += 1
            session.thread = threading.Thread(
                target=self._analyze,
                args=(session, skip_offline),
                name=f"analyze-{album_id[:8]}",
                daemon=True,
            )
            session.thread.start()

    def _analyze(self, session: AlbumSession, skip_offline: bool) -> None:
        def on_progress(progress: Progress) -> None:
            with session.lock:
                session.progress = progress
                if progress.photo_id is not None:
                    session.finished_ids.append(progress.photo_id)
                session.version += 1

        try:
            summary = analyze_album(
                session.store.root,
                session.cfg,
                cancel=session.cancel,
                on_progress=on_progress,
                data_root=self.data_root,
                skip_offline=skip_offline,
            )
            with session.lock:
                session.summary = summary
                session.state = "cancelled" if summary.cancelled else "done"
                session.version += 1
        except Exception as exc:  # the UI must learn about it instead of waiting forever
            log.exception("analysis failed")
            with session.lock:
                session.state = "failed"
                session.error = f"Die Analyse ist fehlgeschlagen: {exc}"
                session.version += 1

    def cancel_analysis(self, album_id: str) -> None:
        self.session(album_id).cancel.set()

    def busy(self) -> bool:
        return any(s.state == "running" for s in self.sessions.values())

    def shutdown(self) -> None:
        """Cancel running work and wait for it (in-flight photos finish, the rest stays pending)."""
        for session in list(self.sessions.values()):
            session.cancel.set()
        for session in list(self.sessions.values()):
            if session.thread is not None:
                session.thread.join(JOIN_TIMEOUT_S)

    # --- read models ----------------------------------------------------------------------

    def view(self, album_id: str) -> dict[str, Any]:
        session = self.session(album_id)
        with contextlib.closing(session.store.connect()) as conn:
            rows = conn.execute(
                "SELECT id, rel_path, width, height, status, rating, taken_at, mtime_ns, error"
                " FROM photos WHERE present=1"
                " ORDER BY taken_at IS NULL, taken_at, rel_path COLLATE NOCASE"
            ).fetchall()
        photos = [
            {
                "id": r["id"],
                "n": index + 1,  # film-strip number in capture order
                "name": Path(r["rel_path"]).name,
                "rel_path": r["rel_path"],
                "w": r["width"],
                "h": r["height"],
                "status": r["status"],
                "rating": r["rating"],
                "taken_at": r["taken_at"],
                "v": r["mtime_ns"],
                "error": r["error"],
            }
            for index, r in enumerate(rows)
        ]
        return {
            "album_id": album_id,
            "name": session.store.root.name,
            "path": str(session.store.root),
            "state": session.state,
            "photos": photos,
        }

    def photo_row(self, album_id: str, photo_id: int) -> dict[str, Any]:
        session = self.session(album_id)
        with contextlib.closing(session.store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM photos WHERE id=? AND present=1", (photo_id,)
            ).fetchone()
        if row is None:
            raise ApiError(404, "Bild nicht gefunden.")
        return dict(row)

    def recent(self) -> list[dict[str, Any]]:
        root = albums_dir(self.data_root)
        try:
            index = json.loads((root / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = []
        for album_id, entry in sorted(
            index.items(), key=lambda kv: kv[1].get("last_opened", 0), reverse=True
        ):
            path = Path(entry.get("path", ""))
            db = root / album_id / "album.db"
            if not path.is_dir() or not db.is_file():
                continue
            try:
                with contextlib.closing(connect(db, read_only=True)) as conn:
                    counts = {
                        r[0]: r[1]
                        for r in conn.execute(
                            "SELECT status, COUNT(*) FROM photos WHERE present=1 GROUP BY status"
                        )
                    }
                    setting = conn.execute(
                        "SELECT value FROM settings WHERE key='include_subdirs'"
                    ).fetchone()
            except Exception:
                continue
            items.append(
                {
                    "album_id": album_id,
                    "name": path.name,
                    "path": str(path),
                    "last_opened": entry.get("last_opened"),
                    "photos": sum(counts.values()),
                    "done": counts.get("done", 0) + counts.get("error", 0),
                    "recursive": setting is None or setting[0] == "1",
                }
            )
            if len(items) >= RECENT_LIMIT:
                break
        return items
