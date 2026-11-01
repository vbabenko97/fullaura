import { CATEGORIES, GARMENT_TYPES, PRODUCT_LINES, SIZE_SYSTEMS, SLOTS } from "./types";
import type {
  FindResponse,
  Catalog,
  Category,
  Concept,
  ConceptItem,
  ConceptMatch,
  ConceptOutcome,
  ConceptResponse,
  ItemRef,
  NormalizedSize,
  Offer,
  OwnedNotice,
  Plan,
  PlanOutcome,
  PlanResponse,
  Session,
  Shoppable,
  Slot,
  Source,
  Variant,
  VariantImage,
} from "./types";

export class ApiResponseError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string | null = null,
    readonly reason: string | null = null,
  ) {
    super(detail ?? `API request failed with status ${status}`);
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
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

export function readCategory(value: unknown): Category {
  if (typeof value === "string") {
    const category = CATEGORIES.find((candidate) => candidate === value);
    if (category) return category;
  }
  throw new Error("Invalid product category in API response");
}

function readChoice<T extends string>(value: unknown, choices: readonly T[], field: string): T {
  const choice = choices.find((candidate) => candidate === value);
  if (choice === undefined) throw new Error(`Invalid ${field} in API response`);
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

function readImage(value: unknown): VariantImage | null {
  if (value == null) return null; // most variants have no photo FullAura may show
  if (!isRecord(value)) throw new Error("Invalid product image in catalog response");
  const url = readString(value.url, "image URL");
  const width = readNumber(value.width, "image width");
  const height = readNumber(value.height, "image height");
  if (!/^https:\/\//i.test(url) || width <= 0 || height <= 0) {
    throw new Error("Invalid product image in catalog response");
  }
  return {
    url,
    alt: readString(value.alt, "image description"),
    width,
    height,
    attribution: value.attribution == null ? null : readString(value.attribution, "image attribution"),
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
    image: readImage(value.image),
    composition: {
      status,
      components,
      source_text: readNullableString(composition.source_text, "composition source text"),
    },
    source: readSource(value.source),
    offers: readArray(value.offers, "offers").map(readOffer),
  };
}

export function parseCatalog(value: unknown): Catalog {
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
        footwear_closure:
          entry.footwear_closure == null
            ? null
            : readChoice(entry.footwear_closure, ["closed", "open"] as const, "footwear closure"),
        variants: readArray(entry.variants, "variants").map(readVariant),
      };
    }),
  };
  if (catalog.synthetic || catalog.products.length === 0) {
    throw new Error("The catalog is empty or synthetic");
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

const PLAN_OUTCOMES: readonly PlanOutcome[] = [
  "success",
  "infeasible",
  "generation_failed",
  "provider_timeout",
  "provider_error",
  "restore_failed",
];

export function parsePlanResponse(value: unknown): PlanResponse {
  if (!isRecord(value)) throw new Error("Invalid planning response");
  const outcome = PLAN_OUTCOMES.find((candidate) => candidate === value.outcome);
  if (outcome === undefined) throw new Error("Unknown planning outcome");
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
  const notices = readArray(value.notices ?? [], "planning notices").map((entry) => {
    if (!isRecord(entry) || (entry.kind !== "owned_reported" && entry.kind !== "owned_unconfirmed")) {
      throw new Error("Invalid planning notice");
    }
    const item = parseItemRef(entry.item);
    if (item.kind !== "owned") throw new Error("Invalid planning notice item");
    const kind: OwnedNotice["kind"] = entry.kind;
    return { kind, item, material: readString(entry.material, "planning notice material") };
  });
  const plan = value.plan == null ? null : parsePlan(value.plan);
  const planToken = value.plan_token == null ? null : readString(value.plan_token, "plan token");
  if (outcome === "success" && (plan === null || planToken === null)) {
    throw new Error("A successful planning response needs a plan and its token");
  }
  return {
    outcome,
    plan,
    plan_token: planToken,
    catalog_id: value.catalog_id == null ? null : readString(value.catalog_id, "catalog ID"),
    catalog_changed: value.catalog_changed === true,
    conflicts,
    violations,
    notices,
  };
}

function readCount(value: unknown, field: string): number {
  const count = readNumber(value, field);
  if (!Number.isInteger(count) || count < 0) throw new Error(`Invalid ${field} in API response`);
  return count;
}

function readFlag(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`Invalid ${field} in API response`);
  return value;
}

function readStrings(value: unknown, field: string): string[] {
  return readArray(value, field).map((entry) => readString(entry, field));
}

