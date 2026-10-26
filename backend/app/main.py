"""Serve the API and, when built, the React frontend from one origin."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    index_file = settings.web_dist / "index.html"
    assets_dir = settings.web_dist / "assets"
    if settings.environment == "production" and not index_file.is_file():
        raise RuntimeError("Build the frontend before starting in production")

    app = FastAPI(
        title="LookPilot API",
        version="0.1.0",
        docs_url="/api/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment == "development" else None,
    )

    @app.get("/api/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Process health; this does not contact or verify Meta or Azure."""
        return {"status": "ok", "service": "lookpilot"}

    @app.get("/api/ready", tags=["operations"])
    def ready() -> JSONResponse:
        """A production instance also needs its frontend entry point."""
        usable = settings.environment == "development" or index_file.is_file()
        return JSONResponse(
            {"status": "ready" if usable else "not_ready"},
            status_code=200 if usable else 503,
        )

    # Reserve the API prefix so an unknown API URL can never serve HTML.
    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def missing_api(path: str) -> JSONResponse:
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    @app.get("/", include_in_schema=False, response_model=None)
    def home() -> FileResponse | JSONResponse:
        if index_file.is_file():
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            {"service": "lookpilot", "status": "frontend_not_built"},
            status_code=503,
        )

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    return app


app = create_app()
