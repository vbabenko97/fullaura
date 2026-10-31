"""The closed beta over HTTP: invites, plan tokens, restore, try-on, quotas and feedback.

Model replies and preview images are scripted, so no test calls a provider."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.catalog import MATERIALS
from backend.app.config import Settings
from backend.app.main import MAX_BODY_BYTES, create_app
from backend.app.meta_images import ImageRequestError, ImageResult, InputImage
from backend.app.pilot import invite_hash, planning_reserve_micro_usd
from backend.app.planner import MAX_TOKENS, SparkClient
from backend.app.state import MAX_LOCKS, Slot
from backend.app.store import Document, MemoryStore, StoreUnavailable
from tests.test_pilot import InviteWritesFail
from tests.test_planner import CATALOG, FIXTURE, LOOK_1, LOOK_2, LOOK_3, VALID, completion, reply

CODE = "beta-invite-code-1"
OTHER_CODE = "beta-invite-code-2"
SECRET = "s" * 40
PREVIEW = b"\x89PNG\r\n\x1a\npreview"
BRIEF = {"style_goal": "quiet smart casual for dinners", "preferred_colors": ["olive"]}
STATE = {"budget": {"amount_cents": 20000}, "material_exclusions": ["wool"]}
CATALOG_ID = json.loads(FIXTURE.read_text(encoding="utf-8"))["id"]


def jpeg() -> bytes:
    return b"\xff\xd8\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00pixels\xff\xd9"


class ScriptedSpark(SparkClient):
    """Replies in turn; records every request body."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.sent: list[dict[str, Any]] = []

        def post(request: Any, timeout_s: float) -> dict[str, Any]:
            self.sent.append(json.loads(request.data))
            return completion(self.replies.pop(0))

        super().__init__("key", post=post, sleep=lambda seconds: None)


class FakeEditor:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.images: list[InputImage] = []

    async def edit_async(
        self, prompt: str, images: Sequence[InputImage], *, deadline: float
    ) -> ImageResult:
        self.prompts.append(prompt)
        self.images = list(images)
        return ImageResult(PREVIEW, "image/png", 1, 0.1, {}, None)


def settings(tmp_path: Path, **fields: Any) -> Settings:
    return Settings(
        web_dist=tmp_path,
        catalog_file=FIXTURE,
        session_secret=SECRET,
        invite_hashes=frozenset({invite_hash(CODE), invite_hash(OTHER_CODE)}),
        **fields,
    )


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def spark() -> ScriptedSpark:
    return ScriptedSpark(*[VALID] * 12)


@pytest.fixture
def editor() -> FakeEditor:
    return FakeEditor()


@pytest.fixture
def http(
    tmp_path: Path, store: MemoryStore, spark: ScriptedSpark, editor: FakeEditor
) -> Iterator[TestClient]:
    app = create_app(settings(tmp_path), spark=spark, image_editor=editor, store=store)
    with TestClient(app) as client:
        yield client


def join(http: TestClient, code: str = CODE, *, photos: bool = False) -> None:
    assert http.post("/api/session/invite", json={"code": code}).status_code == 200
    assert http.post("/api/session/confirm-age", json={"over_18": True}).status_code == 200
    if photos:
        assert http.post("/api/session/photo-consent", json={"consent": True}).status_code == 200


def operation() -> dict[str, str]:
    return {"X-Operation-Id": str(uuid.uuid4())}


def plan(http: TestClient, headers: dict[str, str] | None = None, **fields: Any) -> Any:
    body = {"brief": BRIEF, "state": STATE} | fields
    return http.post("/api/plans", json=body, headers=headers or operation())


def operation_record(store: MemoryStore, name: str) -> dict[str, Any] | None:
    document = store.read(name) if name.startswith("operations/") else None
    return document.data if document else None


def try_on(http: TestClient, token: str, look_id: str = "look_1", **headers: str) -> Any:
    return http.post(
        "/api/previews",
        files={
            "portrait": ("portrait.jpg", jpeg(), "image/jpeg"),
            "full_body": ("full-body.jpg", jpeg(), "image/jpeg"),
        },
        headers={"X-Plan-Token": token, "X-Look-Id": look_id, **operation(), **headers},
    )


