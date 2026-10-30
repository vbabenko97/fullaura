import { EU_SHOE_SIZE, MAX_FIT_SIZES, SLOT_CATEGORIES, SLOTS, WAIST_SIZE } from "./types";
import type {
  BudgetMode,
  Catalog,
  Fit,
  FitDraft,
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

export function hasReplacementAlternative(
  catalog: Catalog,
  ownedItems: OwnedItem[],
  currentItem: ItemRef,
  slot: Slot,
  fit: Fit | undefined,
): boolean {
  const categories = SLOT_CATEGORIES[slot];
  return (
    ownedItems.some((item) => item.id !== currentItem.id && categories.includes(item.category)) ||
    catalog.products.some(
      (product) =>
        categories.includes(product.category) &&
        product.variants.some(
          (variant) =>
            variant.id !== currentItem.id &&
            variant.offers.length > 0 &&
            fitsCatalogVariant(product, variant, fit),
        ),
    )
  );
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

export function wholePlanCost(plan: Plan, catalog: Catalog): number | null {
  const variantIds = new Set(
    plan.looks.flatMap((look) =>
      Object.values(look.items).flatMap((item) =>
        item?.kind === "catalog" ? [item.id] : [],
      ),
    ),
  );
  let total = 0;
  for (const variantId of variantIds) {
    const entry = catalogItem(catalog, variantId);
    const offer = entry ? selectedOffer(plan, entry.variant) : null;
    if (!offer || offer.price_cents === null) return null;
    total += offer.price_cents;
  }
  return total;
}

export function totalLookCosts(plan: Plan, catalog: Catalog): number | null {
  let total = 0;
  for (const look of plan.looks) {
    const cost = lookCost(look, plan, catalog);
    if (cost === null) return null;
    total += cost;
  }
  return total;
}

export function preferenceKey(
  styleGoal: string,
  colors: string[],
  budgetAmount: string,
  budgetMode: BudgetMode,
  exclusions: Material[],
  fit: FitDraft,
  ownedItems: OwnedItem[],
): string {
  return JSON.stringify({
    style_goal: styleGoal.trim(),
    preferred_colors: colors,
    amount_cents: Math.round(Number(budgetAmount) * 100),
    budget_mode: budgetMode,
    material_exclusions: [...exclusions].sort(),
    fit,
    owned_items: ownedItems,
  });
}
