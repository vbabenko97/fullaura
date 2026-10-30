"""Muse Spark proposes three looks from catalog candidates; code checks every plan it proposes.

The model chooses catalog variant and owned-item IDs. Code picks each variant's offer and checks
the plan with check_plan; a plan that fails gets one repair attempt that lists its violations. A
model or provider failure is never reported as infeasible. Logs carry the model, the number of
calls, latency and token usage, never prompts, briefs or profiles.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Annotated, Any, Self
from urllib.error import HTTPError
from urllib.request import Request

from pydantic import Field, model_validator

from backend.app.catalog import Catalog, Category, Offer, Record
from backend.app.constraints import (
    Check,
    Rule,
    Violation,
    cheapest_usable_offers,
    check_plan,
    lock_conflicts,
)
from backend.app.meta_images import RETRY_STATUS, error_detail, post_json_async
from backend.app.state import FootwearChoice, ItemKind, ItemRef, Look, Plan, Slot, State

BASETEN_URL = "https://inference.baseten.co/v1"  # Baseten Model APIs docs, checked 2026-10-27
SPARK_MODEL = "meta-models/muse-spark-open-1026"
DEADLINE_S = 150.0  # for the whole request, across every call
GENERATION_ATTEMPTS = 2  # the first plan and one repair
HTTP_ATTEMPTS_PER_CALL = 2
# Keep reasoning bounded so the model can finish its JSON plan within the token cap.
MAX_TOKENS = 6000
REASONING_EFFORT = "medium"

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    SUCCESS = "success"
    # The locks alone break a hard constraint, or no usable items complete a look, so no plan
    # can pass
    INFEASIBLE = "infeasible"
    GENERATION_FAILED = "generation_failed"  # the model's plans failed the checks or the format
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"


class Occasion(StrEnum):
    OFFICE = "office"
    CASUAL = "casual"
    EVENING = "evening"
    TRAVEL = "travel"
    SPECIAL_EVENT = "special_event"


class Season(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class Brief(Record):
    """What the person wants from the looks, in their own words. Occasion and season are wishes
    for the model; code checks neither."""

    style_goal: Annotated[str, Field(min_length=1, max_length=300)]
    preferred_colors: tuple[Annotated[str, Field(min_length=1, max_length=30)], ...] = Field(
        default=(), max_length=5
    )
    occasion: Occasion | None = None
    season: Season | None = None


class Replacement(Record):
    """The current item and position a person asked the planner to replace."""

    look_id: str
    slot: Slot
    item: ItemRef


class PlanRequest(Record):
    brief: Brief
    state: State
    replacement: Replacement | None = None

    @model_validator(mode="after")
    def _replacement_refers_to_an_unlocked_current_item(self) -> Self:
        if self.replacement is None:
            return self
        if self.state.plan is None:
            raise ValueError("A replacement request needs the current plan")
        look = next(
            (look for look in self.state.plan.looks if look.id == self.replacement.look_id),
            None,
        )
        if look is None or look.items.get(self.replacement.slot) != self.replacement.item:
            raise ValueError("The replacement target does not match the current plan")
        if any(
            lock.look_id == self.replacement.look_id and lock.slot == self.replacement.slot
            for lock in self.state.locks
        ):
            raise ValueError("A locked item cannot be replaced")
        return self


@dataclass(frozen=True, slots=True)
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    http_attempts: int = 0
    unmetered_calls: int = 0  # completed calls whose reply reported no token usage

    def add(
        self,
        usage: dict[str, Any],
        http_attempts: int,
        *,
        call_completed: bool = True,
    ) -> Usage:
        return Usage(
            calls=self.calls + int(call_completed),
            prompt_tokens=self.prompt_tokens + int(usage.get("prompt_tokens") or 0),
            completion_tokens=self.completion_tokens + int(usage.get("completion_tokens") or 0),
            http_attempts=self.http_attempts + http_attempts,
            unmetered_calls=self.unmetered_calls
            + int(call_completed and not usage.get("completion_tokens")),
        )


@dataclass(frozen=True, slots=True)
class ProposalMetrics:
    completed: int = 0
    format_failures: int = 0
    constraint_failures: int = 0
    constraint_violations: int = 0
    constraint_violations_by_rule: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class PlanResult:
    outcome: Outcome
    usage: Usage
    plan: Plan | None = None
    check: Check | None = None  # the last plan's check, also when generation failed
    conflicts: tuple[Violation, ...] = ()  # why the plan is infeasible
    error: str | None = None
    proposal_metrics: ProposalMetrics = field(default_factory=ProposalMetrics)


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        timed_out: bool = False,
        generation_failed: bool = False,
        http_attempts: int = 0,
        usage: dict[str, Any] | None = None,
        call_completed: bool = False,
    ) -> None:
        super().__init__(message)
        self.timed_out = timed_out
        self.generation_failed = generation_failed
        self.http_attempts = http_attempts
        self.usage = usage or {}
        self.call_completed = call_completed


def _post_json(request: Request, timeout_s: float) -> dict[str, Any]:
    return asyncio.run(post_json_async(request, timeout_s))


@dataclass(slots=True)
class SparkClient:
    """Chat completions from Muse Spark on Baseten, with a timeout and bounded retries."""

    api_key: str = field(repr=False)
    model: str = SPARK_MODEL
    base_url: str = BASETEN_URL
    # Each request is cancelled after this much total wall time or the plan's remaining budget.
    timeout_s: float = 60.0
    max_attempts: int = HTTP_ATTEMPTS_PER_CALL
    post: Callable[[Request, float], dict[str, Any]] = _post_json
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("BASETEN_API_KEY is not set")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        deadline: float,
        clock: Callable[[], float],
    ) -> tuple[str, dict[str, Any], int]:
        """The reply text and its token usage. Raises ProviderError."""
        body = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": MAX_TOKENS,
            "reasoning_effort": REASONING_EFFORT,
        }
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        http_attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            remaining_s = deadline - clock()
            if remaining_s <= 0:
                raise ProviderError(
                    "Planning took too long", timed_out=True, http_attempts=http_attempts
                )
            try:
                http_attempts += 1
                payload = self.post(request, min(self.timeout_s, remaining_s))
            except HTTPError as error:
                failure = f"HTTP {error.code}{error_detail(error, self.api_key)}"
                timed_out = error.code in (408, 504)
                if error.code not in RETRY_STATUS:
                    raise ProviderError(failure, http_attempts=http_attempts) from None
            except (OSError, http.client.HTTPException) as error:
                reason = getattr(error, "reason", error)
                failure = f"{type(error).__name__}: {reason}"
                timed_out = isinstance(error, TimeoutError) or isinstance(reason, TimeoutError)
            except ValueError:
                raise ProviderError(
                    "The response was not valid JSON", http_attempts=http_attempts
                ) from None
            else:
                provider_usage = payload.get("usage") or {} if isinstance(payload, dict) else {}
                if clock() >= deadline:
                    raise ProviderError(
                        "Planning took too long",
                        timed_out=True,
                        http_attempts=http_attempts,
                        usage=provider_usage,
                        call_completed=True,
                    )
                try:
                    choice = payload["choices"][0]
                    reply = str(choice["message"].get("content") or "")
                    finish_reason = choice.get("finish_reason")
                except (LookupError, AttributeError, TypeError):
                    raise ProviderError(
                        "The response was not a chat completion",
                        http_attempts=http_attempts,
                        usage=provider_usage,
                        call_completed=True,
                    ) from None
                if finish_reason != "stop" or not reply.strip():
                    raise ProviderError(
                        "The model reply did not finish normally "
                        f"(finish_reason={finish_reason!r})",
                        generation_failed=True,
                        http_attempts=http_attempts,
                        usage=provider_usage,
                        call_completed=True,
                    )
                return reply, provider_usage, http_attempts
            if attempt < self.max_attempts:
                remaining_s = deadline - clock()
                if remaining_s <= 0:
                    raise ProviderError(
                        "Planning took too long", timed_out=True, http_attempts=http_attempts
                    )
                self.sleep(min(2**attempt, 10, remaining_s))
                if clock() >= deadline:
                    raise ProviderError(
                        "Planning took too long", timed_out=True, http_attempts=http_attempts
                    )
        raise ProviderError(
            f"Gave up after {self.max_attempts} attempts: {failure}",
            timed_out=timed_out,
            http_attempts=http_attempts,
        )


SYSTEM_PROMPT = f"""You are FullAura's stylist. Propose exactly three different looks.

