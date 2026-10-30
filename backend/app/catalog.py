"""Catalog records: a product has variants, and a variant is sold through one or more offers.

A record states only what its source reports. A value the source does not give is null in the
data and None here, never a guess (docs/DATA.md). Every field except a product's line, garment
type and footwear closure and a variant's normalized size is required, so a catalog file must
write an unknown value as an explicit null.
"""

from __future__ import annotations

import re
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
    ValidationInfo,
    field_validator,
    model_validator,
)


class Record(BaseModel):
    """Immutable, and refuses fields it does not define, so a misspelled key fails loudly."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# The generic material names FullAura checks, each with the other names that sources and people
# use for it. Any other name is refused, so an exclusion or a catalog record cannot name a
# material that the checks would not recognize.
MATERIALS: dict[str, tuple[str, ...]] = {
    "acetate": (),
    "acrylic": (),
    "cashmere": (),
    "cork": (),
    "cotton": (),
    "elastane": ("spandex",),
    "ethylene-vinyl acetate": ("eva",),
    "leather": (),
    "linen": (),
    "lyocell": (),
    "metal": (),
    "modal": (),
    "polyamide": ("nylon",),
    "polyester": (),
    "polyurethane": ("pu",),
    "rubber": ("natural rubber",),
    "silk": (),
    "synthetic rubber": (),
    "viscose": ("rayon",),
    "wool": ("merino", "merino wool", "lambswool", "lamb's wool", "woollen", "woolen"),
}
_GENERIC_NAME = {other: name for name, others in MATERIALS.items() for other in (name, *others)}


def generic_material(name: str) -> str:
    """The generic name for a listed material or one of its other names, ignoring case and
    spacing: "PU" gives "polyurethane" and "Merino Wool" gives "wool"."""
    key = " ".join(name.casefold().replace("’", "'").split())
    if key not in _GENERIC_NAME:
        raise ValueError(f"Unknown material {name!r}; use one of {sorted(MATERIALS)}")
    return _GENERIC_NAME[key]


Id = Annotated[str, Field(pattern=r"^\S+$")]
Material = Annotated[str, AfterValidator(generic_material)]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]


class Category(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"
    ONE_PIECE = "one_piece"
    KNITWEAR = "knitwear"
    OUTERWEAR = "outerwear"
    FOOTWEAR = "footwear"
    ACCESSORY = "accessory"


class Line(StrEnum):
    """The retailer's own department for a product."""

    WOMEN = "women"
    MEN = "men"
    UNISEX = "unisex"


class GarmentType(StrEnum):
    """What kind of bottom or one-piece a product is."""

    TROUSERS = "trousers"
    JEANS = "jeans"
    SHORTS = "shorts"
    SKIRT = "skirt"
    LEGGINGS = "leggings"
    DRESS = "dress"


class FootwearClosure(StrEnum):
    """Whether a shoe covers the foot, from the retailer's own product type: a sneaker, shoe or
    boot is closed; a slide, sandal or clog with an open heel is open."""

    CLOSED = "closed"
    OPEN = "open"


class SizeSystem(StrEnum):
    LETTER = "letter"
    WAIST_LENGTH = "waist_length"  # inches: a waist such as 32, or a waist and length, 32/34
    EU_SHOE = "eu_shoe"  # such as 42 or 42.5


_SIZE_PATTERNS = {
    SizeSystem.LETTER: r"XXS|XS|S|M|L|XL|XXL|3XL",
    SizeSystem.WAIST_LENGTH: r"[0-9]{2}(/[0-9]{2})?",
    SizeSystem.EU_SHOE: r"[0-9]{2}(\.5)?",
}


