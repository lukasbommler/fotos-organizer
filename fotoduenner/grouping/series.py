"""Level 1 – series of near-identical shots (03 §5.1). Pure functions, no I/O.

Link i and j if (pHash Hamming <= 8 and |dt| <= 120 s) or (cosine >= 0.95 and |dt| <= 300 s).
Photos without EXIF time only link by pHash (Hamming <= 5), with no time condition.
Connected components (union-find) are the series.
"""

from __future__ import annotations

import numpy as np

from fotoduenner.config import Config


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def labels(self) -> np.ndarray:
        return np.array([self.find(i) for i in range(len(self.parent))], dtype=np.int64)


def _popcount64(x: np.ndarray) -> np.ndarray:
    return np.bitwise_count(x).astype(np.int64)


def phash_ints(hashes: list[bytes]) -> np.ndarray:
    return np.array([int.from_bytes(h, "big") for h in hashes], dtype=np.uint64)


def find_series(
    times: np.ndarray,
    timed: np.ndarray,
    phashes: np.ndarray,
    embeddings: np.ndarray,
    cfg: Config,
) -> np.ndarray:
    """Series label per photo (the smallest index of its component).

    ``times`` seconds (any epoch), ``timed`` True where the time comes from EXIF,
    ``phashes`` uint64 per photo, ``embeddings`` (N, D) float32 L2-normalised.
    """
    n = len(times)
    uf = UnionFind(n)
    window = max(cfg.series_phash_dt, cfg.series_cos_dt)
    timed_idx = np.flatnonzero(timed)
    order = timed_idx[np.argsort(times[timed_idx], kind="stable")]
    sorted_times = times[order]
    for pos, i in enumerate(order):
        end = int(np.searchsorted(sorted_times, sorted_times[pos] + window, side="right"))
        if end <= pos + 1:
            continue
        others = order[pos + 1 : end]
        dt = sorted_times[pos + 1 : end] - sorted_times[pos]
        ham = _popcount64(phashes[others] ^ phashes[i])
        cos = embeddings[others] @ embeddings[i]
        linked = ((ham <= cfg.phash_series_max) & (dt <= cfg.series_phash_dt)) | (
            (cos >= cfg.series_cos_min) & (dt <= cfg.series_cos_dt)
        )
        for j in others[linked]:
            uf.union(int(i), int(j))
    for i in np.flatnonzero(~timed):  # no capture time: pHash only, stricter, no time window
        ham = _popcount64(phashes ^ phashes[i])
        for j in np.flatnonzero(ham <= cfg.phash_no_time_max):
            if j != i:
                uf.union(int(i), int(j))
    return uf.labels()


def series_members(labels: np.ndarray) -> np.ndarray:
    """Boolean mask: photo belongs to a series with at least two photos."""
    _, inverse, counts = np.unique(labels, return_inverse=True, return_counts=True)
    return counts[inverse] >= 2
