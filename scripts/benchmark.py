"""Analysis throughput benchmark (docs/05 "Benchmark").

    uv run python scripts/benchmark.py [--n 300] [--workers 1,2,3,auto] [--record]

Creates (once) N synthetic 24 MP JPEGs of ~13 MB under %LOCALAPPDATA%\\FotoDuenner-dev\\bench
(outside Google Drive, A8), analyses them with a fresh cache for each worker count, and
reports ms per stage, throughput, peak RAM and the projection for 2,000 photos.
40 % of the photos carry a portrait from tests/fixtures/faces (faces, eyes); the noise
backgrounds look alike to CLIP, so every face photo also gets the second pass (T6) – a
deliberate worst case for the M3 time budget.
``--record`` appends the result to docs/BENCHMARKS.md.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import shutil
import statistics
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import piexif
import psutil
from PIL import Image

from fotoduenner import __version__
from fotoduenner.config import DEFAULT
from fotoduenner.paths import dev_models_dir
from fotoduenner.pipeline.runner import analyze_album, default_workers

BENCH_ROOT = dev_models_dir().parent / "bench"
REPO = Path(__file__).resolve().parent.parent
SIZE = (6000, 4000)  # 24 MP
NOISE = 16  # with quality 95: ~13 MB per file, like a 24 MP camera JPEG
QUALITY = 95
PROJECT_TO = 2000
A7_CAP = 3  # 4-core office laptop equivalent (docs/DECISIONS.md A7)
FACES = sorted((REPO / "tests" / "fixtures" / "faces").glob("*.jpg"))
FACE_SHARE = (1, 2)  # seed % 5 in FACE_SHARE -> portrait pasted in (never the rotated ones)
ALBUM_NAME = "album_24mp_m3"


def _make_one(args: tuple[Path, int]) -> None:
    path, seed = args
    rng = np.random.default_rng(seed)
    w, h = SIZE
    coarse = rng.integers(0, 256, (h // 48, w // 48, 3), dtype=np.uint8)
    base = np.asarray(
        Image.fromarray(coarse).resize(SIZE, Image.Resampling.BICUBIC), dtype=np.int16
    )
    pixels = np.clip(base + rng.integers(-NOISE, NOISE + 1, (h, w, 3), dtype=np.int16), 0, 255)
    if seed % 5 in FACE_SHARE and FACES:
        face = Image.open(FACES[seed % len(FACES)]).convert("RGB")
        face = face.resize((face.width * 2, face.height * 2), Image.Resampling.BICUBIC)
        x, y = int(rng.integers(0, w - face.width)), int(rng.integers(0, h - face.height))
        pixels[y : y + face.height, x : x + face.width] = np.asarray(face)
    minute, second = divmod(seed, 60)
    exif = piexif.dump(
        {
            "0th": {
                piexif.ImageIFD.Make: "Bench",
                piexif.ImageIFD.Model: "Synthetic 24MP",
                piexif.ImageIFD.Orientation: 6 if seed % 5 == 0 else 1,  # some portrait shots
            },
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: f"2024:07:15 {10 + minute // 60:02d}:"
                f"{minute % 60:02d}:{second:02d}"
            },
        }
    )
    tmp = path.with_name(path.name + ".tmp")
    Image.fromarray(pixels.astype(np.uint8)).save(tmp, "JPEG", quality=QUALITY, exif=exif)
    tmp.replace(path)


def ensure_album(n: int) -> Path:
    album = BENCH_ROOT / ALBUM_NAME
    album.mkdir(parents=True, exist_ok=True)
    missing = [(album / f"BENCH_{i:05d}.jpg", i) for i in range(n)]
    missing = [item for item in missing if not item[0].exists()]
    if missing:
        print(f"Erzeuge {len(missing)} Testbilder (24 MP) in {album} ...", flush=True)
        with ProcessPoolExecutor() as pool:
            list(pool.map(_make_one, missing, chunksize=4))
    return album


class PeakRam:
    """Samples RSS of this process plus all children (the worker pool)."""

    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        me = psutil.Process()
        while not self._stop.is_set():
            total = 0
            for proc in [me, *me.children(recursive=True)]:
                with contextlib.suppress(psutil.Error):
                    total += proc.memory_info().rss
            self.peak = max(self.peak, total)
            self._stop.wait(0.2)

    def __enter__(self) -> PeakRam:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()


def run_once(album: Path, files: list[Path], workers: int, clip: str) -> dict[str, object]:
    cache = BENCH_ROOT / f"cache_w{workers}"
    shutil.rmtree(cache, ignore_errors=True)  # our own cache only, never the album
    cfg = replace(DEFAULT, analysis_workers=workers, clip_in_worker=clip == "worker")
    with PeakRam() as ram:
        start = time.perf_counter()
        summary = analyze_album(album, cfg, workers=workers, data_root=cache, keep_results=True)
        wall = time.perf_counter() - start
    shutil.rmtree(cache, ignore_errors=True)
    stages: dict[str, list[float]] = defaultdict(list)
    for result in summary.results:
        for stage, seconds in result.timings.items():
            stages[stage].append(seconds * 1000)
    n = len(summary.results)
    return {
        "workers": workers,
        "clip": clip,
        "n": n,
        "errors": summary.errors,
        "wall": wall,
        "per_image_ms": wall / n * 1000,
        "projected_min": wall / n * PROJECT_TO / 60,
        "peak_ram_mb": ram.peak / 1e6,
        "stages": {k: statistics.mean(v) for k, v in stages.items()},
        "faces_native": summary.faces_native,
        "mb_per_file": statistics.mean(p.stat().st_size for p in files) / 1e6,
    }


def cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or "unknown CPU"


def report(runs: list[dict[str, object]], n: int) -> str:
    stage_names = ["read", "hash", "decode", "thumbs", "quality", "faces", "clip"]
    lines = [
        f"### {datetime.now():%Y-%m-%d %H:%M} – M3 (alle Merkmale + 2. Durchgang), v{__version__}",
        "",
        f"- CPU: {cpu_name()} ({psutil.cpu_count(logical=False)} Kerne / "
        f"{psutil.cpu_count()} Threads), RAM {psutil.virtual_memory().total / 1e9:.0f} GB, "
        f"Python {platform.python_version()}",
        f"- {n} synthetische 24-MP-JPEGs, Ø {runs[0]['mb_per_file']:.1f} MB, SSD; Dateien lagen "
        "im Datei-Cache des Betriebssystems (Lesezeiten optimistisch, USB-HDD deutlich langsamer)",
        f"- 40 % mit Gesicht; zweiter Durchgang (T6) für {runs[0]['faces_native']} Bilder "
        "(Worst Case: jedes Gesichtsbild in einer Serie)",
        "",
        "| Worker | CLIP | ms/Bild (Wand) | Hochrechnung 2.000 | Spitzen-RAM | "
        + " | ".join(f"{s} ms" for s in stage_names)
        + " |",
        "|---|---|---|---|---|" + "---|" * len(stage_names),
    ]
    for run in runs:
        stages = run["stages"]
        assert isinstance(stages, dict)
        cells = " | ".join(f"{stages.get(s, 0):.0f}" for s in stage_names)
        mark = " (A7)" if run["workers"] == A7_CAP else ""
        lines.append(
            f"| {run['workers']}{mark} | {run['clip']} | {run['per_image_ms']:.0f} | "
            f"{run['projected_min']:.1f} min | {run['peak_ram_mb']:.0f} MB | {cells} |"
        )
    lines.append("")
    lines.append(
        "Stufen-Zeiten = CPU-Zeit je Bild innerhalb eines Workers (Mittelwert); „faces“ nur "
        "erster Durchgang; „clip“ bei „stage“ = Anteil am Batch im Hauptprozess (16 Bilder, "
        "mehrere Threads). Wand-Zeit und Hochrechnung enthalten den zweiten Durchgang."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--workers", default="1,2,3,auto")
    parser.add_argument("--record", action="store_true", help="append to docs/BENCHMARKS.md")
    parser.add_argument("--clip", default="stage", help="stage, worker or both (T11)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # cp1252 consoles

    album = ensure_album(args.n)
    files = sorted(album.glob("BENCH_*.jpg"))[: args.n]
    if len(files) != args.n:
        print(f"expected {args.n} files, found {len(files)}", file=sys.stderr)
        return 1
    if len(list(album.glob("BENCH_*.jpg"))) > args.n:
        print("Hinweis: Ordner enthält mehr Bilder als --n; alle werden analysiert.")

    counts: list[int] = []
    for token in args.workers.split(","):
        count = default_workers(DEFAULT) if token.strip() == "auto" else int(token)
        if count not in counts:
            counts.append(count)
    runs = []
    modes = ["worker", "stage"] if args.clip == "both" else [args.clip]
    for count, clip in [(c, m) for c in counts for m in modes]:
        print(f"Worker {count}, CLIP {clip} ...", flush=True)
        run = run_once(album, files, count, clip)
        print(
            f"  {run['per_image_ms']:.0f} ms/Bild, 2.000 Bilder ~ {run['projected_min']:.1f} min,"
            f" RAM {run['peak_ram_mb']:.0f} MB, Fehler {run['errors']}",
            flush=True,
        )
        runs.append(run)

    text = report(runs, int(runs[0]["n"]))  # type: ignore[call-overload]
    print()
    print(text)
    if args.record:
        target = REPO / "docs" / "BENCHMARKS.md"
        if not target.exists():
            target.write_text(
                "# Benchmarks\n\nErzeugt mit `scripts/benchmark.py` (05). "
                "Neueste Messung jeweils unten.\n",
                encoding="utf-8",
            )
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n" + text + "\n")
        print(f"\n→ {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
