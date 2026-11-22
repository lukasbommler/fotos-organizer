"""Analysis orchestration (02 §5): scan, sync, process pool, progress, cancel, resume."""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from fotoduenner.config import Config
from fotoduenner.grouping.series import find_series, phash_ints, series_members
from fotoduenner.pipeline.analyze import (
    FaceJob,
    FaceResult,
    Job,
    Result,
    analyze_faces_native,
    analyze_photo,
    init_worker,
)
from fotoduenner.pipeline.clipstage import ClipStage
from fotoduenner.pipeline.embed import from_blob
from fotoduenner.scanner.discover import ScanResult, scan_album
from fotoduenner.store.db import AlbumStore, SyncReport

log = logging.getLogger(__name__)

CRASH_MESSAGE = "Analyse-Prozess abgestürzt"


@dataclass(slots=True)
class Progress:
    stage: str  # 'scan' | 'analyze' | 'faces' (second pass, T6) | 'done' | 'cancelled'
    done: int
    total: int
    eta_seconds: float | None = None
    current_file: str | None = None
    errors: int = 0
    photo_id: int | None = None  # the photo just finished (live grid)


@dataclass(slots=True)
class RunSummary:
    scan: ScanResult
    sync: SyncReport
    processed: int = 0
    errors: int = 0
    cancelled: bool = False
    faces_native: int = 0  # photos that got the second pass (T6)
    faces_pending: int = 0  # second-pass photos left over (cancelled)
    clip_seconds: float = 0.0  # time spent in the separate CLIP stage (T11)
    seconds: float = 0.0
    results: list[Result] = field(default_factory=list)  # only with keep_results=True


ProgressCallback = Callable[[Progress], None]


