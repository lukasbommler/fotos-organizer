"""Central configuration: every threshold and weight lives here (defaults from docs/03 §10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["clean", "quota"]


# Zero-shot labels, checked on the M3 test set (A24): "blurry" hits only 29 % of blurred photos
# at 0.6 – unreliable, switched off (D13); sharpness ranks cover blur anyway.
def _zs_enabled() -> dict[str, bool]:
    return {"person_back": True, "screenshot": True, "document_sign": True, "blurry": False}


def _zs_threshold() -> dict[str, float]:
    return {"person_back": 0.70, "screenshot": 0.50, "document_sign": 0.65, "blurry": 0.6}


@dataclass(frozen=True, slots=True)
class Config:
    # Bump to force recomputation of cached features. 2: features of M3 (quality, faces, CLIP).
    algo_version: int = 2

    # Scanning (01 §3)
    include_subdirs: bool = True
    sidecar_max_bytes: int = 10 * 1024 * 1024

    # Decoding (03 §2, T9)
    decode_target: int = 1600
    decode_min_long_edge: int = 1500
    # Pillow's decompression-bomb guard; stitched panoramas can exceed its 179 MP default.
    max_image_pixels: int = 600_000_000
    thumb_small: int = 320
    thumb_large: int = 1600
    thumb_jpeg_quality: int = 85
    full_jpeg_quality: int = 92  # 100 % zoom: original re-encoded in memory (04 §2)

    # Exposure score q_exposure (03 §3.3)
    exposure_clip_high_free: float = 0.02
    exposure_clip_high_weight: float = 4.0
    exposure_clip_low_free: float = 0.08
    exposure_clip_low_weight: float = 2.0
    # ASSUMPTION: 03 §3.3 names a penalty below 35 / above 225 without its size; it grows
    # linearly and reaches 1 at mean 0 resp. 255.
    exposure_mean_low: float = 35.0
    exposure_mean_high: float = 225.0

    # Faces (03 §3.5)
    # M3 test set (A23): 0.75 on img_640 found 91 % of the faces > 3 %; 0.6 on 480 + 640 px
    # (merged) finds 96 %. People photos still need conf >= people_min_conf (0.85).
    face_score_threshold: float = 0.6
    face_detect_edges: tuple[int, ...] = (480, 640)
    face_nms_threshold: float = 0.3
    face_top_k: int = 50
    face_min_size_rel: float = 0.015
    people_min_size_rel: float = 0.035
    people_min_conf: float = 0.85
    eyes_min_size_rel: float = 0.04
    eyes_min_presence: float = 0.5
    # eyes_open >= this counts as "open" (03 §6 bonus at 0.6; M3 test set: best split, A23)
    eyes_open_threshold: float = 0.6
    face_second_pass: bool = True  # T6: native face sharpness for series members with faces

    # Series, level 1 (03 §5.1)
    phash_series_max: int = 8
    phash_no_time_max: int = 5
    series_phash_dt: float = 120.0
    series_cos_min: float = 0.95
    series_cos_dt: float = 300.0

    # Sequences: brackets and panoramas (03 §5.0, A2)
    seq_max_gap: float = 3.0
    seq_min_len: int = 3
    bracket_min_ev_span: float = 0.7
    pano_shift_min: float = 0.15
    pano_shift_max: float = 0.70
    pano_min_peak: float = 0.1

    # Motif groups, level 2 (03 §5.2)
    motif_alpha: float = 0.06
    motif_tmax: float = 3 * 3600.0
    motif_day_penalty: float = 0.05
    motif_thresholds: tuple[float, ...] = (0.08, 0.11, 0.14, 0.18, 0.22)
    motif_default_level: int = 2  # index into motif_thresholds
    group_max_size: int = 30
    group_max_span: float = 24 * 3600.0
    group_split_factor: float = 0.7
    group_split_max_depth: int = 3

    # ASSUMPTION: photos without EXIF time get the full time penalty and the day penalty in the
    # motif distance (03 §5.2 leaves them open) – they only group when they look nearly equal.

    # Photo value (03 §6): weights of sharpness, exposure, aesthetic, faces
    value_weights_people: tuple[float, float, float, float] = (0.35, 0.10, 0.15, 0.40)
    value_weights_other: tuple[float, float, float] = (0.50, 0.20, 0.30)
    people_face_sharp_weight: float = 0.6  # q_sharp of a people photo: faces vs. whole photo
    eyes_unknown: float = 0.7  # q_face when eyes could not be judged (small face, person_back)
    eyes_all_open_bonus: float = 0.1

    # Ranking within groups (03 §7)
    mmr_lambda: float = 0.65
    mmr_gamma: float = 1.5
    facecount_diversity: float = 0.8
    people_priority_boost: float = 0.5
    aesthetic_priority: tuple[float, float] = (0.85, 0.3)  # priority · (a + b · q_aes)

    # Mode "clean" (03 §8.1, D12)
    clean_distinct_cos: float = 0.93

    # Mode "quota" (03 §8.2)
    people_second_min_group: int = 3  # rank 1 is kept too if it is a people photo
    low_single_value: float = 0.25  # option "Einzelbilder mit geringem Wert aussortieren"
    low_single_aesthetic_rank: float = 0.2
    # Dominant factor in a series reason ("..., aber unschärfer") must differ at least this much.
    reason_min_diff: float = 0.02

    # Quality (03 §4, T5). blur_abs stays None until calibrated in M7.
    blur_abs: float | None = None
    blur_rel: float = 0.10
    blur_review_rel: float = 0.05
    misfire_sharp_rel: float = 0.03
    misfire_contrast: float = 8.0
    overexp_clip_high: float = 0.35
    overexp_mean: float = 240.0
    underexp_mean: float = 20.0
    underexp_contrast: float = 15.0

    # Zero-shot labels (03 §3.8, D13)
    zs_enabled: dict[str, bool] = field(default_factory=_zs_enabled)
    zs_threshold: dict[str, float] = field(default_factory=_zs_threshold)
    zs_low_value_factor: float = 0.8

    # Selection defaults (03 §8)
    default_mode: Mode = "clean"
    target_keep_default: float = 0.50
    target_keep_range: tuple[float, float] = (0.30, 0.90)

    # Übernehmen / Rückgängig (02 §6): a taken name gets " (2)", " (3)" … up to this number
    name_suffix_limit: int = 999

    # Analysis runtime (02 §5)
    analysis_workers: int | None = None  # None = physical cores - 1, capped by free RAM
    # ASSUMPTION: peak RSS of one worker with all models (CLIP FP16 ≈ 0.5 GB, A25) plus a reserve
    # for Windows, WebView2 and the main process; fewer workers on laptops with little free RAM.
    worker_ram_mb: int = 800
    ram_reserve_mb: int = 1500
    submit_ahead_per_worker: int = 2  # bounded queue: fast cancel, bounded memory
    # T11/A25: CLIP runs as a separate batched stage in the main process (one model in RAM
    # instead of one per worker, ~1.5x CLIP throughput with FP16). False = inside each worker.
    clip_in_worker: bool = False
    clip_batch: int = 16
    clip_stage_threads: int | None = None  # None = number of workers
    db_batch_size: int = 50
    # ASSUMPTION: CPU ms per photo for the pre-check estimate (estimate = n * ms / workers):
    # M3 benchmark 136 ms wall per photo with 3 workers incl. CLIP stage (docs/BENCHMARKS.md).
    estimate_ms_per_photo: float = 420.0
    # ASSUMPTION: full-resolution decode of a 24 MP JPEG + face crops (02 §5: 250–400 ms).
    estimate_ms_second_pass: float = 350.0
    estimate_startup_s: float = 3.0
    few_photos_warning: int = 20
    eta_window: int = 50


DEFAULT = Config()