def test_an_invite_opens_a_signed_session(http: TestClient) -> None:
    assert http.get("/api/session").json()["invited"] is False
    assert http.get("/api/catalog").status_code == 401
    assert plan(http).status_code == 401
    refused = http.post("/api/session/invite", json={"code": "not-an-invite"})
    assert refused.status_code == 403 and refused.json()["reason"] == "invite_invalid"

    joined = http.post("/api/session/invite", json={"code": CODE})
    cookie = joined.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    session = joined.json()
    assert session["invited"] and session["accounting"] and not session["age_confirmed"]
    assert (session["plan_requests_limit"], session["preview_requests_limit"]) == (8, 12)
    assert http.get("/api/catalog").status_code == 200

    tampered = http.cookies["fullaura_session"] + "x"
    http.cookies.clear()
    http.cookies.set("fullaura_session", tampered)
    assert http.get("/api/session").json()["invited"] is False  # a changed cookie is not a session


def test_planning_needs_age_confirmation_and_an_operation_id(http: TestClient) -> None:
    http.post("/api/session/invite", json={"code": CODE})
    assert plan(http).json()["reason"] == "age_required"
    assert http.post("/api/session/confirm-age", json={"over_18": False}).status_code == 403
    assert http.post("/api/session/confirm-age", json={"over_18": True}).status_code == 200
    missing = http.post("/api/plans", json={"brief": BRIEF, "state": STATE})
    assert missing.status_code == 400 and missing.json()["reason"] == "operation_id"
    assert plan(http).json()["outcome"] == "success"


def test_a_plan_comes_with_a_token_that_updates_and_replacements_name(
    http: TestClient, spark: ScriptedSpark
) -> None:
    join(http)
    first = plan(http).json()
    assert first["outcome"] == "success" and first["plan_token"] and first["plan_version"] == 1
    assert first["catalog_id"] == CATALOG_ID

    locked = {"look_id": "look_1", "slot": "top", "item": {"kind": "catalog", "id": "P01"}}
    without_token = plan(http, state=STATE | {"locks": [locked]})
    assert without_token.status_code == 422  # locks refer to a plan the server must know

    spark.replies[0] = reply({**LOOK_1}, {**LOOK_2, "top": "P16"}, LOOK_3)
    replacement = {"look_id": "look_2", "slot": "top", "item": {"kind": "catalog", "id": "P03"}}
    replaced = plan(
        http,
        state=STATE | {"material_exclusions": []},
        replacement=replacement,
        plan_token=first["plan_token"],
    ).json()
    assert replaced["outcome"] == "success" and replaced["plan_version"] == 2
    assert replaced["plan"]["looks"][1]["items"]["top"]["id"] == "P16"
    # The old token stopped working once the plan changed.
    stale = plan(http, plan_token=first["plan_token"])
    assert stale.status_code == 409 and stale.json()["reason"] == "plan_expired"


def test_the_browser_cannot_send_its_own_plan(http: TestClient) -> None:
    join(http)
    current = plan(http).json()
    forged = plan(http, state=STATE | {"plan": current["plan"]})
    assert forged.status_code == 422


def test_a_failed_update_keeps_the_previous_plan_current(
    http: TestClient, spark: ScriptedSpark
) -> None:
    join(http)
    first = plan(http).json()
    with_wool = reply({**LOOK_1, "top": "P02"}, LOOK_2, LOOK_3)
    spark.replies[:2] = [with_wool, with_wool]
    failed = plan(http, plan_token=first["plan_token"]).json()
    assert failed["outcome"] == "generation_failed" and failed["plan"] is None
    assert "plan_token" not in failed
    assert plan(http, plan_token=first["plan_token"]).json()["outcome"] == "success"


def test_infeasible_requests_cost_no_quota(
    http: TestClient, spark: ScriptedSpark, store: MemoryStore
) -> None:
    join(http)
    repeated = operation()
    impossible = plan(http, repeated, state=STATE | {"footwear": "open"}).json()
    assert plan(http, repeated, state=STATE | {"footwear": "open"}).json() == impossible
    assert impossible["outcome"] == "infeasible" and spark.sent == []
    assert http.get("/api/session").json()["plan_requests_used"] == 0
    # Counted once for the pilot report, by rule, without the request itself
    rules = sorted({conflict["rule"] for conflict in impossible["conflicts"]})
    invites = [store.read(name) for name in store.names() if name.startswith("invites/")]
    assert [
        (d.data["infeasible_requests"], sorted(d.data["infeasible_rules"])) for d in invites if d
    ] == [(1, rules)]


