# FullAura

[![CI](https://github.com/vbabenko97/fullaura/actions/workflows/ci.yml/badge.svg)](https://github.com/vbabenko97/fullaura/actions/workflows/ci.yml)

Personal style plans built around your preferences, budget, and existing wardrobe.

FullAura aims to combine outfits, optional hair and beard previews, and links to real products. A plan should remain consistent when you keep an item, change a preference, or lower the budget.

**Status: closed beta.** The [Azure site](https://fullaura-demo.blackbay-aaeee2c5.francecentral.azurecontainerapps.io) serves invited people only ([docs/BETA.md](docs/BETA.md)). An invited person confirms they are 18 or older, states a style goal, budget, shoes, sizes and optional wishes, and gets three looks from a catalog of real products that ship to Austria, checked in code against the hard constraints. They can try a look on their own photos as an experimental visualization, keep items, lower the budget, replace one item, copy or print a shared shopping list, save plans on their device, restore them, and send feedback. Each invite has 8 planning requests and 12 try-ons, and a shared ledger stops model calls when the beta's $8 planning or $3 image budget is used up. Model calls close at the end of the pilot, on 6 November 2026 at 23:59 Vienna time; on 9 November at 10:00 a scheduled job deletes the pilot's records and the beta closes. The try-on has not passed the formal feasibility test (T-02), and hair and beard previews are not implemented.

## Run locally

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.4, and Node.js 24 with npm. Dependencies are recorded in `uv.lock` and `frontend/package-lock.json`.

```bash
git clone https://github.com/vbabenko97/fullaura.git
cd fullaura
cp .env.example .env
uv sync --frozen
uv run --frozen python -m scripts.pilot_invites --count 1 --base-url http://localhost:5173 \
    --folder data/private/beta-dev
```

Put the digest from `data/private/beta-dev/invite-hashes.txt` into `.env` as `FULLAURA_INVITE_HASHES`, then start the backend:

```bash
uv run --env-file .env uvicorn backend.app.main:app --reload
```

In a second terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev
```

Open the link in `data/private/beta-dev/invites.csv`. Vite forwards `/api` requests to the backend at port 8000. Development API docs are at <http://localhost:8000/api/docs>.

Development always opens the beta; production opens it only with `FULLAURA_BETA_ENABLED=true`. Without `FULLAURA_STORE_URL`, development keeps quotas, spending and feedback in memory; `file:///absolute/folder` keeps them across restarts. Without `FULLAURA_SESSION_SECRET`, each start signs cookies with a new secret, so the invite link must be opened again.

Planning needs `BASETEN_API_KEY` in `.env`, which Git ignores, and the try-on needs Meta Standard-mode access through `MODEL_API_KEY`. Both calls cost money and count against the invite's quota and the beta's budget. The server asks Muse Spark Open on Baseten for three looks and checks them against the hard constraints; it writes the try-on prompt from the checked look, and the browser sends only two photos, redrawn as JPEGs without metadata. Loading the interface and catalog needs no cloud credentials. Two scripts also call models, with keys from `.env`: the feasibility test script ([docs/FEASIBILITY.md](docs/FEASIBILITY.md)) calls Meta, and the model smoke check makes one small call to each model and prints its status, latency and token usage:

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
npm --prefix frontend test
npm --prefix frontend run build
npx --prefix frontend playwright install chromium   # once
npm --prefix frontend run e2e
```

CI runs these checks, builds the container, and checks its HTTP endpoints. It does not deploy or call a model provider.

Frontend unit tests drive the interface against a scripted API with the checked-in catalog. `npm run e2e` serves the built app with `scripts/e2e_server.py`, whose planner and image model are deterministic stand-ins, and drives the whole beta flow on a desktop and a phone viewport: invite, plan, try-on, keep, lower budget, update, replace, save, restore, copy, print and feedback. A second test uses the budget and feedback controls with the keyboard alone and gives the catalog invented product photos from `synthetic.example`, some of which fail to load. It writes screenshots to the ignored `runs/e2e/screens/`. Nothing it shows is model output.

## Run the production container

```bash
docker build -t fullaura:local .
docker run --rm -p 127.0.0.1:8000:8000 fullaura:local
```

Open <http://localhost:8000>. The container serves both the built frontend and API, runs as a non-root user, disables development API docs, and keeps the beta closed. With `FULLAURA_BETA_ENABLED=true` it also needs `FULLAURA_SESSION_SECRET` of 32 or more characters, `FULLAURA_INVITE_HASHES`, a persistent `FULLAURA_STORE_URL`, `FULLAURA_PILOT_ENDS_AT` and a later `FULLAURA_RECORDS_PURGE_AT`, or it refuses to start. The same image deletes the pilot records with `python -m backend.app.purge`, which the deployment runs as a scheduled job ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#deleting-the-pilot-records)). `GET /api/health` checks the process; `GET /api/ready` also checks that the frontend entry point exists. Neither endpoint verifies a model provider.

While the container is running, check it from another terminal:

```bash
uv run --frozen python scripts/smoke_http.py
```

## Continue development

- [Product specification](docs/SPEC.md)
- [Tasks in build order](docs/TASKS.md)
- [Decision log](docs/DECISIONS.md)
- [Closed beta for participants and the operator](docs/BETA.md)
- [Azure deployment](docs/DEPLOYMENT.md)
- [Data and asset provenance](docs/DATA.md)
- [Planning evaluation protocol](docs/EVALUATION.md)
- [Contributor and coding-agent guidance](AGENTS.md)

Development began on 26 October 2026 for the Meta Global AI Developer Hackathon, under the name LookPilot; the project became FullAura on 27 October 2026 ([D-19](docs/DECISIONS.md#d-19-the-product-is-named-fullaura)). The intended track is Indie Developer, under Build for Your World. Commit history and the final demo must identify the work built during the event.

Source code is available under the [MIT license](LICENSE). Third-party models, product data, and photographs retain their own terms.
