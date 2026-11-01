import type { BudgetMode, Category, Product, Slot, Variant } from "./types";

export function formatMoney(cents: number | null): string {
  if (cents === null) return "Price not reported";
  return new Intl.NumberFormat("en-AT", { style: "currency", currency: "EUR" }).format(cents / 100);
}

/** What a budget limits, in the terms the hard check uses (docs/SPEC.md#hard-constraints). */
export function budgetMeaning(amountCents: number | null, mode: BudgetMode): string {
  const amount = amountCents !== null && Number.isSafeInteger(amountCents) && amountCents > 0 ? amountCents : null;
  if (mode === "whole_plan") {
    const limit = amount === null ? "The budget" : formatMoney(amount);
    return `${limit} is the limit for all three looks together, counting an item used in more than one look once, as the shopping list does.`;
  }
  if (amount === null) return "The budget is a limit for each look on its own, so the three looks together can cost more.";
  return `${formatMoney(amount)} is the limit for each look on its own, so the three looks together can cost up to ${formatMoney(amount * 3)}. The shopping list counts an item used in more than one look once.`;
}

/** What the budget limits in Design my look, which designs one look; the mode does not matter. */
export function conceptBudgetMeaning(amountCents: number | null, _mode?: BudgetMode): string {
  const amount = amountCents !== null && Number.isSafeInteger(amountCents) && amountCents > 0 ? amountCents : null;
  const limit = amount === null ? "The budget" : formatMoney(amount);
  return `${limit} is the limit for the one look, checked against the real products found for it.`;
}

/** The budget a checked plan kept, for its results heading. */
export function budgetKept(amountCents: number, mode: BudgetMode): string {
  return mode === "per_look"
    ? `Each look within ${formatMoney(amountCents)}`
    : `All three looks within ${formatMoney(amountCents)}`;
}

function checkedDay(date: Date): string {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" }).format(date);
}

export function formatCheckedDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Source date not available";
  return `Price checked ${checkedDay(date)}`;
}

/** The days a set of prices was checked, oldest first, so the newest check does not date them all. */
export function formatCheckedRange(values: string[]): string {
  const times = values.map((value) => new Date(value).getTime()).filter((time) => !Number.isNaN(time));
  if (times.length === 0) return "";
  const oldest = checkedDay(new Date(Math.min(...times)));
  const newest = checkedDay(new Date(Math.max(...times)));
  return oldest === newest ? `Prices checked ${newest}` : `Prices checked ${oldest} to ${newest}`;
}

export function stockText(stock: "in_stock" | "out_of_stock" | null): string {
  if (stock === "in_stock") return "In stock";
  return stock === "out_of_stock" ? "Out of stock" : "Stock not reported";
}

export function lookLabel(lookId: string): string {
  return lookId.replaceAll("_", " ");
}

export function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** When the beta deletes its records, as a sentence ending; the beta runs on Vienna time. */
export function recordsDeletedText(value: string | null): string {
  const date = new Date(value ?? Number.NaN);
  if (Number.isNaN(date.getTime())) return "after the beta ends";
  const when = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "long",
    timeStyle: "short",
    timeZone: "Europe/Vienna",
  }).format(date);
  return `on ${when} Vienna time`;
}

/** A catalog variant's brand, color and size, as a product line shows them. */
export function variantFacts(product: Product, variant: Variant): string {
  return [product.brand, variant.color, variant.size === null ? null : `Size ${variant.size}`]
    .filter((value): value is string => value !== null)
    .join(", ");
}

/** What the catalog record says about a variant's materials. */
export function compositionText(variant: Variant): string {
  const materials = [...new Set(variant.composition.components.map((component) => component.material))];
  return variant.composition.status === "complete"
    ? `Materials: ${materials.join(", ")}`
    : variant.composition.status === "partial"
      ? "Composition is incomplete"
      : variant.composition.status === "conflicting"
        ? "Sources disagree on composition"
        : "Composition not reported";
}

export function labelCategory(category: Category): string {
  return category.replaceAll("_", " ");
}

export function labelSlot(slot: Slot): string {
  const labels: Record<Slot, string> = {
    top: "Top",
    bottom: "Bottom",
    one_piece: "One-piece",
    mid_layer: "Mid layer",
    outer_layer: "Outer layer",
    footwear: "Footwear",
    accessory: "Accessory",
  };
  return labels[slot];
}