def test_an_infeasible_answer_does_not_depend_on_the_store(
    tmp_path: Path, spark: ScriptedSpark
) -> None:
    store = UnavailableStore()
    app = create_app(settings(tmp_path), spark=spark, store=store)
    with TestClient(app) as http:
        join(http)
        store.down = True
        response = plan(http, state=STATE | {"footwear": "open"})
    assert response.status_code == 200 and response.json()["outcome"] == "infeasible"


def test_a_repeated_request_is_answered_without_a_second_model_call(
    http: TestClient, spark: ScriptedSpark
) -> None:
    join(http)
    headers = operation()
    first = plan(http, headers=headers).json()
    again = plan(http, headers=headers).json()
    assert again["plan"] == first["plan"] and again["plan_token"] == first["plan_token"]
    assert len(spark.sent) == 1
    assert http.get("/api/session").json()["plan_requests_used"] == 1


def test_a_repeated_request_that_did_not_produce_the_current_plan_is_refused(
    http: TestClient, spark: ScriptedSpark
) -> None:
    join(http)
    headers = operation()
    spark.replies[:2] = [reply(LOOK_1, LOOK_2), reply(LOOK_1, LOOK_2)]
    assert plan(http, headers=headers).json()["outcome"] == "generation_failed"
    repeated = plan(http, headers=headers)
    assert repeated.status_code == 409 and repeated.json()["reason"] == "duplicate"
    assert len(spark.sent) == 2  # the first request and its repair, nothing more


def test_each_invite_has_its_own_planning_quota(tmp_path: Path, store: MemoryStore) -> None:
    spark = ScriptedSpark(*[VALID] * 4)
    app = create_app(settings(tmp_path, plan_requests_per_invite=2), spark=spark, store=store)
    with TestClient(app) as first, TestClient(app) as second:
        join(first)
        join(second, OTHER_CODE)
        assert [plan(first).status_code for _ in range(2)] == [200, 200]
        exhausted = plan(first)
        assert exhausted.status_code == 429 and exhausted.json()["reason"] == "quota"
        assert plan(second).status_code == 200
        assert first.get("/api/session").json()["planning_open"] is False


def test_the_shared_budget_closes_planning(tmp_path: Path, store: MemoryStore) -> None:
    app = create_app(
        settings(tmp_path, planning_budget_usd=0.01), spark=ScriptedSpark(VALID), store=store
    )
    with TestClient(app) as http:
        join(http)
        refused = plan(http)
        assert refused.status_code == 503 and refused.json()["reason"] == "budget"
        assert http.get("/api/session").json()["planning_open"] is False
        assert http.get("/api/catalog").status_code == 200


class UnavailableStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def read(self, name: str) -> Document | None:
        if self.down and not name.startswith("invites/"):
            raise StoreUnavailable("down")
        return super().read(name)


def test_unavailable_accounting_pauses_model_calls_but_keeps_plans(tmp_path: Path) -> None:
    store = UnavailableStore()
    app = create_app(
        settings(tmp_path), spark=ScriptedSpark(VALID), image_editor=FakeEditor(), store=store
    )
    with TestClient(app) as http:
        join(http, photos=True)
        current = plan(http).json()
        store.down = True
        refused = plan(http, plan_token=current["plan_token"])
        assert refused.status_code == 503 and refused.json()["reason"] == "accounting_unavailable"
        assert try_on(http, current["plan_token"]).json()["reason"] == "accounting_unavailable"
        session = http.get("/api/session").json()
        assert session["accounting"] is False and session["planning_open"] is False
        saved = {"brief": BRIEF, "state": STATE | {"plan": current["plan"]}, "catalog_id": "x"}
        assert http.post("/api/plans/restore", json=saved).json()["outcome"] == "success"


