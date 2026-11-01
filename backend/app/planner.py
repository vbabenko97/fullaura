"""Muse Spark proposes three looks from catalog candidates; code checks every plan it proposes.

The model chooses catalog variant and owned-item IDs. Code picks each variant's offer and checks
the plan with check_plan; a plan that fails gets one repair attempt that lists its violations. A
model or provider failure is never reported as infeasible. Logs carry the model, the number of
calls, latency and token usage, never prompts, briefs or profiles.
"""

from __future__ import annotations

import asyncio
import heapq
import http.client
import json
import logging
import math
import time
from collections.abc import Callable, Iterator, Sequence
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
    budget_text,
    cheapest_usable_offers,
    check_plan,
    eur,
    lock_conflicts,
    missing_sizes_text,
)
from backend.app.meta_images import RETRY_STATUS, error_detail, post_json_async
from backend.app.state import (
    BudgetMode,
    FootwearChoice,
    ItemKind,
    ItemRef,
    LockedPlacement,
    Look,
    Plan,
    Slot,
    State,
)

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
An item fills at most one slot in a look, and it may appear in several looks. Candidates with
the same title, brand and color are sizes of one product: use only one of them across the three
looks. Keep every entry of "locks" in its look and slot. Owned items cost nothing.
Stay within the budget: with mode "per_look" each look's distinct catalog items, and with mode
"whole_plan" all distinct catalog items across the looks, must cost at most amount_eur.
Suit the brief's occasion and season when they are given.

When "replacement" is present, replace its item with a different item in the stated look and
slot, not another size of the same item. Keep every other slot exactly as it appears in
"current_plan", including slots that are empty. Do not add or remove items in other slots.

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
    conflicts = infeasibility(state, catalog, offers, replacement)
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
            planned = _replacement_violations(plan, state, replacement, catalog)
            planned += _repeated_looks(plan)
            if planned:
                check = Check(tuple(planned) + check.violations, check.notices)
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
    state: State,
    catalog: Catalog,
    offers: dict[str, Offer] | None = None,
    replacement: Replacement | None = None,
) -> list[Violation]:
    """Violations that every plan for this state would have, so no plan can pass; empty when a
    plan may exist. Needs no model call."""
    if offers is None:
        offers = cheapest_usable_offers(catalog, state)
    held = _holding_kept_positions(state, replacement)
    conflicts = lock_conflicts(held, catalog) + _base_outfit_conflicts(state, catalog, offers)
    if replacement is not None and state.plan is not None:
        # A replacement leaves empty positions empty, so a look that needs more cannot get it.
        conflicts += _look_violations(state.plan, state, catalog)
    conflicts = conflicts or _cheapest_look_conflicts(held, catalog, offers)
    if conflicts:
        return conflicts
    if replacement is not None:
        return _replacement_conflicts(state, catalog, offers, replacement)
    return [] if state.locks else _three_look_conflicts(state, catalog, offers)


def _holding_kept_positions(state: State, replacement: Replacement | None) -> State:
    """The state with every position a replacement keeps held like a locked item, because it
    stays as it is. An owned item the person has since removed cannot be held; the plan's check
    reports it."""
    if replacement is None or state.plan is None:
        return state
    owned = {item.id for item in state.owned_items}
    kept = tuple(
        LockedPlacement(look_id=look.id, slot=slot, item=item)
        for look in state.plan.looks
        for slot, item in look.items.items()
        if (look.id, slot) != (replacement.look_id, replacement.slot)
        and (item.kind is ItemKind.CATALOG or item.id in owned)
    )
    return State.model_validate({**dict(state), "locks": kept})


