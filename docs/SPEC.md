# Specification

This file describes what the first version of FullAura does, the rules it keeps, and what it leaves out. The catalog record format, a catalog of real product variants, the hard-constraint checks, planning with Muse Spark, and the closed beta exist: invited people plan three looks, try one on their own photos as an experimental visualization, keep and replace items, and save a shopping list. Hair and beard previews and photos of owned items are planned. The build order is in [TASKS.md](TASKS.md), and the reasons behind each choice are in [DECISIONS.md](DECISIONS.md).

## What a person does

A person opens an invite, confirms that they are 18 or older, and gives FullAura their style preferences, the shoes they want, their sizes and a budget, and can add a few items they already own. FullAura proposes three looks. A look is one coordinated outfit made of products from a small catalog, and it can include the person's own items. The person can try a look on their own photos, keep an item or change a constraint and get an updated plan, replace one item, and keep the shopping list for the plan.

The first version has an English interface, shows prices in EUR, and offers only products that can be delivered to Austria.

## Scope of the first version

| Area | First version |
| --- | --- |
| Inputs | Style goal, preferred colors, occasion and season (optional), budget, closed, open or any shoes, materials to exclude; whom the looks are for: clothing lines, kinds of bottoms and one-pieces, and sizes (optional); up to three owned items (optional); a portrait and a full-body photo for a try-on (optional) |
| Products | A small catalog that FullAura has permission to use (see [Catalog records](#catalog-records)) |
| Planning | Three looks; code checks the [hard constraints](#hard-constraints) |
| Models | Muse Spark for planning; Muse Image for the experimental try-on, which the feasibility test (T-02 in [TASKS.md](TASKS.md)) has not yet judged |
| Editing | Replace one item or lower the budget; locked items stay |
| Hair and beard | Planned, not in the closed beta: one or two optional previews; a reference card for a barber if preview quality allows |
| Output | Three looks, an experimental try-on, a shopping list with sources, costs and what is still unknown, plans saved on the device, and feedback |

## Look structure

Each look has a complete base outfit: either a top and bottom or a one-piece, not both. Every
item's category must match its slot. Extra layers and accessories are optional, and so is footwear unless the person chose closed or open shoes. Code
checks these rules with the hard constraints before it returns a plan. An invalid look gets one
repair attempt; if the plan still fails, the response has no plan and reports the violations
([D-29](DECISIONS.md#d-29-looks-have-a-complete-base-outfit)).

## Catalog records

A catalog record describes one product and its variants. A variant is one purchasable version of the product, such as one size and color, with its SKU and composition. Each variant has one or more offers, and an offer is one retailer's price in EUR, the countries it confirms delivery to, its stock and its shopping link. Every variant and offer keeps its source and the time its data was last checked against that source. Size and stock are recorded when the source gives them. A product also records the clothing line its retailer lists it under (women's, men's or unisex), for a bottom or one-piece its garment type, such as trousers, jeans or dress, and for a shoe whether it is closed or open. A variant records its size as the source labels it and, when it can be compared, in one sizing system: letter sizes, waist and length, or EU shoe sizes. FullAura does not convert sizes between systems. FullAura recommends only products that have a catalog record, and it chooses them before it generates a preview. Data that a record lacks stays unknown and is shown as unknown in the output. [DATA.md](DATA.md) has the rules for catalog sources and their rights.

A plan selects one offer for each variant it uses, and every look that contains the variant uses that offer's price, delivery and stock ([D-20](DECISIONS.md#d-20-a-plan-buys-each-variant-through-one-selected-offer)).

A composition is complete when the source names the material of every part that EU labelling covers. For clothing, that is each textile component, except a component that is not a main lining and makes up less than 30% of the weight. For shoes, it is the upper, the lining and insole, and the sole. Trims that labelling leaves out, such as buttons, zips, elastic bands and laces, do not decide completeness ([D-27](DECISIONS.md#d-27-a-composition-is-complete-when-it-names-every-labelled-part)). A composition can also be partial, when a labelled part is missing or named only vaguely, such as "textile/synthetic"; unknown; or conflicting, when sources disagree. Components name materials from a fixed list, which also accepts other common names, such as PU for polyurethane and merino or lambswool for wool. The record also keeps the source's own wording.

FullAura keeps delivery to a country and stock in a local shop as separate facts.

## Hard constraints

A hard constraint is a rule that code checks before FullAura shows a plan. A catalog product that fails a check is left out of the plan. The first version has seven:

- **Budget.** The cost stays within the budget. The person chooses whether the budget applies to each look or to the whole plan, meaning all three looks together. Each look is the default, and the interface then shows that the three looks together can cost more than the amount entered. When it applies to each look, code checks each look on its own. When it applies to the whole plan, a product variant that appears in more than one look counts once; two colors or sizes of one product are different variants. Each variant costs the price of its selected offer. A catalog product whose price is unknown fails this check.
- **Material exclusions.** Exclusions are hard filters for catalog products: while an exclusion is active, a catalog product that contains an excluded material fails this check, and so does one whose composition cannot be checked. Only a complete composition can be checked. The check covers every material the record lists, including a trim whose material the source names, such as a metal zip; a trim that the source does not name, or names only vaguely, such as a zip "aus Plastik", is not checked. An exclusion names a material from the same fixed list as the catalog, so an exclusion of PU is an exclusion of polyurethane, and FullAura does not accept an exclusion whose name is not on the list ([D-23](DECISIONS.md#d-23-materials-are-compared-by-names-from-a-fixed-list)). The interface says that exclusions work this way. With no exclusion active, a catalog product with unknown composition can be used, and the interface labels its composition as unknown. Owned items follow their own rule (see [Owned items](#owned-items)).
- **Delivery.** Every catalog product can be delivered to Austria. A product whose selected offer does not confirm delivery to Austria fails this check.
- **Stock.** A product whose selected offer reports it out of stock fails this check; another offer of the same variant can be selected instead. When the offer gives no stock, the product can be used, and the interface shows its stock as unknown ([D-24](DECISIONS.md#d-24-an-offer-reported-out-of-stock-fails)).
- **Fit.** The person can state whom the looks are for: the clothing lines to show, the kinds of bottoms and one-pieces they wear, and the sizes they accept in each sizing system, such as M, waist 30, and EU 39.5 or 40. FullAura never infers these from photographs. While lines are chosen, a catalog product must belong to one of them or be unisex; a product with no recorded line fails. While garment types are chosen, a bottom or one-piece must have one of them; one with no recorded type fails. When the person gives a size in a variant's sizing system, the variant must match one of those sizes; a waist without a length matches every length. A variant passes the size check when the person gave no size in its system, and fails it when the person gave any size and the variant's size cannot be compared. Owned items are not checked ([D-36](DECISIONS.md#d-36-the-person-states-whom-the-looks-are-for)).
- **Footwear.** The person chooses closed, open or any shoes; the interface starts at closed. With closed or open, every look needs footwear, and a catalog shoe whose closure is the other one, or is not recorded, fails. A catalog record takes a shoe's closure from the retailer's product type, such as a sneaker or a slide. Owned shoes count as footwear and are not checked ([D-45](DECISIONS.md#d-45-people-choose-closed-open-or-any-shoes)).
- **Locked items.** A locked item is one the person chose to keep. It stays in the look where the person locked it, in the same position, such as footwear, through every edit. The same item in another look can still change ([D-22](DECISIONS.md#d-22-a-lock-holds-one-position-in-one-look)).

When no plan meets every hard constraint, for example because the locked items cost more than the budget, or because no catalog item that passes the item checks and no owned item can complete a top and bottom or a one-piece, FullAura reports that the plan is infeasible. It says so only when code shows that no plan can pass. When the model's plans keep failing the checks, or the model does not answer in time, FullAura says that instead and does not call the request infeasible.

When a person replaces an item, the request identifies the current item, look and slot. The person cannot replace a locked item. Every other position in all three looks, including empty positions, stays as it was. Code requires a different item in the selected position and checks the complete returned plan against the current budget, exclusions, delivery, stock, composition, fit and locks. If replacement fails, the previous plan remains displayed.

The interface disables replacement when no other catalog variant that passes the fit and footwear checks, and no owned item, has a category that can fill the position. Current constraints can still leave no valid replacement.

The selected variants and offers, budget, exclusions, fit, footwear choice, owned items and locked items are kept in structured application state, and code validates the hard constraints against that state. FullAura does not present a material exclusion as a medical assurance. Occasion, season and preferred colors are wishes for the model; code does not check them.

## Plans across requests

A successful plan comes with an opaque token. The server keeps the last checked plan of each browser session in memory, and an update, a replacement or a try-on names that plan by its token, so the browser never sends a plan it could have changed. When a request fails, the previous plan and its token stay current. The browser keeps its working plan for the open tab and up to ten plans saved on the device, with their shopping lists and without photos or try-ons. Restoring a saved plan, or the working plan after the server restarted, checks it again against the current catalog and every hard constraint, chooses its offers again and makes it current, without a model call. A saved plan that fails the checks is not restored, but its saved shopping list stays readable ([D-46](DECISIONS.md#d-46-the-server-keeps-the-checked-plan-and-the-browser-keeps-saved-copies)).

## Shopping list

The shopping list has one line for each catalog variant and each owned item in the plan, with the looks that use it. A variant that appears in several looks is listed and counted once; an owned item costs nothing and has no shopping link. Each catalog line shows the selected offer's price, retailer link, the date the price was checked, and stock when the offer reports it. The interface says that the total is the items' price, without delivery or returns, and not a guaranteed final cost. The list can be copied as text or printed.

## Feedback

An invited person can send short feedback: whether they would wear at least one of the looks, how useful FullAura was on a scale of 1 to 5, how good the try-on was on a scale of 1 to 5 or that they cannot judge it, a problem with the products, and an optional comment of up to 500 characters. Feedback holds no photo, plan, profile or provider response, and the person can delete it.

## Owned items

A person can add up to three items they already own, each as a category, a color and a short text description. Adding them is optional, and the person does not pick them from the catalog.

An owned item can take the place of a catalog product in a look. FullAura labels it "Already owned", adds nothing to the cost, and gives it no shopping link. The person can lock, unlock or remove an owned item.

Material exclusions apply to new purchases, so an owned item can stay in a look while an exclusion is active:

- When its composition is unknown, the interface labels it as unconfirmed for that exclusion.
- When its description names an excluded material, such as wool, the interface shows that material as reported by the person and says that the material is excluded from new purchases. A description names a material when it contains the material's name or another name for it as whole words, as in "merino jumper". Where names overlap, the longest counts, so "synthetic rubber sole" names synthetic rubber and not rubber. A description that negates the material, such as "wool-free scarf" or "no merino wool", does not name it, and the item is labeled as unconfirmed.

A photo of an owned item is planned for after the first complete flow and the feasibility test (T-07 in [TASKS.md](TASKS.md)).

## Previews

A preview is a generated image of the person wearing a look, or with a hair or beard style. FullAura labels every preview as a preview. A preview cannot establish fit, measurements, composition, price or availability, and a generated garment is not evidence that a product is sold or available. FullAura does not promise exact clothing fit or an achievable hair density.

In the closed beta, the try-on is an experimental visualization of one look of the current plan ([D-44](DECISIONS.md#d-44-the-betas-try-on-is-experimental-and-does-not-wait-for-t-02)). The server writes its prompt from the checked look: each catalog item's garment kind, catalog color, title and complete materials, and for an owned item its category and the person's own color and description, reduced to plain words, cut to 80 characters and quoted. It sends no product image and no other text from the browser, so the result draws approximate colors, shapes and combinations, and the interface says so. A try-on belongs to the plan it was made for; a changed plan hides it.

The try-on accepts a portrait and a full-body photo. The browser redraws a JPEG, PNG or WebP photo as an upright JPEG without metadata and explains how to convert a photo it cannot read. The server accepts only JPEG, checks every access rule before it reads the upload, and has one 120-second deadline from upload through provider response. It cancels provider work when that deadline expires or the browser disconnects, and keeps photo data in memory only for that request ([D-31](DECISIONS.md#d-31-no-persistent-server-copies-of-personal-photos), [D-32](DECISIONS.md#d-32-preview-requests-have-one-deadline-and-cancel-on-disconnect)).

If the formal feasibility test fails, a person can still complete a sourced style plan, and FullAura states the visual limitation ([D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them)).

## Photographs

FullAura does not infer health, ethnicity, personality or other sensitive traits from photographs, and it never reads sizes or a clothing line from them.

Before a first try-on, FullAura shows a notice that explains how photos are processed and asks the person to agree to it, separately from the 18+ confirmation; the person can withdraw that consent. Withdrawing it stops a try-on in progress and removes the chosen photos and any result from the page at once, and a photo still being prepared or a try-on answer that arrives later is discarded; photos already sent to Meta cannot be called back. Meta's Standard mode keeps content out of model training but does not mean zero retention ([D-07](DECISIONS.md#d-07-standard-mode-for-personal-photographs)), so the notice says that Meta may keep content under its terms.

## Closed beta access

Production serves the catalog, planning, try-on and feedback routes only when `FULLAURA_BETA_ENABLED=true`, and then only to invited sessions ([D-43](DECISIONS.md#d-43-an-invite-only-beta-replaces-the-anonymous-demo)). An invite code opens a signed, HTTP-only session cookie; the server holds only digests of the codes. Each invite confirms its age once and may make 8 planning requests and 12 try-ons, one model request at a time, even while two revisions of the app overlap during a deployment ([D-48](DECISIONS.md#d-48-a-running-call-blocks-its-invite-whichever-process-started-it)). A shared ledger reserves each model call's maximum cost before the call and closes calls of a kind when its budget, $8 for planning or $3 for images, is used up. A repeated request with the same operation ID never starts a second paid call. Quotas, the ledger and feedback survive restarts; when they cannot be read or written, model calls stop and plans stay available. The pilot's end closes model calls. At `FULLAURA_RECORDS_PURGE_AT`, which must come after the end, a scheduled job deletes every pilot record and the beta closes, so no new record appears; the interface tells participants that time ([D-49](DECISIONS.md#d-49-a-scheduled-job-deletes-the-pilot-records-and-the-beta-closes-then)). A write that the app accepted just before then either finishes before the deletion or is refused, and no record reappears afterwards, provided each request reaches storage within about a minute ([D-50](DECISIONS.md#d-50-a-closure-marker-and-a-grace-period-keep-writes-from-outliving-the-purge)). [BETA.md](BETA.md) describes the pilot for participants and the operator.

## Out of scope

The first version leaves out full wardrobe ingestion, city-wide stock search, payments, booking, social features, and more than one agent service.
