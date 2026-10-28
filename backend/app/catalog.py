"""Catalog records: a product has variants, and a variant is sold through one or more offers.

A record states only what its source reports. A value the source does not give is null in the
data and None here, never a guess (docs/DATA.md). Every field is required, so a catalog file
must write an unknown value as an explicit null.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)


class Record(BaseModel):
    """Immutable, and refuses fields it does not define, so a misspelled key fails loudly."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def normalize_material(name: str) -> str:
    """Lowercase with single spaces, so "Merino  Wool" and "merino wool" compare equal."""
    normalized = " ".join(name.casefold().split())
    if not normalized:
        raise ValueError("A material name cannot be empty")
    return normalized


Id = Annotated[str, Field(pattern=r"^\S+$")]
Material = Annotated[str, AfterValidator(normalize_material)]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]


class Category(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"
    ONE_PIECE = "one_piece"
    KNITWEAR = "knitwear"
    OUTERWEAR = "outerwear"
    FOOTWEAR = "footwear"
    ACCESSORY = "accessory"


class CompositionStatus(StrEnum):
    COMPLETE = "complete"  # the source lists every component of the item
    PARTIAL = "partial"  # some components are listed, for example the upper but not the sole
    UNKNOWN = "unknown"  # the source gives no composition
    CONFLICTING = "conflicting"  # sources disagree, so no single composition is listed


class Component(Record):
    material: Material  # the generic name, such as "wool" or "polyester"
    percent: Annotated[int, Field(ge=1, le=100)] | None
    part: str | None  # such as "upper" or "lining"; None when the source names no part


class Composition(Record):
    status: CompositionStatus
    components: tuple[Component, ...]

    @model_validator(mode="after")
    def _components_match_status(self) -> Self:
        listed = self.status in (CompositionStatus.COMPLETE, CompositionStatus.PARTIAL)
        if listed != bool(self.components):
            raise ValueError(
                f"A {self.status} composition {'needs' if listed else 'cannot have'} components"
            )
        if self.status is CompositionStatus.COMPLETE:
            for part in {component.part for component in self.components}:
                percents = [c.percent for c in self.components if c.part == part]
                if None not in percents and sum(percents) != 100:
                    raise ValueError(f"Percentages for the {part or 'item'} must sum to 100")
        return self


class Source(Record):
    """Where a record's facts come from, and when they were last checked against it."""

    url: HttpUrl
    checked_at: AwareDatetime


class Stock(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"


class Offer(Record):
    """One retailer's offer of a variant: the price, delivery and stock it reports."""

    id: Id
    retailer: str
    url: HttpUrl  # the shopping link
    price_cents: Annotated[int, Field(ge=0)] | None
    currency: Literal["EUR"]
    delivery_confirmed_to: frozenset[CountryCode]  # a country missing here is not confirmed
    stock: Stock | None
    source: Source


class Variant(Record):
    """One purchasable version of a product, such as one color and size."""

    id: Id
    sku: str | None
    color: str | None
    size: str | None
    composition: Composition
    source: Source
    offers: tuple[Offer, ...] = Field(min_length=1)

    def offer(self, offer_id: str) -> Offer | None:
        return next((offer for offer in self.offers if offer.id == offer_id), None)


class Product(Record):
    id: Id
    title: str
    brand: str | None
    category: Category
    variants: tuple[Variant, ...] = Field(min_length=1)


class Catalog(Record):
    """A catalog file. A synthetic catalog is invented test data and never shown as real."""

    id: Id
    synthetic: bool
    products: tuple[Product, ...]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        ids = Counter(product.id for product in self.products)
        for product in self.products:
            ids.update(variant.id for variant in product.variants)
            ids.update(offer.id for variant in product.variants for offer in variant.offers)
        repeated = sorted(record_id for record_id, count in ids.items() if count > 1)
        if repeated:
            raise ValueError(f"Record IDs must be unique across the catalog: {repeated}")
        return self

    def variant(self, variant_id: str) -> Variant | None:
        return next(
            (
                variant
                for product in self.products
                for variant in product.variants
                if variant.id == variant_id
            ),
            None,
        )
