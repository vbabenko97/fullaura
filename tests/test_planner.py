"""Planning with Muse Spark, without network calls: the model's replies are scripted."""

import asyncio
import io
import json
import logging
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from backend.app.catalog import Catalog
from backend.app.config import Settings
from backend.app.constraints import Rule
from backend.app.main import create_app
from backend.app.planner import (
    Brief,
    Outcome,
    PlanRequest,
    Replacement,
    SparkClient,
    _post_json,
    plan_looks,
)
from backend.app.state import Budget, ItemKind, ItemRef, LockedPlacement, Look, Plan, Slot, State

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_catalog.json"
CATALOG = Catalog.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
BRIEF = Brief(style_goal="quiet smart casual for dinners", preferred_colors=("ivory", "olive"))


def reply(*looks: dict[str, str]) -> str:
    items = [{"id": f"look_{n}", "items": look} for n, look in enumerate(looks, start=1)]
    return json.dumps({"looks": items})


LOOK_1 = {"top": "P01", "bottom": "P05", "footwear": "P18"}  # EUR 180, all wool-free
LOOK_2 = {"top": "P03", "bottom": "P05", "footwear": "P18"}  # EUR 195
LOOK_3 = {"top": "P01", "bottom": "P09", "footwear": "P18"}  # EUR 185
VALID = f"Here are the looks:\n```json\n{reply(LOOK_1, LOOK_2, LOOK_3)}\n```"
WITH_WOOL = reply({**LOOK_1, "top": "P02"}, LOOK_2, LOOK_3)  # P02 is 100% wool


def completion(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 300},
    }


def http_error(code: int) -> HTTPError:
    return HTTPError("https://inference.baseten.co/v1", code, "error", {}, io.BytesIO(b"{}"))


def spark(*outcomes: Any) -> tuple[SparkClient, list[dict[str, Any]], list[float]]:
    """A client whose transport returns or raises each outcome in turn."""
    sent: list[dict[str, Any]] = []
    slept: list[float] = []
    queue = list(outcomes)

    def post(request: Request, timeout_s: float) -> dict[str, Any]:
        sent.append(json.loads(request.data))
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return completion(outcome)

    return SparkClient("secret-key", post=post, sleep=slept.append), sent, slept


def state(budget_cents: int = 20000, **fields: Any) -> State:
    exclusions = fields.pop("material_exclusions", {"wool"})
    return State(
        budget=Budget(amount_cents=budget_cents),
        material_exclusions=exclusions,
        **fields,
    )


def plan_from_looks(*look_items: dict[str, str]) -> Plan:
    looks = tuple(
        Look(
            id=f"look_{number}",
            items={
                Slot(slot): ItemRef(kind=ItemKind.CATALOG, id=variant_id)
                for slot, variant_id in items.items()
            },
        )
        for number, items in enumerate(look_items, start=1)
    )
    variants = {item.id for look in looks for item in look.items.values()}
    return Plan(
        looks=looks,
        selected_offers={variant_id: f"offer_{variant_id}_AT" for variant_id in variants},
    )


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def test_a_valid_plan_succeeds_with_offers_chosen_by_code() -> None:
    client, sent, _ = spark(VALID)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert result.outcome is Outcome.SUCCESS and result.check.passed
    assert result.plan.selected_offers == {
        variant_id: f"offer_{variant_id}_AT" for variant_id in ("P01", "P03", "P05", "P09", "P18")
    }
    assert (result.usage.calls, result.usage.prompt_tokens) == (1, 1000)
    request = json.loads(sent[0]["messages"][1]["content"])
    candidates = {candidate["id"] for candidate in request["candidates"]}
    # Wool, compositions that cannot be checked and unconfirmed delivery never reach the model.
    assert candidates.isdisjoint({"P02", "P04", "P06", "P07", "P11", "P12", "P13"})
    assert {"P01", "P05", "P18"} <= candidates


