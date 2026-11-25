"""Selection modes "clean" and "quota" (03 §8, D5, D12). Pure functions, no I/O.

Automatic decisions never look at manual ones; manual decisions are laid over them at the end.
That way a click can never change the decision of another photo (T1, A28) – also not through
rounding of the quota budget.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from fotoduenner.config import Config, Mode
from fotoduenner.selection.reasons import Reason, dominant_aspect
from fotoduenner.selection.select import Verdict

OPEN, KEEP, REJECT = 0, 1, -1


@dataclass(frozen=True, slots=True)
class Settings:
    mode: Mode = "clean"
    keep_target: float = 0.50  # quota: share of photos to keep
    level: int = 2  # grouping strength, index into cfg.motif_thresholds
    low_singles: bool = False  # quota: also sort out single photos of low value


@dataclass(slots=True)
class Unit:
    """A series (level 1) or a motif group (level 2) with its MMR ranking."""

    kind: str  # series | bracket | pano | motif | single
    members: np.ndarray  # photo indices in capture order
    order: np.ndarray  # members not sorted out for quality, in MMR order


@dataclass(slots=True)
class Layout:
    """Everything the modes need for one grouping level; independent of manual decisions."""

    n: int
    ratings: np.ndarray
    people: np.ndarray
    face_count: np.ndarray
    eyes_closed: np.ndarray
    embeddings: np.ndarray
    value: np.ndarray  # album-wide photo value
    series_value: np.ndarray  # value inside the own series (native face sharpness, T6)
    q_aes: np.ndarray
    q_parts: np.ndarray  # (n, 4): sharp (series-adjusted), exposure, aesthetic, face
    zs_hint: list[str | None]  # screenshot / document_sign, for the reason text
    duplicates: dict[int, int]  # duplicate -> original
    in_sequence: np.ndarray
    verdicts: list[Verdict | None]  # quality, after the people protection of this level
    series: list[Unit]
    series_of: np.ndarray  # index into series, -1 if none
    groups: list[Unit]  # motif groups incl. single photos; duplicates are in none
    group_of: np.ndarray  # index into groups, -1 for duplicates
    marginal: np.ndarray  # MMR marginal value inside the motif group


@dataclass(slots=True)
class Decisions:
    keep: np.ndarray  # final, manual decisions applied
    auto_keep: np.ndarray
    review: np.ndarray  # "Bitte prüfen" (kept, yellow mark), not yet decided by the user
    auto_review: np.ndarray  # the same before manual decisions
    reasons: list[Reason | None]
    keep_floor: float | None = None  # quota: share that the protection rules keep anyway
    target: int | None = None  # quota: photos to keep automatically
    stats: dict[str, int] = field(default_factory=dict)


def _fixed(layout: Layout) -> tuple[np.ndarray, list[Reason | None], np.ndarray, np.ndarray]:
    """03 §8.0 steps 2–5: rating, exact copies, sequences, quality."""
    n = layout.n
    state = np.full(n, OPEN, dtype=np.int8)
    reasons: list[Reason | None] = [None] * n
    hard_review = np.zeros(n, dtype=bool)
    soft_review = np.zeros(n, dtype=bool)
    state[layout.ratings >= 1] = KEEP
    for dup, orig in layout.duplicates.items():
        if state[dup] == OPEN:
            state[dup] = REJECT
            reasons[dup] = Reason("duplicate", orig)
    state[(state == OPEN) & layout.in_sequence] = KEEP
    for i, verdict in enumerate(layout.verdicts):
        if verdict is None or state[i] != OPEN:
            continue
        if verdict.kind == "bad":
            state[i] = REJECT
            reasons[i] = Reason(verdict.code, None, dict(verdict.params))
        elif verdict.code == "person_quality":  # protected people photo: kept, to be checked
            state[i] = KEEP
            hard_review[i] = True
            reasons[i] = Reason(verdict.code, None, dict(verdict.params))
        else:  # ASSUMPTION: uncalibrated blur only protects from a *quality* rejection (T5)
            soft_review[i] = True
            reasons[i] = Reason(verdict.code, None, dict(verdict.params))
    return state, reasons, hard_review, soft_review


def _most_similar(layout: Layout, i: int, kept: list[int]) -> int:
    sims = layout.embeddings[kept] @ layout.embeddings[i]
    return kept[int(np.argmax(sims))]


def _series_reason(layout: Layout, i: int, kept: list[int], cfg: Config) -> Reason:
    ref = _most_similar(layout, i, kept)
    parts = dict(zip(("sharp", "exposure", "aesthetic", "face"), layout.q_parts[i], strict=True))
    ref_parts = dict(
        zip(("sharp", "exposure", "aesthetic", "face"), layout.q_parts[ref], strict=True)
    )
    closed = bool(layout.eyes_closed[i] and not layout.eyes_closed[ref])
    aspect = dominant_aspect(parts, ref_parts, bool(layout.people[i]), closed, cfg)
    return Reason("series", ref, {"aspect": aspect})


def _distinct(layout: Layout, i: int, kept: list[int], cfg: Config) -> bool:
    """Brings something of its own: different enough or another constellation of people."""
    if not kept:
        return True
    if float(np.max(layout.embeddings[kept] @ layout.embeddings[i])) < cfg.clean_distinct_cos:
        return True
    return all(layout.face_count[i] != layout.face_count[k] for k in kept)


def _clean(layout: Layout, state: np.ndarray, reasons: list[Reason | None], cfg: Config) -> None:
    """Thin out series only (03 §8.1, D12); motif groups and single photos stay untouched."""
    for unit in layout.series:
        kept = [int(i) for i in unit.order if state[i] == KEEP]
        for pos, i in enumerate(int(i) for i in unit.order):
            if state[i] != OPEN:
                continue
            if pos == 0 or _distinct(layout, i, kept, cfg):  # rank 0 always stays
                state[i] = KEEP
                kept.append(i)
            else:
                state[i] = REJECT
                reasons[i] = _series_reason(layout, i, kept, cfg)
        _protect_people(layout, unit, state, reasons, layout.series_value)
    state[state == OPEN] = KEEP


def _protect_people(
    layout: Layout,
    unit: Unit,
    state: np.ndarray,
    reasons: list[Reason | None],
    values: np.ndarray,
) -> None:
    """A unit with people photos keeps at least its best people photo (03 §8.1, §4)."""
    people = [int(i) for i in unit.order if layout.people[i] and state[i] != REJECT]
    people += [
        int(i)
        for i in unit.order
        if layout.people[i] and state[i] == REJECT and reasons[i] and reasons[i].code == "series"  # type: ignore[union-attr]
    ]
    if not people or any(state[i] == KEEP for i in people):
        return
    best = max(people, key=lambda i: (values[i], -i))
    state[best] = KEEP
    reasons[best] = None


def _priority(layout: Layout, i: int, cfg: Config) -> float:
    a, b = cfg.aesthetic_priority
    boost = 1.0 + cfg.people_priority_boost * float(layout.people[i])
    return float(layout.marginal[i]) * boost * (a + b * float(layout.q_aes[i]))


def _low_single(layout: Layout, i: int, cfg: Config) -> bool:
    return (
        not layout.people[i]
        and layout.value[i] < cfg.low_single_value
        and layout.q_aes[i] < cfg.low_single_aesthetic_rank
    )


def _quota(
    layout: Layout,
    state: np.ndarray,
    reasons: list[Reason | None],
    settings: Settings,
    cfg: Config,
) -> tuple[float, int]:
    """Global target share (03 §8.2) with mandatory keeps, budget and priority fill."""
    low_single = np.zeros(layout.n, dtype=bool)
    for unit in layout.groups:
        order = [int(i) for i in unit.order]
        if len(unit.members) == 1:
            i = int(unit.members[0])
            if state[i] != OPEN:
                continue
            if settings.low_singles and _low_single(layout, i, cfg):
                low_single[i] = True
            else:
                state[i] = KEEP
            continue
        if order and state[order[0]] == OPEN:
            state[order[0]] = KEEP
        if (
            len(unit.members) >= cfg.people_second_min_group
            and len(order) >= 2
            and layout.people[order[1]]
            and state[order[1]] == OPEN
        ):
            state[order[1]] = KEEP
        # ASSUMPTION: like in a series, a group with people photos keeps its best one (05 M4)
        people = [i for i in order if layout.people[i] and state[i] != REJECT]
        if people and not any(state[i] == KEEP for i in people):
            state[max(people, key=lambda i: (layout.value[i], -i))] = KEEP

    kept_auto = int((state == KEEP).sum())
    target = int(settings.keep_target * layout.n + 0.5)
    budget = max(0, target - kept_auto)
    candidates = [int(i) for i in np.flatnonzero(state == OPEN)]
    candidates.sort(key=lambda i: (-_priority(layout, i, cfg), i))
    for i in candidates[:budget]:
        state[i] = KEEP
    rejected = candidates[budget:]
    for i in rejected:
        state[i] = REJECT
    for i in rejected:
        reasons[i] = _quota_reason(layout, i, state, bool(low_single[i]), cfg)
    return kept_auto / max(1, layout.n), target


def _quota_reason(
    layout: Layout, i: int, state: np.ndarray, low_single: bool, cfg: Config
) -> Reason:
    if low_single:
        return Reason("low_single")
    s = int(layout.series_of[i])
    if s >= 0:
        kept = [int(k) for k in layout.series[s].members if state[k] == KEEP]
        if kept:
            return _series_reason(layout, i, kept, cfg)
    g = int(layout.group_of[i])
    kept = [int(k) for k in layout.groups[g].members if state[k] == KEEP]
    ref = _most_similar(layout, i, kept) if kept else None
    return Reason("motif", ref, {"kept": len(kept), "zs": layout.zs_hint[i]})


def decide(layout: Layout, settings: Settings, manual: Mapping[int, str], cfg: Config) -> Decisions:
    """Automatic decisions for ``settings``, then manual ones ('keep'/'reject') on top."""
    state, reasons, hard_review, soft_review = _fixed(layout)
    keep_floor: float | None = None
    target: int | None = None
    if settings.mode == "quota":
        keep_floor, target = _quota(layout, state, reasons, settings, cfg)
    else:
        _clean(layout, state, reasons, cfg)
    auto_keep = state == KEEP
    review = hard_review | (soft_review & auto_keep)
    for i in np.flatnonzero(soft_review & auto_keep):
        if reasons[i] is None or reasons[i].code != "blur_review":  # type: ignore[union-attr]
            reasons[i] = Reason("blur_review")
    for i in np.flatnonzero(soft_review & ~auto_keep):
        if reasons[i] is not None and reasons[i].code == "blur_review":  # type: ignore[union-attr]
            reasons[i] = None
    auto_review = review.copy()
    keep = auto_keep.copy()
    for i, choice in manual.items():
        if choice == "keep":
            keep[i] = True
            review[i] = False
            reasons[i] = None
        elif choice == "reject":
            keep[i] = False
            review[i] = False
            reasons[i] = Reason("manual")
    for i in np.flatnonzero(keep):
        if not review[i]:
            reasons[i] = None
    return Decisions(
        keep=keep,
        auto_keep=auto_keep,
        review=review,
        auto_review=auto_review,
        reasons=reasons,
        keep_floor=keep_floor,
        target=target,
        stats=_stats(keep, reasons, review),
    )


def hold(n: int, manual: Mapping[int, str]) -> Decisions:
    """Album already applied (T2): every photo left is kept until "Erneut ausdünnen".

    Manual decisions still count, so single photos can be sorted out and applied again.
    """
    keep = np.ones(n, dtype=bool)
    reasons: list[Reason | None] = [None] * n
    for i, choice in manual.items():
        if choice == "reject":
            keep[i] = False
            reasons[i] = Reason("manual")
    review = np.zeros(n, dtype=bool)
    return Decisions(
        keep=keep,
        auto_keep=np.ones(n, dtype=bool),
        review=review,
        auto_review=review.copy(),
        reasons=reasons,
        stats=_stats(keep, reasons, review),
    )


CATEGORY = {
    "duplicate": "duplicate",
    "series": "series",
    "motif": "motif",
    "low_single": "motif",
    "manual": "manual",
}


def _stats(keep: np.ndarray, reasons: list[Reason | None], review: np.ndarray) -> dict[str, int]:
    stats = {"total": len(keep), "keep": int(keep.sum()), "reject": int((~keep).sum())}
    stats["review"] = int(review.sum())
    for key in ("duplicate", "series", "motif", "quality", "manual"):
        stats[f"reject_{key}"] = 0
    for i in np.flatnonzero(~keep):
        reason = reasons[i]
        category = CATEGORY.get(reason.code, "quality") if reason else "manual"
        stats[f"reject_{category}"] += 1
    return stats
