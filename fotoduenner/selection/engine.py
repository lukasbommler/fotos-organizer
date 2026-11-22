"""Grouping and selection for one album (03 §5–§8). No file access: the caller loads the inputs.

Expensive parts (duplicates, sequences, series, motif dendrogram, scores) are computed once.
A grouping level is cut and ranked once and cached; mode, quota and manual decisions only
re-run the cheap decision step (< 300 ms for 2,000 photos, 05 M4).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from fotoduenner.config import Config
from fotoduenner.grouping.cluster import MotifTree, Node, build_nodes, exact_duplicates
from fotoduenner.grouping.sequences import PhotoSequence, ShiftFn, find_sequences
from fotoduenner.grouping.series import find_series, phash_ints
from fotoduenner.pipeline.embed import from_blob
from fotoduenner.pipeline.zeroshot import active_labels
from fotoduenner.selection.modes import Decisions, Layout, Settings, Unit, decide
from fotoduenner.selection.scoring import PhotoFacts, compute_scores, series_values
from fotoduenner.selection.select import Verdict, mmr, quality_verdicts


@dataclass(slots=True)
class SelectionInput:
    """Analysed photos of an album (status 'done' with features), all arrays of length N."""

    ids: np.ndarray  # photo ids (DB)
    rel_paths: list[str]
    times: np.ndarray  # seconds; EXIF time or file mtime
    timed: np.ndarray  # True where the time comes from EXIF
    cameras: list[str | None]
    biases: np.ndarray  # exposure bias in EV, NaN if unknown
    file_hashes: list[bytes | None]
    ratings: np.ndarray  # combined rating, 0 if none
    phashes: np.ndarray  # uint64
    embeddings: np.ndarray  # (N, D) float32, L2-normalised
    facts: PhotoFacts


def input_from_rows(rows: Sequence[Any], cfg: Config) -> SelectionInput:
    """Build the input from ``AlbumStore.selection_rows`` (sqlite rows or mappings)."""

    def column(name: str, default: float = np.nan) -> np.ndarray:
        return np.array([default if r[name] is None else r[name] for r in rows], dtype=np.float64)

    dim = len(from_blob(rows[0]["embedding"])) if rows else 512
    return SelectionInput(
        ids=np.array([r["id"] for r in rows], dtype=np.int64),
        rel_paths=[r["rel_path"] for r in rows],
        times=column("taken_at", 0.0),
        timed=np.array([r["taken_at_source"] == "exif" for r in rows], dtype=bool),
        cameras=[r["camera"] for r in rows],
        biases=column("exposure_bias"),
        file_hashes=[r["file_hash"] for r in rows],
        ratings=np.array([r["rating"] or 0 for r in rows], dtype=np.int64),
        phashes=phash_ints([r["phash"] for r in rows]),
        embeddings=(
            np.stack([from_blob(r["embedding"]) for r in rows]).astype(np.float32)
            if rows
            else np.zeros((0, dim), dtype=np.float32)
        ),
        facts=PhotoFacts(
            sharpness_peak=column("sharpness_peak", 0.0),
            exposure_mean=column("exposure_mean", 128.0),
            contrast=column("contrast", 50.0),
            clip_low=column("clip_low", 0.0),
            clip_high=column("clip_high", 0.0),
            aesthetic=column("aesthetic"),
            faces=[json.loads(r["faces"] or "[]") for r in rows],
            zs_active=[active_labels(json.loads(r["zeroshot"] or "{}"), cfg) for r in rows],
        ),
    )


def _no_shift(_a: int, _b: int) -> None:
    return None


class Engine:
    def __init__(self, inp: SelectionInput, cfg: Config, shift_fn: ShiftFn | None = None) -> None:
        self.inp = inp
        self.cfg = cfg
        n = self.n = len(inp.ids)
        # capture order: time, then path
        keys = [p.casefold() for p in inp.rel_paths]
        path_rank = np.argsort(np.argsort(np.array(keys, dtype=object), kind="stable"))
        self.capture_pos = np.empty(n, dtype=np.int64)
        self.capture_pos[np.lexsort((path_rank, inp.times))] = np.arange(n)

        self.duplicates = exact_duplicates(inp.file_hashes, inp.rel_paths)
        eligible = np.ones(n, dtype=bool)
        eligible[list(self.duplicates)] = False
        self.sequences: list[PhotoSequence] = find_sequences(
            inp.times, inp.timed, inp.cameras, inp.biases, eligible, shift_fn or _no_shift, cfg
        )
        self.in_sequence = np.zeros(n, dtype=bool)
        for seq in self.sequences:
            self.in_sequence[list(seq.members)] = True
        rest = np.flatnonzero(eligible & ~self.in_sequence)
        labels = (
            find_series(
                inp.times[rest], inp.timed[rest], inp.phashes[rest], inp.embeddings[rest], cfg
            )
            if len(rest)
            else np.zeros(0, dtype=np.int64)
        )
        self.nodes: list[Node] = build_nodes(rest, labels, self.sequences)
        self.tree = MotifTree(self.nodes, inp.embeddings, inp.times, inp.timed, cfg)

        self.scores = compute_scores(inp.facts, cfg)
        self.base_verdicts = quality_verdicts(
            inp.facts, self.scores, inp.ratings, self.in_sequence, cfg
        )
        self.series_value = self.scores.value.copy()
        self.series_q_sharp = self.scores.q_sharp.copy()
        for node in self.nodes:
            if node.kind == "series":
                members = list(node.members)
                value, q_sharp = series_values(members, inp.facts, self.scores, cfg)
                self.series_value[members] = value
                self.series_q_sharp[members] = q_sharp
        self.zs_hint: list[str | None] = [
            "document_sign"
            if "document_sign" in labels_
            else "screenshot"
            if "screenshot" in labels_
            else None
            for labels_ in inp.facts.zs_active
        ]
        self._layouts: dict[int, Layout] = {}

    def _sorted(self, members: list[int] | tuple[int, ...]) -> np.ndarray:
        return np.array(sorted(members, key=lambda i: self.capture_pos[i]), dtype=np.int64)

    def _protect_people(self, members: np.ndarray, verdicts: list[Verdict | None]) -> None:
        """A bad people photo without a better people photo in its group: "Bitte prüfen"."""
        people, value = self.scores.people, self.scores.value
        for i in members:
            verdict = verdicts[i]
            if not (verdict and verdict.kind == "bad" and people[i]):
                continue
            if verdict.code == "rating_rejected":  # rejected by the user in Lightroom
                continue
            others = [int(j) for j in members if j != i and people[j]]
            better = [
                j
                for j in others
                if (verdicts[j] is None or verdicts[j].kind != "bad")  # type: ignore[union-attr]
                and value[j] >= value[i]
            ]
            if not better:
                verdicts[i] = Verdict(
                    "review", "person_quality", {"only": not others, "problem": verdict.code}
                )

    def _rank(
        self, kind: str, members: np.ndarray, values: np.ndarray, verdicts: list[Verdict | None]
    ) -> tuple[Unit, np.ndarray, np.ndarray]:
        cand = np.array(
            [i for i in members if verdicts[i] is None or verdicts[i].kind != "bad"],  # type: ignore[union-attr]
            dtype=np.int64,
        )
        order, marginal = mmr(
            values[cand], self.inp.embeddings[cand], self.scores.face_count_main[cand], self.cfg
        )
        return Unit(kind, members, cand[order]), cand, marginal

    def layout(self, level: int) -> Layout:
        level = min(max(0, level), len(self.cfg.motif_thresholds) - 1)
        cached = self._layouts.get(level)
        if cached is not None:
            return cached
        n, scores = self.n, self.scores
        verdicts = list(self.base_verdicts)
        node_groups = self.tree.groups(self.cfg.motif_thresholds[level])
        group_members = [
            self._sorted([i for k in group for i in self.nodes[k].members]) for group in node_groups
        ]
        for members in group_members:
            self._protect_people(members, verdicts)

        groups: list[Unit] = []
        group_of = np.full(n, -1, dtype=np.int64)
        marginal = np.zeros(n)
        for node_idx, members in zip(node_groups, group_members, strict=True):
            kind = self.nodes[node_idx[0]].kind if len(node_idx) == 1 else "motif"
            unit, cand, marg = self._rank(kind, members, scores.value, verdicts)
            marginal[cand] = marg
            group_of[members] = len(groups)
            groups.append(unit)

        series: list[Unit] = []
        series_of = np.full(n, -1, dtype=np.int64)
        for node in self.nodes:
            if node.kind != "series":
                continue
            members = self._sorted(node.members)
            unit, _, _ = self._rank("series", members, self.series_value, verdicts)
            series_of[members] = len(series)
            series.append(unit)

        q_parts = np.stack([self.series_q_sharp, scores.q_exp, scores.q_aes, scores.q_face], 1)
        layout = Layout(
            n=n,
            ratings=self.inp.ratings,
            people=scores.people,
            face_count=scores.face_count_main,
            eyes_closed=scores.eyes_closed,
            embeddings=self.inp.embeddings,
            value=scores.value,
            series_value=self.series_value,
            q_aes=scores.q_aes,
            q_parts=q_parts,
            zs_hint=self.zs_hint,
            duplicates=self.duplicates,
            in_sequence=self.in_sequence,
            verdicts=verdicts,
            series=series,
            series_of=series_of,
            groups=groups,
            group_of=group_of,
            marginal=marginal,
        )
        self._layouts[level] = layout
        return layout

    def decide(self, settings: Settings, manual: Mapping[int, str] | None = None) -> Decisions:
        """Decisions for ``settings``; ``manual`` maps photo *index* to 'keep' / 'reject'."""
        return decide(self.layout(settings.level), settings, manual or {}, self.cfg)