def _cheapest_look_conflicts(
    state: State, catalog: Catalog, offers: dict[str, Offer]
) -> list[Violation]:
    """A budget violation when even the cheapest complete look costs more than the budget. A plan
    costs at least as much as each of its looks under either budget mode, so no plan can pass
    then. A look counts its locked items and, for each position it still needs, the cheapest
    usable variant or owned item. Run after the other checks, so every locked item is usable."""
    owned = {item.category for item in state.owned_items}
    cheapest: dict[Slot, float] = {slot: math.inf for slot in Slot}
    for slot, categories in SLOT_CATEGORIES.items():
        prices = [
            offers[variant.id].price_cents or 0
            for product in catalog.products
            if product.category in categories
            for variant in product.variants
            if variant.id in offers
        ]
        cheapest[slot] = min([*prices, *([0] if owned & categories else [])], default=math.inf)

    def lowest_cost(locks: Sequence[LockedPlacement]) -> float:
        kept = sum(
            offers[lock.item.id].price_cents or 0
            for lock in locks
            if lock.item.kind is ItemKind.CATALOG and lock.item.id in offers
        )
        slots = {lock.slot for lock in locks}
        if Slot.ONE_PIECE in slots:
            base = 0.0
        elif slots & {Slot.TOP, Slot.BOTTOM}:
            base = sum(cheapest[slot] for slot in (Slot.TOP, Slot.BOTTOM) if slot not in slots)
        else:
            base = min(cheapest[Slot.TOP] + cheapest[Slot.BOTTOM], cheapest[Slot.ONE_PIECE])
        shoes_needed = state.footwear is not FootwearChoice.ANY and Slot.FOOTWEAR not in slots
        return kept + base + (cheapest[Slot.FOOTWEAR] if shoes_needed else 0)

    budget = state.budget
    free = lowest_cost(())
    if budget.amount_cents < free < math.inf:
        cents = int(free)
        detail = f"The cheapest complete look costs {eur(cents)}, over the {budget_text(budget)}"
        return [Violation(Rule.BUDGET, detail, cost_cents=cents)]
    violations = []
    for look_id in dict.fromkeys(lock.look_id for lock in state.locks):
        lowest = lowest_cost([lock for lock in state.locks if lock.look_id == look_id])
        if budget.amount_cents < lowest < math.inf:
            cents = int(lowest)
            label = look_id.replace("_", " ")
            detail = (
                f"The items kept in {label} and the cheapest items it still needs cost "
                f"{eur(cents)}, over the {budget_text(budget)}"
            )
            violations.append(Violation(Rule.BUDGET, detail, look_id=look_id, cost_cents=cents))
    return violations


@dataclass(frozen=True, slots=True)
class _Piece:
    """An item a look can use: an owned item, or a garment at its cheapest usable size. A plan
    holds one size of a garment, so the cheapest size stands for all of them."""

    id: str
    category: Category
    price: int


def _pieces(state: State, catalog: Catalog, offers: dict[str, Offer]) -> list[_Piece]:
    cheapest: dict[tuple[str, str | None], _Piece] = {}
    for product in catalog.products:
        for variant in product.variants:
            if variant.id in offers:
                piece = _Piece(variant.id, product.category, offers[variant.id].price_cents or 0)
                garment = (product.id, variant.color)
                if garment not in cheapest or piece.price < cheapest[garment].price:
                    cheapest[garment] = piece
    return [*cheapest.values(), *(_Piece(i.id, i.category, 0) for i in state.owned_items)]


