"""Serve the API and, when built, the React frontend from one origin.

Production keeps everything except health, readiness and the session status closed unless
FULLAURA_BETA_ENABLED=true; the closed beta then serves invited sessions only. Every model call
first passes the pilot's checks, which count it against the invite's quota and reserve its
maximum cost in the shared ledger (backend/app/pilot.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal, Self

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, ValidationError, model_validator
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.catalog import Catalog, Material, Record
from backend.app.concepts import RESERVED_HTTP_ATTEMPTS, concept_json, concept_prompt, design_look
from backend.app.config import Settings
from backend.app.meta_images import ImageRequestError, MuseImageClient
from backend.app.photos import (
    PREVIEW_DEADLINE_S,
    AsyncImageEditor,
    PhotoInputError,
    PhotoProcessingError,
    PhotoTooLargeError,
    PreviewResponse,
    preview_prompt,
    read_preview_uploads,
    wait_for_preview_or_disconnect,
)
from backend.app.pilot import (
    IMAGE_MICRO_USD,
    Budget,
    Operation,
    OperationKind,
    Pilot,
    PilotLimits,
    Refusal,
    Refused,
    TryOnIssue,
    planning_cost_micro_usd,
    planning_reserve_micro_usd,
)
from backend.app.planner import (
    BASETEN_URL,
    GENERATION_ATTEMPTS,
    HTTP_ATTEMPTS_PER_CALL,
    MAX_TOKENS,
    SPARK_MODEL,
    Brief,
    Outcome,
    PlanRequest,
    PlanResult,
    SparkClient,
    Usage,
    check_complete_plan,
    infeasibility,
    plan_looks,
    result_json,
    with_cheapest_offers,
)
from backend.app.sessions import (
    SESSION_COOKIE,
    SESSION_COOKIE_MAX_AGE_S,
    ConceptMemory,
    FindMemory,
    PlanMemory,
    PlanRecord,
    Session,
    read_session,
    sign_session,
)
from backend.app.shopper import (
    FIND_RESERVE_MICRO_USD,
    FindOutcome,
    FindRequest,
    FindResult,
    MetaResponses,
    ShopAgent,
    find_json,
    find_prompt,
)
from backend.app.shops import ShopIndex
from backend.app.state import Budget as LookBudget
from backend.app.state import Fit, FootwearChoice, State
from backend.app.store import Store, StoreUnavailable, open_store

OPERATION_HEADER = "X-Operation-Id"
PLAN_TOKEN_HEADER = "X-Plan-Token"
CONCEPT_TOKEN_HEADER = "X-Concept-Token"
FIND_TOKEN_HEADER = "X-Find-Token"
LOOK_HEADER = "X-Look-Id"
NO_STORE = {"Cache-Control": "no-store"}
REFUSAL_STATUS = {
    Refusal.AGE_REQUIRED: 403,
    Refusal.PHOTO_CONSENT_REQUIRED: 403,
    Refusal.PILOT_ENDED: 403,
    Refusal.QUOTA: 429,
    Refusal.BUSY: 409,
    Refusal.DUPLICATE: 409,
    Refusal.BUDGET: 503,
    Refusal.ACCOUNTING_UNAVAILABLE: 503,
}
IMAGE_ATTEMPTS = 3  # MuseImageClient.max_attempts
# The largest body the app sends, a restore of a full saved plan, is about 12 KB even when every
# character of its text needs an escape (docs/DECISIONS.md, D-61).
MAX_BODY_BYTES = 64 * 1024
OWN_BODY_LIMIT = "/api/previews"  # reads its upload itself, after its access checks (photos.py)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
# Images may come from a shop's https host (T-11); a try-on result and a chosen photo are blobs.
PAGE_POLICY = (
    "default-src 'self'; img-src 'self' blob: https:; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
HSTS = "max-age=31536000"

logger = logging.getLogger(__name__)


class InviteCode(BaseModel):
    code: Annotated[str, Field(max_length=64)]


class AgeConfirmation(BaseModel):
    over_18: StrictBool


class PhotoConsent(BaseModel):
    consent: StrictBool


class PlanSubmission(Record):
    """A planning request. The server adds the plan it last checked for this browser session,
    named by plan_token, so the browser never sends a plan here."""

    brief: Brief
    state: dict[str, Any]
    replacement: dict[str, Any] | None = None
    plan_token: str | None = None


class ConceptState(Record):
    """What Design My Look checks: the budget, excluded materials, shoes and fit. Owned items
    and locks are not part of this mode."""

    budget: LookBudget
    material_exclusions: frozenset[Material] = frozenset()
    footwear: FootwearChoice = FootwearChoice.ANY
    fit: Fit = Fit()


class ConceptSubmission(Record):
    brief: Brief
    state: ConceptState


class RestoreRequest(Record):
    """A plan the browser saved earlier, to check again against the current catalog."""

    brief: Brief
    state: State
    catalog_id: str


class FeedbackAnswers(Record):
    would_wear: StrictBool  # would wear at least one of the looks
    usefulness: Annotated[int, Field(ge=1, le=5)]
    try_on: Annotated[int, Field(ge=1, le=5)] | Literal["cannot_assess"]
    # None when the question was skipped; answers saved before it existed have no such key
    try_on_issues: tuple[TryOnIssue, ...] | None = None
    catalog_problem: Annotated[str, Field(max_length=300)] = ""
    comment: Annotated[str, Field(max_length=500)] = ""

    @model_validator(mode="after")
    def _issues_go_with_a_judged_try_on(self) -> Self:
        issues = self.try_on_issues
        if issues is None:
            return self
        if not issues or len(set(issues)) != len(issues):
            raise ValueError("List each try-on issue once, or send null to skip the question")
        if TryOnIssue.NONE_NOTICED in issues and len(issues) > 1:
            raise ValueError("'No problems noticed' cannot go with a problem")
        if self.try_on == "cannot_assess":
            raise ValueError("Try-on issues need a try-on score")
        return self


def _error(
    detail: str, status_code: int, reason: str | None = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "reason": reason},
        status_code=status_code,
        headers={**NO_STORE, **(headers or {})},
    )


def _refusal(refused: Refused) -> JSONResponse:
    return _error(refused.detail, REFUSAL_STATUS[refused.reason], refused.reason.value)


def _invalid(error: ValidationError) -> JSONResponse:
    details = error.errors(include_url=False, include_input=False, include_context=False)
    return JSONResponse({"detail": details, "reason": "invalid"}, status_code=422, headers=NO_STORE)


class BodyLimit:
    """Refuses an API request whose body is larger than max_bytes before any route reads it: by
    its Content-Length, or, for a chunked or understated body, as soon as the bytes received pass
    the limit. Routes parse a JSON body before their access checks, so this holds the body, at
    most max_bytes of it, and then hands it to the route. The try-on route has its own limits."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"] if scope["type"] == "http" else ""
        if not path.startswith("/api/") or path == OWN_BODY_LIMIT:
            await self.app(scope, receive, send)
            return
        try:
            declared = int(Headers(scope=scope).get("content-length", "0"))
        except ValueError:
            declared = 0  # the bytes received still count
        if declared > self.max_bytes:
            await self._refuse(scope, receive, send)
            return
        chunks: list[bytes] = []
        received = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client disconnected, so no one waits for an answer
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > self.max_bytes:
                await self._refuse(scope, receive, send)
                return
            chunks.append(chunk)
            more_body = message.get("more_body", False)
        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()  # such as a later disconnect
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Closing the connection stops a client that keeps sending the rest of the body.
        detail = f"The request is larger than {self.max_bytes // 1024} KiB"
        await _error(detail, 413, "invalid", {"Connection": "close"})(scope, receive, send)


