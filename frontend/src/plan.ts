import { EU_SHOE_SIZE, LETTER_SIZES, MAX_FIT_SIZES, SLOT_CATEGORIES, SLOTS, WAIST_SIZE } from "./types";
import { formatCheckedDate, labelCategory, labelSlot, lookLabel, stockText } from "./format";
import type {
  BudgetMode,
  Catalog,
  Fit,
  FitDraft,
  FootwearChoice,
  ItemRef,
  LockedPlacement,
  Look,
  Material,
  NormalizedSize,
  Offer,
  OwnedItem,
  Plan,
  Product,
  ReplacementTarget,
  ShoppingLine,
  Slot,
  Variant,
} from "./types";

export function catalogItem(catalog: Catalog, variantId: string): { product: Product; variant: Variant } | null {
  for (const product of catalog.products) {
    const variant = product.variants.find((candidate) => candidate.id === variantId);
    if (variant) return { product, variant };
  }
  return null;
}

// A waist without a length matches every length of that waist.
function sizeMatches(wanted: NormalizedSize, actual: NormalizedSize): boolean {
  return (
    wanted.value === actual.value ||
    (wanted.system === "waist_length" &&
      !wanted.value.includes("/") &&
      actual.value.startsWith(`${wanted.value}/`))
  );
}

/** The fit hard constraint from SPEC.md, as the backend checks it. Owned items are not checked. */
function fitsCatalogVariant(product: Product, variant: Variant, fit: Fit | undefined): boolean {
  if (!fit) return true;
  const { category, line, garment_type: garmentType } = product;
  if (fit.lines?.length && line !== "unisex" && (line === null || !fit.lines.includes(line))) {
    return false;
  }
  if (
    fit.garment_types?.length &&
    (category === "bottom" || category === "one_piece") &&
    (garmentType === null || !fit.garment_types.includes(garmentType))
  ) {
    return false;
  }
  if (!fit.sizes?.length) return true;
  const size = variant.normalized_size;
  if (size === null) return category === "accessory";
  const wanted = fit.sizes.filter((candidate) => candidate.system === size.system);
  return wanted.length === 0 || wanted.some((candidate) => sizeMatches(candidate, size));
}

/** The footwear choice from SPEC.md: with closed or open, a shoe of the other kind or with no
 * recorded closure fails. */
function meetsFootwearChoice(product: Product, footwear: FootwearChoice): boolean {
  return footwear === "any" || product.category !== "footwear" || product.footwear_closure === footwear;
}

export function hasReplacementAlternative(
  catalog: Catalog,
  ownedItems: OwnedItem[],
  currentItem: ItemRef,
  slot: Slot,
  fit: Fit | undefined,
  footwear: FootwearChoice,
): boolean {
  const categories = SLOT_CATEGORIES[slot];
  const current = currentItem.kind === "catalog" ? catalogItem(catalog, currentItem.id) : null;
  return (
    ownedItems.some((item) => item.id !== currentItem.id && categories.includes(item.category)) ||
    catalog.products.some(
      (product) =>
        categories.includes(product.category) &&
        meetsFootwearChoice(product, footwear) &&
        product.variants.some(
          (variant) =>
            variant.id !== currentItem.id &&
            // Another size of the same product and color is not another item.
            !(current?.product.id === product.id && current.variant.color === variant.color) &&
            variant.offers.length > 0 &&
            fitsCatalogVariant(product, variant, fit),
        ),
    )
  );
}

/** A check's text for a person: product names for catalog IDs, and looks and positions in words.
 * The server's text keeps the IDs because a repair prompt needs them. */
export function readableDetail(
  check: { rule: string | null; detail: string },
  catalog: Catalog | null,
  ownedItems: OwnedItem[] = [],
): string {
  if (check.rule === "unknown_item") return "An item in this plan is no longer in the catalog.";
  const names = new Map<string, string>(ownedItems.map((item) => [item.id, `your ${item.description}`]));
  for (const product of catalog?.products ?? []) {
    for (const variant of product.variants) {
      names.set(variant.id, product.title);
      for (const offer of variant.offers) names.set(offer.id, `The offer for ${product.title}`);
    }
  }
  // Longest first, because an offer's ID starts with its variant's.
  let text = check.detail;
  for (const id of [...names.keys()].sort((a, b) => b.length - a.length)) {
    text = text.replaceAll(id, names.get(id) ?? id);
  }
  return text
    .replace(/\blook look_(\d)/g, "look $1")
    .replace(/\blook_(\d)/g, "look $1")
    .replace(/\b(one|mid|outer)_(piece|layer)\b/g, "$1 $2")
    .replace(" is locked in ", " is kept in ");
}

