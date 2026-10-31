import type { BudgetMode, Category, Slot } from "./types";

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

/** The budget a checked plan kept, for its results heading. */
export function budgetKept(amountCents: number, mode: BudgetMode): string {
  return mode === "per_look"
    ? `Each look within ${formatMoney(amountCents)}`
    : `All three looks within ${formatMoney(amountCents)}`;
}

export function formatCheckedDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Source date not available";
  return `Price checked ${new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" }).format(date)}`;
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
