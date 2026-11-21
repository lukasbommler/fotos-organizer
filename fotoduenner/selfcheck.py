"""Self-check: load every runtime model exactly as the app will. Used by tests and packaging."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from fotoduenner.paths import models_dir

log = logging.getLogger(__name__)

RUNTIME_ONNX = (
    "clip_vit_b32_vision_int8.onnx",
    "face_landmarks_detector.onnx",
    "face_blendshapes.onnx",
)
YUNET = "face_detection_yunet_2023mar.onnx"
AESTHETIC = "aesthetic_vit_b32_linear.npz"


def run_selfcheck(directory: Path | None = None) -> list[str]:
    """Return a list of problems (empty = all good). Models are loaded from bytes (umlaut paths)."""
    base = directory or models_dir()
    problems: list[str] = []
    for name in RUNTIME_ONNX:
        path = base / name
        try:
            start = time.perf_counter()
            session = ort.InferenceSession(path.read_bytes(), providers=["CPUExecutionProvider"])
            inputs = [(i.name, i.shape) for i in session.get_inputs()]
            log.info(
                "selfcheck %s ok %s %.0f ms", name, inputs, (time.perf_counter() - start) * 1e3
            )
        except Exception as exc:  # report every failure, keep checking the rest
            problems.append(f"{name}: {exc}")
    try:
        buf = np.frombuffer((base / YUNET).read_bytes(), np.uint8)
        detector = cv2.FaceDetectorYN.create("onnx", buf, np.empty(0, np.uint8), (320, 320))
        detector.detect(np.zeros((320, 320, 3), np.uint8))
        log.info("selfcheck %s ok (OpenCV %s)", YUNET, cv2.__version__)
    except Exception as exc:
        problems.append(f"{YUNET}: {exc}")
    try:
        head = np.load(base / AESTHETIC)
        if head["weight"].shape != (512,):
            problems.append(f"{AESTHETIC}: unexpected shape {head['weight'].shape}")
    except Exception as exc:
        problems.append(f"{AESTHETIC}: {exc}")
    for problem in problems:
        log.error("selfcheck failed: %s", problem)
    return problems
