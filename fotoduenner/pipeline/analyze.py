"""Per-photo analysis, executed inside a worker process (02 §5, simple variant T11).

Everything here must be picklable and import-light: workers are started with ``spawn``.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from fotoduenner.config import Config
from fotoduenner.pipeline.decode import decode_bytes
from fotoduenner.pipeline.thumbs import write_thumbs
from fotoduenner.scanner.exif import PhotoMeta

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Job:
    photo_id: int
    path: str  # absolute, with \\?\ prefix on Windows
    rel_path: str
    thumb_dir: str


@dataclass(slots=True)
class Result:
    photo_id: int
    rel_path: str
    ok: bool
    error: str | None = None
    file_hash: bytes | None = None
    meta: PhotoMeta | None = None
    timings: dict[str, float] = field(default_factory=dict)  # seconds per stage


_cfg: Config | None = None


def init_worker(cfg: Config, low_priority: bool = True) -> None:
    """Pool initializer: runs once per worker process (models get loaded here from M3 on)."""
    global _cfg
    _cfg = cfg
    Image.MAX_IMAGE_PIXELS = cfg.max_image_pixels
    if low_priority:
        try:
            import psutil

            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:  # priority is a courtesy, never a reason to fail
            log.debug("could not lower worker priority", exc_info=True)


def analyze_photo(job: Job, cfg: Config | None = None) -> Result:
    """Analyse one photo. Never raises: failures come back as ``ok=False`` (status 'error')."""
    cfg = cfg or _cfg
    if cfg is None:
        raise RuntimeError("worker not initialised")
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    try:
        with open(job.path, "rb") as handle:  # noqa: PTH123 – \\?\ string path, hot loop
            data = handle.read()
        t1 = time.perf_counter()
        timings["read"] = t1 - t0
        # SHA-256: hardware-accelerated on current CPUs, ~3x faster than BLAKE2b (A13).
        file_hash = hashlib.sha256(data).digest()
        t2 = time.perf_counter()
        timings["hash"] = t2 - t1
        decoded = decode_bytes(data, cfg)
        del data
        t3 = time.perf_counter()
        timings["decode"] = t3 - t2
        write_thumbs(decoded.image, Path(job.thumb_dir), job.photo_id, cfg, decoded.icc_profile)
        timings["thumbs"] = time.perf_counter() - t3
    except Exception as exc:
        log.debug("cannot analyse %s", job.rel_path, exc_info=True)  # main process logs
        message = f"{type(exc).__name__}: {exc}"
        return Result(job.photo_id, job.rel_path, ok=False, error=message[:500], timings=timings)
    return Result(
        job.photo_id,
        job.rel_path,
        ok=True,
        file_hash=file_hash,
        meta=decoded.meta,
        timings=timings,
    )
