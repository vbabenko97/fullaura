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
- **Status:** accepted; the T-02 gate in its consequences is replaced by [D-44](#d-44-the-betas-try-on-is-experimental-and-does-not-wait-for-t-02)
- **Decision:** When photo uploads are enabled, FullAura keeps originals, metadata-stripped inputs and generated previews in memory only for the request. It stores no server copy, cache, backup, database record or raw provider response. It deletes any required temporary files in `finally` on every success and failure path, and removes leftovers from interrupted processes during startup before accepting uploads. If cleanup or the startup sweep fails, photo processing stays blocked until cleanup succeeds. [DATA.md](DATA.md#photo-handling-for-upload-support) describes the cleanup order.
- **Reason:** A short server retention period is unnecessary for the first flow and increases the amount of personal data that needs storage, access control and deletion handling.
- **Consequences:** The request-scoped route and interface are implemented, but the route returns 503 before reading the body until T-02 passes and `FULLAURA_T02_PREVIEWS_PASSED=true` is set. The route remains development-only. The browser holds selected photos and may retain a returned preview according to its own behavior; a model provider's retention remains governed by its published terms and the user-facing processing notice.

## D-32: Preview requests have one deadline and cancel on disconnect

- **Date:** 2026-10-29
- **Status:** accepted; the T-02 gate in its decision is replaced by [D-44](#d-44-the-betas-try-on-is-experimental-and-does-not-wait-for-t-02)
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
- **Status:** superseded by [D-43](#d-43-an-invite-only-beta-replaces-the-anonymous-demo)
- **Decision:** Production exposes the catalog and planning routes only when `FULLAURA_PUBLIC_DEMO_ENABLED=true`. Each browser session uses a random opaque `HttpOnly`, `Secure`, `SameSite=Lax` cookie. The server stores age confirmation and planning-request count in memory and expires inactive sessions after 24 hours. Planning requires an explicit 18+ confirmation and permits three requests per session; initial plans, updates and replacements count. Photo previews stay disabled in production pending T-02.
- **Reason:** A small link-accessible pilot needs a clear age gate and a bounded number of paid planning submissions without adding accounts or a database.
- **Consequences:** The session limit assumes one process, one worker and one replica. A process restart resets request counts, and a person can start a new browser session. This is not an aggregate spending ceiling or abuse-proof access control. Verify Azure and provider account-level spending controls before enabling the public routes. The interface explains the current photo state and the processing that would apply if the T-02 gate is later passed.

## D-35: Compare planning with the cheapest admissible outfit baseline

- **Date:** 2026-10-29
- **Status:** superseded by [D-42](#d-42-t-06-compares-with-the-cheapest-outfit-baseline-and-judges-only-the-fit-case)
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
- **Status:** superseded by [D-41](#d-41-the-demo-is-accepted-on-its-wearers-yes-or-no-answers-set-before-the-run)
- **Decision:** The project author is the demo wearer. The author states a fit, gets a plan, and scores each of the three looks on the T-06 scale; the demo flow is accepted when every look scores at least 4 for style relevance and for wearability. This acceptance is recorded apart from the T-06 comparison, which scores whole plans on fixed, non-personal cases.
- **Reason:** The threshold is fixed before the run so that the result cannot be adjusted to it. One named wearer makes "three looks that suit a person" testable within the hackathon.
- **Consequences:** The acceptance shows the flow works for one person; it is not evidence of general preference or fit.

## D-38: Previews depend on the formal test materials by 31 October

- **Date:** 2026-10-30
- **Status:** superseded by [D-44](#d-44-the-betas-try-on-is-experimental-and-does-not-wait-for-t-02)
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
- **Decision:** The T-06 runner adds a fourth fixed case, `mens-fit-per-look`, that states an example fit: men's line, trousers or jeans, size L, waist and length 32/32, and EU shoe size 42 or 43, with €250 per look and no material exclusion. The three earlier cases keep no fit and serve as the regression set. A live run keeps the cap of 12 HTTP attempts, now shared by all the cases in the run. A case starts only while at least four attempts, its full allowance, remain; otherwise it gets no model call and is recorded as not run. [EVALUATION.md](EVALUATION.md) has the cases.
- **Reason:** The three earlier cases state no fit, so rerunning them checks for regressions but not the fit constraint ([D-36](#d-36-the-person-states-whom-the-looks-are-for)). The example fit is the one the tests use and is not a real person's, so the cases stay non-personal; the demo wearer's own fit is checked in the acceptance ([D-37](#d-37-the-demo-is-accepted-on-its-wearers-scores-set-before-the-run)).
- **Consequences:** The new case has no result from the 2026-10-29 run to compare with. A full live run costs no more than before, at most 12 HTTP attempts, but it no longer guarantees every case a model call: each case can use up to four attempts, and the fit case runs last. If the first three cases leave fewer than four attempts, the fit case is run on its own. A case is never cut short by the cap, so a provider error in the results comes from the provider.

## D-41: The demo is accepted on its wearer's yes-or-no answers, set before the run

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** The project author is the demo wearer. The author states a fit on the deployed site, gets a plan and answers two questions for each of the three looks of the first plan, before any edit: "Is this what I asked for?" and "Would I go and try this on?" The demo flow is accepted when all six answers are yes. This acceptance is recorded apart from the T-06 comparison. It replaces the threshold of 4 for style relevance and wearability in [D-37](#d-37-the-demo-is-accepted-on-its-wearers-scores-set-before-the-run).
- **Reason:** The author cannot reliably score style relevance or color coherence, so a threshold on those scales would not show whether the looks suit the wearer. Both questions need no styling expertise, and whether to try an item on is the decision FullAura supports. The rule is fixed before the run so that the result cannot be adjusted to it.
- **Consequences:** The acceptance shows the flow works for one person; it is not evidence of general preference or fit. The interface has no previews or product images, so the wearer judges each look from item names, catalog colors and the retailer pages.

## D-42: T-06 compares with the cheapest outfit baseline and judges only the fit case

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** T-06 compares the full planner and a deterministic baseline on the same fixed, non-personal cases. The baseline chooses the three cheapest distinct complete outfits from variants that pass the existing item checks, then validates each complete plan with `check_plan`. Code records hard-constraint outcomes and provider usage for every case. A person judges only `mens-fit-per-look` ([D-40](#d-40-the-t-06-comparison-adds-a-case-with-an-example-fit)): for each look of both plans, they answer the two questions of [D-41](#d-41-the-demo-is-accepted-on-its-wearers-yes-or-no-answers-set-before-the-run) as the person who made the request. The cases without a fit check hard constraints only. This replaces the four 1–5 scores of [D-35](#d-35-compare-planning-with-the-cheapest-admissible-outfit-baseline). [EVALUATION.md](EVALUATION.md) defines the cases and measurements.
- **Reason:** In the cases without a fit, plans mix men's and women's lines and sizes, so no single person can say whether they would try the looks on. The four scales asked for styling judgments that the available rater cannot make reliably. A reproducible price-only baseline still gives the planner a concrete comparison.
- **Consequences:** The human comparison rests on one case with three looks per approach, which supports a pilot observation only. The 2026-10-29 run has no fit case and gets no human answers; its machine-checked results stay recorded. The baseline does not establish general quality, fit, stock availability or broad preference. T-06 remains open until the human answers, the demo and submission are complete.

## D-43: An invite-only beta replaces the anonymous demo

- **Date:** 2026-10-30
- **Status:** accepted; in its consequences, the immediate interruption of another process's call is replaced by [D-48](#d-48-a-running-call-blocks-its-invite-whichever-process-started-it), and the 30-day expiry of pilot records by [D-49](#d-49-a-scheduled-job-deletes-the-pilot-records-and-the-beta-closes-then)
- **Decision:** With `FULLAURA_BETA_ENABLED=true`, the production app serves invited sessions only; without it, everything except health, readiness and the session status stays closed. An invite is a random code; the server holds only SHA-256 digests of the codes and signs a session cookie that names the invite. Each invite confirms age 18 or older once, may make 8 planning requests and 12 try-ons, and runs one model request at a time. Before each model call the server reserves the call's maximum cost at list prices, including every retry, in a ledger shared by all invites: $8 for planning and $3 for images. After the call it records the actual cost, or keeps the whole reservation when the outcome is unknown. A request carries a UUID, so a repeat never starts a second paid call, and a call that a stopped process left running is never retried and keeps its reservation. Quotas, the ledger and feedback live in a private Azure Blob Storage container that the app reaches with its managed identity, and every change is a conditional write on the document's ETag. When the store cannot be reached, model calls stop and plans stay available. `FULLAURA_PILOT_ENDS_AT` closes model calls at the end of the pilot. This replaces [D-34](#d-34-open-the-pilot-with-anonymous-limited-sessions).
- **Reason:** The author and friends use the product in a closed beta with $25 for extra costs ($8 planning, $3 images, $10 Azure, $4 reserve). D-34's in-memory per-session cap reset on every restart and was not a spending limit. Writing to Blob through its REST API with a managed-identity token needs no Azure SDK, and ETags keep two writers from losing an update ([concurrency in Blob Storage](https://learn.microsoft.com/en-us/azure/storage/blobs/concurrency-manage)).
- **Consequences:** The demo URL needs an invite, so a hackathon judge needs one too. The ledger holds list-price estimates, not provider invoices; the Azure budget still only notifies. The store holds no photo, preview, plan, profile or provider response. Plans still live in one process's memory, so the app keeps one replica; quotas and money no longer depend on it. A model call that another process started counts as interrupted at once, so during the short overlap of two revisions in a deployment, a call still running on the old revision keeps its whole reservation and does not block its invite. Because pilot records expire 30 days after their last change, the production beta refuses to start without `FULLAURA_PILOT_ENDS_AT`; an expired ledger would otherwise reopen the budgets.

## D-44: The beta's try-on is experimental and does not wait for T-02

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** In the beta, an invited person who agrees to a separate photo notice can try one look of their current plan on two of their own photos. The browser redraws a JPEG, PNG or WebP photo as an upright JPEG without metadata; the server still accepts only JPEG and removes metadata again. The server writes the prompt from the checked look: garment kind, catalog color, title and complete materials of each catalog item, and for an owned item its category and the person's own color and description, reduced to letters, digits and light punctuation, cut to 80 characters and quoted as a description. It sends no product image and no other text from the browser. The interface labels the result an experimental visualization that shows approximate colors, shapes and combinations, not the exact products, fit or size. A changed plan hides an earlier try-on. `FULLAURA_T02_PREVIEWS_PASSED` is removed, because no route depends on it. The T-02 protocol, its thresholds and its results stay as they are; a beta try-on is not a T-02 run.
- **Reason:** The author decided on 2026-10-30 that a useful beta matters more than the hackathon schedule and that try-on on one's own photo is part of it. Exploratory runs passed every check ([FEASIBILITY.md](FEASIBILITY.md#results)), and an honest approximate label keeps the promise within what has been seen. A prompt written from the server's checked plan, with the person's own words only as a short quoted description, gives the browser no room to turn the request into another image.
- **Consequences:** T-02 stays open, so the try-on's quality has no formal evidence; an agent's visual check of synthetic runs is a preliminary check only. [D-31](#d-31-no-persistent-server-copies-of-personal-photos) and [D-32](#d-32-preview-requests-have-one-deadline-and-cancel-on-disconnect) still govern photo handling. This replaces [D-38](#d-38-previews-depend-on-the-formal-test-materials-by-31-october).

## D-45: People choose closed, open or any shoes

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** A plan request carries a footwear choice: closed, open or any. Each catalog shoe records its closure from the retailer's own product type, such as a sneaker or a slide. With closed or open, every look needs footwear, and a catalog shoe of the other kind or with no recorded closure fails as a hard constraint; owned shoes count and are not checked. The interface starts at closed shoes. Occasion and season join the brief as wishes for the model; code does not check them.
- **Reason:** In the T-06 fit case an open slide ended up in an autumn office look ([EVALUATION.md](EVALUATION.md#human-scoring)), and nothing in the request could rule it out. The closed beta's coverage scenarios need closed shoes in every look.
- **Consequences:** Every catalog shoe needs a closure for a closed or open choice to use it. When no usable shoe of the chosen kind exists, the request is infeasible without a model call.

## D-46: The server keeps the checked plan, and the browser keeps saved copies

- **Date:** 2026-10-30
- **Status:** accepted
- **Decision:** A successful plan comes with an opaque token. The server keeps the last checked plan of each browser session in memory, and an update, a replacement or a try-on names it by that token; the browser never sends a plan with these requests. A failed request leaves the plan and its token current. The browser keeps its working plan for the tab and up to ten plans saved on the device, with their shopping lists and without photos or try-ons. `POST /api/plans/restore` checks a saved plan again against the current catalog and constraints, chooses its offers again and issues a new token, without a model call.
- **Reason:** A try-on prompt and a replacement must come from a plan the server checked, not one a browser could change. The plan memory ends with the process, and the app scales to zero, so a plan must survive in the browser.
- **Consequences:** After a restart or a scale-down, the browser restores its plan before the next request. A saved plan that no longer passes the checks cannot be restored, but its saved shopping list stays readable.

## D-47: Tom Tailor and Tchibo supply affordable basics and closed shoes

- **Date:** 2026-10-30
- **Status:** accepted; the consequence that women's L under a wool exclusion depends on a single shoe no longer holds after [D-55](#d-55-a-second-closed-shoe-in-eu-40-keeps-womens-l-from-depending-on-one-pair)
- **Decision:** The catalog adds 23 variants from Tom Tailor's Austrian shop and 2 Tom Tailor sneaker variants sold by Tchibo: men's chinos in 32/32 and 34/32, women's tops and letter-sized trousers and leggings in S and L, and closed sneakers in EU 38, 40, 42 and 43. Every Tom Tailor record has a complete composition, including the four parts of each shoe; the Tchibo shoe's composition is partial. The closed beta's coverage cases ([DATA.md](DATA.md#catalog-coverage)) become a test.
- **Reason:** With the earlier catalog, no closed wool-free shoe had a complete composition, no women's sizes S or L existed, and no men's bottom fitted 34/32, so none of the twelve coverage cases passed. Both stores' terms have no clause on content use, their robots.txt allows product pages, and they deliver to Austria. On 2026-10-30 these stores were rejected for a content clause: C&A, s.Oliver, bonprix, About You, Rieker and remonte, Clarks, Vagabond, Camper, Think!, Intersport, Superga and Ecoalf; these for blocking automated reading so that their terms could not be read: Decathlon, Next, Native Shoes and Vivobarefoot; Esprit and Lidl have no Austrian online shop. Deichmann, Tamaris, Gabor, Hervis, Waschbär, Novesta, Saye and hessnatur passed the terms but had no shoe that fit: vague or unnamed parts, no line, or prices above €110. Mango passed the terms but blocked the re-check of its product pages, so nothing from it is recorded.
- **Consequences:** All twelve coverage cases pass. Women's L under a wool exclusion depends on a single shoe, and four shoe prices are sale prices. Shoes whose soles are thermoplastic elastomer or thermoplastic rubber, materials not on the fixed list, stay out until the list gains them.

## D-48: A running call blocks its invite, whichever process started it

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** A model call recorded as running in the ledger blocks another call for the same invite until it finishes or is more than 300 seconds old, longer than the planning and try-on deadlines. A different process ID no longer makes a running call count as interrupted. A call older than 300 seconds is interrupted: it is never retried, and its whole reservation counts as spent; if its process reports later, the report changes nothing. A repeated operation ID, the quotas and the reservation of the maximum cost stay as in [D-43](#d-43-an-invite-only-beta-replaces-the-anonymous-demo).
- **Reason:** During a deployment the old revision keeps serving for a while. Under D-43 the new revision treated the old revision's running call as interrupted at once, so one invite could run two paid calls in parallel. The ledger cannot tell a stopped process from a busy one, and the existing time limit already covers a stopped one, so no heartbeat or lease is needed.
- **Consequences:** After a restart or a deployment, an invite whose call was cut off waits up to five minutes before its next model call, and the interface reports that another request is still running. Other invites are not affected.

## D-49: A scheduled job deletes the pilot records, and the beta closes then

- **Date:** 2026-10-31
- **Status:** accepted; how the job and the app's writes take turns is replaced by [D-50](#d-50-a-closure-marker-and-a-grace-period-keep-writes-from-outliving-the-purge), and the single image for the app and the job by [D-51](#d-51-the-app-and-the-purge-job-have-their-own-images-and-roll-back-apart)
- **Decision:** A Container Apps job in the beta's environment, `fullaura-demo-purge`, runs the app's image with `python -m backend.app.purge` at 09:00 UTC (10:00 in Vienna) every day from 9 to 15 November. It deletes every record in the `pilot` container once `FULLAURA_RECORDS_PURGE_AT`, 2026-11-09 10:00 Vienna time, has passed, and checks that none is left; before that time it deletes nothing and only checks its access. The settings refuse a purge time that is not after `FULLAURA_PILOT_ENDS_AT`. At the purge time the app closes the beta as if `FULLAURA_BETA_ENABLED` were false, so no new record appears. The job's managed identity has Storage Blob Data Contributor on the `pilot` container only. The lifecycle rule that deletes a record 30 days after its last change stays as a backstop. The interface shows participants the deletion time.
- **Reason:** The Free Trial subscription, created on 2026-10-23, ends about 2026-11-22 18:56 UTC; the 30-day rule would have run into December, after Azure disables the subscription's resources, and deleting the records by hand depends on the operator. A scheduled job in the existing environment needs no extra service, uses code that is tested with the app, and runs without anyone's computer. Two and a half days after the pilot's end on 2026-11-06 23:59 leave time for late feedback and the operator's final report; the six later runs retry a failure and still end a week before the trial does. The pilot's end keeps model calls closed whatever the ledger holds, so deleting the ledger reopens no budget.
- **Consequences:** From the purge time on, invited people see that the beta is closed; plans saved in their browsers stay in their browser storage. The operator's final report must run before 2026-11-09 10:00 Vienna time. Moving the pilot's end later also needs a later purge time and schedule; with a purge time that is not after the end, the app refuses to start and the job deletes nothing. The job costs a few seconds of Consumption compute per run.

## D-50: A closure marker and a grace period keep writes from outliving the purge

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** Every write of the pilot store passes one check in the app, `ClosingStore` in `backend/app/pilot.py`: every create, replace and delete, including ETag retries, feedback and a model call's late settlement. Right before the write, the check refuses once the purge time has passed or the marker `purge-marker.json` exists. A write it allowed must leave the app within 5 seconds of the check, token refresh included, or the Blob store does not send it. Every Blob request also carries the service's own 10-second timeout. When due, the purge job first writes the marker, then waits 120 seconds, longer than any write that passed the check before the marker existed can take. Only then does it delete every record and check that none is left. The marker holds only when the deletion began and the purge time; the job never deletes it, so the app refuses writes for as long as it exists. A refused write fails like an unreachable store: the request is refused, and nothing is written.
- **Reason:** The route check of D-49 closes the beta at the purge time, but a request accepted just before could still write after the job had deleted the records: an age confirmation, a consent, feedback, an ETag retry or a late settlement could bring a record back. Blob Storage has no transaction across documents, so the job cannot stop another document's write. A marker that every write checks, combined with a time bound on how long a checked write can take, needs no extra service. It assumes that a request the app sent reaches the Blob service within about a minute, DNS and network included; the client enforces everything else. A lock or lease document that every write takes would serialize all invites on one document and still need the same time bound for a holder that stops.
- **Consequences:** Each write reads the marker first, one more request of a few milliseconds. The deletion now finishes about two minutes after the job starts. If the lifecycle rule deletes the marker 30 days after it was written, the app's own check of the purge time still refuses writes. To reopen writing, for example for a new pilot with the same store, the operator deletes the marker by hand. Each daily run from 10 to 15 November 2026 deletes every record it finds, so a record that appeared after an earlier run, for example from a request delayed past the wait, is deleted by the next run that succeeds within the schedule; that is not necessarily the next morning. These repeats lower the remaining risk, but they do not turn the assumption about request delivery into a strict guarantee, and a record that appears after the last scheduled run is outside the schedule.

## D-51: The app and the purge job have their own images and roll back apart

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** The template takes the job's image as its own required parameter, `purgeJobImage`, beside the app's `containerImage`, and DEPLOYMENT.md records a digest for each. The app rolls back alone, to its recorded rollback image, and only with `FULLAURA_BETA_ENABLED=false` in the same command, because earlier app images write without checking the closure marker. The purge job keeps its image: only an image whose purge writes the marker may run it, and no earlier image does.
- **Reason:** Under D-49 one image served both, and the documented rollback pointed both at the image of `00c7e2f`, which has no `backend.app.purge`, so the scheduled deletion would have failed. The image of `a7eba24` has the purge but deletes without the marker. Run locally with the deployed pilot settings and the beta off, both earlier images refused every route that writes and wrote nothing.
- **Consequences:** An app rollback closes the beta until the current image returns; the schedule, the purge time, budgets, invites and secrets stay. A template deployment must name both images.

## D-52: A call record keeps its outcome apart from its cost, and the pilot report counts invites

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** A model call's record in the pilot store gains an `outcome` field with what happened to the request, such as `success`, `provider_timeout`, `timeout`, `provider_error`, `disconnected` or `interrupted`. Its `status` keeps its meaning: `unknown` whenever the cost is unknown, which keeps the whole reservation spent. An invite's record also counts the planning requests that no plan can meet, which are answered without a model call or quota, by the hard-constraint rules that ruled them out; it keeps the last 20 of their operation IDs, so a repeated request counts once, and counts nothing before the age confirmation. `scripts/pilot_report.py` prints aggregates only, with a denominator for every count and the time it read the store. Its unit for people is the invite, an approximation of a participant. It sorts the calls that may have reached the provider into succeeded, failed, cancelled, unknown and unfinished, where unfinished needs the ledger to still hold the reservation; counts calls that never reached the provider apart; and splits spending by whether the cost was reported or unknown. Feedback counts each invite's latest answer, saved last and, between equal times, listed last in the invite's record; answers that still exist count, repeats are shown as a number, and free text is counted but not printed. `--per-invite` keeps the earlier per-invite counts.
- **Reason:** The record set `status` to `unknown` for every call whose cost was unknown, so a try-on that timed out, failed at the provider or was cancelled by the browser could not be told apart, and the report could not say how often planning and try-ons worked. The report printed request counters and a count of feedback IDs, so it could not show how many invites got a plan or what participants answered.
- **Consequences:** Records written before this change keep `unknown` without an outcome, and the report counts them as unknown rather than guessing. The cost rules of [BETA.md](BETA.md#costs) do not change. The report reads the store document by document, so its final run belongs after model calls close; [BETA.md](BETA.md#reading-the-pilot-report) explains how to read it.

## D-53: Feedback asks what a try-on got wrong, from a fixed list

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** The feedback form adds an optional question, asked only with a try-on score: whether the try-on did not look like the person, body proportions looked off, clothes or colors differed from the look, the background looked wrong, something else, or no problems were noticed. "No problems" cannot go with a problem; the server refuses that, an empty list and any answer given with "I can't judge it". A skipped question is stored as `null`; an answer sent by a page from before the question existed has no such field.
- **Reason:** A single 1-to-5 score says that a try-on disappointed but not why, so it cannot show whether likeness, body shape, the garments or the setting needs work. A short fixed list keeps the answers countable without free text about the person.
- **Consequences:** The answers describe the drawing as the person saw it; they are not a measure of a product's fit and hold nothing about the person. The pilot report counts them out of the answers with a try-on score and flags answers from invites with no recorded successful try-on.

## D-54: The interface says what the budget limits next to the request button

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** A line above "Generate three looks" or "Update three looks" says what the amount limits. For each look, it says that the three looks together can cost up to three times the amount and that the shopping list counts an item used in several looks once; for the whole plan, it says that such an item counts once. It adds that prices are item prices without delivery or returns. Above the looks, the total is now the shopping list's items total, with the budget the plan kept, such as "Each look within € 150,00", instead of the sum of the three looks' prices.
- **Reason:** [D-17](#d-17-the-budget-applies-to-each-look-by-default) promised that the interface would show that the looks together can cost more than a per-look amount, but the form showed only the choice. In per-look mode the heading added up the looks, so an item in two looks counted twice and the heading disagreed with the shopping list.
- **Consequences:** The text promises no saving and no final price. The per-look and whole-plan totals that the heading used are removed from the frontend.

## D-55: A second closed shoe in EU 40 keeps women's L from depending on one pair

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** The catalog adds Tom Tailor's women's "Sneaker mit Mesh-Einsatz" in white, EU 40, with a complete composition and no wool, at a sale price of €44.99 (regular €59.99). A coverage test removes the platform sneaker in EU 40 and checks that the three women's L cases still give three looks with closed shoes and a valid replacement.
- **Reason:** Under a wool exclusion, the women's L cases depended on that one platform sneaker; if it sold out, two cases would fail. Of the 51 colour pages in Tom Tailor's women's sneaker category read on 2026-10-31, 42 have soles of thermoplastic elastomer, which is not on the material list, and of the rest only the mesh sneaker and the platform sneaker offered EU 40. The shop offered the "Sneaker mit erhöhter Sohle" in EU 40 only as "Benachrichtigen, wenn verfügbar".
- **Consequences:** On 2026-10-31, removing any one product left all twelve coverage cases passing; a script checked that, and the test checks only the platform sneaker's removal. Every women's item in those cases still comes from Tom Tailor, so a problem with that shop affects all women's cases ([DATA.md](DATA.md#catalog-coverage)).

## D-56: A variant can carry a photo only with recorded rights

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** A catalog variant can carry one photo of itself in its color: an HTTPS URL, a description, its width and height, and the credit its rights require. The look cards show it beside the product's facts in a box sized from the width and height, load it lazily, and show "Image unavailable" in the same box when it fails to load. A variant gets a photo only when [DATA.md](DATA.md) records a license, a feed whose terms permit display, or FullAura's own photograph for it. On 2026-10-31 none of the catalog's sources gives that right, so no variant has a photo.
- **Reason:** Photos would make the looks easier to compare in the app, but [D-25](#d-25-product-facts-and-product-images-need-different-rights) requires rights for images. Checked on 2026-10-31: Armedangels' imprint forbids using its graphics in other publications without express consent; Giesswein sends images to the press on request, and its dealer terms let only its dealers use them; GEA's press downloads hold no product photos and are for their stated purpose only; Tom Tailor's press images are for editorial use; Tchibo reserves all rights. Tom Tailor and Tchibo have affiliate programmes on Awin, whose publisher terms license advertiser images for marketing that advertiser, but only after Awin accepts the publisher and the advertiser approves it.
- **Consequences:** Showing photos needs a retailer's written permission, an Awin publisher account with a programme's approval, which would also bring tracked affiliate links, or photos the author takes. The interface code and its tests exist; [T-11](TASKS.md#t-11-product-photos) stays planned until rights are recorded.

## D-57: Evaluation run records carry their provenance, including failed runs

- **Date:** 2026-10-31
- **Status:** accepted
- **Decision:** `scripts/run_provenance.py` writes, for the planning comparison and the feasibility test, a run record with status `running` before the first model call, and `succeeded` or `failed` with the error at the end. The record holds the command, the HTTP attempts made even when the run fails, the Git revision, whether the working tree differed from it, the hashes of `uv.lock`, `pyproject.toml` and `.python-version`, the Python version and platform, the configuration in use and whether each provider key was set. `source.diff` beside it holds the uncommitted code, untracked files that Git does not ignore included. Values of provider keys and of other secrets in the environment, and each line of a multi-line one, are replaced in both files. The feasibility test also records the hashes of its cases, input photos and garment images, and every image it produced, and its summary prints the hash of the scores it counts.
- **Reason:** A record held only the Git revision, so a run from a modified working tree could not be reproduced, and a run that crashed before its first case left no record at all.
- **Consequences:** Runs made with a revision that has `scripts/run_provenance.py` carry these fields; earlier runs do not and are not rewritten. A secret inside a binary file, or one that is not in the environment, is not found ([EVALUATION.md](EVALUATION.md#run)).

## D-58: The pilot's results are read against guideline thresholds

- **Date:** 2026-10-31
- **Status:** accepted on 2026-10-31 as the guidelines for this pilot only
- **Decision:** The final report's feedback is read against the thresholds in [BETA.md](BETA.md#deciding-what-to-build-next), which were set before it was read. Each rule has its own minimum: 5 answering invites for planning, 5 try-on scores, and 5 answers to the try-on issue question, "I noticed no problems" included. The experiment continues with the plans as they are when at least 60% would wear a look and at least half rate usefulness 4 or 5. It continues with the try-on when at least half of the try-on scores are 3 or more and fewer than a third of the answers to the issue question, "I noticed no problems" included, name likeness; a skipped question stays out of that share and does not count as no problem. Reliability comes first when fewer than 90% of planning calls succeed or more than 10% have an unknown outcome.
- **Reason:** The beta had no rule for what its results mean, and a rule chosen after seeing about ten answers only describes them. One shared minimum would let a single try-on score decide the try-on, and counting a skipped issue question as no problem would understate likeness complaints.
- **Consequences:** Passing a threshold means the experiment continues; it does not show that the plans or the try-on are good, because about ten invites answering for themselves cannot show that. The thresholds apply to this pilot only; a later pilot sets its own before its answers are read.

