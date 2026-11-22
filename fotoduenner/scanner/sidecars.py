"""Assign .xmp sidecars to JPEGs and read XMP ratings (read-only, never written)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})
SIDECAR_SUFFIX = ".xmp"

_RATING_RE = re.compile(
    rb"(?:xmp|xap):Rating\s*(?:=\s*[\"']\s*|>\s*)(-?\d+(?:\.\d+)?)",
)


def parse_xmp_rating(data: bytes) -> int | None:
    """Return xmp:Rating (-1..5) from an XMP packet, attribute or element form."""
    match = _RATING_RE.search(data)
    if not match:
        return None
    try:
        value = round(float(match.group(1)))
    except ValueError:
        return None
    return max(-1, min(5, value))


def combine_ratings(*ratings: int | None) -> int | None:
    """Merge embedded and sidecar ratings, erring on the side of keeping.

    # ASSUMPTION: any source with rating >= 1 protects the photo (highest wins); a reject (-1)
    # only counts if no source rates it positively (docs/DECISIONS.md A12).
    """
    known = [r for r in ratings if r is not None]
    if not known:
        return None
    if max(known) >= 1:
        return max(known)
    if -1 in known:
        return -1
    return 0


@dataclass(slots=True)
class SidecarMatch:
    companions: list[str] = field(default_factory=list)  # moved together with the JPEG
    rating_sources: list[str] = field(default_factory=list)  # read for protection only


def match_sidecars(names: Iterable[str]) -> dict[str, SidecarMatch]:
    """Map JPEG file names of one directory to their sidecars.

    Conventions: ``IMG_1.jpg.xmp`` (darktable) always belongs to ``IMG_1.jpg``.
    ``IMG_1.xmp`` (Lightroom, Bridge) belongs to ``IMG_1.jpg`` only if no other file shares the
    stem. # ASSUMPTION: next to a RAW (``IMG_1.CR2``) the sidecar belongs to the RAW and is never
    moved; its rating still protects the JPEG. Same for two JPEGs sharing a stem (A12).
    """
    all_names = list(names)
    jpegs = [n for n in all_names if Path(n).suffix.casefold() in JPEG_SUFFIXES]
    result = {n: SidecarMatch() for n in jpegs}
    by_full = {n.casefold(): n for n in jpegs}
    stem_owners: dict[str, list[str]] = defaultdict(list)
    for name in all_names:
        if Path(name).suffix.casefold() != SIDECAR_SUFFIX:
            stem_owners[Path(name).stem.casefold()].append(name)

    for name in all_names:
        path = Path(name)
        if path.suffix.casefold() != SIDECAR_SUFFIX:
            continue
        inner = path.stem.casefold()  # "img_1.jpg" or "img_1"
        if inner in by_full:
            match = result[by_full[inner]]
            match.companions.append(name)
            match.rating_sources.append(name)
            continue
        owners = stem_owners.get(inner, [])
        owner_jpegs = [o for o in owners if o in result]
        if not owner_jpegs:
            continue
        if len(owners) == 1:
            result[owner_jpegs[0]].companions.append(name)
        for jpeg in owner_jpegs:
            result[jpeg].rating_sources.append(name)
    return result


def read_sidecar_rating(path: Path, max_bytes: int) -> int | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes)
    except OSError:
        return None
    return parse_xmp_rating(data)
