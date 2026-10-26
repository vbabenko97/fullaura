# Feasibility test

This report covers T-02 in [TASKS.md](TASKS.md): whether Muse Image can show a person in specific garments while it keeps their identity and the garments' details, and whether a targeted edit makes the requested change while the rest of the look stays the same. It holds the protocol, the thresholds and the results of each run. The test is a small feasibility study: its results show whether previews are worth building on, not that they are reliable in production. Personal photos and previews stay in `data/private/` and never appear here ([D-13](DECISIONS.md#d-13-personal-data-stays-out-of-git)).

Status: the thresholds were approved on 2026-10-26. An exploratory run with the author's photos and one garment comes first; the formal evaluation waits for two more subjects.

## Model and costs

- Model: `muse-image-1.0` in Standard mode, through `POST https://api.meta.ai/v1/images/edits`. The account's model list showed it on 2026-10-26.
- Price: $0.01 per generated image; the limit is 150 requests per minute per team ([Meta pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits), checked 2026-10-26).
- Client: `backend/app/meta_images.py`, with a 120-second timeout on each network operation and at most three attempts per image.

## Client check

On 2026-10-26 the client generated one product image of a plain navy sweater and then edited it to burgundy, with no personal data. Both calls succeeded on the first attempt, in 9.4 s and 11.0 s. The edit changed only the color; the fold, the knit texture, the lighting and the background stayed the same. This shows that the client works, and answers none of the questions of this test.

## Exploratory run

Before the formal evaluation, a separate run uses the author's own portrait and full-body photo and one garment, to tune the prompts and find problems early. Its inputs and outputs stay in `data/private/feasibility/exploratory/`. Its results are reported below, but they are not compared with the thresholds.

## Formal evaluation

- Three subjects who gave permission for model processing, each with a portrait and a full-body photo.
- Three outfits per subject, each made of two to four garments whose product images LookPilot may use (see the register in [DATA.md](DATA.md)).
- For each outfit, one look preview made from the photos and the garment images, then one targeted edit of that preview that replaces one garment.
- Two samples of each step: 3 subjects × 3 outfits × 2 steps × 2 samples = 36 images, about $0.36 before retries.

Its inputs and outputs stay in `data/private/feasibility/formal/`. Prompts follow one template for every case, so that results are comparable:

- Look: "Show the person from the first two images, full body, wearing exactly the garments in the other images. Keep their face, hair, skin tone and body proportions. Plain light background."
- Edit: "Replace only the <garment> with the one in the second image. Keep the person, the other garments, the pose and the background unchanged."

## Checks

The author scores each produced image by hand in the run's `scores.csv`, as pass, fail or unassessable. An image is unassessable for a check when it does not allow a judgment, for example when the face is hidden. A model's evaluation of its own output does not count.

| Check | Passes when |
| --- | --- |
| Identity | The person is recognizable as themselves. |
| Proportions | Body proportions are not visibly distorted. |
| Product details | Each garment's color, pattern, cut and visible details match its product image. |
| Edit succeeds | For edits only: the requested change happened, and the rest of the look stayed materially consistent. |

Every planned image counts in the denominator of each check that applies to it. A failed API call, an edit skipped because its look failed, a fail and an unassessable image all count as not passing. Each result gives the numerator and the denominator, with the counts of fails, unassessable images and images not produced.

## Thresholds

Approved on 2026-10-26 for an initial feasibility test.

| Check | Previews meet SPEC.md when |
| --- | --- |
| Identity | at least 80% of all images pass, and at least 75% of each subject's images pass |
| Proportions | at least 80% of images pass |
| Product details | at least 70% of images pass |
| Edit succeeds | at least 70% of edits pass |

If every threshold holds, previews stay as [SPEC.md](SPEC.md#previews) describes them. If any fails, the fallback in [D-11](DECISIONS.md#d-11-test-previews-first-and-keep-the-plan-useful-without-them) applies, and the author decides how far to narrow the visual promise. Latency, attempts and cost are recorded for every run but have no threshold.

## How to run

1. Put the photos, the garment images and `cases.json` in `data/private/feasibility/formal/`, or in `exploratory/` for an exploratory run. The format of `cases.json` is at the top of `scripts/feasibility_run.py`.
2. Check the inputs without calling the API: `uv run --frozen --env-file .env python -m scripts.feasibility_run --dry-run`. For an exploratory run, add `--cases data/private/feasibility/exploratory/cases.json` to this and the next command.
3. Run the test: `uv run --frozen --env-file .env python -m scripts.feasibility_run --samples 2`. The run's `attempts.jsonl` records the model ID, prompt, input file names, attempts, latency and usage or error of every API call.
4. Score every produced image in the run's `scores.csv`.
5. Count the results: `uv run --frozen python -m scripts.feasibility_run --summarize <run folder>`.
6. Add the run to the results below, without personal images or names.

## Results

Each check shows passed / planned images.

| Run | Kind | Date | Produced / planned | Median / p90 latency | Cost | Identity | Lowest subject identity | Proportions | Product details | Edit succeeds | Fails, unassessable, not produced |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
