"""Muse Spark proposes three looks from catalog candidates; code checks every plan it proposes.

The model chooses catalog variant and owned-item IDs. Code picks each variant's offer and checks
the plan with check_plan; a plan that fails gets one repair attempt that lists its violations. A
model or provider failure is never reported as infeasible. Logs carry the model, the number of
calls, latency and token usage, never prompts, briefs or profiles.
"""

from __future__ import annotations

import http.client
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import Field

from backend.app.catalog import Catalog, Offer, Record
from backend.app.constraints import Check, Violation, check_plan, item_violations, lock_conflicts
from backend.app.meta_images import RETRY_STATUS, _error_detail
from backend.app.state import ItemKind, ItemRef, Look, Plan, Slot, State

BASETEN_URL = "https://inference.baseten.co/v1"  # Baseten Model APIs docs, checked 2026-10-27
SPARK_MODEL = "meta-models/muse-spark-open-1026"
DEADLINE_S = 150.0  # for the whole request, across every call
GENERATION_ATTEMPTS = 2  # the first plan and one repair
# The model reasons before it replies, and its reasoning counts against max_tokens.
MAX_TOKENS = 6000

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    SUCCESS = "success"
    INFEASIBLE = "infeasible"  # the locks alone break a hard constraint, so no plan can pass
    GENERATION_FAILED = "generation_failed"  # the model's plans failed the checks or the format
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"


class Brief(Record):
    """What the person wants from the looks, in their own words."""

    style_goal: Annotated[str, Field(min_length=1, max_length=300)]
    preferred_colors: tuple[Annotated[str, Field(min_length=1, max_length=30)], ...] = Field(
        default=(), max_length=5
    )


class PlanRequest(Record):
    brief: Brief
    state: State


@dataclass(frozen=True, slots=True)
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, usage: dict[str, Any]) -> Usage:
        return Usage(
            self.calls + 1,
            self.prompt_tokens + int(usage.get("prompt_tokens") or 0),
            self.completion_tokens + int(usage.get("completion_tokens") or 0),
        )


@dataclass(frozen=True, slots=True)
class PlanResult:
    outcome: Outcome
    usage: Usage
    plan: Plan | None = None
    check: Check | None = None  # the last plan's check, also when generation failed
    conflicts: tuple[Violation, ...] = ()  # why the plan is infeasible
    error: str | None = None


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def _post_json(request: Request, timeout_s: float) -> dict[str, Any]:
    with urlopen(request, timeout=timeout_s) as response:
        return json.load(response)


@dataclass(slots=True)
class SparkClient:
    """Chat completions from Muse Spark on Baseten, with a timeout and bounded retries."""

    api_key: str = field(repr=False)
    model: str = SPARK_MODEL
    base_url: str = BASETEN_URL
    # urllib applies this to each network operation (connect, send, each read).
    timeout_s: float = 60.0
    max_attempts: int = 2
    post: Callable[[Request, float], dict[str, Any]] = _post_json
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("BASETEN_API_KEY is not set")

    def complete(self, messages: Sequence[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        """The reply text and its token usage. Raises ProviderError."""
        body = {"model": self.model, "messages": list(messages), "max_tokens": MAX_TOKENS}
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(1, self.max_attempts + 1):
            try:
                payload = self.post(request, self.timeout_s)
            except HTTPError as error:
                failure = f"HTTP {error.code}{_error_detail(error, self.api_key)}"
                timed_out = error.code in (408, 504)
                if error.code not in RETRY_STATUS:
                    raise ProviderError(failure) from None
            except (OSError, http.client.HTTPException) as error:
                reason = getattr(error, "reason", error)
                failure = f"{type(error).__name__}: {reason}"
                timed_out = isinstance(error, TimeoutError) or isinstance(reason, TimeoutError)
            except ValueError:
                raise ProviderError("The response was not valid JSON") from None
            else:
                try:
                    reply = str(payload["choices"][0]["message"].get("content") or "")
                except (LookupError, AttributeError, TypeError):
                    raise ProviderError("The response was not a chat completion") from None
                return reply, payload.get("usage") or {}
            if attempt < self.max_attempts:
                self.sleep(min(2**attempt, 10))
        raise ProviderError(
            f"Gave up after {self.max_attempts} attempts: {failure}", timed_out=timed_out
        )


SYSTEM_PROMPT = f"""You are FullAura's stylist. Propose exactly three different looks.

Use only item IDs from "candidates" and "owned_items". Each look maps slots to item IDs; the
slots are {", ".join(slot.value for slot in Slot)}. A look has a top and a bottom, or a
one_piece, and usually footwear. An item fills at most one slot in a look, and it may appear
in several looks. Keep every entry of "locks" in its look and slot. Owned items cost nothing.
Stay within the budget: with mode "per_look" each look's distinct catalog items, and with mode
"whole_plan" all distinct catalog items across the looks, must cost at most amount_eur.

Reply with JSON only, in this form:
{{"looks": [{{"id": "look_1", "items": {{"top": "ID", "bottom": "ID", "footwear": "ID"}}}},
{{"id": "look_2", "items": {{...}}}}, {{"id": "look_3", "items": {{...}}}}]}}"""


def plan_looks(
    brief: Brief,
    state: State,
    catalog: Catalog,
    client: SparkClient,
    clock: Callable[[], float] = time.monotonic,
) -> PlanResult:
    """Three checked looks for the brief, or the reason there are none."""
    usage = Usage()
    conflicts = lock_conflicts(state, catalog)
    if conflicts:
        return _logged(PlanResult(Outcome.INFEASIBLE, usage, conflicts=tuple(conflicts)), client, 0)

    offers = _cheapest_usable_offers(catalog, state.material_exclusions)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(_request(brief, state, catalog, offers))},
    ]
    started = clock()
    result = PlanResult(Outcome.GENERATION_FAILED, usage, error="No plan was generated")
    for _ in range(GENERATION_ATTEMPTS):
        if clock() - started > DEADLINE_S:
            result = PlanResult(Outcome.PROVIDER_TIMEOUT, usage, error="Planning took too long")
            break
        try:
            reply, call_usage = client.complete(messages)
        except ProviderError as error:
            outcome = Outcome.PROVIDER_TIMEOUT if error.timed_out else Outcome.PROVIDER_ERROR
            result = PlanResult(outcome, usage, error=str(error))
            break
        usage = usage.add(call_usage)
        try:
            plan = _plan_from_reply(reply, state, catalog, offers)
        except ValueError as error:
            result = PlanResult(Outcome.GENERATION_FAILED, usage, error=str(error))
            problems = str(error)
        else:
            check = check_plan(State.model_validate({**dict(state), "plan": plan}), catalog)
            if check.passed:
                result = PlanResult(Outcome.SUCCESS, usage, plan=plan, check=check)
                break
            result = PlanResult(Outcome.GENERATION_FAILED, usage, plan=plan, check=check)
            problems = "; ".join(violation.detail for violation in check.violations)
        messages += [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": f"That plan failed: {problems}. Reply with a corrected "
                "plan, as JSON only.",
            },
        ]
    return _logged(result, client, clock() - started)


