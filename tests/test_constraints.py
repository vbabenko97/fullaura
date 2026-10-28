"""Hard constraints from docs/SPEC.md against a synthetic catalog; no product in it is real.

Tests named G01 to G12 follow scenarios of those names in the research notes that informed
T-03, which are not in the repository; each test states its inputs and expected results in
full. Costs are EUR cents.
"""

from collections.abc import Iterable
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.catalog import Catalog, Component, Composition, Product, Variant
from backend.app.constraints import (
    Notice,
    NoticeKind,
    Rule,
    Violation,
    check_plan,
    cost,
    item_violations,
    lock_conflicts,
)
from backend.app.state import (
    Budget,
    BudgetMode,
    ItemKind,
    ItemRef,
    LockedPlacement,
    Look,
    OwnedItem,
    Plan,
    State,
)

CATALOG = Catalog.model_validate_json(
    (Path(__file__).parent / "fixtures" / "synthetic_catalog.json").read_text(encoding="utf-8")
)
BOOTS = OwnedItem(id="O01", category="footwear", color="black", description="black ankle boots")
SCARF = OwnedItem(id="O02", category="accessory", color="red", description="red wool scarf")
PER_LOOK, WHOLE_PLAN = BudgetMode.PER_LOOK, BudgetMode.WHOLE_PLAN


def ref(item_id: str) -> ItemRef:
    """An owned item when the ID starts with O, otherwise a catalog variant."""
    return ItemRef(kind=ItemKind.OWNED if item_id.startswith("O") else ItemKind.CATALOG, id=item_id)


def plan(*looks: dict[str, str], offers: dict[str, str] | None = None) -> Plan:
    """Looks look_1 to look_3 from slot-to-ID maps; each variant uses its fixture offer."""
    used = {item_id for look in looks for item_id in look.values() if not item_id.startswith("O")}
    return Plan(
        looks=[
            Look(id=f"look_{n}", items={slot: ref(item_id) for slot, item_id in items.items()})
            for n, items in enumerate(looks, start=1)
        ],
        selected_offers={variant_id: f"offer_{variant_id}_AT" for variant_id in used}
        | (offers or {}),
    )


def lock(look_id: str, slot: str, item_id: str) -> LockedPlacement:
    return LockedPlacement(look_id=look_id, slot=slot, item=ref(item_id))


def state(budget_cents: int, mode: BudgetMode = PER_LOOK, **fields: object) -> State:
    return State(budget=Budget(amount_cents=budget_cents, mode=mode), **fields)


def found(violations: Iterable[Violation]) -> list[tuple[Rule, str | None, str | None]]:
    return [(v.rule, v.item.id if v.item else None, v.look_id) for v in violations]


def rules_per_item(item_ids: list[str], exclusions: set[str]) -> dict[str, set[Rule]]:
    """The rules each fixture variant fails, bought through its only offer."""
    result = {}
    for item_id in item_ids:
        variant = CATALOG.variant(item_id)
        result[item_id] = {v.rule for v in item_violations(variant, variant.offers[0], exclusions)}
    return result


def variant_like(base_id: str, new_id: str, **offer_changes: object) -> Variant:
    """A copy of a fixture variant under a new ID, with its offer changed."""
    base = CATALOG.variant(base_id)
    offer = base.offers[0].model_copy(update={"id": f"offer_{new_id}_AT", **offer_changes})
    return base.model_copy(update={"id": new_id, "offers": (offer,)})


def catalog_with(*variants: Variant) -> Catalog:
    """The fixture catalog plus one product holding the given variants."""
    extra = Product(
        id="product_extra", title="Extra", brand=None, category="top", variants=variants
    )
    return Catalog(id="test", synthetic=True, products=(*CATALOG.products, extra))