class Size(Record):
    """A size in a form code can compare. Each size has one stored form, letters in capitals
    and a half shoe size with a dot, so two sizes are equal exactly when their values are."""

    system: SizeSystem
    value: str

    @field_validator("value")
    @classmethod
    def _value_fits_its_system(cls, value: str, info: ValidationInfo) -> str:
        system = info.data.get("system")
        if system is None:  # the system is invalid and reports its own error
            return value
        if system is SizeSystem.LETTER:
            value = value.upper()
        elif system is SizeSystem.EU_SHOE:
            value = value.replace(",", ".")
        if not re.fullmatch(_SIZE_PATTERNS[system], value):
            raise ValueError(f"{value!r} is not a {system} size")
        return value


class CompositionStatus(StrEnum):
    # Every part that EU labelling covers, each named by a listed material: for clothing, each
    # textile component except one under 30% of the weight that is not a main lining; for
    # shoes, the upper, the lining and insole, and the sole. Trims such as buttons, zips, elastic
    # bands and laces do not decide it (docs/DECISIONS.md, D-27).
    COMPLETE = "complete"
    # A labelled part is missing, as with an upper but no sole, or named only vaguely
    PARTIAL = "partial"
    UNKNOWN = "unknown"  # the source gives no composition
    CONFLICTING = "conflicting"  # sources disagree, so no single composition is listed


class Component(Record):
    material: Material  # stored as the generic name, such as "wool" or "polyurethane"
    percent: Annotated[int, Field(ge=1, le=100)] | None
    part: str | None  # such as "upper" or "lining"; None when the source names no part


class Composition(Record):
    status: CompositionStatus
    components: tuple[Component, ...]
    # The composition as the source shows it, word for word, such as "Hauptmaterial: 100%
    # Baumwolle (bio); Trimming: Reissverschluss aus Plastik". It keeps parts whose material is
    # vague or not listed, which the components leave out.
    source_text: str | None

    @model_validator(mode="after")
    def _components_match_status(self) -> Self:
        status = self.status
        if status is CompositionStatus.COMPLETE and not self.components:
            raise ValueError("A complete composition needs components")
        if status is CompositionStatus.PARTIAL and not (self.components or self.source_text):
            raise ValueError("A partial composition needs components or source text")
        if status in (CompositionStatus.UNKNOWN, CompositionStatus.CONFLICTING) and self.components:
            raise ValueError(f"A {status} composition cannot list components")
        if status is CompositionStatus.UNKNOWN and self.source_text:
            raise ValueError("A composition with source text is partial or conflicting")
        if status is CompositionStatus.COMPLETE:
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
    size: str | None  # the source's own label, such as "Weite 32 / Länge 32"
    normalized_size: Size | None = None  # the same size in a form code can compare
    composition: Composition
    source: Source
    offers: tuple[Offer, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _a_waist_size_has_a_length(self) -> Self:
        size = self.normalized_size
        if size and size.system is SizeSystem.WAIST_LENGTH and "/" not in size.value:
            raise ValueError(f"Variant {self.id} needs a waist and a length, such as 32/32")
        return self

    def offer(self, offer_id: str) -> Offer | None:
        return next((offer for offer in self.offers if offer.id == offer_id), None)


class Product(Record):
    id: Id
    title: str
    brand: str | None
    category: Category
    line: Line | None = None  # None when the source gives no line
    garment_type: GarmentType | None = None  # for a bottom or one-piece; None when not given
    footwear_closure: FootwearClosure | None = None  # for footwear; None when not given
    variants: tuple[Variant, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _only_bottoms_and_one_pieces_have_a_garment_type(self) -> Self:
        if self.garment_type and self.category not in (Category.BOTTOM, Category.ONE_PIECE):
            raise ValueError(f"A {self.category} product cannot have a garment type")
        if self.footwear_closure and self.category is not Category.FOOTWEAR:
            raise ValueError(f"A {self.category} product cannot have a footwear closure")
        return self


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

    def product_of(self, variant_id: str) -> Product | None:
        return next(
            (
                product
                for product in self.products
                if any(variant.id == variant_id for variant in product.variants)
            ),
            None,
        )
