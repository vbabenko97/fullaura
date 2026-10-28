# FullAura

[![CI](https://github.com/vbabenko97/fullaura/actions/workflows/ci.yml/badge.svg)](https://github.com/vbabenko97/fullaura/actions/workflows/ci.yml)

Personal style plans built around your preferences, budget, and existing wardrobe.

FullAura aims to combine outfits, optional hair and beard previews, and links to real products. A plan should remain consistent when you keep an item, change a preference, or lower the budget.

**Status: initial scaffold.** The repository contains a React landing page, a FastAPI server, runtime tests, and a Docker build. A Muse Image client and a feasibility test script exist. Exploratory runs used the author's photos; the formal evaluation is pending. A first catalog of 15 real products, with their facts and no images, and the code that checks the hard constraints exist. A development-only endpoint asks Muse Spark for three looks and checks them; the interface does not use it yet. Photo uploads, product selection, and personalized previews are planned. There is no hosted product yet.

## Run locally

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.4, and Node.js 24 with npm. Dependencies are recorded in `uv.lock` and `frontend/package-lock.json`.

```bash
git clone https://github.com/vbabenko97/fullaura.git
cd fullaura
cp .env.example .env
uv sync --frozen
uv run --env-file .env uvicorn backend.app.main:app --reload
```

In a second terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. Vite forwards `/api` requests to the backend at port 8000. Development API docs are at <http://localhost:8000/api/docs>.

In development, the server's planning endpoint, `POST /api/plans`, asks Muse Spark Open on Baseten for three looks from the catalog and checks them against the hard constraints. It needs `BASETEN_API_KEY` in `.env`, which Git ignores, and the production container leaves it switched off. The rest of the web app needs no cloud credentials. Two scripts also call models, with keys from `.env`. The feasibility test script ([docs/FEASIBILITY.md](docs/FEASIBILITY.md)) calls Meta with `MODEL_API_KEY`. The model smoke check makes one small call to Muse Spark Open on Baseten, with `BASETEN_API_KEY`, and one to Muse Image on Meta, and prints each call's status, latency and token usage:

```bash
uv run --frozen --env-file .env python -m scripts.smoke_models
```

## Check a change

From the repository root:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
npm --prefix frontend ci
npm --prefix frontend run build
```

CI runs these checks, builds the container, and checks its HTTP endpoints. It does not deploy or call Meta.

## Run the production container

```bash
docker build -t fullaura:local .
docker run --rm -p 127.0.0.1:8000:8000 fullaura:local
```

Open <http://localhost:8000>. The container serves both the built frontend and API, runs as a non-root user, and disables development API docs. `GET /api/health` checks the process; `GET /api/ready` also checks that the frontend entry point exists. Neither endpoint verifies a model provider.

While the container is running, check it from another terminal:

```bash
uv run --frozen python scripts/smoke_http.py
```

## Continue development

- [Product specification](docs/SPEC.md)
- [Tasks in build order](docs/TASKS.md)
- [Decision log](docs/DECISIONS.md)
- [Azure deployment plan](docs/DEPLOYMENT.md)
- [Data and asset provenance](docs/DATA.md)
- [Contributor and coding-agent guidance](AGENTS.md)

Development began on 26 October 2026 for the Meta Global AI Developer Hackathon, under the name LookPilot; the project became FullAura on 27 October 2026 ([D-19](docs/DECISIONS.md#d-19-the-product-is-named-fullaura)). The intended track is Indie Developer, under Build for Your World. Commit history and the final demo must identify the work built during the event.

Source code is available under the [MIT license](LICENSE). Third-party models, product data, and photographs retain their own terms.
