# Planning evaluation

The T-06 protocol compares Muse Spark's generated and code-checked plans with a deterministic baseline on the same fixed, non-personal cases. The live comparison ran once on 2026-10-29 against code commit `2ade8433a27b8dd6c6873a765aedcb805d467b3d`. Human scoring, demo video and submission remain outstanding.

## Cases

The runner defines three requests in `scripts/evaluate_planner.py` and loads the same 15-record real catalog used by the application.

| Case | Request | Budget | Exclusion |
| --- | --- | --- | --- |
| `wool-free-per-look` | Quiet smart casual for dinners; black, grey and earth tones | €180 per look | Wool |
| `open-materials-per-look` | Relaxed casual for mild spring weather; black, blue and red | €250 per look | None |
| `wool-free-whole-plan` | Minimal travel capsule with varied outfits; black, grey and neutral | €220 for the whole plan | Wool |

These cases contain no personal data. They test both budget modes and a material exclusion. The styles and colors are inputs to human scoring; code does not treat them as hard constraints.

## Baseline

For each case, the baseline uses the cheapest offer for each catalog variant that passes the same item eligibility checks as the planner. It enumerates outfits with a top and bottom, or a one-piece, plus footwear. It selects the three cheapest distinct outfits whose complete plan passes `check_plan`. Per-look cases minimize the sum of the three look prices; whole-plan cases minimize the price of the distinct catalog items across all three looks. Stable item-ID ordering breaks price ties.

The baseline makes no model calls. Its rule is intentionally narrow: it does not rank style, color coordination, or outfit variety beyond requiring three different item combinations.

The offline run on catalog `fullaura-catalog-2026-10-28` selected a plan that passed `check_plan` in all cases:

| Case | Baseline catalog price | Budget result |
| --- | ---: | --- |
| `wool-free-per-look` | €414.25 across three looks | Passes €180 per look |
| `open-materials-per-look` | €414.25 across three looks | Passes €250 per look |
| `wool-free-whole-plan` | €219.55 across distinct items | Passes €220 for the whole plan |

These amounts describe selected catalog products, not Baseten usage or remaining hackathon credit.

## Live comparison

One sequential run used `meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1` on code commit `2ade8433a27b8dd6c6873a765aedcb805d467b3d`. Each case returned a checked plan on its first proposal. The run used 3 of the 12 allowed HTTP attempts and wrote three raw responses to the ignored `runs/t06/20261029T183234323281Z/provider_responses.jsonl` file.

| Case | Baseline price | Muse Spark price | Rejected proposals / hard violations | Final violations | Calls / HTTP | Input / output tokens | Latency | Estimated API cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `wool-free-per-look` | €414.25 | €499.45 | 0 / 0 | 0 | 1 / 1 | 1,165 / 2,397 | 7.753 s | $0.011644 |
| `open-materials-per-look` | €414.25 | €694.25 | 0 / 0 | 0 | 1 / 1 | 1,417 / 1,917 | 5.779 s | $0.009919 |
| `wool-free-whole-plan` | €219.55 | €219.55 | 0 / 0 | 0 | 1 / 1 | 1,165 / 3,371 | 14.153 s | $0.015783 |

All three model plans passed hard checks and contained three distinct looks. Across the run, Muse Spark used 3,747 input tokens, 7,685 output tokens and 3 HTTP attempts, with 27.685 seconds of summed planner latency. The estimated cost from total reported tokens is $0.037345, assuming all input tokens use the uncached rate; the raw responses report 384 cached input tokens, so this estimate does not apply Baseten's cache discount. It also cannot account for any billable timed-out request without returned usage.

The model-selected catalog price exceeded the baseline in two cases and matched it in the whole-plan case. This is a comparison with a price heuristic on these three fixed cases. No human judgments were recorded, and the results do not establish general planning quality or user preference.

