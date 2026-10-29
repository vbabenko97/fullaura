# Azure deployment plan

**Status:** `infra/azure/main.bicep` prepares the initial Azure Container Apps deployment. No Azure resources have been provisioned and no image has been published. The separate feasibility script has made Meta requests; see [FEASIBILITY.md](FEASIBILITY.md).

## Initial configuration

Use one Azure Container App on a Consumption workload profile. FastAPI serves the compiled frontend and `/api` on port 8000. All future Meta requests originate on the backend.

| Setting | Proposed initial value |
| --- | --- |
| Region and quota | West Europe; checked 2026-10-29: Managed Environment Count 0/1, Sandbox Cores 0/10 |
| Compute | 0.5 vCPU / 1 GiB; revise after measuring image-request memory |
| Ingress | External HTTPS; target port 8000 |
| Revisions | Single active revision |
| Replicas | Minimum 0, maximum 1 |
| Public planning | `FULLAURA_PUBLIC_DEMO_ENABLED=false` until account-level spending controls are verified |
| Probes | HTTP `/api/health` for liveness; `/api/ready` for readiness, port 8000 |
| Container image | Public registry image pinned by SHA-256 digest, built from a recorded Git commit |
| Secrets | Container App secrets; inject into backend environment variables |

The Bicep template creates a Log Analytics workspace with 30-day retention, a Consumption environment, and one externally reachable Container App. It configures HTTP-to-HTTPS redirection, one active revision, 0.5 vCPU / 1 GiB, scale from zero to one replica, and explicit liveness/readiness probes. `FULLAURA_PUBLIC_DEMO_ENABLED` and `FULLAURA_T02_PREVIEWS_PASSED` remain `false`. The template accepts an image reference as a required parameter; deployment must pass a publicly pullable registry reference pinned by digest.

Scaling to zero reduces idle compute usage but introduces a cold start. Replica limits and Azure budget alerts are not hard spending caps. The template does not create a budget because the pilot's monthly alert threshold has not been specified. Logging ingestion and network egress may add costs.

`Microsoft.App` and `Microsoft.Quota` are registered in the current subscription. The verified West Europe quotas allow one new managed environment and up to 10 sandbox cores; current usage is zero. Recheck the quota before deployment if other Container Apps resources are added.

## Cost estimate

The Microsoft Retail Prices API lists West Europe Consumption rates of $0.000034 per active vCPU-second, $0.000004 per active GiB-second, and $0.56 per million requests. At 0.5 vCPU and 1 GiB running continuously for 30 days, compute and memory total about $54.43 before any subscription free grant. If the full monthly Container Apps grant remains unused, it offsets about $7.56, for about $46.87. The grant includes 180,000 vCPU-seconds, 360,000 GiB-seconds, and 2 million requests per subscription per month; other apps can consume it. With no active replicas, compute and memory charges are $0, excluding logs, network, registry, and other services. These are list-price estimates, not an account-adjusted forecast.

Model API usage is separate. The code allows up to two completed model calls per planning request, with a 6,000-token completion limit per call. At the published Muse Spark Open output rate of $4.25 per million tokens, the output-only upper bound is $0.051 per planning request or $0.153 for three requests in one session, before input tokens and any HTTP retries. Input is priced separately. The session allowance does not bound total pilot spend.

The app stores anonymous 18+ confirmations and three-request session quotas in process memory. Keep one Uvicorn worker and one active replica. A process restart resets those counts, and browser users can create another session. The session limit does not cap aggregate Azure or provider spend.

Use a public GHCR image containing code and compiled assets if available. Never bake `.env`, customer images, or provider keys into an image. No publishing or deployment workflow runs automatically in this repository.

## Information needed for the first deploy

- Recheck Azure credit balance, expiry, and spending-limit status before deployment; choose a budget-alert threshold.
- Recheck West Europe Container Apps quotas if other resources are added. Verify the deployment identity's permissions at the target scope before deployment.
- A dedicated resource group name (suggested: `rg-fullaura-demo`).
- An authenticated Azure session with permission to create Container Apps resources.
- A publicly reachable image digest built from `18ed8df` and the tested commit.
- For Muse Spark planning, store `BASETEN_API_KEY` as a backend secret and confirm the credit grant and provider spend controls before enabling public planning. The code uses `meta-models/muse-spark-open-1026`.
- `MODEL_API_KEY` is used only by Meta photo previews. Keep `FULLAURA_T02_PREVIEWS_PASSED=false` until T-02 passes and the preview setup is verified.
- A reviewed monthly Azure budget alert threshold and a confirmed Baseten model key and spend-control setup. Keep `FULLAURA_PUBLIC_DEMO_ENABLED=false` until account-level controls are verified.

## Deployment sequence

1. Build and smoke-test the image as described in `README.md`.
2. Inspect the subscription, numeric region quota, effective permissions, credits, and hard spending-limit behavior. Choose a budget alert threshold; Azure budgets notify and do not stop spending by default.
3. Publish the image to the chosen registry and record its immutable digest.
4. Deploy `infra/azure/main.bicep` to the dedicated resource group, passing the public image digest as `containerImage`. The template keeps public planning and photo previews disabled.
5. Verify the HTTPS root page, frontend assets, `/api/health`, `/api/ready`, and a JSON 404 for an unknown `/api` route.
6. Add model secrets only when the provider adapter exists. Keep the public planning flag false until Azure and provider account-level spending controls are verified. The app requires an 18+ confirmation and limits each anonymous session to three plan requests; updates and replacements count. Photo previews remain disabled pending T-02 and the route remains development-only.
7. Record the URL, commit, resource names, and rollback image digest in this document. Review usage after the first real session and after the demo.

Container-local files are ephemeral. The scaffold stores no photographs. If the MVP needs persistent image storage, add private Azure Blob Storage with short-lived access and explicit deletion, then document that behavior before accepting uploads.

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

Resource provisioning, registry publishing, a hard Azure cost ceiling, and end-to-end Azure validation remain pending. Azure budget alerts do not enforce a hard cap by default; Baseten monthly budgets can be set to enforce Model API limits but do not stop dedicated deployments or training.
