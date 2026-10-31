import type { Category, Slot } from "./types";

export function formatMoney(cents: number | null): string {
  if (cents === null) return "Price not reported";
  return new Intl.NumberFormat("en-AT", { style: "currency", currency: "EUR" }).format(cents / 100);
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
