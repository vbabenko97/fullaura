"""Serve the API and, when built, the React frontend from one origin."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool
from starlette.requests import Request
from starlette.responses import Response

from backend.app.catalog import Catalog
from backend.app.config import Settings
from backend.app.meta_images import ImageRequestError, MuseImageClient
from backend.app.photos import (
    PREVIEW_DEADLINE_S,
    AsyncImageEditor,
    PhotoInputError,
    PhotoProcessingError,
    PhotoTooLargeError,
    PreviewResponse,
    read_preview_uploads,
    wait_for_preview_or_disconnect,
)
from backend.app.planner import (
    BASETEN_URL,
    SPARK_MODEL,
    PlanRequest,
    SparkClient,
    plan_looks,
    result_json,
)
from backend.app.sessions import (
    DEMO_SESSION_COOKIE,
    MAX_PLAN_REQUESTS_PER_SESSION,
    SESSION_TTL_SECONDS,
    DemoSessionStore,
    DemoSessionView,
)

PREVIEW_DISABLED_DETAIL = "Photo previews are unavailable pending the T-02 feasibility result"
PREVIEW_CONFIG_DETAIL = "Photo previews need MODEL_API_KEY in the development environment"


class AgeConfirmation(BaseModel):
    over_18: StrictBool


def _session_payload(
    enabled: bool,
    session: DemoSessionView,
) -> dict[str, bool | int]:
    return {
        "enabled": enabled,
        "age_confirmed": session.age_confirmed,
        "plan_requests_used": session.plan_requests_used,
        "plan_requests_limit": MAX_PLAN_REQUESTS_PER_SESSION,
        "plan_requests_remaining": session.plan_requests_remaining,
    }


def _set_demo_cookie(response: Response, session_id: str, settings: Settings) -> None:
    response.set_cookie(
        key=DEMO_SESSION_COOKIE,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )


def _preview_error(detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def create_app(
    settings: Settings | None = None,
    spark: SparkClient | None = None,
    image_editor: AsyncImageEditor | None = None,
) -> FastAPI:
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
    demo_enabled = settings.environment == "development" or settings.public_demo_enabled
    demo_sessions = DemoSessionStore()

    @app.middleware("http")
    async def refresh_demo_session_cookie(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        session_id = request.cookies.get(DEMO_SESSION_COOKIE)
        if session_id and demo_sessions.refresh(session_id):
            _set_demo_cookie(response, session_id, settings)
        return response

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

    @app.get("/api/session", tags=["demo"])
    def get_demo_session(request: Request) -> JSONResponse:
        """Create or refresh the anonymous session used for consent and request limits."""
        session_id, session = demo_sessions.get_or_create(request.cookies.get(DEMO_SESSION_COOKIE))
        response = JSONResponse(
            _session_payload(demo_enabled, session),
            headers={"Cache-Control": "no-store"},
        )
        if request.cookies.get(DEMO_SESSION_COOKIE) != session_id:
            _set_demo_cookie(response, session_id, settings)
        return response

    @app.post("/api/session/confirm-age", tags=["demo"])
    def confirm_demo_age(request: Request, confirmation: AgeConfirmation) -> JSONResponse:
        """Store an explicit 18+ confirmation for this anonymous browser session."""
        if not demo_enabled:
            return JSONResponse(
                {"detail": "The planning demo is closed"},
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        if not confirmation.over_18:
            return JSONResponse(
                {"detail": "You must confirm that you are 18 or older to use planning"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        session = demo_sessions.confirm_age(request.cookies.get(DEMO_SESSION_COOKIE))
        if session is None:
            return JSONResponse(
                {"detail": "The demo session expired; refresh the page to continue"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            _session_payload(demo_enabled, session),
            headers={"Cache-Control": "no-store"},
        )

    def preview_status() -> tuple[bool, str | None]:
        if settings.environment != "development":
            return False, "Photo previews are available only in development"
        if not settings.t02_previews_passed:
            return False, PREVIEW_DISABLED_DETAIL
        if not settings.model_api_key and image_editor is None:
            return False, PREVIEW_CONFIG_DETAIL
        return True, None

    @app.get("/api/previews/status", tags=["previews"])
    def get_preview_status() -> JSONResponse:
        """Report whether local photo previews are available without receiving photos."""
        enabled, detail = preview_status()
        return JSONResponse(
            {"enabled": enabled, "detail": detail},
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/api/previews",
        tags=["previews"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["portrait", "full_body", "prompt"],
                            "properties": {
                                "portrait": {"type": "string", "format": "binary"},
                                "full_body": {"type": "string", "format": "binary"},
                                "prompt": {"type": "string", "maxLength": 3000},
                            },
                        }
                    }
                },
            }
        },
    )
    async def create_preview(request: Request) -> Response:
        """Create one request-scoped preview when T-02 is enabled in development."""
        enabled, detail = preview_status()
        if not enabled:
            return _preview_error(detail or PREVIEW_DISABLED_DETAIL, 503)

        editor = image_editor
        if editor is None:
            editor = MuseImageClient(settings.model_api_key, settings.meta_image_model)

        deadline = time.monotonic() + PREVIEW_DEADLINE_S
        portrait: bytearray | None = None
        full_body: bytearray | None = None
        try:
            async with asyncio.timeout_at(deadline):
                prompt, portrait, full_body = await read_preview_uploads(request)
                preview = await wait_for_preview_or_disconnect(
                    request,
                    prompt,
                    portrait,
                    full_body,
                    editor,
                    deadline=deadline,
                )
        except PhotoTooLargeError as error:
            return _preview_error(str(error), 413)
        except PhotoInputError as error:
            return _preview_error(str(error), 400)
        except TimeoutError:
            return _preview_error("Preview generation took too long", 504)
        except ImageRequestError as error:
            if error.timed_out:
                return _preview_error("Preview generation took too long", 504)
            return _preview_error("The image provider could not complete this preview", 502)
        except PhotoProcessingError:
            return _preview_error("The image provider returned an unusable preview", 502)
        finally:
            if portrait is not None:
                portrait.clear()
            if full_body is not None:
                full_body.clear()

        if preview is None:
            return Response(status_code=499, headers={"Cache-Control": "no-store"})
        return PreviewResponse(preview)

    # Production routes remain closed until the pilot operator opts in. Each planning request
    # still requires an age-confirmed session and consumes one of three per-session requests.
    if demo_enabled:
        catalog = Catalog.model_validate_json(settings.catalog_file.read_text(encoding="utf-8"))

        @app.get("/api/catalog", tags=["catalog"])
        def get_catalog() -> JSONResponse:
            """Expose catalog facts while the planning demo is enabled."""
            return JSONResponse(catalog.model_dump(mode="json"))

        @app.post("/api/plans", tags=["planning"])
        def create_plan(plan_request: PlanRequest, request: Request) -> JSONResponse:
            """Three looks from the catalog, checked against every hard constraint."""
            session_id = request.cookies.get(DEMO_SESSION_COOKIE)
            access, session = demo_sessions.check_plan_access(session_id)
            if access == "missing":
                return JSONResponse(
                    {"detail": "Start a demo session before requesting a plan"},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            if access == "age_required":
                return JSONResponse(
                    {"detail": "Confirm that you are 18 or older before requesting a plan"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
            if access == "limit":
                return JSONResponse(
                    {"detail": "This demo session has reached its three planning requests"},
                    status_code=429,
                    headers={
                        "Cache-Control": "no-store",
                        "X-Plan-Requests-Remaining": str(
                            session.plan_requests_remaining if session else 0
                        ),
                    },
                )

            client = spark
            if client is None:
                if not settings.baseten_api_key:
                    return JSONResponse(
                        {"detail": "The planning model is not configured"},
                        status_code=503,
                        headers={"Cache-Control": "no-store"},
                    )
                client = SparkClient(
                    settings.baseten_api_key,
                    model=settings.baseten_model or SPARK_MODEL,
                    base_url=settings.baseten_base_url or BASETEN_URL,
                )
            access, session = demo_sessions.reserve_plan_request(session_id)
            if access == "limit":
                return JSONResponse(
                    {"detail": "This demo session has reached its three planning requests"},
                    status_code=429,
                    headers={
                        "Cache-Control": "no-store",
                        "X-Plan-Requests-Remaining": str(
                            session.plan_requests_remaining if session else 0
                        ),
                    },
                )
            if access != "ok" or session is None:
                return JSONResponse(
                    {"detail": "The demo session expired; refresh the page to continue"},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            response = JSONResponse(
                result_json(
                    plan_looks(
                        plan_request.brief,
                        plan_request.state,
                        catalog,
                        client,
                        replacement=plan_request.replacement,
                    )
                ),
                headers={
                    "Cache-Control": "no-store",
                    "X-Plan-Requests-Remaining": str(session.plan_requests_remaining),
                },
            )
            return response

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


# Uvicorn configures only its own loggers, which would drop the planner's INFO usage records.
# The handler covers this package alone, so library request logs stay off. Every record at INFO
# or above from this package reaches the production log, so none may hold what a person entered.
_app_log_handler = logging.StreamHandler()
_app_log_handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
logging.getLogger("backend.app").addHandler(_app_log_handler)
logging.getLogger("backend.app").setLevel(logging.INFO)

app = create_app()
