# Product scope

## The first complete experience

A person supplies photographs, a style goal, preferred colors, a budget, existing items, and materials to exclude. LookPilot proposes three coordinated looks using a small, sourced product catalog. The person can keep an item or change a constraint, then receive an updated plan with previews and shopping links.

Initial assumptions: English interface, EUR prices, and products that can be delivered to Austria. Hair and beard choices are optional. These assumptions can change after the first user test.

## MVP boundary

| Area | Planned first version |
| --- | --- |
| Inputs | Portrait and full-body photo; goal, colors, budget, material exclusions |
| Products | Small permitted catalog with SKU, variant, price, composition, link, and verification time |
| Planning | Three looks; deterministic checks for budget, exclusions, and locked items |
| Personalization | Muse Spark for planning/tool use; Muse Image for visual previews, subject to feasibility testing |
| Editing | Replace one item or lower the budget while preserving explicit locks |
| Hair and beard | One or two optional previews; a reference card for a barber if quality permits |
| Output | Preview, sourced shopping list, costs, and explicit unknowns |

Unknown composition cannot satisfy a hard material exclusion. Product availability must distinguish delivery to a country from stock in a local shop. Generated images cannot establish fit, measurements, composition, price, or availability.

Full wardrobe ingestion, city-wide stock search, payments, booking, social features, and multiple agent services are outside the first version. SAM is optional if testing identifies a need for segmentation.

## Build order

1. **Foundation — implemented.** React/FastAPI scaffold, dependency locks, health/readiness endpoints, Dockerfile, tests, and CI.
2. **Meta feasibility — next.** With permitted photos and a few specific garments, test identity preservation, garment fidelity, and one targeted edit. Record model IDs, prompts, retries, latency, and usage. Keep personal results out of the public repository.
3. **Catalog and constraints.** Define a typed product schema and a small permitted catalog. Implement hard constraints and locked-item state before model orchestration.
4. **One complete flow.** Connect preferences, catalog selection, previews, and edits. Add per-session request/image limits and a provider timeout path before exposing model calls publicly.
5. **Azure demo.** Deploy the tested container with backend-only secrets, access controls appropriate to the pilot, and monitoring that excludes personal inputs.
6. **Evaluation and submission.** Compare the full workflow with a simple baseline on the same cases. Record constraint failures and human judgments separately. Prepare the three-minute demo and public contribution summary.

## Feasibility decision

Before expanding the UI, test several consenting subjects and multiple outfits. Check facial identity, body proportions, recognizable product details, and whether editing one item preserves the rest. Review outputs manually; model self-evaluation alone is insufficient. Define acceptance thresholds before the run and report all attempts.

If previews cannot preserve identity or product details reliably, narrow the promised visual behavior and keep the sourced style plan useful on its own. No preview quality, model cost, or user-preference result has been measured yet.
