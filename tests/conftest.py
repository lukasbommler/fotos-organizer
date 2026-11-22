from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the real %LOCALAPPDATA%\\FotoDuenner from tests."""
    data = tmp_path / "appdata"
    monkeypatch.setenv("FOTODUENNER_DATA", str(data))
    return data