function parseConceptMatch(value: unknown): ConceptMatch {
  if (!isRecord(value)) throw new Error("Invalid concept match in API response");
  const status = readChoice(value.status, ["close", "alternative", "none"] as const, "match status");
  const variantId = readNullableString(value.variant_id, "matched variant ID");
  const offerId = readNullableString(value.offer_id, "matched offer ID");
  const base = {
    differences: readStrings(value.differences, "match differences"),
  };
  if (status === "none") return { ...base, status, variant_id: null, offer_id: null };
  if (variantId === null || offerId === null) throw new Error("A concept match needs its product");
  // An alternative must never read as the same item, so it always says how it differs.
  if (status === "alternative" && base.differences.length === 0) {
    throw new Error("An alternative match needs its differences");
  }
  return { ...base, status, variant_id: variantId, offer_id: offerId };
}

function parseConceptItem(value: unknown): ConceptItem {
  if (!isRecord(value)) throw new Error("Invalid concept item in API response");
  return {
    slot: readChoice(value.slot, SLOTS, "concept slot"),
    kind: readString(value.kind, "concept kind"),
    color: readString(value.color, "concept color"),
    color_family: readString(value.color_family, "concept color family"),
    material: readString(value.material, "concept material"),
    fit: readString(value.fit, "concept fit"),
    details: readString(value.details, "concept details"),
    match: parseConceptMatch(value.match),
  };
}

function parseConcept(value: unknown): Concept {
  if (!isRecord(value)) throw new Error("Invalid concept in API response");
  const items = readArray(value.items, "concept items").map(parseConceptItem);
  if (items.length === 0 || new Set(items.map((item) => item.slot)).size !== items.length) {
    throw new Error("The design API returned a concept without distinct items");
  }
  return {
    id: readString(value.id, "concept ID"),
    name: readString(value.name, "concept name"),
    why: readString(value.why, "concept reason"),
    items,
  };
}

function parseShoppable(value: unknown): Shoppable {
  if (!isRecord(value)) throw new Error("Invalid shoppable summary in API response");
  return {
    complete: readFlag(value.complete, "shoppable flag"),
    passes_hard_checks: readFlag(value.passes_hard_checks, "hard check flag"),
    matched_total_cents: readNullableNumber(value.matched_total_cents, "matched total"),
    violations: readStrings(value.violations, "shoppable violations"),
  };
}

const CONCEPT_OUTCOMES: readonly ConceptOutcome[] = [
  "success",
  "generation_failed",
  "provider_timeout",
  "provider_error",
];

export function parseConceptResponse(value: unknown): ConceptResponse {
  if (!isRecord(value)) throw new Error("Invalid design response");
  const outcome = CONCEPT_OUTCOMES.find((candidate) => candidate === value.outcome);
  if (outcome === undefined) throw new Error("Unknown design outcome");
  const concept = value.concept === null ? null : parseConcept(value.concept);
  const token = readNullableString(value.concept_token, "concept token");
  const shoppable = value.shoppable === null ? null : parseShoppable(value.shoppable);
  if (outcome === "success" && (concept === null || token === null || shoppable === null)) {
    throw new Error("A successful design response needs a concept, its token and its summary");
  }
  // The summary must agree with the items, so an incomplete look can never read as checked.
  const statuses = concept?.items.map((item) => item.match.status) ?? [];
  if (shoppable?.complete && statuses.some((status) => status !== "close")) {
    throw new Error("A design response called a look complete that has an alternative or no match");
  }
  if (shoppable?.passes_hard_checks && (statuses.length === 0 || statuses.includes("none"))) {
    throw new Error("A design response checked a look that has an item without a product");
  }
  return {
    outcome,
    error: readNullableString(value.error, "design error"),
    catalog_id: readString(value.catalog_id, "catalog ID"),
    concept_token: token,
    concept,
    matching_failed: readFlag(value.matching_failed, "matching flag"),
    shoppable,
  };
}

export function parseSession(value: unknown): Session {
  if (!isRecord(value)) throw new Error("Invalid session response");
  return {
    beta: readFlag(value.beta, "beta flag"),
    invited: readFlag(value.invited, "invite flag"),
    accounting: readFlag(value.accounting, "accounting flag"),
    age_confirmed: readFlag(value.age_confirmed, "age confirmation"),
    photo_consent: readFlag(value.photo_consent, "photo consent"),
    plan_requests_used: readCount(value.plan_requests_used, "used plan requests"),
    plan_requests_limit: readCount(value.plan_requests_limit, "plan request limit"),
    preview_requests_used: readCount(value.preview_requests_used, "used try-ons"),
    preview_requests_limit: readCount(value.preview_requests_limit, "try-on limit"),
    planning_open: readFlag(value.planning_open, "planning flag"),
    previews_open: readFlag(value.previews_open, "try-on flag"),
    pilot_ends_at: readNullableString(value.pilot_ends_at, "pilot end"),
    pilot_ended: readFlag(value.pilot_ended, "pilot end flag"),
    records_purge_at: readNullableString(value.records_purge_at, "record deletion time"),
    feedback_ids: readArray(value.feedback_ids, "feedback IDs").map((id) => readString(id, "feedback ID")),
    has_plan: readFlag(value.has_plan, "plan flag"),
    // minimal: a server from before Design my look sends no flag, which means the mode is off
    design_open: value.design_open === undefined ? false : readFlag(value.design_open, "design flag"),
    // minimal: a server from before the shopping agent sends no flag, which means the mode is off
    find_open: value.find_open === undefined ? false : readFlag(value.find_open, "find flag"),
  };
}

