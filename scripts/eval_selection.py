"""Grouping and selection on a real folder: timings and a summary (05 M4, development only).

    uv run python scripts/eval_selection.py ALBUM [--workers 3]

Analyses the album into a separate developer cache (%LOCALAPPDATA%\\FotoDuenner-dev\\cache,
never into the album), builds the selection model like the app does and measures every kind
of recalculation: first build, mode switch, quota slider, grouping slider, manual click.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

DEV = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FotoDuenner-dev"
os.environ.setdefault("FOTODUENNER_DATA", str(DEV / "cache"))

from fotoduenner.api.state import AppState  # noqa: E402 – after FOTODUENNER_DATA
from fotoduenner.config import DEFAULT  # noqa: E402
from fotoduenner.pipeline.runner import analyze_album  # noqa: E402


def timed(fn, repeat: int = 3) -> tuple[float, object]:
    times, result = [], None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - start) * 1000)
    return min(times), result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("album", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    from dataclasses import replace

    cfg = replace(DEFAULT, analysis_workers=args.workers)
    start = time.perf_counter()
    summary = analyze_album(args.album, cfg, workers=args.workers)
    print(f"analysis: {summary.processed} new photos in {time.perf_counter() - start:.0f} s")

    state = AppState(cfg)
    info = state.open_album(str(args.album), True)
    album_id = info["album_id"]
    session = state.session(album_id)
    review = session.review
    assert review is not None
    state.update_settings(album_id, {"mode": "clean", "level": 2, "keep_target": 0.5})
    review.invalidate()

    first, view = timed(lambda: state.view(album_id)["review"], repeat=1)
    rv = view  # type: ignore[assignment]
    stats = rv["stats"]  # type: ignore[index]
    groups = rv["groups"]  # type: ignore[index]
    engine = review.engine
    assert engine is not None
    kinds = Counter(g["kind"] for g in groups)
    sizes = [len(g["ids"]) for g in groups]
    report = {
        "photos": stats["total"],
        "analysed": engine.n,
        "duplicates": len(engine.duplicates),
        "sequences": Counter(s.kind for s in engine.sequences),
        "series": sum(1 for n in engine.nodes if n.kind == "series"),
        "groups": dict(kinds),
        "group_sizes": {
            "max": max(sizes, default=0),
            "median": statistics.median(sizes) if sizes else 0,
        },
        "singles": len(rv["singles"]),  # type: ignore[index]
        "clean": {k: stats[k] for k in stats if k.startswith("reject") or k in ("keep", "review")},
    }

    timings = {"first_view_ms": round(first)}
    t, quota = timed(lambda: state.update_settings(album_id, {"mode": "quota"}), repeat=1)
    timings["mode_switch_ms"] = round(t)
    report["quota_50"] = {k: quota["stats"][k] for k in ("keep", "reject", "keep_floor")}  # type: ignore[index]
    targets = iter([0.35, 0.65, 0.4, 0.6, 0.45])
    t, _ = timed(lambda: state.update_settings(album_id, {"keep_target": next(targets)}), 5)
    timings["quota_slider_ms"] = round(t)
    levels = iter([0, 4, 1, 3])
    t, _ = timed(lambda: state.update_settings(album_id, {"level": next(levels)}), 4)
    timings["grouping_slider_new_level_ms"] = round(t)
    t, _ = timed(lambda: state.update_settings(album_id, {"level": 0}), 3)
    timings["grouping_slider_cached_level_ms"] = round(t)
    pid = int(engine.inp.ids[0])
    choices = iter(["keep", "reject", None])
    t, _ = timed(lambda: state.set_decision(album_id, pid, next(choices)), 3)
    timings["manual_click_ms"] = round(t)
    t, _ = timed(lambda: state.view(album_id), 3)
    timings["full_view_ms"] = round(t)
    report["timings"] = timings
    state.update_settings(album_id, {"mode": "clean", "level": 2, "keep_target": 0.5})
    print(json.dumps(report, indent=1, default=dict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
