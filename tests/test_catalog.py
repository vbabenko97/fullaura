"""Catalog files must state every fact or an explicit unknown, and nothing else."""

import copy
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.catalog import Catalog, Size
from backend.app.constraints import check_plan, fit_violations, item_violations
from backend.app.state import Budget, Fit, ItemKind, ItemRef, Look, Plan, State

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "synthetic_catalog.json").read_text(encoding="utf-8")
)
REAL = Catalog.model_validate_json(
    (ROOT / "backend" / "data" / "catalog.json").read_text(encoding="utf-8")
)
REAL_VARIANTS = [variant for product in REAL.products for variant in product.variants]


def test_the_real_catalog_names_registered_sources_and_keeps_their_wording() -> None:
    assert not REAL.synthetic
    register = (ROOT / "docs" / "DATA.md").read_text(encoding="utf-8")
    urls = [variant.source.url for variant in REAL_VARIANTS] + [
        url
        for variant in REAL_VARIANTS
        for offer in variant.offers
        for url in (offer.url, offer.source.url)
    ]
    assert {url.host for url in urls if url.host.removeprefix("www.") not in register} == set()
    assert all(variant.composition.source_text for variant in REAL_VARIANTS)


@pytest.mark.parametrize("exclusions", [set(), {"wool"}])
def test_the_real_catalog_builds_three_looks_that_pass_every_check(exclusions: set[str]) -> None:
    looks = [
        {"top": "ae-jaames-black-m", "bottom": "ae-aaster-space-steel-32-32"},  # EUR 199.75
        {"top": "ae-maarkos-moonshadow-m", "bottom": "ae-kaavo-lolite-32-32"},  # EUR 249.75
        {"top": "ae-loukaa-dark-earth-m", "bottom": "ae-lenovaa-dark-steel-m"},  # EUR 239.75
    ]
    plan = Plan(
        looks=[
            Look(
                id=f"look_{n}",
                items={
                    slot: ItemRef(kind=ItemKind.CATALOG, id=variant_id)
                    for slot, variant_id in {**items, "footwear": "gw-beach-hellgrau-42"}.items()
                },
            )
            for n, items in enumerate(looks, start=1)
        ],
        selected_offers={
            variant_id: f"{variant_id}-offer"
            for variant_id in {v for items in looks for v in items.values()}
            | {"gw-beach-hellgrau-42"}
        },
    )
    state = State(budget=Budget(amount_cents=25000), material_exclusions=exclusions, plan=plan)
    assert check_plan(state, REAL).violations == ()


def test_only_one_real_shoe_passes_a_wool_exclusion() -> None:
    shoes = [
        variant
        for product in REAL.products
        if product.category == "footwear"
        for variant in product.variants
    ]
    passing = [v.id for v in shoes if not item_violations(v, v.offers[0], {"wool"})]
    assert passing == ["gw-beach-hellgrau-42"]


def test_a_mens_fit_keeps_womens_lines_and_other_shoe_sizes_out_of_the_real_catalog() -> None:
    given = (("letter", "M"), ("waist_length", "32"), ("eu_shoe", "42"), ("eu_shoe", "43"))
    fit = Fit(lines={"men"}, sizes=[Size(system=s, value=v) for s, v in given])
    fitting = {
        variant.id
        for product in REAL.products
        for variant in product.variants
        if not fit_violations(product, variant, fit)
    }
    assert fitting == {
        "ae-maarkos-moonshadow-m",
        "ae-jaames-black-m",
        "ae-loukaa-dark-earth-m",
        "ae-wool-cardigan-claystone-m",
        "ae-aaster-space-steel-32-32",
        "ae-kaavo-lolite-32-32",
        "ae-selvedge-overshirt-midnight-air-m",
        "gw-beach-hellgrau-42",  # unisex
        "gw-merino-runners-men-schiefer-43",
        "gea-tramper-flex-schwarz-43",  # unisex
        "gea-kommod-classic-schwarz-43",  # unisex
    }


# An example fit that the real catalog's size L variants can serve; not any person's profile.
MENS_SIZE_L = Fit(
    lines={"men"},
    garment_types={"trousers", "jeans"},
    sizes=[
        Size(system="letter", value="L"),
        Size(system="waist_length", value="32/32"),
        Size(system="eu_shoe", value="42"),
        Size(system="eu_shoe", value="43"),
    ],
)


