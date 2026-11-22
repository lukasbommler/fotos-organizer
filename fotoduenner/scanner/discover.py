"""Find the JPEGs of an album (01 §3): exclusions, sidecars, OneDrive placeholders."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from fotoduenner.config import Config
from fotoduenner.paths import long_path
from fotoduenner.scanner.sidecars import (
    JPEG_SUFFIXES,
    combine_ratings,
    match_sidecars,
    read_sidecar_rating,
)

log = logging.getLogger(__name__)

REJECT_DIR = "_Aussortiert"
IGNORED_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_OFFLINE_MASK = (
    FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


@dataclass(slots=True)
class FoundPhoto:
    rel_path: str  # POSIX separators, relative to the album root
    size: int
    mtime_ns: int
    offline: bool  # cloud placeholder, reading would trigger a download (02 §7.3)
    companions: list[str] = field(default_factory=list)  # rel paths of sidecars moved along
    sidecar_rating: int | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.rel_path).name


@dataclass(slots=True)
class ScanResult:
    root: Path
    photos: list[FoundPhoto]
    ignored_files: int  # other formats, hidden files – never touched
    unreadable_dirs: list[str]

    @property
    def offline_count(self) -> int:
        return sum(p.offline for p in self.photos)


def _attributes(entry: os.DirEntry[str]) -> int:
    try:
        return getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return 0


def _is_hidden(entry: os.DirEntry[str]) -> bool:
    return entry.name.startswith(".") or bool(
        _attributes(entry) & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
    )


def scan_album(root: Path, cfg: Config) -> ScanResult:
    """Walk the album. Directory symlinks and junctions are not followed (no loops)."""
    base = long_path(root)
    photos: list[FoundPhoto] = []
    ignored = 0
    unreadable: list[str] = []
    stack: list[PurePosixPath] = [PurePosixPath()]

    while stack:
        rel_dir = stack.pop()
        abs_dir = base.joinpath(*rel_dir.parts)
        try:
            with os.scandir(abs_dir) as iterator:
                entries = list(iterator)
        except OSError as exc:
            log.warning("cannot list %s: %s", rel_dir, exc)
            unreadable.append(str(rel_dir))
            continue

        files: dict[str, os.stat_result] = {}
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.is_junction() or _is_hidden(entry):
                    continue
                if entry.name.casefold() == REJECT_DIR.casefold():
                    continue
                if cfg.include_subdirs:
                    stack.append(rel_dir / entry.name)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue  # symlinked files are not ours to move
            if entry.name.casefold() in IGNORED_FILES or _is_hidden(entry):
                ignored += 1
                continue
            try:
                files[entry.name] = entry.stat(follow_symlinks=False)
            except OSError as exc:
                log.warning("cannot stat %s: %s", rel_dir / entry.name, exc)

        matches = match_sidecars(files)
        companions = {c for m in matches.values() for c in m.companions}
        ignored += sum(
            1
            for name in files
            if Path(name).suffix.casefold() not in JPEG_SUFFIXES and name not in companions
        )
        for name, match in matches.items():
            info = files[name]
            ratings = [
                read_sidecar_rating(abs_dir / source, cfg.sidecar_max_bytes)
                for source in match.rating_sources
            ]
            photos.append(
                FoundPhoto(
                    rel_path=str(rel_dir / name),
                    size=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                    offline=bool(getattr(info, "st_file_attributes", 0) & _OFFLINE_MASK),
                    companions=[str(rel_dir / c) for c in match.companions],
                    sidecar_rating=combine_ratings(*ratings),
                )
            )

    photos.sort(key=lambda p: p.rel_path.casefold())
    return ScanResult(root=root, photos=photos, ignored_files=ignored, unreadable_dirs=unreadable)
