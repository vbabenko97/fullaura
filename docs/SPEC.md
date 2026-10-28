# Specification

This file describes what the first version of FullAura does, the rules it keeps, and what it leaves out. Most of it is planned. The catalog record format, a first catalog of 15 real products, the hard-constraint checks, and a local development screen for preferences and sourced look results exist. Photo upload and previews do not exist yet (see the [README](../README.md)). The build order is in [TASKS.md](TASKS.md), and the reasons behind each choice are in [DECISIONS.md](DECISIONS.md).

## What a person does

A person gives FullAura their photos, style preferences and budget, and can add a few items they already own. FullAura proposes three looks. A look is one coordinated outfit made of products from a small catalog, and it can include the person's own items. The person can keep an item or change a constraint, and then gets an updated plan with previews and shopping links.

The first version has an English interface, shows prices in EUR, and offers only products that can be delivered to Austria.

## Scope of the first version

| Area | First version |
| --- | --- |
| Inputs | A portrait and a full-body photo; style goal, preferred colors, budget, materials to exclude; up to three owned items (optional) |
| Products | A small catalog that FullAura has permission to use (see [Catalog records](#catalog-records)) |
| Planning | Three looks; code checks the [hard constraints](#hard-constraints) |
| Models | Muse Spark for planning and tool calls; Muse Image for previews; subject to the feasibility test (T-02 in [TASKS.md](TASKS.md)) |
| Editing | Replace one item or lower the budget; locked items stay |
| Hair and beard | One or two optional previews; a reference card for a barber if preview quality allows |
| Output | Previews, a shopping list with sources and costs, and what is still unknown |

## Look structure

Each look has a complete base outfit: either a top and bottom or a one-piece, not both. Every
item's category must match its slot. Extra layers, footwear and accessories are optional. Code
checks these rules with the hard constraints before it returns a plan. An invalid look gets one
repair attempt; if the plan still fails, the response has no plan and reports the violations
([D-29](DECISIONS.md#d-29-looks-have-a-complete-base-outfit)).

## Catalog records

A catalog record describes one product and its variants. A variant is one purchasable version of the product, such as one size and color, with its SKU and composition. Each variant has one or more offers, and an offer is one retailer's price in EUR, the countries it confirms delivery to, its stock and its shopping link. Every variant and offer keeps its source and the time its data was last checked against that source. Size and stock are recorded when the source gives them. FullAura recommends only products that have a catalog record, and it chooses them before it generates a preview. Data that a record lacks stays unknown and is shown as unknown in the output. [DATA.md](DATA.md) has the rules for catalog sources and their rights.

A plan selects one offer for each variant it uses, and every look that contains the variant uses that offer's price, delivery and stock ([D-20](DECISIONS.md#d-20-a-plan-buys-each-variant-through-one-selected-offer)).

A composition is complete when the source names the material of every part that EU labelling covers. For clothing, that is each textile component, except a component that is not a main lining and makes up less than 30% of the weight. For shoes, it is the upper, the lining and insole, and the sole. Trims that labelling leaves out, such as buttons, zips, elastic bands and laces, do not decide completeness ([D-27](DECISIONS.md#d-27-a-composition-is-complete-when-it-names-every-labelled-part)). A composition can also be partial, when a labelled part is missing or named only vaguely, such as "textile/synthetic"; unknown; or conflicting, when sources disagree. Components name materials from a fixed list, which also accepts other common names, such as PU for polyurethane and merino or lambswool for wool. The record also keeps the source's own wording.

FullAura keeps delivery to a country and stock in a local shop as separate facts.

## Hard constraints

A hard constraint is a rule that code checks before FullAura shows a plan. A catalog product that fails a check is left out of the plan. The first version has five:

- **Budget.** The cost stays within the budget. The person chooses whether the budget applies to each look or to the whole plan, meaning all three looks together. Each look is the default, and the interface then shows that the three looks together can cost more than the amount entered. When it applies to each look, code checks each look on its own. When it applies to the whole plan, a product variant that appears in more than one look counts once; two colors or sizes of one product are different variants. Each variant costs the price of its selected offer. A catalog product whose price is unknown fails this check.
- **Material exclusions.** Exclusions are hard filters for catalog products: while an exclusion is active, a catalog product that contains an excluded material fails this check, and so does one whose composition cannot be checked. Only a complete composition can be checked. The check covers every material the record lists, including a trim whose material the source names, such as a metal zip; a trim that the source does not name, or names only vaguely, such as a zip "aus Plastik", is not checked. An exclusion names a material from the same fixed list as the catalog, so an exclusion of PU is an exclusion of polyurethane, and FullAura does not accept an exclusion whose name is not on the list ([D-23](DECISIONS.md#d-23-materials-are-compared-by-names-from-a-fixed-list)). The interface says that exclusions work this way. With no exclusion active, a catalog product with unknown composition can be used, and the interface labels its composition as unknown. Owned items follow their own rule (see [Owned items](#owned-items)).
- **Delivery.** Every catalog product can be delivered to Austria. A product whose selected offer does not confirm delivery to Austria fails this check.
- **Stock.** A product whose selected offer reports it out of stock fails this check; another offer of the same variant can be selected instead. When the offer gives no stock, the product can be used, and the interface shows its stock as unknown ([D-24](DECISIONS.md#d-24-an-offer-reported-out-of-stock-fails)).
- **Locked items.** A locked item is one the person chose to keep. It stays in the look where the person locked it, in the same position, such as footwear, through every edit. The same item in another look can still change ([D-22](DECISIONS.md#d-22-a-lock-holds-one-position-in-one-look)).

When no plan meets every hard constraint, for example because the locked items cost more than the budget, FullAura reports that the plan is infeasible. It says so only when code shows that no plan can pass. When the model's plans keep failing the checks, or the model does not answer in time, FullAura says that instead and does not call the request infeasible.

The selected variants and offers, budget, exclusions, owned items and locked items are kept in structured application state, and code validates the hard constraints against that state. FullAura does not present a material exclusion as a medical assurance.

## Owned items

A person can add up to three items they already own, each as a category, a color and a short text description. Adding them is optional, and the person does not pick them from the catalog.

An owned item can take the place of a catalog product in a look. FullAura labels it "Already owned", adds nothing to the cost, and gives it no shopping link. The person can lock, unlock or remove an owned item.

Material exclusions apply to new purchases, so an owned item can stay in a look while an exclusion is active:

- When its composition is unknown, the interface labels it as unconfirmed for that exclusion.
- When its description names an excluded material, such as wool, the interface shows that material as reported by the person and says that the material is excluded from new purchases. A description names a material when it contains the material's name or another name for it as whole words, as in "merino jumper". Where names overlap, the longest counts, so "synthetic rubber sole" names synthetic rubber and not rubber. A description that negates the material, such as "wool-free scarf" or "no merino wool", does not name it, and the item is labeled as unconfirmed.

A photo of an owned item is planned for after the first complete flow and the feasibility test (T-07 in [TASKS.md](TASKS.md)).

## Previews

A preview is a generated image of the person wearing a look, or with a hair or beard style. FullAura labels every preview as a preview. A preview cannot establish fit, measurements, composition, price or availability, and a generated garment is not evidence that a product is sold or available. FullAura does not promise exact clothing fit or an achievable hair density.

A preview that shows an owned item draws it from the text description only, and FullAura presents that preview as approximate. The same holds for a catalog product that has no product image FullAura may use ([D-26](DECISIONS.md#d-26-the-first-catalog-records-facts-from-armedangels-and-giesswein-without-images)).

If previews fail the feasibility test, a person can still complete a sourced style plan, and FullAura states the visual limitation ([D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them)).

## Photographs

FullAura does not infer health, ethnicity, personality or other sensitive traits from photographs.

Photo upload and storage are not implemented. Before FullAura accepts uploads, it will remove image metadata and apply the accepted no-persistent-server-copy policy in [DATA.md](DATA.md#photo-handling-for-upload-support). Before model access is open to the public, FullAura asks each person to confirm that they are 18 or older and shows a notice that explains how their photos are processed. Meta's Standard mode keeps content out of model training but does not mean zero retention ([D-07](DECISIONS.md#d-07-standard-mode-for-personal-photographs)), so the notice cannot rely on it.

## Out of scope

The first version leaves out full wardrobe ingestion, city-wide stock search, payments, booking, social features, and more than one agent service.
