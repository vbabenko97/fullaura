"""Design My Look: one concept look from the brief alone, then the closest catalog products.

Experimental, behind FULLAURA_DESIGN_MODE (docs/SPEC.md#design-my-look). Muse Spark first designs
one look without seeing the catalog; code checks its structure, its shoes and the materials the
person excluded, and asks for one repair when they fail. A second call proposes, for each concept
item, the closest catalog product among those that pass every item check for the person. Code then
decides each match from the catalog record alone: close only when the product fills the slot and
the record confirms the same kind and color family, and the fit and material wherever the concept
names them (a shoe's or accessory's kind stands for its fit); an alternative when it fills the
slot but differs or the record does not confirm them; none otherwise. A concept is a description,
never a product, and a close match is similar to it, not the same garment.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Self, get_args

from pydantic import (
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from backend.app.catalog import (
    MATERIALS,
    Catalog,
    CompositionStatus,
    Offer,
    Product,
    Record,
    Variant,
)
from backend.app.constraints import cheapest_usable_offers, names_material
from backend.app.photos import outfit_prompt, plain_words
from backend.app.planner import (
    DEADLINE_S,
    HTTP_ATTEMPTS_PER_CALL,
    SLOT_CATEGORIES,
    Brief,
    Outcome,
    ProviderError,
    SparkClient,
    Usage,
    check_complete_plan,
)
from backend.app.state import FootwearChoice, ItemKind, ItemRef, Look, Plan, Slot, State

CONCEPT_ATTEMPTS = 2  # the first concept and one repair
MATCHING_ATTEMPTS = 1
MAX_CALLS = CONCEPT_ATTEMPTS + MATCHING_ATTEMPTS  # the reservation covers every call's attempts
RESERVED_HTTP_ATTEMPTS = MAX_CALLS * HTTP_ATTEMPTS_PER_CALL

# The words a concept and a product's labels may use, so code can compare them.
TOPS = (
    "t-shirt",
    "long-sleeve top",
    "shirt",
    "blouse",
    "polo",
    "sweater",
    "turtleneck",
    "sweatshirt",
    "cardigan",
    "vest",
    "tank top",
)
KINDS: dict[Slot, tuple[str, ...]] = {
    Slot.TOP: TOPS,
    Slot.MID_LAYER: TOPS,
    # The catalog's garment types, so the person's choice of garments applies to a concept too.
    # A jumpsuit is left out until the catalog and that choice know it, a limit of this version.
    Slot.BOTTOM: ("trousers", "jeans", "shorts", "skirt", "leggings"),
    Slot.ONE_PIECE: ("dress",),
    Slot.OUTER_LAYER: ("jacket", "coat", "blazer", "overshirt", "cardigan", "vest"),
    Slot.FOOTWEAR: (
        "sneakers",
        "boots",
        "ankle boots",
        "loafers",
        "derby shoes",
        "oxford shoes",
        "ballet flats",
        "sandals",
        "slides",
        "mules",
    ),
    Slot.ACCESSORY: ("bag", "scarf", "hat", "belt", "jewellery", "other"),
}
OPEN_SHOES = frozenset({"sandals", "slides", "mules"})
ColorFamily = Literal[
    "black",
    "white",
    "grey",
    "navy",
    "blue",
    "green",
    "olive",
    "brown",
    "beige",
    "cream",
    "red",
    "pink",
    "purple",
    "yellow",
    "orange",
    "multicolour",
]
COLOR_FAMILIES: tuple[str, ...] = get_args(ColorFamily)
# Plain color words, English or German, in a retailer's color name, such as "Deep Black",
# "preppy plum" or "schwarz-022"; a fancy name such as "midnight air" states no color family.
_COLOR_WORDS = {
    "black": "black",
    "white": "white",
    "grey": "grey",
    "gray": "grey",
    "navy": "navy",
    "blue": "blue",
    "green": "green",
    "olive": "olive",
    "brown": "brown",
    "beige": "beige",
    "cream": "cream",
    "ivory": "cream",
    "ecru": "cream",
    "red": "red",
    "pink": "pink",
    "purple": "purple",
    "plum": "purple",
    "lilac": "purple",
    "yellow": "yellow",
    "orange": "orange",
    "schwarz": "black",
    "weiss": "white",
    "weiß": "white",
    "grau": "grey",
    "hellgrau": "grey",
    "dunkelgrau": "grey",
    "blau": "blue",
    "hellblau": "blue",
    "grün": "green",
    "braun": "brown",
    "rot": "red",
    "rosa": "pink",
    "lila": "purple",
    "gelb": "yellow",
}
# Words in a product title, English or German, that state the kind of a top, knit, outer layer or
# shoe; the first that matches counts, so "Langarmshirt" is a long-sleeve top and not a shirt.
_TITLE_KINDS = (
    (re.compile(r"langarm|long-?sleeve"), "long-sleeve top"),
    (re.compile(r"sweat-?shirt"), "sweatshirt"),
    (re.compile(r"\bt-?shirt"), "t-shirt"),
    (re.compile(r"overshirt"), "overshirt"),
    (re.compile(r"strickjacke|cardigan"), "cardigan"),
    (re.compile(r"pullover|sweater|jumper"), "sweater"),
    (re.compile(r"boot|stiefel"), "boots"),
    (re.compile(r"sneaker|runner"), "sneakers"),
    (re.compile(r"slides?\b"), "slides"),
    (re.compile(r"sandal"), "sandals"),
    (re.compile(r"loafer"), "loafers"),
)
# Words for a garment's cut in a concept's fit or a product title. Each English word is its own
# cut, so code never counts two cuts, such as slim and regular, as the same; "schmal", "weit" and
# "oversize" count as the cut they translate or spell.
_CUT_WORDS = {
    "slim": "slim",
    "schmal": "slim",
    "skinny": "skinny",
    "fitted": "fitted",
    "regular": "regular",
    "straight": "straight",
    "relaxed": "relaxed",
    "loose": "loose",
    "wide": "wide",
    "weit": "wide",
    "barrel": "barrel",
    "flared": "flared",
    "oversized": "oversized",
    "oversize": "oversized",
    "boxy": "boxy",
}
# Other words a concept's fit may use beside its cut, as in "wide-leg" or "relaxed fit"; any other
# word, such as "not" or "tailored", leaves the fit unconfirmed.
_FIT_WORDS = frozenset({*_CUT_WORDS, "fit", "cut", "leg"})
# The parts of a product that a concept's material describes: a garment's main fabric or a shoe's
# upper, or the whole product when the source names no part.
_OUTER_PARTS = (None, "main fabric", "upper")
# Words that make a material the concept names an imitation of it, as in "faux leather" or
# "leather look", which no composition can confirm.
_IMITATION_WORDS = frozenset({"faux", "fake", "imitation", "look", "synthetic", "vegan"})
_WORDS = re.compile(r"[a-zäöüß]+")
MatchStatus = Literal["close", "alternative", "none"]

logger = logging.getLogger(__name__)


class ConceptItem(Record):
    slot: Slot
    kind: str
    color: Annotated[str, Field(min_length=1, max_length=40)]
    color_family: ColorFamily
    material: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    fit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    details: Annotated[str, Field(max_length=120)] = ""

    @field_validator("fit")
    @classmethod
    def _only_a_garment_has_a_fit(cls, fit: str, info: ValidationInfo) -> str:
        """A shoe's or accessory's kind stands for its cut, so no catalog record could confirm a
        fit named for one; it is dropped rather than shown."""
        return "" if info.data.get("slot") in (Slot.FOOTWEAR, Slot.ACCESSORY) else fit

    @model_validator(mode="after")
    def _kind_fits_the_slot(self) -> Self:
        if self.kind not in KINDS[self.slot]:
            raise ValueError(f"{self.kind!r} is not a {self.slot.value} kind: {KINDS[self.slot]}")
        return self


class Concept(Record):
    """One look as the stylist describes it; never a product."""

    id: Literal["concept_1"] = "concept_1"
    name: Annotated[str, Field(min_length=1, max_length=80)]
    why: Annotated[str, Field(max_length=240)] = ""
    items: tuple[ConceptItem, ...] = Field(min_length=2, max_length=6)

    @field_validator("items")
    @classmethod
    def _one_item_per_slot(cls, items: tuple[ConceptItem, ...]) -> tuple[ConceptItem, ...]:
        if len({item.slot for item in items}) != len(items):
            raise ValueError("Use each slot at most once")
        return items


@dataclass(frozen=True, slots=True)
class Match:
    status: MatchStatus
    variant_id: str | None = None
    offer_id: str | None = None
    differences: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Shoppable:
    """Two separate answers: how close the products are to the concept, and whether they pass
    the person's hard constraints. The concept is shoppable as shown only when both hold."""

    complete: bool  # every concept item has a close match
    passes_hard_checks: bool  # every item has a product, and together they pass every hard check
    matched_total_cents: int | None  # items with a product, close or alternative
    violations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConceptResult:
    outcome: Outcome
    usage: Usage
    concept: Concept | None = None
    matches: tuple[Match, ...] = ()
    matching_failed: bool = False
    shoppable: Shoppable | None = None
    error: str | None = None
    statuses: dict[str, int] = field(default_factory=dict)


