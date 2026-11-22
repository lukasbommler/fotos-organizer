from pathlib import Path

from fastapi.testclient import TestClient

from fotoduenner import __version__
from fotoduenner.api.server import create_app
from fotoduenner.config import DEFAULT, Config
from fotoduenner.paths import long_path


def test_index_and_version() -> None:
    client = TestClient(create_app(token="t", allowed_hosts={"testserver"}))
    index = client.get("/?t=t")
    assert index.status_code == 200
    assert "FotoDünner" in index.text
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/api/version").json() == {"version": __version__}


def test_config_defaults_match_spec() -> None:
    assert DEFAULT.default_mode == "clean"
    assert DEFAULT.blur_abs is None  # uncalibrated until M7 (T5)
    assert len(DEFAULT.motif_thresholds) == 5
    assert 0 <= DEFAULT.motif_default_level < len(DEFAULT.motif_thresholds)
    assert Config().zs_enabled is not Config().zs_enabled  # no shared mutable defaults


def test_long_path_prefix(tmp_path: Path) -> None:
    deep = tmp_path / ("ä" * 50) / ("ö" * 50) / ("ü" * 50) / ("Südtirol 2024 " * 8) / "bild.jpg"
    assert len(str(deep)) > 260
    prefixed = long_path(deep)
    prefixed.parent.mkdir(parents=True)
    prefixed.write_bytes(b"x")
    assert prefixed.read_bytes() == b"x"