const REASON_MESSAGES: Record<string, string> = {
  invite_required: "Open your invite link again to continue.",
  invite_invalid: "This invite code is not valid. Check the link you were sent.",
  age_required: "Confirm that you are 18 or older first.",
  photo_consent_required: "Agree to the photo processing notice before a try-on.",
  quota: "This invite has used all its requests of this kind.",
  busy: "Another request from this invite is still running. Wait for it to finish.",
  budget: "The beta's shared budget for this kind of request is used up. Your plan stays available.",
  accounting_unavailable:
    "Spending cannot be recorded right now, so model requests are paused. Your plan stays available.",
  pilot_ended: "The beta has ended. Your saved plans and shopping list stay available.",
  plan_expired: "This plan is no longer current on the server.",
  concept_expired: "This concept was replaced. Try on the current one.",
  find_expired: "A newer search replaced these products. Try on the current ones.",
  not_configured: "This feature is not configured on the server.",
  closed: "The beta is closed.",
  invalid: "This request is not valid. Check what you entered and try again.",
  timeout: "The request took too long. Try again.",
  provider: "The model provider could not complete this request. Try again later.",
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiResponseError) {
    const known = error.reason ? REASON_MESSAGES[error.reason] : undefined;
    if (known) return known;
    if (error.detail) return error.detail;
    return `The request failed with status ${error.status}.`;
  }
  return "The request could not be completed. Check your connection and try again.";
}

export function reasonOf(error: unknown): string | null {
  return error instanceof ApiResponseError ? error.reason : null;
}

export function newOperationId(): string {
  return crypto.randomUUID();
}

export async function postJson(
  url: string,
  body: unknown,
  headers: Record<string, string> = {},
): Promise<unknown> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  return readJson(response);
}

/** One line of the shopping agent's answer: what it is doing, or the result. */
export function parseFindLine(value: unknown): { kind: "step"; step: string } | { kind: "result"; result: FindResponse } {
  if (!isRecord(value)) throw new Error("Invalid find response");
  if (value.step !== undefined) return { kind: "step", step: readString(value.step, "step") };
  if (!isRecord(value.result)) throw new Error("Invalid find response");
  const result = value.result;
  return {
    kind: "result",
    result: {
      outcome: readString(result.outcome, "outcome"),
      message: readNullableString(result.message, "message"),
      find_token: readNullableString(result.find_token, "find token"),
      scope: readString(result.scope, "scope"),
      budget_cents: readCount(result.budget_cents, "budget"),
      total_cents: readCount(result.total_cents, "total"),
      finds: readArray(result.finds, "finds").map((find) => {
        if (!isRecord(find)) throw new Error("Invalid find");
        return {
          url: readString(find.url, "address"),
          shop: readString(find.shop, "shop"),
          title: readString(find.title, "title"),
          color: readNullableString(find.color, "color"),
          material: readNullableString(find.material, "material"),
          price_cents: readCount(find.price_cents, "price"),
          size_checked: readFlag(find.size_checked, "size flag"),
          slot: readString(find.slot, "slot"),
          why: readString(find.why, "reason"),
        };
      }),
    },
  };
}

/** Post, and hand each JSON line of the answer to onLine as it arrives. */
export async function postLines(
  url: string,
  body: unknown,
  headers: Record<string, string>,
  onLine: (line: unknown) => void,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    await readJson(response);
    return;
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("The answer could not be read");
  const decoder = new TextDecoder();
  let held = "";
  for (;;) {
    const { done, value } = await reader.read();
    held += decoder.decode(value, { stream: !done });
    const lines = held.split("\n");
    held = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) onLine(JSON.parse(line));
    if (done) break;
  }
  if (held.trim()) onLine(JSON.parse(held));
}

export async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) {
    let detail: string | null = null;
    let reason: string | null = null;
    try {
      const payload: unknown = await response.json();
      if (isRecord(payload) && typeof payload.detail === "string") detail = payload.detail;
      if (isRecord(payload) && typeof payload.reason === "string") reason = payload.reason;
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new ApiResponseError(response.status, detail, reason);
  }
  const payload: unknown = await response.json();
  return payload;
}
