# Tasks

This file lists the work that builds the first version described in [SPEC.md](SPEC.md), in build order. Each task has a number that is never reused, a status (done, next or planned) and a check that says when it is done. When you finish a task, mark it done and add the date in the same commit.

| Task | Status | Finished |
| --- | --- | --- |
| [T-01](#t-01-foundation) Foundation | done | 2026-10-26 |
| [T-02](#t-02-feasibility-test) Feasibility test | next | |
| [T-03](#t-03-catalog-and-hard-constraints) Catalog and hard constraints | done | 2026-10-28 |
| [T-04](#t-04-one-complete-flow) One complete flow | done | 2026-10-29 |
| [T-05](#t-05-azure-demo) Azure demo | done | 2026-10-29 |
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

The schema, the application state and the hard-constraint checks are in `backend/app/catalog.py`, `state.py` and `constraints.py`, with tests against a synthetic catalog in `tests/fixtures/`. The catalog, `backend/data/catalog.json`, holds the facts of 15 real product variants from Armedangels and Giesswein and no retailer images ([D-26](DECISIONS.md#d-26-the-first-catalog-records-facts-from-armedangels-and-giesswein-without-images)); its sources are in the register in [DATA.md](DATA.md). Product facts need a source whose terms of use allow FullAura to record them, and product images need a license, a feed whose terms permit display, or FullAura's own photos ([D-25](DECISIONS.md#d-25-product-facts-and-product-images-need-different-rights)). A test checks that the catalog builds three looks that pass every check, with no exclusion and under a wool exclusion.

Done when: each catalog source is recorded in the register in [DATA.md](DATA.md), and tests cover every hard constraint in [SPEC.md](SPEC.md#hard-constraints), including the cases that fail.

## T-04: One complete flow

Connect preferences, catalog selection, previews and edits into the flow in [SPEC.md](SPEC.md#what-a-person-does).

Planning exists on the backend: `POST /api/plans` takes a brief and the structured state, asks Muse Spark for three looks and returns them only after they pass the look-structure and hard-constraint checks ([D-28](DECISIONS.md#d-28-muse-spark-on-baseten-plans-and-code-checks-every-plan), [D-29](DECISIONS.md#d-29-looks-have-a-complete-base-outfit)). The provider uses one deadline across requests, retries and waits; token usage and HTTP attempts are counted separately ([D-30](DECISIONS.md#d-30-one-deadline-covers-planning-and-http-retries)). The local screen reads the catalog through `GET /api/catalog`, submits preferences and structured constraints, displays checked results, lets a person lock items before an update, and can replace one unlocked item while preserving every other position and rechecking all three looks. It reports planner violations and validation errors instead of dropping them. The screen shows product facts and source links without product photos. The request-scoped preview route accepts two upright JPEGs and a prompt in memory, and the local interface supports selecting photos, cancelling a preview request and displaying its result ([D-31](DECISIONS.md#d-31-no-persistent-server-copies-of-personal-photos), [D-32](DECISIONS.md#d-32-preview-requests-have-one-deadline-and-cancel-on-disconnect)). `GET /api/previews/status` and `FULLAURA_T02_PREVIEWS_PASSED` keep uploads disabled until T-02 passes; production remains disabled.

The preview request uses one 120-second deadline across upload, provider calls and retry waits. The asynchronous HTTP request is cancelled at the deadline, and a browser disconnect cancels the provider task. Inputs and result buffers are cleared after success, timeout, disconnect or failure. Planning requires a server-recorded 18+ confirmation and permits three planning requests per anonymous browser session; updates and replacements count. The interface explains which preference fields go to Baseten and how photos will be processed if previews are enabled. Production planning remains closed by default, and previews remain disabled pending T-02 ([SPEC.md](SPEC.md#photographs), [public demo access](SPEC.md#public-demo-access)).

Done when: a person can go through the whole flow locally, from inputs to an edited plan. If previews failed the feasibility test, the person can still complete a sourced style plan, and the interface states the visual limitation ([D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them)).

## T-05: Azure demo

Deploy the tested container with secrets kept on the backend only, access controls that fit the pilot, and monitoring that excludes personal inputs. The code requires an explicit opt-in flag for public planning and limits each browser session to three requests. This does not provide an account-wide cost cap. [DEPLOYMENT.md](DEPLOYMENT.md) has the settings and the steps. Azure Container Apps is the accepted direction ([D-12](DECISIONS.md#d-12-one-azure-container-app-for-the-demo)). Before deploying, validate the region quota, the credit balance and the cost controls, as step 2 of the deployment sequence says.

Done when: the checks in step 5 of the deployment sequence pass, and the URL, source commit, resource names and deployed image digest are recorded in DEPLOYMENT.md. Record a previous working rollback digest when one exists; the first deployment has no earlier working image.

## T-06: Evaluation and submission

The submission deadline is 2026-11-04 21:00 ET, which is 2026-11-05 03:00 in Vienna. The internal target is 2026-11-04 21:00 Vienna time.

Compare the full workflow with a simple baseline on the same cases. Record constraint failures and human judgments separately. Prepare the demo video and the public contribution summary. Three minutes is the working target for the video; check the final format in the submission form.

The fixed cases, deterministic baseline and live comparison metrics are recorded in [EVALUATION.md](EVALUATION.md). Human scoring, the demo video and submission remain outstanding, so T-06 remains planned.

Done when: the submission is sent and its confirmation is received. Being ready to submit does not finish the task, so record each step on its own line:

- Ready to submit:
- Submitted:
- Confirmation received:

## T-07: Photo of an owned item

An optional photo of an owned item, added after T-02 and T-04 are done ([D-01](DECISIONS.md#d-01-owned-items-are-a-short-optional-text-input)).

Done when: to be defined when the task starts.
