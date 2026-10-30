import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

const MATERIALS = [
  "acetate",
  "acrylic",
  "cashmere",
  "cork",
  "cotton",
  "elastane",
  "ethylene-vinyl acetate",
  "leather",
  "linen",
  "lyocell",
  "metal",
  "modal",
  "polyamide",
  "polyester",
  "polyurethane",
  "rubber",
  "silk",
  "synthetic rubber",
  "viscose",
  "wool",
] as const;

const CATEGORIES = [
  "top",
  "bottom",
  "one_piece",
  "knitwear",
  "outerwear",
  "footwear",
  "accessory",
] as const;

const SLOTS = [
  "top",
  "bottom",
  "one_piece",
  "mid_layer",
  "outer_layer",
  "footwear",
  "accessory",
] as const;

const SLOT_CATEGORIES: Record<Slot, readonly Category[]> = {
  top: ["top", "knitwear"],
  bottom: ["bottom"],
  one_piece: ["one_piece"],
  mid_layer: ["top", "knitwear"],
  outer_layer: ["outerwear"],
  footwear: ["footwear"],
  accessory: ["accessory"],
};

const FIT_LINES = ["men", "women"] as const;
const PRODUCT_LINES = [...FIT_LINES, "unisex"] as const;
const GARMENT_TYPES = ["trousers", "jeans", "shorts", "skirt", "leggings", "dress"] as const;
const LETTER_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"] as const;
const SIZE_SYSTEMS = ["letter", "waist_length", "eu_shoe"] as const;
const MAX_FIT_SIZES = 12;
const WAIST_SIZE = /^\d{2}(\/\d{2})?$/;
const EU_SHOE_SIZE = /^\d{2}(\.5)?$/;

type Material = (typeof MATERIALS)[number];
type Category = (typeof CATEGORIES)[number];
type Slot = (typeof SLOTS)[number];
type FitLine = (typeof FIT_LINES)[number];
type GarmentType = (typeof GARMENT_TYPES)[number];
type LetterSize = (typeof LETTER_SIZES)[number];

const LINE_LABELS: Record<FitLine, string> = { men: "Men's", women: "Women's" };
const GARMENT_TYPE_LABELS: Record<GarmentType, string> = {
  trousers: "Trousers",
  jeans: "Jeans",
  shorts: "Shorts",
  skirt: "Skirts",
  leggings: "Leggings",
  dress: "Dresses",
};

interface NormalizedSize {
  system: (typeof SIZE_SYSTEMS)[number];
  value: string;
}

/** Whom the looks are for, as the person states it. Fields that are not set are omitted. */
interface Fit {
  lines?: FitLine[];
  garment_types?: GarmentType[];
  sizes?: NormalizedSize[];
}

interface FitDraft {
  lines: FitLine[];
  garmentTypes: GarmentType[];
  letterSize: LetterSize | "";
  waistSizes: string;
  shoeSizes: string;
}

const EMPTY_FIT_DRAFT: FitDraft = {
  lines: [],
  garmentTypes: [],
  letterSize: "",
  waistSizes: "",
  shoeSizes: "",
};
type BudgetMode = "per_look" | "whole_plan";
type ItemKind = "catalog" | "owned";

interface Source {
  url: string;
  checked_at: string;
}

interface Offer {
  id: string;
  retailer: string;
  url: string;
  price_cents: number | null;
  currency: "EUR";
  stock: "in_stock" | "out_of_stock" | null;
  source: Source;
}

interface Component {
  material: string;
  percent: number | null;
  part: string | null;
}

interface Variant {
  id: string;
  color: string | null;
  size: string | null;
  normalized_size: NormalizedSize | null;
  composition: {
    status: "complete" | "partial" | "unknown" | "conflicting";
    components: Component[];
    source_text: string | null;
  };
  source: Source;
  offers: Offer[];
}

interface Product {
  id: string;
  title: string;
  brand: string | null;
  category: Category;
  line: (typeof PRODUCT_LINES)[number] | null;
  garment_type: GarmentType | null;
  variants: Variant[];
}

interface Catalog {
  id: string;
  synthetic: boolean;
  products: Product[];
}

interface OwnedItem {
  id: string;
  category: Category;
  color: string;
  description: string;
}

interface ItemRef {
  kind: ItemKind;
  id: string;
}

interface Look {
  id: string;
  items: Partial<Record<Slot, ItemRef>>;
}

interface Plan {
  looks: Look[];
  selected_offers: Record<string, string>;
}

interface LockedPlacement {
  look_id: string;
  slot: Slot;
  item: ItemRef;
}

type ReplacementTarget = LockedPlacement;

interface PlanResponse {
  outcome: "success" | "infeasible" | "generation_failed" | "provider_timeout" | "provider_error";
  plan: Plan | null;
  conflicts: { rule: string | null; detail: string }[];
  violations: { rule: string; detail: string }[];
  error: string | null;
  notices: OwnedNotice[];
}

interface PreviewStatus {
  enabled: boolean;
  detail: string | null;
}

interface DemoSession {
  enabled: boolean;
  age_confirmed: boolean;
  plan_requests_used: number;
  plan_requests_limit: number;
  plan_requests_remaining: number;
}

interface PreviewOption {
  id: string;
  label: string;
  prompt: string;
  sourceKey: string;
}

interface PreviewResult {
  url: string;
  optionId: string;
  sourceKey: string;
  portrait: File;
  fullBody: File;
}

interface OwnedNotice {
  kind: "owned_reported" | "owned_unconfirmed";
  item: ItemRef;
  material: string;
}

interface PlanRequest {
  brief: {
    style_goal: string;
    preferred_colors: string[];
  };
  replacement: ReplacementTarget | null;
  state: {
    budget: {
      amount_cents: number;
      mode: BudgetMode;
    };
    material_exclusions: Material[];
    fit?: Fit;
    owned_items: OwnedItem[];
    locks: LockedPlacement[];
    plan: Plan | null;
  };
}

type OwnedItemDraft = OwnedItem;

type CatalogState = "loading" | "ready" | "error" | "disabled";
type SessionState = "loading" | "ready" | "error";
type RequestState = "idle" | "loading" | "success" | "error";
type PreviewStatusState = "loading" | "ready" | "error";