def _cheapest_usable_offers(catalog: Catalog, exclusions: frozenset[str]) -> dict[str, Offer]:
    """For each variant that can appear in a plan, its cheapest offer that passes the checks."""
    usable = {}
    for product in catalog.products:
        for variant in product.variants:
            offers = [o for o in variant.offers if not item_violations(variant, o, exclusions)]
            if offers:
                usable[variant.id] = min(offers, key=lambda offer: offer.price_cents or 0)
    return usable


def _request(
    brief: Brief, state: State, catalog: Catalog, offers: dict[str, Offer]
) -> dict[str, Any]:
    candidates = [
        {
            "id": variant.id,
            "title": product.title,
            "brand": product.brand,
            "category": product.category.value,
            "color": variant.color,
            "price_eur": (offers[variant.id].price_cents or 0) / 100,
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
        "owned_items": [item.model_dump(mode="json") for item in state.owned_items],
        "locks": [
            {"look_id": lock.look_id, "slot": lock.slot.value, "item_id": lock.item.id}
            for lock in state.locks
        ],
        "candidates": candidates,
    }


def _plan_from_reply(reply: str, state: State, catalog: Catalog, offers: dict[str, Offer]) -> Plan:
    """The reply's looks as a plan, with each catalog variant's offer chosen by code."""

    def offer_id(variant_id: str) -> str:
        if variant_id in offers:
            return offers[variant_id].id
        # A variant that cannot pass keeps an offer of its own, so check_plan reports why.
        variant = catalog.variant(variant_id)
        return variant.offers[0].id if variant else "none"

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
        return Plan(looks=plan_looks, selected_offers={vid: offer_id(vid) for vid in used})
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
        "plan": result.plan.model_dump(mode="json") if result.plan else None,
        "violations": [violation(v) for v in (check.violations if check else ())],
        "notices": [
            {"kind": n.kind.value, "item": n.item.model_dump(mode="json"), "material": n.material}
            for n in (check.notices if check else ())
        ],
        "conflicts": [violation(v) for v in result.conflicts],
        "error": result.error,
        "usage": {
            "calls": result.usage.calls,
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
        },
    }


def _logged(result: PlanResult, client: SparkClient, latency_s: float) -> PlanResult:
    logger.info(
        "plan outcome=%s model=%s calls=%d prompt_tokens=%d completion_tokens=%d latency_s=%.1f",
        result.outcome,
        client.model,
        result.usage.calls,
        result.usage.prompt_tokens,
        result.usage.completion_tokens,
        latency_s,
    )
    return result
