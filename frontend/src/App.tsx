import { useEffect, useState } from "react";
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

type Material = (typeof MATERIALS)[number];
type Category = (typeof CATEGORIES)[number];
type Slot = (typeof SLOTS)[number];
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

interface PlanResponse {
  outcome: "success" | "infeasible" | "generation_failed" | "provider_timeout" | "provider_error";
  plan: Plan | null;
  conflicts: { detail: string }[];
  notices: OwnedNotice[];
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
  state: {
    budget: {
      amount_cents: number;
      mode: BudgetMode;
    };
    material_exclusions: Material[];
    owned_items: OwnedItem[];
    locks: LockedPlacement[];
    plan: Plan | null;
  };
}

type OwnedItemDraft = OwnedItem;

type CatalogState = "loading" | "ready" | "error";
type RequestState = "idle" | "loading" | "success" | "error";

class ApiResponseError extends Error {
  constructor(readonly status: number) {
    super(`API request failed with status ${status}`);
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
    return { detail: readString(entry.detail, "conflict detail") };
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
  return {
    outcome,
    plan: value.plan === null ? null : parsePlan(value.plan),
    conflicts,
    notices,
  };
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiResponseError && error.status === 503) {
    return "The planning model is not configured in this development environment.";
  }
  if (error instanceof ApiResponseError && error.status === 404) {
    return "Planning is available only when the backend runs in development mode.";
  }
  return "The request could not be completed. Check the backend and try again.";
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) throw new ApiResponseError(response.status);
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

