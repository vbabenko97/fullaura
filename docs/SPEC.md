# Specification

This file describes what the first version of FullAura does, the rules it keeps, and what it leaves out. All of it is planned: the repository holds only a scaffold so far (see the [README](../README.md)). The build order is in [TASKS.md](TASKS.md), and the reasons behind each choice are in [DECISIONS.md](DECISIONS.md).

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

## Catalog records

A catalog record is one product variant, such as one size and color, with its SKU, price in EUR, composition, the countries it can be delivered to, link, source and the time its data was last verified. Size and stock are recorded when the source gives them. FullAura recommends only products that have a catalog record, and it chooses them before it generates a preview. Data that a record lacks stays unknown and is shown as unknown in the output. [DATA.md](DATA.md) has the rules for catalog sources and their rights.

FullAura keeps delivery to a country and stock in a local shop as separate facts.

## Hard constraints

A hard constraint is a rule that code checks before FullAura shows a plan. A catalog product that fails a check is left out of the plan. The first version has four:

- **Budget.** The cost stays within the budget. The person chooses whether the budget applies to each look or to the whole plan, meaning all three looks together. Each look is the default, and the interface then shows that the three looks together can cost more than the amount entered. When it applies to each look, code checks each look on its own. When it applies to the whole plan, a product variant that appears in more than one look counts once. A catalog product whose price is unknown fails this check.
- **Material exclusions.** Exclusions are hard filters for catalog products: while an exclusion is active, a catalog product that contains an excluded material fails this check, and so does one whose composition cannot be checked. The interface says that exclusions work this way. With no exclusion active, a catalog product with unknown composition can be used, and the interface labels its composition as unknown. Owned items follow their own rule (see [Owned items](#owned-items)).
- **Delivery.** Every catalog product can be delivered to Austria. A product whose delivery to Austria is not confirmed fails this check.
- **Locked items.** A locked item is one the person chose to keep. It stays in the look where the person locked it, through every edit.

When no plan meets every hard constraint, for example because the locked items cost more than the budget, FullAura reports that the plan is infeasible.

The selected SKU IDs, budget, exclusions and locked items are kept in structured application state, and code validates the hard constraints against that state. FullAura does not present a material exclusion as a medical assurance.

## Owned items

A person can add up to three items they already own, each as a category, a color and a short text description. Adding them is optional, and the person does not pick them from the catalog.

An owned item can take the place of a catalog product in a look. FullAura labels it "Already owned", adds nothing to the cost, and gives it no shopping link. The person can lock, unlock or remove an owned item.

Material exclusions apply to new purchases, so an owned item can stay in a look while an exclusion is active:

- When its composition is unknown, the interface labels it as unconfirmed for that exclusion.
- When its description names an excluded material, such as wool, the interface shows that material as reported by the person and says that the material is excluded from new purchases.

A photo of an owned item is planned for after the first complete flow and the feasibility test (T-07 in [TASKS.md](TASKS.md)).

## Previews

A preview is a generated image of the person wearing a look, or with a hair or beard style. FullAura labels every preview as a preview. A preview cannot establish fit, measurements, composition, price or availability, and a generated garment is not evidence that a product is sold or available. FullAura does not promise exact clothing fit or an achievable hair density.

A preview that shows an owned item draws it from the text description only, and FullAura presents that preview as approximate.

If previews fail the feasibility test, a person can still complete a sourced style plan, and FullAura states the visual limitation ([D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them)).

## Photographs

FullAura does not infer health, ethnicity, personality or other sensitive traits from photographs.

Before FullAura accepts photo uploads, it removes image metadata, and the retention and deletion behavior for photos is defined in [DATA.md](DATA.md). Before model access is open to the public, FullAura asks each person to confirm that they are 18 or older and shows a notice that explains how their photos are processed. Meta's Standard mode keeps content out of model training but does not mean zero retention ([D-07](DECISIONS.md#d-07-standard-mode-for-personal-photographs)), so the notice cannot rely on it.

## Out of scope

The first version leaves out full wardrobe ingestion, city-wide stock search, payments, booking, social features, and more than one agent service.
