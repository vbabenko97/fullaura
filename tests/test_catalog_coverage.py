"""The real catalog covers the closed beta's example profiles (docs/DATA.md#catalog-coverage).

For each example profile and scenario, code must find three different complete looks with closed
shoes that pass every hard constraint, from catalog items alone, and at least one position that
a different catalog item can fill while the plan still passes. The profiles are examples, not
any person's sizes. A failure here is a catalog gap: fix it with sourced records, never by
relaxing a constraint.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from backend.app.catalog import Catalog, Category, GarmentType, Line, Product, Size, SizeSystem
from backend.app.config import Settings
from backend.app.constraints import cheapest_usable_offers
from backend.app.planner import check_complete_plan
from backend.app.state import (
    Budget,
    BudgetMode,
    Fit,
    FootwearChoice,
    ItemKind,
    ItemRef,
    Look,
    Plan,
    Slot,
    State,
)

CATALOG = Catalog.model_validate_json(Settings().catalog_file.read_text(encoding="utf-8"))
CHEAPEST_LOOKS = 60  # the search combines only this many of the cheapest looks
PLANS_TRIED = 20  # passing plans checked for a valid replacement


def _fit(line: Line, *sizes: tuple[SizeSystem, str]) -> Fit:
    return Fit(lines={line}, sizes=[Size(system=system, value=value) for system, value in sizes])


PROFILES = {
    "men-m": _fit(
        Line.MEN,
        (SizeSystem.LETTER, "M"),
        (SizeSystem.WAIST_LENGTH, "32/32"),
        (SizeSystem.EU_SHOE, "42"),
    ),
    "men-l": _fit(
        Line.MEN,
        (SizeSystem.LETTER, "L"),
        (SizeSystem.WAIST_LENGTH, "34/32"),
        (SizeSystem.EU_SHOE, "43"),
    ),
    "men-s": _fit(
        Line.MEN,
        (SizeSystem.LETTER, "S"),
        (SizeSystem.WAIST_LENGTH, "30/32"),
        (SizeSystem.EU_SHOE, "41"),
    ),
    "men-xl": _fit(
        Line.MEN,
        (SizeSystem.LETTER, "XL"),
        (SizeSystem.WAIST_LENGTH, "36/32"),
        (SizeSystem.EU_SHOE, "44"),
    ),
    "women-xs": _fit(Line.WOMEN, (SizeSystem.LETTER, "XS"), (SizeSystem.EU_SHOE, "36")),
    "women-s": _fit(Line.WOMEN, (SizeSystem.LETTER, "S"), (SizeSystem.EU_SHOE, "38")),
    "women-m": _fit(Line.WOMEN, (SizeSystem.LETTER, "M"), (SizeSystem.EU_SHOE, "39")),
    "women-l": _fit(Line.WOMEN, (SizeSystem.LETTER, "L"), (SizeSystem.EU_SHOE, "40")),
    "women-xl": _fit(Line.WOMEN, (SizeSystem.LETTER, "XL"), (SizeSystem.EU_SHOE, "37")),
}
# Every women's shoe on sale in EU 41 has a sole material outside the checked list, so its
# composition is partial and fails any exclusion (docs/DATA.md#catalog-coverage).
PROFILES_WITHOUT_AN_EXCLUSION = {
    "women-m-eu-41": _fit(Line.WOMEN, (SizeSystem.LETTER, "M"), (SizeSystem.EU_SHOE, "41")),
}


@dataclass(frozen=True)
class Scenario:
    budget: Budget
    exclusions: frozenset[str]


SCENARIOS = {
    "office-autumn": Scenario(Budget(amount_cents=25000), frozenset({"wool"})),
    "casual": Scenario(Budget(amount_cents=15000), frozenset()),
    "capsule": Scenario(
        Budget(amount_cents=30000, mode=BudgetMode.WHOLE_PLAN), frozenset({"wool"})
    ),
}


def _state(profile: str, scenario: Scenario, plan: Plan | None = None) -> State:
    return State(
        budget=scenario.budget,
        material_exclusions=scenario.exclusions,
        fit=(PROFILES | PROFILES_WITHOUT_AN_EXCLUSION)[profile],
        footwear=FootwearChoice.CLOSED,
        plan=plan,
    )


def _looks(catalog: Catalog, state: State) -> list[tuple[int, dict[Slot, str]]]:
    """Every top-and-bottom or one-piece with closed shoes from usable variants, cheapest first."""
    offers = cheapest_usable_offers(catalog, state)
    by_category: dict[Category, list[str]] = {}
    for product in catalog.products:
        for variant in product.variants:
            if variant.id in offers:
                by_category.setdefault(product.category, []).append(variant.id)
    tops = by_category.get(Category.TOP, []) + by_category.get(Category.KNITWEAR, [])
    bases = [
        {Slot.TOP: t, Slot.BOTTOM: b} for t in tops for b in by_category.get(Category.BOTTOM, [])
    ]
    bases += [{Slot.ONE_PIECE: one} for one in by_category.get(Category.ONE_PIECE, [])]
    looks = [
        {**base, Slot.FOOTWEAR: shoe}
        for base in bases
        for shoe in by_category.get(Category.FOOTWEAR, [])
    ]
    priced = [
        (sum(offers[item].price_cents or 0 for item in look.values()), look) for look in looks
    ]
    return sorted(priced, key=lambda entry: (entry[0], sorted(entry[1].values())))


def _plan(catalog: Catalog, chosen: tuple[dict[Slot, str], ...], state: State) -> Plan:
    offers = cheapest_usable_offers(catalog, state)
    looks = tuple(
        Look(
            id=f"look_{number}",
            items={slot: ItemRef(kind=ItemKind.CATALOG, id=item) for slot, item in look.items()},
        )
        for number, look in enumerate(chosen, start=1)
    )
    used = {item for look in chosen for item in look.values()}
    return Plan(looks=looks, selected_offers={item: offers[item].id for item in used})


def _within_budget(
    chosen: tuple[dict[Slot, str], ...], prices: dict[str, int], budget: Budget
) -> bool:
    """A quick price filter; check_complete_plan still decides."""
    if budget.mode is BudgetMode.PER_LOOK:
        return all(
            sum(prices[item] for item in look.values()) <= budget.amount_cents for look in chosen
        )
    return sum(prices[item] for item in {i for look in chosen for i in look.values()}) <= (
        budget.amount_cents
    )


def _plans(catalog: Catalog, profile: str, scenario: Scenario) -> Iterator[Plan]:
    state = _state(profile, scenario)
    prices = {
        key: offer.price_cents or 0 for key, offer in cheapest_usable_offers(catalog, state).items()
    }
    looks = [look for _, look in _looks(catalog, state)[:CHEAPEST_LOOKS]]
    for chosen in itertools.combinations(looks, 3):
        if not _within_budget(chosen, prices, scenario.budget):
            continue
        plan = _plan(catalog, chosen, state)
        if check_complete_plan(plan, state, catalog).passed:
            yield plan


def _has_valid_replacement(catalog: Catalog, plan: Plan, profile: str, scenario: Scenario) -> bool:
    """Whether some position can take a different catalog item of its kind and still pass."""
    state = _state(profile, scenario)
    offers = cheapest_usable_offers(catalog, state)
    prices = {key: offer.price_cents or 0 for key, offer in offers.items()}
    categories = {
        variant.id: product.category for product in catalog.products for variant in product.variants
    }
    current = tuple({slot: item.id for slot, item in look.items.items()} for look in plan.looks)
    for index, look in enumerate(current):
        for slot, item in look.items():
            for variant_id in offers:
                if categories[variant_id] != categories[item] or variant_id in look.values():
                    continue
                chosen = tuple(
                    {**other, slot: variant_id} if number == index else other
                    for number, other in enumerate(current)
                )
                if not _within_budget(chosen, prices, scenario.budget):
                    continue
                if check_complete_plan(_plan(catalog, chosen, state), state, catalog).passed:
                    return True
    return False


def _assert_covered(catalog: Catalog, profile: str, scenario: str) -> None:
    case = SCENARIOS[scenario]
    plans = list(itertools.islice(_plans(catalog, profile, case), PLANS_TRIED))
    assert plans, f"No three looks with closed shoes pass for {profile} in {scenario}"
    plan = next((p for p in plans if _has_valid_replacement(catalog, p, profile, case)), None)
    assert plan is not None, f"No position in {profile} {scenario} can take a valid replacement"
    assert all(look.items[Slot.FOOTWEAR].kind is ItemKind.CATALOG for look in plan.looks)


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("profile", PROFILES)
def test_the_catalog_gives_three_looks_with_closed_shoes_and_a_replacement(
    profile: str, scenario: str
) -> None:
    _assert_covered(CATALOG, profile, scenario)


@pytest.mark.parametrize("profile", PROFILES_WITHOUT_AN_EXCLUSION)
def test_the_catalog_gives_three_looks_without_an_exclusion(profile: str) -> None:
    _assert_covered(CATALOG, profile, "casual")


def _usable_products(letter: str, category: Category) -> list[Product]:
    """Women's products in this category that have a usable variant in this letter size."""
    state = State(
        budget=Budget(amount_cents=100000),
        fit=_fit(Line.WOMEN, (SizeSystem.LETTER, letter)),
    )
    offers = cheapest_usable_offers(CATALOG, state)
    return [
        product
        for product in CATALOG.products
        if product.category is category and any(v.id in offers for v in product.variants)
    ]