_KINDS_JSON = json.dumps({slot.value: kinds for slot, kinds in KINDS.items()})

CONCEPT_PROMPT = f"""You are FullAura's stylist. Design one look for the person in the brief, as if
you could choose any clothes sold in shops that deliver to Austria; no product catalog is given. A
look has a top and a bottom, or a one_piece, never both; when "footwear" is "closed" or "open",
it also has shoes of that kind. You may add a mid_layer, an outer_layer or an accessory. Follow the
brief's occasion, season and colors and the clothing line. When "garment_types" is not empty, a
bottom or one_piece must be one of those kinds. Keep the look within "budget_eur" in ordinary
shops. Never use a material listed in "avoid_materials". Do not name brands.

Each item has a slot and a kind from this list, by slot: {_KINDS_JSON}.
Its color_family is one of {", ".join(COLOR_FAMILIES)}; "color" may name the exact shade.
"fit" names a garment's cut in one word: slim, regular, relaxed, wide or oversized; leave it empty
for footwear and accessories.

Reply with JSON only, in this form:
{{"name": "short name", "why": "one sentence on why it suits the brief", "items": [{{"slot": "top",
"kind": "shirt", "color": "navy", "color_family": "navy", "material": "cotton", "fit": "regular",
"details": "button-down collar"}}]}}"""

