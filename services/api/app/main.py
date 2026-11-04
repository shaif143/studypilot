"""FastAPI application factory and process entrypoint.

Security posture for the local backend:

* bound to loopback by default;
* every route but ``/healthz`` requires the shared token;
* CORS restricted to the dev server and the Tauri webview origins;
* no wildcard origins and no credentials in CORS, so a web page the student
  happens to have open cannot drive the local API.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import (
    routes_analytics,
    routes_assessment,
    routes_auth,
    routes_courses,
    routes_documents,
    routes_flashcards,
    routes_health,
    routes_notes,
    routes_privacy,
    routes_settings,
    routes_tutor,
)
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.models.base import ConsentRequiredError, ProviderUnavailableError
from app.runtime import RuntimeContext
from app.storage.repositories import NotFoundError
from app.supervision import watch_parent_from_env

log = logging.getLogger(__name__)

DESCRIPTION = """
StudyPilot's local API. Runs on this device; your documents and progress stay in
the application data directory unless you explicitly enable a cloud model provider.
""".strip()


def create_app(
    settings: Settings | None = None, *, context: RuntimeContext | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, allow_sensitive=settings.log_sensitive_content)

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        instance.state.context = context or RuntimeContext.create(settings)
        try:
            yield
        finally:
            existing: RuntimeContext | None = getattr(instance.state, "context", None)
            # A context passed in by a test is owned by that test.
            if existing is not None and context is None:
                existing.close()

    app = FastAPI(
        title="StudyPilot API",
        lifespan=lifespan,
        version=__version__,
        description=DESCRIPTION,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # A hosted deployment grants nothing cross-origin unless its environment
    # names the origins explicitly: the development defaults never apply, so
    # localhost on a student's machine cannot read a hosted account. See
    # `Settings.effective_cors_origins`.
    #
    # Credentials are never allowed, on any origin. The desktop origins send
    # no cookies anyway, and refusing them keeps a misconfigured origin from
    # carrying a session — which is also why a cross-origin deployment has to
    # use bearer tokens rather than the built-in cookie sessions.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.effective_cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "x-studypilot-token", "authorization"],
        max_age=600,
    )

    # --- error translation ----------------------------------------------
    @app.exception_handler(NotFoundError)
    def _not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        # Deliberately the same response whether the row is absent or owned by
        # someone else, so the API cannot be used to probe for other users' data.
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "Not found."},
        )

    @app.exception_handler(ConsentRequiredError)
    def _consent_required(_request: Request, exc: ConsentRequiredError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc), "code": "consent_required"},
        )

    @app.exception_handler(ProviderUnavailableError)
    def _provider_unavailable(_request: Request, exc: ProviderUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc), "code": "provider_unavailable"},
        )

    @app.exception_handler(ValueError)
    def _bad_request(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})

    # --- routes ----------------------------------------------------------
    app.include_router(routes_health.public_router)
    app.include_router(routes_health.router)
    # Accounts exist only where there is something to sign in to.
    if settings.is_hosted:
        app.include_router(routes_auth.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_courses.router)
    app.include_router(routes_documents.router)
    app.include_router(routes_tutor.router)
    app.include_router(routes_assessment.router)
    app.include_router(routes_flashcards.router)
    app.include_router(routes_notes.router)
    app.include_router(routes_analytics.router)
    app.include_router(routes_privacy.router)

    return app


app = create_app()


def main() -> None:
    """Entrypoint for ``studypilot-api`` and for the packaged sidecar binary."""
    import uvicorn

    settings = get_settings()
    # Writes the token file if it does not exist, so the shell can read it.
    settings.resolve_api_token()
    # When launched as a desktop sidecar, exit as soon as the shell does rather
    # than surviving it as an orphan holding the port and the database.
    watch_parent_from_env()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