class SecurityHeaders:
    """Tells the browser what it may do with every response. Only the page gets the content
    policy: the development API docs load their scripts from a CDN, and a JSON response runs no
    content. HTTPS is enforced only in production, because development runs on plain HTTP."""

    def __init__(self, app: ASGIApp, https: bool) -> None:
        self.app = app
        self.https = https

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        added = dict(SECURITY_HEADERS)
        if not scope["path"].startswith("/api/"):
            added["Content-Security-Policy"] = PAGE_POLICY
        if self.https:
            added["Strict-Transport-Security"] = HSTS

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in added.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, with_headers)


def create_app(
    settings: Settings | None = None,
    spark: SparkClient | None = None,
    image_editor: AsyncImageEditor | None = None,
    store: Store | None = None,
    shop_agent: Callable[[FindRequest, Callable[[str], None]], FindResult] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    index_file = settings.web_dist / "index.html"
    assets_dir = settings.web_dist / "assets"
    production = settings.environment == "production"
    if production and not index_file.is_file():
        raise RuntimeError("Build the frontend before starting in production")
    if production and settings.beta_enabled:
        if len(settings.session_secret) < 32:
            raise RuntimeError("The beta needs FULLAURA_SESSION_SECRET of 32 or more characters")
        if settings.store_url == "memory://" and store is None:
            raise RuntimeError("The beta needs a persistent FULLAURA_STORE_URL")
        if not settings.invite_hashes:
            raise RuntimeError("The beta needs FULLAURA_INVITE_HASHES")
        # The end closes model calls whatever the ledger holds, so deleting the records cannot
        # reopen the budgets; the purge time tells participants when the records go.
        if settings.pilot_ends_at is None:
            raise RuntimeError("The beta needs FULLAURA_PILOT_ENDS_AT")
        if settings.records_purge_at is None:
            raise RuntimeError("The beta needs FULLAURA_RECORDS_PURGE_AT")

    app = FastAPI(
        title="FullAura API",
        version="0.2.0",
        docs_url="/api/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment == "development" else None,
    )
    app.add_middleware(BodyLimit, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(SecurityHeaders, https=production)  # the last added runs first

    @app.exception_handler(RequestValidationError)
    def unreadable_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # FastAPI's own answer echoes the input, and some input cannot be written back as JSON: a
        # lone surrogate, or nesting too deep to encode. Either would end in a 500.
        return _error("This request is not valid", 422, "invalid")

    secret = settings.session_secret.encode() or secrets.token_bytes(32)
    pilot = Pilot(
        store or open_store(settings.store_url),
        PilotLimits(
            plan_requests=settings.plan_requests_per_invite,
            preview_requests=settings.preview_requests_per_invite,
            budgets_micro_usd=(
                (Budget.PLANNING, round(settings.planning_budget_usd * 1_000_000)),
                (Budget.IMAGES, round(settings.image_budget_usd * 1_000_000)),
            ),
            ends_at=settings.pilot_ends_at,
            records_purge_at=settings.records_purge_at,
        ),
        settings.invite_hashes,
    )
    plans = PlanMemory()
    concepts = ConceptMemory()
    finds = FindMemory()
    catalog = Catalog.model_validate_json(settings.catalog_file.read_text(encoding="utf-8"))
    plan_reserve = planning_reserve_micro_usd(
        GENERATION_ATTEMPTS * HTTP_ATTEMPTS_PER_CALL, MAX_TOKENS
    )
    preview_reserve = IMAGE_ATTEMPTS * IMAGE_MICRO_USD
    concept_reserve = planning_reserve_micro_usd(RESERVED_HTTP_ATTEMPTS, MAX_TOKENS)

    def planning_client() -> SparkClient | None:
        if spark is not None:
            return spark
        if not settings.baseten_api_key:
            return None
        return SparkClient(
            settings.baseten_api_key,
            model=settings.baseten_model or SPARK_MODEL,
            base_url=settings.baseten_base_url or BASETEN_URL,
        )

    live_agent: ShopAgent | None = None
    live_agent_lock = threading.Lock()

    def find_agent() -> Callable[[FindRequest, Callable[[str], None]], FindResult] | None:
        nonlocal live_agent
        if not settings.shop_mode:
            return None
        if shop_agent is not None:
            return shop_agent
        if not settings.model_api_key:
            return None
        with live_agent_lock:
            if live_agent is None:
                index = ShopIndex()
                # Read the shops' lists now, so a person's first search does not wait for them.
                threading.Thread(target=index.load, daemon=True).start()
                live_agent = ShopAgent(MetaResponses(settings.model_api_key), index)
            return live_agent

    def preview_editor() -> AsyncImageEditor | None:
        if image_editor is not None:
            return image_editor
        if not settings.model_api_key:
            return None
        return MuseImageClient(settings.model_api_key, settings.meta_image_model)

    def beta_open() -> bool:
        """Once the pilot records are deleted, the beta stays closed, so none reappears."""
        return settings.beta_open and not pilot.purged()

    def session_of(request: Request) -> Session | None:
        session = read_session(secret, request.cookies.get(SESSION_COOKIE))
        return session if session and pilot.knows(session.invite_id) else None

    def session_payload(session: Session | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "beta": beta_open(),
            "invited": session is not None,
            "accounting": False,
            "age_confirmed": False,
            "photo_consent": False,
            "plan_requests_used": 0,
            "plan_requests_limit": pilot.limits.plan_requests,
            "preview_requests_used": 0,
            "preview_requests_limit": pilot.limits.preview_requests,
            "planning_open": False,
            "previews_open": False,
            "pilot_ends_at": settings.pilot_ends_at.isoformat() if settings.pilot_ends_at else None,
            "pilot_ended": pilot.ended(),
            "records_purge_at": (
                settings.records_purge_at.isoformat() if settings.records_purge_at else None
            ),
            "feedback_ids": [],
            "has_plan": False,
            "design_open": False,
            "find_open": False,
        }
        if session is None or not beta_open():
            return payload
        payload["has_plan"] = plans.get(session) is not None
        try:
            status = pilot.status(session.invite_id)
            planning_budget = pilot.budget_open(OperationKind.PLAN, plan_reserve)
            image_budget = pilot.budget_open(OperationKind.PREVIEW, preview_reserve)
            design_budget = settings.design_mode and pilot.budget_open(
                OperationKind.PLAN, concept_reserve
            )
            find_budget = pilot.budget_open(OperationKind.PLAN, FIND_RESERVE_MICRO_USD)
        except StoreUnavailable:
            return payload
        open_window = not pilot.ended()
        payload.update(
            accounting=True,
            age_confirmed=status.age_confirmed,
            photo_consent=status.photo_consent,
            plan_requests_used=status.plan_requests_used,
            preview_requests_used=status.preview_requests_used,
            planning_open=open_window
            and planning_budget
            and planning_client() is not None
            and status.plan_requests_used < pilot.limits.plan_requests,
            previews_open=open_window
            and image_budget
            and preview_editor() is not None
            and status.preview_requests_used < pilot.limits.preview_requests,
            feedback_ids=list(status.feedback_ids),
        )
        payload["design_open"] = bool(payload["planning_open"] and design_budget)
        payload["find_open"] = bool(
            open_window
            and find_budget
            and find_agent() is not None
            and status.plan_requests_used < pilot.limits.plan_requests
        )
        return payload

    def plan_payload(record: PlanRecord, result: PlanResult) -> dict[str, Any]:
        payload = result_json(result)
        payload.update(
            plan_token=record.token, plan_version=record.version, catalog_id=record.catalog_id
        )
        return payload

    def closed() -> JSONResponse:
        return _error("The beta is closed", 404, "closed")

    def not_invited() -> JSONResponse:
        return _error("Open your invite link to continue", 401, "invite_required")

    def operation_id_of(request: Request) -> str | None:
        try:
            return str(uuid.UUID(request.headers.get(OPERATION_HEADER, "")))
        except ValueError:
            return None

    @app.get("/api/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Process health; this does not contact or verify Meta, Baseten or Azure."""
        return {"status": "ok", "service": "fullaura"}

    @app.get("/api/ready", tags=["operations"])
    def ready() -> JSONResponse:
        """A production instance also needs its frontend entry point."""
        usable = settings.environment == "development" or index_file.is_file()
        return JSONResponse(
            {"status": "ready" if usable else "not_ready"},
            status_code=200 if usable else 503,
        )

    @app.get("/api/session", tags=["session"])
    def get_session(request: Request) -> JSONResponse:
        """Whether this browser holds an invite, and what it may still do."""
        return JSONResponse(session_payload(session_of(request)), headers=NO_STORE)

    @app.post("/api/session/invite", tags=["session"])
    def open_invite(invite: InviteCode, request: Request) -> JSONResponse:
        """Exchange an invite code for a signed session cookie."""
        if not beta_open():
            return closed()
        invite_id = pilot.invite_id(invite.code)
        if invite_id is None:
            return _error("This invite code is not valid", 403, "invite_invalid")
        cookie = sign_session(secret, invite_id)
        response = JSONResponse(session_payload(read_session(secret, cookie)), headers=NO_STORE)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=cookie,
            max_age=SESSION_COOKIE_MAX_AGE_S,
            httponly=True,
            secure=production,
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/api/session/confirm-age", tags=["session"])
    def confirm_age(confirmation: AgeConfirmation, request: Request) -> JSONResponse:
        """Record that the invited person confirmed they are 18 or older."""
        session = session_of(request)
        if not beta_open():
            return closed()
        if session is None:
            return not_invited()
        if not confirmation.over_18:
            return _error("FullAura is for people aged 18 or older", 403, "age_required")
        try:
            pilot.confirm_age(session.invite_id)
        except StoreUnavailable:
            return _error("The confirmation could not be saved; try again", 503, "accounting")
        return JSONResponse(session_payload(session), headers=NO_STORE)

    @app.post("/api/session/photo-consent", tags=["session"])
    def photo_consent(consent: PhotoConsent, request: Request) -> JSONResponse:
        """Record or withdraw consent to the photo processing notice."""
        session = session_of(request)
        if not beta_open():
            return closed()
        if session is None:
            return not_invited()
        try:
            pilot.set_photo_consent(session.invite_id, consent.consent)
        except StoreUnavailable:
            return _error("The consent could not be saved; try again", 503, "accounting")
        return JSONResponse(session_payload(session), headers=NO_STORE)

    @app.get("/api/catalog", tags=["catalog"])
    def get_catalog(request: Request) -> JSONResponse:
        """Catalog facts for invited sessions."""
        if not beta_open():
            return closed()
        if session_of(request) is None:
            return not_invited()
        return JSONResponse(catalog.model_dump(mode="json"))

    @app.post("/api/plans", tags=["planning"])
    def create_plan(submission: PlanSubmission, request: Request) -> JSONResponse:
        """Three looks from the catalog, checked against every hard constraint. An update or a
        replacement names the current plan by its token; a failure keeps that plan current."""
        if not beta_open():
            return closed()
        session = session_of(request)
        if session is None:
            return not_invited()
        operation_id = operation_id_of(request)
        if operation_id is None:
            return _error(f"Send a UUID in {OPERATION_HEADER}", 400, "operation_id")

        record = plans.get(session)
        if record is not None and record.operation_id == operation_id:
            # A repeat of the request that produced the current plan: answer it again for free.
            plan = record.state.plan
            check = check_complete_plan(plan, record.state, catalog)
            result = PlanResult(Outcome.SUCCESS, Usage(), plan, check)
            return JSONResponse(plan_payload(record, result), headers=NO_STORE)
        if "plan" in submission.state:
            return _error("Send plan_token instead of a plan", 422, "invalid")
        current = None
        if submission.plan_token is not None:
            current = plans.current(session, submission.plan_token)
            if current is None:
                return _error("This plan has expired; restore it first", 409, "plan_expired")
        try:
            plan_request = PlanRequest.model_validate(
                {
                    "brief": submission.brief,
                    "state": {
                        **submission.state,
                        "plan": current.state.plan if current else None,
                    },
                    "replacement": submission.replacement,
                }
            )
        except ValidationError as error:
            return _invalid(error)

        state = plan_request.state
        conflicts = infeasibility(state, catalog, replacement=plan_request.replacement)
        if conflicts:  # no plan can pass, so no model call and no quota
            try:  # counted for the pilot report; the answer does not depend on it
                rules = [str(c.rule) for c in conflicts]
                pilot.record_infeasible(session.invite_id, operation_id, rules)
            except StoreUnavailable as error:
                logger.warning("infeasible request not counted: %s", error)
            result = PlanResult(Outcome.INFEASIBLE, Usage(), conflicts=tuple(conflicts))
            return JSONResponse({**result_json(result), "catalog_id": catalog.id}, headers=NO_STORE)
        client = planning_client()
        if client is None:
            return _error("The planning model is not configured", 503, "not_configured")
        try:
            operation = pilot.begin(
                session.invite_id, OperationKind.PLAN, operation_id, plan_reserve
            )
        except Refused as refused:
            return _refusal(refused)

        cost: int | None = None
        outcome = "error"
        try:
            result = plan_looks(
                plan_request.brief, state, catalog, client, replacement=plan_request.replacement
            )
            usage = result.usage
            # A call that reported no usage counts at its maximum, like an attempt that failed.
            cost = planning_cost_micro_usd(
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.calls - usage.unmetered_calls,
                usage.http_attempts,
                MAX_TOKENS,
            )
            outcome = result.outcome.value
        finally:
            pilot.finish(operation, cost, outcome)

        if result.outcome is Outcome.SUCCESS and result.plan is not None:
            checked = State.model_validate({**dict(state), "plan": result.plan})
            record = plans.store(session, plan_request.brief, checked, catalog.id, operation_id)
            return JSONResponse(plan_payload(record, result), headers=NO_STORE)
        return JSONResponse({**result_json(result), "catalog_id": catalog.id}, headers=NO_STORE)

    @app.post("/api/concepts", tags=["design"])
    def create_concept(submission: ConceptSubmission, request: Request) -> JSONResponse:
        """Experimental Design My Look: one concept look from the brief alone, and for each of
        its items the closest catalog product as code judges it, or none. Closed unless
        FULLAURA_DESIGN_MODE is on. Every call it makes counts as one planning request."""
        if not beta_open() or not settings.design_mode:
            return closed()
        session = session_of(request)
        if session is None:
            return not_invited()
        operation_id = operation_id_of(request)
        if operation_id is None:
            return _error(f"Send a UUID in {OPERATION_HEADER}", 400, "operation_id")
        current = concepts.get(session)
        if current is not None and current.operation_id == operation_id:
            # A repeat of the request that produced the current concept: answer it again for free.
            return JSONResponse(
                {**current.payload, "concept_token": current.token}, headers=NO_STORE
            )
        client = planning_client()
        if client is None:
            return _error("The planning model is not configured", 503, "not_configured")
        # A concept is one look, so its budget is a limit for that look whatever mode is sent.
        budget = {"amount_cents": submission.state.budget.amount_cents}
        state = State.model_validate({**submission.state.model_dump(), "budget": budget})
        try:
            operation = pilot.begin(
                session.invite_id, OperationKind.PLAN, operation_id, concept_reserve
            )
        except Refused as refused:
            return _refusal(refused)

        cost: int | None = None
        outcome = "error"
        try:
            result = design_look(submission.brief, state, catalog, client)
            usage = result.usage
            cost = planning_cost_micro_usd(
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.calls - usage.unmetered_calls,
                usage.http_attempts,
                MAX_TOKENS,
            )
            outcome = result.outcome.value
        finally:
            pilot.finish(operation, cost, outcome)

        payload = concept_json(result, catalog.id, None)
        if result.concept is None:
            return JSONResponse(payload, headers=NO_STORE)
        stored = concepts.store(session, result.concept, payload, operation_id)
        return JSONResponse({**payload, "concept_token": stored.token}, headers=NO_STORE)

    @app.post("/api/finds", tags=["shopping"])
    def create_find(submission: FindRequest, request: Request) -> Response:
        """Real products for what a person asks for, chosen by the agent and checked by code. The
        answer is a stream of JSON lines: what the agent is doing, then the result. It counts as
        one planning request."""
        if not beta_open():
            return closed()
        session = session_of(request)
        if session is None:
            return not_invited()
        operation_id = operation_id_of(request)
        if operation_id is None:
            return _error(f"Send a UUID in {OPERATION_HEADER}", 400, "operation_id")
        agent = find_agent()
        if agent is None:
            if not settings.shop_mode:
                return closed()
            return _error("The shopping agent is not configured", 503, "not_configured")
        lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
        current = finds.get(session)
        if current is not None and current.operation_id == operation_id:
            # A repeat of the request that produced the current finds: answer it again for free.
            lines.put({"result": {**current.payload, "find_token": current.token}})
            lines.put(None)
        else:
            ready = getattr(agent, "ready", None)
            if ready is not None and not ready():
                return _error("The shops cannot be read right now", 503, "shops_unavailable")
            try:
                operation = pilot.begin(
                    session.invite_id, OperationKind.PLAN, operation_id, FIND_RESERVE_MICRO_USD
                )
            except Refused as refused:
                return _refusal(refused)

            def work() -> None:
                # In its own thread, so a person who leaves does not stop the settling of the cost.
                cost: int | None = None
                outcome = "error"
                answer: dict[str, Any] = {
                    "outcome": FindOutcome.FAILED.value,
                    "message": "The search could not be finished.",
                    "find_token": None,
                    "finds": [],
                    "scope": submission.scope.value,
                    "budget_cents": submission.budget_cents,
                    "total_cents": 0,
                }
                try:
                    result = agent(submission, lambda step: lines.put({"step": step}))
                    cost = result.usage.cost_micro_usd if result.cost_known else None
                    outcome = result.outcome.value
                    answer = find_json(result, submission, None)
                    if result.finds:
                        stored = finds.store(session, result.finds, answer, operation_id)
                        answer = {**answer, "find_token": stored.token}
                except Exception:
                    logger.exception("shopping agent failed")
                finally:
                    # The answer goes out whatever the store does, so the page never waits forever.
                    try:
                        pilot.finish(operation, cost, outcome)
                    except Exception as error:
                        logger.warning("find not settled: %s", type(error).__name__)
                    finally:
                        lines.put({"result": answer})
                        lines.put(None)

            threading.Thread(target=work, daemon=True).start()

        def stream() -> Iterator[bytes]:
            while (line := lines.get()) is not None:
                yield json.dumps(line, ensure_ascii=False).encode() + b"\n"

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson",
            headers={**NO_STORE, "X-Accel-Buffering": "no"},
        )

    @app.post("/api/plans/restore", tags=["planning"])
    def restore_plan(saved: RestoreRequest, request: Request) -> JSONResponse:
        """Check a saved plan again against the current catalog and constraints, with offers
        chosen again, and make it current. No model call."""
        if not beta_open():
            return closed()
        session = session_of(request)
        if session is None:
            return not_invited()
        if saved.state.plan is None:
            return _error("Send the saved plan in state.plan", 422, "invalid")
        plan = with_cheapest_offers(saved.state.plan, saved.state, catalog)
        check = check_complete_plan(plan, saved.state, catalog)
        payload: dict[str, Any] = {"catalog_id": catalog.id, "catalog_changed": False}
        payload["catalog_changed"] = saved.catalog_id != catalog.id
        if not check.passed:
            failed = PlanResult(Outcome.GENERATION_FAILED, Usage(), check=check)
            return JSONResponse(
                {**result_json(failed), **payload, "outcome": "restore_failed"}, headers=NO_STORE
            )
        checked = State.model_validate({**dict(saved.state), "plan": plan})
        record = plans.store(session, saved.brief, checked, catalog.id, None)
        result = PlanResult(Outcome.SUCCESS, Usage(), plan, check)
        return JSONResponse({**plan_payload(record, result), **payload}, headers=NO_STORE)

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
                            "required": ["portrait", "full_body"],
                            "properties": {
                                "portrait": {"type": "string", "format": "binary"},
                                "full_body": {"type": "string", "format": "binary"},
                            },
                        }
                    }
                },
            }
        },
    )
    async def create_preview(request: Request) -> Response:
        """An experimental try-on of one look of the current plan on two upright JPEGs. Every
        access check runs before the body is read; the server writes the prompt."""
        if not beta_open():
            return closed()
        session = session_of(request)
        if session is None:
            return not_invited()
        editor = preview_editor()
        if editor is None:
            return _error("Try-on previews are not configured", 503, "not_configured")
        operation_id = operation_id_of(request)
        if operation_id is None:
            return _error(f"Send a UUID in {OPERATION_HEADER}", 400, "operation_id")
        concept_token = request.headers.get(CONCEPT_TOKEN_HEADER)
        find_token = request.headers.get(FIND_TOKEN_HEADER)
        try:
            if find_token is not None:
                # What the shopping agent found: only what this page shows now
                if not settings.shop_mode:
                    return closed()
                found = finds.current(session, find_token)
                if found is None:
                    return _error("These finds were replaced; search again", 409, "find_expired")
                prompt = find_prompt(found.finds)
            elif concept_token is not None:
                # A Design My Look concept: only the one this page shows now
                if not settings.design_mode:
                    return closed()
                concept = concepts.current(session, concept_token)
                if concept is None:
                    return _error(
                        "This concept was replaced; design the look again", 409, "concept_expired"
                    )
                prompt = concept_prompt(concept.concept)
            else:
                record = plans.current(session, request.headers.get(PLAN_TOKEN_HEADER))
                if record is None or record.state.plan is None:
                    return _error("This plan has expired; restore it first", 409, "plan_expired")
                look_id = request.headers.get(LOOK_HEADER)
                look = next((look for look in record.state.plan.looks if look.id == look_id), None)
                if look is None:
                    return _error("That look is not in the current plan", 404, "look_unknown")
                prompt = preview_prompt(look, record.state, catalog)
            await asyncio.to_thread(
                pilot.check, session.invite_id, OperationKind.PREVIEW, preview_reserve
            )
        except PhotoInputError as error:
            return _error(str(error), 422, "invalid")
        except Refused as refused:
            return _refusal(refused)

        operation: Operation | None = None
        reservation: asyncio.Future[Operation] | None = None

        async def reserve() -> None:
            nonlocal operation, reservation
            # The reserving thread finishes even when this request is cancelled meanwhile; the
            # shield keeps its result, so the cleanup below settles a call that never started.
            reservation = asyncio.ensure_future(
                asyncio.to_thread(
                    pilot.begin,
                    session.invite_id,
                    OperationKind.PREVIEW,
                    operation_id,
                    preview_reserve,
                )
            )
            operation = await asyncio.shield(reservation)

        started = time.monotonic()
        deadline = started + PREVIEW_DEADLINE_S
        portrait: bytearray | None = None
        full_body: bytearray | None = None
        preview = None
        cost: int | None = None
        outcome = "unknown"
        try:
            async with asyncio.timeout_at(deadline):
                portrait, full_body = await read_preview_uploads(request)
                preview = await wait_for_preview_or_disconnect(
                    request,
                    prompt,
                    portrait,
                    full_body,
                    editor,
                    deadline=deadline,
                    before_call=reserve,
                )
            if preview is not None:
                cost, outcome = preview.attempts * IMAGE_MICRO_USD, "succeeded"
            else:
                outcome = "disconnected"
        except Refused as refused:
            outcome = refused.reason.value
            return _refusal(refused)
        except PhotoTooLargeError as error:
            outcome = "too_large"
            return _error(str(error), 413, "invalid")
        except PhotoInputError as error:
            outcome = "invalid_photo"
            return _error(str(error), 400, "invalid")
        except TimeoutError:
            outcome = "timeout"
            return _error("The try-on took too long", 504, "timeout")
        except ImageRequestError as error:
            outcome = "timeout" if error.timed_out else "provider_error"
            if error.timed_out:
                return _error("The try-on took too long", 504, "timeout")
            return _error("The image provider could not complete this try-on", 502, "provider")
        except PhotoProcessingError:
            outcome = "unusable_result"
            return _error("The image provider returned an unusable image", 502, "provider")
        finally:
            if portrait is not None:
                portrait.clear()
            if full_body is not None:
                full_body.clear()
            if operation is None and reservation is not None:
                try:
                    operation = await reservation
                except Refused:
                    pass  # nothing was reserved
                else:
                    cost, outcome = 0, "cancelled_before_call"
            if operation is not None:
                await asyncio.to_thread(pilot.finish, operation, cost, outcome)
            logger.info(
                "preview outcome=%s attempts=%s latency_s=%.1f",
                outcome,
                preview.attempts if preview is not None else "unknown",
                time.monotonic() - started,
            )

        if preview is None:
            return Response(status_code=499, headers=NO_STORE)
        return PreviewResponse(preview)

    @app.post("/api/feedback", tags=["feedback"])
    def add_feedback(answers: FeedbackAnswers, request: Request) -> JSONResponse:
        """Short product feedback, without photos, plans or profiles."""
        if not beta_open():
            return closed()
        session = session_of(request)
        if session is None:
            return not_invited()
        stored = answers.model_dump(mode="json")
        if "try_on_issues" not in answers.model_fields_set:
            del stored["try_on_issues"]  # a page from before the question: not asked, not skipped
        try:
            feedback_id = pilot.add_feedback(session.invite_id, stored)
        except StoreUnavailable:
            return _error("Feedback could not be saved; try again later", 503, "accounting")
        return JSONResponse({"id": feedback_id}, status_code=201, headers=NO_STORE)

    @app.delete("/api/feedback/{feedback_id}", tags=["feedback"])
    def delete_feedback(feedback_id: str, request: Request) -> Response:
        """Delete one of this invite's feedback entries."""
        if not beta_open():
            return closed()
        session = session_of(request)
        if session is None:
            return not_invited()
        try:
            deleted = pilot.delete_feedback(session.invite_id, uuid.UUID(feedback_id).hex)
        except ValueError:
            deleted = False
        except StoreUnavailable:
            return _error("Feedback could not be deleted; try again later", 503, "accounting")
        if not deleted:
            return _error("No such feedback for this invite", 404, "not_found")
        return Response(status_code=204, headers=NO_STORE)

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