G06_PLAN = (
    {"top": "P01", "outer_layer": "P15", "bottom": "P05", "footwear": "P07"},
    {"top": "P03", "bottom": "P05", "footwear": "P07"},
    {"top": "P01", "bottom": "P05", "footwear": "P07"},
)
G07_PLAN = (
    {"top": "P01", "outer_layer": "P10", "bottom": "P05", "footwear": "P08"},
    {"top": "P01", "bottom": "P05", "footwear": "P07"},
    {"top": "P03", "bottom": "P05", "footwear": "P07"},
)
G07_LOCKS = (lock("look_1", "outer_layer", "P10"), lock("look_1", "footwear", "P08"))


# Materials and delivery, item by item (G01, G03, G04, G05)

UNCHECKED, MATERIAL = Rule.COMPOSITION_UNCHECKED, Rule.MATERIAL


@pytest.mark.parametrize(
    ("exclusions", "expected"),
    [
        pytest.param(
            {"wool"},
            {"P01": set(), "P02": {MATERIAL}, "P03": set(), "P04": {UNCHECKED}, "P05": set()}
            | {"P06": {MATERIAL}, "P07": {UNCHECKED}, "P08": {UNCHECKED}, "P18": set()},
            id="G01-G03-wool-excluded",
        ),
        pytest.param(
            set(),
            {item_id: set() for item_id in ("P02", "P04", "P06", "P07", "P08", "P12")},
            id="G04-no-exclusion",
        ),
    ],
)
def test_exclusions_reject_listed_materials_and_compositions_they_cannot_check(
    exclusions: set[str], expected: dict[str, set[Rule]]
) -> None:
    assert rules_per_item(list(expected), exclusions) == expected


def test_unknown_composition_is_never_reported_as_the_excluded_material() -> None:
    p04 = CATALOG.variant("P04")
    (violation,) = item_violations(p04, p04.offers[0], {"wool"})
    assert violation.rule is UNCHECKED and "wool" not in violation.detail


def test_a_plan_with_excluded_or_unchecked_materials_fails() -> None:
    looks = plan(
        {"top": "P02", "bottom": "P04", "footwear": "P07"},
        {"top": "P01", "bottom": "P05", "footwear": "P18"},
        {"top": "P01", "bottom": "P05", "footwear": "P18"},
    )
    expected = [(MATERIAL, "P02", None), (UNCHECKED, "P04", None), (UNCHECKED, "P07", None)]
    for exclusion in ("wool", " WOOL "):
        check = check_plan(state(10**6, material_exclusions={exclusion}, plan=looks), CATALOG)
        assert found(check.violations) == expected
    assert check_plan(state(10**6, plan=looks), CATALOG).passed


def test_item_checks_ignore_the_case_of_an_exclusion() -> None:
    p02 = CATALOG.variant("P02")
    assert [v.rule for v in item_violations(p02, p02.offers[0], {" Wool"})] == [MATERIAL]


def test_g01_g03_g04_example_looks_fit_their_budgets() -> None:
    def look_cost(*item_ids: str) -> int | None:
        offers = {i: f"offer_{i}_AT" for i in item_ids if not i.startswith("O")}
        return cost([ref(i) for i in item_ids], offers, CATALOG)

    assert look_cost("P01", "P05", "O01") == 12000 <= 17000  # G01: owned boots cost nothing
    assert look_cost("P01", "P05", "P18") == 18000 <= 25000  # G03
    assert look_cost("P01", "P04", "P07") == 21000 <= 25000  # G04
    assert CATALOG.variant("P04").composition.status == "unknown"  # shown as unknown


def test_g05_delivery_to_austria_must_be_confirmed() -> None:
    assert rules_per_item(["P13", "P12"], set()) == {"P13": {Rule.DELIVERY}, "P12": set()}


