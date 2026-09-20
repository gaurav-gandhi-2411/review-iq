"""Path-scoped CORS: a second, narrower allowlist that applies to ONE route only.

The global CORSMiddleware in app/main.py serves the dashboard/web-app origins from
settings.allowed_origins. The public marketing site (samidhareviews.xyz) needs to POST
to /leads from the browser, but must NOT be added to that global list -- that would let
the marketing origin call every authenticated endpoint cross-origin as well. This wrapper
runs its own CORSMiddleware instance for requests whose path is exactly `path` and passes
every other request straight through untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class PathScopedCORSMiddleware:
    """Apply an independent CORS policy to requests for a single exact path."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        path: str,
        allow_origins: Sequence[str],
        allow_methods: Sequence[str],
        allow_headers: Sequence[str],
        max_age: int = 600,
    ) -> None:
        if "*" in allow_origins:
            raise ValueError("PathScopedCORSMiddleware must not allow '*' -- list origins.")
        self._app = app
        self._path = path
        self._cors = CORSMiddleware(
            app,
            allow_origins=list(allow_origins),
            allow_methods=list(allow_methods),
            allow_headers=list(allow_headers),
            allow_credentials=False,
            max_age=max_age,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].rstrip("/") == self._path:
            await self._cors(scope, receive, send)
            return
        await self._app(scope, receive, send)
