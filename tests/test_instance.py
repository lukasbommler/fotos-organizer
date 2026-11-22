from __future__ import annotations

from pathlib import Path

from fotoduenner import instance


def test_second_lock_is_refused(tmp_path: Path) -> None:
    first = instance.acquire(tmp_path)
    assert first is not None
    assert instance.acquire(tmp_path) is None
    first.publish(12345, "tok")
    assert (tmp_path / instance.INFO_NAME).is_file()
    first.release()
    assert not (tmp_path / instance.INFO_NAME).exists()
    again = instance.acquire(tmp_path)
    assert again is not None
    again.release()


def test_focus_without_running_instance(tmp_path: Path) -> None:
    assert instance.focus_running_instance(tmp_path) is False