function sizeList(text: string): string[] {
  return [...new Set(text.split(",").map((size) => size.replace(/\s+/g, "")).filter(Boolean))];
}

type FitResult = { ok: true; fit: Fit | undefined } | { ok: false; error: string };

export function parseFit(draft: FitDraft): FitResult {
  const waistSizes = sizeList(draft.waistSizes);
  const invalidWaist = waistSizes.find((size) => !WAIST_SIZE.test(size));
  if (invalidWaist !== undefined) {
    return {
      ok: false,
      error: `${invalidWaist} is not a waist size. Enter waists separated by commas, each as a waist or waist/length, such as 33, 34/32.`,
    };
  }
  const shoeSizes = sizeList(draft.shoeSizes);
  const invalidShoeSize = shoeSizes.find((size) => !EU_SHOE_SIZE.test(size));
  if (invalidShoeSize !== undefined) {
    return {
      ok: false,
      error: `${invalidShoeSize} is not an EU shoe size. Enter whole or half sizes separated by commas, such as 39.5, 40.`,
    };
  }
  const sizes: NormalizedSize[] = [
    ...(draft.letterSize ? [{ system: "letter" as const, value: draft.letterSize }] : []),
    ...waistSizes.map((value) => ({ system: "waist_length" as const, value })),
    ...shoeSizes.map((value) => ({ system: "eu_shoe" as const, value })),
  ];
  if (sizes.length > MAX_FIT_SIZES) {
    return {
      ok: false,
      error: `Enter no more than ${MAX_FIT_SIZES} sizes in total; you entered ${sizes.length}.`,
    };
  }
  const fit: Fit = {};
  if (draft.lines.length > 0) fit.lines = draft.lines;
  if (draft.garmentTypes.length > 0) fit.garment_types = draft.garmentTypes;
  if (sizes.length > 0) fit.sizes = sizes;
  return { ok: true, fit: Object.keys(fit).length > 0 ? fit : undefined };
}

/** The form's fit fields for a fit that a saved plan stored. */
export function fitDraftFrom(fit: Fit | undefined): FitDraft {
  const sizes = fit?.sizes ?? [];
  const letter = sizes.find((size) => size.system === "letter")?.value;
  return {
    lines: fit?.lines ?? [],
    garmentTypes: fit?.garment_types ?? [],
    letterSize: LETTER_SIZES.find((size) => size === letter) ?? "",
    waistSizes: sizes.filter((size) => size.system === "waist_length").map((size) => size.value).join(", "),
    shoeSizes: sizes.filter((size) => size.system === "eu_shoe").map((size) => size.value).join(", "),
  };
}

export function selectedOffer(plan: Plan, variant: Variant): Offer | null {
  const offerId = plan.selected_offers[variant.id];
  return variant.offers.find((offer) => offer.id === offerId) ?? null;
}

export function sameItem(left: ItemRef | undefined, right: ItemRef | undefined): boolean {
  return left?.kind === right?.kind && left?.id === right?.id;
}

export function isExactReplacement(
  previous: Plan,
  next: Plan,
  target: ReplacementTarget,
): boolean {
  const oldTargetLook = previous.looks.find((look) => look.id === target.look_id);
  const newTargetLook = next.looks.find((look) => look.id === target.look_id);
  const oldItem = oldTargetLook?.items[target.slot];
  const newItem = newTargetLook?.items[target.slot];
  if (!oldTargetLook || !newTargetLook || !newItem || sameItem(oldItem, newItem)) return false;

  return previous.looks.every((oldLook) => {
    const newLook = next.looks.find((look) => look.id === oldLook.id);
    return Boolean(
      newLook &&
        SLOTS.every(
          (slot) =>
            (oldLook.id === target.look_id && slot === target.slot) ||
            sameItem(oldLook.items[slot], newLook.items[slot]),
        ),
    );
  });
}

export function locksForReplacement(
  plan: Plan,
  target: ReplacementTarget,
  currentLocks: LockedPlacement[],
): LockedPlacement[] {
  const locks = new Map<string, LockedPlacement>();
  const add = (lock: LockedPlacement) => {
    const key = `${lock.look_id}:${lock.slot}:${lock.item.kind}:${lock.item.id}`;
    locks.set(key, lock);
  };
  currentLocks.forEach(add);
  for (const look of plan.looks) {
    for (const slot of SLOTS) {
      const item = look.items[slot];
      if (item && !(look.id === target.look_id && slot === target.slot)) {
        add({ look_id: look.id, slot, item });
      }
    }
  }
  return [...locks.values()];
}