@pytest.mark.parametrize(
    ("components", "status", "rule"),
    [
        ([Component(material="Merino Wool", percent=100, part=None)], "complete", MATERIAL),
        ([Component(material="lambswool", percent=None, part="collar")], "partial", MATERIAL),
        ([], "conflicting", UNCHECKED),
    ],
)
def test_exclusion_matches_material_names_inside_longer_names(
    components: list[Component], status: str, rule: Rule
) -> None:
    variant = variant_like("P01", "X1").model_copy(
        update={"composition": Composition(status=status, components=components)}
    )
    assert {v.rule for v in item_violations(variant, variant.offers[0], {"wool"})} == {rule}


# Budget: both modes, shared variants and unknown prices (G02)

G02_PLAN = plan(
    {"top": "P01", "bottom": "P05", "footwear": "P07"},
    {"top": "P03", "bottom": "P05", "footwear": "P07"},
    {"top": "P01", "mid_layer": "P03", "bottom": "P05", "footwear": "P07"},
)


@pytest.mark.parametrize(("budget", "passes"), [(27500, True), (27000, True), (26999, False)])
def test_g02_whole_plan_counts_a_shared_variant_once(budget: int, passes: bool) -> None:
    items = [item for look in G02_PLAN.looks for item in look.items.values()]
    assert cost(items, G02_PLAN.selected_offers, CATALOG) == 27000  # 685.00 if counted per look
    check = check_plan(state(budget, WHOLE_PLAN, plan=G02_PLAN), CATALOG)
    assert check.passed is passes
    if not passes:
        assert [(v.rule, v.look_id, v.cost_cents) for v in check.violations] == [
            (Rule.BUDGET, None, 27000)
        ]


def test_per_look_budget_checks_each_look_on_its_own() -> None:
    assert check_plan(state(27000, plan=G02_PLAN), CATALOG).passed
    check = check_plan(state(21000, plan=G02_PLAN), CATALOG)
    assert [(v.rule, v.look_id, v.cost_cents) for v in check.violations] == [
        (Rule.BUDGET, "look_2", 21500),
        (Rule.BUDGET, "look_3", 27000),
    ]


def test_the_budget_is_per_look_by_default() -> None:
    assert Budget(amount_cents=10000).mode is PER_LOOK


def test_whole_plan_counts_each_color_or_size_variant_of_a_product() -> None:
    medium, large = variant_like("P01", "S-M"), variant_like("P01", "S-L")
    catalog = catalog_with(medium, large)
    shirts = plan(
        {"top": "S-M", "bottom": "P05"},
        {"top": "S-L", "bottom": "P05"},
        {"top": "S-M", "bottom": "P05"},
    )
    assert check_plan(state(17500, WHOLE_PLAN, plan=shirts), catalog).passed  # 55+55+65
    assert not check_plan(state(17499, WHOLE_PLAN, plan=shirts), catalog).passed


@pytest.mark.parametrize("mode", [PER_LOOK, WHOLE_PLAN])
def test_an_unknown_price_fails_the_budget(mode: BudgetMode) -> None:
    catalog = catalog_with(variant_like("P01", "X1", price_cents=None))
    looks = plan({"top": "X1", "bottom": "P05"}, {"top": "P01"}, {"top": "P01"})
    check = check_plan(state(10**6, mode, plan=looks), catalog)
    assert found(check.violations) == [(Rule.PRICE_UNKNOWN, "X1", None)]
    assert cost(looks.looks[0].items.values(), looks.selected_offers, catalog) is None


@pytest.mark.parametrize(
    ("mode", "budget", "over"),
    [(WHOLE_PLAN, 11999, (None, 12000)), (PER_LOOK, 6499, ("look_1", 6500))],
)
def test_known_prices_over_the_budget_fail_beside_an_unknown_price(
    mode: BudgetMode, budget: int, over: tuple[str | None, int]
) -> None:
    catalog = catalog_with(variant_like("P01", "X1", price_cents=None))
    looks = plan({"top": "X1", "bottom": "P05"}, {"top": "P01"}, {"top": "P01"})
    unknown, budget_violation = check_plan(state(budget, mode, plan=looks), catalog).violations
    assert unknown.rule is Rule.PRICE_UNKNOWN
    assert (budget_violation.rule, budget_violation.look_id, budget_violation.cost_cents) == (
        Rule.BUDGET,
        *over,
    )
    assert "or more" in budget_violation.detail


