"""Aesthetic score (03 §3.7): LAION Aesthetic Predictor v1, linear head on the CLIP embedding.

A coarse estimate (≈ 1–10): good at telling dull/failed shots from nice ones, weak at fine
differences within a series – it gets a low weight there (03 §6).
"""

from __future__ import annotations

import numpy as np


def aesthetic_score(embedding: np.ndarray, weight: np.ndarray, bias: float) -> float:
    return float(embedding @ weight + bias)
