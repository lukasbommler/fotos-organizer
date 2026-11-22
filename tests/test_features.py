"""Unit tests for the M3 feature stages (03 §3): quality, hashes, series, zero-shot math."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from fixtures.make_fixtures import pattern

from fotoduenner.config import DEFAULT, Config
from fotoduenner.grouping.series import find_series, phash_ints, series_members
from fotoduenner.pipeline.embed import center_crop_tensor, from_blob, l2_normalize, to_blob
from fotoduenner.pipeline.hashing import hamming, phash
from fotoduenner.pipeline.models import ZeroShotText
from fotoduenner.pipeline.quality import Quality, exposure_score, luma, measure, sharpness
from fotoduenner.pipeline.zeroshot import active_labels, scores


def textured(width: int = 1024, height: int = 768, seed: int = 0) -> np.ndarray:
    """Photo-like texture with edges at many scales."""
    rng = np.random.default_rng(seed)
    img = pattern(width, height, seed).astype(np.float32)
    for _ in range(60):  # hard edges: rectangles of random brightness
        x, y = int(rng.integers(0, width - 40)), int(rng.integers(0, height - 40))
        w, h = int(rng.integers(10, 200)), int(rng.integers(10, 200))
        img[y : y + h, x : x + w] = rng.integers(0, 256, 3)
    return np.clip(img, 0, 255).astype(np.uint8)


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma)


# --- sharpness (03 §3.2) ---------------------------------------------------------------


def test_sharpness_decreases_monotonically_with_blur() -> None:
    img = textured()
    values = [measure(blur(img, s) if s else img) for s in (0, 1, 2, 4, 8)]
    peaks = [q.sharpness_peak for q in values]
    globals_ = [q.sharpness_global for q in values]
    assert peaks == sorted(peaks, reverse=True)
    assert globals_ == sorted(globals_, reverse=True)
    assert peaks[0] > 10 * peaks[-1]


def test_peak_keeps_partially_sharp_portrait_high() -> None:
    """Bokeh: only the subject is sharp. The peak must stay near the sharp value."""
    sharp = textured(seed=1)
    bokeh = blur(sharp, 6)
    # sharp subject covering ~30 % of the frame (the 90th percentile needs > 10 % sharp tiles)
    bokeh[150:650, 300:750] = sharp[150:650, 300:750]
    q_sharp, q_bokeh, q_blur = measure(sharp), measure(bokeh), measure(blur(sharp, 6))
    assert q_bokeh.sharpness_peak > 0.5 * q_sharp.sharpness_peak
    assert q_bokeh.sharpness_peak > 5 * q_blur.sharpness_peak
    assert q_bokeh.sharpness_global < 0.5 * q_sharp.sharpness_global  # global drops, peak not


def test_flat_tiles_are_ignored_for_the_peak() -> None:
    """A clear sky does not pull the percentile down."""
    img = textured(seed=2)
    sky = img.copy()
    sky[: img.shape[0] * 3 // 4] = (120, 170, 230)  # three quarters flat blue
    _, peak_full = sharpness(luma(img))
    _, peak_sky = sharpness(luma(sky))
    assert peak_sky > 0.3 * peak_full


def test_textureless_frame_has_defined_values() -> None:
    gray = np.full((480, 640), 30, np.float32)
    sharp_global, sharp_peak = sharpness(gray)
    assert sharp_global == sharp_peak == 0.0


# --- exposure (03 §3.3) ----------------------------------------------------------------


def q(mean: float, contrast: float = 50.0, low: float = 0.0, high: float = 0.0) -> Quality:
    return Quality(100.0, 100.0, mean, contrast, low, high)


def test_exposure_measurements() -> None:
    img = textured(seed=3)
    over = np.clip(img.astype(np.int16) + 190, 0, 255).astype(np.uint8)
    under = (img // 12).astype(np.uint8)
    qn, qo, qu = measure(img), measure(over), measure(under)
    assert qo.clip_high > 0.3 > qn.clip_high
    assert qu.exposure_mean < 25 < qn.exposure_mean
    assert qu.clip_low > qn.clip_low
    assert 0 <= qn.clip_low <= 1 and 0 <= qn.clip_high <= 1


def test_exposure_score_rules() -> None:
    cfg = DEFAULT
    assert exposure_score(q(120), cfg) == 1.0
    assert exposure_score(q(120, high=0.30), cfg) < 0.1  # blown out
    # night shot / sunset: many dark pixels, mean still fine -> only a mild penalty
    assert exposure_score(q(60, low=0.30), cfg) > 0.5
    assert exposure_score(q(10, contrast=5, low=0.9), cfg) == 0.0
    assert exposure_score(q(250, high=0.1), cfg) < exposure_score(q(200, high=0.1), cfg)
    for mean in (0, 34, 35, 120, 225, 226, 255):
        assert 0.0 <= exposure_score(q(mean), cfg) <= 1.0


# --- pHash (03 §3.4) -------------------------------------------------------------------


def test_phash_near_duplicates_and_different_images() -> None:
    a = textured(seed=4)
    rng = np.random.default_rng(0)
    noisy = np.clip(a.astype(np.int16) + rng.integers(-8, 9, a.shape), 0, 255).astype(np.uint8)
    shifted = np.roll(a, 6, axis=1)
    brighter = np.clip(a.astype(np.int16) + 25, 0, 255).astype(np.uint8)
    other = textured(seed=99)
    h = {name: phash(luma(img)) for name, img in {
        "a": a, "noisy": noisy, "shifted": shifted, "brighter": brighter, "other": other,
    }.items()}  # fmt: skip
    assert len(h["a"]) == 8
    assert hamming(h["a"], h["noisy"]) <= 4
    assert hamming(h["a"], h["brighter"]) <= 4
    assert hamming(h["a"], h["shifted"]) <= DEFAULT.phash_series_max
    assert hamming(h["a"], h["other"]) > 16
    assert hamming(h["a"], h["a"]) == 0


def test_phash_is_size_independent() -> None:
    a = textured(seed=5)
    half = cv2.resize(a, (512, 384), interpolation=cv2.INTER_AREA)
    assert hamming(phash(luma(a)), phash(luma(half))) <= 3


# --- series (03 §5.1) ------------------------------------------------------------------


def _unit(rng: np.random.Generator, n: int) -> np.ndarray:
    v = rng.normal(size=(n, 512)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_series_rules() -> None:
    rng = np.random.default_rng(1)
    emb = _unit(rng, 8)
    base = textured(seed=6)
    near = phash(luma(base))
    far = [phash(luma(textured(seed=s))) for s in range(10, 18)]
    hashes = [near, near, far[0], far[1], far[2], far[3], near, far[4]]
    emb[3] = l2_normalize(emb[2] + 0.05 * emb[3])  # 2 and 3: same scene by embedding
    times = np.array([0, 30, 100, 350, 1000, 5000, 9e5, 9e5], dtype=np.float64)
    timed = np.array([True, True, True, True, True, True, False, True])
    labels = find_series(times, timed, phash_ints(hashes), emb, DEFAULT)
    assert labels[0] == labels[1]  # pHash close, 30 s apart
    assert labels[2] == labels[3]  # cosine >= 0.95 and 250 s <= 300 s
    # photo 6 has no EXIF time: pHash-only link (<= 5) to 0/1 regardless of time
    assert labels[6] == labels[0]
    assert len({labels[4], labels[5], labels[7], labels[0], labels[2]}) == 5
    members = series_members(labels)
    assert members.tolist()[:2] == [True, True]
    assert not members[4]


def test_series_time_windows() -> None:
    rng = np.random.default_rng(2)
    emb = _unit(rng, 4)
    emb[1] = l2_normalize(emb[0] + 0.02 * emb[1])  # cosine >= 0.95 to 0
    emb[3] = l2_normalize(emb[2] + 0.02 * emb[3])
    hashes = [phash(luma(textured(seed=s))) for s in range(20, 24)]
    times = np.array([0.0, 290.0, 1000.0, 1310.0])  # 290 s: linked, 310 s: not
    timed = np.ones(4, dtype=bool)
    labels = find_series(times, timed, phash_ints(hashes), emb, DEFAULT)
    assert labels[0] == labels[1]
    assert labels[2] != labels[3]


def test_series_handles_empty_and_single() -> None:
    empty = find_series(np.array([]), np.array([], bool), np.array([], np.uint64),
                        np.zeros((0, 512), np.float32), DEFAULT)  # fmt: skip
    assert len(empty) == 0
    one = find_series(np.array([5.0]), np.array([True]), phash_ints([b"\0" * 8]),
                      np.ones((1, 512), np.float32), DEFAULT)  # fmt: skip
    assert one.tolist() == [0]


# --- embeddings and zero-shot ----------------------------------------------------------


def test_clip_preprocessing_shape_and_normalisation() -> None:
    from PIL import Image

    from fotoduenner.pipeline.embed import CLIP_MEAN, CLIP_STD

    img = Image.fromarray(np.full((300, 900, 3), 128, np.uint8))
    tensor = center_crop_tensor(img, 224, 224, CLIP_MEAN, CLIP_STD)
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    expected = (128 / 255 - CLIP_MEAN) / CLIP_STD
    assert np.allclose(tensor[0, :, 100, 100], expected, atol=1e-2)


def test_embedding_blob_roundtrip() -> None:
    vec = l2_normalize(np.arange(512, dtype=np.float32) - 256)
    back = from_blob(to_blob(vec))
    assert len(to_blob(vec)) == 1024
    assert float(back @ vec) > 0.9999


def test_zeroshot_softmax_against_counter_prompts() -> None:
    e = np.eye(512, dtype=np.float32)
    text = ZeroShotText(
        labels=("screenshot", "blurry"),
        label_embeds=np.stack([e[0], e[1]]),
        counter_embeds=np.stack([e[2], e[3]]),
        logit_scale=100.0,
    )
    s = scores(l2_normalize(e[0] * 0.9 + e[2] * 0.1), text)
    assert s["screenshot"] > 0.99
    assert s["blurry"] < 0.01
    s = scores(l2_normalize(e[0] + e[2]), text)  # tie with one counter prompt
    assert abs(s["screenshot"] - 0.5) < 0.01


def test_active_labels_respect_switches_and_thresholds() -> None:
    zs = {"screenshot": 0.8, "blurry": 0.9, "person_back": 0.5}
    assert active_labels(zs, DEFAULT) == {"screenshot"}  # blurry is off by default (A24)
    cfg = Config(zs_enabled={**DEFAULT.zs_enabled, "blurry": True})
    assert active_labels(zs, cfg) == {"screenshot", "blurry"}
    assert active_labels(None, DEFAULT) == set()


@pytest.mark.parametrize("value", [0.0, 0.5])
def test_l2_normalize_zero_safe(value: float) -> None:
    out = l2_normalize(np.full(4, value, np.float32))
    assert np.all(np.isfinite(out))