The two per-look baseline plans contain the same three looks: none of the cheapest items contains wool, so the wool exclusion does not change the baseline's selection. In `wool-free-whole-plan`, the baseline and Muse Spark plans use the same five catalog items, which cost €219.55 against the €220 budget, and differ only in how they combine them. Human scores for that case can therefore reflect only the combinations.

## Human scoring

Human scores for the 2026-10-29 run go on a blind sheet in the ignored `runs/t06/20261029T183234323281Z-blind/` folder; the runner does not create this sheet. `blind_review.md` lists each case's two plans as options A and B, with item names, catalog colors and retailer links, and `blind_scores.csv` takes the four scores and a note for each option, using the column names of `human_scores.csv`. The sheet hides prices and approach names, and the A/B assignment and the look order are shuffled for each case. The key, `blind_key.json`, stays in the run folder, where `run.json` and `human_scores.csv` also reveal which option is which. After scoring, copy each row into `human_scores.csv` using the key.

Scores are blind only when the rater does not know which option is which. A rater who has seen the key or been told the mapping gives an open assessment, and the results must say so. For this run, the project author has seen the mapping, so the author's scores are an open assessment. The sheet hides the labels but not the baseline's behavior: the per-look baseline repeats its looks across two cases, the sheet has no product images, and the retailer pages that show the items also show their prices. A rater who knows the baseline rule may recognize the baseline from these cues, so record with the scores whether the rater knew the rule.

## Measures

The runner records machine-checked results separately from human judgments:

- Hard constraints: final pass/fail and final violation count.
- Repair behavior: rejected proposals, format failures, and rejected hard-constraint violations grouped by rule. A repaired first proposal remains in these counts even when the final plan passes.
- Provider usage: completed calls, HTTP attempts, prompt and completion tokens, and elapsed time.
- Model API cost estimate: reported prompt tokens at $1.25 per million uncached input tokens and completion tokens at $4.25 per million output tokens, using [Baseten's published rates](https://www.baseten.co/pricing/) checked 2026-10-29. The estimate does not discount cached input or account for a timed-out request whose usage was not returned.
- Catalog price: the selected plan's EUR cents under the case's budget mode. This is product price, not model usage cost.
- Human judgment: style relevance, color coherence, visual variety and wearability, each scored from 1 (poor), 3 (adequate) or 5 (strong); use 2 and 4 between those anchors. Add notes where a score needs explanation. These scores do not change the hard-constraint result.

The planner records aggregate proposal counters and logs them. The counters do not include the brief or raw provider response. Live runs write raw HTTP response bodies to `provider_responses.jsonl` under the ignored run folder; the PR contains only validated item IDs and aggregate metrics. HTTP error bodies redact the API key if it appears in a response.

## Run

An offline run is the default and creates the baseline plans and a blank human-score sheet under ignored `runs/t06/`:

```bash
uv run --frozen python -m scripts.evaluate_planner
```

A live run calls Baseten's exact hackathon model. The three-case run is capped at 12 HTTP attempts: up to two model generations per case, with up to two HTTP attempts per generation. At 6,000 output tokens per attempt, 72,000 output tokens would cost about $0.306 at the listed output rate, plus input tokens. This is an estimate for the configured token limits, not a complete cost ceiling.

```bash
uv run --frozen --env-file .env python -m scripts.evaluate_planner \
  --live --case-id wool-free-per-look
```

The live runner uses `BASETEN_API_KEY` from the environment and enforces `meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1`. `--live` without at least one `--case-id` is rejected. The PR records aggregate run results; raw provider responses remain in the ignored run folder.

Each run writes `run.json` and `human_scores.csv` under ignored `runs/t06/`. A live run also writes `provider_responses.jsonl`. The run manifest records the case inputs, model endpoint and ID, catalog and source-code hashes, attempt ceiling, token rates and Git revision. Review the automated outcome first; score only plans that were produced. Keep model and baseline scores paired by case. A small catalog and three fixed cases support a pilot comparison only; they do not establish general quality, fit, stock availability or broad user preference.