def test_replacement_changes_only_the_target_and_checks_the_whole_plan(tmp_path: Path) -> None:
    current_plan = plan_from_looks(LOOK_1, LOOK_2, LOOK_3)
    target = Replacement(
        look_id="look_2",
        slot=Slot.TOP,
        item=ItemRef(kind=ItemKind.CATALOG, id="P03"),
    )
    locks = tuple(
        LockedPlacement(look_id=look.id, slot=slot, item=item)
        for look in current_plan.looks
        for slot, item in look.items.items()
        if not (look.id == target.look_id and slot is target.slot)
    )
    request_state = state(material_exclusions=frozenset(), plan=current_plan, locks=locks)
    request = PlanRequest(brief=BRIEF, state=request_state, replacement=target)
    changed_look_2 = {**LOOK_2, "top": "P16"}
    client, sent, _ = spark(reply(LOOK_1, changed_look_2, LOOK_3))
    app = create_app(Settings(web_dist=tmp_path, catalog_file=FIXTURE), spark=client)

    with TestClient(app) as http:
        response = http.post("/api/plans", json=request.model_dump(mode="json"))

    payload = response.json()
    assert response.status_code == 200 and payload["outcome"] == "success"
    assert payload["violations"] == []
    assert payload["plan"]["looks"][1]["items"]["top"]["id"] == "P16"
    submitted = json.loads(sent[0]["messages"][1]["content"])
    assert submitted["replacement"] == {
        "look_id": "look_2",
        "slot": "top",
        "item": {"kind": "catalog", "id": "P03"},
    }
    assert submitted["current_plan"][1]["items"]["top"] == {
        "kind": "catalog",
        "id": "P03",
    }
    assert len(submitted["locks"]) == 8


def test_replacement_retries_when_the_target_or_another_position_is_unchanged() -> None:
    current_plan = plan_from_looks(LOOK_1, LOOK_2, LOOK_3)
    target = Replacement(
        look_id="look_2",
        slot=Slot.TOP,
        item=ItemRef(kind=ItemKind.CATALOG, id="P03"),
    )
    request = PlanRequest(brief=BRIEF, state=state(plan=current_plan), replacement=target)
    invalid_replacement = reply(LOOK_1, {**LOOK_2, "bottom": "P09"}, LOOK_3)
    client, sent, _ = spark(invalid_replacement, invalid_replacement)

    result = plan_looks(
        request.brief,
        request.state,
        CATALOG,
        client,
        replacement=request.replacement,
    )

    assert result.outcome is Outcome.GENERATION_FAILED
    assert result.check and {issue.rule for issue in result.check.violations} == {Rule.REPLACEMENT}
    repair = sent[1]["messages"][-1]["content"]
    assert "Replace look_2 top with a different item" in repair
    assert "changed the unrelated look_2 bottom position" in repair


def test_replacement_target_must_be_unlocked_and_match_the_current_plan() -> None:
    current_plan = plan_from_looks(LOOK_1, LOOK_2, LOOK_3)
    target = Replacement(
        look_id="look_2",
        slot=Slot.TOP,
        item=ItemRef(kind=ItemKind.CATALOG, id="P03"),
    )
    locked = LockedPlacement(look_id="look_2", slot=Slot.TOP, item=target.item)

    with pytest.raises(ValueError, match="A locked item cannot be replaced"):
        PlanRequest(
            brief=BRIEF, state=state(plan=current_plan, locks=(locked,)), replacement=target
        )
    with pytest.raises(ValueError, match="does not match the current plan"):
        PlanRequest(
            brief=BRIEF,
            state=state(plan=current_plan),
            replacement=target.model_copy(
                update={"item": ItemRef(kind=ItemKind.CATALOG, id="P01")}
            ),
        )


def test_a_failing_plan_gets_one_repair_that_lists_its_violations() -> None:
    client, sent, _ = spark(WITH_WOOL, VALID)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert (result.outcome, result.usage.calls) == (Outcome.SUCCESS, 2)
    assert "P02 contains wool" in sent[1]["messages"][-1]["content"]