@pytest.mark.parametrize(
    ("exclusions", "shoes"),
    [
        # The GEA shoes have partial compositions, so a wool exclusion leaves only Beach.
        (
            set(),
            (
                "gea-kommod-classic-schwarz-43",
                "gea-tramper-flex-schwarz-43",
                "gw-beach-hellgrau-42",
            ),
        ),
        ({"wool"}, ("gw-beach-hellgrau-42",) * 3),
    ],
)
def test_the_real_catalog_builds_three_looks_for_a_mens_size_l_fit(
    exclusions: set[str], shoes: tuple[str, str, str]
) -> None:
    looks = [
        {"top": "ae-jaames-black-l", "bottom": "ae-aaster-space-steel-32-32"},
        {"top": "ae-maarkos-moonshadow-l", "bottom": "ae-kaavo-lolite-32-32"},
        {
            "top": "ae-maarkos-moonshadow-l",
            "mid_layer": "ae-loukaa-dark-earth-l",
            "bottom": "ae-aaster-space-steel-32-32",
        },
    ]
    looks = [{**items, "footwear": shoe} for items, shoe in zip(looks, shoes, strict=True)]
    plan = Plan(
        looks=[
            Look(
                id=f"look_{n}",
                items={slot: ItemRef(kind=ItemKind.CATALOG, id=v) for slot, v in items.items()},
            )
            for n, items in enumerate(looks, start=1)
        ],
        selected_offers={v: f"{v}-offer" for items in looks for v in items.values()},
    )
    state = State(
        budget=Budget(amount_cents=50000),
        material_exclusions=exclusions,
        fit=MENS_SIZE_L,
        plan=plan,
    )
    assert check_plan(state, REAL).violations == ()


@pytest.mark.parametrize(
    ("system", "given", "stored"),
    [
        ("letter", "xl", "XL"),
        ("letter", "3xl", "3XL"),
        ("waist_length", "33", "33"),
        ("waist_length", "33/32", "33/32"),
        ("eu_shoe", "39,5", "39.5"),
        ("eu_shoe", "43", "43"),
    ],
)
def test_a_size_is_stored_in_one_form(system: str, given: str, stored: str) -> None:
    assert Size(system=system, value=given).value == stored


@pytest.mark.parametrize(
    ("system", "value"),
    [
        ("letter", "XXXL"),
        ("letter", "42"),
        ("waist_length", "W32"),
        ("waist_length", "32-34"),
        ("eu_shoe", "42.3"),
        ("eu_shoe", "M"),
        ("eu_shoe", "٤٢"),  # digits other than 0-9
        ("us_shoe", "9"),
    ],
)
def test_a_size_outside_its_system_is_refused(system: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Size(system=system, value=value)


def test_the_synthetic_fixture_is_labeled_and_links_nowhere_real() -> None:
    catalog = Catalog.model_validate(FIXTURE)
    assert catalog.synthetic
    variants = [variant for product in catalog.products for variant in product.variants]
    assert len(variants) == 18
    urls = [variant.source.url for variant in variants] + [
        url
        for variant in variants
        for offer in variant.offers
        for url in (offer.url, offer.source.url)
    ]
    assert {url.host for url in urls} == {"synthetic.example"}  # a reserved test domain


def first_variant(data: dict) -> dict:
    return data["products"][0]["variants"][0]


def first_offer(data: dict) -> dict:
    return first_variant(data)["offers"][0]


def variant(data: dict, variant_id: str) -> dict:
    return next(
        v for product in data["products"] for v in product["variants"] if v["id"] == variant_id
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda d: first_offer(d).pop("price_cents"), id="unknown-price-left-out"),
        pytest.param(lambda d: first_variant(d).update(colour="ivory"), id="unknown-field"),
        pytest.param(
            lambda d: first_variant(d)["source"].update(checked_at="2026-10-27T10:00:00"),
            id="time-without-zone",
        ),
        pytest.param(lambda d: first_offer(d).update(currency="USD"), id="not-eur"),
        pytest.param(lambda d: first_offer(d).update(delivery_confirmed_to=["at"]), id="country"),
        pytest.param(
            lambda d: first_variant(d)["composition"].update(components=[]),
            id="complete-without-components",
        ),
        pytest.param(
            lambda d: first_variant(d)["composition"].update(status="unknown"),
            id="unknown-with-components",
        ),
        pytest.param(
            lambda d: first_variant(d)["composition"]["components"][0].update(percent=60),
            id="complete-but-60-percent",
        ),
        pytest.param(
            lambda d: first_variant(d)["composition"]["components"][0].update(
                material="pure cotton"
            ),
            id="unlisted-material",
        ),
        pytest.param(
            lambda d: variant(d, "P04")["composition"].update(source_text="see label"),
            id="unknown-with-source-text",
        ),
        pytest.param(
            lambda d: variant(d, "P07")["composition"].update(source_text=None),
            id="partial-with-nothing-listed",
        ),
        pytest.param(
            lambda d: d["products"][1]["variants"][0].update(id="P01"), id="repeated-variant-id"
        ),
        pytest.param(lambda d: first_variant(d).update(offers=[]), id="variant-without-offer"),
        pytest.param(lambda d: d["products"][0].update(line="kids"), id="unknown-line"),
        pytest.param(
            lambda d: d["products"][0].update(garment_type="jeans"), id="garment-type-on-a-top"
        ),
        pytest.param(
            lambda d: first_variant(d).update(
                normalized_size={"system": "waist_length", "value": "32"}
            ),
            id="variant-waist-without-length",
        ),
    ],
)
def test_malformed_records_are_refused(corrupt: Callable[[dict], object]) -> None:
    data = copy.deepcopy(FIXTURE)
    corrupt(data)
    with pytest.raises(ValidationError):
        Catalog.model_validate(data)
