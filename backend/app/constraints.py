"""The hard constraints in docs/SPEC.md, checked in code against state and catalog records.

SPEC.md allows a plan to be shown only when check_plan finds no violation. When lock_conflicts
finds one, no plan that keeps the locks can pass, and the plan is reported as infeasible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from backend.app.catalog import Catalog, CompositionStatus, Offer, Variant, normalize_material
from backend.app.state import Budget, BudgetMode, ItemKind, ItemRef, LockedPlacement, State

DELIVERY_COUNTRY = "AT"


class Rule(StrEnum):
    UNKNOWN_ITEM = "unknown_item"  # no catalog record or owned item has this ID
    OFFER = "offer"  # the selected offer is not one of the variant's offers
    PRICE_UNKNOWN = "price_unknown"  # fails the budget
    BUDGET = "budget"
    MATERIAL = "material"  # the composition lists an excluded material
    COMPOSITION_UNCHECKED = "composition_unchecked"  # not complete, so an exclusion cannot pass
    DELIVERY = "delivery"  # delivery to Austria is not confirmed
    LOCK = "lock"


@dataclass(frozen=True, slots=True)
class Violation:
    rule: Rule
    detail: str
    item: ItemRef | None = None
    look_id: str | None = None
    cost_cents: int | None = None  # for a budget violation, the known cost that exceeds it


class NoticeKind(StrEnum):
    OWNED_UNCONFIRMED = "owned_unconfirmed"  # unknown whether the item has the material
    OWNED_REPORTED = "owned_reported"  # the person's description names the material


@dataclass(frozen=True, slots=True)
class Notice:
    """A label the interface shows for an owned item under an exclusion; not a failure."""

    kind: NoticeKind
    item: ItemRef
    material: str


@dataclass(frozen=True, slots=True)
class Check:
    violations: tuple[Violation, ...]
    notices: tuple[Notice, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def names_material(text: str, material: str) -> bool:
    """Whether the text contains the material's name, even inside a longer word: "wool" is in
    "merino wool" and "lambswool". Erring this way leaves an item out rather than letting an
    excluded material through. Case and spacing do not matter."""
    return normalize_material(material) in " ".join(text.casefold().split())


def item_violations(variant: Variant, offer: Offer, exclusions: Iterable[str]) -> list[Violation]:
    """Why this variant, bought through this offer, cannot appear in any plan; empty if it can.
    The checks hold for every look, so a planner can use them to filter the catalog."""
    item = ItemRef(kind=ItemKind.CATALOG, id=variant.id)
    violations = []
    exclusions = sorted({normalize_material(material) for material in exclusions})
    composition = variant.composition
    found = [
        material
        for material in exclusions
        if any(names_material(part.material, material) for part in composition.components)
    ]
    if found:
        violations.append(
            Violation(Rule.MATERIAL, f"{variant.id} contains {', '.join(found)}", item)
        )
    elif exclusions and composition.status is not CompositionStatus.COMPLETE:
        violations.append(
            Violation(
                Rule.COMPOSITION_UNCHECKED,
                f"{variant.id} has a {composition.status} composition, so exclusions cannot "
                "be checked",
                item,
            )
        )
    if DELIVERY_COUNTRY not in offer.delivery_confirmed_to:
        violations.append(
            Violation(Rule.DELIVERY, f"{offer.id} does not confirm delivery to Austria", item)
        )
    if offer.price_cents is None:
        violations.append(Violation(Rule.PRICE_UNKNOWN, f"{offer.id} has no price", item))
    return violations


def cost(
    items: Iterable[ItemRef], selected_offers: Mapping[str, str], catalog: Catalog
) -> int | None:
    """EUR cents for the distinct catalog variants among the items, each at its selected
    offer's price. Owned items cost nothing. None when a price is unknown or does not resolve.

    For one look this is the look's cost; for all three looks, the whole plan's."""
    prices = _prices(items, selected_offers, catalog)
    return None if None in prices else sum(prices)


def _prices(
    items: Iterable[ItemRef], selected_offers: Mapping[str, str], catalog: Catalog
) -> list[int | None]:
    """The selected offer's price for each distinct catalog variant among the items."""
    prices = []
    for variant_id in {item.id for item in items if item.kind is ItemKind.CATALOG}:
        variant = catalog.variant(variant_id)
        offer = variant.offer(selected_offers.get(variant_id, "")) if variant else None
        prices.append(offer.price_cents if offer else None)
    return prices


