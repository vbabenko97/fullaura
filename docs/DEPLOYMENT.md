# Azure demo deployment

**Status:** The demo is deployed to Azure Container Apps in France Central. The app is publicly reachable over HTTPS, while public planning and photo previews remain disabled. The Baseten DevRain workspace was not changed.

## Deployed configuration

| Setting | Deployed value |
| --- | --- |
| Subscription | `Azure subscription 1` (`cfc3a957-dd4c-4248-856b-2e17aa22df5f`), Free Trial, spending limit on |
| Resource group | `rg-fullaura-demo` |
| Region | France Central (`francecentral`) |
| Log Analytics workspace | `law-fullaura-demo-frc`, 30-day retention |
| Container Apps environment | `cae-fullaura-demo-frc`, Consumption workload profile |
| Container App | `fullaura-demo` |
| Revision | `fullaura-demo--oqzvs62`, healthy at the smoke check |
| Compute and scaling | 0.5 vCPU / 1 GiB; minimum 0 and maximum 1 replica |
| Ingress | External HTTPS, target port 8000, insecure HTTP disabled |
| Public planning | `FULLAURA_PUBLIC_DEMO_ENABLED=false` |
| Photo previews | `FULLAURA_T02_PREVIEWS_PASSED=false` |
| Image | `ghcr.io/vbabenko97/fullaura@sha256:3e33cff2c0249e0cc08fd40e93bcee8ad83328df969a83506c13ea6bfdd4805f` (`linux/amd64`) |
| Application source | Docker inputs in deployment commit `767d57e` match application commit `18ed8df` |
| Successful ARM deployment | `fullaura-francecentral-amd64-20261029` |

The image is public and an anonymous pull of the pinned digest succeeded. The deployment contains no provider API keys. The resource group and the existing subscription-level budget remain in place.

The existing `fullaura-monthly` budget is $20 per month from 2026-10-01 through 2027-10-01, with actual-spend notifications at 50%, 80%, and 100%. Budgets notify; they do not stop resources or cap usage. The Free Trial spending limit remains on. The latest billing record available during the pre-deployment check showed a $200 closed balance expiring 2026-11-22 18:56 UTC; this was not a live estimated balance.

## Smoke check

The HTTPS smoke check passed on 2026-10-29. After the revision scaled to zero, a follow-up request restarted it and `/`, `/api/health`, and `/api/ready` again returned `200`.

| Request | Result |
| --- | --- |
| `GET /` | `200`, compiled frontend returned |
| `GET /api/health` | `200`, `{"status":"ok","service":"fullaura"}` |
| `GET /api/ready` | `200`, `{"status":"ready"}` |
| `GET /api/does-not-exist` | `404`, JSON `{"detail":"Not Found"}` |

Demo URL: <https://fullaura-demo.blackbay-aaeee2c5.francecentral.azurecontainerapps.io>.

This is the first successful deployment, so there is no previous working image digest to use as a rollback target. The deployed digest above is the known-good version.

## Configuration and cost

The Bicep template in `infra/azure/main.bicep` creates a 30-day Log Analytics workspace, a Consumption environment, and one externally reachable Container App. It configures one active revision, 0.5 vCPU / 1 GiB, scale from zero to one replica, HTTPS ingress, and explicit liveness and readiness probes. Public planning and photo previews remain disabled until their separate account and evaluation gates are met.

The Azure Retail Prices API listed these France Central Consumption rates on 2026-10-29:

| Meter | Retail rate |
| --- | ---: |
| Active vCPU | $0.000024 per vCPU-second |
| Active memory | $0.000003 per GiB-second |
| Requests | $0.40 per million requests |

At 0.5 vCPU and 1 GiB running continuously for 30 days, compute and memory total about $38.88 before the monthly free grant, or $33.48 if the full grant is unused. The grant covers 180,000 vCPU-seconds, 360,000 GiB-seconds, and 2 million requests per subscription per month; other apps can consume it. Scaling to zero avoids Container Apps compute charges while no replica runs. Request charges above the grant, Log Analytics ingestion, network egress, and other Azure services are not included. These are list-price estimates, not an account-adjusted forecast.

The $20 budget alert is below the continuous-running estimate and is not a spending cap. The Free Trial spending limit protects against paid overage by disabling resources when its credit is exhausted, which would make the demo unavailable. Keep the app at zero minimum replicas and review Azure cost usage after pilot sessions.

Provider usage is not included in the Azure estimate. The deployed app makes no model calls while public planning is disabled. Recalculate provider cost from the verified account and model billing terms before enabling planning.

Baseten's hackathon credit and key association with the DevRain workspace remain unverified. No Baseten settings or keys were changed, and public planning must stay disabled until the model credit and account-level spending controls are confirmed.

The app stores anonymous 18+ confirmations and three-request session quotas in process memory. Keep one Uvicorn worker and one active replica. A process restart resets those counts, and browser users can create another session. The session limit does not cap aggregate Azure or provider spend.

Container-local files are ephemeral. The scaffold stores no photographs. If the MVP needs persistent image storage, add private Azure Blob Storage with short-lived access and explicit deletion, then document that behavior before accepting uploads.

## Deployment procedure

1. Build and smoke-test the image as described in `README.md`.
2. Inspect the subscription, region quota, effective permissions, credit balance, and spending-limit behavior. Choose a monthly budget alert; Azure budgets notify but do not stop spending.
3. Publish a publicly pullable image and record its immutable digest and source commit.
4. Deploy `infra/azure/main.bicep` to the dedicated resource group, passing the image digest as `containerImage`. Keep public planning and photo previews disabled.
5. Verify the HTTPS root page, `/api/health`, `/api/ready`, and a JSON 404 for an unknown `/api` route.
6. Add model secrets only when the provider adapter is ready. Keep public planning disabled until Azure and provider account-level controls are verified. Photo previews remain disabled pending T-02.
7. Record the URL, source commit, resource names, deployed digest, and a previous working rollback digest when one exists. Review usage after pilot sessions and after the demo.

## Official references

Reviewed 29 October 2026:

- [Container Apps scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)
- [Container Apps health probes](https://learn.microsoft.com/en-us/azure/container-apps/health-probes)
- [Container Apps billing and free grant](https://learn.microsoft.com/en-us/azure/container-apps/billing)
- [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
- [Container Apps quotas](https://learn.microsoft.com/en-us/azure/container-apps/quotas)
- [Azure spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit)
- [Azure budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
- [Baseten billing and budgets](https://docs.baseten.co/organization/billing)
- [Baseten pricing](https://www.baseten.co/pricing/)
- [FastAPI container deployment](https://fastapi.tiangolo.com/deployment/docker/)
- [Meta API documentation](https://dev.meta.ai/docs/overview)
