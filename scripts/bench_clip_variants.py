"""T11: CLIP throughput – one session per worker (1 thread, batch 1) vs. a separate batch stage.

    uv run python scripts/bench_clip_variants.py [--cores 3] [--n 96]

Variant A (built): ``cores`` processes, each with its own 1-thread session, batch 1.
Variant B (only built if measurably faster): one process, ``cores`` intra-op threads, batch 16.
Inputs are random tensors (CLIP cost does not depend on content). Prints images per second.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fotoduenner.paths import models_dir
from fotoduenner.pipeline.models import CLIP_FILE, onnx_session


def _worker(n: int) -> float:
    session = onnx_session((models_dir() / CLIP_FILE).read_bytes(), threads=1)
    x = np.random.default_rng(0).standard_normal((1, 3, 224, 224)).astype(np.float32)
    session.run(None, {"pixel_values": x})  # warm-up
    start = time.perf_counter()
    for _ in range(n):
        session.run(None, {"pixel_values": x})
    return time.perf_counter() - start


def variant_a(cores: int, n: int) -> float:
    per = n // cores
    with ProcessPoolExecutor(cores) as pool:
        start = time.perf_counter()
        list(pool.map(_worker, [per] * cores))
        wall = time.perf_counter() - start
    return per * cores / wall


def variant_b(cores: int, n: int, batch: int = 16) -> float:
    session = onnx_session((models_dir() / CLIP_FILE).read_bytes(), threads=cores)
    x = np.random.default_rng(0).standard_normal((batch, 3, 224, 224)).astype(np.float32)
    session.run(None, {"pixel_values": x})
    rounds = max(1, n // batch)
    start = time.perf_counter()
    for _ in range(rounds):
        session.run(None, {"pixel_values": x})
    return rounds * batch / (time.perf_counter() - start)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cores", type=int, default=3)
    parser.add_argument("--n", type=int, default=96)
    args = parser.parse_args()
    a = variant_a(args.cores, args.n)
    b = variant_b(args.cores, args.n)
    print(f"A  {args.cores} workers x 1 thread, batch 1 : {a:5.1f} img/s ({1000 / a:.0f} ms/img)")
    print(f"B  1 stage  x {args.cores} threads, batch 16: {b:5.1f} img/s ({1000 / b:.0f} ms/img)")
    print(f"B/A = {b / a:.2f}")


if __name__ == "__main__":
    main()
