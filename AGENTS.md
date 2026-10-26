# Working on LookPilot

Read `README.md` and `docs/SPEC.md` before changing product behavior. `docs/TASKS.md` lists the work in build order, and `docs/DECISIONS.md` records why each choice was made. Keep implemented features separate from proposed work in documentation and the UI.

## Implementation

- Backend: Python 3.12 and FastAPI. Frontend: React, TypeScript, and Vite on Node 24.
- Use type hints, validated boundaries, and small dependencies. Keep `uv.lock` and `frontend/package-lock.json` in sync with dependency changes.
- Start with a single backend and one frontend. Add a database, job queue, or extra model when a working use case requires it.
- Keep model calls on the server. Add explicit timeouts, bounded retries, and usage accounting when implementing the provider adapter.
- Follow the product rules in `docs/SPEC.md`: hard constraints validated in code against structured state, recommendations only from catalog records, previews labeled as previews, and no inference of sensitive traits from photographs.

## Data and credentials

- Never commit `.env`, API keys, personal photographs, generated personal previews, or raw provider payloads. Keep local private inputs under ignored `data/private/`.
- Use Meta Standard mode for personal photographs. Confirm the current provider terms and exact model IDs before integration; do not use Contributor mode, where Meta may train on the content, with personal data.
- Do not log image bytes, image URLs containing access tokens, or full user profiles. Future upload/storage work must include retention and deletion behavior.
- Record rights and source information for every public demo asset in `docs/DATA.md`.

## Files and folders

Put each file where its kind of content already lives, and name it like its neighbors. New folders are lowercase; Python packages use snake_case.

```text
backend/app/        FastAPI application: snake_case.py modules
frontend/src/       React app: PascalCase.tsx components, camelCase.ts other modules
tests/              pytest tests: test_<subject>.py
scripts/            command-line helpers for people and CI: snake_case.py
docs/               project documents: UPPERCASE.md
.github/workflows/  CI
data/private/       ignored by Git: personal photos, previews, provider payloads, model test runs
tmp/                ignored by Git and Docker: agents' working files and notes
```

- Keep the repository root for tooling and top-level documents. To add a root entry, add it to `ROOT_ENTRIES` in `tests/test_repo_layout.py` in the same commit and give the reason in the commit message.
- Write your own working output (session plans, progress reports, summaries, notes, scratch scripts, logs and command output) to `tmp/` or outside the repository, such as the system temp directory or your scratchpad. Notes that must survive between sessions go in `tmp/<topic>/`; `tmp/` exists only on the local machine and is never committed. Project documents that a task asks for, such as `docs/FEASIBILITY.md`, go in `docs/`.
- Do not keep copies or old versions of files (`*.bak`, `*.old`, `*_old.*`, `*_backup.*`, `App 2.tsx`); Git keeps the history.
- Add a file to `docs/` only for a new topic; otherwise extend the document that covers it.
- Generated files go to ignored folders: build output to `frontend/dist/`, model test runs with personal data to `data/private/`, other run output to `runs/`.
- `tests/test_repo_layout.py` checks the root entries; copies, scratch files (`scratch*`, `tmp*`, `temp*`, `debug*`) and session reports (`*SUMMARY*.md`, `*REPORT*.md`, `PLAN*.md`, `*NOTES*.md`, `TODO*.md`) anywhere outside `.github/`; and the names of Markdown files in `docs/`. It is narrow on purpose and does not check code naming. `uv run --frozen pytest` runs it with the other tests.

## Verification and handoff

- Run the relevant checks from `README.md`. For backend or Docker changes, include the production HTTP smoke check where Docker is available.
- Add tests for meaningful failure cases and constraints. Avoid tests that only repeat implementation details.
- Inspect the diff for secrets and unintended files before committing.
- When a task is finished, mark it done in `docs/TASKS.md` in the same commit. Record a new or changed decision as a new entry in `docs/DECISIONS.md`, and update `docs/SPEC.md` when product behavior changes. State any checks you could not run.
- Preserve the hackathon contribution history. Do not present pre-existing code, cached model output, or mocked services as newly built live behavior.
