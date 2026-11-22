"""Pre-compute the CLIP text embeddings for the zero-shot labels (03 §3.8). Development only.

Usage:  uv run --with tokenizers python scripts/make_text_embeddings.py

Needs the dev models (scripts/fetch_models.py --dev): CLIP ViT-B/32 text encoder + tokenizer.
Writes fotoduenner/assets/zeroshot_text.npz; at runtime no text encoder or tokenizer is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fotoduenner.paths import asset_dir, dev_models_dir

# ASSUMPTION: English prompts only. The OpenAI CLIP text encoder was trained on English
# captions; German prompts tokenise into fragments and score noticeably worse (03 §3.8 asked
# for German/English ensembles). Counter prompts are the neutral alternatives of 03 §3.8.
LABEL_PROMPTS: dict[str, list[str]] = {
    "person_back": [
        "a photo of a person seen from behind",
        "a person with their back to the camera",
        "a person facing away from the camera looking at the view",
        "the back of a person standing in front of a landscape",
        "people walking away from the camera",
    ],
    "screenshot": [
        "a screenshot",
        "a screenshot of a phone screen",
        "a screenshot of a computer screen",
        "a screenshot of an app",
        "a screenshot of a website",
        "a screen capture of a chat conversation",
    ],
    "document_sign": [
        "a photo of a restaurant menu",
        "a photo of a sign with text",
        "a photo of an information board",
        "a photo of a printed document",
        "a photo of a train timetable",
        "a photo of a ticket",
        "a close-up photo of a text on paper",
    ],
    "blurry": [
        "a blurry photo",
        "an out of focus photo",
        "a photo with strong motion blur",
        "a very blurry and unsharp picture",
        "a shaky blurred photo",
    ],
}
COUNTER_PROMPTS: list[str] = [
    "a holiday photo",
    "a photo of a landscape",
    "a photo of people",
    "a portrait photo of a person",
    "a photo of a city street",
    "a photo of a building",
    "a photo of food",
    "a photo of the beach",
    "a sharp photo",
]
LOGIT_SCALE = 100.0  # CLIP temperature
CONTEXT = 77


def encode(session: ort.InferenceSession, tokenizer, prompts: list[str]) -> np.ndarray:  # type: ignore[no-untyped-def]
    out = []
    for prompt in prompts:  # one at a time: no padding, the EOT position stays unambiguous
        ids = tokenizer.encode(prompt).ids[:CONTEXT]
        emb = session.run(["text_embeds"], {"input_ids": np.array([ids], np.int64)})[0][0]
        out.append(emb / np.linalg.norm(emb))
    return np.stack(out).astype(np.float32)


def main() -> None:
    from tokenizers import Tokenizer  # dev-only dependency (uv run --with tokenizers)

    models = dev_models_dir()
    tokenizer = Tokenizer.from_file(str(models / "clip_tokenizer.json"))
    session = ort.InferenceSession(
        (models / "clip_vit_b32_text_fp32.onnx").read_bytes(), providers=["CPUExecutionProvider"]
    )
    labels = list(LABEL_PROMPTS)
    label_embeds = []
    for name in labels:
        ensemble = encode(session, tokenizer, LABEL_PROMPTS[name]).mean(axis=0)
        label_embeds.append(ensemble / np.linalg.norm(ensemble))
    counter = encode(session, tokenizer, COUNTER_PROMPTS)
    out = asset_dir() / "zeroshot_text.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out,
        labels=np.array(labels),
        label_embeds=np.stack(label_embeds).astype(np.float32),
        counter_embeds=counter,
        logit_scale=np.float32(LOGIT_SCALE),
        prompts=np.array(json.dumps({"labels": LABEL_PROMPTS, "counter": COUNTER_PROMPTS})),
    )
    print(f"wrote {out} ({len(labels)} labels, {len(COUNTER_PROMPTS)} counter prompts)")


if __name__ == "__main__":
    main()