Use only item IDs from "candidates" and "owned_items". Each look maps slots to item IDs; the
slots are {", ".join(slot.value for slot in Slot)}. A look has a top and a bottom, or a
one_piece, and usually footwear; when "footwear_required" is true, every look needs footwear.
An item fills at most one slot in a look, and it may appear in several looks. Keep every
entry of "locks" in its look and slot. Owned items cost nothing.
Stay within the budget: with mode "per_look" each look's distinct catalog items, and with mode
"whole_plan" all distinct catalog items across the looks, must cost at most amount_eur.
Suit the brief's occasion and season when they are given.

When "replacement" is present, replace its item with a different item in the stated look and
slot. Keep every other slot exactly as it appears in "current_plan", including slots that are
empty. Do not add or remove items in other slots.

Reply with JSON only, in this form:
{{"looks": [{{"id": "look_1", "items": {{"top": "ID", "bottom": "ID", "footwear": "ID"}}}},
{{"id": "look_2", "items": {{...}}}}, {{"id": "look_3", "items": {{...}}}}]}}"""


def plan_looks(
    brief: Brief,
    state: State,
    catalog: Catalog,
    client: SparkClient,
    clock: Callable[[], float] = time.monotonic,
    replacement: Replacement | None = None,
) -> PlanResult:
    """Three checked looks for the brief, or the reason there are none."""
    usage = Usage()
    offers = cheapest_usable_offers(catalog, state)
    conflicts = infeasibility(state, catalog, offers)
    if conflicts:
        return _logged(PlanResult(Outcome.INFEASIBLE, usage, conflicts=tuple(conflicts)), client, 0)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(_request(brief, state, catalog, offers, replacement)),
        },
    ]
    started = clock()
    deadline = started + DEADLINE_S
    result = PlanResult(Outcome.GENERATION_FAILED, usage, error="No plan was generated")
    completed = format_failures = constraint_failures = constraint_violations = 0
    violations_by_rule: dict[str, int] = {}
    for _ in range(GENERATION_ATTEMPTS):
        if clock() >= deadline:
            result = PlanResult(Outcome.PROVIDER_TIMEOUT, usage, error="Planning took too long")
            break
        try:
            reply, call_usage, http_attempts = client.complete(
                messages, deadline=deadline, clock=clock
            )
        except ProviderError as error:
            usage = usage.add(
                error.usage,
                error.http_attempts,
                call_completed=error.call_completed,
            )
            if error.timed_out or clock() >= deadline:
                outcome = Outcome.PROVIDER_TIMEOUT
            elif error.generation_failed:
                outcome = Outcome.GENERATION_FAILED
            else:
                outcome = Outcome.PROVIDER_ERROR
            result = PlanResult(outcome, usage, error=str(error))
            break
        usage = usage.add(call_usage, http_attempts)
        completed += 1
        try:
            plan = _plan_from_reply(reply, state, catalog, offers)
        except ValueError as error:
            format_failures += 1
            result = PlanResult(Outcome.GENERATION_FAILED, usage, error=str(error))
            problems = str(error)
        else:
            plan = _preserve_replacement_positions(plan, state, replacement, catalog, offers)
            check = check_complete_plan(plan, state, catalog)
            replaced = _replacement_violations(plan, state, replacement)
            if replaced:
                check = Check(tuple(replaced) + check.violations, check.notices)
            if check.passed:
                result = PlanResult(Outcome.SUCCESS, usage, plan=plan, check=check)
            else:
                constraint_failures += 1
                constraint_violations += len(check.violations)
                for violation in check.violations:
                    rule = violation.rule.value
                    violations_by_rule[rule] = violations_by_rule.get(rule, 0) + 1
                result = PlanResult(Outcome.GENERATION_FAILED, usage, check=check)
                problems = "; ".join(violation.detail for violation in check.violations)
        if clock() >= deadline:
            result = PlanResult(Outcome.PROVIDER_TIMEOUT, usage, error="Planning took too long")
            break
        if result.outcome is Outcome.SUCCESS:
            break
        messages += [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": f"That plan failed: {problems}. Reply with a corrected "
                "plan, as JSON only.",
            },
        ]
    result = replace(
        result,
        proposal_metrics=ProposalMetrics(
            completed=completed,
            format_failures=format_failures,
            constraint_failures=constraint_failures,
            constraint_violations=constraint_violations,
            constraint_violations_by_rule=tuple(sorted(violations_by_rule.items())),
        ),
    )
    return _logged(result, client, clock() - started)


def infeasibility(
    state: State, catalog: Catalog, offers: dict[str, Offer] | None = None
) -> list[Violation]:
    """Violations that every plan for this state would have, so no plan can pass; empty when a
    plan may exist. Needs no model call."""
    if offers is None:
        offers = cheapest_usable_offers(catalog, state)
    return lock_conflicts(state, catalog) + _base_outfit_conflicts(state, catalog, offers)


def check_complete_plan(plan: Plan, state: State, catalog: Catalog) -> Check:
    """Everything a plan must pass before it is shown: the look structure and the hard
    constraints of docs/SPEC.md, against the state's budget, exclusions, fit and locks."""
    check = check_plan(State.model_validate({**dict(state), "plan": plan}), catalog)
    structural = _look_violations(plan, state, catalog)
    return Check(tuple(structural) + check.violations, check.notices) if structural else check