export function lookCost(look: Look, plan: Plan, catalog: Catalog): number | null {
  let total = 0;
  for (const item of Object.values(look.items)) {
    if (!item || item.kind === "owned") continue;
    const entry = catalogItem(catalog, item.id);
    const offer = entry ? selectedOffer(plan, entry.variant) : null;
    if (!offer || offer.price_cents === null) return null;
    total += offer.price_cents;
  }
  return total;
}

export interface Preferences {
  styleGoal: string;
  colors: string[];
  occasion: string;
  season: string;
  budgetAmount: string;
  budgetMode: BudgetMode;
  footwear: FootwearChoice;
  exclusions: Material[];
  fit: FitDraft;
  ownedItems: OwnedItem[];
}

/** Changes whenever a preference that a plan request sends changes. */
export function preferenceKey(preferences: Preferences): string {
  return JSON.stringify({
    ...preferences,
    colors: preferences.colors.map((color) => color.trim()).filter(Boolean),
    styleGoal: preferences.styleGoal.trim(),
    amount_cents: Math.round(Number(preferences.budgetAmount) * 100),
    exclusions: [...preferences.exclusions].sort(),
  });
}

/** One line per distinct catalog variant and owned item: a variant used in several looks is
 * bought once, and an owned item costs nothing. */
export function shoppingList(plan: Plan, catalog: Catalog, ownedItems: OwnedItem[]): ShoppingLine[] {
  const lines = new Map<string, ShoppingLine>();
  for (const look of plan.looks) {
    for (const slot of SLOTS) {
      const item = look.items[slot];
      if (!item) continue;
      const key = `${item.kind}:${item.id}`;
      const existing = lines.get(key);
      if (existing) {
        if (!existing.looks.includes(lookLabel(look.id))) existing.looks.push(lookLabel(look.id));
        continue;
      }
      if (item.kind === "owned") {
        const owned = ownedItems.find((candidate) => candidate.id === item.id);
        lines.set(key, {
          key,
          kind: "owned",
          name: owned?.description ?? "Your item",
          details: owned ? `${owned.color}, ${labelCategory(owned.category)}` : labelSlot(slot),
          looks: [lookLabel(look.id)],
          price_cents: 0,
          retailer: null,
          url: null,
          checked_at: null,
          stock: null,
        });
        continue;
      }
      const entry = catalogItem(catalog, item.id);
      const offer = entry ? selectedOffer(plan, entry.variant) : null;
      lines.set(key, {
        key,
        kind: "catalog",
        name: entry?.product.title ?? item.id,
        details: entry
          ? [entry.product.brand, entry.variant.color, entry.variant.size && `Size ${entry.variant.size}`]
              .filter((value): value is string => Boolean(value))
              .join(", ")
          : "Catalog item unavailable",
        looks: [lookLabel(look.id)],
        price_cents: offer?.price_cents ?? null,
        retailer: offer?.retailer ?? null,
        url: offer && /^https:\/\//i.test(offer.url) ? offer.url : null,
        checked_at: offer?.source.checked_at ?? null,
        stock: offer?.stock ?? null,
      });
    }
  }
  return [...lines.values()];
}

export function shoppingTotal(lines: ShoppingLine[]): number | null {
  let total = 0;
  for (const line of lines) {
    if (line.price_cents === null) return null;
    total += line.price_cents;
  }
  return total;
}

export function shoppingListText(lines: ShoppingLine[], formatMoney: (cents: number | null) => string): string {
  const rows = lines.map((line) =>
    [
      `- ${line.name} (${line.details})`,
      line.kind === "owned" ? "already owned, EUR 0" : formatMoney(line.price_cents),
      line.retailer && line.url ? `${line.retailer}: ${line.url}` : null,
      line.checked_at ? formatCheckedDate(line.checked_at) : null,
      line.kind === "catalog" ? stockText(line.stock) : null,
      `in ${line.looks.join(", ")}`,
    ]
      .filter(Boolean)
      .join(" | "),
  );
  return [
    "FullAura shopping list",
    ...rows,
    `Items total: ${formatMoney(shoppingTotal(lines))}. Delivery and returns are not included; check each shop before buying.`,
  ].join("\n");
}