def test_womens_m_has_tops_of_three_kinds_and_long_bottoms() -> None:
    titles = [product.title.lower() for product in _usable_products("M", Category.TOP)]
    for kind in ("t-shirt", "langarm", "bluse"):
        assert any(kind in title for title in titles), f"No women's M top is a {kind}"
    garments = {product.garment_type for product in _usable_products("M", Category.BOTTOM)}
    assert GarmentType.TROUSERS in garments


@pytest.mark.parametrize("letter", ["S", "M", "L"])
def test_womens_sizes_have_knitwear_and_outerwear(letter: str) -> None:
    assert len(_usable_products(letter, Category.KNITWEAR)) >= 2
    assert _usable_products(letter, Category.OUTERWEAR)


# The women's L cases must hold if this variant sells out: it was once the only women's shoe in
# EU 40 that passed a wool exclusion (docs/DATA.md#catalog-coverage).
SOLD_OUT = "tt-sneaker-plateau-pink-40"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_womens_l_cases_pass_without_the_platform_sneaker_in_eu_40(scenario: str) -> None:
    assert CATALOG.variant(SOLD_OUT) is not None
    products = tuple(
        product.model_copy(
            update={"variants": tuple(v for v in product.variants if v.id != SOLD_OUT)}
        )
        for product in CATALOG.products
    )
    catalog = CATALOG.model_copy(update={"products": products})
    assert catalog.variant(SOLD_OUT) is None
    _assert_covered(catalog, "women-l", scenario)
