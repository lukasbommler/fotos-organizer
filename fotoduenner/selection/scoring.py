"""Photo value (03 §6) and the per-photo facts it needs. Pure functions, no I/O.

All partial values are 0..1; ranks are album-relative percentile ranks over the photos that
take part in the selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import rankdata

from fotoduenner.config import Config

LOW_VALUE_LABELS = ("screenshot", "document_sign")


@dataclass(slots=True)
class PhotoFacts:
    """Per-photo inputs of the selection, all arrays of length N (album order)."""

    sharpness_peak: np.ndarray
    exposure_mean: np.ndarray
    contrast: np.ndarray
    clip_low: np.ndarray
    clip_high: np.ndarray
    aesthetic: np.ndarray
    faces: list[list[dict]]  # stored face records (pipeline/faces.py Face.to_json)
    zs_active: list[set[str]]  # active zero-shot labels per photo


@dataclass(slots=True)
class Scores:
    people: np.ndarray  # bool: is_people_photo
    face_count_main: np.ndarray  # int
    main_faces: list[list[int]]  # indices into PhotoFacts.faces[i]
    sharp_rank: np.ndarray  # rank of sharpness_peak
    face_rank: np.ndarray  # mean rank of the main faces' sharpness, NaN without measured face
    face_rank_max: np.ndarray  # best rank among the main faces, NaN without measured face
    eyes_closed: np.ndarray  # bool: some main face has eyes_open below the threshold
    q_sharp: np.ndarray
    q_exp: np.ndarray
    q_aes: np.ndarray
    q_face: np.ndarray
    zs_factor: np.ndarray
    value: np.ndarray


def percentile_rank(values: np.ndarray) -> np.ndarray:
    """0..1 rank, ties averaged, NaN stays NaN; a single value gets 0.5."""
    out = np.full(len(values), np.nan)
    ok = ~np.isnan(values)
    n = int(ok.sum())
    if n == 1:
        out[ok] = 0.5
    elif n > 1:
        out[ok] = (rankdata(values[ok], method="average") - 1) / (n - 1)
    return out


def exposure_scores(facts: PhotoFacts, cfg: Config) -> np.ndarray:
    """q_exposure (03 §3.3), vectorised version of pipeline.quality.exposure_score."""
    penalty = np.maximum(0.0, facts.clip_high - cfg.exposure_clip_high_free) * (
        cfg.exposure_clip_high_weight
    )
    penalty += np.maximum(0.0, facts.clip_low - cfg.exposure_clip_low_free) * (
        cfg.exposure_clip_low_weight
    )
    mean = facts.exposure_mean
    penalty += np.where(
        mean < cfg.exposure_mean_low, (cfg.exposure_mean_low - mean) / cfg.exposure_mean_low, 0.0
    )
    penalty += np.where(
        mean > cfg.exposure_mean_high,
        (mean - cfg.exposure_mean_high) / (255.0 - cfg.exposure_mean_high),
        0.0,
    )
    return 1.0 - np.minimum(1.0, penalty)


def _main_faces(faces: list[dict]) -> list[int]:
    """Faces with size_rel >= 0.5 * prominence (03 §3.5)."""
    if not faces:
        return []
    prominence = max(f["size_rel"] for f in faces)
    return [k for k, f in enumerate(faces) if f["size_rel"] >= 0.5 * prominence]


def face_sharpness(face: dict, native: bool) -> float | None:
    value = face.get("sharpness_native") if native else face.get("sharpness")
    return None if value is None else float(value)


def combine(
    q_sharp: np.ndarray,
    q_exp: np.ndarray,
    q_aes: np.ndarray,
    q_face: np.ndarray,
    people: np.ndarray,
    zs_factor: np.ndarray,
    cfg: Config,
) -> np.ndarray:
    wp, wo = cfg.value_weights_people, cfg.value_weights_other
    value_people = wp[0] * q_sharp + wp[1] * q_exp + wp[2] * q_aes + wp[3] * q_face
    value_other = wo[0] * q_sharp + wo[1] * q_exp + wo[2] * q_aes
    return np.where(people, value_people, value_other) * zs_factor


def people_q_sharp(face_rank: np.ndarray, sharp_rank: np.ndarray, cfg: Config) -> np.ndarray:
    w = cfg.people_face_sharp_weight
    return np.where(np.isnan(face_rank), sharp_rank, w * face_rank + (1 - w) * sharp_rank)


def compute_scores(facts: PhotoFacts, cfg: Config) -> Scores:
    n = len(facts.sharpness_peak)
    main = [_main_faces(f) for f in facts.faces]
    people = np.zeros(n, dtype=bool)
    eyes_closed = np.zeros(n, dtype=bool)
    q_face = np.full(n, cfg.eyes_unknown)
    for i, faces in enumerate(facts.faces):
        # passers-by in the background do not make a landscape a people photo (03 §3.5)
        people[i] = "person_back" in facts.zs_active[i] or any(
            f["size_rel"] >= cfg.people_min_size_rel and f["conf"] >= cfg.people_min_conf
            for f in faces
        )
        eyes = [faces[k].get("eyes_open") for k in main[i]]
        if eyes:
            judged = [e for e in eyes if e is not None]
            eyes_closed[i] = any(e < cfg.eyes_open_threshold for e in judged)
            mean = float(np.mean([cfg.eyes_unknown if e is None else e for e in eyes]))
            all_open = len(judged) == len(eyes) and all(e > cfg.eyes_open_threshold for e in judged)
            q_face[i] = min(1.0, mean + (cfg.eyes_all_open_bonus if all_open else 0.0))

    # Face sharpness rank among all main faces of people photos (first pass, comparable values)
    owners: list[int] = []
    values: list[float] = []
    for i in np.flatnonzero(people):
        for k in main[i]:
            s = face_sharpness(facts.faces[i][k], native=False)
            if s is not None:
                owners.append(int(i))
                values.append(s)
    face_rank = np.full(n, np.nan)
    face_rank_max = np.full(n, np.nan)
    if values:
        ranks = percentile_rank(np.array(values))
        sums = np.zeros(n)
        counts = np.zeros(n)
        np.add.at(sums, owners, ranks)
        np.add.at(counts, owners, 1)
        best = np.full(n, -1.0)
        np.maximum.at(best, owners, ranks)
        face_rank_max = np.where(counts > 0, best, np.nan)
        with np.errstate(invalid="ignore"):
            face_rank = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)

    sharp_rank = percentile_rank(facts.sharpness_peak.astype(np.float64))
    q_sharp = np.where(people, people_q_sharp(face_rank, sharp_rank, cfg), sharp_rank)
    q_exp = exposure_scores(facts, cfg)
    q_aes = np.nan_to_num(percentile_rank(facts.aesthetic.astype(np.float64)), nan=0.5)
    zs_factor = np.array(
        [
            cfg.zs_low_value_factor if labels & set(LOW_VALUE_LABELS) else 1.0
            for labels in facts.zs_active
        ]
    )
    q_sharp = np.nan_to_num(q_sharp, nan=0.5)
    value = combine(q_sharp, q_exp, q_aes, q_face, people, zs_factor, cfg)
    return Scores(
        people=people,
        face_count_main=np.array([len(m) for m in main], dtype=np.int64),
        main_faces=main,
        sharp_rank=np.nan_to_num(sharp_rank, nan=0.5),
        face_rank=face_rank,
        face_rank_max=face_rank_max,
        eyes_closed=eyes_closed,
        q_sharp=q_sharp,
        q_exp=q_exp,
        q_aes=q_aes,
        q_face=q_face,
        zs_factor=zs_factor,
        value=value,
    )


def series_values(
    members: Sequence[int], facts: PhotoFacts, scores: Scores, cfg: Config
) -> tuple[np.ndarray, np.ndarray]:
    """Values and q_sharp for ranking inside one series, with native face sharpness (T6).

    ASSUMPTION: native values (second pass) only compare within a series, so they reorder the
    album ranks of the members: the member with the sharpest native faces gets the best of the
    members' album face ranks, and so on. Album-wide comparisons keep the first-pass ranks.
    """
    idx = np.array(members)
    face_rank = scores.face_rank[idx].copy()
    native = np.full(len(idx), np.nan)
    for pos, i in enumerate(idx):
        if not scores.people[i] or np.isnan(face_rank[pos]):
            continue
        vals = [face_sharpness(facts.faces[i][k], native=True) for k in scores.main_faces[i]]
        if vals and all(v is not None for v in vals):
            native[pos] = float(np.mean(vals))  # type: ignore[arg-type]
    have = np.flatnonzero(~np.isnan(native))
    if len(have) < 2:
        return scores.value[idx], scores.q_sharp[idx]
    face_rank[have[np.argsort(native[have], kind="stable")]] = np.sort(face_rank[have])
    q_sharp = np.where(
        scores.people[idx],
        people_q_sharp(face_rank, scores.sharp_rank[idx], cfg),
        scores.q_sharp[idx],
    )
    value = combine(
        q_sharp,
        scores.q_exp[idx],
        scores.q_aes[idx],
        scores.q_face[idx],
        scores.people[idx],
        scores.zs_factor[idx],
        cfg,
    )
    return value, q_sharp