def check_plan(state: State, catalog: Catalog) -> Check:
    """Every hard-constraint violation in the state's plan, and the owned-item labels."""
    plan = state.plan
    if plan is None:
        raise ValueError("The state has no plan to check")
    violations: list[Violation] = []
    notices: list[Notice] = []
    owned = {item.id: item for item in state.owned_items}
    placed = (item for look in plan.looks for item in look.items.values())
    for item in dict.fromkeys(placed):  # each distinct item once, in plan order
        if item.kind is ItemKind.OWNED:
            if item.id not in owned:
                violations.append(Violation(Rule.UNKNOWN_ITEM, f"No owned item {item.id}", item))
                continue
            description = owned[item.id].description
            notices.extend(
                Notice(
                    NoticeKind.OWNED_REPORTED
                    if names_material(description, material)
                    else NoticeKind.OWNED_UNCONFIRMED,
                    item,
                    material,
                )
                for material in sorted(state.material_exclusions)
            )
            continue
        variant = catalog.variant(item.id)
        if variant is None:
            violations.append(Violation(Rule.UNKNOWN_ITEM, f"No catalog record {item.id}", item))
            continue
        offer_id = plan.selected_offers.get(item.id, "")
        offer = variant.offer(offer_id)
        if offer is None:
            violations.append(
                Violation(Rule.OFFER, f"No offer {offer_id!r} of {item.id} is selected", item)
            )
            continue
        violations.extend(item_violations(variant, offer, state.material_exclusions))

    # Known prices alone can exceed the budget even when another price is unknown.
    if state.budget.mode is BudgetMode.PER_LOOK:
        for look in plan.looks:
            prices = _prices(look.items.values(), plan.selected_offers, catalog)
            violations.extend(_over_budget(prices, state.budget, look.id))
    else:
        items = [item for look in plan.looks for item in look.items.values()]
        prices = _prices(items, plan.selected_offers, catalog)
        violations.extend(_over_budget(prices, state.budget))

    looks = {look.id: look for look in plan.looks}
    for lock in state.locks:
        look = looks.get(lock.look_id)
        if look is None or look.items.get(lock.slot) != lock.item:
            violations.append(
                Violation(
                    Rule.LOCK,
                    f"{lock.item.id} is locked in {lock.look_id} as {lock.slot}",
                    lock.item,
                    lock.look_id,
                )
            )
    return Check(tuple(violations), tuple(notices))


def lock_conflicts(state: State, catalog: Catalog) -> list[Violation]:
    """Violations that every plan keeping the state's locks would have.

    A locked catalog item counts at its cheapest usable offer, so a budget violation here is
    a lower bound that no choice of the other items can fix. Owned items cost nothing and
    never conflict."""
    violations: list[Violation] = []
    prices: dict[str, int] = {}
    for item in dict.fromkeys(lock.item for lock in state.locks):
        if item.kind is ItemKind.OWNED:
            continue
        variant = catalog.variant(item.id)
        if variant is None:
            violations.append(Violation(Rule.UNKNOWN_ITEM, f"No catalog record {item.id}", item))
            continue
        options = [
            item_violations(variant, offer, state.material_exclusions) for offer in variant.offers
        ]
        usable = [
            offer.price_cents
            for offer, issues in zip(variant.offers, options, strict=True)
            if not issues and offer.price_cents is not None
        ]
        if usable:
            prices[item.id] = min(usable)
        else:
            violations.extend(min(options, key=len))  # the offer closest to usable

    def locked_prices(locks: Iterable[LockedPlacement]) -> list[int | None]:
        return [prices.get(variant_id) for variant_id in {lock.item.id for lock in locks}]

    catalog_locks = [lock for lock in state.locks if lock.item.kind is ItemKind.CATALOG]
    if state.budget.mode is BudgetMode.PER_LOOK:
        for look_id in dict.fromkeys(lock.look_id for lock in catalog_locks):
            look_locks = [lock for lock in catalog_locks if lock.look_id == look_id]
            violations.extend(
                _over_budget(locked_prices(look_locks), state.budget, look_id, locked=True)
            )
    else:
        violations.extend(_over_budget(locked_prices(catalog_locks), state.budget, locked=True))
    return violations


def _over_budget(
    prices: list[int | None], budget: Budget, look_id: str | None = None, locked: bool = False
) -> list[Violation]:
    """A budget violation when the known prices alone exceed the budget."""
    known = sum(price for price in prices if price is not None)
    if known <= budget.amount_cents:
        return []
    subject = f"look {look_id}" if look_id else "the whole plan"
    if locked:
        subject = f"the items locked in {subject}" if look_id else "the locked items"
    return [
        Violation(
            Rule.BUDGET,
            f"{_eur(known)}{' or more' if None in prices else ''} for {subject}, over the "
            f"{_eur(budget.amount_cents)} budget for "
            f"{'each look' if budget.mode is BudgetMode.PER_LOOK else 'the whole plan'}",
            look_id=look_id,
            cost_cents=known,
        )
    ]


def _eur(cents: int) -> str:
    return f"EUR {cents // 100}.{cents % 100:02d}"
