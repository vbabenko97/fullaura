export const MATERIALS = [
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

export const CATEGORIES = [
  "top",
  "bottom",
  "one_piece",
  "knitwear",
  "outerwear",
  "footwear",
  "accessory",
] as const;

export const SLOTS = [
  "top",
  "bottom",
  "one_piece",
  "mid_layer",
  "outer_layer",
  "footwear",
  "accessory",
] as const;

export const SLOT_CATEGORIES: Record<Slot, readonly Category[]> = {
  top: ["top", "knitwear"],
  bottom: ["bottom"],
  one_piece: ["one_piece"],
  mid_layer: ["top", "knitwear"],
  outer_layer: ["outerwear"],
  footwear: ["footwear"],
  accessory: ["accessory"],
};

export const FOOTWEAR_CHOICES = ["closed", "open", "any"] as const;
export const OCCASIONS = ["office", "casual", "evening", "travel", "special_event"] as const;
export const SEASONS = ["spring", "summer", "autumn", "winter"] as const;
export const FIT_LINES = ["men", "women"] as const;
export const PRODUCT_LINES = [...FIT_LINES, "unisex"] as const;
export const GARMENT_TYPES = ["trousers", "jeans", "shorts", "skirt", "leggings", "dress"] as const;
export const LETTER_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"] as const;
export const SIZE_SYSTEMS = ["letter", "waist_length", "eu_shoe"] as const;
export const TRY_ON_ISSUES = [
  "likeness",
  "body_proportions",
  "clothes_or_colors",
  "background",
  "other",
  "none_noticed",
] as const;
export const MAX_FIT_SIZES = 12;
export const WAIST_SIZE = /^\d{2}(\/\d{2})?$/;
export const EU_SHOE_SIZE = /^\d{2}(\.5)?$/;

export type Material = (typeof MATERIALS)[number];
export type Category = (typeof CATEGORIES)[number];
export type Slot = (typeof SLOTS)[number];
export type FitLine = (typeof FIT_LINES)[number];
export type GarmentType = (typeof GARMENT_TYPES)[number];
export type FootwearChoice = (typeof FOOTWEAR_CHOICES)[number];
export type Occasion = (typeof OCCASIONS)[number];
export type Season = (typeof SEASONS)[number];
export type TryOnIssue = (typeof TRY_ON_ISSUES)[number];
type LetterSize = (typeof LETTER_SIZES)[number];


export interface NormalizedSize {
  system: (typeof SIZE_SYSTEMS)[number];
  value: string;
}

/** Whom the looks are for, as the person states it. Fields that are not set are omitted. */
export interface Fit {
  lines?: FitLine[];
  garment_types?: GarmentType[];
  sizes?: NormalizedSize[];
}

export interface FitDraft {
  lines: FitLine[];
  garmentTypes: GarmentType[];
  letterSize: LetterSize | "";
  waistSizes: string;
  shoeSizes: string;
}

export const EMPTY_FIT_DRAFT: FitDraft = {
  lines: [],
  garmentTypes: [],
  letterSize: "",
  waistSizes: "",
  shoeSizes: "",
};
export type BudgetMode = "per_look" | "whole_plan";
type ItemKind = "catalog" | "owned";

export interface Source {
  url: string;
  checked_at: string;
}

export interface Offer {
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

/** A product photo FullAura may show, with the rights recorded in docs/DATA.md. */
export interface VariantImage {
  url: string;
  alt: string;
  width: number;
  height: number;
  attribution: string | null;
}

export interface Variant {
  id: string;
  color: string | null;
  size: string | null;
  normalized_size: NormalizedSize | null;
  image: VariantImage | null;
  composition: {
    status: "complete" | "partial" | "unknown" | "conflicting";
    components: Component[];
    source_text: string | null;
  };
  source: Source;
  offers: Offer[];
}

export interface Product {
  id: string;
  title: string;
  brand: string | null;
  category: Category;
  line: (typeof PRODUCT_LINES)[number] | null;
  garment_type: GarmentType | null;
  footwear_closure: "closed" | "open" | null;
  variants: Variant[];
}

export interface Catalog {
  id: string;
  synthetic: boolean;
  products: Product[];
}

export interface OwnedItem {
  id: string;
  category: Category;
  color: string;
  description: string;
}

export interface ItemRef {
  kind: ItemKind;
  id: string;
}

export interface Look {
  id: string;
  items: Partial<Record<Slot, ItemRef>>;
}

export interface Plan {
  looks: Look[];
  selected_offers: Record<string, string>;
}

export interface LockedPlacement {
  look_id: string;
  slot: Slot;
  item: ItemRef;
}

export type ReplacementTarget = LockedPlacement;

export type PlanOutcome =
  | "success"
  | "infeasible"
  | "generation_failed"
  | "provider_timeout"
  | "provider_error"
  | "restore_failed";

export interface PlanResponse {
  outcome: PlanOutcome;
  plan: Plan | null;
  plan_token: string | null;
  catalog_id: string | null;
  catalog_changed: boolean;
  conflicts: { rule: string | null; detail: string }[];
  violations: { rule: string; detail: string }[];
  error: string | null;
  notices: OwnedNotice[];
}

export interface Session {
  beta: boolean;
  invited: boolean;
  accounting: boolean;
  age_confirmed: boolean;
  photo_consent: boolean;
  plan_requests_used: number;
  plan_requests_limit: number;
  preview_requests_used: number;
  preview_requests_limit: number;
  planning_open: boolean;
  previews_open: boolean;
  pilot_ends_at: string | null;
  pilot_ended: boolean;
  records_purge_at: string | null;
  feedback_ids: string[];
  has_plan: boolean;
  /** Design my look is switched on and planning is open for this invite. */
  design_open: boolean;
}

export interface OwnedNotice {
  kind: "owned_reported" | "owned_unconfirmed";
  item: ItemRef;
  material: string;
}

export interface Brief {
  style_goal: string;
  preferred_colors: string[];
  occasion: Occasion | null;
  season: Season | null;
}

/** The structured state the server checks; the server adds the plan named by plan_token. */
export interface StateInput {
  budget: {
    amount_cents: number;
    mode: BudgetMode;
  };
  material_exclusions: Material[];
  fit?: Fit;
  footwear: FootwearChoice;
  owned_items: OwnedItem[];
  locks: LockedPlacement[];
}

export interface PlanRequest {
  brief: Brief;
  state: StateInput;
  replacement: ReplacementTarget | null;
  plan_token: string | null;
}

/** What Design my look checks: no owned items and no locks. */
export type ConceptState = Pick<StateInput, "budget" | "material_exclusions" | "footwear" | "fit">;

export interface ConceptRequest {
  brief: Brief;
  state: ConceptState;
}

export type ConceptOutcome = "success" | "generation_failed" | "provider_timeout" | "provider_error";

/** The closest catalog product code found for a concept item: a close match or an alternative
 * that differs or is unconfirmed as listed, each with its product, or none. */
export type ConceptMatch = { differences: string[] } & (
  | { status: "close" | "alternative"; variant_id: string; offer_id: string }
  | { status: "none"; variant_id: null; offer_id: null }
);

/** A concept item as the stylist model describes it; never a product. */
export interface ConceptItem {
  slot: Slot;
  kind: string;
  color: string;
  color_family: string;
  material: string;
  fit: string;
  details: string;
  match: ConceptMatch;
}

export interface Concept {
  id: string;
  name: string;
  why: string;
  items: ConceptItem[];
}

/** Two separate answers: complete when every item has a close match, and, once every item has a
 * product, whether those products together pass the hard checks. Shoppable as shown needs both. */
export interface Shoppable {
  complete: boolean;
  passes_hard_checks: boolean;
  matched_total_cents: number | null;
  violations: string[];
}

export interface ConceptResponse {
  outcome: ConceptOutcome;
  error: string | null;
  catalog_id: string;
  concept_token: string | null;
  concept: Concept | null;
  matching_failed: boolean;
  shoppable: Shoppable | null;
}

/** A checked plan kept on this device, with the facts needed to show it without the server. */
export interface SavedPlan {
  id: string;
  saved_at: string;
  name: string;
  brief: Brief;
  state: StateInput & { plan: Plan };
  catalog_id: string;
  shopping_list: ShoppingLine[];
}

export interface ShoppingLine {
  key: string;
  kind: "catalog" | "owned";
  name: string;
  details: string;
  looks: string[];
  price_cents: number | null;
  retailer: string | null;
  url: string | null;
  checked_at: string | null;
  stock: "in_stock" | "out_of_stock" | null;
}

export type RequestState = "idle" | "loading" | "success" | "error";
