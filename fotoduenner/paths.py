"""Filesystem locations and Windows long-path handling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "FotoDuenner"


def data_dir() -> Path:
    """Per-user data directory (%LOCALAPPDATA%\\FotoDuenner); created on demand."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def models_dir() -> Path:
    return resource_dir() / "models"


def long_path(path: Path) -> Path:
    """Return a path usable for file operations beyond MAX_PATH on Windows.

    LongPathsEnabled is usually off (docs/DECISIONS.md A6), so absolute paths get the
    extended-length prefix. No-op on other platforms and for already prefixed paths.
    """
    if os.name != "nt":
        return path
    text = str(path.resolve())
    if text.startswith("\\\\?\\"):
        return path
    if text.startswith("\\\\"):  # UNC share: \\server\share -> \\?\UNC\server\share
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)
