import { describe, expect, it } from "vitest";
import { hasReplacementAlternative, passedChecksText, readableDetail, shoppingList, uncheckedText } from "../src/plan";
import type { Catalog, Fit, ItemRef, OwnedItem, Plan } from "../src/types";

/* A hand-made catalog: one shirt in two sizes and two colors, and a pair of shoes. */
const variant = (id: string, color: string) => ({ id, color, normalized_size: null, offers: [{ id: `${id}-offer` }] });
const catalog = {
  products: [
    {
      id: "shirt",
      title: "LINEN SHIRT",
      category: "top",
      line: null,
      garment_type: null,
      footwear_closure: null,
      variants: [variant("shirt-navy-m", "navy"), variant("shirt-navy-l", "navy")],
    },
    {
      id: "shoe",
      title: "CANVAS SHOE",
      category: "footwear",
      line: null,
      garment_type: null,
      footwear_closure: "closed",
      variants: [variant("shoe-42", "white")],
    },
  ],
} as unknown as Catalog;

const owned: OwnedItem[] = [{ id: "owned-1", category: "top", color: "red", description: "old red shirt" }];
const shirt: ItemRef = { kind: "catalog", id: "shirt-navy-m" };

describe("hasReplacementAlternative", () => {
  it("does not count another size of the same garment", () => {
    expect(hasReplacementAlternative(catalog, [], shirt, "top", undefined, "any")).toBe(false);
  });

  it("counts another color of the product", () => {
    const colors = {
      products: [
        {
          ...catalog.products[0],
          variants: [variant("shirt-navy-m", "navy"), variant("shirt-green-m", "green")],
        },
      ],
    } as unknown as Catalog;
    expect(hasReplacementAlternative(colors, [], shirt, "top", undefined, "any")).toBe(true);
  });

  it("counts an owned item", () => {
    expect(hasReplacementAlternative(catalog, owned, shirt, "top", undefined, "any")).toBe(true);
  });
});

describe("what a plan's checks could not confirm", () => {
  /* A hand-made plan: the top is in two looks, the bag has no size, and the skirt's shop reports no stock. */
  const sized = (id: string, system: string | null, value: string | null, stock: string | null = "in_stock") => ({
    id,
    color: "black",
    size: value,
    normalized_size: system && value ? { system, value } : null,
    offers: [{ id: `${id}-offer`, stock, price_cents: 1000, url: "https://shop.example/", retailer: "Shop", source: { checked_at: "2026-11-01T10:00:00Z" } }],
  });
  const fitCatalog = {
    products: [
      { id: "t", title: "TOP", brand: "B", category: "top", variants: [sized("top-m", "letter", "M")] },
      { id: "s", title: "SKIRT", brand: "B", category: "bottom", variants: [sized("skirt-m", "letter", "M", null)] },
      { id: "c", title: "CHINO", brand: "B", category: "bottom", variants: [sized("chino-32", "waist_length", "32/32")] },
      { id: "f", title: "SHOE", brand: "B", category: "footwear", variants: [sized("shoe-39", "eu_shoe", "39")] },
      { id: "b", title: "BAG", brand: "B", category: "accessory", variants: [sized("bag", null, null)] },
    ],
  } as unknown as Catalog;
  const refs = (id: string): ItemRef => ({ kind: "catalog", id });
  const offers = ["top-m", "skirt-m", "chino-32", "shoe-39", "bag"].map((id) => [id, `${id}-offer`]);
  const plan = {
    looks: [
      {
        id: "look_1",
        items: { top: refs("top-m"), bottom: refs("skirt-m"), footwear: refs("shoe-39"), accessory: refs("bag") },
      },
      {
        id: "look_2",
        items: { top: refs("top-m"), bottom: refs("chino-32"), footwear: refs("shoe-39"), outer_layer: { kind: "owned", id: "owned-1" } },
      },
    ],
    selected_offers: Object.fromEntries(offers),
  } as unknown as Plan;
  const stocked = {
    ...plan,
    looks: [{ id: "look_1", items: { top: refs("top-m"), bottom: refs("chino-32"), footwear: refs("shoe-39") } }],
  } as unknown as Plan;
  const waistOnly: Fit = { sizes: [{ system: "waist_length", value: "50" }] };
  const everySystem: Fit = {
    sizes: [
      { system: "letter", value: "M" },
      { system: "waist_length", value: "32" },
      { system: "eu_shoe", value: "39" },
    ],
  };

  it("names the size systems the person gave no size in, counting each product once", () => {
    const text = uncheckedText(plan, fitCatalog, waistOnly) ?? "";
    expect(text).toContain("letter sizes on 2 items");
    expect(text).toContain("EU shoe sizes on 1 item");
    expect(text).not.toContain("waist and length on");
  });

  it("names stock the shop does not report, and no sizes once every system was given", () => {
    const text = uncheckedText(plan, fitCatalog, everySystem) ?? "";
    expect(text).toContain("Stock not reported for 1 item");
    expect(text).not.toContain("Sizes not checked");
  });

  it("has nothing to say when every size was compared and every stock reported", () => {
    expect(uncheckedText(stocked, fitCatalog, everySystem)).toBeNull();
  });

  it("claims sizes and stock only when they were checked", () => {
    expect(passedChecksText(stocked, fitCatalog, everySystem)).toContain(
      "budget, sizes, shoes, materials, delivery and stock.",
    );
    expect(passedChecksText(plan, fitCatalog, waistOnly)).toContain("budget, shoes, materials and delivery.");
  });

  it("marks a product's size in the shopping list when it was not compared", () => {
    const details = Object.fromEntries(
      shoppingList(plan, fitCatalog, [], waistOnly).map((line) => [line.name, line.details]),
    );
    expect(details.SKIRT).toContain("size not checked");
    expect(details.CHINO).not.toContain("size not checked");
  });
});

describe("readableDetail", () => {
  const read = (rule: string, detail: string) => readableDetail({ rule, detail }, catalog, owned);

  it.each([
    ["fit", "shirt-navy-m is letter size M, not L", "LINEN SHIRT is letter size M, not L"],
    [
      "one_size",
      "shirt-navy-m and shirt-navy-l are two sizes of one product; use one in every look",
      "LINEN SHIRT and LINEN SHIRT are two sizes of one product; use one in every look",
    ],
    ["budget", "EUR 124.88 for look look_1, over the budget", "EUR 124.88 for look 1, over the budget"],
    ["lock", "owned-1 is locked in look_2 as outer_layer", "your old red shirt is kept in look 2 as outer layer"],
    ["stock", "shoe-42-offer reports it out of stock", "The offer for CANVAS SHOE reports it out of stock"],
    ["unknown_item", "No catalog record removed-product-m", "An item in this plan is no longer in the catalog."],
    ["unknown_item", "No owned item owned-9", "An item in this plan is no longer in the catalog."],
  ])("%s: %s", (rule, detail, expected) => {
    expect(read(rule, detail)).toBe(expected);
  });

  it("leaves the catalog IDs alone before the catalog has loaded", () => {
    expect(readableDetail({ rule: "fit", detail: "shirt-navy-m is letter size M" }, null, [])).toBe(
      "shirt-navy-m is letter size M",
    );
  });
});
