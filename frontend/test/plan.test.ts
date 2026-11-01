import { describe, expect, it } from "vitest";
import { hasReplacementAlternative, readableDetail } from "../src/plan";
import type { Catalog, ItemRef, OwnedItem } from "../src/types";

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
