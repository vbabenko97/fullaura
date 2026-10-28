"""Catalog files must state every fact or an explicit unknown, and nothing else."""

import copy
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.catalog import Catalog

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "synthetic_catalog.json").read_text(encoding="utf-8")
)


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
            lambda d: d["products"][1]["variants"][0].update(id="P01"), id="repeated-variant-id"
        ),
        pytest.param(lambda d: first_variant(d).update(offers=[]), id="variant-without-offer"),
    ],
)
def test_malformed_records_are_refused(corrupt: Callable[[dict], object]) -> None:
    data = copy.deepcopy(FIXTURE)
    corrupt(data)
    with pytest.raises(ValidationError):
        Catalog.model_validate(data)
