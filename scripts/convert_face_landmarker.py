"""Convert MediaPipe's face landmark + blendshape models (TFLite) to ONNX (development only).

TensorFlow is needed only for this one-off conversion and never enters the project
environment. Run it in a temporary environment:

    uv run --no-project --python 3.12 --with tensorflow==2.21.0 --with tf2onnx==1.17.0 ^
        --with onnx==1.23.2 python scripts/convert_face_landmarker.py

Input:  <models>/face_landmarker.task   (fetched by scripts/fetch_models.py --dev)
Output: <models>/face_landmarks_detector.onnx, <models>/face_blendshapes.onnx
Decision and verification: docs/DECISIONS.md D17, models/MODELS.md.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MODELS = ("face_landmarks_detector", "face_blendshapes")


def models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(os.environ.get("FOTODUENNER_MODELS", Path(base) / "FotoDuenner-dev" / "models"))


def main() -> None:
    dest = models_dir()
    task = dest / "face_landmarker.task"
    if not task.exists():
        raise SystemExit(f"{task} missing - run scripts/fetch_models.py --dev first")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(task) as archive:
            archive.extractall(tmp)
        for name in MODELS:
            out = dest / f"{name}.onnx"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tf2onnx.convert",
                    "--tflite",
                    str(Path(tmp) / f"{name}.tflite"),
                    "--output",
                    str(out),
                    "--opset",
                    "17",
                ],
                check=True,
            )
            digest = hashlib.sha256(out.read_bytes()).hexdigest()
            print(f"{out.name}: {out.stat().st_size} bytes, sha256 {digest}")


if __name__ == "__main__":
    main()
