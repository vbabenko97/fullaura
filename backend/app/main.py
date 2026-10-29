"""Serve the API and, when built, the React frontend from one origin."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.catalog import Catalog
from backend.app.config import Settings
from backend.app.planner import (
    BASETEN_URL,
    SPARK_MODEL,
    PlanRequest,
    SparkClient,
    plan_looks,
    result_json,
)

PREVIEW_DISABLED_DETAIL = "Photo previews are unavailable pending the T-02 feasibility result"


def create_app(settings: Settings | None = None, spark: SparkClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    index_file = settings.web_dist / "index.html"
    assets_dir = settings.web_dist / "assets"
    if settings.environment == "production" and not index_file.is_file():
        raise RuntimeError("Build the frontend before starting in production")

    app = FastAPI(
        title="FullAura API",
        version="0.1.0",
        docs_url="/api/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment == "development" else None,
    )

    @app.get("/api/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Process health; this does not contact or verify Meta or Azure."""
        return {"status": "ok", "service": "fullaura"}

    @app.get("/api/ready", tags=["operations"])
    def ready() -> JSONResponse:
        """A production instance also needs its frontend entry point."""
        usable = settings.environment == "development" or index_file.is_file()
        return JSONResponse(
            {"status": "ready" if usable else "not_ready"},
            status_code=200 if usable else 503,
        )

    @app.post("/api/previews", tags=["previews"], status_code=503)
    def previews_disabled() -> JSONResponse:
        """Keep photo processing unavailable pending the formal T-02 result."""
        return JSONResponse(
            {"detail": PREVIEW_DISABLED_DETAIL},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    # Planning calls a paid model, so it stays off in production until T-04 adds per-session
    # limits, the 18+ confirmation and the photo-processing notice (docs/TASKS.md).
    if settings.environment == "development":
        catalog = Catalog.model_validate_json(settings.catalog_file.read_text(encoding="utf-8"))

        @app.get("/api/catalog", tags=["catalog"])
        def get_catalog() -> JSONResponse:
            """Expose catalog facts only in development."""
            return JSONResponse(catalog.model_dump(mode="json"))

        @app.post("/api/plans", tags=["planning"])
        def create_plan(request: PlanRequest) -> JSONResponse:
            """Three looks from the catalog, checked against every hard constraint."""
            client = spark
            if client is None:
                if not settings.baseten_api_key:
                    return JSONResponse({"detail": "BASETEN_API_KEY is not set"}, status_code=503)
                client = SparkClient(
                    settings.baseten_api_key,
                    model=settings.baseten_model or SPARK_MODEL,
                    base_url=settings.baseten_base_url or BASETEN_URL,
                )
            return JSONResponse(
                result_json(
                    plan_looks(
                        request.brief,
                        request.state,
                        catalog,
                        client,
                        replacement=request.replacement,
                    )
                )
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
            {"service": "fullaura", "status": "frontend_not_built"},
            status_code=503,
        )

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    return app


app = create_app()
