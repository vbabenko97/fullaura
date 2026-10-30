# Closed beta

The closed beta lets a small group of invited people use the whole FullAura flow on the deployed site: preferences, three looks, an experimental try-on on their own photos, keeping and replacing items, a shopping list, saved plans and feedback. It is an experiment for the author and friends. It does not complete the formal preview test (T-02) or show that real people find the product useful; their use of the beta is how those questions get answered. [DECISIONS.md](DECISIONS.md) records why it works this way (D-43 to D-46), and [DEPLOYMENT.md](DEPLOYMENT.md) records what is deployed.

## For participants

You need the invite link you were sent, a phone or computer with a current browser, and, for a try-on, two photos of yourself: a portrait and a full-body photo, upright, as JPEG, PNG or WebP. You must be 18 or older.

1. Open the invite link. FullAura asks you to confirm that you are 18 or older.
2. Describe what the looks are for, and set an occasion, season and colors if you like. Enter a budget and choose whether it applies to each look or to all three together.
3. Choose closed, open or any shoes, and enter your clothing line and sizes. FullAura never reads sizes from photos, so enter the sizes you buy. Under "Avoid materials" and "Items you already own" you can exclude materials and add up to three of your own items.
4. Select "Generate three looks". Each look shows real products from shops that deliver to Austria, with their price, the date the price was checked, and a link to the shop.
5. To try a look on, read the photo notice and agree to it, choose your two photos, pick a look and select "Try on". The result is an experimental drawing: it shows approximate colors and combinations, not the exact products, their fit or your size. It is not saved.
6. Select "Keep item" on anything you want to hold, change your budget or other preferences, and select "Update three looks". Kept items stay in place. To swap one item, select "Replace" next to it; everything else stays.
7. The shopping list counts each product once, however many looks use it, and your own items at no cost. Copy it or print it. Prices exclude delivery and returns, and stock can change.
8. Select "Save this plan" to keep it in this browser. "Restore" checks a saved plan against today's catalog; "Delete" removes it.
9. Answer the four feedback questions at the bottom. You can delete your answers later.

Each invite has 8 planning requests and 12 try-ons; generating, updating and replacing each use a planning request, and a cancelled try-on still counts. The beta can also pause model requests early when its shared budget is used up. Your plans and shopping lists keep working when that happens.

## Costs

Every model call first reserves its largest possible cost in a shared ledger, at the providers' list prices checked on 2026-10-30: Muse Spark Open on Baseten at $1.25 per million input tokens and $4.25 per million output tokens ([Baseten pricing](https://www.baseten.co/pricing/)), and Muse Image on Meta at $0.01 per image ([Meta pricing](https://dev.meta.ai/docs/pricing-rate-limits)). A planning request can make four HTTP attempts, each counted at 24,000 input tokens and the 6,000-token output cap, so it reserves about $0.22. A try-on can make three attempts and reserves $0.03. After the call, the ledger records the cost from the tokens the provider reported, plus the full maximum for each attempt that reported nothing; a try-on counts $0.01 for each attempt. A call whose outcome is unknown, such as a timeout or a lost connection, keeps its whole reservation.

| Budget | Limit | Covers |
| --- | ---: | --- |
| Planning | $8 | Muse Spark calls, including development and live checks after the ledger was created |
| Images | $3 | Muse Image calls, including the synthetic test runs |
| Azure | $10 | Container Apps, Blob Storage and Log Analytics for the pilot and its 30 days of retention |
| Reserve | $4 | Unplanned costs; not available to the app |

The app enforces the planning and image limits. Azure has no hard limit in the app: the subscription's Free Trial spending limit and the `fullaura-monthly` budget alert in [DEPLOYMENT.md](DEPLOYMENT.md) cover it. The ledger's figures are list-price estimates, not provider invoices.

## Running the pilot

The operator, the person who deploys the beta and hands out invites, follows these steps. [DEPLOYMENT.md](DEPLOYMENT.md) has the Azure commands.

1. Create the invites: `uv run --frozen python -m scripts.pilot_invites --count 10 --base-url <site URL>`. The codes and links go to the ignored `data/private/beta/invites.csv`, and their SHA-256 digests to `data/private/beta/invite-hashes.txt`; the app receives only the digests. Note in the CSV whom each invite went to.
2. Deploy with `betaEnabled=true`, the digests, a new session secret, both provider keys and `pilotEndsAt` seven days after the start.
3. Check spending and use without personal data: `uv run --frozen python -m scripts.pilot_report --store-url <store URL> --invites data/private/beta/invite-hashes.txt`. It needs the Storage Blob Data Reader role or higher on the storage account.
4. To withdraw one invite, deploy again without its digest. To stop the beta at once, set `FULLAURA_BETA_ENABLED=false`, or stop the app (see [DEPLOYMENT.md](DEPLOYMENT.md#stopping-the-beta)).

The store keeps, per invite, the time of the age confirmation and the photo consent, request counts and feedback IDs; the ledger, the record of each model call, and the feedback answers. A lifecycle rule deletes every record 30 days after its last change, so all pilot data is gone within 30 days after the pilot ends. A participant's feedback can be deleted by the participant at any time, and the operator can delete an invite's records earlier with the commands in [DEPLOYMENT.md](DEPLOYMENT.md#stopping-the-beta).