function selectedOffer(plan: Plan, variant: Variant): Offer | null {
  const offerId = plan.selected_offers[variant.id];
  return variant.offers.find((offer) => offer.id === offerId) ?? null;
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
  ownedItems: OwnedItem[],
): string {
  return JSON.stringify({
    style_goal: styleGoal.trim(),
    preferred_colors: colors,
    amount_cents: Math.round(Number(budgetAmount) * 100),
    budget_mode: budgetMode,
    material_exclusions: [...exclusions].sort(),
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
  isLocked: boolean;
  lockDisabled: boolean;
  onToggleLock: (lookId: string, slot: Slot, item: ItemRef) => void;
}

function ProductLine({
  catalog,
  look,
  plan,
  slot,
  item,
  ownedItems,
  ownedNotices,
  isLocked,
  lockDisabled,
  onToggleLock,
}: ProductLineProps) {
  if (item.kind === "owned") {
    const owned = ownedItems.find((candidate) => candidate.id === item.id);
    const notices = ownedNotices.filter((notice) => notice.item.id === item.id);
    return (
      <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
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
  const facts = [entry.product.brand, entry.variant.color, entry.variant.size]
    .filter((value): value is string => value !== null)
    .join(", ");
  const offerLink = offer && /^https:\/\//i.test(offer.url) ? offer.url : null;

  return (
    <li className="look-item">
        <div className="look-item-heading">
          <span className="slot-name">{labelSlot(slot)}</span>
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
  locks: LockedPlacement[];
  lockDisabled: boolean;
  onToggleLock: (lookId: string, slot: Slot, item: ItemRef) => void;
}

function LookCard({
  catalog,
  look,
  plan,
  ownedItems,
  ownedNotices,
  locks,
  lockDisabled,
  onToggleLock,
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
              isLocked={isLocked}
              item={item}
              lockDisabled={lockDisabled}
              look={look}
              onToggleLock={onToggleLock}
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

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogState, setCatalogState] = useState<CatalogState>("loading");
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [styleGoal, setStyleGoal] = useState("");
  const [preferredColors, setPreferredColors] = useState("");
  const [budgetAmount, setBudgetAmount] = useState("");
  const [budgetMode, setBudgetMode] = useState<BudgetMode>("per_look");
  const [exclusions, setExclusions] = useState<Material[]>([]);
  const [ownedItems, setOwnedItems] = useState<OwnedItemDraft[]>([]);
  const [locks, setLocks] = useState<LockedPlacement[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planOwnedItems, setPlanOwnedItems] = useState<OwnedItem[]>([]);
  const [planNotices, setPlanNotices] = useState<OwnedNotice[]>([]);
  const [successfulPreferenceKey, setSuccessfulPreferenceKey] = useState<string | null>(null);
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [requestMessage, setRequestMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function loadCatalog() {
      try {
        const response = await fetch("/api/catalog", { signal: controller.signal });
        const loadedCatalog = parseCatalog(await readJson(response));
        setCatalog(loadedCatalog);
        setCatalogState("ready");
      } catch (error) {
        if (controller.signal.aborted) return;
        setCatalogError(errorMessage(error));
        setCatalogState("error");
      }
    }
    void loadCatalog();
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

  async function submitPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!catalog) return;
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

    const request: PlanRequest = {
      brief: { style_goal: styleGoal.trim(), preferred_colors: colors },
      state: {
        budget: { amount_cents: amountCents, mode: budgetMode },
        material_exclusions: exclusions,
        owned_items: ownedItems,
        locks,
        plan,
      },
    };
    const submittedPreferenceKey = preferenceKey(
      request.brief.style_goal,
      request.brief.preferred_colors,
      budgetAmount,
      budgetMode,
      exclusions,
      ownedItems,
    );
    setRequestState("loading");
    setRequestMessage(plan ? "Updating the plan. Kept items stay in place." : "Requesting three looks from the catalog.");
    try {
      const response = await fetch("/api/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      const result = parsePlanResponse(await readJson(response));
      if (result.outcome === "success" && result.plan) {
        setPlan(result.plan);
        setPlanOwnedItems(ownedItems);
        setPlanNotices(result.notices);
        setSuccessfulPreferenceKey(submittedPreferenceKey);
        setRequestState("success");
        setRequestMessage("The three looks passed the catalog and budget checks.");
      } else if (result.outcome === "infeasible") {
        setRequestState("error");
        setRequestMessage(
          result.conflicts.length
            ? `Kept items conflict with these constraints: ${result.conflicts.map((item) => item.detail).join("; ")}`
            : "No plan can meet the kept items and current constraints.",
        );
      } else if (result.outcome === "provider_timeout") {
        setRequestState("error");
        setRequestMessage("The planning request timed out. Try again with the same or fewer constraints.");
      } else if (result.outcome === "provider_error") {
        setRequestState("error");
        setRequestMessage("The planning provider could not complete this request.");
      } else {
        setRequestState("error");
        setRequestMessage("No plan passed the catalog checks. Adjust your preferences and try again.");
      }
    } catch (error) {
      setRequestState("error");
      setRequestMessage(errorMessage(error));
    }
  }

  const canSubmit = catalogState === "ready" && requestState !== "loading";
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
    ownedItems,
  );
  const planCurrent = Boolean(plan && successfulPreferenceKey === currentPreferenceKey);
  const planTotal =
    plan && catalog
      ? budgetMode === "whole_plan"
        ? wholePlanCost(plan, catalog)
        : totalLookCosts(plan, catalog)
      : null;

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="FullAura home">FullAura</a>
        <p className="environment-label">Development planning</p>
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
            {catalogState === "error" && "Catalog unavailable"}
          </p>
        </div>

        <div className="workspace-grid">
          <section className="preferences-panel" aria-labelledby="preferences-title">
            <div className="panel-heading">
              <h2 id="preferences-title">Your preferences</h2>
              <p>FullAura checks the budget, delivery, stock and material exclusions in code.</p>
            </div>

            {catalogState === "error" ? (
              <div className="notice notice-error" role="alert">{catalogError}</div>
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

                <button
                  aria-busy={requestState === "loading"}
                  className={`primary-button${requestState === "loading" ? " is-loading" : requestState === "error" ? " is-error" : requestState === "success" && planCurrent ? " is-success" : ""}`}
                  disabled={!canSubmit}
                  type="submit"
                >
                  {requestState === "loading" ? "Planning looks…" : plan ? "Update three looks" : "Generate three looks"}
                </button>
                <p className="submit-help">
                  This sends your preferences to the configured planning model through Baseten. Provider quota or cost may apply.
                </p>
              </form>
            )}
          </section>

          <section className="results-panel" aria-labelledby="results-title">
            <div className="results-heading">
              <div>
                <h2 id="results-title">Three looks</h2>
                <p>Each item links to its catalog source. Keep pieces you want to retain.</p>
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
                    {requestState === "loading" && "Updating the plan. Your kept items stay in place."}
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
                      locks={locks}
                      look={look}
                      onToggleLock={toggleLock}
                      ownedItems={planOwnedItems}
                      ownedNotices={planNotices}
                      plan={plan}
                    />
                  ))}
                </div>
                <p className="preview-note">
                  Product photos and personal photo previews are not part of this development screen.
                </p>
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
