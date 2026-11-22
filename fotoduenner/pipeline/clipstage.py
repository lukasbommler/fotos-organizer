"""Separate CLIP stage (T11, A25): batched embeddings in the main process, then aesthetic and
zero-shot scores, then the DB write.

Workers send a 224² uint8 crop instead of running CLIP themselves. One CLIP FP16 model in RAM
instead of one per worker, and batches of 16 run ~1.5x faster per photo than single photos.
The stage runs in its own thread so the runner keeps feeding the worker pool meanwhile.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

import onnxruntime as ort

from fotoduenner.config import Config
from fotoduenner.paths import asset_dir, models_dir
from fotoduenner.pipeline import zeroshot
from fotoduenner.pipeline.aesthetic import aesthetic_score
from fotoduenner.pipeline.analyze import Result
from fotoduenner.pipeline.embed import clip_embed_batch, to_blob
from fotoduenner.pipeline.models import (
    AESTHETIC_FILE,
    ZEROSHOT_FILE,
    ZeroShotText,
    load_clip,
    load_zeroshot,
)

log = logging.getLogger(__name__)

_DONE = object()
_cache: dict[int, ort.InferenceSession] = {}  # threads -> session, reused across analyses
_cache_lock = threading.Lock()


def _session(threads: int) -> ort.InferenceSession:
    with _cache_lock:
        if threads not in _cache:
            _cache.clear()  # keep at most one CLIP model in memory
            _cache[threads] = load_clip(threads)
        return _cache[threads]


class ClipStage(threading.Thread):
    """Consumes worker results, completes their features, hands batches to ``save``."""

    def __init__(self, cfg: Config, threads: int, save: Callable[[list[Result]], None]) -> None:
        super().__init__(name="clip-stage", daemon=True)
        import numpy as np

        self.cfg = cfg
        self.save = save
        self.session = _session(max(1, threads))
        with np.load(models_dir() / AESTHETIC_FILE) as head:
            self.weight = head["weight"].astype(np.float32)
            self.bias = float(head["bias"].reshape(-1)[0])
        self.text: ZeroShotText = load_zeroshot(asset_dir() / ZEROSHOT_FILE)
        self.inbox: queue.Queue[object] = queue.Queue()
        self.error: BaseException | None = None
        self.seconds = 0.0
        self.count = 0

    def submit(self, result: Result) -> None:
        self.inbox.put(result)

    def close(self) -> None:
        """Process everything submitted so far, then stop. Re-raises a stage failure."""
        self.inbox.put(_DONE)
        self.join()
        if self.error is not None:
            raise RuntimeError("CLIP stage failed") from self.error

    def run(self) -> None:
        batch: list[Result] = []
        finished = False
        while not finished:
            item = self.inbox.get()
            if item is _DONE:
                finished = True
            else:
                batch.append(item)  # type: ignore[arg-type]
                # take what is already waiting, up to one batch, without blocking
                while len(batch) < self.cfg.clip_batch:
                    try:
                        nxt = self.inbox.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is _DONE:
                        finished = True
                        break
                    batch.append(nxt)  # type: ignore[arg-type]
            if batch and (finished or len(batch) >= self.cfg.clip_batch or self.inbox.empty()):
                if self.error is None:  # after a failure keep draining until close()
                    self._process(batch)
                batch = []

    def _process(self, batch: list[Result]) -> None:
        try:
            todo = [r for r in batch if r.ok and r.features and r.features.clip_input is not None]
            if todo:
                start = time.perf_counter()
                embeddings = clip_embed_batch(
                    self.session,
                    [r.features.clip_input for r in todo],  # type: ignore[union-attr]
                )
                self.seconds += time.perf_counter() - start
                self.count += len(todo)
                for res, emb in zip(todo, embeddings, strict=True):
                    feat = res.features
                    assert feat is not None
                    feat.embedding = to_blob(emb)
                    feat.aesthetic = aesthetic_score(emb, self.weight, self.bias)
                    feat.zeroshot = zeroshot.scores(emb, self.text)
                    feat.clip_input = None
                    res.timings["clip"] = (time.perf_counter() - start) / len(todo)
            self.save(batch)
        except BaseException as exc:  # never hang the runner: remember, re-raise in close()
            log.exception("CLIP stage failed")
            self.error = exc
