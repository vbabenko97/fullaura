# Planning evaluation

The T-06 protocol compares Muse Spark's generated and code-checked plans with a deterministic baseline on the same fixed, non-personal cases. Live comparisons ran on 2026-10-29 against code commit `2ade8433a27b8dd6c6873a765aedcb805d467b3d` and on 2026-10-30 against `dd8ba6689b46d37da16269d52e4fd21f859067e4`, which includes the fit constraint and the example-fit case. The demo video and submission remain outstanding.

## Cases

The runner defines four requests in `scripts/evaluate_planner.py` and loads the real catalog used by the application. The live comparison of 2026-10-29 ran the first three, and the run of 2026-10-30 ran all four.

| Case | Request | Budget | Exclusion | Fit |
| --- | --- | --- | --- | --- |
| `wool-free-per-look` | Quiet smart casual for dinners; black, grey and earth tones | €180 per look | Wool | None |
| `open-materials-per-look` | Relaxed casual for mild spring weather; black, blue and red | €250 per look | None | None |
| `wool-free-whole-plan` | Minimal travel capsule with varied outfits; black, grey and neutral | €220 for the whole plan | Wool | None |
| `mens-fit-per-look` | Smart casual for autumn office days; navy, grey and black | €250 per look | None | Men's line; trousers or jeans; size L, waist and length 32/32, EU shoe 42 or 43 |

