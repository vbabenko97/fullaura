# Azure deployment

**Status:** The closed beta runs on Azure Container Apps in France Central since 2026-10-30. Invited sessions can plan and try looks on; everyone else sees the invite step. The pilot closes model calls at 2026-11-06 23:59 Vienna time.

## Deployed configuration

| Setting | Deployed value |
| --- | --- |
| Subscription | `Azure subscription 1` (`cfc3a957-dd4c-4248-856b-2e17aa22df5f`), Free Trial, spending limit on |
| Resource group | `rg-fullaura-demo` |
| Region | France Central (`francecentral`) |
| Log Analytics workspace | `law-fullaura-demo-frc`, 30-day retention |
| Container Apps environment | `cae-fullaura-demo-frc`, Consumption workload profile |
| Container App | `fullaura-demo`, with a system-assigned managed identity |
| Revision | `fullaura-demo--0000006`, serving all traffic since 2026-10-30 |
| Compute and scaling | 0.5 vCPU / 1 GiB; minimum 0 and maximum 1 replica; scales to zero after 300 seconds without traffic |
| Ingress | External HTTPS, target port 8000, insecure HTTP disabled |
| Beta | `FULLAURA_BETA_ENABLED=true`; 11 invite digests in the secret `invite-hashes` (10 for participants, 1 for the operator); cookies signed with the secret `session-secret` |
| Pilot window | `FULLAURA_PILOT_ENDS_AT=2026-11-06T23:59:00+01:00` |
| Budgets in the app | Planning $7.97 and images $2.91: the beta's $8 and $3, less the $0.023 and $0.09 spent outside the ledger on the checks of 2026-10-30 |
| Pilot store | Storage account `stfullauradurnz5gu4z`, container `pilot`; shared keys and public blob access off; a lifecycle rule deletes each record 30 days after its last change. The app's identity has Storage Blob Data Contributor on the container; the operator has Storage Blob Data Reader on the account. |
| Planning model | `meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1`, key in the secret `baseten-api-key` |
| Try-on model | `muse-image-1.0` in Standard mode, key in the secret `model-api-key` |
| Image | `ghcr.io/vbabenko97/fullaura@sha256:aa797cca7be943806f64299fb8d502ca2096f7bda9a2e4a7dec9780985cc95ed` (tag `00c7e2f-linux-amd64`; its `linux/amd64` manifest is `sha256:b8c12de2a840a05ffff8c048333bd0b9fe6c82332599c10c13dfa8f99604739d`) |
| Application source | `main` at `00c7e2f` |
| Rollback image | `ghcr.io/vbabenko97/fullaura@sha256:9fececb06a258c5074aa3340e054a7d3d723c0153f69381b5ca9301526169fb5`, built from `e918d07`, the anonymous planning demo; it ignores the beta settings and keeps planning closed |
| ARM deployments | `fullaura-beta-20261030` and `fullaura-beta-budgets-20261030`, from `infra/azure/main.bicep` |

The image is public, and GHCR issues an anonymous pull token for it. The keys, the session secret and the invite digests are encrypted Container Apps secrets, passed to the template as secure parameters; none is in the image or the repository. The resource group and the existing subscription-level budget remain in place.

The existing `fullaura-monthly` budget is $20 per month from 2026-10-01 through 2027-10-01, with actual-spend notifications at 50%, 80%, and 100%. Budgets notify; they do not stop resources or cap usage. The Free Trial spending limit remains on. The last billing record available during the pre-deployment check showed a $200 closed balance expiring 2026-11-22 18:56 UTC; this was not a live estimated balance.

On 2026-10-29, Baseten's pinned answer in the hackathon `#baseten` channel confirmed that the $100 grant is attached to the hackathon API key, not a personal workspace; personal workspace budgets do not apply. Baseten said no payment method is needed and the key cannot be charged beyond $100. The remaining credit cannot be viewed. The planning API returns prompt and completion token counts, which provide an activity record but not a live dollar balance.

The app writes one `backend.app.planner` line for each planning request the planner completes, with its outcome, model, calls, HTTP attempts, token counts, proposals, failures and latency, and one `backend.app.main` line for each try-on, with its outcome, attempts and latency. Neither holds anything a person entered, an invite code or an image. The Container Apps environment sends the container log to the Log Analytics workspace, where the lines appear in `ContainerAppConsoleLogs_CL`. A pilot-store warning names at most the first 16 hex digits of an invite's digest.

