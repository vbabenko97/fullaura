# Closed beta

The closed beta lets a small group of invited people use the whole FullAura flow on the deployed site: preferences, three looks, an experimental try-on on their own photos, keeping and replacing items, a shopping list, saved plans and feedback. It is an experiment for the author and friends. It does not complete the formal preview test (T-02) or show that real people find the product useful; their use of the beta is how those questions get answered. [DECISIONS.md](DECISIONS.md) records why it works this way (D-43 to D-46), and [DEPLOYMENT.md](DEPLOYMENT.md) records what is deployed.

## For participants

You need the invite link you were sent, a phone or computer with a current browser, and, for a try-on, two photos of yourself: a portrait and a full-body photo, upright, as JPEG, PNG or WebP. You must be 18 or older.

1. Open the invite link. FullAura asks you to confirm that you are 18 or older.
2. Describe what the looks are for, and set an occasion, season and colors if you like. Enter a budget and choose whether it applies to each look or to all three together.
3. Choose closed, open or any shoes, and enter your clothing line and sizes. FullAura never reads sizes from photos, so enter the sizes you buy. Under "Avoid materials" and "Items you already own" you can exclude materials and add up to three of your own items.
4. Select "Generate three looks". Each look shows real products from shops that deliver to Austria, with their price, the date the price was checked, and a link to the shop.
5. To try a look on, read the photo notice and agree to it, choose your two photos, pick a look and select "Try on". The result is an experimental drawing: it shows approximate colors and combinations, not the exact products, their fit or your size. It is not saved. "Withdraw consent" stops a try-on in progress and removes your photos and the result from the page; photos already sent to Meta cannot be called back.
6. Select "Keep item" on anything you want to hold, change your budget or other preferences, and select "Update three looks". Kept items stay in place. To swap one item, select "Replace" next to it; everything else stays.
7. The shopping list counts each product once, however many looks use it, and your own items at no cost. Copy it or print it. Prices exclude delivery and returns, and stock can change.
8. Select "Save this plan" to keep it in this browser. "Restore" checks a saved plan against today's catalog; "Delete" removes it.
9. Answer the four feedback questions at the bottom. You can delete your answers later.

Each invite has 8 planning requests and 12 try-ons; generating, updating and replacing each use a planning request, and a cancelled try-on still counts. The beta can also pause model requests early when its shared budget is used up. Your plans and shopping lists keep working when that happens.

Planning and try-ons close at the end of the pilot, on 6 November 2026 at 23:59 Vienna time. On 9 November 2026 at 10:00 Vienna time the beta closes, and within a few minutes FullAura deletes your invite's records and your feedback. Plans you saved stay in your browser until you delete them.

## Costs

Every model call first reserves its largest possible cost in a shared ledger, at the providers' list prices checked on 2026-10-30: Muse Spark Open on Baseten at $1.25 per million input tokens and $4.25 per million output tokens ([Baseten pricing](https://www.baseten.co/pricing/)), and Muse Image on Meta at $0.01 per image ([Meta pricing](https://dev.meta.ai/docs/pricing-rate-limits)). A planning request can make four HTTP attempts, each counted at 24,000 input tokens and the 6,000-token output cap, so it reserves about $0.22. A try-on can make three attempts and reserves $0.03. After the call, the ledger records the cost from the tokens the provider reported, plus the full maximum for each attempt that reported nothing; a try-on counts $0.01 for each attempt. A call whose outcome is unknown, such as a timeout or a lost connection, keeps its whole reservation.

| Budget | Limit | Covers |
| --- | ---: | --- |
| Planning | $8 | Muse Spark calls, including development and live checks after the ledger was created |
| Images | $3 | Muse Image calls, including the synthetic test runs |
| Azure | $10 | Container Apps, the purge job, Blob Storage and Log Analytics for the pilot until its records are deleted |
| Reserve | $4 | Unplanned costs; not available to the app |

The app enforces the planning and image limits. Azure has no hard limit in the app: the subscription's Free Trial spending limit and the `fullaura-monthly` budget alert in [DEPLOYMENT.md](DEPLOYMENT.md) cover it. The ledger's figures are list-price estimates, not provider invoices.

## Running the pilot

The operator, the person who deploys the beta and hands out invites, follows these steps. [DEPLOYMENT.md](DEPLOYMENT.md) has the Azure commands.

1. Create the invites: `uv run --frozen python -m scripts.pilot_invites --count 10 --base-url <site URL>`. The codes and links go to the ignored `data/private/beta/invites.csv`, and their SHA-256 digests to `data/private/beta/invite-hashes.txt`; the app receives only the digests. Note in the CSV whom each invite went to.
2. Deploy with `betaEnabled=true`, the digests, a new session secret, both provider keys, `pilotEndsAt` seven days after the start, `recordsPurgeAt` and `recordsPurgeSchedule` for the deletion of the records after the end, and `planningBudgetUsd` and `imageBudgetUsd` set to the budgets less anything already spent outside the ledger.
3. Check spending and use without personal data, at the latest before the records are deleted: `uv run --frozen python -m scripts.pilot_report --store-url <store URL> --invites data/private/beta/invite-hashes.txt --subscription <subscription ID>`. It needs the Storage Blob Data Reader role or higher on the storage account; the subscription option picks the right tenant when the Azure CLI's default subscription is another one.
4. To withdraw one invite, deploy again without its digest. To stop the beta at once, set `FULLAURA_BETA_ENABLED=false`, or stop the app (see [DEPLOYMENT.md](DEPLOYMENT.md#stopping-the-beta)).

The store keeps, per invite, the time of the age confirmation and the photo consent, request counts and feedback IDs; the ledger, the record of each model call, and the feedback answers. The beta closes on 2026-11-09 at 10:00 Vienna time (09:00 UTC), and a scheduled job then deletes every record within about three minutes; a closure marker that the job writes first keeps any record from reappearing ([D-49](DECISIONS.md#d-49-a-scheduled-job-deletes-the-pilot-records-and-the-beta-closes-then), [D-50](DECISIONS.md#d-50-a-closure-marker-and-a-grace-period-keep-writes-from-outliving-the-purge)); [DEPLOYMENT.md](DEPLOYMENT.md#deleting-the-pilot-records) describes the job. A lifecycle rule that deletes a record 30 days after its last change stays as a backstop. A participant's feedback can be deleted by the participant at any time, and the operator can delete an invite's records earlier with the commands in [DEPLOYMENT.md](DEPLOYMENT.md#stopping-the-beta).

## Checks before the beta opened

On 2026-10-30 three synthetic adult personas, generated with Muse Image for testing and showing no real person, went through planning and a try-on against a local server with the real providers: a men's M office look under €250 without wool, a women's S casual look under €150, and a women's L capsule under €300 for the whole plan without wool. All three plans passed the hard constraints on the first model call, and all three try-ons succeeded on the first attempt, in 13 to 19 seconds. The run cost $0.023 for planning and $0.03 for images; generating the personas cost $0.06.

An agent that had not seen the code reviewed the try-ons against the input photos and the looks. It judged all three usable as experimental visualizations, with no warped faces, extra limbs, text or broken backgrounds. It noted that the results read as the clothes placed on the same photo, that a top under a sweater shows only at the collar and cuffs, and that a white T-shirt in one look matched the white T-shirt in that persona's own photo. This is an automated preliminary check, not human evidence of quality.
