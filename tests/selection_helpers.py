"""Synthetic albums for the grouping and selection tests (05 M4): features only, no images."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from fotoduenner.selection.engine import SelectionInput
from fotoduenner.selection.scoring import PhotoFacts

DIM = 512
DAY = 86400.0
T0 = 1_750_000_000.0  # some day in June 2025, 10:13 UTC


def scene(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


def jitter(base: np.ndarray, eps: float, rng: np.random.Generator) -> np.ndarray:
    """cos(base, result) ≈ 1 / sqrt(1 + eps²); two results ≈ 1 / (1 + eps²)."""
    v = base + rng.normal(size=DIM) * eps / np.sqrt(DIM)
    return v / np.linalg.norm(v)


def flip_bits(value: int, count: int, rng: np.random.Generator) -> int:
    for bit in rng.choice(64, size=count, replace=False):
        value ^= 1 << int(bit)
    return value


def face(size: float = 0.12, sharp: float = 200.0, eyes: float | None = 0.9, **kw) -> dict:
    data = {
        "size_rel": size,
        "conf": 0.95,
        "sharpness": sharp,
        "sharpness_native": None,
        "eyes_open": eyes,
    }
    data.update(kw)
    return data


_counter = itertools.count()


class each(list):  # lower case: reads like a keyword in the tests
    """Per-photo values for Album.series / Album.motif: each([a, b, c])."""


def _pick(kw: dict, k: int) -> dict:
    return {key: (v[k] if isinstance(v, each) else v) for key, v in kw.items()}


@dataclass
class P:
    t: float
    emb: np.ndarray
    phash: int
    sharp: float = 100.0
    mean: float = 120.0
    contrast: float = 50.0
    clip_low: float = 0.01
    clip_high: float = 0.01
    aes: float = 5.0
    faces: list[dict] = field(default_factory=list)
    zs: set[str] = field(default_factory=set)
    rating: int = 0
    file_hash: bytes | None = None
    camera: str | None = "Cam"
    bias: float = float("nan")
    timed: bool = True
    path: str | None = None


def build(photos: list[P]) -> SelectionInput:
    n = len(photos)
    paths = [p.path or f"IMG_{next(_counter):05d}.jpg" for p in photos]
    return SelectionInput(
        ids=np.arange(1, n + 1, dtype=np.int64),
        rel_paths=paths,
        times=np.array([p.t for p in photos], dtype=np.float64),
        timed=np.array([p.timed for p in photos], dtype=bool),
        cameras=[p.camera for p in photos],
        biases=np.array([p.bias for p in photos], dtype=np.float64),
        file_hashes=[
            p.file_hash or f"h{path}".encode() for p, path in zip(photos, paths, strict=True)
        ],
        ratings=np.array([p.rating for p in photos], dtype=np.int64),
        phashes=np.array([p.phash for p in photos], dtype=np.uint64),
        embeddings=np.array([p.emb for p in photos], dtype=np.float32).reshape(n, DIM),
        facts=PhotoFacts(
            sharpness_peak=np.array([p.sharp for p in photos], dtype=np.float64),
            exposure_mean=np.array([p.mean for p in photos], dtype=np.float64),
            contrast=np.array([p.contrast for p in photos], dtype=np.float64),
            clip_low=np.array([p.clip_low for p in photos], dtype=np.float64),
            clip_high=np.array([p.clip_high for p in photos], dtype=np.float64),
            aesthetic=np.array([p.aes for p in photos], dtype=np.float64),
            faces=[p.faces for p in photos],
            zs_active=[set(p.zs) for p in photos],
        ),
    )


class Album:
    """Builder: each helper appends photos and returns their indices."""

    def __init__(self, seed: int = 1) -> None:
        self.rng = np.random.default_rng(seed)
        self.photos: list[P] = []
        self._scene = 1000 * seed

    def _new_scene(self) -> np.ndarray:
        self._scene += 1
        return scene(self._scene)

    def add(self, photo: P) -> int:
        self.photos.append(photo)
        return len(self.photos) - 1

    def single(self, t: float, **kw) -> int:
        kw.setdefault("sharp", float(self.rng.uniform(80, 200)))
        kw.setdefault("aes", float(self.rng.uniform(4, 6)))
        return self.add(P(t=t, emb=self._new_scene(), phash=int(self.rng.integers(0, 2**63)), **kw))

    def series(self, t: float, count: int, step: float = 2.0, eps: float = 0.08, **kw) -> list[int]:
        """Near-identical burst: same pHash ± 2 bits, cos ≈ 0.99."""
        base = self._new_scene()
        ph = int(self.rng.integers(0, 2**63))
        out = []
        for k in range(count):
            extra = _pick(kw, k)
            extra.setdefault("sharp", float(self.rng.uniform(80, 200)))
            out.append(
                self.add(
                    P(
                        t=t + k * step,
                        emb=jitter(base, eps, self.rng),
                        phash=flip_bits(ph, 2, self.rng),
                        **extra,
                    )
                )
            )
        return out

    def motif(self, t: float, count: int, step: float = 600.0, eps: float = 0.3, **kw) -> list[int]:
        """Same subject, minutes apart, different framing: no series, one motif group."""
        base = self._new_scene()
        out = []
        for k in range(count):
            extra = _pick(kw, k)
            extra.setdefault("sharp", float(self.rng.uniform(80, 200)))
            extra.setdefault("aes", float(self.rng.uniform(4, 6)))
            out.append(
                self.add(
                    P(
                        t=t + k * step,
                        emb=jitter(base, eps, self.rng),
                        phash=int(self.rng.integers(0, 2**63)),
                        **extra,
                    )
                )
            )
        return out

    def background(self, count: int, start: float = T0 + 5 * DAY) -> list[int]:
        """Unrelated single photos on other days, so that album ranks are meaningful."""
        return [self.single(start + k * 3600.0) for k in range(count)]

    def build(self) -> SelectionInput:
        return build(self.photos)
