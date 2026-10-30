# Decisions

This file records decisions about FullAura's product and code, with the reason for each, so that a settled point is not reopened by accident. [SPEC.md](SPEC.md) describes what the product does, and [AGENTS.md](../AGENTS.md) holds the working rules for coding agents; this file says why.

Each decision has a number that is never reused and a status: accepted, proposed (not decided yet), or superseded when a later decision replaces it. After an entry is accepted, only its status line changes. To change a decision, add a new entry and set the old one's status to superseded, with a link to the new one. When a proposed decision is accepted as written, change its status in place.

## D-01: Owned items are a short, optional text input

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** A person can add up to three items they already own, each as a category, a color and a short description. An owned item can take the place of a catalog product in a look, and it can be locked. [SPEC.md](SPEC.md#owned-items) has the rules.
- **Reason:** It keeps the first version simple.
- **Consequences:** Previews show owned items only approximately, because they are drawn from text. A photo of an owned item can follow after the first complete flow and the feasibility test (T-07 in [TASKS.md](TASKS.md)).

## D-02: The person chooses how the budget applies

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** The person chooses whether the budget applies to each look or to the whole plan. [SPEC.md](SPEC.md#hard-constraints) has the rules.
- **Reason:** Some people buy one look and others buy all three.

## D-03: An owned item with unknown composition can stay in a look

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** Under a material exclusion, an owned item whose composition is unknown can stay in a look, labeled as unconfirmed for that exclusion. [SPEC.md](SPEC.md#owned-items) has the rule.
- **Reason:** Material exclusions are filters for what to buy, and the person already owns the item.

## D-04: Python and FastAPI, React and Vite

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** The backend uses Python 3.12 and FastAPI. The frontend uses React, TypeScript and Vite on Node 24.
- **Reason:** The scaffold already uses this stack, and it is practical for fast delivery.

## D-05: One backend and one frontend

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** LookPilot runs as one FastAPI backend that also serves the built frontend. A database, a job queue or an extra model is added only when a working use case needs it.
- **Reason:** Fewer moving parts and a simpler deployment.

## D-06: Model calls stay on the server

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** Only the backend calls Meta's models.
- **Reason:** It protects API keys and keeps usage controls in one place.

## D-07: Standard mode for personal photographs

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** Personal photographs go only to Meta models in Standard mode, never in Contributor mode, which trades lower prices for Meta's permission to train future models on the content.
- **Reason:** It protects personal data and follows Meta's rules for personal information.
- **Consequences:** Meta states that Standard mode content is not used to train its models, and it states no retention period ([Meta pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits), checked 2026-10-26). Standard mode therefore does not imply zero retention, and the photo-processing notice in [SPEC.md](SPEC.md#photographs) cannot rely on it.

## D-08: Muse Spark plans and Muse Image draws

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** Muse Spark plans looks and calls tools. Muse Image generates previews. Both are subject to the feasibility test (T-02 in [TASKS.md](TASKS.md)).
- **Reason:** These are Meta's models for planning and image tasks, and the feasibility test checks them before the product depends on them.

## D-09: Recommendations come only from catalog records

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** LookPilot recommends only products that have a catalog record, and it chooses them before it generates a preview.
- **Reason:** Recommendations, prices and materials have to trace back to a record.

## D-10: English, EUR and delivery to Austria

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** The first version has an English interface, prices in EUR, and only products that can be delivered to Austria. This can change after the first user test.
- **Reason:** It keeps the first pilot focused on one language, one currency and one delivery market.

## D-11: Test previews first, and keep the plan useful without them

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** The feasibility test (T-02 in [TASKS.md](TASKS.md)) runs before the interface grows. If previews fail it, LookPilot narrows what it promises visually: a person can still complete a sourced style plan, and the interface states the visual limitation clearly.
- **Reason:** Previews are the main product risk, so the test checks them early, and the plan stays useful if they fail.

## D-12: One Azure Container App for the demo

- **Date:** 2026-10-26
- **Status:** accepted on 2026-10-26 as the initial deployment direction
- **Decision:** Host the demo as one Azure Container App, with the settings in [DEPLOYMENT.md](DEPLOYMENT.md).
- **Reason:** Azure credits are available, and the deployment is simple and needs little operation.
- **Consequences:** Region, quota and replica settings depend on testing. Scaling to zero is not a spending cap.

## D-13: Personal data stays out of Git

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** Personal photographs, generated personal previews, raw provider payloads, `.env` and API keys are never committed. Private files live in `data/private/`, which Git ignores.
- **Reason:** It keeps personal data and provider payloads out of Git.

## D-14: MIT license for source code

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** LookPilot's source code is under the MIT license. Third-party models, product data and photographs keep their own terms.
- **Reason:** MIT is already the repository's code license, and it permits reuse.

## D-15: Agents' working files go in tmp/

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** Agents put temporary files and working notes in `tmp/`, which Git and Docker ignore. `tests/test_repo_layout.py` checks the root entries, leftover copies, scratch files and session reports, and the names of Markdown files in `docs/`. [AGENTS.md](../AGENTS.md#files-and-folders) has the rules.
- **Reason:** It keeps temporary agent files out of the repository and the Docker build context, and the test stays narrow enough to allow normal development.

## D-16: An owned item that names an excluded material can stay

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** When an owned item's description names an excluded material, such as wool, the item can stay in a look. The interface shows the material as reported by the person, says that the material is excluded from new purchases, and lets the person remove or unlock the item. [SPEC.md](SPEC.md#owned-items) has the rule.
- **Reason:** Material exclusions apply to new purchases, as in D-03.

## D-17: The budget applies to each look by default

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** The budget starts in per-look mode, and the interface shows that the three looks together can cost more than the amount entered. The whole-plan mode from D-02 stays available.
- **Reason:** Not recorded.

## D-18: Unknown composition matters only under an active exclusion

- **Date:** 2026-10-26
- **Status:** accepted
- **Decision:** With no material exclusion active, a catalog product with unknown composition can be used, labeled as unknown. While an exclusion is active, catalog products whose composition cannot be checked are left out. [SPEC.md](SPEC.md#hard-constraints) has the rule.
- **Reason:** Not recorded.

## D-19: The product is named FullAura

- **Date:** 2026-10-27
- **Status:** accepted
- **Decision:** The product and its GitHub repository are named FullAura. Code identifiers, environment variables, Docker images and Azure resource names use the lowercase form `fullaura`.
- **Reason:** Not recorded.
- **Consequences:** The product was called LookPilot until this decision, and earlier entries keep that name because accepted entries do not change. The repository moved from `vbabenko97/lookpilot` to `vbabenko97/fullaura`, and GitHub redirects the old URLs. The environment variables `LOOKPILOT_ENV` and `LOOKPILOT_WEB_DIST` became `FULLAURA_ENV` and `FULLAURA_WEB_DIST`.

## D-20: A plan buys each variant through one selected offer

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** A catalog product has variants, such as one color and size, and each variant has one or more retailer offers with a price, delivery countries and stock. A plan selects one offer for each variant it uses. Every look that contains the variant uses that offer's price and delivery, and in whole-plan mode the variant counts once at that price. [SPEC.md](SPEC.md#catalog-records) has the record format.
- **Reason:** Price, delivery and stock belong to a retailer's offer rather than to the garment, and one variant can be sold by several retailers. With one selected offer, a variant has the same price in every look.
- **Consequences:** When FullAura tests whether the locked items alone exceed the budget, a locked variant counts at its cheapest offer that passes the other checks, because a plan can still select that offer.

## D-21: Only a complete composition passes a material exclusion

- **Date:** 2026-10-28
- **Status:** superseded by [D-23](#d-23-materials-are-compared-by-names-from-a-fixed-list)
- **Decision:** While an exclusion is active, a catalog product passes only when its composition is complete and no listed component's name includes the excluded material. A partial, unknown or conflicting composition fails, and a product whose listed components include the material fails as containing it. Matching ignores case and accepts the material inside a longer name, so an exclusion of wool also matches merino wool and lambswool. [SPEC.md](SPEC.md#hard-constraints) has the rule.
- **Reason:** A status label alone cannot show that wool is absent; the components must be listed. A partial composition, such as a shoe's upper without its sole, cannot show that the rest is free of the material. Matching inside longer names leaves out more products than exact matching, which is the safer error for a hard filter.
- **Consequences:** An exclusion of a short name can leave out unrelated products whose material names contain it. Catalog records name each material by its generic name (see [DATA.md](DATA.md)), because a trade name or abbreviation that does not contain the generic name would not match. Owned items use the same matching to decide whether their description names an excluded material ([D-16](#d-16-an-owned-item-that-names-an-excluded-material-can-stay)), so a description such as "wool-free" is labeled as reporting wool.

## D-22: A lock holds one position in one look

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** A lock keeps an item in the look and the position where the person locked it, such as footwear in the first look. Moving the item to another position in that look breaks the lock. The same item in another look is not locked and can change. [SPEC.md](SPEC.md#hard-constraints) has the rule.
- **Reason:** An item can appear in several looks, and a person who keeps it in one look has not decided about the others. Tying the lock to a position lets code check it exactly after every edit.

## D-23: Materials are compared by names from a fixed list

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** Catalog components and material exclusions name materials from a fixed list of generic names in `backend/app/catalog.py`. Each name has other common names that map to it, such as PU for polyurethane and merino, lambswool and woollen for wool. A catalog file or an exclusion that uses a name not on the list is refused. While an exclusion is active, a catalog product passes only when its composition is complete and none of its listed components is the excluded material; a partial, unknown or conflicting composition fails, as D-21 decided. An owned item's description names a material when it contains the material's name or another name for it as whole words. Where listed names overlap, the longest one counts, so "synthetic rubber sole" names synthetic rubber and not rubber. The material is not named when "no", "non", "not" or "without" comes before that longest name or "free" follows it, as in "no merino wool" or "wool-free".
- **Reason:** D-21 matched an exclusion anywhere inside a component's name. That left out products by mistake, because an exclusion of PU matched "pure cotton". It also let products through, because PU never matched "polyurethane", and it labeled an owned "wool-free cotton scarf" as reporting wool. Names from a fixed list make every comparison exact.
- **Consequences:** An exclusion typed with a name that is not on the list cannot be stored, so the interface has to offer the list or ask the person to choose from it. A catalog source that uses a new material needs that material added to the list first. Each name matches only its own material: an exclusion of wool does not exclude cashmere, and an exclusion of rubber does not exclude synthetic rubber. Negation is recognized only in the forms above; any other phrasing that mentions the material labels the owned item as reporting it.

## D-24: An offer reported out of stock fails

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** A catalog product fails the checks when its selected offer reports it out of stock. A plan can select another offer of the same variant instead. An offer that gives no stock can be used, and the interface shows its stock as unknown. [SPEC.md](SPEC.md#hard-constraints) has the rule.
- **Reason:** A retailer cannot sell a product it reports as out of stock, so a plan that includes it cannot be bought as shown. Failing unknown stock as well would leave out every product whose source gives no stock.
- **Consequences:** The first version has five hard constraints. When FullAura tests whether the locked items alone make a plan infeasible, a locked variant whose offers are all out of stock is a conflict.

## D-25: Product facts and product images need different rights

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** A catalog source for product facts, such as price, composition and delivery, needs terms of use that allow FullAura to record those facts; it does not need an individual permission from each brand or retailer. Product images need a license, a feed whose terms permit display, or photos that FullAura takes itself. The consent of people whose photos FullAura processes is a separate requirement and does not change. [DATA.md](DATA.md) has the rules for recording each source.
- **Reason:** Copyright protects images but not individual facts such as a price or a composition. A site's terms of use and the EU database right can still forbid copying a large part of its catalog, so the terms are checked and only a few products are recorded by hand. Treating facts and images as one permission made T-02 and T-03 wait for replies from brands that might not arrive before the deadline.
- **Consequences:** T-03 depends on choosing a source and checking its terms. The feasibility test (T-02) can use photos that the author takes of real garments. This is the project's working rule for the prototype, not legal advice; a lawyer should review it before FullAura is offered to the public.

## D-26: The first catalog records facts from Armedangels and Giesswein, without images

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** The first catalog records the facts of 15 product variants, read from product pages: 11 from the Austrian Armedangels store (clothing) and 4 from Giesswein (footwear). Their terms of use, checked on 2026-10-28, contain no clause on using the site's content ([Armedangels](https://www.armedangels.com/at-de/agb), [Giesswein](https://www.giesswein.com/pages/agb)), and their robots.txt files allow crawlers on product pages. The catalog holds no retailer images; the interface shows a placeholder and a link to the retailer.
- **Reason:** Both sites show prices in EUR, delivery to Austria and material composition, and neither needs a sign-up. Zalando also has suitable terms but refuses automated requests, and the other sources checked either restrict the use of their content (H&M, COS, Zara, Veja, Birkenstock and Humanic) or block automated requests (ECCO). No openly licensed product images were found, and an affiliate feed that allows images needs each retailer's approval, which might not arrive before the deadline.
- **Consequences:** Giesswein is the only footwear source, and only one of its four shoes contains no wool, so under a wool exclusion every look uses the same shoe. Without a product image, a preview of a catalog product cannot show the real garment and is approximate ([SPEC.md](SPEC.md#previews)).

## D-27: A composition is complete when it names every labelled part

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** A catalog product's composition is complete when the source names the material of every part that EU labelling covers. For clothing, that is each textile component, except a component that is not a main lining and makes up less than 30% of the weight ([Regulation (EU) 1007/2011, Article 11](https://www.legislation.gov.uk/eur/2011/1007/article/11/adopted)). For shoes, it is the upper, the lining and insole, and the sole. Trims that the regulation leaves out of the fibre composition, such as buttons, zips, labels and elastic bands at specific points ([Annex VII](https://www.legislation.gov.uk/eur/2011/1007/annex/VII/adopted)), do not decide completeness. A trim whose material the source names is still recorded and checked.
- **Reason:** The earlier reading, under which every part had to be named, including buttons, elastic and laces, left almost no real product complete, because product pages give the labelled composition and rarely name trims. Every plan under an exclusion would then be infeasible. The labelled parts are what a seller has to state, so a page that gives them is as complete as the source can be.
- **Consequences:** An exclusion does not check unnamed trims, such as an elastic waistband that may contain rubber, and the interface says so. A labelled part that the source leaves out or names only vaguely, such as "Synthetik", still makes the composition partial.

## D-28: Muse Spark on Baseten plans, and code checks every plan

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** Muse Spark Open (`meta-models/muse-spark-open-1026`), served through Baseten's model API, plans the looks. The model receives the brief, the budget, the owned items, the locks and only the catalog products that pass the hard-constraint checks, and replies in JSON with item IDs. Code picks each variant's cheapest offer that passes the checks, then checks the whole plan with the same code as every other plan. A plan that fails gets one repair attempt that lists its violations. A request ends in one of five outcomes:
  - success;
  - infeasible: the locks alone break a hard constraint, found by code before any model call;
  - generation failed: the model's plans failed the checks or the reply format;
  - provider timeout;
  - provider error.
- **Reason:** The model is used for styling choices, and code keeps prices, offers and hard constraints exact (D-09). Separate outcomes keep a model or provider failure from being shown as an impossible request.
- **Consequences:** A request makes at most two model calls within 150 seconds. Each call has a 60-second timeout and is retried once after a timeout, a rate limit or a server error. Token usage is returned with each result and logged without prompts or briefs. The endpoint runs only in development until T-04 adds per-session limits, the 18+ confirmation and the photo-processing notice. The model replies in JSON, not through tool calls.

## D-29: Looks have a complete base outfit

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** A generated look must contain either a top and bottom or one one-piece item, never both. Each item's category must match its slot. Extra layers, footwear and accessories are optional. Code checks these rules before success and sends violations back for the one repair attempt ([SPEC.md](SPEC.md#look-structure)).
- **Reason:** A model reply can be valid JSON and pass price, material, delivery and stock checks while still describing an incomplete outfit or placing a product in an unrelated slot.
- **Consequences:** A plan that still fails look-structure checks ends as `generation_failed`; the public response has no plan and keeps the violations for diagnosis.

## D-30: One deadline covers planning and HTTP retries

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** One monotonic 150-second deadline covers both model generations, each HTTP attempt and every retry wait. Each HTTP request receives no more than the remaining time, and a reply received at or after the deadline is discarded. The HTTP client cancels the full request when that timeout expires. `usage.calls` counts returned model completions; `usage.http_attempts` counts each outbound request, including retries.
- **Reason:** A timeout on one blocking network operation does not limit the combined duration of multiple HTTP attempts and retry waits.
- **Consequences:** A request ends as `provider_timeout` at the shared deadline, even when the provider later returns a usable reply. The API and planning log expose the two counters separately.

## D-31: No persistent server copies of personal photos

- **Date:** 2026-10-28
- **Status:** accepted
- **Decision:** When photo uploads are enabled, FullAura keeps originals, metadata-stripped inputs and generated previews in memory only for the request. It stores no server copy, cache, backup, database record or raw provider response. It deletes any required temporary files in `finally` on every success and failure path, and removes leftovers from interrupted processes during startup before accepting uploads. If cleanup or the startup sweep fails, photo processing stays blocked until cleanup succeeds. [DATA.md](DATA.md#photo-handling-for-upload-support) describes the cleanup order.
- **Reason:** A short server retention period is unnecessary for the first flow and increases the amount of personal data that needs storage, access control and deletion handling.
- **Consequences:** The request-scoped route and interface are implemented, but the route returns 503 before reading the body until T-02 passes and `FULLAURA_T02_PREVIEWS_PASSED=true` is set. The route remains development-only. The browser holds selected photos and may retain a returned preview according to its own behavior; a model provider's retention remains governed by its published terms and the user-facing processing notice.

## D-32: Preview requests have one deadline and cancel on disconnect

- **Date:** 2026-10-29
- **Status:** accepted
- **Decision:** One monotonic 120-second deadline covers receiving the bounded multipart request, provider attempts and retry waits. The asynchronous HTTP request receives only the remaining time and is cancelled when the deadline expires. If the browser disconnects while the provider is working, the server cancels that task. Request and preview buffers are cleared on success, timeout, disconnect and failure. Preview uploads remain disabled until the formal T-02 result passes and `FULLAURA_T02_PREVIEWS_PASSED=true` is set; production stays disabled.
- **Reason:** Per-operation network timeouts do not bound upload plus retry duration, and a synchronous request cannot be reliably cancelled when the caller leaves.
- **Consequences:** A local preview request ends with HTTP 504 at the shared deadline. A disconnected browser receives no response, and no provider work continues after the server detects the disconnect. Public model access still needs the T-04 safeguards described in [TASKS.md](TASKS.md#t-04-one-complete-flow).

## D-33: Bound Muse Spark reasoning for plan generation

- **Date:** 2026-10-29
- **Status:** accepted
- **Decision:** Send `reasoning_effort=medium` with planning requests and keep the 6,000-token completion cap. Code remains authoritative for budget, catalog, lock and look-structure checks.
- **Reason:** Muse Spark's default `xhigh` reasoning exhausted the completion cap before returning JSON in a live planning request. Meta documents `medium` as the general agentic setting and says lower reasoning effort uses fewer generated tokens; reasoning and the answer share the completion budget ([recommended settings](https://dev.meta.ai/docs/muse-spark-open/settings), [inference guidance](https://dev.meta.ai/docs/muse-spark-open/deploy)).
- **Consequences:** More of the bounded completion budget remains available for the structured plan. The checked plan still has to pass the same hard constraints.

## D-34: Open the pilot with anonymous limited sessions

- **Date:** 2026-10-29
- **Status:** accepted
- **Decision:** Production exposes the catalog and planning routes only when `FULLAURA_PUBLIC_DEMO_ENABLED=true`. Each browser session uses a random opaque `HttpOnly`, `Secure`, `SameSite=Lax` cookie. The server stores age confirmation and planning-request count in memory and expires inactive sessions after 24 hours. Planning requires an explicit 18+ confirmation and permits three requests per session; initial plans, updates and replacements count. Photo previews stay disabled in production pending T-02.
- **Reason:** A small link-accessible pilot needs a clear age gate and a bounded number of paid planning submissions without adding accounts or a database.
- **Consequences:** The session limit assumes one process, one worker and one replica. A process restart resets request counts, and a person can start a new browser session. This is not an aggregate spending ceiling or abuse-proof access control. Verify Azure and provider account-level spending controls before enabling the public routes. The interface explains the current photo state and the processing that would apply if the T-02 gate is later passed.

## D-35: Compare planning with the cheapest admissible outfit baseline

- **Date:** 2026-10-29
- **Status:** accepted
- **Decision:** T-06 compares the full planner and a deterministic baseline on the same fixed, non-personal cases. The baseline chooses the three cheapest distinct complete outfits from variants that pass the existing item checks, then validates each complete plan with `check_plan`. Hard-constraint outcomes and provider usage are recorded by code; style relevance, color coherence, visual variety and wearability remain separate human scores. [EVALUATION.md](EVALUATION.md) defines the cases and measurements.
- **Reason:** A reproducible price-only baseline gives the live planner a concrete comparison while retaining the existing hard constraints. A human judges style and color instead of pretending the baseline or code can score taste.
- **Consequences:** The three-case live comparison is recorded in [EVALUATION.md](EVALUATION.md). The baseline does not establish general quality, fit, stock availability or broad preference. T-06 remains open until human scoring, the demo and submission are complete.

## D-36: The person states whom the looks are for

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** A plan request can carry a fit: the clothing lines to show (women's, men's), the kinds of bottoms and one-pieces the person wears, and the sizes they accept in each sizing system (letter, waist and length, EU shoe). Code checks fit as a hard constraint on catalog items; owned items are exempt. A product that its retailer lists as unisex passes any choice of line. Each product records its line and garment type as the retailer labels them, and each variant its size in the retailer's own system; FullAura does not convert sizes between systems. A variant whose size cannot be compared fails once the person gives any size, and a system in which the person gives no size is not checked. FullAura never infers line, garment types or sizes from photographs. [SPEC.md](SPEC.md#hard-constraints) has the rule.
- **Reason:** In the T-06 comparison every plan passed the hard constraints, yet plans mixed men's and women's lines, and one combined shoes in EU 39 and 42, so no single person could wear it. Neither the request nor the catalog said whom the looks were for. An explicit statement makes "for this person" checkable without inferring a sensitive trait.
- **Consequences:** The catalog needs variants in the sizes a wearer gives and a garment type and line on each product that should stay eligible; a product without them drops out while the matching choice is active. A matching size label does not guarantee fit, because size charts differ between models.

## D-37: The demo is accepted on its wearer's scores, set before the run

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** The project author is the demo wearer. The author states a fit, gets a plan, and scores each of the three looks on the T-06 scale; the demo flow is accepted when every look scores at least 4 for style relevance and for wearability. This acceptance is recorded apart from the T-06 comparison, which scores whole plans on fixed, non-personal cases.
- **Reason:** The threshold is fixed before the run so that the result cannot be adjusted to it. One named wearer makes "three looks that suit a person" testable within the hackathon.
- **Consequences:** The acceptance shows the flow works for one person; it is not evidence of general preference or fit.

## D-38: Previews depend on the formal test materials by 31 October

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** The formal preview test (T-02) runs only if two more consenting subjects and the garment images are available by the evening of 2026-10-31, Vienna time. Otherwise the demo follows [D-11](#d-11-test-previews-first-and-keep-the-plan-useful-without-them) and shows no previews.
- **Reason:** Four days remain before the internal deadline, and the planning flow for a stated wearer needs them. Missing test materials are not a result about the model.
- **Consequences:** If the materials do not arrive, T-02 records that the formal test did not run and why, and the submission does not present previews as tested.

## D-39: Closed shoes come from GEA Waldviertler, with partial compositions

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** The catalog adds two closed leather shoes from GEA Waldviertler, Tramper Flex and Kommod Classic, whose product pages say they are for women and men. The pages name the upper, the lining and, for Tramper Flex, the sole, but not the footbed, so both compositions are partial under [D-27](#d-27-a-composition-is-complete-when-it-names-every-labelled-part) and the shoes drop out while any material exclusion is active.
- **Reason:** The demo wearer needs closed shoes, and the only wool-free shoe in the first catalog is an open slide ([D-26](#d-26-the-first-catalog-records-facts-from-armedangels-and-giesswein-without-images)). None of the 17 stores checked on 2026-10-30 combined terms that allow recording facts, delivery to Austria and a complete shoe composition. Glore, Bergzeit, Legero, Shoepassion, Lloyd, Wildling, Nat-2, Duckfeet and NAE Vegan restrict the use of their content; avesu's successor shop disallows all crawlers; Shoezuu does not ship to Austria; Ethletic names neither a clothing line nor an insole material; Grüne Erde sells clothing for women only. Treating the named lining as the whole "lining and insole" part would declare the shoes wool-free without knowing the footbed material.
- **Consequences:** Without a material exclusion, the demo wearer can get closed shoes; with a wool exclusion, Beach is still the only shoe for every look.

## D-40: The T-06 comparison adds a case with an example fit

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** The T-06 runner adds a fourth fixed case, `mens-fit-per-look`, that states an example fit: men's line, trousers or jeans, size L, waist and length 32/32, and EU shoe size 42 or 43, with €250 per look and no material exclusion. The three earlier cases keep no fit and serve as the regression set. A live run of all four cases is capped at 16 HTTP attempts. [EVALUATION.md](EVALUATION.md) has the cases.
- **Reason:** The three earlier cases state no fit, so rerunning them checks for regressions but not the fit constraint ([D-36](#d-36-the-person-states-whom-the-looks-are-for)). The example fit is the one the tests use and is not a real person's, so the cases stay non-personal; the demo wearer's own fit is checked in the acceptance ([D-37](#d-37-the-demo-is-accepted-on-its-wearers-scores-set-before-the-run)).
- **Consequences:** The new case has no result from the 2026-10-29 run to compare with. A full live run can use up to 16 HTTP attempts instead of 12.
