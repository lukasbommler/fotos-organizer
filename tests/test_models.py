import pytest

from fotoduenner.paths import models_dir
from fotoduenner.selfcheck import AESTHETIC, RUNTIME_ONNX, YUNET, run_selfcheck

_present = all((models_dir() / n).exists() for n in (*RUNTIME_ONNX, YUNET, AESTHETIC))


@pytest.mark.skipif(not _present, reason="models not fetched (scripts/fetch_models.py)")
def test_all_runtime_models_load() -> None:
    assert run_selfcheck() == []
