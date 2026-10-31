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
| [T-06](#t-06-evaluation-and-submission) Evaluation and submission | planned | |
| [T-07](#t-07-photo-of-an-owned-item) Photo of an owned item (optional) | planned | |

## T-01: Foundation

The React and FastAPI scaffold, dependency locks, health and readiness endpoints, the Dockerfile, tests and CI, in commits `aa96130` and `ef034a0`.

Done when: the checks in the [README](../README.md#check-a-change) pass, and CI builds the container and checks it over HTTP. CI passed on `ef034a0`.

## T-02: Feasibility test

Before the interface grows, test whether Meta's models keep a person's identity and a garment's details in a preview, and whether a targeted edit makes the requested change while the rest of the look stays materially consistent.

[FEASIBILITY.md](FEASIBILITY.md) has the protocol: several subjects who gave permission, several outfits made of a few specific garments, checks scored by hand, thresholds set before the run, and a record of every attempt. A model's evaluation of its own output is not enough. Personal photos and previews stay in `data/private/`, which Git ignores ([DATA.md](DATA.md)). If the test shows a need to cut garments or people out of images, Meta's segmentation model SAM is an option.

Preview quality, the model cost of a full plan and user preference have not been measured yet.

The client, the run script and the approved thresholds exist, and the exploratory runs are done. The formal evaluation is set up; it needs two more subjects who give permission for model processing, their photos, and garment images that FullAura may use. Photos that the author takes of real garments are images FullAura may use, so the garment images do not depend on a retailer ([D-25](DECISIONS.md#d-25-product-facts-and-product-images-need-different-rights)). T-03 proceeds while the subjects are recruited.

Done when: the thresholds were set before the run, `docs/FEASIBILITY.md` reports every attempt, and the result says whether previews meet [SPEC.md](SPEC.md#previews) or the fallback in [D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them) applies.

## T-03: Catalog and hard constraints

Define a typed product schema and a small catalog that FullAura has permission to use. Implement the hard constraints and locked-item state, including owned items and both budget modes, before any model orchestration. Keep the catalog data under `backend/`, because the container image leaves out `data/`.

The schema, the application state and the hard-constraint checks are in `backend/app/catalog.py`, `state.py` and `constraints.py`, with tests against a synthetic catalog in `tests/fixtures/`. The catalog, `backend/data/catalog.json`, holds the facts of real product variants from Armedangels and Giesswein, since T-08 from GEA Waldviertler, and since T-09 from Tom Tailor and Tchibo ([D-47](DECISIONS.md#d-47-tom-tailor-and-tchibo-supply-affordable-basics-and-closed-shoes)), and no retailer images ([D-26](DECISIONS.md#d-26-the-first-catalog-records-facts-from-armedangels-and-giesswein-without-images), [D-39](DECISIONS.md#d-39-closed-shoes-come-from-gea-waldviertler-with-partial-compositions)); its sources are in the register in [DATA.md](DATA.md). Product facts need a source whose terms of use allow FullAura to record them, and product images need a license, a feed whose terms permit display, or FullAura's own photos ([D-25](DECISIONS.md#d-25-product-facts-and-product-images-need-different-rights)). A test checks that the catalog builds three looks that pass every check, with no exclusion and under a wool exclusion.

Done when: each catalog source is recorded in the register in [DATA.md](DATA.md), and tests cover every hard constraint in [SPEC.md](SPEC.md#hard-constraints), including the cases that fail.

## T-04: One complete flow

Connect preferences, catalog selection, previews and edits into the flow in [SPEC.md](SPEC.md#what-a-person-does).

Planning exists on the backend: `POST /api/plans` takes a brief and the structured state, asks Muse Spark for three looks and returns them only after they pass the look-structure and hard-constraint checks ([D-28](DECISIONS.md#d-28-muse-spark-on-baseten-plans-and-code-checks-every-plan), [D-29](DECISIONS.md#d-29-looks-have-a-complete-base-outfit)). The provider uses one deadline across requests, retries and waits; token usage and HTTP attempts are counted separately ([D-30](DECISIONS.md#d-30-one-deadline-covers-planning-and-http-retries)). The local screen reads the catalog through `GET /api/catalog`, submits preferences and structured constraints, displays checked results, lets a person lock items before an update, and can replace one unlocked item while preserving every other position and rechecking all three looks. It reports planner violations and validation errors instead of dropping them. The screen shows product facts and source links without product photos. The request-scoped preview route accepts two upright JPEGs and a prompt in memory, and the local interface supports selecting photos, cancelling a preview request and displaying its result ([D-31](DECISIONS.md#d-31-no-persistent-server-copies-of-personal-photos), [D-32](DECISIONS.md#d-32-preview-requests-have-one-deadline-and-cancel-on-disconnect)). At the time, `GET /api/previews/status` and `FULLAURA_T02_PREVIEWS_PASSED` kept uploads disabled until T-02 passed; T-09 replaced that gate with the closed beta's experimental try-on ([D-44](DECISIONS.md#d-44-the-betas-try-on-is-experimental-and-does-not-wait-for-t-02)).

The preview request uses one 120-second deadline across upload, provider calls and retry waits. The asynchronous HTTP request is cancelled at the deadline, and a browser disconnect cancels the provider task. Inputs and result buffers are cleared after success, timeout, disconnect or failure. Planning requires a server-recorded 18+ confirmation and permits three planning requests per anonymous browser session; updates and replacements count. The interface explains which preference fields go to Baseten and how photos will be processed if previews are enabled. Production planning remains closed by default, and previews remain disabled pending T-02 ([SPEC.md](SPEC.md#photographs), [public demo access](SPEC.md#public-demo-access)).

Done when: a person can go through the whole flow locally, from inputs to an edited plan. If previews failed the feasibility test, the person can still complete a sourced style plan, and the interface states the visual limitation ([D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them)).

## T-05: Azure demo

Deploy the tested container with secrets kept on the backend only, access controls that fit the pilot, and monitoring that excludes personal inputs. The code required an explicit opt-in flag for public planning and limited each browser session to three requests, which was not an account-wide cost cap; T-09 replaced this with invites and a shared spending ledger ([D-43](DECISIONS.md#d-43-an-invite-only-beta-replaces-the-anonymous-demo)). [DEPLOYMENT.md](DEPLOYMENT.md) has the settings and the steps. Azure Container Apps is the accepted direction ([D-12](DECISIONS.md#d-12-one-azure-container-app-for-the-demo)). Before deploying, validate the region quota, the credit balance and the cost controls, as step 2 of the deployment sequence says.

Done when: the checks in step 5 of the deployment sequence pass, and the URL, source commit, resource names and deployed image digest are recorded in DEPLOYMENT.md. Record a previous working rollback digest when one exists; the first deployment has no earlier working image.

## T-08: Looks for a stated wearer

In the T-06 comparison every plan passed the hard constraints, but plans mixed men's and women's lines and one combined shoes in EU 39 and 42, because nothing said whom the looks were for. The person now states the clothing lines, the kinds of bottoms and one-pieces they wear and their sizes, and code checks them as a hard constraint ([D-36](DECISIONS.md#d-36-the-person-states-whom-the-looks-are-for)). The catalog records each product's line and garment type and a comparable size for each variant. For the demo wearer it gains variants in their sizes and closed wool-free shoes from a source whose terms are recorded in [DATA.md](DATA.md).

The live T-06 run of 2026-10-30 on commit `dd8ba66` meets the second condition below: both approaches passed every hard check in all four cases ([EVALUATION.md](EVALUATION.md#2026-10-30)). The demo wearer's acceptance remains.

Done when: the checks in the [README](../README.md#check-a-change) pass; a new T-06 run on this code, including the example-fit case ([D-40](DECISIONS.md#d-40-the-t-06-comparison-adds-a-case-with-an-example-fit)), records no hard-constraint violation for either approach, with the earlier run kept; and on the deployed site the demo wearer gets three looks for their stated fit, keeps an item, lowers the budget and replaces an unlocked item, every result respects the fit, and the wearer answers yes to both acceptance questions for each of the three looks of the first plan ([D-41](DECISIONS.md#d-41-the-demo-is-accepted-on-its-wearers-yes-or-no-answers-set-before-the-run)).

## T-09: Closed beta

The author and friends use the whole product on the deployed site: invite, preferences, three looks, a try-on on their own photos, keeping and replacing items, a shopping list, saved plans and feedback ([BETA.md](BETA.md), [D-43](DECISIONS.md#d-43-an-invite-only-beta-replaces-the-anonymous-demo) to [D-46](DECISIONS.md#d-46-the-server-keeps-the-checked-plan-and-the-browser-keeps-saved-copies)). Austria, EUR and English stay. Extra costs stay within $25. Until the beta is handed over, only synthetic adult personas are used, and earlier personal photos and results are not. The beta is experimental and does not complete T-02 or show that real people find the product useful; the participants' use answers that.

The catalog covers four example profiles in three scenarios, each with three different complete looks with closed shoes and at least one unlocked position that a valid replacement can fill ([DATA.md](DATA.md#catalog-coverage)).

Withdrawing photo consent stops a try-on in progress and clears its photos and result, and a catalog that fails to load after an accepted invite can be loaded again. A running model call blocks its invite until it ends or is 300 seconds old, whichever revision started it ([D-48](DECISIONS.md#d-48-a-running-call-blocks-its-invite-whichever-process-started-it)). A scheduled job deletes the pilot records on 2026-11-09 from 10:00 Vienna time, after the pilot ends, and the beta closes then ([D-49](DECISIONS.md#d-49-a-scheduled-job-deletes-the-pilot-records-and-the-beta-closes-then)). A closure marker and a grace period keep a write accepted just before then from bringing a record back ([D-50](DECISIONS.md#d-50-a-closure-marker-and-a-grace-period-keep-writes-from-outliving-the-purge)), and the app and the job roll back separately ([D-51](DECISIONS.md#d-51-the-app-and-the-purge-job-have-their-own-images-and-roll-back-apart)).

Done when: the checks in the [README](../README.md#check-a-change) pass, including the browser flow on a desktop and a phone viewport and the catalog coverage test; the error cases pass (incompatible budget, no replacement, timeout, cancellation, a foreign or stale plan token, an exhausted quota, a repeated operation ID, a restart); logs, the pilot store, saved plans and the build hold no photo, try-on, full profile or secret; live runs with three synthetic adult personas pass a preliminary visual check by an independent agent; and one full run on the deployed beta with the real providers and synthetic photos succeeds, with try-on enabled for invited sessions and spending counted across a restart.

## T-10: Beta measurement and participant fixes

Make the closed beta answer what to build next, and remove what participants would trip over. The pilot report counts invites, call outcomes, feedback and spending, each with its denominator, and call records keep the outcome apart from the cost ([D-52](DECISIONS.md#d-52-a-call-record-keeps-its-outcome-apart-from-its-cost-and-the-pilot-report-counts-invites)). Feedback asks what a try-on got wrong ([D-53](DECISIONS.md#d-53-feedback-asks-what-a-try-on-got-wrong-from-a-fixed-list)). The interface says what the budget limits next to the request button ([D-54](DECISIONS.md#d-54-the-interface-says-what-the-budget-limits-next-to-the-request-button)), and the data notice describes what Baseten receives and keeps, and that an owned item's description can be part of a try-on's text. A second closed shoe in EU 40 removes the catalog's single point of failure ([D-55](DECISIONS.md#d-55-a-second-closed-shoe-in-eu-40-keeps-womens-l-from-depending-on-one-pair)), and evaluation runs record their provenance ([D-57](DECISIONS.md#d-57-evaluation-run-records-carry-their-provenance-including-failed-runs)). Keyboard focus no longer falls back to the top of the page after a plan request or a feedback answer. The author accepted guideline thresholds for reading the pilot's results ([D-58](DECISIONS.md#d-58-the-pilots-results-are-read-against-guideline-thresholds)).

The work was deployed on 2026-10-31 as `7e1d48d` and checked on the deployed site ([DEPLOYMENT.md](DEPLOYMENT.md#checks-on-the-deployed-beta)).

Done when: the checks in the [README](../README.md#check-a-change) pass, including the browser flows on a desktop and a phone viewport; the work is deployed as [DEPLOYMENT.md](DEPLOYMENT.md) describes and the deployed site passes the HTTP smoke check, before the final pilot report is read.

## T-11: Product photos

Show a photo of each recommended variant in its color, so people can compare looks in the app ([D-56](DECISIONS.md#d-56-a-variant-can-carry-a-photo-only-with-recorded-rights)). A catalog variant can carry a photo with a description, its size and a credit, and the look cards show it in a box sized before it loads, with a fallback when it fails. No source whose rights allow displaying the photos is recorded yet, so no variant has one and the cards look as before.

Done when: [DATA.md](DATA.md) records the license, feed terms or own photography for the photos of the variants the coverage cases use, those variants carry them, the data notice names the host that serves them, and the look cards show them on the deployed site.

## T-06: Evaluation and submission

The submission deadline is 2026-11-04 21:00 ET, which is 2026-11-05 03:00 in Vienna. The internal target is 2026-11-04 21:00 Vienna time.

Compare the full workflow with a simple baseline on the same cases. Record constraint failures and human judgments separately. Prepare the demo video and the public contribution summary. Three minutes is the working target for the video; check the final format in the submission form.

The fixed cases, deterministic baseline, live comparison metrics and the author's answers for the example-fit case are recorded in [EVALUATION.md](EVALUATION.md). The demo video and submission remain outstanding, so T-06 remains planned.

Done when: the submission is sent and its confirmation is received. Being ready to submit does not finish the task, so record each step on its own line:

- Ready to submit:
- Submitted:
- Confirmation received:

## T-07: Photo of an owned item

An optional photo of an owned item, added after T-02 and T-04 are done ([D-01](DECISIONS.md#d-01-owned-items-are-a-short-optional-text-input)).

Done when: to be defined when the task starts.