The Uvicorn access log records the method, path, status and client address of each request, but not request bodies or cookies. On 2026-10-30 the client address it recorded was `100.100.0.67`, an internal address of the Container Apps ingress, not the visitor's IP. Uvicorn trusts forwarded client addresses only from 127.0.0.1 and ::1, and `FORWARDED_ALLOW_IPS` is not set.

## Checks on the deployed beta

On 2026-10-30, after the deployment of `00c7e2f`:

- `scripts/smoke_http.py` passed over HTTPS: the frontend and its assets, `/api/health`, `/api/ready`, an open beta session and a JSON 404 for an unknown `/api` route. Without an invite, `/api/catalog` answers 401.
- The operator invite opened a session, and its age confirmation reached the pilot store through the managed identity.
- The whole flow passed once with the real providers and a synthetic adult persona: plan, try-on, keeping an item, a lower budget, an update, a replacement, saving, reloading, restoring, copying, printing and feedback. Its three planning requests and one try-on cost $0.028 and $0.01 in the ledger.
- After `az containerapp revision restart`, a new replica served the invite with its three planning requests still counted.
- The app was rolled back to the previous image, which served health and readiness with planning closed, and rolled forward again to `00c7e2f`.
- The budget limits were then lowered by redeploying the template; revision `fullaura-demo--0000006` passed the smoke check and the invite session check again.

URL: <https://fullaura-demo.blackbay-aaeee2c5.francecentral.azurecontainerapps.io>.

To roll back, point the app at the rollback image in the table above with `az containerapp update` (see the deployment procedure). That image serves the anonymous demo of 2026-10-30, so the beta is closed while it runs.

## Configuration and cost

The Bicep template in `infra/azure/main.bicep` describes the 30-day Log Analytics workspace, the Consumption environment, the externally reachable Container App with one active revision, 0.5 vCPU / 1 GiB, scale from zero to one replica, HTTPS ingress and liveness and readiness probes, and the pilot store. The template keeps the beta closed unless `betaEnabled` is true.

The Azure Retail Prices API listed these France Central Consumption rates on 2026-10-29:

| Meter | Retail rate |
| --- | ---: |
| Active vCPU | $0.000024 per vCPU-second |
| Active memory | $0.000003 per GiB-second |
| Requests | $0.40 per million requests |

At 0.5 vCPU and 1 GiB running continuously for 30 days, compute and memory total about $38.88 before the monthly free grant, or $33.48 if the full grant is unused. The grant covers 180,000 vCPU-seconds, 360,000 GiB-seconds, and 2 million requests per subscription per month; other apps can consume it. Scaling to zero avoids Container Apps compute charges while no replica runs. Request charges above the grant, Log Analytics ingestion, network egress, and other Azure services are not included. These are list-price estimates, not an account-adjusted forecast.

The $20 budget alert is below the continuous-running estimate and is not a spending cap. The Free Trial spending limit protects against paid overage by disabling resources when its credit is exhausted, which would make the demo unavailable. Keep the app at zero minimum replicas and review Azure cost usage after pilot sessions.

Provider usage is not included in the Azure estimate. Public planning now uses the hackathon API key and its $100 hard credit cap. Baseten does not expose the remaining balance for this key. Review the token counts returned by each planning request; they do not establish the exact credit balance or dollars spent.

The Baseten hackathon key is not linked to the DevRain workspace, and its remaining credit cannot be checked in that workspace. Baseten's pinned answer says the $100 key credit cannot be exceeded; the app records per-request token counts, not a provider balance. No Baseten account or workspace settings were changed.

The app keeps each browser session's checked plan in process memory, so it keeps one Uvicorn worker and one replica; a restart or a scale-down forgets the plans, and browsers restore them. Quotas, spending and feedback live in the pilot store and survive restarts. The store costs cents at this size; blob storage, transactions and Log Analytics ingestion are the only charges beyond Container Apps.

