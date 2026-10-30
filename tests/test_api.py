"""The closed beta over HTTP: invites, plan tokens, restore, try-on, quotas and feedback.

Model replies and preview images are scripted, so no test calls a provider."""

import asyncio
import json
import logging
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.meta_images import ImageResult, InputImage
from backend.app.pilot import invite_hash
from backend.app.planner import SparkClient
from backend.app.store import Document, MemoryStore, StoreUnavailable
from tests.test_planner import FIXTURE, LOOK_1, LOOK_2, LOOK_3, VALID, completion, reply

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


def test_infeasible_requests_cost_no_quota(http: TestClient, spark: ScriptedSpark) -> None:
    join(http)
    impossible = plan(http, state=STATE | {"footwear": "open"}).json()
    assert impossible["outcome"] == "infeasible" and spark.sent == []
    assert http.get("/api/session").json()["plan_requests_used"] == 0


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
    assert [document.data["answers"] for document in stored if document] == [answers]
    assert all("quiet smart casual" not in name for name in store.names())
    assert http.delete(f"/api/feedback/{feedback_id}").status_code == 204
    assert http.delete(f"/api/feedback/{feedback_id}").status_code == 404


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
    beta = Settings(
        **base,
        session_secret=SECRET,
        store_url=f"file://{tmp_path}/s",
        invite_hashes=frozenset({invite_hash(CODE)}),
        planning_budget_usd=1,
    )
    with TestClient(create_app(beta), base_url="https://testserver") as http:
        joined = http.post("/api/session/invite", json={"code": CODE})
        assert "secure" in joined.headers["set-cookie"].lower()
        assert joined.json()["beta"] is True


def test_the_image_budget_setting_closes_try_ons(tmp_path: Path, store: MemoryStore) -> None:
    limited = settings(tmp_path, image_budget_usd=0.02)
    app = create_app(limited, image_editor=FakeEditor(), store=store)
    with TestClient(app) as http:
        join(http, photos=True)
        session = http.get("/api/session").json()
    assert session["previews_open"] is False  # one try-on reserves $0.03
