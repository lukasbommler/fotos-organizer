"""Download all models once (development only), verify SHA256, derive runtime artefacts.

Usage:  uv run python scripts/fetch_models.py [--dev] [--dest DIR]

Runtime models are always fetched. ``--dev`` additionally fetches files that are only needed
by development scripts (FP32 CLIP for the INT8 comparison, CLIP text encoder + tokenizer for
the zero-shot text embeddings, MediaPipe task bundle for the eye spike).

Every download is pinned to an exact revision and checked against its SHA256. Sources and
licenses are documented in models/MODELS.md. The application itself never downloads anything.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import pickle
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fotoduenner.paths import dev_models_dir

HF_CLIP = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/6b7c523703ae26e39be4b03774966f8571c7f523"
HF_YUNET = "https://huggingface.co/opencv/face_detection_yunet/resolve/3cc26e7f1014a5ee5d74a42acee58bafc9d0a310"
GH_LAION = "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/6d122adad522ab246644d9dc1c6d7a3810ee255f"
MP_LANDMARKER = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1"
)


@dataclass(frozen=True)
class Asset:
    name: str  # file name in the models directory
    url: str
    sha256: str
    dev_only: bool = False


ASSETS: tuple[Asset, ...] = (
    Asset(
        "face_detection_yunet_2023mar.onnx",
        f"{HF_YUNET}/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    Asset(
        "clip_vit_b32_vision_int8.onnx",
        f"{HF_CLIP}/onnx/vision_model_quantized.onnx",
        "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
    ),
    Asset(
        "sa_0_4_vit_b_32_linear.pth",
        f"{GH_LAION}/sa_0_4_vit_b_32_linear.pth",
        "c7b14cead230694acc7b9447974d3cad78003c72da032e402a303b6c2429e85f",
    ),
    Asset(
        "clip_vit_b32_vision_fp32.onnx",
        f"{HF_CLIP}/onnx/vision_model.onnx",
        "fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40",
        dev_only=True,
    ),
    Asset(
        "clip_vit_b32_text_fp32.onnx",
        f"{HF_CLIP}/onnx/text_model.onnx",
        "3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc",
        dev_only=True,
    ),
    Asset(
        "clip_tokenizer.json",
        f"{HF_CLIP}/tokenizer.json",
        "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd",
        dev_only=True,
    ),
    Asset(
        "face_landmarker.task",
        f"{MP_LANDMARKER}/face_landmarker.task",
        "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
        dev_only=True,
    ),
)

USER_AGENT = {"User-Agent": "fotoduenner-dev-fetch"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(asset: Asset, dest: Path) -> None:
    target = dest / asset.name
    if target.exists() and sha256_of(target) == asset.sha256:
        print(f"  ok      {asset.name}")
        return
    tmp = target.with_suffix(target.suffix + ".part")
    print(f"  fetch   {asset.name}  <- {asset.url}")
    request = urllib.request.Request(asset.url, headers=USER_AGENT)
    with urllib.request.urlopen(request, timeout=60) as response, tmp.open("wb") as fh:
        while chunk := response.read(1 << 20):
            fh.write(chunk)
    actual = sha256_of(tmp)
    if actual != asset.sha256:
        tmp.unlink()
        raise SystemExit(f"SHA256 mismatch for {asset.name}: {actual} != {asset.sha256}")
    tmp.replace(target)
    print(f"  sha256  {actual}")


class _TorchFreeUnpickler(pickle.Unpickler):
    """Load a PyTorch zip checkpoint into NumPy without importing torch."""

    def __init__(self, fh: io.BytesIO, archive: zipfile.ZipFile, prefix: str) -> None:
        super().__init__(fh)
        self._archive = archive
        self._prefix = prefix

    def find_class(self, module: str, name: str):  # type: ignore[no-untyped-def]
        if module == "torch._utils" and name == "_rebuild_tensor_v2":
            return _rebuild_tensor
        if module == "torch" and name.endswith("Storage"):
            return name
        if module == "collections" and name == "OrderedDict":
            from collections import OrderedDict

            return OrderedDict
        raise pickle.UnpicklingError(f"refusing to load {module}.{name}")

    def persistent_load(self, pid):  # type: ignore[no-untyped-def]
        _, storage_type, key, _location, _numel = pid
        dtype = {"FloatStorage": np.float32, "HalfStorage": np.float16}[storage_type]
        raw = self._archive.read(f"{self._prefix}/data/{key}")
        return np.frombuffer(raw, dtype=dtype)


def _rebuild_tensor(storage, offset, size, stride, *_):  # type: ignore[no-untyped-def]
    count = int(np.prod(size)) if size else 1
    flat = storage[offset : offset + count]
    return np.lib.stride_tricks.as_strided(
        flat, shape=tuple(size), strides=tuple(s * flat.itemsize for s in stride)
    ).copy()


def convert_aesthetic(dest: Path) -> None:
    """sa_0_4_vit_b_32_linear.pth (nn.Linear 512 -> 1) -> aesthetic_vit_b32_linear.npz."""
    src = dest / "sa_0_4_vit_b_32_linear.pth"
    out = dest / "aesthetic_vit_b32_linear.npz"
    with zipfile.ZipFile(src) as archive:
        pkl = next(n for n in archive.namelist() if n.endswith("data.pkl"))
        prefix = pkl.rsplit("/", 1)[0]
        state = _TorchFreeUnpickler(io.BytesIO(archive.read(pkl)), archive, prefix).load()
    weight = np.asarray(state["weight"], dtype=np.float32).reshape(-1)
    bias = np.asarray(state["bias"], dtype=np.float32).reshape(-1)
    if weight.shape != (512,) or bias.shape != (1,):
        raise SystemExit(f"unexpected aesthetic head shapes {weight.shape} {bias.shape}")
    np.savez(out, weight=weight, bias=bias)
    print(f"  wrote   {out.name}  (weight {weight.shape}, bias {float(bias[0]):.4f})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="also fetch development-only files")
    parser.add_argument("--dest", type=Path, default=dev_models_dir())
    args = parser.parse_args()
    args.dest.mkdir(parents=True, exist_ok=True)
    print(f"models -> {args.dest}")
    for asset in ASSETS:
        if asset.dev_only and not args.dev:
            continue
        download(asset, args.dest)
    convert_aesthetic(args.dest)


if __name__ == "__main__":
    main()