def default_workers(cfg: Config) -> int:
    if cfg.analysis_workers is not None:
        return max(0, cfg.analysis_workers)
    physical = psutil.cpu_count(logical=False) or 2
    by_cpu = max(1, physical - 1)
    free_mb = psutil.virtual_memory().available / 1e6 - cfg.ram_reserve_mb
    by_ram = max(1, int(free_mb // cfg.worker_ram_mb))
    return min(by_cpu, by_ram)


class _Eta:
    """Moving average over the last ``window`` completions (02 §5)."""

    def __init__(self, window: int) -> None:
        self._stamps: deque[float] = deque(maxlen=window + 1)

    def tick(self) -> None:
        self._stamps.append(time.monotonic())

    def remaining(self, left: int) -> float | None:
        if len(self._stamps) < 2:
            return None
        per_item = (self._stamps[-1] - self._stamps[0]) / (len(self._stamps) - 1)
        return per_item * left


@contextlib.contextmanager
def _below_normal_priority(enabled: bool) -> Iterator[None]:
    """The CLIP stage computes in this process: keep the laptop usable while it runs (02 §5).

    The window is a separate WebView2 process and is not affected.
    """
    proc = psutil.Process()
    previous = None
    if enabled:
        try:
            previous = proc.nice()
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
        except (psutil.Error, OSError, AttributeError):
            previous = None
    try:
        yield
    finally:
        if previous is not None:
            with contextlib.suppress(psutil.Error, OSError):
                proc.nice(previous)


def analyze_album(
    root: Path,
    cfg: Config,
    *,
    cancel: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
    workers: int | None = None,
    data_root: Path | None = None,
    keep_results: bool = False,
    skip_offline: bool = False,
) -> RunSummary:
    """Scan ``root`` and analyse every photo not cached yet. Safe to cancel and to resume.

    ``workers=0`` runs in the calling thread (tests, debugging).
    """
    started = time.perf_counter()
    cancel = cancel or threading.Event()
    notify = on_progress or (lambda _p: None)
    notify(Progress("scan", 0, 0))
    scan = scan_album(root, cfg)
    store = AlbumStore(root, data_root)
    sync = store.sync(scan, cfg)
    summary = RunSummary(scan=scan, sync=sync)
    jobs = [
        Job(pid, str(store.abs_path(rel)), rel, str(store.thumbs_dir))
        for pid, rel in store.pending(include_offline=not skip_offline)
    ]
    log.info(
        "album %s: %d photos, %d to analyse (%d reused)", root, sync.total, len(jobs), sync.reused
    )
    with _below_normal_priority(enabled=workers != 0):
        _run_jobs(store, jobs, cfg, summary, cancel, notify, workers, keep_results)
        if cfg.face_second_pass and not cancel.is_set():
            _second_pass(store, cfg, summary, cancel, notify, workers)
    unfinished = summary.processed < len(jobs) or summary.faces_pending > 0
    summary.cancelled = cancel.is_set() and unfinished
    summary.seconds = time.perf_counter() - started
    final = "cancelled" if summary.cancelled else "done"
    notify(Progress(final, summary.processed, len(jobs), 0.0, None, summary.errors))
    return summary


def _run_jobs(
    store: AlbumStore,
    jobs: list[Job],
    cfg: Config,
    summary: RunSummary,
    cancel: threading.Event,
    notify: ProgressCallback,
    workers: int | None,
    keep_results: bool,
) -> None:
    total = len(jobs)
    eta = _Eta(cfg.eta_window)
    batch: list[Result] = []
    conn = store.connect()
    count = default_workers(cfg) if workers is None else workers
    stage: ClipStage | None = None
    if not cfg.clip_in_worker and total:

        def save_from_stage(results: list[Result]) -> None:
            with contextlib.closing(store.connect()) as stage_conn:  # own thread, own connection
                store.save_results(stage_conn, results, cfg)

        threads = cfg.clip_stage_threads or max(1, min(count, total))
        stage = ClipStage(cfg, threads, save_from_stage)
        stage.start()

    def record(result: Result) -> None:
        if stage is not None:
            stage.submit(result)
        else:
            batch.append(result)
        summary.processed += 1
        summary.errors += not result.ok
        if not result.ok:
            log.warning("photo unreadable: %s – %s", result.rel_path, result.error)
        if keep_results:
            summary.results.append(result)
        eta.tick()
        if len(batch) >= cfg.db_batch_size:
            flush()
        notify(
            Progress(
                "analyze",
                summary.processed,
                total,
                eta.remaining(total - summary.processed),
                result.rel_path,
                summary.errors,
                result.photo_id,
            )
        )

    def flush() -> None:
        if batch:
            store.save_results(conn, batch, cfg)
            batch.clear()

    try:
        notify(Progress("analyze", 0, total))
        if count == 0 or total == 0:
            init_worker(cfg, low_priority=False)
            for job in jobs:
                if cancel.is_set():
                    break
                record(analyze_photo(job, cfg))
        else:
            _run_pool(jobs, cfg, min(count, total), cancel, record)
    finally:
        flush()
        conn.close()
        if stage is not None:
            stage.close()  # waits until every photo is embedded and saved
            summary.clip_seconds = stage.seconds


def second_pass_jobs(store: AlbumStore, cfg: Config) -> list[FaceJob]:
    """Series members (03 §5.1) with faces whose native face sharpness is still missing."""
    with contextlib.closing(store.connect()) as conn:
        rows = store.series_inputs(conn)
    if len(rows) < 2:
        return []
    times = np.array([r["taken_at"] or 0.0 for r in rows], dtype=np.float64)
    timed = np.array([r["taken_at_source"] == "exif" for r in rows], dtype=bool)
    phashes = phash_ints([r["phash"] for r in rows])
    embeddings = np.stack([from_blob(r["embedding"]) for r in rows])
    members = series_members(find_series(times, timed, phashes, embeddings, cfg))
    wanted = [
        r
        for r, member in zip(rows, members, strict=True)
        if member and r["face_count"] and r["faces_native"] == 0
    ]
    return store.face_jobs(wanted)


def _second_pass(
    store: AlbumStore,
    cfg: Config,
    summary: RunSummary,
    cancel: threading.Event,
    notify: ProgressCallback,
    workers: int | None,
) -> None:
    """T6: decode series members with faces at full resolution, measure native face sharpness."""
    jobs = second_pass_jobs(store, cfg)
    if not jobs:
        return
    log.info("second pass: %d series photos with faces", len(jobs))
    total = len(jobs)
    eta = _Eta(cfg.eta_window)
    batch: list[FaceResult] = []
    done = 0
    conn = store.connect()

    def record(result: FaceResult) -> None:
        nonlocal done
        done += 1
        summary.faces_native += result.ok
        if not result.ok:
            log.warning("second pass failed: %s – %s", result.rel_path, result.error)
        batch.append(result)
        eta.tick()
        if len(batch) >= cfg.db_batch_size:
            store.save_face_results(conn, batch)
            batch.clear()
        notify(Progress("faces", done, total, eta.remaining(total - done), result.rel_path))

    try:
        notify(Progress("faces", 0, total))
        count = default_workers(cfg) if workers is None else workers
        if count == 0:
            init_worker(cfg, low_priority=False)
            for job in jobs:
                if cancel.is_set():
                    break
                record(analyze_faces_native(job))
        else:
            _run_pool(
                jobs, cfg, min(count, total), cancel, record, analyze_faces_native, _face_crash
            )
    finally:
        if batch:
            store.save_face_results(conn, batch)
        conn.close()
        summary.faces_pending = total - done


def _photo_crash(job: Job) -> Result:
    return Result(job.photo_id, job.rel_path, ok=False, error=CRASH_MESSAGE)


def _face_crash(job: FaceJob) -> FaceResult:
    return FaceResult(job.photo_id, job.rel_path, ok=False, error=CRASH_MESSAGE)


def _run_pool(
    jobs: list[Any],
    cfg: Config,
    workers: int,
    cancel: threading.Event,
    record: Callable[[Any], None],
    task: Callable[[Any], Any] = analyze_photo,
    crash_result: Callable[[Any], Any] = _photo_crash,
) -> None:
    """Keep at most ``workers * submit_ahead`` jobs in flight; a crash restarts the pool.

    Photos in flight during a crash become suspects and are retried one at a time, so a
    crash with a single photo in flight identifies the culprit exactly (status 'error').
    ``task`` must be a module-level function (pickled by name for ``spawn``).
    """
    queue: deque[Any] = deque(jobs)
    suspects: deque[Any] = deque()
    limit = workers * cfg.submit_ahead_per_worker
    context = multiprocessing.get_context("spawn")

    while (queue or suspects) and not cancel.is_set():
        pool = ProcessPoolExecutor(workers, context, init_worker, (cfg,))
        in_flight: dict[Future[Any], Any] = {}
        lost: list[Any] = []
        try:
            while not lost:
                while not cancel.is_set():
                    if suspects:  # isolation: wait until nothing else runs, then one suspect
                        if in_flight:
                            break
                        job = suspects.popleft()
                    elif queue and len(in_flight) < limit:
                        job = queue.popleft()
                    else:
                        break
                    in_flight[pool.submit(task, job)] = job
                if not in_flight:
                    break
                done, _ = wait(in_flight, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    job = in_flight.pop(future)
                    try:
                        record(future.result())
                    except BrokenProcessPool:
                        lost.append(job)
                if lost:  # every other in-flight future died with the pool
                    lost += in_flight.values()
                    in_flight.clear()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if len(lost) == 1:
            log.error("worker crashed on %s", lost[0].rel_path)
            record(crash_result(lost[0]))
        elif lost:
            log.error("worker pool crashed with %d photos in flight, isolating", len(lost))
            suspects.extend(lost)