class ApiResponseError extends Error {
  constructor(readonly status: number, readonly detail: string | null = null) {
    super(detail ?? `API request failed with status ${status}`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(`Invalid ${field} in API response`);
  return value;
}

function readNullableString(value: unknown, field: string): string | null {
  return value === null ? null : readString(value, field);
}

function readNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Invalid ${field} in API response`);
  }
  return value;
}

function readNullableNumber(value: unknown, field: string): number | null {
  return value === null ? null : readNumber(value, field);
}

function readArray(value: unknown, field: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`Invalid ${field} in API response`);
  return value;
}

function readSource(value: unknown): Source {
  if (!isRecord(value)) throw new Error("Invalid source in catalog response");
  return {
    url: readString(value.url, "source URL"),
    checked_at: readString(value.checked_at, "source date"),
  };
}

function readCategory(value: unknown): Category {
  if (typeof value === "string") {
    const category = CATEGORIES.find((candidate) => candidate === value);
    if (category) return category;
  }
  throw new Error("Invalid product category in API response");
}

function readChoice<T extends string>(value: unknown, choices: readonly T[], field: string): T {
  const choice = choices.find((candidate) => candidate === value);
  if (choice === undefined) throw new Error(`Invalid ${field} in catalog response`);
  return choice;
}

function readNormalizedSize(value: unknown): NormalizedSize | null {
  // minimal: a missing fit field reads as null, so a catalog written before fit fields still loads
  if (value == null) return null;
  if (!isRecord(value)) throw new Error("Invalid normalized size in catalog response");
  return {
    system: readChoice(value.system, SIZE_SYSTEMS, "size system"),
    value: readString(value.value, "normalized size"),
  };
}

function readOffer(value: unknown): Offer {
  if (!isRecord(value)) throw new Error("Invalid offer in catalog response");
  const stock = value.stock;
  if (stock !== null && stock !== "in_stock" && stock !== "out_of_stock") {
    throw new Error("Invalid stock status in catalog response");
  }
  if (value.currency !== "EUR") throw new Error("Invalid currency in catalog response");
  return {
    id: readString(value.id, "offer ID"),
    retailer: readString(value.retailer, "retailer"),
    url: readString(value.url, "offer URL"),
    price_cents: readNullableNumber(value.price_cents, "price"),
    currency: "EUR",
    stock,
    source: readSource(value.source),
  };
}

function readVariant(value: unknown): Variant {
  if (!isRecord(value) || !isRecord(value.composition)) {
    throw new Error("Invalid variant in catalog response");
  }
  const composition = value.composition;
  const status = composition.status;
  if (status !== "complete" && status !== "partial" && status !== "unknown" && status !== "conflicting") {
    throw new Error("Invalid composition status in catalog response");
  }
  const components = readArray(composition.components, "composition components").map((entry) => {
    if (!isRecord(entry)) throw new Error("Invalid composition component in catalog response");
    return {
      material: readString(entry.material, "material"),
      percent: readNullableNumber(entry.percent, "material percentage"),
      part: readNullableString(entry.part, "material part"),
    };
  });
  return {
    id: readString(value.id, "variant ID"),
    color: readNullableString(value.color, "variant color"),
    size: readNullableString(value.size, "variant size"),
    normalized_size: readNormalizedSize(value.normalized_size),
    composition: {
      status,
      components,
      source_text: readNullableString(composition.source_text, "composition source text"),
    },
    source: readSource(value.source),
    offers: readArray(value.offers, "offers").map(readOffer),
  };
}

function parseCatalog(value: unknown): Catalog {
  if (!isRecord(value) || typeof value.synthetic !== "boolean") {
    throw new Error("Invalid catalog response");
  }
  const catalog: Catalog = {
    id: readString(value.id, "catalog ID"),
    synthetic: value.synthetic,
    products: readArray(value.products, "products").map((entry) => {
      if (!isRecord(entry)) throw new Error("Invalid product in catalog response");
      return {
        id: readString(entry.id, "product ID"),
        title: readString(entry.title, "product title"),
        brand: readNullableString(entry.brand, "product brand"),
        category: readCategory(entry.category),
        line: entry.line == null ? null : readChoice(entry.line, PRODUCT_LINES, "product line"),
        garment_type:
          entry.garment_type == null
            ? null
            : readChoice(entry.garment_type, GARMENT_TYPES, "garment type"),
        variants: readArray(entry.variants, "variants").map(readVariant),
      };
    }),
  };
  if (catalog.synthetic || catalog.products.length === 0) {
    throw new Error("The development catalog is empty or synthetic");
  }
  return catalog;
}

function parseItemRef(value: unknown): ItemRef {
  if (!isRecord(value) || (value.kind !== "catalog" && value.kind !== "owned")) {
    throw new Error("Invalid plan item in API response");
  }
  return { kind: value.kind, id: readString(value.id, "plan item ID") };
}

function parsePlan(value: unknown): Plan {
  if (!isRecord(value)) throw new Error("Invalid plan in API response");
  const selectedOffers = value.selected_offers;
  if (!isRecord(selectedOffers)) throw new Error("Invalid selected offers in API response");
  const offerEntries = Object.entries(selectedOffers).map(([variantId, offerId]) => [
    variantId,
    readString(offerId, "selected offer ID"),
  ]);
  const looks = readArray(value.looks, "looks").map((entry) => {
    if (!isRecord(entry) || !isRecord(entry.items)) throw new Error("Invalid look in API response");
    const items: Partial<Record<Slot, ItemRef>> = {};
    for (const [slot, item] of Object.entries(entry.items)) {
      const knownSlot = SLOTS.find((candidate) => candidate === slot);
      if (!knownSlot) throw new Error("Invalid look slot in API response");
      items[knownSlot] = parseItemRef(item);
    }
    return { id: readString(entry.id, "look ID"), items };
  });
  if (looks.length !== 3 || new Set(looks.map((look) => look.id)).size !== 3) {
    throw new Error("The planning API returned something other than three distinct looks");
  }
  return {
    looks,
    selected_offers: Object.fromEntries(offerEntries),
  };
}

function parsePlanResponse(value: unknown): PlanResponse {
  if (!isRecord(value)) throw new Error("Invalid planning response");
  const outcome = value.outcome;
  if (
    outcome !== "success" &&
    outcome !== "infeasible" &&
    outcome !== "generation_failed" &&
    outcome !== "provider_timeout" &&
    outcome !== "provider_error"
  ) {
    throw new Error("Unknown planning outcome");
  }
  const conflicts = readArray(value.conflicts, "planning conflicts").map((entry) => {
    if (!isRecord(entry)) throw new Error("Invalid planning conflict");
    return {
      rule: entry.rule == null ? null : readString(entry.rule, "conflict rule"),
      detail: readString(entry.detail, "conflict detail"),
    };
  });
  const violations = readArray(value.violations ?? [], "planning violations").map((entry) => {
    if (!isRecord(entry)) throw new Error("Invalid planning violation");
    return {
      rule: readString(entry.rule, "planning violation rule"),
      detail: readString(entry.detail, "planning violation detail"),
    };
  });
  const error = value.error == null ? null : readString(value.error, "planning error");
  const notices = readArray(value.notices ?? [], "planning notices").map((entry) => {
    if (!isRecord(entry) || (entry.kind !== "owned_reported" && entry.kind !== "owned_unconfirmed")) {
      throw new Error("Invalid planning notice");
    }
    const item = parseItemRef(entry.item);
    if (item.kind !== "owned") throw new Error("Invalid planning notice item");
    const kind: OwnedNotice["kind"] = entry.kind;
    return { kind, item, material: readString(entry.material, "planning notice material") };
  });
  return {
    outcome,
    plan: value.plan === null ? null : parsePlan(value.plan),
    conflicts,
    violations,
    error,
    notices,
  };
}

function parsePreviewStatus(value: unknown): PreviewStatus {
  if (!isRecord(value) || typeof value.enabled !== "boolean") {
    throw new Error("Invalid preview status response");
  }
  return {
    enabled: value.enabled,
    detail: readNullableString(value.detail, "preview status detail"),
  };
}

function parseDemoSession(value: unknown): DemoSession {
  if (
    !isRecord(value) ||
    typeof value.enabled !== "boolean" ||
    typeof value.age_confirmed !== "boolean"
  ) {
    throw new Error("Invalid demo session response");
  }
  const planRequestsUsed = readNumber(value.plan_requests_used, "used plan request count");
  const planRequestsLimit = readNumber(value.plan_requests_limit, "plan request limit");
  const planRequestsRemaining = readNumber(value.plan_requests_remaining, "remaining plan request count");
  if (
    !Number.isInteger(planRequestsUsed) ||
    !Number.isInteger(planRequestsLimit) ||
    !Number.isInteger(planRequestsRemaining) ||
    planRequestsUsed < 0 ||
    planRequestsLimit < 1 ||
    planRequestsRemaining < 0 ||
    planRequestsRemaining > planRequestsLimit ||
    planRequestsUsed + planRequestsRemaining !== planRequestsLimit
  ) {
    throw new Error("Invalid demo session request limits");
  }
  return {
    enabled: value.enabled,
    age_confirmed: value.age_confirmed,
    plan_requests_used: planRequestsUsed,
    plan_requests_limit: planRequestsLimit,
    plan_requests_remaining: planRequestsRemaining,
  };
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiResponseError && error.status === 401) {
    return "The demo session expired. Refresh the page to start another session.";
  }
  if (error instanceof ApiResponseError && error.status === 403) {
    return error.detail ?? "Confirm that you are 18 or older before requesting a plan.";
  }
  if (error instanceof ApiResponseError && error.status === 429) {
    return error.detail ?? "This demo session has reached its planning request limit.";
  }
  if (error instanceof ApiResponseError && error.status === 503) {
    return error.detail ?? "The planning model is not configured.";
  }
  if (error instanceof ApiResponseError && error.status === 404) {
    return "Planning is available only when the backend runs in development mode.";
  }
  return "The request could not be completed. Check the backend and try again.";
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) {
    let detail: string | null = null;
    try {
      const payload: unknown = await response.json();
      if (isRecord(payload) && typeof payload.detail === "string") detail = payload.detail;
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new ApiResponseError(response.status, detail);
  }
  const payload: unknown = await response.json();
  return payload;
}

function formatMoney(cents: number | null): string {
  if (cents === null) return "Price not reported";
  return new Intl.NumberFormat("en-AT", { style: "currency", currency: "EUR" }).format(cents / 100);
}

function formatCheckedDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Source date not available";
  return `Price checked ${new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" }).format(date)}`;
}

function labelCategory(category: Category): string {
  return category.replaceAll("_", " ");
}

function labelSlot(slot: Slot): string {
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

function catalogItem(catalog: Catalog, variantId: string): { product: Product; variant: Variant } | null {
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

function hasReplacementAlternative(
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

function parseFit(draft: FitDraft): FitResult {
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
      error: `${invalidShoeSize} is not an EU shoe size. Enter whole or half sizes separated by commas, such as 42.5, 43.`,
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

// Keeps the order of choices, so the same selection always produces the same request.
function toggled<T>(choices: readonly T[], selected: readonly T[], value: T): T[] {
  return choices.filter((choice) =>
    choice === value ? !selected.includes(choice) : selected.includes(choice),
  );
}

function selectedOffer(plan: Plan, variant: Variant): Offer | null {
  const offerId = plan.selected_offers[variant.id];
  return variant.offers.find((offer) => offer.id === offerId) ?? null;
}

function sameItem(left: ItemRef | undefined, right: ItemRef | undefined): boolean {
  return left?.kind === right?.kind && left?.id === right?.id;
}

function isExactReplacement(
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

function locksForReplacement(
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

function lookCost(look: Look, plan: Plan, catalog: Catalog): number | null {
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

function wholePlanCost(plan: Plan, catalog: Catalog): number | null {
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

function totalLookCosts(plan: Plan, catalog: Catalog): number | null {
  let total = 0;
  for (const look of plan.looks) {
    const cost = lookCost(look, plan, catalog);
    if (cost === null) return null;
    total += cost;
  }
  return total;
}

function preferenceKey(
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

interface ProductLineProps {
  catalog: Catalog;
  look: Look;
  plan: Plan;
  slot: Slot;
  item: ItemRef;
  ownedItems: OwnedItem[];
  ownedNotices: OwnedNotice[];
  fit: Fit | undefined;
  isLocked: boolean;
  lockDisabled: boolean;
  replaceDisabled: boolean;
  onToggleLock: (lookId: string, slot: Slot, item: ItemRef) => void;
  onReplaceItem: (lookId: string, slot: Slot, item: ItemRef) => void;
}

function ProductLine({
  catalog,
  look,
  plan,
  slot,
  item,
  ownedItems,
  ownedNotices,
  fit,
  isLocked,
  lockDisabled,
  replaceDisabled,
  onToggleLock,
  onReplaceItem,
}: ProductLineProps) {
  const hasAlternative = hasReplacementAlternative(catalog, ownedItems, item, slot, fit);
  if (item.kind === "owned") {
    const owned = ownedItems.find((candidate) => candidate.id === item.id);
    const notices = ownedNotices.filter((notice) => notice.item.id === item.id);
    return (
      <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
          <div className="look-item-actions">
            <button
              aria-label={`Replace ${owned?.description ?? "owned item"} in ${look.id}`}
              className="secondary-button replace-button"
              disabled={replaceDisabled || isLocked || !hasAlternative}
              onClick={() => onReplaceItem(look.id, slot, item)}
              type="button"
            >
              Replace
            </button>
            <button
              aria-label={`${isLocked ? "Release" : "Keep"} ${owned?.description ?? "owned item"} in ${look.id}`}
              aria-pressed={isLocked}
              className={`lock-button${isLocked ? " is-locked" : ""}`}
              disabled={lockDisabled}
              onClick={() => onToggleLock(look.id, slot, item)}
              type="button"
            >
              {isLocked ? "Kept" : "Keep item"}
            </button>
          </div>
        </div>
        {!hasAlternative && <p className="item-facts">No other catalog or owned item can fill this position.</p>}
        <h3>{owned?.description ?? "Your item"}</h3>
        <p className="item-facts">
          {owned ? `${owned.color}, ${labelCategory(owned.category)}` : "Already owned"}
        </p>
        <p className="item-price">Already owned</p>
        {notices.map((notice) => (
          <p className="item-facts" key={`${notice.kind}-${notice.material}`}>
            {notice.kind === "owned_reported"
              ? `${notice.material} reported by you. It is excluded from new purchases.`
              : `${notice.material} is unconfirmed for this item.`}
          </p>
        ))}
      </li>
    );
  }

  const entry = catalogItem(catalog, item.id);
  if (!entry) {
    return (
      <li className="look-item">
        <span className="slot-name">{labelSlot(slot)}</span>
        <p>Catalog item unavailable</p>
      </li>
    );
  }

  const offer = selectedOffer(plan, entry.variant);
  const materials = [...new Set(entry.variant.composition.components.map((component) => component.material))];
  const composition =
    entry.variant.composition.status === "complete"
      ? `Materials: ${materials.join(", ")}`
      : entry.variant.composition.status === "partial"
        ? "Composition is incomplete"
        : entry.variant.composition.status === "conflicting"
          ? "Sources disagree on composition"
          : "Composition not reported";
  const facts = [
    entry.product.brand,
    entry.variant.color,
    entry.variant.size === null ? null : `Size ${entry.variant.size}`,
  ]
    .filter((value): value is string => value !== null)
    .join(", ");
  const offerLink = offer && /^https:\/\//i.test(offer.url) ? offer.url : null;

  return (
    <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
        <div className="look-item-actions">
          <button
            aria-label={`Replace ${entry.product.title} in ${look.id}`}
            className="secondary-button replace-button"
            disabled={replaceDisabled || isLocked || !hasAlternative}
            onClick={() => onReplaceItem(look.id, slot, item)}
            type="button"
          >
            Replace
          </button>
          <button
            aria-label={`${isLocked ? "Release" : "Keep"} ${entry.product.title} in ${look.id}`}
            aria-pressed={isLocked}
            className={`lock-button${isLocked ? " is-locked" : ""}`}
            disabled={lockDisabled}
            onClick={() => onToggleLock(look.id, slot, item)}
            type="button"
          >
            {isLocked ? "Kept" : "Keep item"}
          </button>
        </div>
      </div>
      {!hasAlternative && <p className="item-facts">No other catalog or owned item can fill this position.</p>}
      <h3>{entry.product.title}</h3>
      {facts && <p className="item-facts">{facts}</p>}
      <p className="item-facts">{composition}</p>
      {offer?.stock === null && <p className="item-facts">Stock unknown</p>}
      <div className="item-source">
        <span className="item-price">{formatMoney(offer?.price_cents ?? null)}</span>
        {offerLink ? (
          <a href={offerLink} rel="noreferrer" target="_blank">
            {offer?.retailer ?? "Source"}
          </a>
        ) : (
          <span>Source unavailable</span>
        )}
      </div>
      {offer && <p className="source-date">{formatCheckedDate(offer.source.checked_at)}</p>}
    </li>
  );
}

interface LookCardProps {
  catalog: Catalog;
  look: Look;
  plan: Plan;
  ownedItems: OwnedItem[];
  ownedNotices: OwnedNotice[];
  fit: Fit | undefined;
  locks: LockedPlacement[];
  lockDisabled: boolean;
  replaceDisabled: boolean;
  onToggleLock: (lookId: string, slot: Slot, item: ItemRef) => void;
  onReplaceItem: (lookId: string, slot: Slot, item: ItemRef) => void;
}

function LookCard({
  catalog,
  look,
  plan,
  ownedItems,
  ownedNotices,
  fit,
  locks,
  lockDisabled,
  replaceDisabled,
  onToggleLock,
  onReplaceItem,
}: LookCardProps) {
  const items = SLOTS.flatMap((slot) => {
    const item = look.items[slot];
    return item ? [{ slot, item }] : [];
  });

  return (
    <article className="look-card" aria-labelledby={`${look.id}-title`}>
      <header className="look-card-header">
        <h2 id={`${look.id}-title`}>{look.id.replaceAll("_", " ")}</h2>
        <p className="look-total">{formatMoney(lookCost(look, plan, catalog))}</p>
      </header>
      <ul className="look-items">
        {items.map(({ slot, item }) => {
          const isLocked = locks.some(
            (lock) =>
              lock.look_id === look.id &&
              lock.slot === slot &&
              lock.item.id === item.id &&
              lock.item.kind === item.kind,
          );
          return (
            <ProductLine
              key={`${look.id}-${slot}`}
              catalog={catalog}
              fit={fit}
              isLocked={isLocked}
              item={item}
              lockDisabled={lockDisabled}
              replaceDisabled={replaceDisabled}
              look={look}
              onToggleLock={onToggleLock}
              onReplaceItem={onReplaceItem}
              ownedItems={ownedItems}
              ownedNotices={ownedNotices}
              plan={plan}
              slot={slot}
            />
          );
        })}
      </ul>
    </article>
  );
}

function previewOptionsForPlan(
  plan: Plan,
  catalog: Catalog,
  ownedItems: OwnedItem[],
): PreviewOption[] {
  return plan.looks.map((look) => {
    const describedItems = SLOTS.flatMap((slot) => {
      const item = look.items[slot];
      if (!item) return [];
      if (item.kind === "owned") {
        const owned = ownedItems.find((candidate) => candidate.id === item.id);
        return owned
          ? [`${labelSlot(slot).toLowerCase()}: ${owned.color} ${owned.description}`]
          : [];
      }
      const entry = catalogItem(catalog, item.id);
      if (!entry) return [];
      const details = [entry.product.brand, entry.product.title, entry.variant.color]
        .filter((value): value is string => value !== null)
        .join(" ");
      return [`${labelSlot(slot).toLowerCase()}: ${details}`];
    });
    return {
      id: look.id,
      label: look.id.replaceAll("_", " "),
      sourceKey: JSON.stringify({
        items: SLOTS.map((slot) => [slot, look.items[slot] ?? null]),
        describedItems,
      }),
      prompt:
        `Create an approximate style preview of the person in the supplied portrait and full-body photos. ` +
        `Dress them in this outfit, using the text descriptions as references: ${describedItems.join("; ")}. ` +
        "Keep their identity, face, hair, skin tone and body proportions. Plain light background. " +
        "Do not infer sensitive traits. The result is an approximate preview, not proof of fit or product availability.",
    };
  });
}

interface PreviewPanelProps {
  enabled: boolean;
  detail: string | null;
  loadingStatus: boolean;
  options: PreviewOption[];
}

function PreviewPanel({ enabled, detail, loadingStatus, options }: PreviewPanelProps) {
  const [portrait, setPortrait] = useState<File | null>(null);
  const [fullBody, setFullBody] = useState<File | null>(null);
  const [lookId, setLookId] = useState(options[0]?.id ?? "");
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);
  const requestController = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => () => {
    requestVersion.current += 1;
    requestController.current?.abort();
  }, []);
  useEffect(() => {
    return () => {
      if (previewResult) URL.revokeObjectURL(previewResult.url);
    };
  }, [previewResult]);

  const selectedLook = options.find((option) => option.id === lookId);
  const previewUrl =
    previewResult &&
    selectedLook &&
    previewResult.optionId === selectedLook.id &&
    previewResult.sourceKey === selectedLook.sourceKey &&
    previewResult.portrait === portrait &&
    previewResult.fullBody === fullBody
      ? previewResult.url
      : null;

  function invalidatePreview() {
    requestVersion.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    setPreviewResult(null);
    setRequestState("idle");
    setMessage(null);
  }

  function setPhoto(setter: (photo: File | null) => void, file: File | undefined) {
    invalidatePreview();
    setter(file ?? null);
  }

  async function generatePreview() {
    if (!enabled || !selectedLook || !portrait || !fullBody) return;
    if ([portrait, fullBody].some((file) => file.type !== "image/jpeg" || file.size <= 0)) {
      setRequestState("error");
      setMessage("Choose two non-empty JPEG photos.");
      return;
    }
    if ([portrait, fullBody].some((file) => file.size > 10 * 1024 * 1024)) {
      setRequestState("error");
      setMessage("Each photo must be no larger than 10 MiB.");
      return;
    }

    requestVersion.current += 1;
    const requestId = requestVersion.current;
    setPreviewResult(null);
    const controller = new AbortController();
    requestController.current = controller;
    setRequestState("loading");
    setMessage(`Creating an approximate preview for ${selectedLook.label}.`);
    try {
      const form = new FormData();
      form.append("portrait", portrait);
      form.append("full_body", fullBody);
      form.append("prompt", selectedLook.prompt);
      const response = await fetch("/api/previews", {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      if (requestId !== requestVersion.current || controller.signal.aborted) return;
      if (!response.ok) {
        let responseMessage = `The preview request failed (${response.status}).`;
        try {
          const payload: unknown = await response.json();
          if (isRecord(payload) && typeof payload.detail === "string") {
            responseMessage = payload.detail;
          }
        } catch {
          // Keep the status-based message when the server did not return JSON.
        }
        throw new Error(responseMessage);
      }
      const image = await response.blob();
      if (requestId !== requestVersion.current || controller.signal.aborted) return;
      if (
        !["image/png", "image/jpeg", "image/webp"].includes(image.type) ||
        image.size === 0 ||
        image.size > 16 * 1024 * 1024
      ) {
        throw new Error("The preview response was not a supported image.");
      }
      setPreviewResult({
        url: URL.createObjectURL(image),
        optionId: selectedLook.id,
        sourceKey: selectedLook.sourceKey,
        portrait,
        fullBody,
      });
      setRequestState("success");
      setMessage("Approximate preview generated. It cannot establish fit or product availability.");
    } catch (error) {
      if (requestId !== requestVersion.current) return;
      setRequestState("error");
      setMessage(isRecord(error) && error.name === "AbortError"
        ? "Preview generation cancelled."
        : error instanceof Error
          ? error.message
          : "The preview request could not be completed.");
    } finally {
      if (requestId === requestVersion.current) requestController.current = null;
    }
  }

  function cancelPreview() {
    requestVersion.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    setPreviewResult(null);
    setRequestState("error");
    setMessage("Preview generation cancelled.");
  }

  function selectLook(nextLookId: string) {
    invalidatePreview();
    setLookId(nextLookId);
  }

  return (
    <section className="preview-panel" aria-labelledby="preview-title">
      <div className="preview-panel-heading">
        <div>
          <h2 id="preview-title">Approximate photo preview</h2>
          <p>
            Product images are unavailable, so the model uses text descriptions. A preview cannot
            establish fit, exact product details or availability.
          </p>
        </div>
      </div>
      <div className="preview-form">
        <label htmlFor="preview-portrait">Portrait photo</label>
        <input
          accept="image/jpeg"
          disabled={!enabled || requestState === "loading"}
          id="preview-portrait"
          onChange={(event) => setPhoto(setPortrait, event.currentTarget.files?.[0])}
          type="file"
        />
        <label htmlFor="preview-full-body">Full-body photo</label>
        <input
          accept="image/jpeg"
          disabled={!enabled || requestState === "loading"}
          id="preview-full-body"
          onChange={(event) => setPhoto(setFullBody, event.currentTarget.files?.[0])}
          type="file"
        />
        <label htmlFor="preview-look">Look to preview</label>
        <select
          disabled={!enabled || requestState === "loading"}
          id="preview-look"
          onChange={(event) => selectLook(event.target.value)}
          value={lookId}
        >
          {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
        <div className="preview-actions">
          {requestState === "loading" ? (
            <button className="secondary-button" onClick={cancelPreview} type="button">
              Cancel preview
            </button>
          ) : (
            <button
              className="secondary-button"
              disabled={!enabled || !portrait || !fullBody || options.length === 0}
              onClick={() => void generatePreview()}
              type="button"
            >
              Generate approximate preview
            </button>
          )}
        </div>
      </div>
      <p className="preview-privacy">
        {enabled
          ? "Metadata is removed before photos are sent to Meta. FullAura keeps the photos and result in memory only for this request; Meta’s current terms govern provider retention."
          : loadingStatus
            ? "Checking preview availability…"
            : detail ?? "Photo previews are unavailable."}
      </p>
      {message && (
        <p className={`preview-status${requestState === "error" ? " is-error" : ""}`} role={requestState === "error" ? "alert" : "status"}>
          {message}
        </p>
      )}
      {previewUrl && <img className="preview-image" src={previewUrl} alt={`Approximate generated preview for ${lookId.replaceAll("_", " ")}`} />}
    </section>
  );
}

export function App() {
  const [demoSession, setDemoSession] = useState<DemoSession | null>(null);
  const [sessionState, setSessionState] = useState<SessionState>("loading");
  const [over18Checked, setOver18Checked] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogState, setCatalogState] = useState<CatalogState>("loading");
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [styleGoal, setStyleGoal] = useState("");
  const [preferredColors, setPreferredColors] = useState("");
  const [budgetAmount, setBudgetAmount] = useState("");
  const [budgetMode, setBudgetMode] = useState<BudgetMode>("per_look");
  const [exclusions, setExclusions] = useState<Material[]>([]);
  const [fitDraft, setFitDraft] = useState<FitDraft>(EMPTY_FIT_DRAFT);
  const [ownedItems, setOwnedItems] = useState<OwnedItemDraft[]>([]);
  const [locks, setLocks] = useState<LockedPlacement[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planOwnedItems, setPlanOwnedItems] = useState<OwnedItem[]>([]);
  const [planFit, setPlanFit] = useState<Fit>();
  const [planNotices, setPlanNotices] = useState<OwnedNotice[]>([]);
  const [successfulPreferenceKey, setSuccessfulPreferenceKey] = useState<string | null>(null);
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [requestMessage, setRequestMessage] = useState<string | null>(null);
  const [previewStatus, setPreviewStatus] = useState<PreviewStatus>({ enabled: false, detail: null });
  const [previewStatusState, setPreviewStatusState] = useState<PreviewStatusState>("loading");

  useEffect(() => {
    const controller = new AbortController();
    async function loadDemoAndCatalog() {
      try {
        const sessionResponse = await fetch("/api/session", { signal: controller.signal });
        const session = parseDemoSession(await readJson(sessionResponse));
        setDemoSession(session);
        setSessionState("ready");
        if (!session.enabled) {
          setCatalogState("disabled");
          return;
        }
        const response = await fetch("/api/catalog", { signal: controller.signal });
        const loadedCatalog = parseCatalog(await readJson(response));
        setCatalog(loadedCatalog);
        setCatalogState("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        setCatalogError(errorMessage(error));
        setSessionState("error");
        setCatalogState("error");
      }
    }
    void loadDemoAndCatalog();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function loadPreviewStatus() {
      try {
        const response = await fetch("/api/previews/status", { signal: controller.signal });
        setPreviewStatus(parsePreviewStatus(await readJson(response)));
        setPreviewStatusState("ready");
      } catch {
        if (controller.signal.aborted) return;
        setPreviewStatus({ enabled: false, detail: "Preview availability could not be checked." });
        setPreviewStatusState("error");
      }
    }
    void loadPreviewStatus();
    return () => controller.abort();
  }, []);

  function toggleExclusion(material: Material) {
    setExclusions((current) =>
      current.includes(material)
        ? current.filter((item) => item !== material)
        : [...current, material],
    );
  }

  function addOwnedItem() {
    if (ownedItems.length >= 3) return;
    const nextNumber =
      Math.max(0, ...ownedItems.map((item) => Number(item.id.replace("owned-", "")))) + 1;
    setOwnedItems((current) => [
      ...current,
      {
        id: `owned-${nextNumber}`,
        category: "top",
        color: "",
        description: "",
      },
    ]);
  }

  function updateOwnedItem(id: string, field: keyof OwnedItem, value: string) {
    setOwnedItems((current) =>
      current.map((item) =>
        item.id === id
          ? { ...item, [field]: field === "category" ? readCategory(value) : value }
          : item,
      ),
    );
  }

  async function refreshDemoSession(): Promise<DemoSession> {
    const response = await fetch("/api/session");
    const session = parseDemoSession(await readJson(response));
    setDemoSession(session);
    setOver18Checked(session.age_confirmed);
    setSessionState("ready");
    return session;
  }

  function toggleLock(lookId: string, slot: Slot, item: ItemRef) {
    if (requestState === "loading") return;
    setLocks((current) => {
      const exists = current.some(
        (lock) =>
          lock.look_id === lookId &&
          lock.slot === slot &&
          lock.item.id === item.id &&
          lock.item.kind === item.kind,
      );
      return exists
        ? current.filter(
            (lock) =>
              !(
                lock.look_id === lookId &&
                lock.slot === slot &&
                lock.item.id === item.id &&
                lock.item.kind === item.kind
              ),
          )
        : [...current, { look_id: lookId, slot, item }];
    });
  }

  async function requestPlan(replacement: ReplacementTarget | null = null) {
    if (!catalog || requestState === "loading") return;
    if (!demoSession?.enabled || sessionState !== "ready") return;
    if (demoSession.plan_requests_remaining <= 0) {
      setRequestMessage("This demo session has reached its three planning requests.");
      setRequestState("error");
      return;
    }
    if (!demoSession.age_confirmed && !over18Checked) {
      setRequestMessage("Confirm that you are 18 or older before requesting a plan.");
      setRequestState("error");
      return;
    }
    if (replacement) {
      const look = plan?.looks.find((candidate) => candidate.id === replacement.look_id);
      const targetIsLocked = locks.some(
        (lock) => lock.look_id === replacement.look_id && lock.slot === replacement.slot,
      );
      if (!planCurrent || !look || !sameItem(look.items[replacement.slot], replacement.item) || targetIsLocked) {
        return;
      }
    }
    const colors = preferredColors
      .split(",")
      .map((color) => color.trim())
      .filter(Boolean);
    const amount = Number(budgetAmount);
    const amountCents = Math.round(amount * 100);
    if (colors.length > 5 || colors.some((color) => color.length > 30)) {
      setRequestMessage("Enter up to five colors, with no more than 30 characters each.");
      setRequestState("error");
      return;
    }
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) {
      setRequestMessage("Enter a budget greater than zero.");
      setRequestState("error");
      return;
    }
    const parsedFit = parseFit(fitDraft);
    if (!parsedFit.ok) {
      setRequestMessage(parsedFit.error);
      setRequestState("error");
      return;
    }
    const fit = parsedFit.fit;

    const request: PlanRequest = {
      brief: { style_goal: styleGoal.trim(), preferred_colors: colors },
      replacement,
      state: {
        budget: { amount_cents: amountCents, mode: budgetMode },
        material_exclusions: exclusions,
        fit,
        owned_items: ownedItems,
        locks: replacement && plan ? locksForReplacement(plan, replacement, locks) : locks,
        plan,
      },
    };
    const submittedPreferenceKey = preferenceKey(
      request.brief.style_goal,
      request.brief.preferred_colors,
      budgetAmount,
      budgetMode,
      exclusions,
      fitDraft,
      ownedItems,
    );
    setRequestState("loading");
    setRequestMessage(
      replacement
        ? "Replacing one item and checking all three looks."
        : plan
          ? "Updating the plan. Kept items stay in place."
          : "Requesting three looks from the catalog.",
    );
    try {
      if (!demoSession.age_confirmed) {
        const confirmation = await fetch("/api/session/confirm-age", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ over_18: true }),
        });
        setDemoSession(parseDemoSession(await readJson(confirmation)));
        setOver18Checked(true);
      }
      const response = await fetch("/api/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      const remainingHeader = response.headers.get("X-Plan-Requests-Remaining");
      const remaining = remainingHeader === null ? null : Number(remainingHeader);
      if (
        remaining !== null &&
        Number.isInteger(remaining) &&
        remaining >= 0 &&
        remaining <= demoSession.plan_requests_limit
      ) {
        setDemoSession((current) =>
          current
            ? {
                ...current,
                plan_requests_used: current.plan_requests_limit - remaining,
                plan_requests_remaining: remaining,
              }
            : current,
        );
      }
      const result = parsePlanResponse(await readJson(response));
      if (result.outcome === "success" && result.plan) {
        if (replacement && plan && !isExactReplacement(plan, result.plan, replacement)) {
          setRequestState("error");
          setRequestMessage("The planner did not replace only this item.");
          return;
        }
        setPlan(result.plan);
        setPlanOwnedItems(ownedItems);
        setPlanFit(fit);
        setPlanNotices(result.notices);
        setSuccessfulPreferenceKey(submittedPreferenceKey);
        setRequestState("success");
        setRequestMessage(
          replacement
            ? "The item was replaced. All three looks passed the catalog and budget checks."
            : "The three looks passed the catalog and budget checks.",
        );
      } else if (result.outcome === "infeasible") {
        setRequestState("error");
        setRequestMessage(
          replacement
            ? result.conflicts.length
              ? `Keeping the other items conflicts with these constraints: ${result.conflicts.map((item) => item.detail).join("; ")}`
              : "No replacement can keep the other items under the current constraints."
            : result.conflicts.length
              ? `${
                  // A look_composition conflict means no item can complete a look at all, kept or not.
                  request.state.locks.length && result.conflicts.some((item) => item.rule !== "look_composition")
                    ? "Kept items conflict with"
                    : "No plan can meet"
                } these constraints: ${result.conflicts.map((item) => item.detail).join("; ")}`
              : "No plan can meet the kept items and current constraints.",
        );
      } else if (result.outcome === "provider_timeout") {
        setRequestState("error");
        setRequestMessage(
          replacement
            ? "The replacement request timed out."
            : "The planning request timed out. Try again with the same or fewer constraints.",
        );
      } else if (result.outcome === "provider_error") {
        setRequestState("error");
        setRequestMessage(
          replacement
            ? "The planning provider could not complete this replacement."
            : "The planning provider could not complete this request.",
        );
      } else {
        setRequestState("error");
        const summary = replacement
          ? "No replacement passed the catalog checks."
          : "No plan passed the catalog checks. Adjust your preferences and try again.";
        // Fit violations come first, so a stated fit that no item meets is always explained.
        const violations = [
          ...result.violations.filter((item) => item.rule === "fit"),
          ...result.violations.filter((item) => item.rule !== "fit"),
        ]
          .slice(0, 3)
          .map((item) => item.detail)
          .join(" ");
        const diagnostic = violations || result.error?.slice(0, 300) || "";
        setRequestMessage(diagnostic ? `${summary} ${diagnostic}` : summary);
      }
    } catch (error) {
      setRequestState("error");
      setRequestMessage(errorMessage(error));
      try {
        await refreshDemoSession();
      } catch {
        // Keep the last server-confirmed session state if a refresh also fails.
      }
    }
  }

  function submitPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void requestPlan();
  }

  function replacePlanItem(lookId: string, slot: Slot, item: ItemRef) {
    void requestPlan({ look_id: lookId, slot, item });
  }

  const canSubmit =
    catalogState === "ready" &&
    requestState !== "loading" &&
    demoSession?.enabled === true &&
    demoSession.plan_requests_remaining > 0 &&
    (demoSession.age_confirmed || over18Checked);
  const preferredColorList = preferredColors
    .split(",")
    .map((color) => color.trim())
    .filter(Boolean);
  const currentPreferenceKey = preferenceKey(
    styleGoal,
    preferredColorList,
    budgetAmount,
    budgetMode,
    exclusions,
    fitDraft,
    ownedItems,
  );
  const planCurrent = Boolean(plan && successfulPreferenceKey === currentPreferenceKey);
  const planTotal =
    plan && catalog
      ? budgetMode === "whole_plan"
        ? wholePlanCost(plan, catalog)
        : totalLookCosts(plan, catalog)
      : null;
  const previewOptions =
    plan && catalog ? previewOptionsForPlan(plan, catalog, planOwnedItems) : [];
  const previewSourceKey = previewOptions
    .map((option) => `${option.id}:${option.sourceKey}`)
    .join("|");

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="FullAura home">FullAura</a>
        <p className="environment-label">Style planning</p>
      </header>

      <div className="workspace">
        <div className="workspace-heading">
          <div>
            <h1>Three looks. Rules checked.</h1>
            <p className="page-description">
              Set your preferences. Each look uses catalog products that pass the rules you choose.
            </p>
          </div>
          <p className="catalog-status" aria-live="polite">
            {catalogState === "loading" && "Loading catalog"}
            {catalogState === "ready" && `${catalog?.products.length ?? 0} catalog products`}
            {catalogState === "disabled" && "Demo closed"}
            {catalogState === "error" && "Catalog unavailable"}
          </p>
        </div>

        <div className="workspace-grid">
          <section className="preferences-panel" aria-labelledby="preferences-title">
            <div className="panel-heading">
              <h2 id="preferences-title">Your preferences</h2>
              <p>FullAura checks the budget, fit, delivery, stock and material exclusions in code.</p>
            </div>

            {catalogState === "error" ? (
              <div className="notice notice-error" role="alert">{catalogError}</div>
            ) : catalogState === "disabled" ? (
              <div className="empty-state">
                <h3>This demo is closed</h3>
                <p>The planning catalog and model are available only when the pilot is enabled.</p>
              </div>
            ) : (
              <form className="preferences-form" onSubmit={submitPlan}>
                <div className="form-field">
                  <label htmlFor="style-goal">What should the looks work for?</label>
                  <textarea
                    autoComplete="off"
                    id="style-goal"
                    maxLength={300}
                    onChange={(event) => setStyleGoal(event.target.value)}
                    placeholder="For example, relaxed outfits for the office"
                    required
                    rows={3}
                    value={styleGoal}
                  />
                  <p className="field-help">Use up to 300 characters.</p>
                </div>

                <div className="form-field">
                  <label htmlFor="preferred-colors">Colors you prefer</label>
                  <input
                    autoComplete="off"
                    id="preferred-colors"
                    maxLength={164}
                    onChange={(event) => setPreferredColors(event.target.value)}
                    placeholder="Navy, cream, olive"
                    type="text"
                    value={preferredColors}
                  />
                  <p className="field-help">Separate up to five colors with commas.</p>
                </div>

                <div className="form-field">
                  <label htmlFor="budget-amount">Budget in EUR</label>
                  <input
                    id="budget-amount"
                    inputMode="decimal"
                    min="0.01"
                    onChange={(event) => setBudgetAmount(event.target.value)}
                    required
                    step="0.01"
                    type="number"
                    value={budgetAmount}
                  />
                </div>

                <fieldset className="budget-mode">
                  <legend>Budget applies to</legend>
                  <label className="radio-choice">
                    <input
                      checked={budgetMode === "per_look"}
                      name="budget-mode"
                      onChange={() => setBudgetMode("per_look")}
                      type="radio"
                    />
                    <span>Each look</span>
                  </label>
                  <label className="radio-choice">
                    <input
                      checked={budgetMode === "whole_plan"}
                      name="budget-mode"
                      onChange={() => setBudgetMode("whole_plan")}
                      type="radio"
                    />
                    <span>All three looks together</span>
                  </label>
                </fieldset>

                <section className="fit-fields" aria-labelledby="fit-title">
                  <h3 id="fit-title">Who the looks are for</h3>
                  <fieldset className="fit-choices">
                    <legend>Clothing lines</legend>
                    <div className="choice-grid">
                      {FIT_LINES.map((line) => (
                        <label className="check-choice" key={line}>
                          <input
                            checked={fitDraft.lines.includes(line)}
                            onChange={() =>
                              setFitDraft((current) => ({
                                ...current,
                                lines: toggled(FIT_LINES, current.lines, line),
                              }))
                            }
                            type="checkbox"
                            value={line}
                          />
                          <span>{LINE_LABELS[line]}</span>
                        </label>
                      ))}
                    </div>
                    <p className="field-help">None checked shows every line. Unisex items always count.</p>
                  </fieldset>

                  <fieldset className="fit-choices">
                    <legend>Bottoms and one-pieces</legend>
                    <div className="choice-grid">
                      {GARMENT_TYPES.map((garmentType) => (
                        <label className="check-choice" key={garmentType}>
                          <input
                            checked={fitDraft.garmentTypes.includes(garmentType)}
                            onChange={() =>
                              setFitDraft((current) => ({
                                ...current,
                                garmentTypes: toggled(GARMENT_TYPES, current.garmentTypes, garmentType),
                              }))
                            }
                            type="checkbox"
                            value={garmentType}
                          />
                          <span>{GARMENT_TYPE_LABELS[garmentType]}</span>
                        </label>
                      ))}
                    </div>
                    <p className="field-help">None checked allows all.</p>
                  </fieldset>

                  <div className="form-field">
                    <label htmlFor="fit-letter-size">Letter size</label>
                    <select
                      id="fit-letter-size"
                      onChange={(event) => {
                        const letterSize =
                          LETTER_SIZES.find((size) => size === event.target.value) ?? "";
                        setFitDraft((current) => ({ ...current, letterSize }));
                      }}
                      value={fitDraft.letterSize}
                    >
                      <option value="">Not set</option>
                      {LETTER_SIZES.map((size) => (
                        <option key={size} value={size}>{size}</option>
                      ))}
                    </select>
                  </div>

                  <div className="form-field">
                    <label htmlFor="fit-waist-sizes">Waist sizes</label>
                    <input
                      autoComplete="off"
                      id="fit-waist-sizes"
                      maxLength={100}
                      onChange={(event) => {
                        const waistSizes = event.target.value;
                        setFitDraft((current) => ({ ...current, waistSizes }));
                      }}
                      placeholder="32, 33/32"
                      type="text"
                      value={fitDraft.waistSizes}
                    />
                    <p className="field-help">
                      Separate sizes with commas. A waist without a length, such as 33, matches every length.
                    </p>
                  </div>

                  <div className="form-field">
                    <label htmlFor="fit-shoe-sizes">EU shoe sizes</label>
                    <input
                      autoComplete="off"
                      id="fit-shoe-sizes"
                      maxLength={100}
                      onChange={(event) => {
                        const shoeSizes = event.target.value;
                        setFitDraft((current) => ({ ...current, shoeSizes }));
                      }}
                      placeholder="42.5, 43"
                      type="text"
                      value={fitDraft.shoeSizes}
                    />
                    <p className="field-help">
                      Separate sizes with commas. Half sizes are rare, so you can add the next whole size too.
                    </p>
                  </div>
                  <p className="field-help">Up to {MAX_FIT_SIZES} sizes in total.</p>
                </section>

                <details className="exclusions-disclosure">
                  <summary>
                    <span>Avoid materials</span>
                    <span className="selection-count">{exclusions.length} selected</span>
                  </summary>
                  <p className="field-help">
                    Exclusions apply to new purchases. Only materials named in a product's catalog record are checked; unnamed or vague parts are not checked. A selection also removes products whose composition cannot be checked.
                  </p>
                  <div className="material-list">
                    {MATERIALS.map((material) => (
                      <label className="check-choice" key={material}>
                        <input
                          checked={exclusions.includes(material)}
                          onChange={() => toggleExclusion(material)}
                          type="checkbox"
                          value={material}
                        />
                        <span>{material}</span>
                      </label>
                    ))}
                  </div>
                </details>

                <section className="owned-items" aria-labelledby="owned-title">
                  <div className="owned-heading">
                    <div>
                      <h3 id="owned-title">Items you already own</h3>
                      <p className="field-help">Optional. Add up to three by description.</p>
                    </div>
                    <button
                      className="secondary-button"
                      disabled={ownedItems.length >= 3}
                      onClick={addOwnedItem}
                      type="button"
                    >
                      Add item
                    </button>
                  </div>

                  {ownedItems.map((item, index) => (
                    <fieldset className="owned-entry" key={item.id}>
                      <legend>Item {index + 1}</legend>
                      <button
                        aria-label={`Remove item ${index + 1}`}
                        className="remove-button"
                        onClick={() => {
                          setOwnedItems((current) => current.filter((entry) => entry.id !== item.id));
                          setLocks((current) =>
                            current.filter(
                              (lock) => !(lock.item.kind === "owned" && lock.item.id === item.id),
                            ),
                          );
                        }}
                        type="button"
                      >
                        Remove
                      </button>
                      <label htmlFor={`${item.id}-category`}>Category</label>
                      <select
                        id={`${item.id}-category`}
                        onChange={(event) => updateOwnedItem(item.id, "category", event.target.value)}
                        value={item.category}
                      >
                        {CATEGORIES.map((category) => (
                          <option key={category} value={category}>{labelCategory(category)}</option>
                        ))}
                      </select>
                      <label htmlFor={`${item.id}-color`}>Color</label>
                      <input
                        id={`${item.id}-color`}
                        maxLength={40}
                        onChange={(event) => updateOwnedItem(item.id, "color", event.target.value)}
                        required
                        value={item.color}
                      />
                      <label htmlFor={`${item.id}-description`}>Description</label>
                      <input
                        id={`${item.id}-description`}
                        maxLength={200}
                        onChange={(event) => updateOwnedItem(item.id, "description", event.target.value)}
                        required
                        value={item.description}
                      />
                    </fieldset>
                  ))}
                </section>

                <div className="demo-consent">
                  <label className="check-choice" htmlFor="confirm-over-18">
                    <input
                      checked={demoSession?.age_confirmed === true || over18Checked}
                      disabled={demoSession?.age_confirmed === true}
                      id="confirm-over-18"
                      onChange={(event) => setOver18Checked(event.target.checked)}
                      required={demoSession?.age_confirmed !== true}
                      type="checkbox"
                    />
                    <span>I confirm that I am 18 or older.</span>
                  </label>
                  <p className="field-help">
                    {demoSession?.age_confirmed
                      ? "Age confirmation is recorded for this browser session."
                      : "Planning sends your style preferences, budget and owned-item descriptions to Baseten. This session allows up to three plan requests, including updates and replacements."}
                  </p>
                  <p className="field-help">
                    Photo previews are disabled pending T-02. If enabled later, two JPEGs will be sent to Meta after metadata removal. FullAura keeps photos in memory only for the request; Meta’s current terms govern provider retention.
                  </p>
                  {demoSession && (
                    <p className="session-quota" role="status">
                      {demoSession.plan_requests_remaining} of {demoSession.plan_requests_limit} planning requests remain in this session.
                    </p>
                  )}
                </div>

                <button
                  aria-busy={requestState === "loading"}
                  className={`primary-button${requestState === "loading" ? " is-loading" : requestState === "error" ? " is-error" : requestState === "success" && planCurrent ? " is-success" : ""}`}
                  disabled={!canSubmit}
                  type="submit"
                >
                  {requestState === "loading" ? "Planning looks…" : plan ? "Update three looks" : "Generate three looks"}
                </button>
                <p className="submit-help">
                  This uses one planning request. Provider quota or cost may apply.
                </p>
              </form>
            )}
          </section>

          <section className="results-panel" aria-labelledby="results-title">
            <div className="results-heading">
              <div>
                <h2 id="results-title">Three looks</h2>
                <p>
                  Each item links to its catalog source. Keep pieces to retain them, or replace one
                  item; other positions stay in place and all three looks are checked again.
                  Replacements send a planning request, so provider quota or cost may apply.
                </p>
              </div>
              {plan && catalog && (
                <p className="plan-total">
                  {budgetMode === "whole_plan" ? "Combined budget" : "Total if each look is bought"}
                  <strong>{formatMoney(planTotal)}</strong>
                </p>
              )}
            </div>

            {catalogState === "loading" ? (
              <div className="loading-state" role="status">
                <span className="visually-hidden">Loading catalog and planning area</span>
                {["first", "second", "third"].map((look) => (
                  <div aria-hidden="true" className="look-skeleton" key={look}>
                    <span className="skeleton-title" />
                    <span className="skeleton-row" />
                    <span className="skeleton-row" />
                  </div>
                ))}
              </div>
            ) : catalogState === "disabled" ? (
              <div className="empty-state">
                <h3>Planning is unavailable</h3>
                <p>The pilot is not open for requests yet.</p>
              </div>
            ) : catalogState === "error" ? (
              <div className="empty-state" role="status">{catalogError}</div>
            ) : requestState === "loading" && !plan ? (
              <div className="loading-state" role="status">
                <span className="visually-hidden">Planning three looks</span>
                {["first", "second", "third"].map((look) => (
                  <div aria-hidden="true" className="look-skeleton" key={look}>
                    <span className="skeleton-title" />
                    <span className="skeleton-row" />
                    <span className="skeleton-row" />
                  </div>
                ))}
              </div>
            ) : plan && catalog ? (
              <>
                {requestMessage && (
                  <p
                    className={`request-message${requestState === "error" ? " is-error" : ""}`}
                    role={requestState === "error" ? "alert" : "status"}
                  >
                    {requestState === "loading" && requestMessage}
                    {requestState === "error" && requestMessage}
                    {requestState === "success" && planCurrent && requestMessage}
                    {requestState === "success" && !planCurrent && "Preferences changed. These looks are from the previous successful request."}
                    {requestState === "error" && " The displayed looks are from the previous successful request."}
                  </p>
                )}
                <div className="look-grid">
                  {plan.looks.map((look) => (
                    <LookCard
                      key={look.id}
                      catalog={catalog}
                      lockDisabled={requestState === "loading"}
                      replaceDisabled={!planCurrent || !canSubmit}
                      locks={locks}
                      look={look}
                      onToggleLock={toggleLock}
                      onReplaceItem={replacePlanItem}
                      ownedItems={planOwnedItems}
                      ownedNotices={planNotices}
                      fit={planFit}
                      plan={plan}
                    />
                  ))}
                </div>
                <PreviewPanel
                  key={previewSourceKey}
                  enabled={previewStatus.enabled && planCurrent}
                  detail={
                    !planCurrent
                      ? "Update the plan before generating a preview."
                      : previewStatus.detail
                  }
                  loadingStatus={previewStatusState === "loading"}
                  options={previewOptions}
                />
              </>
            ) : requestState === "error" && requestMessage ? (
              <div className="empty-state is-error" role="alert">{requestMessage}</div>
            ) : (
              <div className="empty-state">
                <h3>Your plan will appear here</h3>
                <p>Enter a style goal and budget, then generate three looks from the catalog.</p>
                <p>Product photos and personal photo previews are not part of this development screen.</p>
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