def test_incomplete_looks_get_one_repair() -> None:
    incomplete = reply({"top": "P01"}, {"top": "P03"}, {"top": "P09"})
    client, sent, _ = spark(incomplete, VALID)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert (result.outcome, result.usage.calls) == (Outcome.SUCCESS, 2)
    assert "top and bottom or a one_piece" in sent[1]["messages"][-1]["content"]


def test_items_in_the_wrong_slots_get_one_repair() -> None:
    misplaced = reply({"top": "P18", "bottom": "P05", "footwear": "P01"}, LOOK_2, LOOK_3)
    client, sent, _ = spark(misplaced, VALID)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert (result.outcome, result.usage.calls) == (Outcome.SUCCESS, 2)
    repair = sent[1]["messages"][-1]["content"]
    assert "P18 is a footwear item and cannot fill the top slot" in repair
    assert "P01 is a top item and cannot fill the footwear slot" in repair


@pytest.mark.parametrize(
    ("replies", "rule"),
    [((WITH_WOOL, WITH_WOOL), Rule.MATERIAL), ((WITH_WOOL, "I cannot help with that."), None)],
)
def test_plans_that_still_fail_end_as_generation_failed(replies: tuple, rule: Rule) -> None:
    client, _, _ = spark(*replies)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert result.outcome is Outcome.GENERATION_FAILED  # never reported as infeasible
    if rule:
        assert rule in {violation.rule for violation in result.check.violations}
    else:
        assert "JSON" in result.error


@pytest.mark.parametrize(
    ("outcomes", "expected", "requests"),
    [
        ((TimeoutError("timed out"), TimeoutError("timed out")), Outcome.PROVIDER_TIMEOUT, 2),
        ((http_error(504), http_error(504)), Outcome.PROVIDER_TIMEOUT, 2),
        ((http_error(500), http_error(500)), Outcome.PROVIDER_ERROR, 2),
        ((http_error(401),), Outcome.PROVIDER_ERROR, 1),  # not worth a retry
    ],
)
def test_provider_failures_are_retried_once_and_reported_as_such(
    outcomes: tuple, expected: Outcome, requests: int
) -> None:
    client, sent, slept = spark(*outcomes)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert (result.outcome, len(sent), result.usage.calls) == (expected, requests, 0)
    assert result.usage.http_attempts == requests
    assert "secret-key" not in (result.error or "")


def test_a_retry_after_a_rate_limit_succeeds() -> None:
    client, sent, slept = spark(http_error(429), VALID)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert result.outcome is Outcome.SUCCESS
    assert (len(sent), slept) == (2, [2])
    assert (result.usage.calls, result.usage.http_attempts) == (1, 2)


def test_planning_stops_at_the_deadline() -> None:
    clock = FakeClock()
    sent: list[dict[str, Any]] = []

    def post(request: Request, timeout_s: float) -> dict[str, Any]:
        sent.append(json.loads(request.data))
        clock.advance(200)
        return completion(VALID)

    client = SparkClient("secret-key", post=post)
    result = plan_looks(BRIEF, state(), CATALOG, client, clock=clock)
    assert (result.outcome, len(sent), result.usage.http_attempts) == (
        Outcome.PROVIDER_TIMEOUT,
        1,
        1,
    )


def test_one_deadline_covers_http_attempts_and_retry_waits() -> None:
    clock = FakeClock()
    requests: list[float] = []
    slept: list[float] = []
    outcomes: list[Any] = [http_error(429), WITH_WOOL, http_error(429), VALID]

    def post(request: Request, timeout_s: float) -> dict[str, Any]:
        requests.append(timeout_s)
        clock.advance(min(45, timeout_s))
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return completion(outcome)

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds)

    client = SparkClient("secret-key", post=post, sleep=sleep)
    result = plan_looks(BRIEF, state(), CATALOG, client, clock=clock)
    assert result.outcome is Outcome.PROVIDER_TIMEOUT
    assert result.usage.calls == 2
    assert result.usage.http_attempts == 4
    assert requests == [60, 60, 58, 11]
    assert slept == [2, 2]
    assert clock() == 150


