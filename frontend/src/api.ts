import { CATEGORIES, GARMENT_TYPES, PRODUCT_LINES, SIZE_SYSTEMS, SLOTS } from "./types";
import type {
  Catalog,
  Category,
  DemoSession,
  ItemRef,
  NormalizedSize,
  Offer,
  OwnedNotice,
  Plan,
  PlanResponse,
  PreviewStatus,
  Slot,
  Source,
  Variant,
} from "./types";

class ApiResponseError extends Error {
  constructor(readonly status: number, readonly detail: string | null = null) {
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

export function parsePlanResponse(value: unknown): PlanResponse {
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

export function parsePreviewStatus(value: unknown): PreviewStatus {
  if (!isRecord(value) || typeof value.enabled !== "boolean") {
    throw new Error("Invalid preview status response");
  }
  return {
    enabled: value.enabled,
    detail: readNullableString(value.detail, "preview status detail"),
  };
}

export function parseDemoSession(value: unknown): DemoSession {
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

export function errorMessage(error: unknown): string {
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

export async function readJson(response: Response): Promise<unknown> {
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