# Selected offers and catalog records

X1 = variant_like("P01", "X1")
TWO_OFFERS = X1.model_copy(
    update={
        "offers": (
            X1.offers[0],  # EUR 55.00, delivers to Austria
            X1.offers[0].model_copy(
                update={
                    "id": "offer_X1_DE",
                    "price_cents": 4000,
                    "delivery_confirmed_to": frozenset({"DE"}),
                }
            ),
        )
    }
)


@pytest.mark.parametrize(
    ("offer", "expected"),
    [
        ("offer_X1_AT", []),
        ("offer_X1_DE", [(Rule.DELIVERY, "X1", None)]),  # the variant's other offer
        ("offer_P03_AT", [(Rule.OFFER, "X1", None)]),  # an offer of another variant
    ],
)
def test_the_selected_offer_decides_delivery_and_must_belong_to_the_variant(
    offer: str, expected: list
) -> None:
    looks = plan({"top": "X1"}, {"top": "P01"}, {"top": "P01"}, offers={"X1": offer})
    check = check_plan(state(10**6, plan=looks), catalog_with(TWO_OFFERS))
    assert found(check.violations) == expected


def test_a_locked_variant_counts_at_its_cheapest_offer_that_delivers_to_austria() -> None:
    looks = plan({"top": "X1"}, {"top": "P01"}, {"top": "P01"})
    locked = (lock("look_1", "top", "X1"),)
    catalog = catalog_with(TWO_OFFERS)
    assert lock_conflicts(state(5500, locks=locked, plan=looks), catalog) == []
    (conflict,) = lock_conflicts(state(5499, locks=locked, plan=looks), catalog)
    assert conflict.cost_cents == 5500  # not the EUR 40.00 offer without Austrian delivery


def test_items_without_a_record_fail() -> None:
    looks = plan({"top": "P99", "footwear": "O01"}, {"top": "P01"}, {"top": "P01"})
    check = check_plan(state(10**6, plan=looks), CATALOG)
    assert found(check.violations) == [
        (Rule.UNKNOWN_ITEM, "P99", None),
        (Rule.UNKNOWN_ITEM, "O01", None),
    ]


# Owned items: no cost, no shopping link, and labels instead of material failures


def test_owned_items_stay_under_an_exclusion_with_labels() -> None:
    looks = plan(
        {"top": "P01", "bottom": "P05", "footwear": "O01", "accessory": "O02"},
        {"top": "P03", "bottom": "P05", "footwear": "O01"},
        {"top": "P01", "bottom": "P09", "footwear": "P18"},
    )
    owned = (BOOTS, SCARF)
    check = check_plan(
        state(18500, material_exclusions={"wool"}, owned_items=owned, plan=looks), CATALOG
    )
    assert check.passed
    assert set(check.notices) == {
        Notice(NoticeKind.OWNED_UNCONFIRMED, ref("O01"), "wool"),
        Notice(NoticeKind.OWNED_REPORTED, ref("O02"), "wool"),
    }
    assert not check_plan(state(18500, owned_items=owned, plan=looks), CATALOG).notices
    assert "url" not in OwnedItem.model_fields


# Locks (G06, G07, G12)


