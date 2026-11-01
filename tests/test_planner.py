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

from backend.app.catalog import Catalog
from backend.app.constraints import Rule
from backend.app.planner import (
    MAX_TOKENS,
    REASONING_EFFORT,
    Brief,
    Occasion,
    Outcome,
    PlanRequest,
    Replacement,
    Season,
    SparkClient,
    _post_json,
    plan_looks,
)
from backend.app.state import (
    Budget,
    BudgetMode,
    Fit,
    FootwearChoice,
    ItemKind,
    ItemRef,
    LockedPlacement,
    Look,
    OwnedItem,
    Plan,
    Slot,
    State,
)

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
    assert result.proposal_metrics.completed == 1
    assert result.proposal_metrics.format_failures == 0
    assert result.proposal_metrics.constraint_failures == 0
    assert result.plan.selected_offers == {
        variant_id: f"offer_{variant_id}_AT" for variant_id in ("P01", "P03", "P05", "P09", "P18")
    }
    assert (result.usage.calls, result.usage.prompt_tokens) == (1, 1000)
    assert sent[0]["max_tokens"] == MAX_TOKENS
    assert sent[0]["reasoning_effort"] == REASONING_EFFORT == "medium"
    request = json.loads(sent[0]["messages"][1]["content"])
    candidates = {candidate["id"] for candidate in request["candidates"]}
    # Wool, compositions that cannot be checked and unconfirmed delivery never reach the model.
    assert candidates.isdisjoint({"P02", "P04", "P06", "P07", "P11", "P12", "P13"})
    assert {"P01", "P05", "P18"} <= candidates


def test_replacement_changes_only_the_target_and_checks_the_whole_plan() -> None:
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
    changed_look_2 = {**LOOK_2, "top": "P16", "bottom": "P09"}
    client, sent, _ = spark(reply(LOOK_1, changed_look_2, LOOK_3))

    result = plan_looks(
        request.brief, request.state, CATALOG, client, replacement=request.replacement
    )

    assert result.outcome is Outcome.SUCCESS and result.check and result.check.passed
    assert result.plan is not None
    assert result.plan.looks[1].items[Slot.TOP].id == "P16"
    assert result.plan.looks[1].items[Slot.BOTTOM].id == "P05"
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


def test_replacement_does_not_restore_an_item_twice_in_the_same_look() -> None:
    current_look_2 = {**LOOK_2, "mid_layer": "P01"}
    current_plan = plan_from_looks(LOOK_1, current_look_2, LOOK_3)
    target = Replacement(
        look_id="look_2",
        slot=Slot.TOP,
        item=ItemRef(kind=ItemKind.CATALOG, id="P03"),
    )
    proposed_look_2 = {**current_look_2, "top": "P01", "mid_layer": "P03"}
    invalid = reply(LOOK_1, proposed_look_2, LOOK_3)
    client, _, _ = spark(invalid, invalid)

    result = plan_looks(
        BRIEF,
        state(50_000, material_exclusions=frozenset(), plan=current_plan),
        CATALOG,
        client,
        replacement=target,
    )

    assert result.outcome is Outcome.GENERATION_FAILED
    assert result.check and Rule.REPLACEMENT in {issue.rule for issue in result.check.violations}


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
    assert result.proposal_metrics.completed == 2
    assert result.proposal_metrics.constraint_failures == 1
    assert result.proposal_metrics.constraint_violations == 2
    assert result.proposal_metrics.constraint_violations_by_rule == (
        (Rule.BUDGET.value, 1),
        (Rule.MATERIAL.value, 1),
    )
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
    assert result.proposal_metrics.completed == 2
    if rule:
        assert rule in {violation.rule for violation in result.check.violations}
        assert result.proposal_metrics.constraint_failures == 2
        assert result.proposal_metrics.format_failures == 0
    else:
        assert "JSON" in result.error
        assert result.proposal_metrics.constraint_failures == 1
        assert result.proposal_metrics.format_failures == 1
        assert result.proposal_metrics.constraint_violations_by_rule == (
            (Rule.BUDGET.value, 1),
            (Rule.MATERIAL.value, 1),
        )


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

    monkeypatch.setattr("backend.app.meta_images.httpx2.AsyncClient", SlowClient)
    with pytest.raises(TimeoutError):
        _post_json(Request("https://example.test"), 0.01)
    assert cancelled