def test_after_a_restart_a_saved_plan_is_restored_without_a_model_call(
    tmp_path: Path, store: MemoryStore
) -> None:
    spark = ScriptedSpark(VALID)
    with TestClient(create_app(settings(tmp_path), spark=spark, store=store)) as http:
        join(http)
        current = plan(http).json()
        cookies = dict(http.cookies)

    restarted = create_app(settings(tmp_path), spark=ScriptedSpark(), store=store)
    with TestClient(restarted, cookies=cookies) as http:
        session = http.get("/api/session").json()
        assert session["invited"] and session["age_confirmed"] and not session["has_plan"]
        assert session["plan_requests_used"] == 1
        expired = plan(http, plan_token=current["plan_token"])
        assert expired.json()["reason"] == "plan_expired"

        saved_plan = json.loads(json.dumps(current["plan"]))
        saved_plan["selected_offers"] = {key: "offer-gone" for key in saved_plan["selected_offers"]}
        saved = {"brief": BRIEF, "state": STATE | {"plan": saved_plan}, "catalog_id": "old"}
        restored = http.post("/api/plans/restore", json=saved).json()
        assert restored["outcome"] == "success" and restored["catalog_changed"] is True
        assert restored["plan"]["selected_offers"] == current["plan"]["selected_offers"]
        assert http.get("/api/session").json()["has_plan"] is True

        over_budget = STATE | {"plan": current["plan"], "budget": {"amount_cents": 1000}}
        failed = http.post(
            "/api/plans/restore", json={"brief": BRIEF, "state": over_budget, "catalog_id": "x"}
        ).json()
        assert failed["outcome"] == "restore_failed" and failed["plan"] is None
        assert any(violation["rule"] == "budget" for violation in failed["violations"])


def test_the_largest_plan_the_app_saves_is_restored(http: TestClient) -> None:
    """Every field the browser sends at its limit, each character of the text escaped to six
    bytes, owned items with IDs of the longest length allowed, and a lock on every position of
    looks with six positions each."""
    join(http)
    owned = [
        {
            "id": f"owned-{n}" + "x" * 93,
            "category": category,
            "color": "ü" * 40,
            "description": "ü" * 200,
        }
        for n, category in enumerate(("knitwear", "outerwear", "accessory"), start=1)
    ]
    layers = {
        slot: {"kind": "owned", "id": item["id"]}
        for slot, item in zip(("mid_layer", "outer_layer", "accessory"), owned, strict=True)
    }
    looks = [
        {
            "id": f"look_{n}",
            "items": {slot: {"kind": "catalog", "id": i} for slot, i in look.items()} | layers,
        }
        for n, look in enumerate((LOOK_1, LOOK_2, LOOK_3), start=1)
    ]
    variants = {i["id"] for look in looks for i in look["items"].values() if i["kind"] == "catalog"}
    offers = {
        variant.id: variant.offers[0].id
        for product in CATALOG.products
        for variant in product.variants
        if variant.id in variants
    }
    used = {"cotton", "elastane", "polyester", "synthetic rubber", "viscose"}  # by these items
    saved = {
        "brief": {
            "style_goal": "ü" * 300,
            "preferred_colors": ["ü" * 30] * 5,
            "occasion": "special_event",
            "season": "autumn",
        },
        "state": {
            "budget": {"amount_cents": 20000, "mode": "per_look"},
            "material_exclusions": sorted(set(MATERIALS) - used),
            "fit": {"lines": ["women", "men", "unisex"]},
            "footwear": "any",
            "owned_items": owned,
            "locks": [
                {"look_id": look["id"], "slot": slot, "item": item}
                for look in looks
                for slot, item in look["items"].items()
            ],
            "plan": {"looks": looks, "selected_offers": offers},
        },
        "catalog_id": CATALOG_ID,
    }
    content = json.dumps(saved).encode()  # "ü" is sent as ü
    response = http.post(
        "/api/plans/restore", content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200 and response.json()["outcome"] == "success"
    assert len(response.json()["plan"]["looks"][0]["items"]) == 6


def unread_body() -> Iterator[bytes]:
    raise AssertionError("the body was read")
    yield b""


def test_an_oversized_body_is_refused_before_any_route_reads_it(
    http: TestClient, spark: ScriptedSpark
) -> None:
    def send(path: str) -> Any:
        return http.post(
            path,
            content=unread_body(),
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(MAX_BODY_BYTES + 1),
                **operation(),
            },
        )

    for path in ("/api/session/invite", "/api/plans", "/api/feedback"):  # without an invite
        refused = send(path)
        assert (refused.status_code, refused.json()["reason"]) == (413, "invalid")
        assert refused.headers["cache-control"] == "no-store"
    join(http)
    assert send("/api/plans").status_code == 413
    assert spark.sent == [] and http.get("/api/session").json()["plan_requests_used"] == 0