def test_g06_an_edit_keeps_the_locked_footwear() -> None:
    footwear = (lock("look_1", "footwear", "P07"),)
    before = plan(*G06_PLAN)
    edited = plan({**G06_PLAN[0], "outer_layer": "P03"}, *G06_PLAN[1:])
    for current, look_1_cost in ((before, 28500), (edited, 27000)):
        assert check_plan(state(30000, locks=footwear, plan=current), CATALOG).passed
        items = current.looks[0].items.values()
        assert cost(items, current.selected_offers, CATALOG) == look_1_cost

    moved = plan({**G06_PLAN[0], "footwear": "P18"}, *G06_PLAN[1:])
    check = check_plan(state(30000, locks=footwear, plan=moved), CATALOG)
    assert found(check.violations) == [(Rule.LOCK, "P07", "look_1")]


def test_a_lock_holds_only_its_own_look_and_slot() -> None:
    footwear = (lock("look_1", "footwear", "P07"),)
    changed = plan(G06_PLAN[0], {**G06_PLAN[1], "footwear": "P18"}, G06_PLAN[2])
    assert check_plan(state(30000, locks=footwear, plan=changed), CATALOG).passed

    look_1 = {key: value for key, value in G06_PLAN[0].items() if key != "footwear"}
    moved = plan({**look_1, "accessory": "P07"}, *G06_PLAN[1:])  # same look, other slot
    check = check_plan(state(30000, locks=footwear, plan=moved), CATALOG)
    assert found(check.violations) == [(Rule.LOCK, "P07", "look_1")]


def test_per_look_lock_bounds_count_each_look_on_its_own() -> None:
    locks = (lock("look_1", "outer_layer", "P10"), lock("look_3", "footwear", "P07"))
    current = plan(*G07_PLAN)
    assert lock_conflicts(state(11000, locks=locks, plan=current), CATALOG) == []  # not 190.00


@pytest.mark.parametrize(
    ("variant_id", "rule"), [("P13", Rule.DELIVERY), ("P99", Rule.UNKNOWN_ITEM)]
)
def test_a_locked_product_that_cannot_be_bought_is_a_conflict(variant_id: str, rule: Rule) -> None:
    looks = plan({"top": "P01", "one_piece": variant_id}, {"top": "P01"}, {"top": "P01"})
    locked = state(10**6, locks=(lock("look_1", "one_piece", variant_id),), plan=looks)
    assert found(lock_conflicts(locked, CATALOG)) == [(rule, variant_id, None)]


def test_g07_locked_items_over_the_budget_make_the_plan_infeasible() -> None:
    current = plan(*G07_PLAN)
    assert lock_conflicts(state(40000, locks=G07_LOCKS, plan=current), CATALOG) == []
    lowered = state(18000, locks=G07_LOCKS, plan=current)
    (conflict,) = lock_conflicts(lowered, CATALOG)
    assert (conflict.rule, conflict.look_id, conflict.cost_cents) == (Rule.BUDGET, "look_1", 23000)
    # Even look_1 with only its locked items fails, so no choice of other items can pass.
    bare = plan({"outer_layer": "P10", "footwear": "P08"}, *G07_PLAN[1:])
    check = check_plan(state(18000, locks=G07_LOCKS, plan=bare), CATALOG)
    assert (Rule.BUDGET, None, "look_1") in found(check.violations)


def test_whole_plan_lock_bound_counts_a_variant_locked_twice_once() -> None:
    locks = (*G07_LOCKS, lock("look_2", "footwear", "P07"), lock("look_3", "footwear", "P07"))
    current = plan(*G07_PLAN)
    assert lock_conflicts(state(31000, WHOLE_PLAN, locks=locks, plan=current), CATALOG) == []
    (conflict,) = lock_conflicts(state(30999, WHOLE_PLAN, locks=locks, plan=current), CATALOG)
    assert (conflict.rule, conflict.cost_cents) == (Rule.BUDGET, 31000)