def _three_look_conflicts(
    state: State, catalog: Catalog, offers: dict[str, Offer]
) -> list[Violation]:
    """A conflict when no three different looks can pass, for a request that keeps nothing. A
    refusal says no plan exists, so the search counts everything a plan may use: a shared item, a
    second top as a mid layer, and any optional position."""
    tops = SLOT_CATEGORIES[Slot.TOP]
    pieces = _pieces(state, catalog, offers)
    outfits: list[tuple[_Piece, ...]] = [
        (top, bottom)
        for top in pieces
        if top.category in tops
        for bottom in pieces
        if bottom.category is Category.BOTTOM
    ]
    outfits += [(piece,) for piece in pieces if piece.category is Category.ONE_PIECE]
    optional = (
        SLOT_CATEGORIES[Slot.MID_LAYER]
        | SLOT_CATEGORIES[Slot.OUTER_LAYER]
        | SLOT_CATEGORIES[Slot.ACCESSORY]
    )
    if state.footwear is FootwearChoice.ANY:
        optional |= SLOT_CATEGORIES[Slot.FOOTWEAR]
        bases = outfits
    else:
        shoes = [piece for piece in pieces if piece.category is Category.FOOTWEAR]
        bases = [(*outfit, shoe) for outfit in outfits for shoe in shoes]
    bases.sort(key=lambda base: sum(piece.price for piece in base))
    layers = [piece for piece in pieces if piece.category in optional]

    def look_costs() -> Iterator[int]:
        # Three looks with two or more optional items each imply three with one at most, for less.
        for base in bases:
            cost = sum(piece.price for piece in base)
            yield cost
            yield from (cost + layer.price for layer in layers if layer not in base)

    cheapest_looks = heapq.nsmallest(3, look_costs())
    if len(cheapest_looks) < 3:
        count = "one complete look fits" if len(cheapest_looks) == 1 else "two complete looks fit"
        detail = f"Only {count} your line, sizes and materials, and a plan needs three different"
        return [Violation(Rule.LOOK_COMPOSITION, f"{detail} looks")]
    budget = state.budget
    floor = (
        cheapest_looks[2]
        if budget.mode is BudgetMode.PER_LOOK
        else _cheapest_three_looks_together(bases, pieces)
    )
    if budget.amount_cents >= floor:
        return []
    detail = f"Three different looks need at least {eur(floor)}, over the {budget_text(budget)}"
    return [Violation(Rule.BUDGET, detail, cost_cents=floor)]


def _cheapest_three_looks_together(bases: list[tuple[_Piece, ...]], pieces: list[_Piece]) -> int:
    """The lowest cost of the distinct items of three different looks; bases run cheapest first.
    Every such plan holds a base and more items. One more item is enough when it can go over or
    instead of the base's top; otherwise two more make the base, the base with one and the base
    with the other."""
    best = math.inf
    tops = SLOT_CATEGORIES[Slot.TOP]
    for base in bases:
        cost = sum(piece.price for piece in base)
        if cost >= best:
            break
        has_top = any(piece.category in tops for piece in base)
        # A bottom cannot join a one-piece, and any other item can go in or replace a position.
        extras = sorted(
            (piece for piece in pieces if piece not in base),
            key=lambda piece: piece.price,
        )
        usable = [p.price for p in extras if has_top or p.category is not Category.BOTTOM]
        if len(usable) >= 2:
            best = min(best, cost + usable[0] + usable[1])
        if has_top:
            enough = (p.price for p in extras if p.category in {*tops, Category.ONE_PIECE})
            best = min(best, cost + next(enough, math.inf))
    return int(best)


