"""The T-06 baseline uses the real catalog and the production hard-constraint checker."""

import io
import json
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from backend.app.catalog import Catalog, Size
from backend.app.constraints import check_plan
from backend.app.planner import GENERATION_ATTEMPTS, ProviderError, SparkClient
from backend.app.state import Fit, Slot
from scripts.evaluate_planner import (
    CASES,
    MAX_HTTP_ATTEMPTS_PER_GENERATION,
    MAX_LIVE_HTTP_ATTEMPTS,
    ProviderResponseRecorder,
    cheapest_baseline,
    plan_cost_cents,
)

CATALOG_PATH = Path(__file__).parents[1] / "backend" / "data" / "catalog.json"
CATALOG = Catalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def test_each_fixed_case_has_three_distinct_checked_baseline_looks() -> None:
    for case in CASES:
        plan, baseline_cost = cheapest_baseline(case, CATALOG)
        assert len(plan.looks) == 3
        assert check_plan(case.state(plan), CATALOG).passed
        assert (
            len(
                {
                    tuple(sorted(f"{slot.value}:{item.id}" for slot, item in look.items.items()))
                    for look in plan.looks
                }
            )
            == 3
        )
        assert plan_cost_cents(case, plan, CATALOG) == baseline_cost


def test_the_baseline_keeps_to_a_stated_fit() -> None:
    # Without a fit, the cheapest looks pair men's tops with leggings that have no line and
    # women's shorts.
    fit = Fit(lines={"men"}, sizes=[Size(system="eu_shoe", value="42")])
    case = replace(CASES[1], fit=fit)  # open materials, EUR 250.00 per look
    plan, _ = cheapest_baseline(case, CATALOG)  # raises unless check_plan passes with the fit
    assert check_plan(case.state(plan), CATALOG).passed
    assert {look.items[Slot.BOTTOM].id for look in plan.looks} <= {
        "ae-aaster-space-steel-32-32",
        "ae-kaavo-lolite-32-32",
    }
    assert {look.items[Slot.FOOTWEAR].id for look in plan.looks} == {"gw-beach-hellgrau-42"}


def test_cheapest_baseline_is_reproducible() -> None:
    for case in CASES:
        assert cheapest_baseline(case, CATALOG) == cheapest_baseline(case, CATALOG)


def test_three_case_live_run_is_capped_at_twelve_http_attempts() -> None:
    assert len(CASES) * GENERATION_ATTEMPTS * MAX_HTTP_ATTEMPTS_PER_GENERATION == 12
    assert MAX_LIVE_HTTP_ATTEMPTS == 12


def test_response_recorder_stores_payloads_and_blocks_attempts_over_the_cap(
    tmp_path: Path,
) -> None:
    sent = 0

    def post(request: Request, timeout_s: float) -> dict:
        nonlocal sent
        sent += 1
        return {"choices": [{"message": {"content": "validated plan"}}]}

    client = SparkClient("test-secret", post=post)
    recorder = ProviderResponseRecorder(tmp_path / "provider_responses.jsonl", "test-secret", 1)
    recorder.attach(client, "case-1")
    request = Request("https://inference.example/v1/chat/completions", method="POST")

    client.post(request, 5)
    with pytest.raises(ProviderError, match="attempt limit"):
        client.post(request, 5)

    entries = [
        json.loads(line) for line in recorder.output_file.read_text(encoding="utf-8").splitlines()
    ]
    assert sent == recorder.http_attempts == 1
    assert recorder.limit_reached
    assert entries == [
        {
            "case_id": "case-1",
            "http_attempt": 1,
            "response": {"choices": [{"message": {"content": "validated plan"}}]},
        }
    ]
    assert "test-secret" not in recorder.output_file.read_text(encoding="utf-8")


def test_response_recorder_redacts_key_from_http_error_body(tmp_path: Path) -> None:
    def post(request: Request, timeout_s: float) -> dict:
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"test-secret"}'),
        )

    client = SparkClient("test-secret", post=post)
    recorder = ProviderResponseRecorder(tmp_path / "provider_responses.jsonl", "test-secret")
    recorder.attach(client, "case-1")

    with pytest.raises(HTTPError) as raised:
        client.post(Request("https://inference.example/v1/chat/completions"), 5)

    assert raised.value.read() == b'{"error":"test-secret"}'
    entry = json.loads(recorder.output_file.read_text(encoding="utf-8"))
    assert entry["http_status"] == 401
    assert "test-secret" not in recorder.output_file.read_text(encoding="utf-8")
