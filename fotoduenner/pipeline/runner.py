"""Analysis orchestration (02 §5): scan, sync, process pool, progress, cancel, resume."""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from fotoduenner.config import Config
from fotoduenner.pipeline.analyze import Job, Result, analyze_photo, init_worker
from fotoduenner.scanner.discover import ScanResult, scan_album
from fotoduenner.store.db import AlbumStore, SyncReport

log = logging.getLogger(__name__)

CRASH_MESSAGE = "Analyse-Prozess abgestürzt"


@dataclass(slots=True)
class Progress:
    stage: str  # 'scan' | 'analyze' | 'done' | 'cancelled'
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
    seconds: float = 0.0
    results: list[Result] = field(default_factory=list)  # only with keep_results=True


ProgressCallback = Callable[[Progress], None]


def default_workers(cfg: Config) -> int:
    if cfg.analysis_workers is not None:
        return max(0, cfg.analysis_workers)
    physical = psutil.cpu_count(logical=False) or 2
    return max(1, physical - 1)


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
    _run_jobs(store, jobs, cfg, summary, cancel, notify, workers, keep_results)
    summary.cancelled = cancel.is_set() and summary.processed < len(jobs)
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

    def record(result: Result) -> None:
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
        count = default_workers(cfg) if workers is None else workers
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


def _run_pool(
    jobs: list[Job],
    cfg: Config,
    workers: int,
    cancel: threading.Event,
    record: Callable[[Result], None],
    task: Callable[[Job], Result] = analyze_photo,
) -> None:
    """Keep at most ``workers * submit_ahead`` jobs in flight; a crash restarts the pool.

    Photos in flight during a crash become suspects and are retried one at a time, so a
    crash with a single photo in flight identifies the culprit exactly (status 'error').
    ``task`` must be a module-level function (pickled by name for ``spawn``).
    """
    queue: deque[Job] = deque(jobs)
    suspects: deque[Job] = deque()
    limit = workers * cfg.submit_ahead_per_worker
    context = multiprocessing.get_context("spawn")

    while (queue or suspects) and not cancel.is_set():
        pool = ProcessPoolExecutor(workers, context, init_worker, (cfg,))
        in_flight: dict[Future[Result], Job] = {}
        lost: list[Job] = []
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
            record(Result(lost[0].photo_id, lost[0].rel_path, ok=False, error=CRASH_MESSAGE))
        elif lost:
            log.error("worker pool crashed with %d photos in flight, isolating", len(lost))
            suspects.extend(lost)