def test_a_truncated_model_completion_is_not_a_plan() -> None:
    client = SparkClient(
        "secret-key",
        post=lambda request, timeout_s: completion(VALID, finish_reason="length"),
    )
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert result.outcome is Outcome.GENERATION_FAILED
    assert "finish_reason='length'" in result.error
    assert (result.usage.calls, result.usage.http_attempts) == (1, 1)


def test_http_timeout_cancels_the_full_async_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = False

    class SlowClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def request(self, *args: Any, **kwargs: Any) -> None:
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

    monkeypatch.setattr("backend.app.planner.httpx2.AsyncClient", SlowClient)
    with pytest.raises(TimeoutError):
        _post_json(Request("https://example.test"), 0.01)
    assert cancelled


def test_locks_that_break_the_budget_are_infeasible_without_a_model_call() -> None:
    current = Plan(
        looks=[
            Look(id=f"look_{n}", items={"top": ItemRef(kind=ItemKind.CATALOG, id="P10")})
            for n in (1, 2, 3)
        ],
        selected_offers={"P10": "offer_P10_AT"},
    )
    lock = LockedPlacement(
        look_id="look_1", slot="top", item=ItemRef(kind=ItemKind.CATALOG, id="P10")
    )
    client, sent, _ = spark()
    result = plan_looks(BRIEF, state(10000, locks=(lock,), plan=current), CATALOG, client)
    assert (result.outcome, sent) == (Outcome.INFEASIBLE, [])
    assert [conflict.rule for conflict in result.conflicts] == [Rule.BUDGET]


def test_logs_hold_usage_but_not_the_brief(caplog: pytest.LogCaptureFixture) -> None:
    client, _, _ = spark(VALID)
    with caplog.at_level(logging.INFO, logger="backend.app.planner"):
        plan_looks(BRIEF, state(), CATALOG, client)
    assert "outcome=success" in caplog.text and "prompt_tokens=1000" in caplog.text
    assert "dinners" not in caplog.text and "ivory" not in caplog.text


def body() -> dict[str, Any]:
    return {
        "brief": BRIEF.model_dump(mode="json"),
        "state": {"budget": {"amount_cents": 20000}, "material_exclusions": ["wool"]},
    }


def test_the_api_returns_a_checked_plan(tmp_path: Path) -> None:
    client, _, _ = spark(VALID)
    app = create_app(Settings(web_dist=tmp_path, catalog_file=FIXTURE), spark=client)
    with TestClient(app) as http:
        response = http.post("/api/plans", json=body())
    assert response.status_code == 200
    payload = response.json()
    assert (payload["outcome"], payload["violations"], payload["usage"]["calls"]) == (
        "success",
        [],
        1,
    )
    assert len(payload["plan"]["looks"]) == 3
    assert payload["usage"]["http_attempts"] == 1


def test_the_api_hides_a_plan_that_still_fails_checks(tmp_path: Path) -> None:
    client, _, _ = spark(WITH_WOOL, WITH_WOOL)
    app = create_app(Settings(web_dist=tmp_path, catalog_file=FIXTURE), spark=client)
    with TestClient(app) as http:
        response = http.post("/api/plans", json=body())
    payload = response.json()
    assert payload["outcome"] == "generation_failed"
    assert payload["plan"] is None
    assert any(violation["rule"] == Rule.MATERIAL.value for violation in payload["violations"])


def test_the_api_needs_a_key_and_stays_off_in_production(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(web_dist=tmp_path, catalog_file=FIXTURE))) as http:
        assert http.post("/api/plans", json=body()).status_code == 503
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    production = Settings(environment="production", web_dist=tmp_path, baseten_api_key="key")
    with TestClient(create_app(production)) as http:
        assert http.post("/api/plans", json=body()).status_code == 404