def test_a_client_needs_at_least_one_attempt() -> None:
    with pytest.raises(ValueError, match="max_attempts must be at least 1"):
        SparkClient("secret-key", max_attempts=0)


@pytest.mark.parametrize("mode", list(BudgetMode))
@pytest.mark.parametrize(
    ("footwear", "cheapest"),
    # Under a wool exclusion: P01 EUR 55 and P05 EUR 65, with P18 EUR 60 when shoes are required.
    [(FootwearChoice.ANY, 12000), (FootwearChoice.CLOSED, 18000)],
)
def test_a_budget_under_the_cheapest_complete_look_is_infeasible_without_a_model_call(
    mode: BudgetMode, footwear: FootwearChoice, cheapest: int
) -> None:
    def request(amount_cents: int) -> State:
        return State(
            budget=Budget(amount_cents=amount_cents, mode=mode),
            material_exclusions={"wool"},
            footwear=footwear,
        )

    client, sent, _ = spark()
    result = plan_looks(BRIEF, request(cheapest - 1), CATALOG, client)
    assert result.outcome is Outcome.INFEASIBLE and sent == []
    assert [(c.rule, c.cost_cents) for c in result.conflicts] == [(Rule.BUDGET, cheapest)]

    client, sent, _ = spark(VALID, VALID)
    assert plan_looks(BRIEF, request(cheapest), CATALOG, client).outcome is not Outcome.INFEASIBLE
    assert sent


def test_an_owned_item_can_fill_a_position_of_the_cheapest_look_for_free() -> None:
    owned = OwnedItem(id="O1", category="top", color="red", description="red shirt")
    client, sent, _ = spark()
    result = plan_looks(BRIEF, state(6499, owned_items=(owned,)), CATALOG, client)
    assert [c.cost_cents for c in result.conflicts] == [6500] and sent == []  # P05 alone

    client, sent, _ = spark(VALID, VALID)
    plan_looks(BRIEF, state(6500, owned_items=(owned,)), CATALOG, client)
    assert sent


@pytest.mark.parametrize(
    ("looks", "slot", "item", "cheapest"),
    [
        ((LOOK_1, LOOK_2, LOOK_3), Slot.TOP, "P03", 13500),  # P03 EUR 70 and P05 EUR 65
        ((LOOK_1, LOOK_2, LOOK_3), Slot.BOTTOM, "P05", 12000),  # P05 EUR 65 and P01 EUR 55
    ],
)
def test_a_kept_item_counts_with_the_cheapest_items_its_look_still_needs(
    looks: tuple[dict[str, str], ...], slot: Slot, item: str, cheapest: int
) -> None:
    current = plan_from_looks(*looks)
    look = next(look for look in current.looks if look.items.get(slot, None) == ref_of(item))
    lock = LockedPlacement(look_id=look.id, slot=slot, item=ref_of(item))

    client, sent, _ = spark()
    result = plan_looks(BRIEF, state(cheapest - 1, plan=current, locks=(lock,)), CATALOG, client)
    assert result.outcome is Outcome.INFEASIBLE and sent == []
    assert (Rule.BUDGET, cheapest) in [(c.rule, c.cost_cents) for c in result.conflicts]

    client, sent, _ = spark(VALID, VALID)
    plan_looks(BRIEF, state(cheapest, plan=current, locks=(lock,)), CATALOG, client)
    assert sent


def test_a_kept_one_piece_needs_no_top_or_bottom() -> None:
    current = plan_from_looks({"one_piece": "P12"}, LOOK_2, LOOK_3)
    lock = LockedPlacement(look_id="look_1", slot=Slot.ONE_PIECE, item=ref_of("P12"))
    client, sent, _ = spark(VALID, VALID)
    request = state(9500, plan=current, locks=(lock,), material_exclusions=set())
    result = plan_looks(BRIEF, request, CATALOG, client)
    assert sent and result.outcome is not Outcome.INFEASIBLE


def ref_of(item: str) -> ItemRef:
    return ItemRef(kind=ItemKind.CATALOG, id=item)