These cases contain no personal data. They test both budget modes, a material exclusion and a stated fit ([D-40](DECISIONS.md#d-40-the-t-06-comparison-adds-a-case-with-an-example-fit)). The fit in `mens-fit-per-look` is an example, not a real person's. That case has no exclusion, so the closed GEA shoes, which drop out under any exclusion, pass its item checks. The first three cases state no fit and check for regressions. The styles and colors are inputs to human scoring; code does not treat them as hard constraints.

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

### 2026-10-29

One sequential run used `meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1` on code commit `2ade8433a27b8dd6c6873a765aedcb805d467b3d`. Each case returned a checked plan on its first proposal. The run used 3 of the 12 allowed HTTP attempts and wrote three raw responses to the ignored `runs/t06/20261029T183234323281Z/provider_responses.jsonl` file.

| Case | Baseline price | Muse Spark price | Rejected proposals / hard violations | Final violations | Calls / HTTP | Input / output tokens | Latency | Estimated API cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `wool-free-per-look` | €414.25 | €499.45 | 0 / 0 | 0 | 1 / 1 | 1,165 / 2,397 | 7.753 s | $0.011644 |
| `open-materials-per-look` | €414.25 | €694.25 | 0 / 0 | 0 | 1 / 1 | 1,417 / 1,917 | 5.779 s | $0.009919 |
| `wool-free-whole-plan` | €219.55 | €219.55 | 0 / 0 | 0 | 1 / 1 | 1,165 / 3,371 | 14.153 s | $0.015783 |

All three model plans passed hard checks and contained three distinct looks. Across the run, Muse Spark used 3,747 input tokens, 7,685 output tokens and 3 HTTP attempts, with 27.685 seconds of summed planner latency. The estimated cost from total reported tokens is $0.037345, assuming all input tokens use the uncached rate; the raw responses report 384 cached input tokens, so this estimate does not apply Baseten's cache discount. It also cannot account for any billable timed-out request without returned usage.

The model-selected catalog price exceeded the baseline in two cases and matched it in the whole-plan case. This is a comparison with a price heuristic on these three fixed cases. No human judgments were recorded, and the results do not establish general planning quality or user preference.

The two per-look baseline plans contain the same three looks: none of the cheapest items contains wool, so the wool exclusion does not change the baseline's selection. In `wool-free-whole-plan`, the baseline and Muse Spark plans use the same five catalog items, which cost €219.55 against the €220 budget, and differ only in how they combine them. Human scores for that case can therefore reflect only the combinations.

### 2026-10-30

One sequential run on code commit `dd8ba6689b46d37da16269d52e4fd21f859067e4` and catalog `fullaura-catalog-2026-10-30` ran all four cases with the same model and endpoint. Each case returned a checked plan on its first proposal. The run used 4 of the 12 allowed HTTP attempts and wrote four raw responses to the ignored `runs/t06/20261030T174450639792Z/provider_responses.jsonl` file.

| Case | Baseline price | Muse Spark price | Rejected proposals / hard violations | Final violations | Calls / HTTP | Input / output tokens | Latency | Estimated API cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `wool-free-per-look` | €404.25 | €469.45 | 0 / 0 | 0 | 1 / 1 | 1,407 / 3,974 | 13.982 s | $0.018648 |
| `open-materials-per-look` | €404.25 | €654.35 | 0 / 0 | 0 | 1 / 1 | 1,840 / 1,322 | 4.351 s | $0.007919 |
| `wool-free-whole-plan` | €209.55 | €219.55 | 0 / 0 | 0 | 1 / 1 | 1,407 / 2,213 | 9.569 s | $0.011164 |
| `mens-fit-per-look` | €649.25 | €679.20 | 0 / 0 | 0 | 1 / 1 | 1,155 / 3,194 | 10.479 s | $0.015018 |

Both approaches passed the hard checks in all four cases, and every plan contained three distinct looks. Across the run, Muse Spark used 5,809 input tokens, 10,703 output tokens and 4 HTTP attempts, with 38.381 seconds of summed planner latency. The estimated cost from total reported tokens is $0.052749, under the same assumptions as the earlier run; the raw responses report 384 cached input tokens. The catalog gained variants between the runs, so prices differ from the 2026-10-29 run. The two per-look baseline plans without a fit again contain the same three looks.

In `mens-fit-per-look`, every item in both plans is from the men's line in size L or 32/32, or is the unisex Beach slide in EU 42. Both plans use Beach in every look that has footwear. Three closed shoes in EU 43 pass the item checks in this case, Giesswein Merino Runners at €149.95 and the two GEA shoes at €175.00 and €235.00, but the cheapest top and bottom that pass its fit cost €139.80 together, so a look with closed shoes costs at least €289.75 against €250. The only outer layer that passes its fit, an overshirt at €159.90, brings the cheapest top and bottom to €299.70. The Muse Spark look with a sweater over a T-shirt costs €229.70 and would cost €289.65 with Beach, so it has no shoes.

The three cases without a fit mix lines and sizes, as in the first run. For example, the baseline in `wool-free-per-look` uses the same black top in sizes M and L in different looks, and the Muse Spark plan in `open-materials-per-look` combines men's jeans, a women's dress, women's shoes in EU 39 and the Beach slide in EU 42. These cases check for regressions, not fit. No human judgments were recorded for this run.

## Human scoring

A person judges only `mens-fit-per-look`, the one case whose looks are for one stated fit ([D-42](DECISIONS.md#d-42-t-06-compares-with-the-cheapest-outfit-baseline-and-judges-only-the-fit-case)). For each look of both plans, the rater answers yes or no to two questions, as the person who made the request: "Is this what I asked for?" and "Would I go and try this on?" The cases without a fit check hard constraints only. The 2026-10-29 run has no fit case and gets no human answers; the four 1–5 scales on its blind sheet in `runs/t06/20261029T183234323281Z-blind/` are no longer used.

The answers for the 2026-10-30 run go on a blind sheet in the ignored `runs/t06/20261030T174450639792Z-blind/` folder; the runner does not create this sheet. `blind_review.md` lists the case's two plans as options A and B, with item names, catalog colors, sizes and retailer links, and `blind_scores.csv` takes the six answers and a note for each option, using the column names of `human_scores.csv`. The sheet hides prices and approach names, and the A/B assignment and the look order are shuffled. The key, `blind_key.json`, stays in the run folder, where `run.json` and `human_scores.csv` also reveal which option is which, so the rater opens none of them before answering. After answering, copy each option's answers into `human_scores.csv` using the key, which also maps the sheet's look order to the plan's looks.

Answers are blind only when the rater does not know which option is which. A rater who has seen the key or been told the mapping gives an open assessment, and the results must say so. The sheet hides the labels but not the baseline's behavior: it has no product images, and the retailer pages that show the items also show their prices. A rater who knows the baseline rule may recognize the baseline from these cues, so record with the answers whether the rater knew the rule.

The project author answered for the 2026-10-30 run on 2026-10-30, through `blind_rater.html` in the blind folder, a page that shows the retailer product photos kept for internal review. The author did not open the key, knew the baseline rule, and answered before seeing any model's answers.

| Approach | "Is this what I asked for?" | "Would I go and try this on?" |
| --- | ---: | ---: |
| Muse Spark | 2 of 3 looks | 3 of 3 looks |
| Baseline | 1 of 3 looks | 3 of 3 looks |

Both plans contain the same look, a black long-sleeve top with grey chinos and the Beach slide, and it got yes to both questions in each plan. The approaches differ by one answer of six on a single case, which is too little to show a difference. Every look got yes to the second question, so on this case only the first question separated the plans. The author's notes name three concerns: the open slide in an autumn office look, a T-shirt with no layer over it in autumn, and a long-sleeve top or sweater worn without a T-shirt under it. The case's budget rules out closed shoes and an outer layer (see [2026-10-30](#2026-10-30)). The look numbers in the notes follow the sheet order, which `blind_key.json` maps to the plans.

Two model raters answered the same sheet after the author. Both answered no to the first question for all six looks, and yes to the second only for the Muse Spark look without shoes, mostly because of the open slide. Their answers are not human judgments and are not counted above; one set is in `blind_scores_model.csv`.

## Measures

The runner records machine-checked results separately from human judgments:

- Hard constraints: final pass/fail and final violation count.
- Repair behavior: rejected proposals, format failures, and rejected hard-constraint violations grouped by rule. A repaired first proposal remains in these counts even when the final plan passes.
- Provider usage: completed calls, HTTP attempts, prompt and completion tokens, and elapsed time.
- Model API cost estimate: reported prompt tokens at $1.25 per million uncached input tokens and completion tokens at $4.25 per million output tokens, using [Baseten's published rates](https://www.baseten.co/pricing/) checked 2026-10-29. The estimate does not discount cached input or account for a timed-out request whose usage was not returned.
- Catalog price: the selected plan's EUR cents under the case's budget mode. This is product price, not model usage cost.
- Human judgment: for `mens-fit-per-look` only, the two yes-or-no answers for each look described in [Human scoring](#human-scoring), with a note where an answer needs explanation. These answers do not change the hard-constraint result.

The planner records aggregate proposal counters and logs them. The counters do not include the brief or raw provider response. Live runs write raw HTTP response bodies to `provider_responses.jsonl` under the ignored run folder; the PR contains only validated item IDs and aggregate metrics. HTTP error bodies redact the API key if it appears in a response.

## Run

An offline run is the default and creates the baseline plans and a blank human-score sheet under ignored `runs/t06/`:

```bash
uv run --frozen python -m scripts.evaluate_planner
```

A live run calls Baseten's exact hackathon model. All the cases in a run share a cap of 12 HTTP attempts. A case can use up to four: two model generations, each with up to two HTTP attempts. The cases run in the order of the table above. A case starts only while at least four attempts remain, so the cap never stops a case part-way, which would look like a provider failure. Once fewer than four remain, each remaining case is recorded in `run.json` as not run, with no model call. In the 2026-10-29 run each case used one attempt. If the first three cases leave fewer than four attempts, `mens-fit-per-look` can be run on its own with `--case-id`. At 6,000 output tokens per attempt, 72,000 output tokens would cost about $0.306 at the listed output rate, plus input tokens. This is an estimate for the configured token limits, not a complete cost ceiling.

```bash
uv run --frozen --env-file .env python -m scripts.evaluate_planner \
  --live --case-id wool-free-per-look
```

The live runner uses `BASETEN_API_KEY` from the environment and enforces `meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1`. `--live` without at least one `--case-id` is rejected. The PR records aggregate run results; raw provider responses remain in the ignored run folder.

Each run writes `run.json` and `human_scores.csv` under ignored `runs/t06/`. A live run also writes `provider_responses.jsonl`. The run manifest records the case inputs, model endpoint and ID, catalog and source-code hashes, attempt ceiling, token rates and Git revision. Review the automated outcome first; score only plans that were produced. Keep model and baseline scores paired by case. A small catalog and four fixed cases support a pilot comparison only; they do not establish general quality, fit, stock availability or broad user preference.
