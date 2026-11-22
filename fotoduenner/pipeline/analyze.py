"""Per-photo analysis, executed inside a worker process (02 §5, simple variant T11).

Everything here must be picklable and import-light: workers are started with ``spawn``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from fotoduenner.config import Config
from fotoduenner.pipeline import faces as face_stage
from fotoduenner.pipeline import zeroshot
from fotoduenner.pipeline.aesthetic import aesthetic_score
from fotoduenner.pipeline.decode import decode_bytes
from fotoduenner.pipeline.embed import clip_crop, clip_embedding, to_blob
from fotoduenner.pipeline.hashing import phash
from fotoduenner.pipeline.models import Models, load_models
from fotoduenner.pipeline.quality import gray_1024, measure_gray
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
class Features:
    """One row of the ``features`` table (02 §4)."""

    phash: bytes
    embedding: bytes | None  # float16[512], L2-normalised; filled by the CLIP stage
    sharpness_global: float
    sharpness_peak: float
    exposure_mean: float
    contrast: float
    clip_low: float
    clip_high: float
    aesthetic: float | None
    zeroshot: dict[str, float] | None
    faces: list[dict[str, object]]
    clip_input: np.ndarray | None = None  # uint8 224² crop for the CLIP stage, never stored

    @property
    def face_count(self) -> int:
        return len(self.faces)


@dataclass(slots=True)
class Result:
    photo_id: int
    rel_path: str
    ok: bool
    error: str | None = None
    file_hash: bytes | None = None
    meta: PhotoMeta | None = None
    features: Features | None = None
    timings: dict[str, float] = field(default_factory=dict)  # seconds per stage


@dataclass(frozen=True, slots=True)
class FaceJob:
    """Second pass (T6): native face sharpness on the full-resolution original."""

    photo_id: int
    path: str
    rel_path: str
    faces_json: str


@dataclass(slots=True)
class FaceResult:
    photo_id: int
    rel_path: str
    ok: bool
    faces_json: str | None = None
    error: str | None = None
    seconds: float = 0.0


_cfg: Config | None = None
_models: Models | None = None
_models_key: tuple | None = None


def _face_params(cfg: Config) -> tuple[float, float, int]:
    return (cfg.face_score_threshold, cfg.face_nms_threshold, cfg.face_top_k)


def init_worker(cfg: Config, low_priority: bool = True) -> None:
    """Pool initializer: runs once per worker process and loads every model once."""
    global _cfg, _models, _models_key
    _cfg = cfg
    Image.MAX_IMAGE_PIXELS = cfg.max_image_pixels
    cv2.setNumThreads(1)  # one core per worker; the pool provides the parallelism
    if low_priority:
        try:
            import psutil

            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:  # priority is a courtesy, never a reason to fail
            log.debug("could not lower worker priority", exc_info=True)
    key = (*_face_params(cfg), cfg.clip_in_worker)
    if _models is None or _models_key != key:
        _models = load_models(_face_params(cfg), with_clip=cfg.clip_in_worker)
        _models_key = key


def _require() -> tuple[Config, Models]:
    if _cfg is None or _models is None:
        raise RuntimeError("worker not initialised")
    return _cfg, _models


def compute_features(models: Models, large: Image.Image, cfg: Config) -> tuple[Features, dict]:
    """All image features from the working image (≤ 1600 px, orientation applied)."""
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    rgb = np.asarray(large)
    gray = gray_1024(rgb)
    quality = measure_gray(gray)
    p_hash = phash(gray)
    t1 = time.perf_counter()
    timings["quality"] = t1 - t0
    faces = face_stage.analyze_faces(models, rgb, cfg)
    t2 = time.perf_counter()
    timings["faces"] = t2 - t1
    if models.clip is None:  # separate CLIP stage (T11): send the crop, embed later
        crop = clip_crop(large)
        features = Features(
            phash=p_hash,
            embedding=None,
            sharpness_global=quality.sharpness_global,
            sharpness_peak=quality.sharpness_peak,
            exposure_mean=quality.exposure_mean,
            contrast=quality.contrast,
            clip_low=quality.clip_low,
            clip_high=quality.clip_high,
            aesthetic=None,
            zeroshot=None,
            faces=[face.to_json() for face in faces],
            clip_input=crop,
        )
        timings["clip_crop"] = time.perf_counter() - t2
        return features, timings
    embedding = clip_embedding(models.clip, large)
    t3 = time.perf_counter()
    timings["clip"] = t3 - t2
    features = Features(
        phash=p_hash,
        embedding=to_blob(embedding),
        sharpness_global=quality.sharpness_global,
        sharpness_peak=quality.sharpness_peak,
        exposure_mean=quality.exposure_mean,
        contrast=quality.contrast,
        clip_low=quality.clip_low,
        clip_high=quality.clip_high,
        aesthetic=aesthetic_score(embedding, models.aesthetic_weight, models.aesthetic_bias),
        zeroshot=zeroshot.scores(embedding, models.zeroshot),
        faces=[face.to_json() for face in faces],
    )
    timings["zeroshot"] = time.perf_counter() - t3
    return features, timings


def analyze_photo(job: Job, cfg: Config | None = None) -> Result:
    """Analyse one photo. Never raises: failures come back as ``ok=False`` (status 'error')."""
    cfg_worker, models = _require()
    cfg = cfg or cfg_worker
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
        large = write_thumbs(
            decoded.image, Path(job.thumb_dir), job.photo_id, cfg, decoded.icc_profile
        )
        timings["thumbs"] = time.perf_counter() - t3
        features, feature_timings = compute_features(models, large, cfg)
        timings.update(feature_timings)
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
        features=features,
        timings=timings,
    )


def decode_full_gray(data: bytes) -> np.ndarray:
    """Full-resolution luma of the original, orientation applied (no colour conversion)."""
    img = Image.open(io.BytesIO(data))
    if img.format == "JPEG":
        img.draft("L", img.size)  # libjpeg outputs greyscale directly: skips colour conversion
    img.load()
    img = ImageOps.exif_transpose(img)
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img, np.float32)


def analyze_faces_native(job: FaceJob) -> FaceResult:
    """Second pass (T6). Never raises."""
    start = time.perf_counter()
    try:
        _require()
        with open(job.path, "rb") as handle:  # noqa: PTH123 – \\?\ string path
            data = handle.read()
        gray = decode_full_gray(data)
        del data
        faces = [face_stage.Face.from_json(f) for f in json.loads(job.faces_json)]
        face_stage.native_sharpness(gray, faces)
        payload = json.dumps([f.to_json() for f in faces], separators=(",", ":"))
    except Exception as exc:
        log.debug("second pass failed for %s", job.rel_path, exc_info=True)
        error = f"{type(exc).__name__}: {exc}"
        return FaceResult(job.photo_id, job.rel_path, ok=False, error=error[:500])
    seconds = time.perf_counter() - start
    return FaceResult(job.photo_id, job.rel_path, ok=True, faces_json=payload, seconds=seconds)
