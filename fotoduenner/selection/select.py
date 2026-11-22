"""Ranking within a group (MMR, 03 §7) and the quality check (03 §4). Pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from fotoduenner.config import Config
from fotoduenner.selection.scoring import PhotoFacts, Scores

VerdictKind = Literal["bad", "review"]


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: VerdictKind  # bad = proposed for sorting out, review = "Bitte prüfen" (kept)
    code: str  # rating_rejected | misfire | overexposed | underexposed | blur | blur_review
    params: dict[str, float] = field(default_factory=dict)


def mmr(
    values: np.ndarray, embeddings: np.ndarray, face_counts: np.ndarray, cfg: Config
) -> tuple[np.ndarray, np.ndarray]:
    """Order (positions into the inputs) and marginal value of each photo.

    The first photo is the most valuable; every next one maximises
    ``lambda * value - (1 - lambda) * max_cos_to_ranked``. A different number of main faces than
    all ranked photos lowers the similarity (x 0.8). ``marginal = value * (1 - sim) ** gamma``.
    """
    n = len(values)
    order = np.empty(n, dtype=np.int64)
    marginal = np.zeros(n)
    if n == 0:
        return order, marginal
    first = int(np.argmax(values))
    order[0] = first
    marginal[first] = values[first]
    left = np.ones(n, dtype=bool)
    left[first] = False
    max_sim = embeddings @ embeddings[first]
    counts_seen = {int(face_counts[first])}
    lam = cfg.mmr_lambda
    for pos in range(1, n):
        cand = np.flatnonzero(left)
        sim = max_sim[cand].copy()
        differs = np.array([int(c) not in counts_seen for c in face_counts[cand]])
        sim = np.where(differs, sim * cfg.facecount_diversity, sim)
        best = int(np.argmax(lam * values[cand] - (1 - lam) * sim))
        pick = int(cand[best])
        order[pos] = pick
        marginal[pick] = values[pick] * (1.0 - float(np.clip(sim[best], 0.0, 1.0))) ** cfg.mmr_gamma
        left[pick] = False
        max_sim = np.maximum(max_sim, embeddings @ embeddings[pick])
        counts_seen.add(int(face_counts[pick]))
    return order, marginal


def quality_verdicts(
    facts: PhotoFacts,
    scores: Scores,
    ratings: np.ndarray,
    in_sequence: np.ndarray,
    cfg: Config,
) -> list[Verdict | None]:
    """Technically bad photos (03 §4) before the protection rules that need the groups.

    Rating >= 1 never gets a verdict. Sequence members are never judged by exposure. Without a
    calibrated ``blur_abs`` blur only leads to "Bitte prüfen" (T5).
    """
    out: list[Verdict | None] = []
    calibrated = cfg.blur_abs is not None
    for i in range(len(ratings)):
        if ratings[i] >= 1:
            out.append(None)
            continue
        if ratings[i] < 0:
            out.append(Verdict("bad", "rating_rejected"))
            continue
        peak = float(facts.sharpness_peak[i])
        rank = float(scores.sharp_rank[i])
        mean, contrast = float(facts.exposure_mean[i]), float(facts.contrast[i])
        clip_high = float(facts.clip_high[i])
        if contrast < cfg.misfire_contrast and (
            peak < cfg.blur_abs / 2 if calibrated else rank < cfg.misfire_sharp_rel  # type: ignore[operator]
        ):
            out.append(Verdict("bad", "misfire"))
            continue
        if not in_sequence[i]:
            if clip_high > cfg.overexp_clip_high or mean > cfg.overexp_mean:
                out.append(Verdict("bad", "overexposed", {"white": clip_high}))
                continue
            if mean < cfg.underexp_mean and contrast < cfg.underexp_contrast:
                out.append(Verdict("bad", "underexposed"))
                continue
        # people photos are only blurry if every main face is blurry as well (03 §4)
        faces_blurry = not scores.people[i] or bool(
            np.isnan(scores.face_rank_max[i]) or scores.face_rank_max[i] < cfg.blur_rel
        )
        if calibrated and peak < cfg.blur_abs and rank < cfg.blur_rel and faces_blurry:  # type: ignore[operator]
            out.append(Verdict("bad", "blur", {"rank": rank}))
            continue
        if not calibrated and rank < cfg.blur_review_rel and faces_blurry:
            out.append(Verdict("review", "blur_review", {"rank": rank}))
            continue
        out.append(None)
    return out