def _replacement_conflicts(
    state: State, catalog: Catalog, offers: dict[str, Offer], replacement: Replacement
) -> list[Violation]:
    """A conflict when no other item can take the replaced position with every other position as
    it is: another size of the item is not another item, and a plan holds one size of a garment."""
    plan = state.plan
    look = (
        next((look for look in plan.looks if look.id == replacement.look_id), None)
        if plan
        else None
    )
    if plan is None or look is None:
        return []

    def price(item: ItemRef) -> int:
        offer = offers.get(item.id) if item.kind is ItemKind.CATALOG else None
        return (offer.price_cents or 0) if offer else 0

    in_look = {item for slot, item in look.items.items() if slot is not replacement.slot}
    kept = {
        item
        for other in plan.looks
        for slot, item in other.items.items()
        if (other.id, slot) != (replacement.look_id, replacement.slot)
    }
    per_look = state.budget.mode is BudgetMode.PER_LOOK
    room = state.budget.amount_cents - sum(price(item) for item in (in_look if per_look else kept))
    categories = SLOT_CATEGORIES[replacement.slot]
    if room >= 0 and any(
        owned.category in categories
        and (ref := ItemRef(kind=ItemKind.OWNED, id=owned.id)) != replacement.item
        and ref not in in_look
        for owned in state.owned_items
    ):
        return []
    worn = {_garment(item, catalog): item.id for item in kept}
    replaced = _garment(replacement.item, catalog)
    for product in catalog.products:
        if product.category not in categories:
            continue
        for variant in product.variants:
            ref = ItemRef(kind=ItemKind.CATALOG, id=variant.id)
            garment = (product.id, variant.color)
            if (
                variant.id in offers
                and ref not in in_look
                and garment != replaced
                and worn.get(garment, variant.id) == variant.id
                and (0 if not per_look and ref in kept else price(ref)) <= room
            ):
                return []
    detail = "No other item can fill this position within your line, sizes, materials and budget"
    return [Violation(Rule.REPLACEMENT, detail, replacement.item, replacement.look_id)]


def _garment(item: ItemRef, catalog: Catalog) -> tuple[str, str | None] | None:
    """The product and color of a catalog item, which a plan holds in one size; None otherwise."""
    variant, product = catalog.variant(item.id), catalog.product_of(item.id)
    if item.kind is ItemKind.OWNED or variant is None or product is None:
        return None
    return product.id, variant.color


def _repeated_looks(plan: Plan) -> list[Violation]:
    """A look with the same items in the same slots as an earlier look repeats it. Design My Look
    checks its one look as three copies, so check_complete_plan cannot hold this rule."""
    first_with: dict[frozenset[tuple[Slot, ItemRef]], str] = {}
    violations = []
    for look in plan.looks:
        first = first_with.setdefault(frozenset(look.items.items()), look.id)
        if first != look.id:
            detail = f"{look.id} repeats {first}; make every look different"
            violations.append(Violation(Rule.LOOK_COMPOSITION, detail, look_id=look.id))
    return violations


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


SLOT_CATEGORIES: dict[Slot, frozenset[Category]] = {
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
            if category is not None and category not in SLOT_CATEGORIES[slot]:
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
    plan: Plan, state: State, replacement: Replacement | None, catalog: Catalog
) -> list[Violation]:
    """Require a replacement to change only the selected, occupied position, to a different
    item."""
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
                elif (garment := _garment(after, catalog)) and garment == _garment(before, catalog):
                    violations.append(
                        Violation(
                            Rule.REPLACEMENT,
                            f"Replace {look_id} {slot.value} with a different item, not another "
                            "size of it",
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
        categories & SLOT_CATEGORIES[Slot.TOP] and Category.BOTTOM in categories
    ):
        worn = {*SLOT_CATEGORIES[Slot.TOP], Category.BOTTOM, Category.ONE_PIECE}
        detail = (
            "No top and bottom or dress fits your line, sizes and materials, and your own items "
            f"do not complete a look. {missing_sizes_text(state, catalog, worn)}"
        )
        conflicts.append(Violation(Rule.LOOK_COMPOSITION, detail.strip()))
    if state.footwear is not FootwearChoice.ANY and Category.FOOTWEAR not in categories:
        detail = (
            f"No {state.footwear} shoes fit your line, sizes and materials, and you listed no "
            f"shoes of your own. {missing_sizes_text(state, catalog, {Category.FOOTWEAR})}"
        )
        conflicts.append(Violation(Rule.FOOTWEAR, detail.strip()))
    return conflicts


def _request(
    brief: Brief,
    state: State,
    catalog: Catalog,
    offers: dict[str, Offer],
    replacement: Replacement | None = None,
) -> dict[str, Any]:
    replaced = _garment(replacement.item, catalog) if replacement else None
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
        if variant.id in offers and (product.id, variant.color) != replaced
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
