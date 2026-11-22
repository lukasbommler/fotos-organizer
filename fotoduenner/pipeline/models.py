"""Model loading for workers: every model is loaded once per process, always from bytes.

Loading from bytes avoids OpenCV/ONNX path handling with umlauts (CLAUDE.md rule 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from fotoduenner.paths import asset_dir, models_dir

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
CLIP_FILE = "clip_vit_b32_vision_fp16.onnx"  # A25: INT8 changed similarities too much
LANDMARKS_FILE = "face_landmarks_detector.onnx"
BLENDSHAPES_FILE = "face_blendshapes.onnx"
AESTHETIC_FILE = "aesthetic_vit_b32_linear.npz"
ZEROSHOT_FILE = "zeroshot_text.npz"  # small derived asset, shipped inside the package


def onnx_session(data: bytes, threads: int = 1) -> ort.InferenceSession:
    """CPU session; one intra-op thread per worker (02 §5, T11)."""
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.log_severity_level = 3
    return ort.InferenceSession(data, opts, providers=["CPUExecutionProvider"])


def load_clip(threads: int, directory: Path | None = None) -> ort.InferenceSession:
    try:
        data = ((directory or models_dir()) / CLIP_FILE).read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Modelldatei fehlt oder ist nicht lesbar: {CLIP_FILE}") from exc
    return onnx_session(data, threads)


def yunet_detector(data: bytes, score: float, nms: float, top_k: int) -> cv2.FaceDetectorYN:
    buf = np.frombuffer(data, np.uint8)
    return cv2.FaceDetectorYN.create(
        "onnx", buf, np.empty(0, np.uint8), (320, 320), score, nms, top_k
    )


@dataclass(slots=True)
class ZeroShotText:
    """Pre-computed CLIP text embeddings (03 §3.8): one ensemble vector per label + counters."""

    labels: tuple[str, ...]
    label_embeds: np.ndarray  # (L, 512) float32, L2-normalised
    counter_embeds: np.ndarray  # (C, 512) float32, L2-normalised
    logit_scale: float


def load_zeroshot(path: Path) -> ZeroShotText:
    with np.load(path) as data:
        return ZeroShotText(
            labels=tuple(str(x) for x in data["labels"]),
            label_embeds=data["label_embeds"].astype(np.float32),
            counter_embeds=data["counter_embeds"].astype(np.float32),
            logit_scale=float(data["logit_scale"]),
        )


@dataclass(slots=True)
class Models:
    yunet: cv2.FaceDetectorYN
    landmarks: ort.InferenceSession
    blendshapes: ort.InferenceSession
    clip: ort.InferenceSession | None  # None in workers when CLIP runs as a separate stage
    aesthetic_weight: np.ndarray  # (512,)
    aesthetic_bias: float
    zeroshot: ZeroShotText


def load_models(
    cfg_face: tuple[float, float, int], directory: Path | None = None, with_clip: bool = True
) -> Models:
    """Load every runtime model. Raises with the file name if one is missing or broken."""
    base = directory or models_dir()

    def read(name: str) -> bytes:
        try:
            return (base / name).read_bytes()
        except OSError as exc:
            raise RuntimeError(f"Modelldatei fehlt oder ist nicht lesbar: {name}") from exc

    score, nms, top_k = cfg_face
    with np.load(base / AESTHETIC_FILE) as head:
        weight = head["weight"].astype(np.float32)
        bias = float(head["bias"].reshape(-1)[0])
    return Models(
        yunet=yunet_detector(read(YUNET_FILE), score, nms, top_k),
        landmarks=onnx_session(read(LANDMARKS_FILE)),
        blendshapes=onnx_session(read(BLENDSHAPES_FILE)),
        clip=onnx_session(read(CLIP_FILE)) if with_clip else None,
        aesthetic_weight=weight,
        aesthetic_bias=bias,
        zeroshot=load_zeroshot(asset_dir() / ZEROSHOT_FILE),
    )