def test_a_replacement_cannot_fix_what_the_positions_it_keeps_break() -> None:
    current = plan_from_looks(LOOK_1, LOOK_2, LOOK_3)
    top = Replacement(look_id="look_1", slot=Slot.TOP, item=ref_of("P01"))

    client, sent, _ = spark()
    # Look 2 stays as it is and costs EUR 195.
    result = plan_looks(BRIEF, state(19499, plan=current), CATALOG, client, replacement=top)
    assert result.outcome is Outcome.INFEASIBLE and sent == []
    assert ("look_2", Rule.BUDGET) in [(c.look_id, c.rule) for c in result.conflicts]

    # P09 is viscose and stays in look 3.
    excluded = state(20000, plan=current, material_exclusions={"viscose"})
    result = plan_looks(BRIEF, excluded, CATALOG, client, replacement=top)
    assert result.outcome is Outcome.INFEASIBLE and sent == []
    assert [c.rule for c in result.conflicts] == [Rule.MATERIAL]

    # The first look has no shoes and keeps that position empty.
    no_shoes = plan_from_looks({"top": "P01", "bottom": "P05"}, LOOK_2, LOOK_3)
    closed = state(20000, plan=no_shoes, footwear=FootwearChoice.CLOSED)
    result = plan_looks(BRIEF, closed, CATALOG, client, replacement=top)
    assert result.outcome is Outcome.INFEASIBLE and sent == []
    assert [c.rule for c in result.conflicts] == [Rule.LOOK_COMPOSITION]


def test_a_replacement_can_remove_the_item_that_broke_a_constraint() -> None:
    current = plan_from_looks(LOOK_1, LOOK_2, LOOK_3)
    client, sent, _ = spark(VALID, VALID)
    bottom = Replacement(look_id="look_3", slot=Slot.BOTTOM, item=ref_of("P09"))
    excluded = state(20000, plan=current, material_exclusions={"viscose"})
    result = plan_looks(BRIEF, excluded, CATALOG, client, replacement=bottom)
    assert sent and result.outcome is not Outcome.INFEASIBLE


def test_a_replacement_keeping_an_owned_item_that_was_removed_still_reaches_the_model() -> None:
    owned = ItemRef(kind=ItemKind.OWNED, id="O1")
    look = Look(id="look_1", items={Slot.TOP: owned, Slot.BOTTOM: ref_of("P05")})
    rest = plan_from_looks(LOOK_1, LOOK_2, LOOK_3)
    current = Plan(looks=(look, *rest.looks[1:]), selected_offers=rest.selected_offers)
    client, sent, _ = spark(VALID, VALID)
    bottom = Replacement(look_id="look_2", slot=Slot.BOTTOM, item=ref_of("P05"))
    plan_looks(BRIEF, state(20000, plan=current), CATALOG, client, replacement=bottom)
    assert sent


def test_repeated_looks_get_one_repair() -> None:
    client, sent, _ = spark(reply(LOOK_1, LOOK_1, LOOK_2), VALID)
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert result.outcome is Outcome.SUCCESS and len(sent) == 2
    assert "look_2 repeats look_1" in sent[1]["messages"][-1]["content"]


def test_looks_that_stay_repeated_fail() -> None:
    client, _, _ = spark(reply(LOOK_1, LOOK_2, LOOK_1), reply(LOOK_2, LOOK_2, LOOK_2))
    result = plan_looks(BRIEF, state(), CATALOG, client)
    assert result.outcome is Outcome.GENERATION_FAILED
    assert {v.rule for v in result.check.violations} == {Rule.LOOK_COMPOSITION}


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


MENS_LOOKS = reply(LOOK_1, LOOK_2, {"top": "P01", "bottom": "P05"})  # P09 is a women's skirt


def test_a_catalog_variant_outside_the_fit_gets_one_repair() -> None:
    client, sent, _ = spark(VALID, MENS_LOOKS)
    result = plan_looks(BRIEF, state(fit=Fit(lines={"men"})), CATALOG, client)
    assert (result.outcome, result.usage.calls) == (Outcome.SUCCESS, 2)
    assert result.proposal_metrics.constraint_violations_by_rule == ((Rule.FIT.value, 1),)
    assert "P09 is from the women line, not men" in sent[1]["messages"][-1]["content"]


NO_MENS_BOTTOM = Fit(lines={"men"}, garment_types={"jeans"})  # P05 is the only men's bottom


