"""Zero-shot labels (03 §3.8, T7, D13) from pre-computed CLIP text embeddings.

Score per label = softmax over (label ensemble, neutral counter prompts) at CLIP temperature.
Labels only ever add hints or a "Bitte prüfen"; they are never the sole reason to sort out.
"""

from __future__ import annotations

import numpy as np

from fotoduenner.config import Config
from fotoduenner.pipeline.models import ZeroShotText


def scores(embedding: np.ndarray, text: ZeroShotText) -> dict[str, float]:
    """Probability of each label against the counter prompts (0..1)."""
    label_logits = text.logit_scale * (text.label_embeds @ embedding)  # (L,)
    counter_logits = text.logit_scale * (text.counter_embeds @ embedding)  # (C,)
    result: dict[str, float] = {}
    for name, logit in zip(text.labels, label_logits, strict=True):
        logits = np.concatenate(([logit], counter_logits))
        logits -= logits.max()
        probs = np.exp(logits)
        result[name] = round(float(probs[0] / probs.sum()), 4)
    return result


def active_labels(zs: dict[str, float] | None, cfg: Config) -> set[str]:
    """Labels that are switched on and reach their threshold."""
    if not zs:
        return set()
    return {
        name
        for name, score in zs.items()
        if cfg.zs_enabled.get(name, False) and score >= cfg.zs_threshold.get(name, 1.0)
    }
