"""EXIF metadata: capture time, orientation, camera, rating, exposure bias (03 §3.1)."""

from __future__ import annotations

import calendar
import math
import re
from dataclasses import dataclass
from typing import Literal

from PIL import Image

from fotoduenner.scanner.sidecars import combine_ratings, parse_xmp_rating

TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_ORIENTATION = 0x0112
TAG_DATETIME = 0x0132
TAG_RATING = 0x4746
IFD_EXIF = 0x8769
TAG_EXPOSURE_BIAS = 0x9204
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_SUBSEC = 0x9290
TAG_SUBSEC_ORIGINAL = 0x9291
TAG_SUBSEC_DIGITIZED = 0x9292

_DT_RE = re.compile(r"(\d{4})[:\-./](\d{1,2})[:\-./](\d{1,2})[ T]+(\d{1,2}):(\d{1,2}):(\d{1,2})")

TakenSource = Literal["exif", "mtime"]


@dataclass(slots=True)
class PhotoMeta:
    width: int  # as displayed, i.e. after applying the orientation
    height: int
    orientation: int
    exif_dt: str | None  # raw capture time "YYYY:MM:DD HH:MM:SS[.subsec]", part of T13 fingerprint
    taken_at: float | None
    camera: str | None
    exposure_bias: float | None
    rating: int | None


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


def parse_exif_datetime(value: str | None, subsec: str | None = None) -> float | None:
    """Parse an EXIF timestamp to seconds.

    EXIF times carry no time zone; they are used unchanged (02 §7.7) and interpreted as UTC
    only to obtain a number. Only differences between photos matter.
    """
    if not value:
        return None
    match = _DT_RE.search(value)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(g) for g in match.groups())
    if year < 1900 or not (1 <= month <= 12 and 1 <= day <= 31):
        return None  # e.g. "0000:00:00 00:00:00" from cameras without a set clock
    if hour > 23 or minute > 59 or second > 60:
        return None
    try:
        stamp = float(calendar.timegm((year, month, day, hour, minute, min(second, 59), 0, 0, 0)))
    except (OverflowError, ValueError):
        return None
    digits = "".join(ch for ch in (subsec or "") if ch.isdigit())
    if digits:
        stamp += int(digits) / 10 ** len(digits)
    return stamp


def _camera(make: str | None, model: str | None) -> str | None:
    if make and model and model.casefold().startswith(make.split()[0].casefold()):
        return model  # "Canon" + "Canon EOS R6" -> "Canon EOS R6"
    parts = [p for p in (make, model) if p]
    return " ".join(parts) or None


def _float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]  # IFDRational, int, float
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def read_meta(img: Image.Image) -> PhotoMeta:
    """Read metadata from an opened (not necessarily loaded) image. Never raises on bad EXIF."""
    width, height = img.size
    try:
        exif = img.getexif()
        exif_ifd = exif.get_ifd(IFD_EXIF)
    except Exception:  # broken EXIF must not make the photo unreadable
        exif, exif_ifd = Image.Exif(), {}

    orientation = exif.get(TAG_ORIENTATION)
    orientation = orientation if isinstance(orientation, int) and 1 <= orientation <= 8 else 1
    if orientation in (5, 6, 7, 8):
        width, height = height, width

    exif_dt: str | None = None
    taken_at: float | None = None
    for dt_tag, sub_tag, ifd in (
        (TAG_DATETIME_ORIGINAL, TAG_SUBSEC_ORIGINAL, exif_ifd),
        (TAG_DATETIME_DIGITIZED, TAG_SUBSEC_DIGITIZED, exif_ifd),
        (TAG_DATETIME, TAG_SUBSEC, None),
    ):
        raw = _text((ifd if ifd is not None else exif).get(dt_tag))
        sub = _text(exif_ifd.get(sub_tag))
        taken_at = parse_exif_datetime(raw, sub)
        if taken_at is not None:
            exif_dt = f"{raw}.{sub}" if sub else raw
            break

    rating = exif.get(TAG_RATING)
    rating = max(-1, min(5, rating)) if isinstance(rating, int) else None
    xmp = img.info.get("xmp")
    if isinstance(xmp, bytes | str):
        xmp_rating = parse_xmp_rating(xmp if isinstance(xmp, bytes) else xmp.encode())
        rating = combine_ratings(rating, xmp_rating)

    return PhotoMeta(
        width=width,
        height=height,
        orientation=orientation,
        exif_dt=exif_dt,
        taken_at=taken_at,
        camera=_camera(_text(exif.get(TAG_MAKE)), _text(exif.get(TAG_MODEL))),
        exposure_bias=_float(exif_ifd.get(TAG_EXPOSURE_BIAS)),
        rating=rating,
    )


def read_exif_dt(path_str: str) -> str | None:
    """Capture time only, reading just the file header (fingerprint check, T13)."""
    try:
        with Image.open(path_str) as img:
            return read_meta(img).exif_dt
    except Exception:
        return None
