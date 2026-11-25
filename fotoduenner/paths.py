"""Filesystem locations and Windows long-path handling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "FotoDuenner"


def data_dir() -> Path:
    """Per-user data directory (%LOCALAPPDATA%\\FotoDuenner); created on demand.

    FOTODUENNER_DATA overrides the location (tests, benchmarks).
    """
    override = os.environ.get("FOTODUENNER_DATA")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def albums_dir(root: Path | None = None) -> Path:
    path = (root or data_dir()) / "albums"
    path.mkdir(parents=True, exist_ok=True)
    return path


def calibration_path(root: Path | None = None) -> Path:
    """Calibration records (D20), outside the album caches so clearing those keeps them."""
    path = (root or data_dir()) / "calibration"
    path.mkdir(parents=True, exist_ok=True)
    return path / "records.db"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_dir() -> Path:
    """Directory holding bundled resources (web/, models/) – works from source and PyInstaller."""
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        return Path(frozen_base)
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        return Path(frozen_base) / "web"
    return Path(__file__).resolve().parent / "web"


def asset_dir() -> Path:
    """Small derived data files shipped inside the package (e.g. zero-shot text embeddings)."""
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        return Path(frozen_base) / "assets"
    return Path(__file__).resolve().parent / "assets"


def dev_models_dir() -> Path:
    """Model cache for development, outside Google Drive (docs/DECISIONS.md A8)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FotoDuenner-dev" / "models"


def models_dir() -> Path:
    """Runtime model directory: env override > PyInstaller bundle > development cache."""
    override = os.environ.get("FOTODUENNER_MODELS")
    if override:
        return Path(override)
    if getattr(sys, "_MEIPASS", None):
        return resource_dir() / "models"
    return dev_models_dir()


def long_path(path: Path) -> Path:
    """Return a path usable for file operations beyond MAX_PATH on Windows.

    LongPathsEnabled is usually off (docs/DECISIONS.md A6), so absolute paths get the
    extended-length prefix. No-op on other platforms and for already prefixed paths.
    """
    if os.name != "nt":
        return path
    text = str(path)
    if text.startswith("\\\\?\\"):
        return path
    # abspath is purely lexical (no filesystem access), unlike resolve().
    text = os.path.abspath(text)  # noqa: PTH100
    if text.startswith("\\\\"):  # UNC share: \\server\share -> \\?\UNC\server\share
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)