MATCHING_PROMPT = """You find real products for a stylist's concept. For each item of "concept",
choose the one product in "candidates" that comes closest to it and fills the same slot, or null
when none can fill that slot. Give one entry for every concept item. Use only candidate IDs, and
each product at most once.

Reply with JSON only, in this form:
{"matches": [{"item": 0, "candidate_id": "ID or null"}]}"""


def design_look(
    brief: Brief,
    state: State,
    catalog: Catalog,
    client: SparkClient,
    clock: Callable[[], float] = time.monotonic,
) -> ConceptResult:
    """One checked concept and the closest products for it, or why there is none."""
    started = clock()
    deadline = started + DEADLINE_S
    usage = Usage()
    messages = [
        {"role": "system", "content": CONCEPT_PROMPT},
        {"role": "user", "content": json.dumps(_concept_request(brief, state))},
    ]
    concept: Concept | None = None
    failure = "No concept was generated"
    for _ in range(CONCEPT_ATTEMPTS):
        if clock() >= deadline:
            return _logged(
                ConceptResult(Outcome.PROVIDER_TIMEOUT, usage, error=failure), started, clock
            )
        try:
            reply, call_usage, http_attempts = client.complete(
                messages, deadline=deadline, clock=clock
            )
        except ProviderError as error:
            usage = usage.add(error.usage, error.http_attempts, call_completed=error.call_completed)
            outcome = _provider_outcome(error, clock() >= deadline)
            return _logged(ConceptResult(outcome, usage, error=str(error)), started, clock)
        usage = usage.add(call_usage, http_attempts)
        try:
            concept = Concept.model_validate(_json_object(reply))
        except (ValueError, ValidationError) as error:
            failure = f"The concept was not in the expected form: {_short(error)}"
        else:
            problems = concept_problems(concept, state)
            if not problems:
                break
            failure = "The concept broke these rules: " + "; ".join(problems)
            concept = None
        messages += [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": f"{failure}. Reply again with a corrected look, JSON only.",
            },
        ]
    if concept is None:
        return _logged(
            ConceptResult(Outcome.GENERATION_FAILED, usage, error=failure), started, clock
        )

    offers = cheapest_usable_offers(catalog, state)
    matches: tuple[Match, ...] = tuple(Match("none") for _ in concept.items)
    matching_failed = True
    if clock() < deadline:
        try:
            reply, call_usage, http_attempts = client.complete(
                [
                    {"role": "system", "content": MATCHING_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(_matching_request(concept, catalog, offers)),
                    },
                ],
                deadline=deadline,
                clock=clock,
            )
        except ProviderError as error:
            usage = usage.add(error.usage, error.http_attempts, call_completed=error.call_completed)
        else:
            usage = usage.add(call_usage, http_attempts)
            try:
                proposals = _proposals(reply, len(concept.items))
            except ValueError:
                pass  # a reply without one proposal per item is a failed search, not "no match"
            else:
                matches = decide_matches(concept, proposals, catalog, offers)
                matching_failed = False
    shoppable = assess(concept, matches, state, catalog, offers)
    statuses = {
        status: sum(m.status == status for m in matches)
        for status in ("close", "alternative", "none")
    }
    result = ConceptResult(
        Outcome.SUCCESS,
        usage,
        concept,
        matches,
        matching_failed,
        shoppable,
        statuses=statuses,
    )
    return _logged(result, started, clock)