def with_cheapest_offers(plan: Plan, state: State, catalog: Catalog) -> Plan:
    """The same looks, each catalog variant bought through its cheapest offer that passes the
    checks now, as the planner chooses offers."""
    offers = cheapest_usable_offers(catalog, state)
    used = {
        item.id
        for look in plan.looks
        for item in look.items.values()
        if item.kind is ItemKind.CATALOG
    }
    return Plan(
        looks=plan.looks,
        selected_offers={variant_id: _offer_id(variant_id, catalog, offers) for variant_id in used},
    )


def _preserve_replacement_positions(
    proposed: Plan,
    state: State,
    replacement: Replacement | None,
    catalog: Catalog,
    offers: dict[str, Offer],
) -> Plan:
    """Apply the model's replacement choice to the current plan's exact positions."""
    current = state.plan
    if replacement is None or current is None:
        return proposed

    current_looks = {look.id: look for look in current.looks}
    proposed_looks = {look.id: look for look in proposed.looks}
    target_look = proposed_looks.get(replacement.look_id)
    if current_looks.keys() != proposed_looks.keys() or target_look is None:
        return proposed
    replacement_item = target_look.items.get(replacement.slot)
    if replacement_item is None or replacement_item == replacement.item:
        return proposed
    current_target_look = current_looks[replacement.look_id]
    if any(
        slot is not replacement.slot and item == replacement_item
        for slot, item in current_target_look.items.items()
    ):
        return proposed

    looks = []
    for look in current.looks:
        items = dict(look.items)
        if look.id == replacement.look_id:
            items[replacement.slot] = replacement_item
        looks.append(Look(id=look.id, items=items))

    catalog_items = {
        item.id for look in looks for item in look.items.values() if item.kind is ItemKind.CATALOG
    }
    selected_offers = {vid: _offer_id(vid, catalog, offers) for vid in catalog_items}
    return Plan(looks=tuple(looks), selected_offers=selected_offers)