def test_a_body_without_a_length_is_cut_off_at_the_limit(tmp_path: Path, http: TestClient) -> None:
    def chunked() -> Iterator[bytes]:  # sent without Content-Length
        yield b'{"brief": '
        yield b" " * MAX_BODY_BYTES
        yield b"}"

    join(http)
    refused = http.post(
        "/api/plans",
        content=chunked(),
        headers={"Content-Type": "application/json", **operation()},
    )
    assert (refused.status_code, refused.json()["reason"]) == (413, "invalid")

    # A body that never ends is read no further than the limit.
    reads = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b" " * 4096, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/plans",
        "raw_path": b"/api/plans",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(create_app(settings(tmp_path))(scope, receive, send))
    assert sent[0]["status"] == 413
    assert reads == MAX_BODY_BYTES // 4096 + 1


def test_overlong_ids_and_too_many_locks_are_refused_before_a_model_call(
    http: TestClient, spark: ScriptedSpark
) -> None:
    join(http)
    token = plan(http).json()["plan_token"]
    calls = len(spark.sent)
    owned = {"id": "o" * 101, "category": "top", "color": "navy", "description": "navy shirt"}
    long_id = plan(http, state=STATE | {"owned_items": [owned]})
    positions = [(f"look_{n}", slot) for n in range(1, 5) for slot in Slot][: MAX_LOCKS + 1]
    locks = [
        {"look_id": look_id, "slot": slot, "item": {"kind": "catalog", "id": "P01"}}
        for look_id, slot in positions
    ]
    too_many = plan(http, state=STATE | {"locks": locks}, plan_token=token)
    for response, error in ((long_id, "string_too_long"), (too_many, "too_long")):
        assert (response.status_code, response.json()["reason"]) == (422, "invalid")
        assert [detail["type"] for detail in response.json()["detail"]] == [error]
    assert len(spark.sent) == calls
    assert http.get("/api/session").json()["plan_requests_used"] == 1


def test_a_try_on_is_written_from_the_checked_look(
    http: TestClient, editor: FakeEditor, store: MemoryStore
) -> None:
    join(http, photos=True)
    current = plan(http).json()
    response = try_on(http, current["plan_token"], "look_2")
    assert response.status_code == 200 and response.content == PREVIEW
    assert response.headers["cache-control"] == "no-store"
    prompt = editor.prompts[0]
    assert "navy overshirt" in prompt and "olive relaxed chinos" in prompt
    assert "closed shoes" in prompt and len(editor.images) == 2
    session = http.get("/api/session").json()
    assert session["preview_requests_used"] == 1
    ledger = store.read("ledger.json")
    assert ledger is not None and ledger.data["spent"]["images"] == 10_000


def test_a_try_on_is_refused_before_its_body_is_read(http: TestClient) -> None:
    join(http)
    token = plan(http).json()["plan_token"]

    def body() -> Iterator[bytes]:
        raise AssertionError("the body was read")
        yield b""

    def send(look_id: str = "look_1", plan_token: str = token) -> Any:
        return http.post(
            "/api/previews",
            content=body(),
            headers={
                "Content-Type": "multipart/form-data; boundary=x",
                "X-Plan-Token": plan_token,
                "X-Look-Id": look_id,
                **operation(),
            },
        )

    assert send().json()["reason"] == "photo_consent_required"
    http.post("/api/session/photo-consent", json={"consent": True})
    assert send(plan_token="stale").json()["reason"] == "plan_expired"
    assert send(look_id="look_9").json()["reason"] == "look_unknown"
    assert http.get("/api/session").json()["preview_requests_used"] == 0


def test_an_updated_plan_retires_the_token_a_try_on_used(http: TestClient) -> None:
    join(http, photos=True)
    first = plan(http).json()
    plan(http, plan_token=first["plan_token"])
    assert try_on(http, first["plan_token"]).json()["reason"] == "plan_expired"


