"""Compare Muse Spark with a deterministic cheapest-outfit baseline on fixed cases.

The default run is offline and produces baseline plans plus a blank human-score sheet. A live
run requires --live and at least one explicit --case-id; it uses the Baseten key from the
environment. run.json stores validated item IDs, aggregate usage and the run's provenance
(scripts/run_provenance.py), with the uncommitted code in source.diff.
provider_responses.jsonl, in the same run folder under the ignored runs/, stores every raw
provider response, and every HTTP error body with the API key redacted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

from backend.app.catalog import Catalog, Category, Offer, Size
from backend.app.config import Settings
from backend.app.constraints import cheapest_usable_offers, check_plan, cost
from backend.app.planner import (
    BASETEN_URL,
    GENERATION_ATTEMPTS,
    SPARK_MODEL,
    Brief,
    Outcome,
    PlanResult,
    ProviderError,
    SparkClient,
    plan_looks,
)
from backend.app.state import Budget, BudgetMode, Fit, ItemKind, ItemRef, Look, Plan, Slot, State
from scripts.run_provenance import recorded, write_record

ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = ROOT  # the Git checkout whose revision and uncommitted code the run record keeps
CATALOG_FILE = ROOT / "backend" / "data" / "catalog.json"
MAX_LIVE_HTTP_ATTEMPTS = 12  # shared by all cases in a run
MAX_HTTP_ATTEMPTS_PER_GENERATION = 2
INPUT_USD_PER_MILLION_TOKENS = 1.25
OUTPUT_USD_PER_MILLION_TOKENS = 4.25
# A person answers only for the example-fit case (D-42); other cases leave these blank.
HUMAN_ANSWER_COLUMNS = tuple(
    f"look_{look}_{question}_yes_no"
    for look in (1, 2, 3)
    for question in ("as_requested", "would_try_on")
)
SCORE_COLUMNS = (
    "case_id",
    "approach",
    "outcome",
    "hard_constraints_passed",
    "final_violation_count",
    "final_violations_by_rule",
    "distinct_looks",
    "rejected_proposals",
    "rejected_violation_count",
    "rejected_violations_by_rule",
    "provider_calls",
    "http_attempts",
    "prompt_tokens",
    "completion_tokens",
    "estimated_model_api_cost_usd",
    "latency_s",
    "catalog_cost_cents",
    *HUMAN_ANSWER_COLUMNS,
    "notes",
)


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    id: str
    brief: Brief
    budget: Budget
    exclusions: frozenset[str]
    fit: Fit = Fit()

    def state(self, plan: Plan | None = None) -> State:
        return State(
            budget=self.budget, material_exclusions=self.exclusions, fit=self.fit, plan=plan
        )


@dataclass(frozen=True, slots=True)
class CandidateLook:
    items: tuple[tuple[Slot, str], ...]
    cost_cents: int

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(f"{slot.value}:{variant_id}" for slot, variant_id in self.items)


@dataclass(slots=True)
class ProviderResponseRecorder:
    """Save model API responses outside Git and prevent calls beyond the run limit."""

    output_file: Path
    api_key: str
    max_http_attempts: int = MAX_LIVE_HTTP_ATTEMPTS
    http_attempts: int = 0
    limit_reached: bool = False

    def attach(self, client: SparkClient, case_id: str) -> None:
        original_post = client.post

        def record(request: Request, timeout_s: float) -> dict[str, Any]:
            if self.http_attempts >= self.max_http_attempts:
                self.limit_reached = True
                raise ProviderError("T-06 HTTP attempt limit reached", http_attempts=0)
            self.http_attempts += 1
            attempt = self.http_attempts
            try:
                response = original_post(request, timeout_s)
            except HTTPError as error:
                body = error.read()
                self._write(
                    {
                        "case_id": case_id,
                        "http_attempt": attempt,
                        "http_status": error.code,
                        "response_body": self._redact(body.decode("utf-8", errors="replace")),
                    }
                )
                raise HTTPError(
                    error.url,
                    error.code,
                    error.msg,
                    error.hdrs,
                    io.BytesIO(body),
                ) from error
            self._write({"case_id": case_id, "http_attempt": attempt, "response": response})
            return response

        client.post = record

    def _write(self, entry: dict[str, Any]) -> None:
        try:
            with self.output_file.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                file.flush()
        except OSError as error:
            raise RuntimeError("Could not persist a Baseten response") from error

    def _redact(self, text: str) -> str:
        return text.replace(self.api_key, "[REDACTED]") if self.api_key else text


CASES = (
    EvaluationCase(
        id="wool-free-per-look",
        brief=Brief(
            style_goal="quiet smart casual for dinners",
            preferred_colors=("black", "grey", "earth tones"),
        ),
        budget=Budget(amount_cents=18_000, mode=BudgetMode.PER_LOOK),
        exclusions=frozenset({"wool"}),
    ),
    EvaluationCase(
        id="open-materials-per-look",
        brief=Brief(
            style_goal="relaxed casual for mild spring weather",
            preferred_colors=("black", "blue", "red"),
        ),
        budget=Budget(amount_cents=25_000, mode=BudgetMode.PER_LOOK),
        exclusions=frozenset(),
    ),
    EvaluationCase(
        id="wool-free-whole-plan",
        brief=Brief(
            style_goal="a minimal travel capsule with varied outfits",
            preferred_colors=("black", "grey", "neutral"),
        ),
        budget=Budget(amount_cents=22_000, mode=BudgetMode.WHOLE_PLAN),
        exclusions=frozenset({"wool"}),
    ),
    # An example fit, not a real person's; without an exclusion the closed shoes pass the item
    # checks, though none fits €250 with a top and bottom (EVALUATION.md).
    EvaluationCase(
        id="mens-fit-per-look",
        brief=Brief(
            style_goal="smart casual for autumn office days",
            preferred_colors=("navy", "grey", "black"),
        ),
        budget=Budget(amount_cents=25_000, mode=BudgetMode.PER_LOOK),
        exclusions=frozenset(),
        fit=Fit(
            lines={"men"},
            garment_types={"trousers", "jeans"},
            sizes=(
                Size(system="letter", value="L"),
                Size(system="waist_length", value="32/32"),
                Size(system="eu_shoe", value="42"),
                Size(system="eu_shoe", value="43"),
            ),
        ),
    ),
)


def candidate_looks(catalog: Catalog, offers: dict[str, Offer]) -> list[CandidateLook]:
    """Enumerate base outfits with footwear; extra layers are outside this baseline."""
    tops = [
        variant
        for product in catalog.products
        if product.category in {Category.TOP, Category.KNITWEAR}
        for variant in product.variants
        if variant.id in offers
    ]
    bottoms = [
        variant
        for product in catalog.products
        if product.category is Category.BOTTOM
        for variant in product.variants
        if variant.id in offers
    ]
    one_pieces = [
        variant
        for product in catalog.products
        if product.category is Category.ONE_PIECE
        for variant in product.variants
        if variant.id in offers
    ]
    footwear = [
        variant
        for product in catalog.products
        if product.category is Category.FOOTWEAR
        for variant in product.variants
        if variant.id in offers
    ]

    base_outfits = [
        ((Slot.TOP, top.id), (Slot.BOTTOM, bottom.id)) for top in tops for bottom in bottoms
    ] + [((Slot.ONE_PIECE, item.id),) for item in one_pieces]

    result = []
    for base in base_outfits:
        for shoes in footwear:
            items = tuple(
                sorted((*base, (Slot.FOOTWEAR, shoes.id)), key=lambda pair: pair[0].value)
            )
            price = sum(offers[variant_id].price_cents or 0 for _, variant_id in items)
            result.append(CandidateLook(items=items, cost_cents=price))
    return sorted(result, key=lambda look: (look.cost_cents, look.signature))


# A whole-plan baseline combines only this many of the cheapest outfits: every set of three
# would be hundreds of millions of combinations once the catalog holds about fifty variants.
WHOLE_PLAN_SEARCH_LOOKS = 60


def cheapest_baseline(case: EvaluationCase, catalog: Catalog) -> tuple[Plan, int]:
    """Return the cheapest distinct three-look plan that passes the shared hard checks."""
    offers = cheapest_usable_offers(catalog, case.state())
    candidates = candidate_looks(catalog, offers)
    chosen_looks: tuple[CandidateLook, ...] | None = None
    plan_cost: int | None = None
    if case.budget.mode is BudgetMode.PER_LOOK:
        affordable = [look for look in candidates if look.cost_cents <= case.budget.amount_cents]
        if len(affordable) >= 3:
            chosen_looks = tuple(affordable[:3])
            plan_cost = sum(look.cost_cents for look in chosen_looks)
    else:
        best_rank: tuple[int, tuple[tuple[str, ...], ...]] | None = None
        best_looks: tuple[CandidateLook, ...] | None = None
        for chosen in itertools.combinations(candidates[:WHOLE_PLAN_SEARCH_LOOKS], 3):
            variant_ids = {variant_id for look in chosen for _, variant_id in look.items}
            total = sum(offers[variant_id].price_cents or 0 for variant_id in variant_ids)
            if total > case.budget.amount_cents:
                continue
            rank = (total, tuple(look.signature for look in chosen))
            if best_rank is None or rank < best_rank:
                best_rank, best_looks = rank, chosen
        if best_rank is not None and best_looks is not None:
            chosen_looks = best_looks
            plan_cost = best_rank[0]

    if chosen_looks is None or plan_cost is None:
        raise ValueError(f"No three-look baseline passes the hard checks for {case.id}")
    looks = tuple(
        Look(
            id=f"look_{index}",
            items={
                slot: ItemRef(kind=ItemKind.CATALOG, id=variant_id)
                for slot, variant_id in candidate.items
            },
        )
        for index, candidate in enumerate(chosen_looks, start=1)
    )
    variant_ids = {
        item.id for look in looks for item in look.items.values() if item.kind is ItemKind.CATALOG
    }
    plan = Plan(
        looks=looks,
        selected_offers={variant_id: offers[variant_id].id for variant_id in variant_ids},
    )
    if not check_plan(case.state(plan), catalog).passed:
        raise ValueError(f"The selected baseline failed the hard checks for {case.id}")
    return plan, plan_cost


def plan_cost_cents(case: EvaluationCase, plan: Plan, catalog: Catalog) -> int | None:
    if case.budget.mode is BudgetMode.PER_LOOK:
        costs = [cost(look.items.values(), plan.selected_offers, catalog) for look in plan.looks]
        return None if None in costs else sum(value for value in costs if value is not None)
    return cost(
        (item for look in plan.looks for item in look.items.values()),
        plan.selected_offers,
        catalog,
    )


def distinct_look_count(plan: Plan) -> int:
    signatures = {
        tuple(sorted((slot.value, item.kind.value, item.id) for slot, item in look.items.items()))
        for look in plan.looks
    }
    return len(signatures)


def plan_metrics(
    case: EvaluationCase, result: PlanResult, latency_s: float, catalog: Catalog
) -> dict[str, Any]:
    final_violations = result.check.violations if result.check else ()
    final_violations_by_rule = dict(
        sorted(Counter(violation.rule.value for violation in final_violations).items())
    )
    return {
        "outcome": result.outcome.value,
        "hard_constraints_passed": result.outcome is Outcome.SUCCESS
        and result.check is not None
        and result.check.passed,
        "final_violation_count": len(final_violations),
        "final_violations_by_rule": final_violations_by_rule,
        "distinct_looks": distinct_look_count(result.plan) if result.plan else None,
        "rejected_proposals": result.proposal_metrics.constraint_failures
        + result.proposal_metrics.format_failures,
        "rejected_constraint_failures": result.proposal_metrics.constraint_failures,
        "rejected_format_failures": result.proposal_metrics.format_failures,
        "rejected_violation_count": result.proposal_metrics.constraint_violations,
        "rejected_violations_by_rule": dict(result.proposal_metrics.constraint_violations_by_rule),
        "provider_calls": result.usage.calls,
        "http_attempts": result.usage.http_attempts,
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "estimated_model_api_cost_usd": round(
            result.usage.prompt_tokens * INPUT_USD_PER_MILLION_TOKENS / 1_000_000
            + result.usage.completion_tokens * OUTPUT_USD_PER_MILLION_TOKENS / 1_000_000,
            6,
        ),
        "latency_s": round(latency_s, 3),
        "catalog_cost_cents": (
            plan_cost_cents(case, result.plan, catalog) if result.plan is not None else None
        ),
        "plan": result.plan.model_dump(mode="json") if result.plan is not None else None,
    }


def baseline_metrics(
    case: EvaluationCase, plan: Plan, plan_cost: int, catalog: Catalog
) -> dict[str, Any]:
    """The plan_metrics keys for a baseline plan, which makes no provider calls."""
    return {
        "outcome": "baseline",
        "hard_constraints_passed": check_plan(case.state(plan), catalog).passed,
        "final_violation_count": 0,
        "final_violations_by_rule": {},
        "distinct_looks": distinct_look_count(plan),
        "rejected_proposals": 0,
        "rejected_constraint_failures": 0,
        "rejected_format_failures": 0,
        "rejected_violation_count": 0,
        "rejected_violations_by_rule": {},
        "provider_calls": 0,
        "http_attempts": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "estimated_model_api_cost_usd": 0,
        "latency_s": 0,
        "catalog_cost_cents": plan_cost,
        "plan": plan.model_dump(mode="json"),
    }


def score_row(case_id: str, approach: str, metrics: dict[str, Any]) -> dict[str, str]:
    def blank_if_none(key: str) -> str:
        value = metrics.get(key)
        return "" if value is None else str(value)

    return {
        "case_id": case_id,
        "approach": approach,
        "outcome": str(metrics["outcome"]),
        "hard_constraints_passed": str(metrics["hard_constraints_passed"]).lower(),
        "final_violation_count": str(metrics["final_violation_count"]),
        "final_violations_by_rule": json.dumps(
            metrics.get("final_violations_by_rule", {}), sort_keys=True
        ),
        "distinct_looks": blank_if_none("distinct_looks"),
        "rejected_proposals": str(metrics.get("rejected_proposals", 0)),
        "rejected_violation_count": str(metrics.get("rejected_violation_count", 0)),
        "rejected_violations_by_rule": json.dumps(
            metrics.get("rejected_violations_by_rule", {}), sort_keys=True
        ),
        "provider_calls": str(metrics.get("provider_calls", 0)),
        "http_attempts": str(metrics.get("http_attempts", 0)),
        "prompt_tokens": str(metrics.get("prompt_tokens", 0)),
        "completion_tokens": str(metrics.get("completion_tokens", 0)),
        "estimated_model_api_cost_usd": str(metrics.get("estimated_model_api_cost_usd", 0)),
        "latency_s": str(metrics.get("latency_s", "")),
        "catalog_cost_cents": blank_if_none("catalog_cost_cents"),
        **dict.fromkeys(HUMAN_ANSWER_COLUMNS, ""),
        "notes": "",
    }


def write_run(output_dir: Path, run: dict[str, Any], score_rows: list[dict[str, str]]) -> None:
    write_record(output_dir, run)
    with (output_dir / "human_scores.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SCORE_COLUMNS)
        writer.writeheader()
        writer.writerows(score_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="call Muse Spark on Baseten; requires explicit --case-id selection",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        choices=[case.id for case in CASES],
        help="evaluate this fixed case; may be repeated",
    )
    args = parser.parse_args()
    if args.live and not args.case_id:
        parser.error("--live requires one or more explicit --case-id values")
    if args.case_id and len(set(args.case_id)) != len(args.case_id):
        parser.error("--case-id values must be unique")
    return args


def main(output_root: Path = ROOT / "runs" / "t06") -> None:
    args = parse_args()
    cases = [case for case in CASES if not args.case_id or case.id in args.case_id]
    catalog_bytes = CATALOG_FILE.read_bytes()
    catalog = Catalog.model_validate_json(catalog_bytes)
    now = datetime.now(UTC)
    output_dir = output_root / now.strftime("%Y%m%dT%H%M%S%fZ")
    run: dict[str, Any] = {
        "created_at_utc": now.isoformat(),
        "catalog_id": catalog.id,
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "source_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "scripts/evaluate_planner.py",
                "backend/app/planner.py",
                "backend/app/catalog.py",
                "backend/app/constraints.py",
                "backend/app/state.py",
            )
        },
        "mode": "live" if args.live else "baseline_only",
        "case_ids": [case.id for case in cases],
        "token_pricing": {
            "input_uncached_usd_per_million": INPUT_USD_PER_MILLION_TOKENS,
            "output_usd_per_million": OUTPUT_USD_PER_MILLION_TOKENS,
            "checked_on": "2026-10-29",
            "source": "https://www.baseten.co/pricing/",
        },
        "cases": {},
    }
    score_rows = []
    settings = Settings.from_env() if args.live else None
    if settings and not settings.baseten_api_key:
        raise SystemExit("BASETEN_API_KEY is not set; use --env-file .env")
    if settings and settings.baseten_model not in ("", SPARK_MODEL):
        raise SystemExit(f"The T-06 runner requires model {SPARK_MODEL}")
    if settings and settings.baseten_base_url not in ("", BASETEN_URL):
        raise SystemExit(f"The T-06 runner requires endpoint {BASETEN_URL}")
    attempts_per_case = GENERATION_ATTEMPTS * MAX_HTTP_ATTEMPTS_PER_GENERATION
    run["model"] = SPARK_MODEL if args.live else None
    run["base_url"] = BASETEN_URL if args.live else None
    run["attempt_limits"] = {
        "cases": len(cases),
        "generations_per_case": GENERATION_ATTEMPTS,
        "http_attempts_per_generation": MAX_HTTP_ATTEMPTS_PER_GENERATION,
        "total_http_attempts": min(len(cases) * attempts_per_case, MAX_LIVE_HTTP_ATTEMPTS),
    }
    run["provider_response_file"] = "provider_responses.jsonl" if args.live else None
    output_dir.mkdir(parents=True, exist_ok=False)
    response_recorder = (
        ProviderResponseRecorder(
            output_file=output_dir / "provider_responses.jsonl",
            api_key=settings.baseten_api_key,
        )
        if settings
        else None
    )

    with recorded(output_dir, run, CODE_ROOT):
        try:
            for case in cases:
                baseline, baseline_cost = cheapest_baseline(case, catalog)
                baseline_summary = baseline_metrics(case, baseline, baseline_cost, catalog)
                case_result: dict[str, Any] = {
                    "inputs": {
                        "brief": case.brief.model_dump(mode="json"),
                        "budget_amount_cents": case.budget.amount_cents,
                        "budget_mode": case.budget.mode.value,
                        "material_exclusions": sorted(case.exclusions),
                        "fit": case.fit.model_dump(mode="json"),
                    },
                    "baseline": baseline_summary,
                }
                score_rows.append(score_row(case.id, "cheapest_baseline", baseline_summary))

                if (
                    response_recorder
                    and response_recorder.max_http_attempts - response_recorder.http_attempts
                    < attempts_per_case
                ):
                    # A case cut short by the shared cap would look like a provider failure, so a
                    # case runs only with its full allowance left.
                    response_recorder.limit_reached = True
                    case_result["muse_spark_not_run"] = (
                        f"fewer than {attempts_per_case} of the run's "
                        f"{response_recorder.max_http_attempts} HTTP attempts remained"
                    )
                elif settings and response_recorder:
                    client = SparkClient(
                        api_key=settings.baseten_api_key,
                        base_url=BASETEN_URL,
                        model=SPARK_MODEL,
                        max_attempts=MAX_HTTP_ATTEMPTS_PER_GENERATION,
                    )
                    response_recorder.attach(client, case.id)
                    started = time.monotonic()
                    result = plan_looks(case.brief, case.state(), catalog, client)
                    model_metrics = plan_metrics(case, result, time.monotonic() - started, catalog)
                    case_result["muse_spark"] = model_metrics
                    score_rows.append(score_row(case.id, "muse_spark", model_metrics))
                run["cases"][case.id] = case_result
                if response_recorder:
                    run["http_attempts_used"] = response_recorder.http_attempts
                    run["attempt_limit_reached"] = response_recorder.limit_reached
                write_run(output_dir, run, score_rows)
        finally:
            # A run that fails part-way still records the HTTP attempts it made.
            if response_recorder:
                run["http_attempts_used"] = response_recorder.http_attempts
                run["attempt_limit_reached"] = response_recorder.limit_reached

    print(f"mode={run['mode']} catalog={catalog.id} output={output_dir}")
    for case_id, metrics in run["cases"].items():
        baseline_summary = metrics["baseline"]
        print(
            f"{case_id}: baseline cost={baseline_summary['catalog_cost_cents']} cents; "
            f"hard_checks={'pass' if baseline_summary['hard_constraints_passed'] else 'fail'}"
        )
        if "muse_spark" in metrics:
            model = metrics["muse_spark"]
            print(
                f"  Muse Spark: {model['outcome']}; calls={model['provider_calls']}; "
                f"tokens={model['prompt_tokens']}+{model['completion_tokens']}; "
                f"rejected_constraints={model['rejected_violation_count']}"
            )
        elif "muse_spark_not_run" in metrics:
            print(f"  Muse Spark: not run; {metrics['muse_spark_not_run']}")


if __name__ == "__main__":
    main()
