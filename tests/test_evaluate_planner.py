"""The T-06 baseline uses the real catalog and the production hard-constraint checker."""

import io
import json
import os
import sys
from dataclasses import replace
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from backend.app.catalog import Catalog, Size
from backend.app.constraints import check_plan
from backend.app.planner import Outcome, PlanResult, ProviderError, SparkClient, Usage
from backend.app.state import Fit, Slot
from scripts import evaluate_planner
from scripts.evaluate_planner import (
    CASES,
    MAX_LIVE_HTTP_ATTEMPTS,
    ProviderResponseRecorder,
    baseline_metrics,
    cheapest_baseline,
    plan_cost_cents,
    plan_metrics,
    score_row,
)
from tests.test_run_provenance import make_repo

CATALOG_PATH = Path(__file__).parents[1] / "backend" / "data" / "catalog.json"
CATALOG = Catalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def checkout(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway Git checkout for the run record, so a test never copies the developer's
    uncommitted work into its output."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = make_repo(tmp_path_factory.mktemp("checkout") / "repo")
    monkeypatch.setattr(evaluate_planner, "CODE_ROOT", repo)
    return repo


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
    items = [item for look in plan.looks for item in look.items.values()]
    lines = {CATALOG.product_of(item.id).line for item in items}  # type: ignore[union-attr]
    assert lines <= {"men", "unisex"}
    shoes = {CATALOG.variant(look.items[Slot.FOOTWEAR].id) for look in plan.looks}
    assert {shoe.normalized_size.value for shoe in shoes} == {"42"}  # type: ignore[union-attr]


def test_cheapest_baseline_is_reproducible() -> None:
    for case in CASES:
        assert cheapest_baseline(case, CATALOG) == cheapest_baseline(case, CATALOG)


def test_baseline_and_model_metrics_have_the_same_keys() -> None:
    case = CASES[0]
    plan, plan_cost = cheapest_baseline(case, CATALOG)
    baseline = baseline_metrics(case, plan, plan_cost, CATALOG)
    model = plan_metrics(case, PlanResult(Outcome.PROVIDER_ERROR, Usage()), 1.0, CATALOG)
    assert baseline.keys() == model.keys()
    assert baseline["rejected_constraint_failures"] == baseline["rejected_format_failures"] == 0


def test_a_score_row_without_a_plan_leaves_its_plan_columns_blank() -> None:
    case = CASES[0]
    failed = PlanResult(Outcome.PROVIDER_ERROR, Usage(), error="HTTP 500")
    row = score_row(case.id, "muse_spark", plan_metrics(case, failed, 1.0, CATALOG))
    assert (row["distinct_looks"], row["catalog_cost_cents"]) == ("", "")


@pytest.mark.parametrize(
    ("server_errors", "attempts_per_case"),
    [
        # A generation that gets a 500 retries, and every reply is unusable, so each
        # generation is followed by a repair.
        pytest.param({1, 3, 5, 7, 9, 11}, [4, 4, 4], id="cap-spent"),
        pytest.param({1, 3, 5, 7, 9}, [4, 4, 3], id="one-attempt-left"),
    ],
)
def test_a_live_run_starts_a_case_only_with_its_full_share_of_twelve_attempts_left(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkout: Path,
    server_errors: set[int],
    attempts_per_case: list[int],
) -> None:
    sent = 0

    def post(request: Request, timeout_s: float) -> dict:
        nonlocal sent
        sent += 1
        if sent in server_errors:
            raise HTTPError(request.full_url, 500, "Server Error", {}, io.BytesIO(b"{}"))
        return {"choices": [{"message": {"content": "not a plan"}, "finish_reason": "stop"}]}

    client = partial(SparkClient, post=post, sleep=lambda _: None)
    monkeypatch.setattr(evaluate_planner, "SparkClient", client)
    monkeypatch.setenv("BASETEN_API_KEY", "test-secret")
    monkeypatch.delenv("BASETEN_MODEL", raising=False)
    monkeypatch.delenv("BASETEN_BASE_URL", raising=False)
    case_args = [arg for case in CASES for arg in ("--case-id", case.id)]
    monkeypatch.setattr(sys, "argv", ["evaluate_planner", "--live", *case_args])

    evaluate_planner.main(output_root=tmp_path)

    run = json.loads(next(tmp_path.glob("*/run.json")).read_text(encoding="utf-8"))
    assert run["status"] == "succeeded"
    assert run["case_ids"] == [case.id for case in CASES]
    assert run["attempt_limits"]["total_http_attempts"] == MAX_LIVE_HTTP_ATTEMPTS == 12
    assert sent == run["http_attempts_used"] == sum(attempts_per_case)
    assert run["attempt_limit_reached"]
    *spent, last = (run["cases"][case.id] for case in CASES)
    assert [case["muse_spark"]["http_attempts"] for case in spent] == attempts_per_case
    assert "muse_spark" not in last and last["muse_spark_not_run"]


def test_a_live_run_that_raises_keeps_a_failed_record_without_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checkout: Path
) -> None:
    statuses = []

    def post(request: Request, timeout_s: float) -> dict:
        (record,) = tmp_path.glob("*/run.json")
        statuses.append(json.loads(record.read_text(encoding="utf-8"))["status"])
        raise RuntimeError("connection dropped after sending test-secret-value")

    monkeypatch.setattr(evaluate_planner, "SparkClient", partial(SparkClient, post=post))
    monkeypatch.setenv("BASETEN_API_KEY", "test-secret-value")
    monkeypatch.delenv("BASETEN_MODEL", raising=False)
    monkeypatch.delenv("BASETEN_BASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["evaluate_planner", "--live", "--case-id", CASES[0].id])

    with pytest.raises(RuntimeError, match="connection dropped"):
        evaluate_planner.main(output_root=tmp_path)

    (run_dir,) = tmp_path.iterdir()
    text = (run_dir / "run.json").read_text(encoding="utf-8")
    run = json.loads(text)
    assert statuses == ["running"]  # the record existed before the provider call
    assert run["status"] == "failed"
    assert run["error"] == {
        "type": "RuntimeError",
        "message": "connection dropped after sending [REDACTED]",
    }
    assert "test-secret-value" not in text
    assert b"test-secret-value" not in (run_dir / "source.diff").read_bytes()
    assert run["case_ids"] == [CASES[0].id] and run["cases"] == {}
    assert run["http_attempts_used"] == 1  # the attempt that failed after it was sent
    assert run["provider_keys_set"]["BASETEN_API_KEY"] is True
    assert run["git_sha"] and run["lock_file_sha256"]["uv.lock"]
    assert run["catalog_sha256"] and run["source_sha256"]["backend/app/planner.py"]


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
