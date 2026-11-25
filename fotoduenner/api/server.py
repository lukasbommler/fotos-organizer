"""FastAPI application: static frontend + JSON API (04 §5)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fotoduenner import __version__
from fotoduenner.api.security import make_guard, new_token
from fotoduenner.api.state import ApiError, AppState
from fotoduenner.paths import web_dir
from fotoduenner.pipeline.thumbs import thumb_paths

SSE_INTERVAL_S = 0.5  # at most two updates per second (04 §1.3)
THUMB_CACHE = "private, max-age=31536000, immutable"  # URLs carry a version parameter


class AnalyzeRequest(BaseModel):
    skip_offline: bool = False


class OpenRequest(BaseModel):
    path: str
    recursive: bool = True


class SettingsRequest(BaseModel):
    mode: Literal["clean", "quota"] | None = None
    keep_target: float | None = None
    level: int | None = None
    low_singles: bool | None = None


class DecisionRequest(BaseModel):
    photo_id: int | None = None
    photo_ids: list[int] | None = None  # group actions ("alle behalten", "zurücksetzen")
    manual: Literal["keep", "reject"] | None = None


class SeenRequest(BaseModel):
    items: list[tuple[int, Annotated[int, Field(ge=1, le=7)], bool]]  # id, how, shown proposal


class ApplyRequest(BaseModel):
    photo_ids: list[int]  # exactly the photos the confirmation dialog showed


class BatchRequest(BaseModel):
    batch_id: str | None = None  # None = the latest batch


def create_app(
    state: AppState | None = None,
    token: str | None = None,
    allowed_hosts: set[str] | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    static = static_dir or web_dir()
    state = state or AppState()
    token = token or new_token()
    app = FastAPI(
        title="FotoDünner", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.fd = state
    app.middleware("http")(make_guard(token, allowed_hosts or {"127.0.0.1"}))

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": __version__}

    @app.get("/api/recent")
    def recent() -> list[dict[str, Any]]:
        return state.recent()

    @app.post("/api/pick-folder")
    def pick_folder() -> dict[str, str | None]:
        if state.folder_picker is None:
            raise ApiError(501, "Die Ordnerauswahl ist nur im Programmfenster verfügbar.")
        return {"path": state.folder_picker()}

    @app.post("/api/album/open")
    def open_album(body: OpenRequest) -> dict[str, Any]:
        return state.open_album(body.path, body.recursive)

    @app.post("/api/album/{album_id}/analyze")
    def analyze(album_id: str, body: AnalyzeRequest | None = None) -> dict[str, str]:
        state.start_analysis(album_id, skip_offline=bool(body and body.skip_offline))
        return {"state": "running"}

    @app.post("/api/album/{album_id}/cancel")
    def cancel(album_id: str) -> dict[str, str]:
        state.cancel_analysis(album_id)
        return {"state": "cancelling"}

    @app.get("/api/album/{album_id}/events")
    async def events(album_id: str, request: Request) -> StreamingResponse:
        session = state.session(album_id)

        async def stream() -> AsyncIterator[str]:
            last_version, sent = -1, 0
            while not await request.is_disconnected():
                version, payload, sent = session.snapshot(sent)
                if version != last_version or payload["finished"]:
                    last_version = version
                    yield f"data: {json.dumps(payload)}\n\n"
                if payload["state"] != "running":
                    break
                await asyncio.sleep(SSE_INTERVAL_S)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/album/{album_id}/view")
    def view(album_id: str) -> dict[str, Any]:
        return state.view(album_id)

    @app.post("/api/album/{album_id}/settings")
    def settings(album_id: str, body: SettingsRequest) -> dict[str, Any]:
        return state.update_settings(album_id, body.model_dump(exclude_none=True))

    @app.post("/api/album/{album_id}/decision")
    def decision(album_id: str, body: DecisionRequest) -> dict[str, Any]:
        ids = list(body.photo_ids or []) + ([body.photo_id] if body.photo_id is not None else [])
        if not ids:
            raise ApiError(422, "Kein Bild angegeben.")
        return state.set_decision(album_id, ids, body.manual)

    @app.post("/api/album/{album_id}/seen")
    def seen(album_id: str, body: SeenRequest) -> dict[str, bool]:
        state.add_seen(album_id, body.items)
        return {"ok": True}

    @app.post("/api/album/{album_id}/rethin")
    def rethin(album_id: str) -> dict[str, Any]:
        return state.rethin(album_id)

    @app.post("/api/album/{album_id}/apply/preview")
    def apply_preview(album_id: str) -> dict[str, Any]:
        return state.apply_preview(album_id)

    @app.post("/api/album/{album_id}/apply")
    def apply(album_id: str, body: ApplyRequest) -> dict[str, Any]:
        return state.start_apply(album_id, body.photo_ids)

    @app.post("/api/album/{album_id}/apply/retry")
    def apply_retry(album_id: str, body: BatchRequest) -> dict[str, Any]:
        if not body.batch_id:
            raise ApiError(422, "Kein Vorgang angegeben.")
        return state.start_retry(album_id, body.batch_id)

    @app.get("/api/album/{album_id}/apply/status")
    def apply_status(album_id: str) -> dict[str, Any]:
        return state.job_status(album_id)

    @app.post("/api/album/{album_id}/undo")
    def undo(album_id: str, body: BatchRequest | None = None) -> dict[str, Any]:
        return state.start_undo(album_id, body.batch_id if body else None)

    @app.post("/api/album/{album_id}/open-folder")
    def open_folder(album_id: str) -> dict[str, bool]:
        state.open_reject_folder(album_id)
        return {"ok": True}

    @app.get("/api/album/{album_id}/photo/{photo_id}/thumb")
    def thumb(album_id: str, photo_id: int, size: Literal["s", "l"] = "s") -> FileResponse:
        state.photo_row(album_id, photo_id)  # only photos of this album, never arbitrary paths
        small, large = thumb_paths(state.session(album_id).store.thumbs_dir, photo_id)
        path = small if size == "s" else large
        if not path.is_file():
            raise ApiError(404, "Vorschau noch nicht erstellt.")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": THUMB_CACHE})

    @app.get("/api/album/{album_id}/photo/{photo_id}/full")
    def full(album_id: str, photo_id: int) -> Response:
        data = state.full_image(album_id, photo_id)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": THUMB_CACHE})

    @app.get("/api/album/{album_id}/photo/{photo_id}/info")
    def info(album_id: str, photo_id: int) -> dict[str, Any]:
        row = state.photo_row(album_id, photo_id)
        rel = Path(row["rel_path"])
        return {
            "id": row["id"],
            "name": rel.name,
            "folder": str(rel.parent) if str(rel.parent) != "." else "",
            "size": row["size"],
            "width": row["width"],
            "height": row["height"],
            "taken_at": row["taken_at"],
            "taken_at_source": row["taken_at_source"],
            "camera": row["camera"],
            "exposure_bias": row["exposure_bias"],
            "rating": row["rating"],
            "status": row["status"],
            "error": row["error"],
            "companions": json.loads(row["companions"] or "[]"),
            "features": state.photo_features(album_id, photo_id),
        }

    @app.post("/api/instance/focus")
    def focus() -> dict[str, bool]:
        if state.on_focus:
            state.on_focus()
        return {"ok": True}

    @app.post("/api/shutdown")
    def shutdown() -> dict[str, bool]:
        if state.on_shutdown:
            state.on_shutdown()
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
