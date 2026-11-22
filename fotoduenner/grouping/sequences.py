"""Exposure brackets and panorama sequences (03 §5.0, A2). Pure functions, no I/O.

Candidates are runs of consecutive shots of the same camera with gaps <= ``seq_max_gap`` and at
least ``seq_min_len`` photos. A run is a bracket when its exposure bias varies; panoramas are
the longest stretches of a run whose neighbours are shifted 15–70 % sideways (or up/down) in
the same direction, measured by phase correlation on small greyscale previews. The caller
supplies those shifts through ``shift_fn`` so that this module stays free of file access.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import cv2
import numpy as np

from fotoduenner.config import Config

SequenceKind = Literal["bracket", "pano"]
Shift = tuple[float, float, float]  # dx, dy relative to the width/height, correlation peak
ShiftFn = Callable[[int, int], Shift | None]


@dataclass(frozen=True, slots=True)
class PhotoSequence:
    kind: SequenceKind
    members: tuple[int, ...]  # photo indices in capture order


def candidate_runs(
    times: np.ndarray,
    timed: np.ndarray,
    cameras: Sequence[str | None],
    eligible: np.ndarray,
    cfg: Config,
) -> list[list[int]]:
    """Runs of consecutive shots per camera (gap <= seq_max_gap, length >= seq_min_len)."""
    by_camera: dict[str | None, list[int]] = {}
    for i in np.flatnonzero(timed & eligible):
        by_camera.setdefault(cameras[i], []).append(int(i))
    runs: list[list[int]] = []
    for idx in by_camera.values():
        idx.sort(key=lambda i: (times[i], i))
        run = [idx[0]]
        for prev, cur in pairwise(idx):
            if times[cur] - times[prev] <= cfg.seq_max_gap:
                run.append(cur)
                continue
            if len(run) >= cfg.seq_min_len:
                runs.append(run)
            run = [cur]
        if len(run) >= cfg.seq_min_len:
            runs.append(run)
    return runs


def is_bracket(biases: np.ndarray, cfg: Config) -> bool:
    """At least two different exposure-bias values spanning >= bracket_min_ev_span EV."""
    known = biases[~np.isnan(biases)]
    if len(known) < 2:
        return False
    return len(np.unique(np.round(known, 2))) >= 2 and float(np.ptp(known)) >= (
        cfg.bracket_min_ev_span - 1e-6
    )


def phase_shift(a: np.ndarray, b: np.ndarray) -> Shift | None:
    """Shift of ``b`` against ``a`` (relative to the size) and the correlation peak.

    Phase correlation is periodic: shifts beyond half the frame alias to the other side. Real
    panorama steps of 15–50 % are therefore measured reliably, larger ones fail the direction
    check and the photos simply stay normal photos (kept or thinned like any others).
    """
    if a.shape != b.shape or a.ndim != 2:
        return None
    fa = a.astype(np.float32)
    fb = b.astype(np.float32)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), peak = cv2.phaseCorrelate(fa, fb, window)
    return dx / a.shape[1], dy / a.shape[0], float(peak)


def _pano_step(shift: Shift | None, cfg: Config) -> tuple[str, int] | None:
    """Axis and direction of a panorama step, None if the pair is no panorama step."""
    if shift is None:
        return None
    dx, dy, peak = shift
    if peak < cfg.pano_min_peak:
        return None
    axis, value = ("x", dx) if abs(dx) >= abs(dy) else ("y", dy)
    if not cfg.pano_shift_min <= abs(value) <= cfg.pano_shift_max:
        return None
    return axis, 1 if value > 0 else -1


def pano_stretches(run: list[int], shift_fn: ShiftFn, cfg: Config) -> list[list[int]]:
    """Longest stretches of ``run`` whose neighbour steps share axis and direction."""
    stretches: list[list[int]] = []
    current: list[int] = [run[0]]
    direction: tuple[str, int] | None = None
    for prev, cur in pairwise(run):
        step = _pano_step(shift_fn(prev, cur), cfg)
        if step is not None and (direction is None or step == direction):
            current.append(cur)
            direction = step
            continue
        if len(current) >= cfg.seq_min_len:
            stretches.append(current)
        current = [prev, cur] if step is not None else [cur]
        direction = step
    if len(current) >= cfg.seq_min_len:
        stretches.append(current)
    return stretches


def find_sequences(
    times: np.ndarray,
    timed: np.ndarray,
    cameras: Sequence[str | None],
    biases: np.ndarray,
    eligible: np.ndarray,
    shift_fn: ShiftFn,
    cfg: Config,
) -> list[PhotoSequence]:
    """All brackets and panoramas among ``eligible`` photos; a photo is in at most one."""
    found: list[PhotoSequence] = []
    for run in candidate_runs(times, timed, cameras, eligible, cfg):
        if is_bracket(biases[run], cfg):
            found.append(PhotoSequence("bracket", tuple(run)))
            continue
        found.extend(PhotoSequence("pano", tuple(s)) for s in pano_stretches(run, shift_fn, cfg))
    return found
