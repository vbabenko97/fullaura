"""Structured application state: the plan, and the budget, exclusions, fit, footwear, owned items
and locks that code checks it against (docs/SPEC.md#hard-constraints)."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, PositiveInt, model_validator

from backend.app.catalog import Category, GarmentType, Id, Line, Material, Record, Size

LOOKS_PER_PLAN = 3
MAX_OWNED_ITEMS = 3
MAX_FIT_SIZES = 12


class BudgetMode(StrEnum):
    PER_LOOK = "per_look"
    WHOLE_PLAN = "whole_plan"


class Budget(Record):
    amount_cents: PositiveInt  # EUR
    mode: BudgetMode = BudgetMode.PER_LOOK  # the default, D-17 in docs/DECISIONS.md


class Fit(Record):
    """Whom the looks are for, as the person states it; never inferred from photographs. An
    empty field filters nothing."""

    lines: frozenset[Line] = frozenset()
    garment_types: frozenset[GarmentType] = frozenset()
    sizes: tuple[Size, ...] = Field(default=(), max_length=MAX_FIT_SIZES)


class FootwearChoice(StrEnum):
    """Which shoes the person wants. With closed or open, every look needs footwear, and a
    catalog shoe whose closure is the other one or unknown fails; owned shoes are not checked."""

    ANY = "any"
    CLOSED = "closed"
    OPEN = "open"


class Slot(StrEnum):
    """The position an item fills in a look."""

    TOP = "top"
    BOTTOM = "bottom"
    ONE_PIECE = "one_piece"
    MID_LAYER = "mid_layer"
    OUTER_LAYER = "outer_layer"
    FOOTWEAR = "footwear"
    ACCESSORY = "accessory"


MAX_LOCKS = LOOKS_PER_PLAN * len(Slot)  # one for each position a plan has


class ItemKind(StrEnum):
    CATALOG = "catalog"  # a catalog variant, bought through the plan's selected offer
    OWNED = "owned"  # an item the person already owns


class ItemRef(Record):
    kind: ItemKind
    id: Id


class OwnedItem(Record):
    """An item the person owns and describes: it costs nothing and has no shopping link."""

    id: Id
    category: Category
    color: Annotated[str, Field(min_length=1, max_length=40)]
    description: Annotated[str, Field(min_length=1, max_length=200)]


class Look(Record):
    id: Id
    items: dict[Slot, ItemRef] = Field(min_length=1)

    @model_validator(mode="after")
    def _each_item_fills_one_slot(self) -> Self:
        if len(set(self.items.values())) != len(self.items):
            raise ValueError(f"An item can fill only one slot of look {self.id}")
        return self


class Plan(Record):
    """Three looks, and the one offer chosen for each catalog variant they use. A variant that
    appears in several looks is bought through the same offer at the same price."""

    looks: tuple[Look, ...] = Field(min_length=LOOKS_PER_PLAN, max_length=LOOKS_PER_PLAN)
    selected_offers: dict[Id, Id]  # variant ID to offer ID

    @model_validator(mode="after")
    def _offers_cover_exactly_the_catalog_items(self) -> Self:
        if len({look.id for look in self.looks}) != len(self.looks):
            raise ValueError("Look IDs must be unique")
        used = {
            item.id
            for look in self.looks
            for item in look.items.values()
            if item.kind is ItemKind.CATALOG
        }
        if used != self.selected_offers.keys():
            raise ValueError(
                f"Select one offer for each catalog variant in the looks, and no others; "
                f"missing {sorted(used - self.selected_offers.keys())}, "
                f"unused {sorted(self.selected_offers.keys() - used)}"
            )
        return self


class LockedPlacement(Record):
    """An item the person chose to keep, in the look and slot where they locked it. The same
    item elsewhere in the plan stays free to change."""

    look_id: Id
    slot: Slot
    item: ItemRef


class State(Record):
    budget: Budget
    material_exclusions: frozenset[Material] = frozenset()
    fit: Fit = Fit()
    footwear: FootwearChoice = FootwearChoice.ANY
    owned_items: tuple[OwnedItem, ...] = Field(default=(), max_length=MAX_OWNED_ITEMS)
    locks: tuple[LockedPlacement, ...] = Field(default=(), max_length=MAX_LOCKS)
    plan: Plan | None = None

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        owned = Counter(item.id for item in self.owned_items)
        if any(count > 1 for count in owned.values()):
            raise ValueError("Owned item IDs must be unique")
        if len({(lock.look_id, lock.slot) for lock in self.locks}) != len(self.locks):
            raise ValueError("A slot can hold only one lock")
        if self.locks and self.plan is None:
            raise ValueError("Locks refer to a plan")
        missing = {
            lock.item.id
            for lock in self.locks
            if lock.item.kind is ItemKind.OWNED and lock.item.id not in owned
        }
        if missing:
            raise ValueError(f"Locked owned items must be in the owned items: {sorted(missing)}")
        return self
