"""Application state behind the API: opened albums, the running analysis, window hooks."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from fotoduenner.api.review import Review
from fotoduenner.apply import mover, protocol
from fotoduenner.apply.undo import UndoError, last_batch, recover, undo
from fotoduenner.config import DEFAULT, Config
from fotoduenner.paths import albums_dir, long_path
from fotoduenner.pipeline.quality import Quality, exposure_score
from fotoduenner.pipeline.runner import (
    Progress,
    RunSummary,
    analyze_album,
    default_workers,
    second_pass_jobs,
)
from fotoduenner.pipeline.thumbs import full_jpeg
from fotoduenner.pipeline.zeroshot import active_labels
from fotoduenner.scanner.discover import REJECT_DIR, scan_album
from fotoduenner.store import calibration
from fotoduenner.store.db import AlbumStore, connect
from fotoduenner.winplatform import drive_kind

log = logging.getLogger(__name__)

RunState = Literal["idle", "running", "done", "cancelled", "failed"]
JobKind = Literal["apply", "retry", "undo"]
REASON_KEYS = ("duplicate", "series", "motif", "quality", "manual")
MSG_MOVING = "Das Verschieben läuft noch."
MSG_ALBUM_MOVING = "Für dieses Album werden gerade Dateien verschoben."
RECENT_LIMIT = 8
JOIN_TIMEOUT_S = 60.0


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message  # German, shown to the user


@dataclass
class Job:
    """Moving into _Aussortiert or back, in a background thread (04 §3: progress)."""

    kind: JobKind
    batch_id: str = ""
    state: Literal["running", "done", "failed"] = "running"
    done: int = 0
    total: int = 0
    current: str | None = None
    report: dict[str, Any] | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "batch_id": self.batch_id,
            "state": self.state,
            "done": self.done,
            "total": self.total,
            "current": self.current,
            "report": self.report,
            "error": self.error,
        }


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
    review: Review | None = None
    job: Job | None = None

    @property
    def moving(self) -> bool:
        return self.job is not None and self.job.state == "running"

    def __post_init__(self) -> None:
        if self.review is None:
            self.review = Review(self.store, self.cfg)

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
        self.open_folder: Callable[[Path], None] | None = None  # Explorer (os.startfile)

    # --- albums -------------------------------------------------------------------------

    def open_album(self, path: str, recursive: bool) -> dict[str, Any]:
        root = Path(path)
        if not path or not root.is_dir():
            raise ApiError(404, "Ordner nicht gefunden.")
        cfg = replace(self.cfg, include_subdirs=recursive)
        scan = scan_album(root, cfg)
        store = AlbumStore(root, self.data_root)
        old = self.sessions.get(store.album_id)
        if old and old.moving:
            raise ApiError(409, MSG_ALBUM_MOVING)
        recover(store)  # crash repair, undo from the protocol copy (02 §6, T4)
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
            if old and old.moving:
                raise ApiError(409, MSG_ALBUM_MOVING)
            session = AlbumSession(store=store, cfg=cfg)
            self.sessions[store.album_id] = session

        workers = max(1, default_workers(cfg))
        # second pass (T6) still open, e.g. after cancelling during it
        faces_pending = len(second_pass_jobs(store, cfg)) if cfg.face_second_pass else 0
        estimate = 0.0
        if report.pending or faces_pending:
            work_ms = (
                report.pending * cfg.estimate_ms_per_photo
                + faces_pending * cfg.estimate_ms_second_pass
            )
            estimate = work_ms / 1000 / workers + cfg.estimate_startup_s
        with contextlib.closing(store.connect()) as conn:
            applied = mover.status(conn)
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
            "faces_pending": faces_pending,
            "estimate_seconds": round(estimate),
            "unreadable_dirs": scan.unreadable_dirs,
            "apply": applied,
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
            if session.moving:
                raise ApiError(409, MSG_MOVING)
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
            if session.review is not None:
                session.review.invalidate()  # new features: groups and proposals are rebuilt
            with session.lock:
                session.summary = summary
                session.state = "cancelled" if summary.cancelled else "done"
                session.version += 1
        except Exception as exc:  # the UI must learn about it instead of waiting forever
            log.exception("analysis failed")
            if session.review is not None:
                session.review.invalidate()  # part of the photos may have new features
            with session.lock:
                session.state = "failed"
                session.error = f"Die Analyse ist fehlgeschlagen: {exc}"
                session.version += 1

    def cancel_analysis(self, album_id: str) -> None:
        self.session(album_id).cancel.set()

    def busy(self) -> bool:
        return any(s.state == "running" or s.moving for s in self.sessions.values())

    def moving(self) -> bool:
        return any(s.moving for s in self.sessions.values())

    def shutdown(self) -> None:
        """Cancel running work and wait for it (in-flight photos finish, the rest stays pending).

        A move in progress stops after the current photo; everything moved so far is in the
        journal and can be undone or finished later.
        """
        for session in list(self.sessions.values()):
            session.cancel.set()
            if session.job is not None:
                session.job.cancel.set()
        for session in list(self.sessions.values()):
            if session.thread is not None:
                session.thread.join(JOIN_TIMEOUT_S)
            if session.job is not None and session.job.thread is not None:
                session.job.thread.join(JOIN_TIMEOUT_S)

    # --- read models ----------------------------------------------------------------------

    def view(self, album_id: str) -> dict[str, Any]:
        session = self.session(album_id)
        run_state = session.state  # once: "done" must never come without its review
        with contextlib.closing(session.store.connect()) as conn:
            rows = conn.execute(
                "SELECT id, rel_path, width, height, size, status, rating, taken_at, mtime_ns,"
                " error"
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
                "size": r["size"],
                "status": r["status"],
                "rating": r["rating"],
                "taken_at": r["taken_at"],
                "v": r["mtime_ns"],
                "error": r["error"],
            }
            for index, r in enumerate(rows)
        ]
        review = None
        if run_state != "running" and any(r["status"] == "done" for r in rows):
            review = self._review(session).full()
        with contextlib.closing(session.store.connect()) as conn:
            applied = mover.status(conn)
        return {
            "album_id": album_id,
            "name": session.store.root.name,
            "path": str(session.store.root),
            "state": run_state,
            "photos": photos,
            "review": review,
            "apply": applied,
            "job": session.job.to_json() if session.job else None,
        }

    # --- review: proposals, settings, manual decisions (04 §5) ---------------------------

    @staticmethod
    def _review(session: AlbumSession) -> Review:
        if session.state == "running":
            raise ApiError(409, "Die Analyse läuft noch.")
        if session.moving:
            raise ApiError(409, MSG_MOVING)
        assert session.review is not None
        return session.review

    def update_settings(self, album_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return self._review(self.session(album_id)).update_settings(changes)

    def set_decision(
        self, album_id: str, photo_ids: list[int], manual: str | None
    ) -> dict[str, Any]:
        try:
            return self._review(self.session(album_id)).set_manual(photo_ids, manual)
        except KeyError:
            raise ApiError(404, "Bild nicht gefunden oder noch nicht analysiert.") from None

    def set_group_done(self, album_id: str, sig: str, done: bool) -> None:
        self._review(self.session(album_id)).set_group_done(sig, done)

    def add_seen(self, album_id: str, items: list[tuple[int, int, bool]]) -> None:
        session = self.session(album_id)
        if not session.cfg.calibration_records or session.state == "running" or session.moving:
            return
        self._review(session).add_seen(items)

    def rethin(self, album_id: str) -> dict[str, Any]:
        return self._review(self.session(album_id)).rethin()

    # --- Übernehmen und Rückgängig (02 §6, 04 §3) -----------------------------------------

    @staticmethod
    def _blockers(root: Path) -> list[str]:
        target = protocol.reject_dir(root)
        problems = []
        if os.path.lexists(target):
            if target.is_symlink() or target.is_junction():
                problems.append(
                    f"„{REJECT_DIR}“ im Album ist eine Verknüpfung auf einen anderen Ordner. "
                    "Bitte umbenennen – FotoDünner verschiebt nur in einen echten Unterordner."
                )
            elif not target.is_dir():
                problems.append(
                    f"Im Album liegt eine Datei namens „{REJECT_DIR}“. Bitte umbenennen."
                )
        if not os.access(long_path(root), os.W_OK):
            problems.append("Das Album ist schreibgeschützt – es kann nichts verschoben werden.")
        return problems

    def apply_preview(self, album_id: str) -> dict[str, Any]:
        """Summary before moving (04 §3): counts by reason, size, problems."""
        session = self.session(album_id)
        ids, stats = self._review(session).rejected()
        store = session.store
        with contextlib.closing(store.connect()) as conn:
            rows = mover.ids_query(
                conn,
                "SELECT id, rel_path, size, mtime_ns, companions FROM photos WHERE id IN ({ids})",
                ids,
            )
            count = conn.execute("SELECT COUNT(*) FROM photos WHERE present=1").fetchone()[0]
        files = size = changed = 0
        for r in rows:
            companions = json.loads(r["companions"] or "[]")
            files += 1 + len(companions)
            size += r["size"]
            for rel in companions:
                with contextlib.suppress(OSError):
                    size += store.abs_path(rel).stat().st_size
            try:
                st = store.abs_path(r["rel_path"]).stat()
                changed += st.st_size != r["size"] or st.st_mtime_ns != r["mtime_ns"]
            except OSError:
                changed += 1
        warnings = []
        if changed:
            warnings.append(
                f"{changed} Bilder wurden seit der Analyse verändert oder fehlen – "
                "sie werden nicht verschoben."
            )
        return {
            "photo_ids": ids,
            "photos": len(ids),
            "files": files,
            "bytes": size,
            "by_reason": {k: stats[f"reject_{k}"] for k in REASON_KEYS},
            "blocking": self._blockers(store.root),
            "warnings": warnings,
            "target": str(store.root / REJECT_DIR),
            "count_before": count,
            "recorded": session.cfg.calibration_records and not stats["hold"],
        }

    def _claim(self, session: AlbumSession, kind: JobKind) -> Job:
        with self._lock:
            if session.state == "running":
                raise ApiError(409, "Die Analyse läuft noch.")
            if session.moving:
                raise ApiError(409, MSG_MOVING)
            job = Job(kind)
            session.job = job
            session.version += 1
            return job

    def _launch(
        self,
        session: AlbumSession,
        job: Job,
        work: Callable[[Job, mover.ProgressFn], mover.Report],
    ) -> dict[str, Any]:
        def progress(p: mover.Progress) -> None:
            with session.lock:
                job.done, job.total, job.current = p.done, p.total, p.current
                session.version += 1

        def target() -> None:
            try:
                report = work(job, progress)
                if job.kind == "undo":  # photos are back: same as opening the album again
                    session.store.sync(scan_album(session.store.root, session.cfg), session.cfg)
                with session.lock:
                    job.report = report.to_json()
                    job.state = "done"
            except Exception as exc:  # the dialog must learn about it instead of waiting
                log.exception("%s failed", job.kind)
                with session.lock:
                    job.state = "failed"
                    job.error = str(exc) if isinstance(exc, UndoError) else f"Fehler: {exc}"
            finally:
                if session.review is not None:
                    session.review.invalidate()
                with session.lock:
                    session.version += 1

        job.thread = threading.Thread(target=target, name=f"{job.kind}-{session.album_id[:8]}")
        job.thread.start()
        return job.to_json()

    def start_apply(self, album_id: str, photo_ids: list[int]) -> dict[str, Any]:
        session = self.session(album_id)
        if not photo_ids:
            raise ApiError(422, "Keine Bilder zum Verschieben.")
        preview = self.apply_preview(album_id)
        if preview["blocking"]:
            raise ApiError(409, preview["blocking"][0])
        if sorted(preview["photo_ids"]) != sorted(photo_ids):
            raise ApiError(
                409, "Die Auswahl hat sich inzwischen geändert – bitte „Übernehmen …“ neu öffnen."
            )
        job = self._claim(session, "apply")
        try:
            job.batch_id = mover.start(
                session.store, photo_ids, preview["count_before"], session.cfg
            )
        except (mover.ApplyError, FileExistsError) as exc:
            session.job = None
            message = str(exc) if isinstance(exc, mover.ApplyError) else mover.failure_text(exc)
            raise ApiError(409, message) from None
        store, cfg, batch = session.store, session.cfg, job.batch_id

        def work(j: Job, p: mover.ProgressFn) -> mover.Report:
            self._record(session, batch, photo_ids)  # before moving: photos are still present
            report = mover.run(store, batch, cfg, on_progress=p, cancel=j.cancel)
            if not report.files:  # nothing moved: the user will apply again
                self._drop_record(batch)
            return report

        return self._launch(session, job, work)

    def _drop_record(self, batch_id: str) -> None:
        """The decision does not stand (undone or nothing moved): not used for calibration."""
        try:
            calibration.mark_reverted(batch_id, self.data_root)
        except Exception:
            log.exception("calibration record not updated")

    def _record(self, session: AlbumSession, batch_id: str, rejected: list[int]) -> None:
        """Calibration record (D20). It must never keep the user from moving photos."""
        if not session.cfg.calibration_records or session.review is None:
            return
        try:
            snapshot = session.review.snapshot()
            if snapshot is None:
                return
            with contextlib.closing(session.store.connect()) as conn:
                earlier = mover.status(conn)["batches"] - 1
            calibration.save(
                batch_id,
                snapshot,
                rejected,
                session.album_id,
                earlier,
                session.cfg,
                self.data_root,
            )
        except Exception:
            log.exception("calibration record failed")

    def start_retry(self, album_id: str, batch_id: str) -> dict[str, Any]:
        session = self.session(album_id)
        with contextlib.closing(session.store.connect()) as conn:
            if last_batch(conn) != batch_id:
                raise ApiError(409, "Dieser Vorgang kann nicht mehr fortgesetzt werden.")
        job = self._claim(session, "retry")
        job.batch_id = batch_id
        store, cfg = session.store, session.cfg
        return self._launch(
            session, job, lambda j, p: mover.retry(store, batch_id, cfg, p, j.cancel)
        )

    def start_undo(self, album_id: str, batch_id: str | None) -> dict[str, Any]:
        session = self.session(album_id)
        with contextlib.closing(session.store.connect()) as conn:
            last = last_batch(conn)
        if last is None or (batch_id is not None and batch_id != last):
            raise ApiError(409, "Es gibt keinen Vorgang, der rückgängig gemacht werden kann.")
        job = self._claim(session, "undo")
        job.batch_id = last
        store, cfg = session.store, session.cfg

        def work(j: Job, p: mover.ProgressFn) -> mover.Report:
            report = undo(store, last, cfg, on_progress=p, cancel=j.cancel)
            with contextlib.closing(store.connect()) as conn:
                fully = last_batch(conn) != last
            if fully:
                self._drop_record(last)
            return report

        return self._launch(session, job, work)

    def job_status(self, album_id: str) -> dict[str, Any]:
        session = self.session(album_id)
        with contextlib.closing(session.store.connect()) as conn:
            applied = mover.status(conn)
        with session.lock:
            return {"job": session.job.to_json() if session.job else None, "apply": applied}

    def open_reject_folder(self, album_id: str) -> None:
        """Explorer on _Aussortiert (04 §3) – only in the program window."""
        if self.open_folder is None:
            raise ApiError(501, "Nur im Programmfenster verfügbar.")
        root = self.session(album_id).store.root
        target = root / REJECT_DIR
        self.open_folder(target if target.is_dir() else root)

    def full_image(self, album_id: str, photo_id: int) -> bytes:
        """The original, upright, as JPEG for the 100 % zoom (04 §2). Read-only."""
        row = self.photo_row(album_id, photo_id)
        if row["status"] != "done":
            raise ApiError(404, "Bild nicht lesbar.")
        path = self.session(album_id).store.abs_path(row["rel_path"])
        try:
            return full_jpeg(path, self.cfg)
        except OSError as exc:
            raise ApiError(404, f"Original nicht lesbar: {exc}") from None

    def photo_row(self, album_id: str, photo_id: int) -> dict[str, Any]:
        session = self.session(album_id)
        with contextlib.closing(session.store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM photos WHERE id=? AND present=1", (photo_id,)
            ).fetchone()
        if row is None:
            raise ApiError(404, "Bild nicht gefunden.")
        return dict(row)

    def photo_features(self, album_id: str, photo_id: int) -> dict[str, Any] | None:
        """Raw analysis values for the debug view (key D); None before the analysis."""
        session = self.session(album_id)
        with contextlib.closing(session.store.connect()) as conn:
            row = conn.execute("SELECT * FROM features WHERE photo_id=?", (photo_id,)).fetchone()
        if row is None:
            return None
        quality = Quality(
            row["sharpness_global"],
            row["sharpness_peak"],
            row["exposure_mean"],
            row["contrast"],
            row["clip_low"],
            row["clip_high"],
        )
        return {
            "sharpness_global": row["sharpness_global"],
            "sharpness_peak": row["sharpness_peak"],
            "exposure_mean": row["exposure_mean"],
            "contrast": row["contrast"],
            "clip_low": row["clip_low"],
            "clip_high": row["clip_high"],
            "q_exposure": exposure_score(quality, session.cfg),
            "aesthetic": row["aesthetic"],
            "phash": row["phash"].hex() if row["phash"] else None,
            "zeroshot": json.loads(row["zeroshot"] or "{}"),
            "zeroshot_active": sorted(
                active_labels(json.loads(row["zeroshot"] or "{}"), session.cfg)
            ),
            "zeroshot_disabled": sorted(k for k, on in session.cfg.zs_enabled.items() if not on),
            "faces": json.loads(row["faces"] or "[]"),
            "faces_native": row["faces_native"],
        }

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
                    settings = dict(
                        conn.execute(
                            "SELECT key, value FROM settings"
                            " WHERE key IN ('include_subdirs', 'applied')"
                        ).fetchall()
                    )
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
                    "recursive": settings.get("include_subdirs", "1") == "1",
                    "applied": settings.get("applied") == "1",
                }
            )
            if len(items) >= RECENT_LIMIT:
                break
        return items
