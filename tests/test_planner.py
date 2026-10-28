"""Planning with Muse Spark, without network calls: the model's replies are scripted."""

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
from backend.app.planner import Brief, Outcome, SparkClient, plan_looks
from backend.app.state import Budget, ItemKind, ItemRef, LockedPlacement, Look, Plan, State

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


def completion(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
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
    return State(budget=Budget(amount_cents=budget_cents), material_exclusions={"wool"}, **fields)


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


def test_a_failing_plan_gets_one_repair_that_lists_its_violations() -> None:
    client, sent, _ = spark(WITH_WOOL, VALID)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert (result.outcome, result.usage.calls) == (Outcome.SUCCESS, 2)
    assert "P02 contains wool" in sent[1]["messages"][-1]["content"]


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
    assert "secret-key" not in (result.error or "")


def test_a_retry_after_a_rate_limit_succeeds() -> None:
    client, sent, slept = spark(http_error(429), VALID)
    assert plan_looks(BRIEF, state(), CATALOG, client).outcome is Outcome.SUCCESS
    assert (len(sent), slept) == (2, [2])


def test_planning_stops_at_the_deadline() -> None:
    client, sent, _ = spark(WITH_WOOL, VALID)
    times = iter([0.0, 0.0, 200.0, 200.0])
    result = plan_looks(BRIEF, state(), CATALOG, client, clock=lambda: next(times))
    assert (result.outcome, len(sent)) == (Outcome.PROVIDER_TIMEOUT, 1)


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


def test_the_api_needs_a_key_and_stays_off_in_production(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(web_dist=tmp_path, catalog_file=FIXTURE))) as http:
        assert http.post("/api/plans", json=body()).status_code == 503
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    production = Settings(environment="production", web_dist=tmp_path, baseten_api_key="key")
    with TestClient(create_app(production)) as http:
        assert http.post("/api/plans", json=body()).status_code == 404
