"""Single instance (A3): a second start brings the running window to the front and exits."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from fotoduenner.api.security import COOKIE_NAME, POST_HEADER

log = logging.getLogger(__name__)

LOCK_NAME = "instance.lock"
INFO_NAME = "instance.json"
FOCUS_TIMEOUT_S = 3.0


@dataclass(slots=True)
class InstanceLock:
    handle: IO[bytes]
    info_path: Path

    def publish(self, port: int, token: str) -> None:
        """Tell later starts where to find us (user-local file, same trust as the cache)."""
        tmp = self.info_path.with_name(INFO_NAME + ".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token}))
        tmp.replace(self.info_path)

    def release(self) -> None:
        with contextlib.suppress(OSError):
            self.info_path.unlink()  # program state file, not user data
        with contextlib.suppress(OSError):
            _unlock(self.handle)
        self.handle.close()


def _lock(handle: IO[bytes]) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover – development on other systems
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[bytes]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def acquire(data_dir: Path) -> InstanceLock | None:
    """Return the lock, or None if another instance holds it. The OS frees it on any exit."""
    handle = (data_dir / LOCK_NAME).open("a+b")
    try:
        handle.seek(0)
        _lock(handle)
    except OSError:
        handle.close()
        return None
    return InstanceLock(handle, data_dir / INFO_NAME)


def focus_running_instance(data_dir: Path) -> bool:
    """Ask the running instance (127.0.0.1 only) to show its window."""
    try:
        info = json.loads((data_dir / INFO_NAME).read_text())
        port, token = int(info["port"]), str(info["token"])
    except (OSError, ValueError, KeyError):
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/instance/focus",
        method="POST",
        headers={POST_HEADER: "1", "Cookie": f"{COOKIE_NAME}={token}"},
        data=b"",
    )
    try:
        # No proxy, ever: the request must stay on 127.0.0.1 (CLAUDE.md rule 3).
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=FOCUS_TIMEOUT_S) as response:
            return response.status == 200
    except OSError as exc:
        log.info("running instance did not answer: %s", exc)
        return False
