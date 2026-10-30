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

export const FIT_LINES = ["men", "women"] as const;
export const PRODUCT_LINES = [...FIT_LINES, "unisex"] as const;
export const GARMENT_TYPES = ["trousers", "jeans", "shorts", "skirt", "leggings", "dress"] as const;
export const LETTER_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL"] as const;
export const SIZE_SYSTEMS = ["letter", "waist_length", "eu_shoe"] as const;
export const MAX_FIT_SIZES = 12;
export const WAIST_SIZE = /^\d{2}(\/\d{2})?$/;
export const EU_SHOE_SIZE = /^\d{2}(\.5)?$/;

export type Material = (typeof MATERIALS)[number];
export type Category = (typeof CATEGORIES)[number];
export type Slot = (typeof SLOTS)[number];
export type FitLine = (typeof FIT_LINES)[number];
export type GarmentType = (typeof GARMENT_TYPES)[number];
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

export interface Variant {
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

export interface Product {
  id: string;
  title: string;
  brand: string | null;
  category: Category;
  line: (typeof PRODUCT_LINES)[number] | null;
  garment_type: GarmentType | null;
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

export interface PlanResponse {
  outcome: "success" | "infeasible" | "generation_failed" | "provider_timeout" | "provider_error";
  plan: Plan | null;
  conflicts: { rule: string | null; detail: string }[];
  violations: { rule: string; detail: string }[];
  error: string | null;
  notices: OwnedNotice[];
}

export interface PreviewStatus {
  enabled: boolean;
  detail: string | null;
}

export interface DemoSession {
  enabled: boolean;
  age_confirmed: boolean;
  plan_requests_used: number;
  plan_requests_limit: number;
  plan_requests_remaining: number;
}


export interface OwnedNotice {
  kind: "owned_reported" | "owned_unconfirmed";
  item: ItemRef;
  material: string;
}

export interface PlanRequest {
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

export type RequestState = "idle" | "loading" | "success" | "error";
