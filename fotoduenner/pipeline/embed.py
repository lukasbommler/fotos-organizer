"""Image embeddings (03 §3.6): CLIP ViT-B/32 vision encoder via ONNX Runtime."""

from __future__ import annotations

import numpy as np
import onnxruntime as ort
from PIL import Image

CLIP_SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
EMBED_DIM = 512


def center_crop_tensor(
    img: Image.Image, short_side: int, crop: int, mean: np.ndarray, std: np.ndarray
) -> np.ndarray:
    """Shorter side to ``short_side`` (bicubic), centre crop ``crop``², normalise, NCHW float32."""
    width, height = img.size
    factor = short_side / min(width, height)
    size = (max(crop, round(width * factor)), max(crop, round(height * factor)))
    resized = img.resize(size, Image.Resampling.BICUBIC, reducing_gap=2.0)
    left, top = (size[0] - crop) // 2, (size[1] - crop) // 2
    arr = np.asarray(resized.crop((left, top, left + crop, top + crop)), np.float32) / 255.0
    arr = (arr - mean) / std
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])


def clip_crop(img: Image.Image) -> np.ndarray:
    """CLIP input crop as uint8 HWC (224², 150 KB): cheap to send from a worker to the stage."""
    width, height = img.size
    factor = CLIP_SIZE / min(width, height)
    size = (max(CLIP_SIZE, round(width * factor)), max(CLIP_SIZE, round(height * factor)))
    resized = img.resize(size, Image.Resampling.BICUBIC, reducing_gap=2.0)
    left, top = (size[0] - CLIP_SIZE) // 2, (size[1] - CLIP_SIZE) // 2
    return np.asarray(resized.crop((left, top, left + CLIP_SIZE, top + CLIP_SIZE)), np.uint8)


def clip_embed_batch(session: ort.InferenceSession, crops: list[np.ndarray]) -> np.ndarray:
    """(N, 512) L2-normalised embeddings for uint8 crops from ``clip_crop``."""
    arr = np.stack(crops).astype(np.float32) / 255.0
    arr = ((arr - CLIP_MEAN) / CLIP_STD).transpose(0, 3, 1, 2)
    out = session.run(["image_embeds"], {"pixel_values": np.ascontiguousarray(arr)})[0]
    out = out.astype(np.float32)
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.where(norms > 0, norms, 1.0)


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def clip_embedding(session: ort.InferenceSession, img: Image.Image) -> np.ndarray:
    """L2-normalised 512-d float32 embedding (stored as float16)."""
    return clip_embed_batch(session, [clip_crop(img)])[0]


def to_blob(embedding: np.ndarray) -> bytes:
    return embedding.astype(np.float16).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, np.float16).astype(np.float32)