def _offer_id(variant_id: str, catalog: Catalog, offers: dict[str, Offer]) -> str:
    """The variant's cheapest usable offer, chosen by code."""
    if variant_id in offers:
        return offers[variant_id].id
    # A variant that cannot pass keeps an offer of its own, so check_plan reports why.
    variant = catalog.variant(variant_id)
    return variant.offers[0].id if variant else "none"


_SLOT_CATEGORIES: dict[Slot, frozenset[Category]] = {
    Slot.TOP: frozenset({Category.TOP, Category.KNITWEAR}),
    Slot.BOTTOM: frozenset({Category.BOTTOM}),
    Slot.ONE_PIECE: frozenset({Category.ONE_PIECE}),
    Slot.MID_LAYER: frozenset({Category.TOP, Category.KNITWEAR}),
    Slot.OUTER_LAYER: frozenset({Category.OUTERWEAR}),
    Slot.FOOTWEAR: frozenset({Category.FOOTWEAR}),
    Slot.ACCESSORY: frozenset({Category.ACCESSORY}),
}


def _look_violations(plan: Plan, state: State, catalog: Catalog) -> list[Violation]:
    """Whether every look has a complete base outfit and categories match their slots."""
    categories = {
        variant.id: product.category for product in catalog.products for variant in product.variants
    }
    owned = {item.id: item for item in state.owned_items}
    violations = []
    for look in plan.looks:
        if state.footwear is not FootwearChoice.ANY and Slot.FOOTWEAR not in look.items:
            violations.append(
                Violation(
                    Rule.LOOK_COMPOSITION,
                    f"{look.id} needs {state.footwear} footwear",
                    look_id=look.id,
                )
            )
        has_top = Slot.TOP in look.items
        has_bottom = Slot.BOTTOM in look.items
        has_one_piece = Slot.ONE_PIECE in look.items
        separates = has_top and has_bottom and not has_one_piece
        one_piece = has_one_piece and not has_top and not has_bottom
        if not (separates or one_piece):
            violations.append(
                Violation(
                    Rule.LOOK_COMPOSITION,
                    f"{look.id} needs either a top and bottom or a one_piece, but not both",
                    look_id=look.id,
                )
            )
        for slot, item in look.items.items():
            if item.kind is ItemKind.CATALOG:
                category = categories.get(item.id)
            else:
                owned_item = owned.get(item.id)
                category = owned_item.category if owned_item else None
            if category is not None and category not in _SLOT_CATEGORIES[slot]:
                violations.append(
                    Violation(
                        Rule.SLOT_CATEGORY,
                        f"{item.id} is a {category.value} item and cannot fill "
                        f"the {slot.value} slot",
                        item,
                        look.id,
                    )
                )
    return violations


