# Working on LookPilot

Read `README.md` and `docs/SCOPE.md` before changing product behavior. Keep implemented features separate from proposed work in documentation and the UI.

## Implementation

- Backend: Python 3.12 and FastAPI. Frontend: React, TypeScript, and Vite on Node 24.
- Use type hints, validated boundaries, and small dependencies. Keep `uv.lock` and `frontend/package-lock.json` in sync with dependency changes.
- Start with a single backend and one frontend. Add a database, job queue, or extra model when a working use case requires it.
- Keep model calls on the server. Add explicit timeouts, bounded retries, and usage accounting when implementing the provider adapter.
- Keep selected SKU IDs, budget, exclusions, and locked items in structured application state. Validate hard constraints in code.
- Select products from sourced catalog records before generating a preview. A generated garment is not evidence that an item is sold or available.
- Label visualizations as previews; do not promise exact clothing fit or achievable hair density. Material exclusions are shopping filters, not medical assurances.
- Do not infer health, ethnicity, personality, or other sensitive traits from photographs.

## Data and credentials

- Never commit `.env`, API keys, personal photographs, generated personal previews, or raw provider payloads. Keep local private inputs under ignored `data/private/`.
- Use Meta Standard mode for personal photographs. Confirm the current provider terms and exact model IDs before integration; do not use Contributor mode with personal data.
- Do not log image bytes, image URLs containing access tokens, or full user profiles. Future upload/storage work must include retention and deletion behavior.
- Record rights and source information for every public demo asset in `docs/DATA.md`.

## Verification and handoff

- Run the relevant checks from `README.md`. For backend or Docker changes, include the production HTTP smoke check where Docker is available.
- Add tests for meaningful failure cases and constraints. Avoid tests that only repeat implementation details.
- Inspect the diff for secrets and unintended files before committing.
- Update `docs/SCOPE.md` when a task is completed or a product decision changes. State any checks you could not run.
- Preserve the hackathon contribution history. Do not present pre-existing code, cached model output, or mocked services as newly built live behavior.
