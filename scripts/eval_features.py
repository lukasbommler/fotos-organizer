"""M3 acceptance on the evaluation set (scripts/fetch_testset.py): faces, eyes, zero-shot, INT8.

    uv run python scripts/eval_features.py sheets   # numbered boxes for labelling
    uv run python scripts/eval_features.py report   # metrics from tests/fixtures/testset.json
    uv run python scripts/eval_features.py clip [FOLDER ...] [--variant FILE]  # vs FP32 CLIP

Labels live in tests/fixtures/testset.json per item (``labels``):
  faces   list of [cx, cy] centres (relative) of every real face wider than 3 % of the image
  missed  number of real faces > 3 % that no box covers
  eyes    list of [cx, cy, "o"|"c"] for faces >= 4 % whose eye state is clear
  zs      list of zero-shot labels that apply (person_back, screenshot, document_sign, blurry)
  zs_skip labels that are unclear for this photo (left out of that label's statistics)
Derived photos (synthetic blur, app screenshots) take their group as label; blurred copies
skip person_back/document_sign because their sources were not reviewed for those.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fotoduenner.config import DEFAULT as _DEFAULT
from fotoduenner.paths import dev_models_dir
from fotoduenner.pipeline import analyze
from fotoduenner.pipeline.embed import clip_embedding
from fotoduenner.pipeline.faces import Face, analyze_faces
from fotoduenner.pipeline.models import CLIP_FILE, onnx_session
from fotoduenner.pipeline.zeroshot import scores

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "fixtures" / "testset.json"
TESTSET = dev_models_dir().parent / "testset"
LABEL_SCORE = 0.45  # sheets show weaker detections too, to see what the threshold removes
SHEET_EDGE = 900
LABELS = ("person_back", "screenshot", "document_sign", "blurry")
DEFAULT = replace(_DEFAULT, clip_in_worker=True)  # this script embeds in-process


def items() -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["items"]
    derived_path = TESTSET / "derived.json"
    derived = json.loads(derived_path.read_text(encoding="utf-8")) if derived_path.exists() else []
    return [i for i in manifest + derived if (TESTSET / i["file"]).exists()]


def load(path: Path, edge: int = 1600) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return img


def detect_all(img: Image.Image, score: float) -> list[Face]:
    cfg = replace(DEFAULT, face_score_threshold=score)
    analyze.init_worker(cfg, low_priority=False)
    return analyze_faces(analyze._models, np.asarray(img), cfg)


def cmd_sheets(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for item in items():
        if item["group"] not in ("faces", "eyes", "back", "neg"):
            continue
        img = load(TESTSET / item["file"])
        faces = detect_all(img, LABEL_SCORE)
        view = img.copy()
        view.thumbnail((SHEET_EDGE, SHEET_EDGE))
        draw = ImageDraw.Draw(view)
        w, h = view.size
        for n, f in enumerate(faces):
            x, y, bw, bh = f.box
            weak = f.conf < DEFAULT.face_score_threshold
            colour = (255, 178, 36) if not weak else (120, 120, 255)
            draw.rectangle([x * w, y * h, (x + bw) * w, (y + bh) * h], outline=colour, width=2)
            eyes = "-" if f.eyes_open is None else f"{f.eyes_open:.2f}"
            draw.text((x * w, (y + bh) * h + 2), f"{n} {f.size_rel:.3f} e{eyes}", fill=colour)
        view.save(out / item["file"], quality=85)
        record = [
            {"n": n, "c": [round(f.box[0] + f.box[2] / 2, 3), round(f.box[1] + f.box[3] / 2, 3)],
             "size": round(f.size_rel, 3), "conf": round(f.conf, 2),
             "eyes": None if f.eyes_open is None else round(f.eyes_open, 2)}
            for n, f in enumerate(faces)
        ]  # fmt: skip
        (out / (item["file"] + ".json")).write_text(json.dumps(record), encoding="utf-8")
        print(item["file"], len(faces))


def _covered(point: list[float], faces: list[Face], margin: float = 0.25) -> Face | None:
    for f in faces:
        x, y, w, h = f.box
        if x - w * margin <= point[0] <= x + w * (1 + margin) and (
            y - h * margin <= point[1] <= y + h * (1 + margin)
        ):
            return f
    return None


def cmd_report() -> dict:
    analyze.init_worker(DEFAULT, low_priority=False)
    models = analyze._models
    face_hits = face_total = 0
    false_pos = 0
    eye_ok = eye_total = 0
    eye_none = 0
    zs_rows: list[tuple[dict[str, float], set[str], str, set[str]]] = []
    for item in items():
        labels = item.get("labels")
        img = load(TESTSET / item["file"])
        arr = np.asarray(img)
        if labels is not None and ("faces" in labels or "eyes" in labels):
            faces = analyze_faces(models, arr, DEFAULT)
            gt = labels.get("faces", [])
            face_hits += sum(_covered(p, faces) is not None for p in gt)
            face_total += len(gt) + labels.get("missed", 0)
            false_pos += sum(
                1
                for f in faces
                if f.size_rel >= 0.03 and not any(_covered(p, [f], margin=0.0) for p in gt) and gt
            )
            for cx, cy, state in labels.get("eyes", []):
                f = _covered([cx, cy], faces)
                eye_total += 1
                if f is None or f.eyes_open is None:
                    eye_none += 1
                    continue
                eye_ok += (f.eyes_open >= DEFAULT.eyes_open_threshold) == (state == "o")
        emb = clip_embedding(models.clip, img)
        if labels is not None and "zs" in labels:
            truth, skip = set(labels["zs"]), set(labels.get("zs_skip", []))
        else:  # derived photos: the group decides
            truth = {"screen": {"screenshot"}, "blurry": {"blurry"}}.get(item["group"], set())
            skip = {"person_back", "document_sign"} if item["file"].startswith("synblur") else set()
        zs_rows.append((scores(emb, models.zeroshot), truth, item["file"], skip))
    result: dict[str, object] = {
        "faces_recall": round(face_hits / face_total, 3) if face_total else None,
        "faces_total": face_total,
        "faces_false_positive_boxes": false_pos,
        "eyes_accuracy": round(eye_ok / (eye_total - eye_none), 3)
        if eye_total > eye_none
        else None,
        "eyes_labelled": eye_total,
        "eyes_no_verdict": eye_none,
    }
    for label in LABELS:
        rows = [r for r in zs_rows if label not in r[3]]
        pos = [s[label] for s, t, _, _ in rows if label in t]
        neg = [s[label] for s, t, _, _ in rows if label not in t]
        best = None
        for thr in np.arange(0.3, 0.96, 0.05):
            tpr = float(np.mean([p >= thr for p in pos])) if pos else 0.0
            fpr = float(np.mean([n >= thr for n in neg])) if neg else 0.0
            if best is None or (tpr - 2 * fpr) > best[1] - 2 * best[2]:
                best = (round(float(thr), 2), tpr, fpr)
        current = DEFAULT.zs_threshold[label]
        result[label] = {
            "positives": len(pos),
            "negatives": len(neg),
            "threshold": current,
            "hit_rate": round(float(np.mean([p >= current for p in pos])), 3) if pos else None,
            "false_alarm_rate": round(float(np.mean([n >= current for n in neg])), 3),
            "false_alarms": [f for s, t, f, _ in rows if label not in t and s[label] >= current],
            "misses": [f for s, t, f, _ in rows if label in t and s[label] < current],
            "best_threshold (tpr-2fpr)": best,
        }
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def cmd_clip(folders: list[Path], limit: int, variant: str) -> None:
    """Runtime CLIP variant vs. FP32 (05 M3: mean cosine >= 0.98, A25)."""
    fp32 = onnx_session((dev_models_dir() / "clip_vit_b32_vision_fp32.onnx").read_bytes(), 4)
    other = onnx_session((dev_models_dir() / variant).read_bytes(), 4)
    files = [
        p for d in folders for p in sorted(d.rglob("*")) if p.suffix.lower() in (".jpg", ".jpeg")
    ]
    files = files[:limit]
    cos = []
    for path in files:
        img = load(path)
        cos.append(float(clip_embedding(fp32, img) @ clip_embedding(other, img)))
    arr = np.array(cos)
    stats = {
        "variant": variant,
        "images": len(arr),
        "mean_cos": round(float(arr.mean()), 4),
        "min_cos": round(float(arr.min()), 4),
        "p05_cos": round(float(np.percentile(arr, 5)), 4),
    }
    print(json.dumps(stats))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=("sheets", "report", "clip"))
    parser.add_argument("folders", nargs="*", type=Path)
    parser.add_argument("--out", type=Path, default=dev_models_dir().parent / "testset_sheets")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--variant", default=CLIP_FILE)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "sheets":
        cmd_sheets(args.out)
    elif args.cmd == "report":
        cmd_report()
    else:
        cmd_clip(args.folders or [TESTSET], args.limit, args.variant)


if __name__ == "__main__":
    main()
