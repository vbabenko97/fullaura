# Decisions

This file records decisions about LookPilot's product and code, with the reason for each, so that a settled point is not reopened by accident. [SPEC.md](SPEC.md) describes what the product does, and [AGENTS.md](../AGENTS.md) holds the working rules for coding agents; this file says why.

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
