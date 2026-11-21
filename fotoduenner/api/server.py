"""FastAPI application: serves the static frontend and (later) the JSON API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fotoduenner import __version__
from fotoduenner.paths import web_dir


def create_app(static_dir: Path | None = None) -> FastAPI:
    static = static_dir or web_dir()
    app = FastAPI(title="FotoDünner", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": __version__}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static / "index.html")

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