def test_a_try_on_that_times_out_keeps_its_reservation(
    tmp_path: Path, store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    class WaitingEditor:
        cancelled = False

        async def edit_async(
            self, prompt: str, images: Sequence[InputImage], *, deadline: float
        ) -> ImageResult:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError

    waiting = WaitingEditor()
    monkeypatch.setattr("backend.app.main.PREVIEW_DEADLINE_S", 0.05)
    app = create_app(
        settings(tmp_path), spark=ScriptedSpark(VALID), image_editor=waiting, store=store
    )
    with TestClient(app) as http:
        join(http, photos=True)
        response = try_on(http, plan(http).json()["plan_token"])
    assert response.status_code == 504 and waiting.cancelled
    ledger = store.read("ledger.json")
    assert ledger is not None and ledger.data["spent"]["images"] == 30_000
    assert ledger.data["running"] == {}
    previews = [operation_record(store, name) for name in store.names()]
    assert [(r["status"], r["outcome"]) for r in previews if r and r["kind"] == "preview"] == [
        ("unknown", "timeout")
    ]


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (ImageRequestError("refused", attempts=2), "provider_error"),
        (RuntimeError("an unexpected failure"), "unknown"),
    ],
)
def test_a_failed_try_on_records_what_happened_and_keeps_its_reservation(
    tmp_path: Path, store: MemoryStore, failure: Exception, outcome: str
) -> None:
    class FailingEditor:
        async def edit_async(
            self, prompt: str, images: Sequence[InputImage], *, deadline: float
        ) -> ImageResult:
            raise failure

    app = create_app(
        settings(tmp_path), spark=ScriptedSpark(VALID), image_editor=FailingEditor(), store=store
    )
    with TestClient(app, raise_server_exceptions=False) as http:
        join(http, photos=True)
        response = try_on(http, plan(http).json()["plan_token"])
    assert response.status_code != 200
    previews = [operation_record(store, name) for name in store.names()]
    assert [
        (r["status"], r["outcome"], r["cost"]) for r in previews if r and r["kind"] == "preview"
    ] == [("unknown", outcome, None)]
    ledger = store.read("ledger.json")
    assert ledger is not None and ledger.data["spent"]["images"] == 30_000


def test_try_on_quota_is_per_invite(tmp_path: Path, store: MemoryStore) -> None:
    app = create_app(
        settings(tmp_path, preview_requests_per_invite=1),
        spark=ScriptedSpark(VALID),
        image_editor=FakeEditor(),
        store=store,
    )
    with TestClient(app) as http:
        join(http, photos=True)
        token = plan(http).json()["plan_token"]
        assert try_on(http, token).status_code == 200
        assert try_on(http, token).json()["reason"] == "quota"


def test_feedback_is_stored_without_the_plan_and_can_be_deleted(
    http: TestClient, store: MemoryStore
) -> None:
    join(http)
    plan(http)
    answers = {
        "would_wear": True,
        "usefulness": 4,
        "try_on": "cannot_assess",
        "catalog_problem": "No closed shoes in my size",
        "comment": "",
    }
    assert http.post("/api/feedback", json=answers | {"usefulness": 9}).status_code == 422
    created = http.post("/api/feedback", json=answers)
    assert created.status_code == 201
    feedback_id = created.json()["id"]
    assert http.get("/api/session").json()["feedback_ids"] == [feedback_id]
    stored = [store.read(name) for name in store.names() if name.startswith("feedback/")]
    # A page from before the try-on question sends no such field, so none is stored
    assert [document.data["answers"] for document in stored if document] == [answers]
    assert all("quiet smart casual" not in name for name in store.names())
    assert http.delete(f"/api/feedback/{feedback_id}").status_code == 204
    assert http.delete(f"/api/feedback/{feedback_id}").status_code == 404


def test_feedback_that_its_invite_cannot_list_answers_503_and_is_not_kept(tmp_path: Path) -> None:
    store = InviteWritesFail()
    with TestClient(create_app(settings(tmp_path), store=store)) as http:
        join(http)
        store.failing = True
        answers = {"would_wear": True, "usefulness": 3, "try_on": 2}
        response = http.post("/api/feedback", json=answers)
    assert (response.status_code, response.json()["reason"]) == (503, "accounting")
    assert [name for name in store.names() if name.startswith("feedback/")] == []


