"""Rotating file logging in %LOCALAPPDATA%\\FotoDuenner\\logs."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from fotoduenner.paths import logs_dir

_FORMAT = "%(asctime)s %(levelname)-7s %(processName)s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    handler = RotatingFileHandler(
        logs_dir() / "fotoduenner.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