The Free Trial credit expires on 2026-11-22. The pilot ends before that, but the 30-day deletion of pilot records runs until early December; if the subscription is not upgraded, Azure disables its resources when the trial ends, so delete the pilot records with the commands under [Stopping the beta](#stopping-the-beta) after the pilot rather than relying on the lifecycle rule.

## Deployment procedure

The template `infra/azure/main.bicep` describes every resource in `rg-fullaura-demo`: the Log Analytics workspace, the Container Apps environment, the app with its system-assigned managed identity, the private storage account for pilot records with its 30-day deletion rule, and the role assignment that lets the app's identity read and write that account's blobs. Deploy it to the existing resource group so that it updates these resources and creates no second stack. The Azure CLI's default subscription is another one, so every command names the FullAura subscription.

1. Build and smoke-test the image as described in `README.md`, then build it for `linux/amd64`, push it to `ghcr.io/vbabenko97/fullaura` and note its digest and source commit.
2. Check the subscription, its spending limit and credit, and the provider prices and terms named in [BETA.md](BETA.md#costs). Azure budgets notify; they do not stop spending.
3. Preview the change. The secure parameters come from the operator's environment and ignored files, never from the repository:

   ```bash
   az deployment group what-if --subscription <subscription ID> --resource-group rg-fullaura-demo \
     --template-file infra/azure/main.bicep --parameters containerImage=ghcr.io/vbabenko97/fullaura@<digest> \
     betaEnabled=true pilotEndsAt=<ISO date-time with zone> planningBudgetUsd=<USD> imageBudgetUsd=<USD> \
     basetenApiKey=<key> modelApiKey=<key> \
     sessionSecret=<secret> inviteHashes=<comma-separated digests>
   ```

   `pilotEndsAt` is required while the beta is enabled; the app refuses to start without it. Expect changes only to the app, the new storage resources and the role assignment. Stop if the preview deletes or recreates the environment or the workspace.
4. Run the same command with `az deployment group create`. A deployment keeps the session secret only if the same value is passed again; a new secret signs out every participant.
5. Wait for the role assignment to take effect, which can take a few minutes, then check `/`, `/api/health`, `/api/ready`, `/api/session` and a JSON 404 for an unknown `/api` route with `scripts/smoke_http.py <URL>`, and open the operator's invite to check planning and a try-on.
6. Record the URL, source commit, resource names, deployed digest and the previous working digest below.

To deploy only a new image, run step 1 and then `az containerapp update --name fullaura-demo --resource-group rg-fullaura-demo --subscription <subscription ID> --image ghcr.io/vbabenko97/fullaura@<digest>`. In single-revision mode this creates a new revision, moves all traffic to it, and keeps the environment variables and secrets. To roll back, run the same command with the previous working digest.

## Stopping the beta

- Close model calls and the beta's routes at once: `az containerapp update --name fullaura-demo --resource-group rg-fullaura-demo --subscription <subscription ID> --set-env-vars FULLAURA_BETA_ENABLED=false`. Plans saved on participants' devices stay readable in their browsers.
- Stop serving altogether: `az containerapp ingress disable --name fullaura-demo --resource-group rg-fullaura-demo --subscription <subscription ID>`.
- Delete every pilot record now rather than after 30 days: `az storage blob delete-batch --account-name <storage account> --source pilot --auth-mode login --subscription <subscription ID>`. The operator needs the Storage Blob Data Contributor role on the account.
- Remove all FullAura resources: `az group delete --name rg-fullaura-demo --subscription <subscription ID>`. This also deletes the logs and cannot be undone.

## Official references

Reviewed 29 October 2026:

- [Container Apps scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)
- [Container Apps health probes](https://learn.microsoft.com/en-us/azure/container-apps/health-probes)
- [Container Apps billing and free grant](https://learn.microsoft.com/en-us/azure/container-apps/billing)
- [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
- [Container Apps quotas](https://learn.microsoft.com/en-us/azure/container-apps/quotas)
- [Container Apps secrets](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
- [Azure spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit)
- [Azure budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
- [Baseten billing and budgets](https://docs.baseten.co/organization/billing)
- [Baseten pricing](https://www.baseten.co/pricing/)
- [FastAPI container deployment](https://fastapi.tiangolo.com/deployment/docker/)
- [Meta API documentation](https://dev.meta.ai/docs/overview)
