"""Session protection for the local API (04 §5, T12).

The window opens ``/?t=<token>``; the token becomes an HttpOnly, SameSite=Strict cookie.
Every ``/api`` request needs the cookie, every non-GET request also the custom header, and the
Host header must be our own address – this keeps web pages in a browser away from the API.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE_NAME = "fd_session"
POST_HEADER = "X-FotoDuenner"
SAFE_METHODS = frozenset({"GET", "HEAD"})

FORBIDDEN_PAGE = """<!doctype html><html lang="de"><meta charset="utf-8"><title>FotoDünner</title>
<body style="background:#0c0d0e;color:#e9e6df;font-family:Segoe UI,sans-serif;padding:3rem">
<p>Bitte öffnen Sie FotoDünner über das Programmfenster.</p></body></html>"""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _valid(value: str | None, token: str) -> bool:
    return bool(value) and secrets.compare_digest(value.encode(), token.encode())  # type: ignore[union-attr]


def make_guard(
    token: str, allowed_hosts: set[str]
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        path = request.url.path
        has_session = _valid(request.cookies.get(COOKIE_NAME), token)

        if path == "/":
            if _valid(request.query_params.get("t"), token):
                response = RedirectResponse("/", status_code=303)  # drop the token from the URL
                response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", path="/")
                return response
            if not has_session:
                return HTMLResponse(FORBIDDEN_PAGE, status_code=403)
        elif path.startswith("/api/"):
            if not has_session:
                return JSONResponse({"detail": "no session"}, status_code=401)
            if request.method not in SAFE_METHODS and request.headers.get(POST_HEADER) != "1":
                return JSONResponse({"detail": "missing header"}, status_code=403)
        return await call_next(request)

    return guard
