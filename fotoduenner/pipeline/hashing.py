"""Perceptual hash (03 §3.4). The file hash (SHA-256) is computed in analyze.py on the raw bytes."""

from __future__ import annotations

import cv2
import numpy as np

PHASH_SIZE = 32
PHASH_BLOCK = 8


def phash(gray: np.ndarray) -> bytes:
    """64-bit DCT hash: 32x32 grey, 2D DCT, top-left 8x8 without DC, bit = coefficient > median.

    ``gray`` may have any size and dtype; it is resized with area averaging first.
    """
    size = (PHASH_SIZE, PHASH_SIZE)
    small = cv2.resize(gray.astype(np.float32), size, interpolation=cv2.INTER_AREA)
    coeffs = cv2.dct(small)[:PHASH_BLOCK, :PHASH_BLOCK].flatten()
    # The DC term carries only overall brightness: it is left out of the median and its bit is
    # always 0, so 63 informative bits fill the 64-bit hash.
    median = float(np.median(coeffs[1:]))
    bits = coeffs > median
    bits[0] = False
    return np.packbits(bits).tobytes()


def hamming(a: bytes, b: bytes) -> int:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).bit_count()


def hamming_matrix(hashes: np.ndarray) -> np.ndarray:
    """Pairwise Hamming distances for an (N, 8) uint8 array of hashes."""
    bits = np.unpackbits(hashes, axis=1).astype(np.uint8)
    return (bits[:, None, :] != bits[None, :, :]).sum(axis=2)
