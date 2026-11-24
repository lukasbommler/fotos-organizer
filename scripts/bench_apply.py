"""M6 benchmark: plan, move, crash check, undo and rescan for N synthetic photos (+ sidecars).

Runs in a temporary folder on the system drive; nothing of the user is touched.
    uv run python scripts/bench_apply.py [N]
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    with tempfile.TemporaryDirectory(prefix="fd-bench-apply-") as tmp:
        os.environ["FOTODUENNER_DATA"] = str(Path(tmp) / "appdata")
        from fotoduenner.apply import mover
        from fotoduenner.apply.undo import recover, undo
        from fotoduenner.config import Config
        from fotoduenner.scanner.discover import scan_album
        from fotoduenner.store.db import AlbumStore

        root = Path(tmp) / "Album Ä"
        for k in range(n):
            folder = root / f"Tag {k % 8}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"IMG_{k:05d}.jpg").write_bytes(os.urandom(4096))
            if k % 5 == 0:
                (folder / f"IMG_{k:05d}.xmp").write_bytes(b"<x/>")
        cfg = Config()
        store = AlbumStore(root)
        store.sync(scan_album(root, cfg), cfg)
        with contextlib.closing(store.connect()) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM photos")]

        t0 = time.perf_counter()
        batch = mover.start(store, ids, len(ids), cfg)
        t1 = time.perf_counter()
        moved = mover.run(store, batch, cfg)
        t2 = time.perf_counter()
        recover(store)
        t3 = time.perf_counter()
        back = undo(store, batch, cfg)
        t4 = time.perf_counter()
        store.sync(scan_album(root, cfg), cfg)
        t5 = time.perf_counter()
        print(f"{n} photos, {moved.files} files")
        print(f"plan            {t1 - t0:6.2f} s")
        print(f"move            {t2 - t1:6.2f} s  ({(t2 - t1) / moved.files * 1000:.2f} ms/file)")
        print(f"crash check     {t3 - t2:6.3f} s  (on every album open)")
        print(f"undo            {t4 - t3:6.2f} s  ({back.files} files)")
        print(f"rescan          {t5 - t4:6.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
