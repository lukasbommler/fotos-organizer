"""Central configuration: every threshold and weight lives here (defaults from docs/03 §10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["clean", "quota"]


def _zs_enabled() -> dict[str, bool]:
    return {"person_back": True, "screenshot": True, "document_sign": True, "blurry": True}


def _zs_threshold() -> dict[str, float]:
    return {"person_back": 0.5, "screenshot": 0.6, "document_sign": 0.6, "blurry": 0.6}


@dataclass(frozen=True, slots=True)
class Config:
    # Bump to force recomputation of cached features.
    algo_version: int = 1

    # Decoding (03 §2, T9)
    decode_target: int = 1600
    decode_min_long_edge: int = 1500
    thumb_small: int = 320
    thumb_large: int = 1600
    thumb_jpeg_quality: int = 85

    # Faces (03 §3.5)
    face_score_threshold: float = 0.75
    face_nms_threshold: float = 0.3
    face_top_k: int = 50
    face_min_size_rel: float = 0.015
    people_min_size_rel: float = 0.035
    people_min_conf: float = 0.85
    eyes_min_size_rel: float = 0.04

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

    # Ranking within groups (03 §7)
    mmr_lambda: float = 0.65
    mmr_gamma: float = 1.5
    facecount_diversity: float = 0.8
    people_priority_boost: float = 0.5

    # Mode "clean" (03 §8.1, D12)
    clean_distinct_cos: float = 0.93

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

    # Analysis runtime (02 §5)
    db_batch_size: int = 50
    eta_window: int = 50


DEFAULT = Config()