def concept_problems(concept: Concept, state: State) -> list[str]:
    """Rules a concept must keep, checked by code: a complete base outfit, the chosen kind of
    shoes, the person's garment types, and no material the person excluded."""
    slots = {item.slot for item in concept.items}
    problems = []
    separates = {Slot.TOP, Slot.BOTTOM} <= slots and Slot.ONE_PIECE not in slots
    one_piece = Slot.ONE_PIECE in slots and not slots & {Slot.TOP, Slot.BOTTOM}
    if not (separates or one_piece):
        problems.append("a look needs a top and a bottom, or a one_piece, but not both")
    shoes = next((item for item in concept.items if item.slot is Slot.FOOTWEAR), None)
    if state.footwear is not FootwearChoice.ANY:
        if shoes is None:
            problems.append(f"the look needs {state.footwear} shoes")
        elif (shoes.kind in OPEN_SHOES) != (state.footwear is FootwearChoice.OPEN):
            problems.append(f"{shoes.kind} are not {state.footwear} shoes")
    garments = state.fit.garment_types
    for item in concept.items:
        if garments and item.slot in (Slot.BOTTOM, Slot.ONE_PIECE) and item.kind not in garments:
            wanted = " or ".join(sorted(garments))
            problems.append(f"the {item.slot.value} is {item.kind}, not {wanted}")
        text = " ".join((item.material, item.kind, item.details))
        for material in sorted(state.material_exclusions):
            if names_material(text, material):
                problems.append(f"the {item.slot.value} names {material}, which is excluded")
    return problems


def decide_matches(
    concept: Concept, proposals: dict[int, str | None], catalog: Catalog, offers: dict[str, Offer]
) -> tuple[Match, ...]:
    """Each concept item's match, decided by code from the proposed product's catalog record: its
    slot, its kind from the garment type or title, its color family from the color name, a
    garment's fit from the cut its title names (the catalog records no cut; the title must name
    the same cuts as the concept), the material from the composition and, for shoes, the closure.
    A kind, color, fit or material the concept names and the record does not confirm makes it an
    alternative."""
    used: set[str] = set()
    matches = []
    for index, item in enumerate(concept.items):
        variant_id = proposals[index]
        product = catalog.product_of(variant_id) if variant_id else None
        variant = catalog.variant(variant_id) if variant_id else None
        if (
            variant_id is None
            or product is None
            or variant is None
            or variant_id not in offers
            or variant_id in used
            or product.category not in SLOT_CATEGORIES[item.slot]
        ):
            matches.append(Match("none"))
            continue
        used.add(variant_id)
        differences = []
        if item.slot is Slot.FOOTWEAR and product.footwear_closure is not None:
            wanted = "open" if item.kind in OPEN_SHOES else "closed"
            if product.footwear_closure.value != wanted:
                differences.append(f"{product.footwear_closure.value} shoes, not {wanted}")
        kind = _catalog_kind(product)
        if kind is None:
            differences.append(f"kind not confirmed as {item.kind}")
        elif kind != item.kind:
            differences.append(f"kind: {kind}, not {item.kind}")
        color = _catalog_color(variant.color)
        if color is None:
            shade = f' "{variant.color}"' if variant.color else ""
            differences.append(f"color{shade} not confirmed as {item.color_family}")
        elif color != item.color_family:
            differences.append(f"color: {color}, not {item.color_family}")
        if item.fit:  # only a garment has one
            wanted_cut, stated = _cuts(item.fit), _cuts(product.title)
            plain = set(_WORDS.findall(item.fit.lower())) <= _FIT_WORDS
            if not (plain and wanted_cut and stated):
                differences.append(f"fit not confirmed as {item.fit}")
            elif wanted_cut != stated:
                differences.append(f"fit: {' and '.join(sorted(stated))}, not {item.fit}")
        if item.material:
            material = _material_difference(item.material, variant)
            if material:
                differences.append(material)
        status: MatchStatus = "alternative" if differences else "close"
        matches.append(Match(status, variant_id, offers[variant_id].id, tuple(differences)))
    return tuple(matches)


