"""T8: CLIP vs. DINOv2-small as grouping embedding – contact sheets for a visual check.

    uv run python scripts/compare_grouping_embeddings.py ALBUM [--groups 30] [--out DIR]

The album must have been analysed by FotoDünner (CLIP embeddings and previews come from the
cache). DINOv2 embeddings are computed from the 1600-px previews. For each embedding, motif
groups are built as in 03 §5.2 (series as nodes, time term, average linkage). DINOv2 distances
have a different scale, so its cut is chosen to give the same number of groups as CLIP at the
default level – the comparison is then about *which* photos end up together.

Writes one contact sheet per variant (one row per group, the same random sample of "anchor"
photos for both) plus summary.json to --out (default: dev folder, never the album).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fotoduenner.config import DEFAULT, Config
from fotoduenner.grouping.series import find_series, phash_ints
from fotoduenner.paths import dev_models_dir
from fotoduenner.pipeline.embed import center_crop_tensor, from_blob, l2_normalize
from fotoduenner.pipeline.models import onnx_session
from fotoduenner.pipeline.thumbs import thumb_paths
from fotoduenner.store.db import AlbumStore

DINO_FILE = "dinov2_small_int8.onnx"
DINO_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
DINO_STD = np.array([0.229, 0.224, 0.225], np.float32)
TILE = 150
MAX_PER_ROW = 10


def load_album(store: AlbumStore) -> dict[str, np.ndarray | list]:
    with contextlib.closing(store.connect()) as conn:
        rows = conn.execute(
            "SELECT p.id, p.rel_path, p.taken_at, p.taken_at_source, f.phash, f.embedding"
            " FROM photos p JOIN features f ON f.photo_id = p.id"
            " WHERE p.present=1 AND p.status='done' ORDER BY p.taken_at, p.rel_path"
        ).fetchall()
    return {
        "ids": [r["id"] for r in rows],
        "names": [r["rel_path"] for r in rows],
        "times": np.array([r["taken_at"] or 0.0 for r in rows]),
        "timed": np.array([r["taken_at_source"] == "exif" for r in rows]),
        "phash": phash_ints([r["phash"] for r in rows]),
        "clip": np.stack([from_blob(r["embedding"]) for r in rows]),
    }


def dino_embeddings(store: AlbumStore, ids: list[int]) -> np.ndarray:
    session = onnx_session((dev_models_dir() / DINO_FILE).read_bytes(), threads=4)
    out = []
    start = time.perf_counter()
    for pid in ids:
        img = Image.open(thumb_paths(store.thumbs_dir, pid)[1]).convert("RGB")
        x = center_crop_tensor(img, 256, 224, DINO_MEAN, DINO_STD)
        cls = session.run(None, {"pixel_values": x})[0][0, 0]
        out.append(l2_normalize(cls.astype(np.float32)))
    print(f"DINOv2: {len(ids)} photos in {time.perf_counter() - start:.0f} s")
    return np.stack(out)


def motif_linkage(emb: np.ndarray, series: np.ndarray, times: np.ndarray, cfg: Config):
    """Nodes = series (mean embedding, median time); 03 §5.2 distance; average linkage."""
    labels = np.unique(series)
    node_emb = np.stack([l2_normalize(emb[series == s].mean(axis=0)) for s in labels])
    node_t = np.array([np.median(times[series == s]) for s in labels])
    cos = np.clip(node_emb @ node_emb.T, -1.0, 1.0)
    dt = np.abs(node_t[:, None] - node_t[None, :])
    day = np.floor(node_t / 86400.0)
    dist = (1.0 - cos) + cfg.motif_alpha * np.minimum(1.0, dt / cfg.motif_tmax)
    dist += cfg.motif_day_penalty * (day[:, None] != day[None, :])
    np.fill_diagonal(dist, 0.0)
    return labels, linkage(squareform(dist, checks=False), method="average")


def cut(z: np.ndarray, node_labels: np.ndarray, series: np.ndarray, theta: float) -> np.ndarray:
    node_group = fcluster(z, t=theta, criterion="distance")
    lookup = dict(zip(node_labels.tolist(), node_group.tolist(), strict=True))
    return np.array([lookup[s] for s in series.tolist()])


def cut_to_count(z, node_labels, series, n_groups: int) -> tuple[np.ndarray, float]:
    """Smallest threshold whose cut yields at most n_groups groups (bisection)."""
    lo, hi = 0.0, float(z[:, 2].max()) + 1e-6
    for _ in range(40):
        mid = (lo + hi) / 2
        if len(np.unique(cut(z, node_labels, series, mid))) > n_groups:
            lo = mid
        else:
            hi = mid
    return cut(z, node_labels, series, hi), hi


def sheet(store: AlbumStore, ids: list[int], groups: list[list[int]], title: str, out: Path):
    rows = len(groups)
    img = Image.new("RGB", (MAX_PER_ROW * TILE + 60, rows * (TILE + 6) + 30), (20, 21, 23))
    draw = ImageDraw.Draw(img)
    draw.text((8, 8), title, fill=(233, 230, 223))
    for r, members in enumerate(groups):
        y = 30 + r * (TILE + 6)
        draw.text((6, y + 4), f"{r + 1:02d}\n{len(members)}", fill=(255, 178, 36))
        for c, idx in enumerate(members[:MAX_PER_ROW]):
            thumb = Image.open(thumb_paths(store.thumbs_dir, ids[idx])[0]).convert("RGB")
            thumb.thumbnail((TILE - 4, TILE - 4))
            img.paste(thumb, (60 + c * TILE, y))
        if len(members) > MAX_PER_ROW:
            more = f"+{len(members) - MAX_PER_ROW}"
            draw.text((60 + MAX_PER_ROW * TILE - 40, y + TILE - 16), more, fill=(233, 230, 223))
    img.save(out, quality=88)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("album", type=Path)
    parser.add_argument("--groups", type=int, default=30)
    parser.add_argument("--out", type=Path, default=dev_models_dir().parent / "t8")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    cfg = DEFAULT
    store = AlbumStore(args.album)
    data = load_album(store)
    n = len(data["ids"])
    if n < 20:
        raise SystemExit("album not analysed yet (or too small)")
    series = find_series(data["times"], data["timed"], data["phash"], data["clip"], cfg)
    theta = cfg.motif_thresholds[cfg.motif_default_level]
    clip_nodes, clip_z = motif_linkage(data["clip"], series, data["times"], cfg)
    clip_groups = cut(clip_z, clip_nodes, series, theta)
    n_groups = len(np.unique(clip_groups))
    dino = dino_embeddings(store, data["ids"])
    dino_nodes, dino_z = motif_linkage(dino, series, data["times"], cfg)
    dino_groups, dino_theta = cut_to_count(dino_z, dino_nodes, series, n_groups)

    from sklearn.metrics import adjusted_rand_score  # dev-only: uv run --with scikit-learn

    args.out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    multi = [i for i in range(n) if (clip_groups == clip_groups[i]).sum() >= 2]
    anchors = rng.choice(multi, size=min(args.groups, len(multi)), replace=False)
    summary: dict[str, object] = {
        "photos": n,
        "series": len(np.unique(series)),
        "clip_theta": theta,
        "dino_theta": round(dino_theta, 4),
        "groups": n_groups,
        "ari": round(float(adjusted_rand_score(clip_groups, dino_groups)), 3),
    }
    for name, labels in (("clip", clip_groups), ("dino", dino_groups)):
        sizes = np.bincount(np.unique(labels, return_inverse=True)[1])
        summary[f"{name}_multi_groups"] = int((sizes >= 2).sum())
        summary[f"{name}_largest"] = int(sizes.max())
        summary[f"{name}_singles"] = int((sizes == 1).sum())
        groups = [np.flatnonzero(labels == labels[a]).tolist() for a in anchors]
        sheet(
            store,
            data["ids"],
            groups,
            f"{name.upper()} – Gruppe des Ankerbilds",
            args.out / f"{name}.jpg",
        )
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print(f"sheets -> {args.out}")


if __name__ == "__main__":
    main()
