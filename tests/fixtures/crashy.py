"""Worker task that kills its process for one photo (crash-recovery test)."""

from __future__ import annotations

import os

from fotoduenner.pipeline.analyze import Job, Result, analyze_photo

CRASH_NAME = "IMG_0002b_absturz.jpg"  # sorts mid-queue: others are in flight


def crash_on_marker(job: Job) -> Result:
    if job.rel_path.endswith(CRASH_NAME):
        os._exit(3)  # simulates a native crash inside a decoder
    return analyze_photo(job)
