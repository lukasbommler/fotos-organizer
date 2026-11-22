"""Sharpness and exposure measurements (03 §3.2, §3.3). Pure functions on NumPy arrays."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from fotoduenner.config import Config

QUALITY_EDGE = 1024  # img_1024 (03 §3.2)
TILE_GRID = 8
PEAK_PERCENTILE = 90.0
FLAT_TILE_STD = 4.0  # tiles with less brightness variation are sky/water: ignored for the peak


@dataclass(frozen=True, slots=True)
class Quality:
    sharpness_global: float
    sharpness_peak: float
    exposure_mean: float
    contrast: float
    clip_low: float
    clip_high: float


def luma(rgb: np.ndarray) -> np.ndarray:
    """Rec. 601 luma as float32 0..255 (the 'Y' of 03 §3.3)."""
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)


def gray_1024(rgb: np.ndarray) -> np.ndarray:
    """img_1024 luma as uint8: grey first, then resize – a third of the work of RGB."""
    return resize_long_edge(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), QUALITY_EDGE)


def resize_long_edge(img: np.ndarray, long_edge: int) -> np.ndarray:
    height, width = img.shape[:2]
    factor = long_edge / max(height, width)
    if factor >= 1.0:
        return img
    size = (max(1, round(width * factor)), max(1, round(height * factor)))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def _variance(img: np.ndarray) -> float:
    """Population variance; cv2.meanStdDev is ~10x faster than ndarray.var()."""
    return float(cv2.meanStdDev(img)[1][0, 0]) ** 2


def laplacian_var(gray: np.ndarray) -> float:
    return _variance(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))


def _block_mean(img: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    return cv2.resize(img, grid, interpolation=cv2.INTER_AREA).astype(np.float64)


def sharpness(gray: np.ndarray) -> tuple[float, float]:
    """(global Laplacian variance, 90th percentile over textured 8x8 tiles)."""
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    sharp_global = _variance(lap)
    height, width = gray.shape
    tile_h, tile_w = height // TILE_GRID, width // TILE_GRID
    if tile_h < 2 or tile_w < 2:
        return sharp_global, sharp_global
    rows, cols = tile_h * TILE_GRID, tile_w * TILE_GRID  # the last < 8 rows/columns are left out
    g = np.ascontiguousarray(gray[:rows, :cols], dtype=np.float32)
    lp = np.ascontiguousarray(lap[:rows, :cols])
    grid = (TILE_GRID, TILE_GRID)
    # INTER_AREA with an integer factor is an exact block mean: var = E[x^2] - E[x]^2
    gray_var = _block_mean(cv2.multiply(g, g), grid) - _block_mean(g, grid) ** 2
    lap_var = _block_mean(cv2.multiply(lp, lp), grid) - _block_mean(lp, grid) ** 2
    tile_vars = lap_var[gray_var >= FLAT_TILE_STD**2]
    # A frame without any texture (lens cap, fog) has no meaningful peak: use the global value.
    if tile_vars.size == 0:
        return sharp_global, sharp_global
    return sharp_global, float(np.percentile(tile_vars, PEAK_PERCENTILE))


def measure(rgb: np.ndarray) -> Quality:
    """All quality numbers of one photo, computed on img_1024."""
    return measure_gray(gray_1024(rgb))


def measure_gray(gray: np.ndarray) -> Quality:
    """Quality numbers from the img_1024 luma (uint8, or float 0..255)."""
    sharp_global, sharp_peak = sharpness(gray)
    levels = np.clip(np.rint(gray), 0, 255).astype(np.uint8) if gray.dtype != np.uint8 else gray
    hist = cv2.calcHist([levels], [0], None, [256], [0, 256]).ravel().astype(np.float64)
    count = float(hist.sum())
    values = np.arange(256, dtype=np.float64)
    mean = float(hist @ values) / count
    variance = max(0.0, float(hist @ (values * values)) / count - mean * mean)
    return Quality(
        sharpness_global=sharp_global,
        sharpness_peak=sharp_peak,
        exposure_mean=mean,
        contrast=variance**0.5,
        clip_low=float(hist[:5].sum()) / count,  # <= 4
        clip_high=float(hist[251:].sum()) / count,  # >= 251
    )


def exposure_score(q: Quality, cfg: Config) -> float:
    """q_exposure 0..1 (03 §3.3). Dark pixels weigh mildly: night shots and sunsets are fine."""
    penalty = max(0.0, q.clip_high - cfg.exposure_clip_high_free) * cfg.exposure_clip_high_weight
    penalty += max(0.0, q.clip_low - cfg.exposure_clip_low_free) * cfg.exposure_clip_low_weight
    if q.exposure_mean < cfg.exposure_mean_low:
        penalty += (cfg.exposure_mean_low - q.exposure_mean) / cfg.exposure_mean_low
    elif q.exposure_mean > cfg.exposure_mean_high:
        penalty += (q.exposure_mean - cfg.exposure_mean_high) / (255.0 - cfg.exposure_mean_high)
    return 1.0 - min(1.0, penalty)