def _replacement_violations(
    plan: Plan, state: State, replacement: Replacement | None
) -> list[Violation]:
    """Require a replacement to change only the selected, occupied position."""
    if replacement is None:
        return []
    current = state.plan
    if current is None:
        return [Violation(Rule.REPLACEMENT, "A replacement needs the current plan")]

    current_looks = {look.id: look for look in current.looks}
    next_looks = {look.id: look for look in plan.looks}
    violations = []
    if current_looks.keys() != next_looks.keys():
        violations.append(Violation(Rule.REPLACEMENT, "A replacement must keep all three looks"))

    for look_id, current_look in current_looks.items():
        next_look = next_looks.get(look_id)
        if next_look is None:
            continue
        for slot in Slot:
            before = current_look.items.get(slot)
            after = next_look.items.get(slot)
            if look_id == replacement.look_id and slot is replacement.slot:
                if before != replacement.item or after is None or after == before:
                    violations.append(
                        Violation(
                            Rule.REPLACEMENT,
                            f"Replace {look_id} {slot.value} with a different item",
                            replacement.item,
                            look_id,
                        )
                    )
            elif before != after:
                violations.append(
                    Violation(
                        Rule.REPLACEMENT,
                        f"A replacement changed the unrelated {look_id} {slot.value} position",
                        before,
                        look_id,
                    )
                )
    return violations


def _base_outfit_conflicts(
    state: State, catalog: Catalog, offers: dict[str, Offer]
) -> list[Violation]:
    """Violations when no usable catalog variant or owned item completes a top and bottom or a
    one-piece, or provides the footwear every look needs, so no plan can pass. Budget and locks
    are left to the other checks."""
    categories = {item.category for item in state.owned_items} | {
        product.category
        for product in catalog.products
        for variant in product.variants
        if variant.id in offers
    }
    conflicts = []
    if Category.ONE_PIECE not in categories and not (
        categories & _SLOT_CATEGORIES[Slot.TOP] and Category.BOTTOM in categories
    ):
        conflicts.append(
            Violation(
                Rule.LOOK_COMPOSITION,
                "No catalog variant that passes the checks, and no owned item, completes a top "
                "and bottom or a one_piece",
            )
        )
    if state.footwear is not FootwearChoice.ANY and Category.FOOTWEAR not in categories:
        conflicts.append(
            Violation(
                Rule.FOOTWEAR,
                f"No {state.footwear} catalog footwear passes the checks, and no owned item is "
                "footwear",
            )
        )
    return conflicts


