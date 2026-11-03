# Tasks

This file lists the work that builds the first version described in [SPEC.md](SPEC.md), in build order. Each task has a number that is never reused, a status (done, next or planned) and a check that says when it is done. When you finish a task, mark it done and add the date in the same commit.

| Task | Status | Finished |
| --- | --- | --- |
| [T-01](#t-01-foundation) Foundation | done | 2026-10-26 |
| [T-02](#t-02-feasibility-test) Feasibility test | next | |
| [T-03](#t-03-catalog-and-hard-constraints) Catalog and hard constraints | done | 2026-10-28 |
| [T-04](#t-04-one-complete-flow) One complete flow | done | 2026-10-29 |
| [T-05](#t-05-azure-demo) Azure demo | done | 2026-10-29 |
| [T-08](#t-08-looks-for-a-stated-wearer) Looks for a stated wearer | next | |
| [T-09](#t-09-closed-beta) Closed beta | done | 2026-10-30 |
| [T-10](#t-10-beta-measurement-and-participant-fixes) Beta measurement and participant fixes | done | 2026-10-31 |
| [T-11](#t-11-product-photos) Product photos | planned | |
| [T-12](#t-12-design-my-look) Design My Look (experimental) | done | 2026-10-31 |
| [T-13](#t-13-fixes-from-the-test-pass) Fixes from the test pass | done | 2026-11-01 |
| [T-14](#t-14-fixes-from-the-second-test-pass) Fixes from the second test pass | done | 2026-11-01 |
| [T-15](#t-15-fixes-before-the-human-test) Fixes before the human test | done | 2026-11-01 |
| [T-16](#t-16-fixes-from-the-repository-audit) Fixes from the repository audit | done | 2026-11-01 |
| [T-17](#t-17-items-left-open-by-the-audit) Items left open by the audit | done | 2026-11-01 |
| [T-18](#t-18-form-fixes-from-the-authors-test) Form fixes from the author's test | done | 2026-11-01 |
| [T-19](#t-19-small-fixes-from-the-authors-test) Small fixes from the author's test | done | 2026-11-01 |
| [T-20](#t-20-the-shopping-agent) The shopping agent | done | 2026-11-01 |
| [T-21](#t-21-fixes-from-the-review-of-the-shopping-agent) Fixes from the review of the shopping agent | done | 2026-11-02 |
| [T-22](#t-22-a-clothing-line-kept-by-code-and-the-reason-for-an-unknown-cost) A clothing line kept by code, and the reason for an unknown cost | done | 2026-11-02 |
| [T-23](#t-23-fixes-from-an-outside-audit) Fixes from an outside audit | done | 2026-11-02 |
| [T-24](#t-24-fixes-from-a-second-outside-audit) Fixes from a second outside audit | done | 2026-11-02 |
| [T-25](#t-25-follow-ups-to-the-second-outside-audit) Follow-ups to the second outside audit | done | 2026-11-02 |
| [T-26](#t-26-fixes-from-a-third-outside-audit) Fixes from a third outside audit | done | 2026-11-02 |
| [T-27](#t-27-basetens-answer-in-the-data-notice) Baseten's answer in the data notice | done | 2026-11-03 |
| [T-28](#t-28-search-results-a-person-can-use) Search results a person can use | done | 2026-11-03 |
| [T-29](#t-29-no-shop-description-reaches-a-model) No shop description reaches a model | done | 2026-11-03 |
| [T-30](#t-30-a-rule-for-reasons-and-a-try-on-from-title-and-color) A rule for reasons, and a try-on from title and color | done | 2026-11-03 |
| [T-06](#t-06-evaluation-and-submission) Evaluation and submission | next | |
| [T-07](#t-07-photo-of-an-owned-item) Photo of an owned item (optional) | planned | |

## T-01: Foundation

The React and FastAPI scaffold, dependency locks, health and readiness endpoints, the Dockerfile, tests and CI, in commits `aa96130` and `ef034a0`.

Done when: the checks in the [README](../README.md#check-a-change) pass, and CI builds the container and checks it over HTTP. CI passed on `ef034a0`.

## T-02: Feasibility test

Before the interface grows, test whether Meta's models keep a person's identity and a garment's details in a preview, and whether a targeted edit makes the requested change while the rest of the look stays materially consistent.

[FEASIBILITY.md](FEASIBILITY.md) has the protocol: several subjects who gave permission, several outfits made of a few specific garments, checks scored by hand, thresholds set before the run, and a record of every attempt. A model's evaluation of its own output is not enough. Personal photos and previews stay in `data/private/`, which Git ignores ([DATA.md](DATA.md)). If the test shows a need to cut garments or people out of images, Meta's segmentation model SAM is an option.

Preview quality, the model cost of a plan with its previews and user preference have not been measured yet.

The client, the run script and the approved thresholds exist, and the exploratory runs are done. The formal evaluation is set up; it needs two more subjects who give permission for model processing, their photos, and garment images that FullAura may use. Photos that the author takes of real garments are images FullAura may use, so the garment images do not depend on a retailer ([D-25](DECISIONS.md#d-25-product-facts-and-product-images-need-different-rights)). T-03 did not wait for the subjects. The beta's try-on did not wait for this test either ([D-44](DECISIONS.md#d-44-the-betas-try-on-is-experimental-and-does-not-wait-for-t-02)).

Done when: the thresholds were set before the run, `docs/FEASIBILITY.md` reports every attempt, and the result says whether previews meet [SPEC.md](SPEC.md#previews) or the fallback in [D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them) applies.

## T-03: Catalog and hard constraints

Define a typed product schema and a small catalog that FullAura has permission to use. Implement the hard constraints and locked-item state, including owned items and both budget modes, before any model orchestration. Keep the catalog data under `backend/`, because the container image leaves out `data/`.

The schema, the application state and the hard-constraint checks are in `backend/app/catalog.py`, `state.py` and `constraints.py`, with tests against a synthetic catalog in `tests/fixtures/`. The catalog, `backend/data/catalog.json`, holds the facts of real product variants from Armedangels and Giesswein, since T-08 from GEA Waldviertler, and since T-09 from Tom Tailor and Tchibo ([D-47](DECISIONS.md#d-47-tom-tailor-and-tchibo-supply-affordable-basics-and-closed-shoes)), and no retailer images ([D-26](DECISIONS.md#d-26-the-first-catalog-records-facts-from-armedangels-and-giesswein-without-images), [D-39](DECISIONS.md#d-39-closed-shoes-come-from-gea-waldviertler-with-partial-compositions)); its sources are in the register in [DATA.md](DATA.md). Product facts need a source whose terms of use allow FullAura to record them, and product images need a license, a feed whose terms permit display, or FullAura's own photos ([D-25](DECISIONS.md#d-25-product-facts-and-product-images-need-different-rights)). A test checks that the catalog builds three looks that pass every check, with no exclusion and under a wool exclusion.

Done when: each catalog source is recorded in the register in [DATA.md](DATA.md), and tests cover every hard constraint in [SPEC.md](SPEC.md#hard-constraints), including the cases that fail.

## T-04: One complete flow

Connect preferences, catalog selection, previews and edits into the flow in [SPEC.md](SPEC.md#what-a-person-does).

Planning exists on the backend: `POST /api/plans` takes a brief and the structured state, asks Muse Spark for three looks and returns them only after they pass the look-structure and hard-constraint checks ([D-28](DECISIONS.md#d-28-muse-spark-on-baseten-plans-and-code-checks-every-plan), [D-29](DECISIONS.md#d-29-looks-have-a-complete-base-outfit)). The provider uses one deadline across requests, retries and waits; token usage and HTTP attempts are counted separately ([D-30](DECISIONS.md#d-30-one-deadline-covers-planning-and-http-retries)). The local screen reads the catalog through `GET /api/catalog`, submits preferences and structured constraints, displays checked results, lets a person lock items before an update, and can replace one unlocked item while preserving every other position and rechecking all three looks. It reports planner violations instead of dropping them. The screen shows product facts and source links without product photos. The request-scoped preview route accepts two upright JPEGs and a prompt in memory, and the local interface supports selecting photos, cancelling a preview request and displaying its result ([D-31](DECISIONS.md#d-31-no-persistent-server-copies-of-personal-photos), [D-32](DECISIONS.md#d-32-preview-requests-have-one-deadline-and-cancel-on-disconnect)). At the time, `GET /api/previews/status` and `FULLAURA_T02_PREVIEWS_PASSED` kept uploads disabled until T-02 passed; T-09 replaced that gate with the closed beta's experimental try-on ([D-44](DECISIONS.md#d-44-the-betas-try-on-is-experimental-and-does-not-wait-for-t-02)).

The preview request uses one 120-second deadline across upload, provider calls and retry waits. The asynchronous HTTP request is cancelled at the deadline, and a browser disconnect cancels the provider task. Inputs and result buffers are cleared after success, timeout, disconnect or failure. Planning requires a server-recorded 18+ confirmation. At the time it also permitted three planning requests per anonymous browser session, counting updates and replacements, which T-09 replaced with invites. The interface explains which preference fields go to Baseten and how photos will be processed if previews are enabled. Production planning remains closed by default ([SPEC.md](SPEC.md#photographs), [closed beta access](SPEC.md#closed-beta-access)).

Done when: a person can go through the whole flow locally, from inputs to an edited plan. If previews failed the feasibility test, the person can still complete a sourced style plan, and the interface states the visual limitation ([D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them)).

## T-05: Azure demo

Deploy the tested container with secrets kept on the backend only, access controls that fit the pilot, and monitoring that excludes personal inputs. The code required an explicit opt-in flag for public planning and limited each browser session to three requests, which was not an account-wide cost cap; T-09 replaced this with invites and a shared spending ledger ([D-43](DECISIONS.md#d-43-an-invite-only-beta-replaces-the-anonymous-demo)). [DEPLOYMENT.md](DEPLOYMENT.md) has the settings and the steps. Azure Container Apps is the accepted direction ([D-12](DECISIONS.md#d-12-one-azure-container-app-for-the-demo)). Before deploying, validate the region quota, the credit balance and the cost controls, as step 2 of the deployment sequence says.

Done when: the checks in step 5 of the deployment sequence pass, and the URL, source commit, resource names and deployed image digest are recorded in DEPLOYMENT.md. Record a previous working rollback digest when one exists; the first deployment has no earlier working image.

## T-08: Looks for a stated wearer

In the T-06 comparison every plan passed the hard constraints, but plans mixed men's and women's lines and one combined shoes in EU 39 and 42, because nothing said whom the looks were for. The person now states the clothing lines, the kinds of bottoms and one-pieces they wear and their sizes, and code checks them as a hard constraint ([D-36](DECISIONS.md#d-36-the-person-states-whom-the-looks-are-for)). The catalog records each product's line and garment type and a comparable size for each variant. For the demo wearer it gains variants in their sizes and closed wool-free shoes from a source whose terms are recorded in [DATA.md](DATA.md).

The live T-06 run of 2026-10-30 on commit `dd8ba66` meets the second condition below: both approaches passed every hard check in all four cases ([EVALUATION.md](EVALUATION.md#2026-10-30)). The demo wearer's acceptance remains.

On 2026-11-01 the author ran the steps on the deployed site at `8ba7fac`. A budget of 100 for each look was refused for free, with the reason. A budget of 200 gave three looks; keeping one item and lowering the budget to 150 gave looks of 124.88, 149.88 and 134.88; replacing an unlocked bottom changed only that position. Every result respected the stated men's line, trousers and sizes L, 32/32 and 43, with closed shoes. 

The demo flow is not accepted. For the three looks of the first plan, the author answered "Is this what I asked for?" with yes, no, no and "Would I go and try this on?" with yes, no, yes, and [D-41](DECISIONS.md#d-41-the-demo-is-accepted-on-its-wearers-yes-or-no-answers-set-before-the-run) needs six yes answers. These answers came after the edits, although D-41 asks for them before any edit. The author said they would wear the looks indoors in winter but hardly outdoors, depending on the weather. The catalog's only men's outer layer is an overshirt at €159.90, and the cheapest complete look costs €124.88, so it did not fit the budget of 200 for each look. A new attempt needs another invite, because this one has no planning requests left.

Done when: the checks in the [README](../README.md#check-a-change) pass; a new T-06 run on this code, including the example-fit case ([D-40](DECISIONS.md#d-40-the-t-06-comparison-adds-a-case-with-an-example-fit)), records no hard-constraint violation for either approach, with the earlier run kept; and on the deployed site the demo wearer gets three looks for their stated fit, keeps an item, lowers the budget and replaces an unlocked item, every result respects the fit, and the wearer answers yes to both acceptance questions for each of the three looks of the first plan ([D-41](DECISIONS.md#d-41-the-demo-is-accepted-on-its-wearers-yes-or-no-answers-set-before-the-run)).

## T-09: Closed beta

The author and friends use the whole product on the deployed site: invite, preferences, three looks, a try-on on their own photos, keeping and replacing items, a shopping list, saved plans and feedback ([BETA.md](BETA.md), [D-43](DECISIONS.md#d-43-an-invite-only-beta-replaces-the-anonymous-demo) to [D-46](DECISIONS.md#d-46-the-server-keeps-the-checked-plan-and-the-browser-keeps-saved-copies)). Austria, EUR and English stay. Extra costs stay within $25. Until the beta is handed over, only synthetic adult personas are used, and earlier personal photos and results are not. The beta is experimental and does not complete T-02 or show that real people find the product useful; the participants' use answers that.

The catalog covers four example profiles in three scenarios, each with three different complete looks with closed shoes and at least one unlocked position that a valid replacement can fill ([DATA.md](DATA.md#catalog-coverage)).

Withdrawing photo consent stops a try-on in progress and clears its photos and result, and a catalog that fails to load after an accepted invite can be loaded again. Four decisions of 2026-10-31, after the task finished, add to this. A running model call blocks its invite until it ends or is 300 seconds old, whichever revision started it ([D-48](DECISIONS.md#d-48-a-running-call-blocks-its-invite-whichever-process-started-it)). A scheduled job deletes the pilot records on 2026-11-09 from 10:00 Vienna time, a date [D-82](DECISIONS.md#d-82-the-pilot-stays-open-through-the-hackathons-judging) later moved to 2026-11-10, after the pilot ends, and the beta closes then ([D-49](DECISIONS.md#d-49-a-scheduled-job-deletes-the-pilot-records-and-the-beta-closes-then)). A closure marker and a grace period keep a write accepted just before then from bringing a record back ([D-50](DECISIONS.md#d-50-a-closure-marker-and-a-grace-period-keep-writes-from-outliving-the-purge)), and the app and the job roll back separately ([D-51](DECISIONS.md#d-51-the-app-and-the-purge-job-have-their-own-images-and-roll-back-apart)).

Done when: the checks in the [README](../README.md#check-a-change) pass, including the browser flow on a desktop and a phone viewport and the catalog coverage test; the error cases pass (incompatible budget, no replacement, timeout, cancellation, a foreign or stale plan token, an exhausted quota, a repeated operation ID, a restart); logs, the pilot store, saved plans and the build hold no photo, try-on, full profile or secret; live runs with three synthetic adult personas pass a preliminary visual check by an independent agent; and one full run on the deployed beta with the real providers and synthetic photos succeeds, with try-on enabled for invited sessions and spending counted across a restart.

## T-10: Beta measurement and participant fixes

Make the closed beta answer what to build next, and remove what participants would trip over. The pilot report counts invites, call outcomes, feedback and spending, each with its denominator, and call records keep the outcome apart from the cost ([D-52](DECISIONS.md#d-52-a-call-record-keeps-its-outcome-apart-from-its-cost-and-the-pilot-report-counts-invites)). Feedback asks what a try-on got wrong ([D-53](DECISIONS.md#d-53-feedback-asks-what-a-try-on-got-wrong-from-a-fixed-list)). The interface says what the budget limits next to the request button ([D-54](DECISIONS.md#d-54-the-interface-says-what-the-budget-limits-next-to-the-request-button)), and the data notice describes what Baseten receives and keeps, and that an owned item's description can be part of a try-on's text. A second closed shoe in EU 40 removes the catalog's single point of failure ([D-55](DECISIONS.md#d-55-a-second-closed-shoe-in-eu-40-keeps-womens-l-from-depending-on-one-pair)), and evaluation runs record their provenance ([D-57](DECISIONS.md#d-57-evaluation-run-records-carry-their-provenance-including-failed-runs)). Keyboard focus no longer falls back to the top of the page after a plan request or a feedback answer. The author accepted guideline thresholds for reading the pilot's results ([D-58](DECISIONS.md#d-58-the-pilots-results-are-read-against-guideline-thresholds)).

The work was deployed on 2026-10-31 as `7e1d48d` and checked on the deployed site ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass, including the browser flows on a desktop and a phone viewport; the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes and the deployed site passes the HTTP smoke check, before the final pilot report is read.

## T-11: Product photos

Show a photo of each recommended variant in its color, so people can compare looks in the app ([D-56](DECISIONS.md#d-56-a-variant-can-carry-a-photo-only-with-recorded-rights)). A catalog variant can carry a photo with a description, its size and a credit, and the look cards show it in a box sized before it loads, with a fallback when it fails. No source whose rights allow displaying the photos is recorded yet, so no variant has one and the cards look as before.

Done when: [DATA.md](DATA.md) records the license, feed terms or own photography for the photos of the variants the coverage cases use, those variants carry them, the data notice names the host that serves them, and the look cards show them on the deployed site.

## T-12: Design My Look

An experimental second mode for the hackathon demo, behind `FULLAURA_DESIGN_MODE`: a concept look from the person's wishes, the person in it as a try-on, and for each item the closest catalog product as code judges it, or an honest "no close match" ([SPEC.md](SPEC.md#design-my-look), [D-59](DECISIONS.md#d-59-design-my-look-is-an-experimental-mode-behind-a-flag)). The catalog-first flow stayed the default until [T-20](#t-20-the-shopping-agent) and is still the fallback. Deploying the mode or offering it to beta participants needs a separate decision.

Done when: with the flag off, every check in the [README](../README.md#check-a-change) passes and the product behaves as before; with the flag on, a request gives a checked concept, its try-on, and for every item a close match, an alternative with its differences, or no close match, covered by tests and by the browser flow on a desktop and a phone viewport; and an independent review finds no open critical or important issue. A recorded request whose every item has a close match and whose matched look passes every hard constraint is a wanted result for the demo, not a condition.

## T-13: Fixes from the test pass

A test pass of the whole product on 2026-10-31 found what participants would trip over and what a hand-made request could do. Every plan now uses one size of a product, and a new plan has three different looks ([D-62](DECISIONS.md#d-62-a-plan-uses-one-size-of-a-product-and-its-three-looks-differ)). A budget under the cheapest complete look, kept items that cannot fit with what their look still needs, and a replacement that cannot fix the positions it keeps are infeasible without a model call ([D-63](DECISIONS.md#d-63-code-reports-a-request-as-infeasible-when-prices-or-kept-positions-rule-out-every-plan)). A hand-made budget or an unreadable request gets a plain refusal, technical errors stay off the page, and the site sends security headers ([D-64](DECISIONS.md#d-64-refusals-stay-plain-and-every-response-carries-security-headers)). A try-on names the kind of garment a product's title states.

The form opens a closed section that holds an empty required field; an invite link works in a tab that is already open, and a damaged one is refused. Saving an eleventh plan names the plan it removes. The page dates the catalog's prices by their oldest and newest checks, shows stock when a shop reports it, and says that the server keeps a plan until a day after the person last uses FullAura.

The work was deployed on 2026-11-01 as `a5e6028` and checked on the deployed site ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass, live plans with no stated shoe or waist size use one size of each garment, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check.

## T-14: Fixes from the second test pass

A test pass of the whole product on 2026-11-01 found no critical failure, but what participants would meet first needed fixing. A budget that three different looks cannot fit, and a replacement no other item can fill, are refused without a model call, and a refusal names the budget or the sizes that would work ([D-65](DECISIONS.md#d-65-code-also-refuses-a-budget-that-three-different-looks-cannot-fit)). An owned item's description can no longer instruct the image model ([D-66](DECISIONS.md#d-66-an-owned-items-words-reach-the-image-model-through-a-closed-list)).

When the store fails, the plan stays on the screen instead of turning into the age question, as [SPEC.md](SPEC.md#closed-beta-access) already said. A kept item survives a reload, saving and restoring. Saved plans can be read and deleted after the beta closes, as [BETA.md](BETA.md#for-participants) already said. A failure names products and looks instead of catalog IDs, and the Keep and Replace buttons stay inside their card at every width.

The catalog now holds 104 variants of 37 products: women's M has tops and trousers, women have knitwear and outerwear, and shoes reach women's EU 36, 37 and 41 and men's EU 41 and 44, with XL, men's S and women's XS ([D-67](DECISIONS.md#d-67-the-catalog-grows-by-sizes-without-converting-any)). [DATA.md](DATA.md#catalog-coverage) lists the sizes that stay uncovered.

The work was deployed on 2026-11-01 as `33e4fe3` and checked on the deployed site ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass, live runs with the real providers and synthetic personas show the refused budgets using no planning request and two owned-item descriptions that hold instructions drawn as plain garments, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check.

## T-15: Fixes before the human test

An agent walked through the deployed site as a new person on 2026-11-01, and a second agent reviewed the result. Four things could change a participant's answers or lose their work, so they come before people test the product ([D-68](DECISIONS.md#d-68-the-page-says-what-the-checks-did-not-compare)). The page no longer says that sizes or stock were checked when some were not, and it names them. Restore asks before it replaces work that is not saved, including kept items and form changes, and answers beside the saved plans. "Try this look on" moves keyboard focus to the try-on panel, and a finished try-on says it is ready and comes into view unless the person moved the focus elsewhere. While an update is possible, each look explains why Replace is off after the form changes, also after a reload. Separately, a request or try-on that failed at the provider says that it still counts.

The changes were checked on a local server with scripted providers, including by keyboard in Chromium and Firefox. The work was deployed on 2026-11-01 as `7267da0` and checked on the deployed site with one real try-on ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). No plan ran there, because no invite that may be used has planning requests left; the first such plan is the T-08 acceptance.

Done when: the checks in the [README](../README.md#check-a-change) pass, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check and these four scenarios repeated on it.

## T-16: Fixes from the repository audit

An agent audited the whole repository on 2026-11-01, with two more agents reviewing the frontend and the operations files, and checked each finding before fixing it. On the try-on, the newest photo chosen for an input is the one sent, even when an earlier choice finishes preparing later; a try-on that ran out of time says that it may still count; a printout holds the looks and the lists but no try-on, and an opened saved list now prints ([D-69](DECISIONS.md#d-69-a-printout-holds-the-looks-and-the-lists-never-a-try-on)). A reload no longer accepts a new request while it brings back the working plan, a try-on that finds the plan expired and failing says so, and restoring a plan after a try-on keeps Replace on when only the way the budget or sizes were written differs. An owned item removed from the form can no longer be kept, so updates stay valid; a saved plan is restored only once the catalog has loaded; consent failures show in the try-on panel; and keyboard focus no longer drops to the top of the page after a deleted plan, a consent change or a removed item, while the question before a delete gives the focus to the answer that keeps the plan. Until [T-18](#t-18-form-fixes-from-the-authors-test) made the goal optional, a style goal of spaces alone was refused before a request.

For operations, `.dockerignore` keeps `.env` files, `tmp/`, logs and private data out of the image at any depth, not only at the root. The store and the pilot report send a storage token only to an `https://<account>.blob.core.windows.net/<container>` URL, the report ignores spaces around invite digests as the app does, also reads two joined digest files, and refuses any other file, and `pilot_invites` never replaces an earlier batch's digests and creates the codes file readable by the operator alone. CI keeps a result for every commit on `main` and keeps a container that stopped at startup until its logs are read. A test covers an invite removed from the configured digests.

The changes were checked with the README's checks and the browser flows on a local server with scripted providers. The container could not be built in the audit's environment, so CI ran its first build and HTTP check. The work was deployed on 2026-11-01 as `87fd7a2` and passed the HTTP smoke check on the deployed site, where no plan or try-on ran ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass, including CI's container build and HTTP check, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check.

## T-17: Items left open by the audit

A try-on no longer starts while a plan request runs, and one that finds its plan replaced stops without bringing the old plan back over the newer one ([D-70](DECISIONS.md#d-70-a-try-on-waits-for-a-running-plan-request-and-never-restores-over-a-newer-plan)). The purge job's replica timeout rises to 1800 seconds so that every retry fits, the Dockerfile pins its base images by digest, and the runtime user can read the application but not change it, which CI checks ([D-71](DECISIONS.md#d-71-the-purge-jobs-time-limit-covers-every-retry-and-the-images-user-cannot-change-the-application)).

In WebKit, the engine of Safari, a required field in a closed section now takes the focus when the browser refuses the form; before, the section opened with the focus on nothing. The browser flow ran locally in Firefox and in WebKit, the latter with a clipboard stand-in and Option+Tab, because Playwright's WebKit cannot read the clipboard and its plain Tab skips buttons. CI runs Chromium only.

CI still does not publish the image it builds; the operator builds the deployed image from the commit CI checked.

The work was deployed on 2026-11-01 as `8ba7fac`, and the purge job's timeout was raised on the live job. The deployed site passed the HTTP smoke check and a check of the changed behavior with one real try-on ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). No plan ran there during that check, so a try-on that finds its plan replaced by an update ran only in unit tests.

Done when: the checks in the [README](../README.md#check-a-change) pass, the browser flow also passes in Firefox and WebKit, the purge job runs with the new timeout and otherwise unchanged settings, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check and a check of the changed behavior.

## T-18: Form fixes from the author's test

The author used the deployed site on 2026-11-01 and found the form too long and its goal field forced. The goal is now optional, occasion, season and colors sit in a closed section, the button that sends the form stays at the bottom of the screen, and the waist field says how to write a waist and length ([D-72](DECISIONS.md#d-72-a-person-can-leave-the-style-goal-empty-and-the-forms-button-stays-on-screen)). A failure on CI also showed that a consent button pressed just after the try-on panel appeared lost the keyboard focus, which is fixed.

The work was deployed on 2026-11-01 as `b192cbd`. The deployed site passed the HTTP smoke check and shows the shorter form ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). No plan ran with the new form during that check. The author later ran one there with an empty goal, and it passed the catalog checks.

Done when: the checks in the [README](../README.md#check-a-change) pass, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check and showing the shorter form.

## T-19: Small fixes from the author's test

The author's test on 2026-11-01 also found four small defects. The answer "I can't judge it" broke over four lines and now stays on one. Two saved plans with one goal had the same name, and a later one now takes a number. A printed shopping list could break inside an item and now breaks between items. A heading or message that a script gives the focus to showed a ring, as if it were a control, and no longer does; controls keep their ring, and the next Tab still goes on from that place.

The work was deployed on 2026-11-01 as `6c4a462` and checked on the deployed site ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check.

## T-20: The shopping agent

A person says what they need, for a whole outfit, the upper body or one item, and an agent chooses among real products that code lists from shops' sitemaps and reads from their pages ([SPEC.md](SPEC.md#find-it-for-me), [D-73](DECISIONS.md#d-73-the-shopping-agent-chooses-among-real-shop-pages-that-code-reads)). The mode opens first, and the catalog stays as the other tab. Messages now have separate colors for working, done, something to check and an error; a request that runs shows a spinner; and Keep and Replace are quieter.

The work was deployed on 2026-11-01 as `f2d68dc` and checked on the deployed site, including one real search and one try-on of what it found ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check and a real search there.

## T-21: Fixes from the review of the shopping agent

An independent review of `563cd2e` found gaps that its tests did not cover. A recommendation listed before the product could become the page's product under that product's address ([D-74](DECISIONS.md#d-74-a-pages-facts-come-only-from-the-product-it-names)). Six unsuitable pages hid a seventh that could be bought ([D-75](DECISIONS.md#d-75-a-search-reads-pages-in-portions-under-one-limit)). A search that could read no page used a person's request ([D-76](DECISIONS.md#d-76-a-search-that-could-read-no-page-gives-its-request-back)). A dress the model called a top passed for the upper body ([D-77](DECISIONS.md#d-77-a-shops-title-can-rule-out-a-position-or-leave-it-unconfirmed)). The page and the documents now also say what the checks and the try-on do not show ([SPEC.md](SPEC.md#find-it-for-me)).

The first search on the deployed site then found what scripted providers could not show. A whole outfit could end with no answer, because the model's reasoning used up the output cap, and women's outfits found nothing, because the model searched for a word the shop's addresses do not use ([D-78](DECISIONS.md#d-78-the-shopping-agent-bounds-its-reasoning-and-a-clothing-line-is-not-a-word-to-search-for)). A search could also offer a product of another clothing line, because most Tchibo products name none ([D-79](DECISIONS.md#d-79-a-product-that-names-no-clothing-line-is-marked-not-left-out)).

The work was deployed on 2026-11-02 as `bf5751c` and checked on the deployed site ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). The whole outfit ran on that revision, and the upper body and one item on the one before it. This task does not show that the recommendations suit what a person asks for; that remains the author's judgment.

Done when: the checks in the [README](../README.md#check-a-change) pass, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check and real searches for a whole outfit, the upper body and one item, including results from a shop other than Tom Tailor.

## T-22: A clothing line kept by code, and the reason for an unknown cost

A further review of `bf5751c` left two points. A chosen clothing line is now kept by code ([D-80](DECISIONS.md#d-80-a-chosen-clothing-line-must-be-named-in-the-address)), and a search of unknown cost says why in the log ([D-81](DECISIONS.md#d-81-a-call-of-unknown-cost-says-why-in-the-log)).

The work was deployed on 2026-11-02 as `259c686` and passed the HTTP smoke check there ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). No search ran there at that point, to keep the requests left on the invite for the demo video. Two searches with a later invite then succeeded on the same revision.

Done when: the checks in the [README](../README.md#check-a-change) pass, real searches on this code succeed for each scope and both lines, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check.

## T-23: Fixes from an outside audit

An outside audit of the source, made with GPT, named six defects at the boundaries with the model providers and the shops. Five were confirmed and fixed, with tests that fail on the earlier code:

- Token counts that cannot be true no longer reach the spending ledger ([D-85](DECISIONS.md#d-85-token-counts-that-cannot-be-true-count-as-an-unknown-cost)).
- A robots.txt group applies only if it names `fullaura`, the crawler's name that [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.2.1) matches, or the `FullAura/0.1` that the requests send. A group for another agent whose name is part of this one, or for an empty name, applied before.
- A shop owns only addresses on its own port and without credentials.
- Addresses in a sitemap are read with XML's escapes. Giesswein's links to its sitemap files hold `&amp;`, and were requested with it.
- A truncated gzip sitemap counts as corrupt, so the shop's load fails and it keeps its last list.

The sixth point, that a malformed model reply crashes a search, was not adopted. `/api/finds` already answers any failure of the agent as a failed search and keeps the reservation, which `test_a_failed_search_answers_without_detail_and_keeps_its_reservation` checks. The proposed handling would have paid for one more model call and then told the person to try other words.

The three shops' live robots.txt files and sitemaps were read before and after the change on 2026-11-02. No robots.txt has a group that the new matching treats differently, no sitemap is gzipped, and no product address holds an escape or a port. The address counts stayed at 6,584 for Tom Tailor, 36,853 for Tchibo and 781 for Giesswein, where one address moves between language files from read to read, however the link is written. The token counts in 11 recorded replies from Meta and 6 from Baseten all pass the new check. So the fixes change nothing that people see today. They reached the site with the deployment of `1688393`.

Done when: the checks in the [README](../README.md#check-a-change) pass, the new tests fail on the earlier code, and the comparison with the live shops loses no address.

## T-24: Fixes from a second outside audit

A second outside audit of the source, made with GPT, named five possible defects. Two were confirmed and fixed, with tests that fail on the earlier code:

- The shopping agent takes the last JSON object with `items` in its reply, with the fields in any order. An answer that did not start with `items`, or that another object followed, used to cost a repair call. An object that cannot be read after a draft still sends the reply back to the model, because the checks cannot tell a draft that passes from the answer. The 7 model replies recorded in the agent's trial runs each hold one object that starts with `items`, so they read as before.
- The shopping agent's form refuses more than six sizes, or a size longer than 12 characters, before calling the server. It used to drop the sizes after the sixth without saying so, and to send a longer size for the server to refuse.

The audit's parser was not adopted. It took the first object with `items`, so a reply that held a draft and then its answer would have shown the draft. Its patch also did not apply to the source.

The other three points change nothing here:

- Opening an invite in a tab that held a working plan brought that plan and its preferences back, whichever invite made them. [T-25](#t-25-follow-ups-to-the-second-outside-audit) changes this.
- The image provider's answer is read into memory with no limit on its size. Only the 120-second deadline bounds it, and the sender is the authenticated provider. This stays as it is.
- A plan request and a restore that run together in one browser session do not corrupt anything. The later one replaces the other, both plans passed every check, and the older token is refused with `plan_expired` before anything is counted. The page answers that by restoring its own plan. This was read in the code, not run under load.

The change reached the site with the deployment of `1688393`.

Done when: the checks in the [README](../README.md#check-a-change) pass and the new tests fail on the earlier code.

## T-25: Follow-ups to the second outside audit

The same auditor reviewed the T-24 work and asked for two more changes, and then named three defects in the first of them. All are made, with tests that fail on the earlier code, and a browser test now switches invites in Chromium:

- A working plan stays with the invite it was made under ([D-86](DECISIONS.md#d-86-a-tabs-working-plan-stays-with-the-invite-it-was-made-under)). The author accepted that decision. The session cookie that the tabs of one browser share stays a known limitation, which it records.
- A request still running when another invite opens in the same tab no longer reaches that invite. A plan that answered late used to be written to the tab, and an update that found its plan expired used to restore it and send it again under the next invite's cookie.
- A failed write to the tab's storage no longer breaks a change of invite. The tab kept the invite under its own key, so a failed write left the earlier invite there. The page then mounted again and again without opening, and on the way back to the earlier invite it kept the later invite's work and could restore its plan. The plan and its invite are now written together, the open page holds its invite in memory, and the page mounts once for an invite.
- Invites open one at a time, so the link opened last decides both the cookie and the page. A late answer to an earlier link used to decide them.
- Nothing inside a JSON object that cannot be read counts as the shopping agent's answer. The parser used to read on inside a broken object, so it could take a draft with `items` nested in it. It now skips to where the object's braces close, or to the end of the reply when they never do. A `}` inside the text of a broken object can still end it early. That takes a reply with a broken wrapper and a brace in its text, and none of the 7 recorded replies has a wrapper, so it stays.

The work reached the site with the deployment of `1688393`, where switching invites was also checked in Chromium ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass and the new tests fail on the earlier code.

## T-26: Fixes from a third outside audit

A third outside audit of the source, made with GPT, named two defects. Both were confirmed and fixed, with tests that fail on the earlier code:

- A range of prices no longer counts as a price, whatever type the offer states. The check looked only for an `@type` that was exactly `AggregateOffer`, so a list of types, the full schema.org address, another type or no type let a range's low price through as the product's price. An offer now has no price when its `lowPrice` and `highPrice` differ, including an offer with only a `lowPrice` and one that also states a `price`. 55 saved shop pages and 15 live pages of each of the three shops, read on 2026-11-02, hold 265 offers, and none has a `lowPrice` or `highPrice`, so these pages read as before.
- The provider's error type and code now get the same treatment as its message: the API key is removed, line breaks become spaces, and each is limited in length. Only the message used to be cleaned. The running app neither logs this text nor sends it to the browser, so it reaches people only through `scripts/smoke_models.py` and `scripts/feasibility_run.py`.

The audit's patch was not adopted as written. Its check of the price still let an offer of another type or no type through, and it did not apply to the source.

These changes reached the site with the deployment of `029b163`.

Done when: the checks in the [README](../README.md#check-a-change) pass and the new tests fail on the earlier code.

## T-27: Baseten's answer in the data notice

The participants' data notice and [DATA.md](DATA.md#model-providers) give Baseten's email answer of 2026-11-02 about FullAura's key and about Meta, in place of the caveat that its policy was unchecked for FullAura's account ([D-87](DECISIONS.md#d-87-the-data-notice-gives-basetens-answer-about-fullauras-key)).

This change reached the site with the deployment of `029b163`, where the notice was also checked in Chromium ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass and the notice's tests hold the new wording.

## T-28: Search results a person can use

An agent used the site as a new person on 2026-11-03. Of the five products suggested in three searches that gave a size, four could not be bought in that size, a request for a wedding outfit got a casual shirt and lined winter boots called "elegant", and a reload lost a search that a request had paid for. This is an agent's walk, not evidence from people, who still have to test the product.

- A search compares sizes that a page lists, including Tom Tailor's size list in the page as served, within their own system, and leaves out what is not sold in the person's size ([D-88](DECISIONS.md#d-88-a-search-compares-sizes-the-page-lists-within-their-own-system)).
- The agent is asked to dress for the occasion and to give only reasons that the product's facts support, and is told which words find shoes ([D-89](DECISIONS.md#d-89-the-agent-reads-the-shops-description-and-dresses-for-the-occasion)). This task also sent it the shop's description, which [T-29](#t-29-no-shop-description-reaches-a-model) removed.
- A tab keeps its last search from the moment it is sent, and a page reloaded during a search collects the answer without paying again ([D-90](DECISIONS.md#d-90-a-tab-keeps-its-last-search-and-a-reload-collects-a-running-one), [D-91](DECISIONS.md#d-91-a-search-is-kept-from-the-moment-it-is-sent)).
- A message that another request is running is worded to stay true after that request ends.
- The selected tab's label stays readable under the mouse. It had a contrast of 1.07 to 1 there, and the mouse rests on a tab right after a click.

On this code, on a local server with the real model and shops, seven searches across all three scopes and all three line choices, in English and German, all returned products. Six of the seven were compared with each shop's own page. There, all 11 products whose size code had checked were on sale in that size, and for the other 4 with a size given the page said that the size was not checked, because they are sold in numbers and a letter is not compared with those. All prices matched. Both runs of the men's wedding request returned a blazer, suit trousers, a shirt and lace-up shoes, and the agent's reasons said where the shirt or the shoes fall short of formal wear. A real search reloaded after five seconds was collected about 30 seconds later and used one request, and a try-on of the result worked after a further reload. Two earlier wordings of the prompt made women's outfits end without products, and the final one fixed the cases tried. Seven searches do not show how often a search fails or how well it suits people.

The first deployment, of `181323b`, kept a search only once the answer's first line arrived. A real search reloaded after about five seconds on the deployed site lost its results and used its request, because a server that has just woken registers a search only after reading the shops' lists ([D-91](DECISIONS.md#d-91-a-search-is-kept-from-the-moment-it-is-sent)). With the fix, the same case on a local server just started, in WebKit with a reload after two seconds, before any step had appeared, was collected about 22 seconds later and used one request.

The work reached the site with the deployment of `264fd01`, where the same case, one real search in WebKit reloaded about five seconds after it was sent, was collected, used one request and kept its results through a further reload ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). Its products passed a check against the shop's pages. It ran in Playwright's WebKit, not on a real phone.

Done when: the checks in the [README](../README.md#check-a-change) pass, the new tests fail on the earlier code, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check and a real search there.

## T-29: No shop description reaches a model

A review of T-28 asked whether shop descriptions caused its better results, and whether FullAura may send them to Meta. A blind comparison followed, with its rules set before the results. On all 39 products it compared, Tom Tailor's description field held only a sales line, so that wording added nothing. The comparison supports the combined changes to the prompt and search words, and it did not isolate those two from each other ([D-92](DECISIONS.md#d-92-no-shop-description-or-category-reaches-a-model)).

Code no longer reads a description. The search model gets no description and no category. A try-on's text used the shop's category path as a provisional choice for the demo, because it drew a checked shirt as checked in both drawings of it, where the other prompts drew it plain; [T-30](#t-30-a-rule-for-reasons-and-a-try-on-from-title-and-color) removed it. Whether FullAura may send shop text, categories included, stayed open for the author ([DATA.md](DATA.md#reading-shop-pages)).

On this code, with the real model and shops on the operator's machine, the six compared cases ran once each and all returned products. A record of all 18 calls to the search model held no sales line, description field or category.

The work reached the site with the deployment of `f881183`, which passed the HTTP smoke check, a real search and a try-on there ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). That search also showed an open problem: the agent called a checked shirt "solid blue" and "without check pattern", and the try-on drew it checked, so the page contradicts itself. The search model sees no category, so it cannot know, and the prompt's rule against calling a product plain did not stop it. [T-30](#t-30-a-rule-for-reasons-and-a-try-on-from-title-and-color) adds a stricter rule as a mitigation.

Done when: the checks in the [README](../README.md#check-a-change) pass, the new tests fail on the earlier code, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check, a real search and a try-on there.

## T-30: A rule for reasons, and a try-on from title and color

On the author's instruction of 2026-11-03, for the limited test:

- The search prompt tells the agent to state a pattern, lining or fabric, or its absence, only if the title or material gives it. This is a mitigation, not a guarantee.
- Code no longer reads a shop's category, and a try-on's text holds only the shop's color and title. The try-on panel and [BETA.md](BETA.md#for-participants) say that a pattern or detail the title does not name can be missing ([D-92](DECISIONS.md#d-92-no-shop-description-or-category-reaches-a-model)).
- The author accepted [D-90](DECISIONS.md#d-90-a-tab-keeps-its-last-search-and-a-reload-collects-a-running-one) and [D-91](DECISIONS.md#d-91-a-search-is-kept-from-the-moment-it-is-sent), and [D-88](DECISIONS.md#d-88-a-search-compares-sizes-the-page-lists-within-their-own-system) provisionally. D-92 stays proposed, and whether FullAura may send shop titles stays unanswered. [D-93](DECISIONS.md#d-93-the-limited-test-goes-ahead-with-the-rights-risk-accepted) accepts the risk for a test of up to 10 participants and 5 judges.

The rule was tested with the real model and shops on the operator's machine, in a shuffled order: 13 runs of the prompt as deployed and 13 of the new one, on a bank interview (5 each), a men's wedding (5 each) and a women's wedding (3 each). All 26 returned products. With the prompt as deployed, 6 of 46 reasons, in 6 of 13 runs, stated a pattern or its absence that the title and material do not give, such as "solid" or "without check". With the new prompt, none of 46 reasons did. A first count by code was wrong, because it took "size-checked" for a pattern, so the count was redone, and all 46 reasons of the new prompt were also read. Thirteen clean runs would still fit such claims in up to about one run in five, so this does not show that they stop. The new prompt's reasons mostly list materials, and they can still judge a product "formal" or "suitable" without support.

The check of `24812dd` on the deployed site found a fault in the reload of [D-90](DECISIONS.md#d-90-a-tab-keeps-its-last-search-and-a-reload-collects-a-running-one). On an invite whose last request had just been used, a reload hid "Find it for me", because the page showed that mode only while it could search, so the results of that last search could not be seen although the tab held them. The mode now also opens while the tab holds a search of that invite, with its button off and the reason shown.

The work reached the site with the deployment of `037b6fc`. A real search and a try-on ran there on `24812dd`, and the reload on `037b6fc` ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)). The try-on drew a checked shirt and a houndstooth blazer plain, which is the documented limit of a try-on from title and color.

Done when: the checks in the [README](../README.md#check-a-change) pass, the new tests fail on the earlier code, and the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes, with the deployed site passing the HTTP smoke check, a real search and a try-on there. Invitations stay on hold until then.

## T-06: Evaluation and submission

The submission deadline is 2026-11-04 21:00 ET, which is 2026-11-05 03:00 in Vienna. The internal target is 2026-11-04 21:00 Vienna time.

Compare the full workflow with a simple baseline on the same cases. Record constraint failures and human judgments separately. Prepare the demo video and the public contribution summary, which is the section [Built during the hackathon](../README.md#built-during-the-hackathon) of the README.

The participant guide requires a public repository, a three-minute video that highlights what was built during the event, and every person who worked on the project on the submission page, and it says that judges must be able to tell what was created during the event, or the project is disqualified. Judges use the video, the code and the live product, and they score impact (30%), use of Meta's AI tooling (25%), technical depth and execution (20%) and the demo (25%). The submission form has no field for the live site or for access details, and its consent box lets the organizers publish what is submitted, so no invite code goes in it.

The fixed cases, deterministic baseline, live comparison metrics and the author's answers for the example-fit case are recorded in [EVALUATION.md](EVALUATION.md). Raw screen recordings of two searches and a try-on on the deployed site exist on the operator's machine, without narration, and [DEMO.md](DEMO.md) shows the same runs for people without an invite. The finished video and the submission remain outstanding, so T-06 is not done.

Done when: the submission is sent and its confirmation is received. Being ready to submit does not finish the task, so record each step on its own line:

- Ready to submit:
- Submitted:
- Confirmation received:

## T-07: Photo of an owned item

An optional photo of an owned item, added after T-02 and T-04 are done ([D-01](DECISIONS.md#d-01-owned-items-are-a-short-optional-text-input)).

Done when: to be defined when the task starts.