def test_a_fit_that_leaves_no_complete_look_is_infeasible_without_a_model_call() -> None:
    client, sent, _ = spark()
    result = plan_looks(BRIEF, state(fit=NO_MENS_BOTTOM), CATALOG, client)
    assert (result.outcome, sent) == (Outcome.INFEASIBLE, [])
    assert [conflict.rule for conflict in result.conflicts] == [Rule.LOOK_COMPOSITION]


def test_an_owned_item_completes_a_look_that_the_fit_leaves_without_a_bottom() -> None:
    jeans = OwnedItem(id="O01", category="bottom", color="blue", description="blue jeans")
    looks = reply(
        {"top": "P01", "bottom": "O01", "footwear": "P18"},
        {"top": "P03", "bottom": "O01"},
        {"top": "P01", "bottom": "O01"},
    )
    client, sent, _ = spark(looks)
    result = plan_looks(BRIEF, state(fit=NO_MENS_BOTTOM, owned_items=(jeans,)), CATALOG, client)
    assert (result.outcome, len(sent)) == (Outcome.SUCCESS, 1)  # owned items are exempt


def test_logs_hold_usage_but_nothing_the_person_entered(caplog: pytest.LogCaptureFixture) -> None:
    coat = OwnedItem(id="O01", category="outerwear", color="camel", description="grandma's coat")
    client, _, _ = spark(VALID)
    with caplog.at_level(logging.INFO, logger="backend.app.planner"):
        plan_looks(BRIEF, state(owned_items=(coat,)), CATALOG, client)
    assert "outcome=success" in caplog.text and "prompt_tokens=1000" in caplog.text
    for entered in ("dinners", "ivory", "camel", "grandma"):
        assert entered not in caplog.text


def test_closed_footwear_keeps_open_and_unrecorded_shoes_from_the_model() -> None:
    client, sent, _ = spark(VALID)
    chosen = state(material_exclusions=frozenset(), footwear=FootwearChoice.CLOSED)
    assert plan_looks(BRIEF, chosen, CATALOG, client).outcome is Outcome.SUCCESS
    request = json.loads(sent[0]["messages"][1]["content"])
    candidates = request["candidates"]
    shoes = {c["id"]: c.get("closure") for c in candidates if c["category"] == "footwear"}
    # P17 records no closure, so it cannot pass a closed choice.
    assert shoes == {"P07": "closed", "P08": "closed", "P18": "closed"}
    assert request["footwear_required"] is True


def test_a_look_without_shoes_gets_a_repair_once_footwear_is_chosen() -> None:
    barefoot = reply(LOOK_1, LOOK_2, {"top": "P01", "bottom": "P09"})
    client, sent, _ = spark(barefoot, VALID)
    result = plan_looks(BRIEF, state(footwear=FootwearChoice.CLOSED), CATALOG, client)
    assert result.outcome is Outcome.SUCCESS and len(sent) == 2
    assert "look_3 needs closed footwear" in sent[1]["messages"][-1]["content"]


def test_a_footwear_choice_that_no_shoe_meets_is_infeasible_without_a_model_call() -> None:
    client, sent, _ = spark()
    result = plan_looks(BRIEF, state(footwear=FootwearChoice.OPEN), CATALOG, client)
    assert (result.outcome, sent) == (Outcome.INFEASIBLE, [])
    assert [conflict.rule for conflict in result.conflicts] == [Rule.FOOTWEAR]


def test_owned_shoes_meet_a_footwear_choice() -> None:
    boots = OwnedItem(id="O01", category="footwear", color="brown", description="leather boots")
    looks = [{**look, "footwear": "O01"} for look in (LOOK_1, LOOK_2, LOOK_3)]
    client, _, _ = spark(reply(*looks))
    chosen = state(footwear=FootwearChoice.OPEN, owned_items=(boots,))
    assert plan_looks(BRIEF, chosen, CATALOG, client).outcome is Outcome.SUCCESS


def test_occasion_and_season_reach_the_model_as_wishes() -> None:
    client, sent, _ = spark(VALID)
    brief = Brief(style_goal="smart casual", occasion=Occasion.OFFICE, season=Season.AUTUMN)
    plan_looks(brief, state(), CATALOG, client)
    request = json.loads(sent[0]["messages"][1]["content"])
    assert request["brief"] | {"style_goal": ""} == {
        "style_goal": "",
        "preferred_colors": [],
        "occasion": "office",
        "season": "autumn",
    }