@pytest.mark.parametrize(
    ("try_on", "issues", "status"),
    [
        (2, ["likeness", "background"], 201),
        (5, ["none_noticed"], 201),
        (4, None, 201),  # skipped: kept as null
        ("cannot_assess", None, 201),
        (3, ["none_noticed", "other"], 422),  # no problems and a problem
        (3, ["other", "other"], 422),
        (3, [], 422),
        (3, ["the face"], 422),  # only the listed categories
        ("cannot_assess", ["none_noticed"], 422),  # no score, so nothing to explain
    ],
)
def test_try_on_issues_must_agree_with_the_try_on_answer(
    http: TestClient, store: MemoryStore, try_on: int | str, issues: list[str] | None, status: int
) -> None:
    join(http)
    answers = {"would_wear": False, "usefulness": 2, "try_on": try_on, "try_on_issues": issues}
    assert http.post("/api/feedback", json=answers).status_code == status
    saved = [store.read(name) for name in store.names() if name.startswith("feedback/")]
    assert [d.data["answers"]["try_on_issues"] for d in saved if d] == (
        [issues] if status == 201 else []
    )


def test_logs_hold_outcomes_and_usage_but_nothing_a_person_entered(
    http: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="backend.app"):
        join(http, photos=True)
        try_on(http, plan(http).json()["plan_token"])
    assert "plan outcome=success" in caplog.text and "preview outcome=succeeded" in caplog.text
    for entered in ("quiet smart casual", "olive", CODE):
        assert entered not in caplog.text