def assess(
    concept: Concept,
    matches: tuple[Match, ...],
    state: State,
    catalog: Catalog,
    offers: dict[str, Offer],
) -> Shoppable:
    """Whether the concept can be bought as shown. Only a look whose every item has a close
    match is complete. Once every item has a product, close or alternative, the products together
    are checked against every hard constraint, so they can pass it and still differ from the
    concept."""
    priced = {m.variant_id: offers[m.variant_id].price_cents for m in matches if m.variant_id}
    total = None if None in priced.values() else sum(p or 0 for p in priced.values())
    complete = all(m.status == "close" for m in matches)
    if any(m.variant_id is None for m in matches):
        return Shoppable(False, False, total)
    items = {
        item.slot: ItemRef(kind=ItemKind.CATALOG, id=str(match.variant_id))
        for item, match in zip(concept.items, matches, strict=True)
    }
    # A plan holds three looks; one look three times keeps both budget modes' meaning for it.
    looks = tuple(Look(id=f"look_{n}", items=items) for n in (1, 2, 3))
    plan = Plan(looks=looks, selected_offers={i: offers[i].id for i in priced if i})
    check = check_complete_plan(plan, state, catalog)
    # The request route makes the budget a limit per look, which for one look is the budget itself.
    violations = tuple(
        v.detail.replace("look look_1", "the look")
        .replace("look_1", "the look")
        .replace(" budget for each look", " budget")
        for v in check.violations
        if v.look_id in (None, "look_1")
    )
    return Shoppable(complete, check.passed, total, tuple(dict.fromkeys(violations)))


def concept_prompt(concept: Concept) -> str:
    """The try-on prompt for a concept: each item's kind from the fixed list, and the model's own
    words for it in plain words, quoted as a description."""
    lines = []
    for item in concept.items:
        parts = [item.color, item.material, f"{item.fit} fit" if item.fit else "", item.details]
        described = plain_words(", ".join(part for part in parts if part), 160)
        lines.append(f'- {item.slot.value}: {item.kind}, described as "{described}"')
    return outfit_prompt(lines)


def concept_json(result: ConceptResult, catalog_id: str, token: str | None) -> dict[str, Any]:
    concept = result.concept
    return {
        "outcome": result.outcome.value,
        "error": result.error,
        "catalog_id": catalog_id,
        "concept_token": token,
        "concept": None
        if concept is None
        else {
            "id": concept.id,
            "name": concept.name,
            "why": concept.why,
            "items": [
                {
                    **item.model_dump(mode="json"),
                    "match": {
                        "status": match.status,
                        "variant_id": match.variant_id,
                        "offer_id": match.offer_id,
                        "differences": list(match.differences),
                    },
                }
                for item, match in zip(concept.items, result.matches, strict=True)
            ],
        },
        "matching_failed": result.matching_failed,
        "shoppable": None
        if result.shoppable is None
        else {
            "complete": result.shoppable.complete,
            "passes_hard_checks": result.shoppable.passes_hard_checks,
            "matched_total_cents": result.shoppable.matched_total_cents,
            "violations": list(result.shoppable.violations),
        },
    }


def _concept_request(brief: Brief, state: State) -> dict[str, Any]:
    return {
        "brief": brief.model_dump(mode="json"),
        "clothing_line": sorted(line.value for line in state.fit.lines),
        "garment_types": sorted(state.fit.garment_types),
        "budget_eur": state.budget.amount_cents / 100,
        "avoid_materials": sorted(state.material_exclusions),
        "footwear": state.footwear.value,
    }


