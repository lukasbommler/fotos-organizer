"""Exact duplicates, nodes and level 2 – motif groups (03 §5.0, §5.2). Pure functions, no I/O.

Nodes are series (level 1, also single photos) and sequences. Their distance is
``(1 - cos) + alpha * min(1, |dt| / T_max) + beta * [other calendar day]`` on the normalised
mean embedding and the median time. Average linkage is computed once (``MotifTree``); the
grouping slider only cuts the tree again. Groups with more than ``group_max_size`` photos or a
time span over ``group_max_span`` are cut again at ``theta * 0.7``, at most three times.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage

from fotoduenner.config import Config
from fotoduenner.grouping.sequences import PhotoSequence

NodeKind = Literal["series", "single", "bracket", "pano"]
DAY = 86400.0


def exact_duplicates(
    file_hashes: Sequence[bytes | None], rel_paths: Sequence[str]
) -> dict[int, int]:
    """Duplicate index -> index of the original (first by relative path, case-insensitive)."""
    by_hash: dict[bytes, list[int]] = {}
    for i, digest in enumerate(file_hashes):
        if digest:
            by_hash.setdefault(digest, []).append(i)
    result: dict[int, int] = {}
    for members in by_hash.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: (rel_paths[i].casefold(), rel_paths[i]))
        for dup in members[1:]:
            result[dup] = members[0]
    return result


@dataclass(frozen=True, slots=True)
class Node:
    kind: NodeKind
    members: tuple[int, ...]  # photo indices


def build_nodes(
    indices: np.ndarray,
    series_labels: np.ndarray,
    sequences: Sequence[PhotoSequence],
) -> list[Node]:
    """Nodes for the motif clustering: one per series (or single photo) and per sequence.

    ``series_labels`` belongs to ``indices`` (photos outside sequences); sequences keep their
    own members.
    """
    by_label: dict[int, list[int]] = {}
    for idx, label in zip(indices.tolist(), series_labels.tolist(), strict=True):
        by_label.setdefault(label, []).append(idx)
    nodes = [
        Node("series" if len(members) > 1 else "single", tuple(members))
        for members in by_label.values()
    ]
    nodes += [Node(seq.kind, seq.members) for seq in sequences]
    return nodes


def node_features(
    nodes: Sequence[Node], embeddings: np.ndarray, times: np.ndarray, timed: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalised mean embedding, median time and 'has a time' per node."""
    emb = np.zeros((len(nodes), embeddings.shape[1]), dtype=np.float32)
    med = np.zeros(len(nodes), dtype=np.float64)
    has = np.zeros(len(nodes), dtype=bool)
    for k, node in enumerate(nodes):
        members = np.array(node.members)
        mean = embeddings[members].mean(axis=0)
        emb[k] = mean / max(float(np.linalg.norm(mean)), 1e-12)
        with_time = members[timed[members]]
        if len(with_time):
            med[k] = float(np.median(times[with_time]))
            has[k] = True
    return emb, med, has


def motif_distances(emb: np.ndarray, med: np.ndarray, has: np.ndarray, cfg: Config) -> np.ndarray:
    """Condensed distance vector (scipy order) between nodes."""
    n = len(emb)
    rows, cols = np.triu_indices(n, k=1)
    cos = (emb @ emb.T)[rows, cols]  # one matrix product, no (pairs x dim) copies
    dt = np.abs(med[rows] - med[cols])
    both = has[rows] & has[cols]
    time_term = np.where(both, np.minimum(1.0, dt / cfg.motif_tmax), 1.0)
    other_day = np.where(both, np.floor(med[rows] / DAY) != np.floor(med[cols] / DAY), True)
    dist = (1.0 - cos) + cfg.motif_alpha * time_term + cfg.motif_day_penalty * other_day
    return np.maximum(dist, 0.0)


class MotifTree:
    """Average-linkage dendrogram over the nodes; cutting it is cheap."""

    def __init__(
        self,
        nodes: Sequence[Node],
        embeddings: np.ndarray,
        times: np.ndarray,
        timed: np.ndarray,
        cfg: Config,
    ) -> None:
        self.nodes = list(nodes)
        self.cfg = cfg
        self.sizes = np.array([len(n.members) for n in self.nodes], dtype=np.int64)
        self.t_min = np.full(len(self.nodes), np.inf)
        self.t_max = np.full(len(self.nodes), -np.inf)
        for k, node in enumerate(self.nodes):
            members = np.array(node.members)
            with_time = members[timed[members]]
            if len(with_time):
                self.t_min[k] = times[with_time].min()
                self.t_max[k] = times[with_time].max()
        self.linkage: np.ndarray | None = None
        if len(self.nodes) >= 2:
            emb, med, has = node_features(self.nodes, embeddings, times, timed)
            self.linkage = linkage(motif_distances(emb, med, has, cfg), method="average")

    def _cut(self, theta: float) -> np.ndarray:
        if self.linkage is None:
            return np.zeros(len(self.nodes), dtype=np.int64)
        return fcluster(self.linkage, t=theta, criterion="distance").astype(np.int64)

    def _too_big(self, node_idx: np.ndarray) -> bool:
        if self.sizes[node_idx].sum() > self.cfg.group_max_size:
            return True
        lo, hi = self.t_min[node_idx].min(), self.t_max[node_idx].max()
        return bool(np.isfinite(lo) and hi - lo > self.cfg.group_max_span)

    def groups(self, theta: float) -> list[list[int]]:
        """Node indices per motif group, mega groups split (03 §5.2)."""
        depth = self.cfg.group_split_max_depth
        cuts = [self._cut(theta * self.cfg.group_split_factor**k) for k in range(depth + 1)]
        result: list[list[int]] = []

        def split(node_idx: np.ndarray, level: int) -> None:
            if len(node_idx) > 1 and level < depth and self._too_big(node_idx):
                labels = cuts[level + 1][node_idx]
                for label in np.unique(labels):
                    split(node_idx[labels == label], level + 1)
                return
            result.append(sorted(node_idx.tolist()))

        top = cuts[0]
        for label in np.unique(top):
            split(np.flatnonzero(top == label), 0)
        return result