def test_production_keeps_the_beta_closed_unless_enabled(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>FullAura</html>", encoding="utf-8")
    closed = Settings(environment="production", web_dist=tmp_path, baseten_api_key="key")
    with TestClient(create_app(closed)) as http:
        assert http.get("/api/session").json()["beta"] is False
        assert http.post("/api/session/invite", json={"code": CODE}).status_code == 404
        assert http.get("/api/catalog").status_code == 404
        assert plan(http).status_code == 404
        assert http.post("/api/previews").status_code == 404


def test_the_production_beta_needs_a_secret_a_store_and_invites(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>FullAura</html>", encoding="utf-8")
    base = {"environment": "production", "beta_enabled": True, "web_dist": tmp_path}
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app(Settings(**base))
    with pytest.raises(RuntimeError, match="STORE_URL"):
        create_app(Settings(**base, session_secret=SECRET))
    with pytest.raises(RuntimeError, match="INVITE_HASHES"):
        create_app(Settings(**base, session_secret=SECRET, store_url=f"file://{tmp_path}/s"))
    complete = {
        **base,
        "session_secret": SECRET,
        "store_url": f"file://{tmp_path}/s",
        "invite_hashes": frozenset({invite_hash(CODE)}),
    }
    with pytest.raises(RuntimeError, match="PILOT_ENDS_AT"):
        create_app(Settings(**complete))
    ends_at = datetime.now(UTC) + timedelta(days=7)
    with pytest.raises(RuntimeError, match="RECORDS_PURGE_AT"):
        create_app(Settings(**complete, pilot_ends_at=ends_at))
    with pytest.raises(ValueError, match="after FULLAURA_PILOT_ENDS_AT"):
        Settings(**complete, pilot_ends_at=ends_at, records_purge_at=ends_at)
    beta = Settings(**complete, pilot_ends_at=ends_at, records_purge_at=ends_at + timedelta(days=2))
    with TestClient(create_app(beta), base_url="https://testserver") as http:
        joined = http.post("/api/session/invite", json={"code": CODE})
        assert "secure" in joined.headers["set-cookie"].lower()
        assert joined.json()["beta"] is True


def test_deleted_records_reopen_no_budget_and_the_beta_closes_when_they_go(
    tmp_path: Path, store: MemoryStore
) -> None:
    now = datetime.now(UTC)
    purge_at = now + timedelta(days=1)
    ended = settings(tmp_path, pilot_ends_at=now - timedelta(days=1), records_purge_at=purge_at)
    feedback = {"would_wear": True, "usefulness": 3, "try_on": 2}
    with TestClient(create_app(ended, spark=ScriptedSpark(VALID), store=store)) as http:
        join(http)
        assert http.post("/api/feedback", json=feedback).status_code == 201
        for name in store.names():  # what the purge job does
            store.delete(name)
        session = http.get("/api/session").json()
        assert session["pilot_ended"] and not session["planning_open"]
        assert session["records_purge_at"] == purge_at.isoformat()
        refused = plan(http)
        assert (refused.status_code, refused.json()["reason"]) == (403, "pilot_ended")
        assert store.names() == []

    purged = settings(
        tmp_path, pilot_ends_at=now - timedelta(days=2), records_purge_at=now - timedelta(hours=1)
    )
    with TestClient(create_app(purged, store=store)) as http:
        assert http.get("/api/session").json()["beta"] is False
        assert http.post("/api/session/invite", json={"code": CODE}).status_code == 404
        assert http.post("/api/session/confirm-age", json={"over_18": True}).status_code == 404
        assert http.post("/api/feedback", json=feedback).status_code == 404
    assert store.names() == []  # nothing new was written after the deletion


def test_once_the_deletion_has_begun_no_route_writes_a_record(
    tmp_path: Path, store: MemoryStore
) -> None:
    """A host whose clock has not reached the purge time still sees the purge job's marker."""
    now = datetime.now(UTC)
    later = settings(
        tmp_path, pilot_ends_at=now + timedelta(days=1), records_purge_at=now + timedelta(days=2)
    )
    with TestClient(create_app(later, spark=ScriptedSpark(VALID), store=store)) as http:
        join(http)
        feedback = http.post(
            "/api/feedback", json={"would_wear": True, "usefulness": 3, "try_on": 2}
        )
        feedback_id = feedback.json()["id"]
        for name in store.names():  # the purge job: marker first, then the deletion
            store.delete(name)
        store.create("purge-marker.json", {"closing_since": now.isoformat()})
        for response in (
            http.post("/api/session/confirm-age", json={"over_18": True}),
            http.post("/api/session/photo-consent", json={"consent": True}),
            http.post("/api/feedback", json={"would_wear": False, "usefulness": 1, "try_on": 1}),
            http.delete(f"/api/feedback/{feedback_id}"),
            plan(http),
        ):
            assert response.status_code in {403, 404, 503}, response.text  # refused
    assert store.names() == ["purge-marker.json"]


def test_the_image_budget_setting_closes_try_ons(tmp_path: Path, store: MemoryStore) -> None:
    limited = settings(tmp_path, image_budget_usd=0.02)
    app = create_app(limited, image_editor=FakeEditor(), store=store)
    with TestClient(app) as http:
        join(http, photos=True)
        session = http.get("/api/session").json()
    assert session["previews_open"] is False  # one try-on reserves $0.03


class SlowLedgerStore(MemoryStore):
    """Writes the ledger slowly, so a request's deadline can pass while it reserves."""

    def __init__(self) -> None:
        super().__init__()
        self.slow = False

    def create(self, name: str, data: dict[str, Any]) -> str:
        if self.slow and name.startswith("operations/"):
            time.sleep(0.3)
        return super().create(name, data)


def test_a_try_on_cut_off_while_reserving_costs_nothing_and_blocks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SlowLedgerStore()
    editor = FakeEditor()
    spark = ScriptedSpark(VALID)
    app = create_app(settings(tmp_path), spark=spark, image_editor=editor, store=store)
    with TestClient(app) as http:
        join(http, photos=True)
        token = plan(http).json()["plan_token"]
        store.slow = True
        monkeypatch.setattr("backend.app.main.PREVIEW_DEADLINE_S", 0.1)
        assert try_on(http, token).status_code == 504
        ledger = store.read("ledger.json")
        assert ledger is not None and ledger.data["running"] == {}
        assert ledger.data["spent"]["images"] == 0 and editor.prompts == []
        store.slow = False
        monkeypatch.setattr("backend.app.main.PREVIEW_DEADLINE_S", 120.0)
        assert try_on(http, token).status_code == 200


def test_a_planning_call_that_reports_no_usage_counts_at_its_maximum(
    tmp_path: Path, store: MemoryStore
) -> None:
    def post(request: Any, timeout_s: float) -> dict[str, Any]:
        return {"choices": [{"message": {"content": VALID}, "finish_reason": "stop"}]}

    spark = SparkClient("key", post=post, sleep=lambda seconds: None)
    with TestClient(create_app(settings(tmp_path), spark=spark, store=store)) as http:
        join(http)
        assert plan(http).json()["outcome"] == "success"
    ledger = store.read("ledger.json")
    assert ledger is not None
    assert ledger.data["spent"]["planning"] == planning_reserve_micro_usd(1, MAX_TOKENS)