def _matching_request(
    concept: Concept, catalog: Catalog, offers: dict[str, Offer]
) -> dict[str, Any]:
    candidates = []
    for product in catalog.products:
        for variant in product.variants:
            if variant.id not in offers:
                continue
            materials = sorted({c.material for c in variant.composition.components})
            candidates.append(
                {
                    "id": variant.id,
                    "title": product.title,
                    "category": product.category.value,
                    "garment_type": product.garment_type.value if product.garment_type else None,
                    "closure": product.footwear_closure.value if product.footwear_closure else None,
                    "color": variant.color,
                    "materials": materials
                    if variant.composition.status is CompositionStatus.COMPLETE
                    else None,
                    "price_eur": (offers[variant.id].price_cents or 0) / 100,
                }
            )
    return {
        "concept": [
            {"item": index, **item.model_dump(mode="json")}
            for index, item in enumerate(concept.items)
        ],
        "candidates": candidates,
    }


def _proposals(reply: str, count: int) -> dict[int, str | None]:
    """The product proposed for each concept item, or ValueError unless the reply gives one
    well-formed entry for every item."""
    try:
        entries = _json_object(reply)["matches"]
        proposals = {}
        for entry in entries:
            item, candidate = entry["item"], entry["candidate_id"]
            if type(item) is not int or item in proposals or not isinstance(candidate, str | None):
                raise ValueError("a malformed match")
            proposals[item] = None if candidate in ("", "null") else candidate
    except (LookupError, TypeError) as error:
        raise ValueError("a malformed match") from error
    if set(proposals) != set(range(count)):
        raise ValueError("not one match per concept item")
    return proposals


def _catalog_kind(product: Product) -> str | None:
    """A product's kind as its record states it: a bottom's or one-piece's garment type, or else
    a kind its title names."""
    if product.garment_type:
        return product.garment_type.value
    title = product.title.lower()
    return next((kind for words, kind in _TITLE_KINDS if words.search(title)), None)


def _catalog_color(color: str | None) -> str | None:
    """The color family a retailer's color name states in plain words, when it names one."""
    words = {_COLOR_WORDS[w] for w in _WORDS.findall((color or "").lower()) if w in _COLOR_WORDS}
    return words.pop() if len(words) == 1 else None


def _cuts(text: str) -> set[str]:
    """The cuts a concept's fit or a product title names in plain words."""
    return {_CUT_WORDS[w] for w in _WORDS.findall(text.lower()) if w in _CUT_WORDS}


def _material_difference(material: str, variant: Variant) -> str | None:
    """Why the variant's record does not confirm a concept's material, or None when it does: the
    record lists every material the concept names in the outer part, and one of them has the
    largest share there; without percentages, every listed material of that part counts."""
    wanted = {name for name in MATERIALS if names_material(material, name)}
    shares: dict[str, int] = {}
    for component in variant.composition.components:
        if component.part in _OUTER_PARTS:
            shares[component.material] = shares.get(component.material, 0) + (
                component.percent or 0
            )
    if not (wanted and shares) or _IMITATION_WORDS & set(_WORDS.findall(material.lower())):
        return f"material not confirmed as {material}"
    largest = max(shares.values())
    main = sorted(name for name, share in shares.items() if share == largest)
    if wanted <= shares.keys() and wanted & set(main):
        return None
    return f"material: {', '.join(main)}, not {material}"


def _json_object(reply: str) -> Any:
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    return json.loads(reply[start : end + 1])


def _short(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in error.errors()[:5]
        )
    return str(error)[:200]


def _provider_outcome(error: ProviderError, past_deadline: bool) -> Outcome:
    if error.timed_out or past_deadline:
        return Outcome.PROVIDER_TIMEOUT
    if error.generation_failed:
        return Outcome.GENERATION_FAILED
    return Outcome.PROVIDER_ERROR


def _logged(result: ConceptResult, started: float, clock: Callable[[], float]) -> ConceptResult:
    logger.info(
        "concept outcome=%s calls=%d http_attempts=%d prompt_tokens=%d completion_tokens=%d "
        "close=%d alternative=%d none=%d matching_failed=%s latency_s=%.1f",
        result.outcome,
        result.usage.calls,
        result.usage.http_attempts,
        result.usage.prompt_tokens,
        result.usage.completion_tokens,
        result.statuses.get("close", 0),
        result.statuses.get("alternative", 0),
        result.statuses.get("none", 0),
        result.matching_failed,
        clock() - started,
    )
    return result