def _request(
    brief: Brief,
    state: State,
    catalog: Catalog,
    offers: dict[str, Offer],
    replacement: Replacement | None = None,
) -> dict[str, Any]:
    candidates = [
        {
            "id": variant.id,
            "title": product.title,
            "brand": product.brand,
            "category": product.category.value,
            "color": variant.color,
            "price_eur": (offers[variant.id].price_cents or 0) / 100,
            **({"closure": product.footwear_closure.value} if product.footwear_closure else {}),
        }
        for product in catalog.products
        for variant in product.variants
        if variant.id in offers
    ]
    return {
        "brief": brief.model_dump(mode="json"),
        "budget": {
            "amount_eur": state.budget.amount_cents / 100,
            "mode": state.budget.mode.value,
        },
        "footwear_required": state.footwear is not FootwearChoice.ANY,
        "owned_items": [item.model_dump(mode="json") for item in state.owned_items],
        "replacement": (replacement.model_dump(mode="json") if replacement is not None else None),
        "current_plan": (
            [
                {
                    "id": look.id,
                    "items": {
                        slot.value: item.model_dump(mode="json")
                        for slot, item in look.items.items()
                    },
                }
                for look in state.plan.looks
            ]
            if replacement is not None and state.plan is not None
            else None
        ),
        "locks": [
            {"look_id": lock.look_id, "slot": lock.slot.value, "item_id": lock.item.id}
            for lock in state.locks
        ],
        "candidates": candidates,
    }


def _plan_from_reply(reply: str, state: State, catalog: Catalog, offers: dict[str, Offer]) -> Plan:
    """The reply's looks as a plan, with each catalog variant's offer chosen by code."""
    start, end = reply.find("{"), reply.rfind("}")
    try:
        looks = json.loads(reply[start : end + 1])["looks"]
        owned = {item.id for item in state.owned_items}
        plan_looks = [
            Look(
                id=look["id"],
                items={
                    slot: ItemRef(
                        kind=ItemKind.OWNED if item_id in owned else ItemKind.CATALOG, id=item_id
                    )
                    for slot, item_id in look["items"].items()
                },
            )
            for look in looks
        ]
        used = {
            ref.id
            for look in plan_looks
            for ref in look.items.values()
            if ref.kind is ItemKind.CATALOG
        }
        return Plan(
            looks=plan_looks, selected_offers={vid: _offer_id(vid, catalog, offers) for vid in used}
        )
    except (ValueError, LookupError, TypeError, AttributeError) as error:
        # ValidationError is a ValueError: two looks, an item in two slots, a bad slot name.
        raise ValueError(f"The reply is not a plan in the required JSON form ({error})") from None


def result_json(result: PlanResult) -> dict[str, Any]:
    """The result in a form the API returns."""

    def violation(item: Violation) -> dict[str, Any]:
        return {
            "rule": item.rule.value,
            "detail": item.detail,
            "item": item.item.model_dump(mode="json") if item.item else None,
            "look_id": item.look_id,
            "cost_cents": item.cost_cents,
        }

    check = result.check
    return {
        "outcome": result.outcome.value,
        "plan": (
            result.plan.model_dump(mode="json")
            if result.outcome is Outcome.SUCCESS and result.plan
            else None
        ),
        "violations": [violation(v) for v in (check.violations if check else ())],
        "notices": [
            {"kind": n.kind.value, "item": n.item.model_dump(mode="json"), "material": n.material}
            for n in (check.notices if check else ())
        ],
        "conflicts": [violation(v) for v in result.conflicts],
        "error": result.error,
        "usage": {
            "calls": result.usage.calls,
            "http_attempts": result.usage.http_attempts,
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
        },
    }


def _logged(result: PlanResult, client: SparkClient, latency_s: float) -> PlanResult:
    logger.info(
        "plan outcome=%s model=%s calls=%d http_attempts=%d prompt_tokens=%d "
        "completion_tokens=%d proposals=%d format_failures=%d constraint_failures=%d "
        "constraint_violations=%d latency_s=%.1f",
        result.outcome,
        client.model,
        result.usage.calls,
        result.usage.http_attempts,
        result.usage.prompt_tokens,
        result.usage.completion_tokens,
        result.proposal_metrics.completed,
        result.proposal_metrics.format_failures,
        result.proposal_metrics.constraint_failures,
        result.proposal_metrics.constraint_violations,
        latency_s,
    )
    return result