def test_a_locked_product_that_fails_a_new_exclusion_is_a_conflict() -> None:
    looks = plan(
        {"top": "P06", "bottom": "P05", "accessory": "O02"},
        {"top": "P01", "bottom": "P05"},
        {"top": "P01", "bottom": "P05"},
    )
    locks = (lock("look_1", "top", "P06"), lock("look_1", "accessory", "O02"))
    assert (
        lock_conflicts(state(10**6, owned_items=(SCARF,), locks=locks, plan=looks), CATALOG) == []
    )
    excluded = state(
        10**6, material_exclusions={"wool"}, owned_items=(SCARF,), locks=locks, plan=looks
    )
    assert found(lock_conflicts(excluded, CATALOG)) == [(MATERIAL, "P06", None)]


def test_g11_no_one_piece_passes_a_wool_exclusion() -> None:
    one_pieces = [
        product.variants[0] for product in CATALOG.products if product.category == "one_piece"
    ]
    assert rules_per_item([v.id for v in one_pieces], {"wool"}) == {
        "P12": {UNCHECKED},  # unknown composition, not known to contain wool
        "P13": {Rule.DELIVERY},  # wool-free polyester, but no confirmed delivery to Austria
    }
    offers = {"P12": "offer_P12_AT", "P18": "offer_P18_AT"}
    assert cost([ref("P12"), ref("P18")], offers, CATALOG) == 15500  # the budget is not why


def test_g12_three_turns_keep_the_owned_boots_and_the_other_looks() -> None:
    others = (
        {"top": "P03", "bottom": "P05", "footwear": "P07"},
        {"top": "P01", "bottom": "P04", "footwear": "P07"},
    )
    boots = (lock("look_1", "footwear", "O01"),)
    for jacket, look_1_cost in (("P10", 23000), ("P15", 20500), ("P03", 19000)):
        look_1 = {"top": "P01", "outer_layer": jacket, "bottom": "P05", "footwear": "O01"}
        turn = plan(look_1, *others)
        check = check_plan(state(25000, owned_items=(BOOTS,), locks=boots, plan=turn), CATALOG)
        assert check.passed and not check.notices  # P04 is allowed with no exclusion
        assert cost(turn.looks[0].items.values(), turn.selected_offers, CATALOG) == look_1_cost


# State and plan structure


FOUR_OWNED = (BOOTS, SCARF, *(BOOTS.model_copy(update={"id": f"O0{n}"}) for n in (3, 4)))


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: plan({"top": "P01"}, {"top": "P01"}), id="two-looks"),
        pytest.param(
            lambda: plan({"top": "P01", "mid_layer": "P01"}, {"top": "P01"}, {"top": "P01"}),
            id="one-item-in-two-slots",
        ),
        pytest.param(lambda: plan(*G06_PLAN, offers={"P09": "offer_P09_AT"}), id="unused-offer"),
        pytest.param(
            lambda: Plan(looks=plan(*G06_PLAN).looks, selected_offers={}), id="missing-offers"
        ),
        pytest.param(lambda: state(0), id="zero-budget"),
        pytest.param(lambda: state(100, owned_items=FOUR_OWNED), id="four-owned-items"),
        pytest.param(lambda: state(100, owned_items=(BOOTS, BOOTS)), id="repeated-owned-id"),
        pytest.param(lambda: state(100, locks=G07_LOCKS), id="locks-without-plan"),
        pytest.param(
            lambda: state(100, locks=(lock("look_1", "footwear", "O01"),), plan=plan(*G06_PLAN)),
            id="lock-on-missing-owned-item",
        ),
        pytest.param(
            lambda: state(
                100,
                locks=(*G07_LOCKS, lock("look_1", "footwear", "P07")),
                plan=plan(*G07_PLAN),
            ),
            id="two-locks-in-one-slot",
        ),
        pytest.param(lambda: state(100, material_exclusions={"  "}), id="blank-exclusion"),
    ],
)
def test_inconsistent_state_is_refused(build) -> None:
    with pytest.raises(ValidationError):
        build()


def test_exclusions_are_normalized() -> None:
    assert state(100, material_exclusions={" Wool ", "wool"}).material_exclusions == {"wool"}
