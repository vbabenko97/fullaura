# Azure deployment

**Status:** The closed beta runs on Azure Container Apps in France Central since 2026-10-30, with the fixes of 2026-10-31. Invited sessions can plan and try looks on; everyone else sees the invite step. The pilot closes model calls at 2026-11-06 23:59 Vienna time. The purge job is scheduled to delete the pilot records at 2026-11-09 10:00 Vienna time, when the beta closes; it has not run on the real records yet.

## Deployed configuration

| Setting | Deployed value |
| --- | --- |
| Subscription | `Azure subscription 1` (`cfc3a957-dd4c-4248-856b-2e17aa22df5f`), Free Trial, spending limit on |
| Resource group | `rg-fullaura-demo` |
| Region | France Central (`francecentral`) |
| Log Analytics workspace | `law-fullaura-demo-frc`, 30-day retention |
| Container Apps environment | `cae-fullaura-demo-frc`, Consumption workload profile |
| Container App | `fullaura-demo`, with a system-assigned managed identity |
| Revision | `fullaura-demo--0000008`, serving all traffic since 2026-10-31 |
| Compute and scaling | 0.5 vCPU / 1 GiB; minimum 0 and maximum 1 replica; scales to zero after 300 seconds without traffic |
| Ingress | External HTTPS, target port 8000, insecure HTTP disabled |
| Beta | `FULLAURA_BETA_ENABLED=true`; 11 invite digests in the secret `invite-hashes` (10 for participants, 1 for the operator); cookies signed with the secret `session-secret` |
| Pilot window | `FULLAURA_PILOT_ENDS_AT=2026-11-06T23:59:00+01:00` and `FULLAURA_RECORDS_PURGE_AT=2026-11-09T10:00:00+01:00` |
| Budgets in the app | Planning $7.97 and images $2.91: the beta's $8 and $3, less the $0.023 and $0.09 spent outside the ledger on the checks of 2026-10-30 |
| Pilot store | Storage account `stfullauradurnz5gu4z`, container `pilot`; shared keys and public blob access off; a lifecycle rule deletes each record 30 days after its last change. The app's and the purge job's identities each have Storage Blob Data Contributor on the container; the operator has Storage Blob Data Reader on the account. |
| Purge job | `fullaura-demo-purge`, a scheduled Container Apps job in the same environment with a system-assigned identity: cron `0 9 9-15 11 *` in UTC, 0.25 vCPU / 0.5 GiB, 600-second replica timeout, 3 retries, its own image, the purge job image below ([Deleting the pilot records](#deleting-the-pilot-records)) |
| Planning model | `meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1`, key in the secret `baseten-api-key` |
| Try-on model | `muse-image-1.0` in Standard mode, key in the secret `model-api-key` |
| App image | `ghcr.io/vbabenko97/fullaura@sha256:5366c7af7995102dd036c450a59415c81fd919630d68a3ac178bc8b327207e68` (tag `62bd84f-linux-amd64`; its `linux/amd64` manifest is `sha256:f61b8767673ccd9d4e5b6f7ec92c0472bce3f101cd6a64952ad946c1d3d5772a`) |
| Purge job image | `ghcr.io/vbabenko97/fullaura@sha256:5366c7af7995102dd036c450a59415c81fd919630d68a3ac178bc8b327207e68`, the same build as the app image; its purge writes the closure marker |
| Application source | `main` at `62bd84f` |
| App rollback image | `ghcr.io/vbabenko97/fullaura@sha256:4a46e357f6ac485c5655436b0d41dadde4185a54cbcaf908ed08ee5ebbd0ad97`, built from `a7eba24`, which served until 2026-10-31; only with `FULLAURA_BETA_ENABLED=false`, because it writes without checking the closure marker ([Rolling back](#rolling-back)) |
| Purge job rollback image | none: no earlier image can run the purge job |
| ARM deployments | `fullaura-beta-20261030`, `fullaura-beta-budgets-20261030`, `fullaura-beta-fixes-20261031` and `fullaura-purge-fence-20261031`, from `infra/azure/main.bicep` |

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

On 2026-10-31, after the deployment of `a7eba24`:

- The deployment kept the four secrets: their values matched the operator's copies before and after. Revision `fullaura-demo--0000007` served all traffic with the image above, the same pilot end and budgets, and the new purge time.
- `scripts/smoke_http.py` passed over HTTPS, with an open beta session. Without an invite, `/api/catalog`, `/api/previews`, `/api/feedback` and `/api/session/confirm-age` answered 401, and an unknown invite code 403.
- The operator invite planned once for a synthetic adult persona, and a try-on on that persona's photos was withdrawn five seconds after it started: the browser aborted the request, the page showed no result and empty photo fields, the server recorded the withdrawn consent and logged `preview outcome=disconnected`, and the ledger kept the try-on's whole $0.03 reservation, with nothing left running. The plan cost $0.009.
- The purge job was checked against `pilot-purge-check`, a container made for the test with four synthetic records written through the pilot code, with roles for the job and the operator on that container only. A run with the real purge time deleted nothing; a run with a purge time in the past deleted all four and found none left; a repeat found nothing and succeeded. A run of the job as configured, against the real `pilot` container, logged six records found, its access checked and none deleted, and the container still held six. The four log lines held counts only. The test container and its two role assignments were then removed.

Later on 2026-10-31, after the deployment of `62bd84f`, which added the closure marker and the separate job image:

- The secrets matched the operator's copies before and after. Revision `fullaura-demo--0000008` served all traffic; the app and the job ran the image above with the same pilot end, purge time, budgets and schedule, and the job kept its identity and its single role on the `pilot` container. The what-if beforehand changed only the two images.
- `scripts/smoke_http.py` passed over HTTPS with an open beta session. Without an invite, the catalog, try-on, feedback and age routes answered 401, and an unknown invite code 403.
- On `pilot-purge-check`, a container made for the test with four synthetic records and roles for the job and the operator on it only: a run with the real purge time deleted nothing and wrote no marker. A run with a purge time in the past wrote the marker; a write from the app's code, made with a clock before the purge time one second after the marker appeared, was refused while the four records still existed. The run then deleted all four, kept the marker, and a later write was refused too. A repeat run found nothing and succeeded. Each due run took about two and a half minutes.
- The job as configured, run against the real `pilot` container, logged eight records found, its access checked and none deleted; the container still held eight and no marker. No log line of the job named a record. The test container and its two role assignments were then removed.
- No model was called for these checks.

URL: <https://fullaura-demo.blackbay-aaeee2c5.francecentral.azurecontainerapps.io>.

To roll back, follow [Rolling back](#rolling-back): the app and the purge job roll back separately.

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

The Free Trial ends about 2026-11-22 18:56 UTC: the subscription was created on 2026-10-23 at 18:57 UTC, the trial lasts 30 days, and the last billing record available on 2026-10-29 showed the credit expiring then. If the subscription is not upgraded, Azure disables its resources when the trial ends. The pilot ends on 2026-11-06, and the purge job deletes its records on 2026-11-09 ([Deleting the pilot records](#deleting-the-pilot-records)), so the 30-day lifecycle rule, which would run into December, is only a backstop.

## Deleting the pilot records

The template creates the Container Apps job `fullaura-demo-purge` in the same environment when `recordsPurgeAt` is set ([D-49](DECISIONS.md#d-49-a-scheduled-job-deletes-the-pilot-records-and-the-beta-closes-then)). It runs its own image, `purgeJobImage` ([D-51](DECISIONS.md#d-51-the-app-and-the-purge-job-have-their-own-images-and-roll-back-apart)), with `python -m backend.app.purge`, 0.25 vCPU and 0.5 GiB, on the cron schedule `recordsPurgeSchedule`, which Container Apps evaluates in UTC ([Jobs in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/jobs)). Its system-assigned identity has Storage Blob Data Contributor scoped to the `pilot` container, through its own role assignment. That is the narrowest built-in role that can delete blobs; it also allows writing blobs and changing or deleting the container, which the job never does, and it reaches nothing outside the container.

| Setting | Value for this pilot |
| --- | --- |
| Pilot end, `pilotEndsAt` | 2026-11-06 23:59 Vienna time (22:59 UTC) |
| Purge time, `recordsPurgeAt` | `2026-11-09T10:00:00+01:00`: 2026-11-09 10:00 Vienna time, 09:00 UTC |
| Schedule, `recordsPurgeSchedule` | `0 9 9-15 11 *`: 09:00 UTC every day from 9 to 15 November |
| Retries | each run retries a store call three times within 7 seconds; the job retries a failed run up to three times within 600 seconds; the next day's run repeats it |

A run that starts up to five minutes before the purge time waits for it, so a scheduler that fires a few seconds early does not postpone the deletion by a day. Before the purge time a run deletes nothing: it lists the container, deletes a name that cannot exist, which proves the right to delete, logs the number of records and exits 0. From the purge time on it writes the closure marker `purge-marker.json`, waits 120 seconds, deletes every record except the marker, lists the container again and exits 0 only when no record is left ([D-50](DECISIONS.md#d-50-a-closure-marker-and-a-grace-period-keep-writes-from-outliving-the-purge)). So the records are gone about three minutes after the job starts. A repeated run keeps the marker, waits again and deletes every record it finds; when there is none, it succeeds without deleting anything. A settings error, such as a purge time that is not after the pilot's end, exits 2, and an unreachable store exits 1, so the job retries. The log, in `ContainerAppConsoleLogs_CL`, holds counts only.

The app closes the beta at the purge time. Its writes coordinate with the job: right before every create, replace or delete, the app refuses if the purge time has passed or the marker exists. A write that the check allowed must leave the app within 5 seconds of the check, token refresh included, or the app does not send it. Every Blob request carries the service's own 10-second timeout, and the client gives up on each phase of a request after 10 seconds. A write that passed the check before the marker existed therefore reaches the service and ends well within the job's 120-second wait, and one that checks later is refused. The guarantee assumes that a request the app sent reaches the Blob service within about a minute, with DNS and the network included; the client cannot enforce that part. The daily runs from 10 to 15 November 2026 also delete records that appeared after an earlier run, for example from a request delayed past the wait. Such a record is deleted by the next run that succeeds within the schedule, which is not necessarily the next morning. The repeats lower the remaining risk but do not make that assumption a strict guarantee, and a record that appears after the last scheduled run is outside the schedule. The marker holds only when the deletion began and the purge time. While it exists, the app refuses every write; the pilot's end keeps model calls closed although the ledger is gone. To reopen writing, delete the marker by hand.

The job deletes only blobs in the `pilot` container and writes only its marker there. It does not touch the container itself, other storage, the logs or any Azure resource. To check the configuration and the job's runs:

```bash
az containerapp job show --name fullaura-demo-purge --resource-group rg-fullaura-demo --subscription <subscription ID> \
  --query "{schedule:properties.configuration.scheduleTriggerConfig.cronExpression,image:properties.template.containers[0].image,env:properties.template.containers[0].env}"
az containerapp job execution list --name fullaura-demo-purge --resource-group rg-fullaura-demo --subscription <subscription ID> --output table
```

To check access without deleting anything before the purge time, start a run with `az containerapp job start` and read its log line.

Change the pilot's end only by deploying the template again with a later `pilotEndsAt`, `recordsPurgeAt` and `recordsPurgeSchedule`. The job keeps its own copy of the purge time: changing `FULLAURA_PILOT_ENDS_AT` on the app alone would let the job delete the quotas and the ledger while the pilot still runs, and the empty ledger would reopen the budgets.

## Deployment procedure

The template `infra/azure/main.bicep` describes every resource in `rg-fullaura-demo`: the Log Analytics workspace, the Container Apps environment, the app with its system-assigned managed identity, the private storage account for pilot records with its 30-day deletion rule, the purge job that deletes the records after the pilot, and the role assignments that let the app's and the job's identities read, write and delete the blobs in the `pilot` container. Deploy it to the existing resource group so that it updates these resources and creates no second stack. The Azure CLI's default subscription is another one, so every command names the FullAura subscription.

1. Build and smoke-test the image as described in `README.md`, then build it for `linux/amd64`, push it to `ghcr.io/vbabenko97/fullaura` and note its digest and source commit.
2. Check the subscription, its spending limit and credit, and the provider prices and terms named in [BETA.md](BETA.md#costs). Azure budgets notify; they do not stop spending.
3. Preview the change. The secure parameters come from the operator's environment and ignored files, never from the repository:

   ```bash
   az deployment group what-if --subscription <subscription ID> --resource-group rg-fullaura-demo \
     --template-file infra/azure/main.bicep --parameters containerImage=ghcr.io/vbabenko97/fullaura@<app digest> \
     purgeJobImage=ghcr.io/vbabenko97/fullaura@<job digest> betaEnabled=true pilotEndsAt=<ISO date-time with zone> recordsPurgeAt=<later ISO date-time with zone> \
     recordsPurgeSchedule=<cron in UTC> planningBudgetUsd=<USD> imageBudgetUsd=<USD> \
     basetenApiKey=<key> modelApiKey=<key> \
     sessionSecret=<secret> inviteHashes=<comma-separated digests>
   ```

   Both images are required and pinned separately ([D-51](DECISIONS.md#d-51-the-app-and-the-purge-job-have-their-own-images-and-roll-back-apart)). The job's image must contain a `backend.app.purge` that writes the closure marker; no image before this release does. `pilotEndsAt` and `recordsPurgeAt` are required while the beta is enabled; the app refuses to start without them or with a purge time that is not after the end. Expect changes only to the app, the purge job, the storage resources and the role assignments. Stop if the preview deletes or recreates the environment, the workspace or the storage account.
4. Run the same command with `az deployment group create`. A deployment keeps the session secret only if the same value is passed again; a new secret signs out every participant.
5. Wait for the role assignment to take effect, which can take a few minutes, then check `/`, `/api/health`, `/api/ready`, `/api/session` and a JSON 404 for an unknown `/api` route with `scripts/smoke_http.py <URL>`, and open the operator's invite to check planning and a try-on.
6. Record the URL, source commit, resource names, both deployed digests and the app's previous working digest above.

To deploy only a new app image, run step 1 and then `az containerapp update --name fullaura-demo --resource-group rg-fullaura-demo --subscription <subscription ID> --image ghcr.io/vbabenko97/fullaura@<digest>`. In single-revision mode this creates a new revision, moves all traffic to it, and keeps the environment variables and secrets. Change the purge job's image only to an image that writes the closure marker, with `az containerapp job update --name fullaura-demo-purge --resource-group rg-fullaura-demo --subscription <subscription ID> --image ghcr.io/vbabenko97/fullaura@<digest>`; that keeps the job's schedule and settings.

## Rolling back

The app and the purge job roll back separately, each to the digest recorded for it in the table above ([D-51](DECISIONS.md#d-51-the-app-and-the-purge-job-have-their-own-images-and-roll-back-apart)).

- **App.** Roll back only to the recorded app rollback image, and switch the beta off in the same command, because earlier app images write without checking the closure marker:

  ```bash
  az containerapp update --name fullaura-demo --resource-group rg-fullaura-demo --subscription <subscription ID> \
    --image ghcr.io/vbabenko97/fullaura@<app rollback digest> --set-env-vars FULLAURA_BETA_ENABLED=false
  ```

  One command makes one revision with both changes, so no revision of the earlier image serves the beta. It keeps the other settings, the secrets with the invites, the budgets and the pilot's times. Invited people see that the beta is closed; plans saved in their browsers stay there. Check it with `scripts/smoke_http.py <URL>`, which must report a closed beta. To return, run the same command with the current app digest and `FULLAURA_BETA_ENABLED=true`.
- **Purge job.** Leave it alone: rolling back the app does not change the job's image, schedule or settings. No earlier image can run the job. The image of `00c7e2f` has no `backend.app.purge`, so every run would fail, and the image of `a7eba24` deletes without the closure marker.
- **Template.** A deployment of the template always names both images. For a rollback, pass the app rollback digest as `containerImage`, `betaEnabled=false`, and the current job image as `purgeJobImage`. Pass every other parameter with its current value, as in step 3 of the deployment procedure: the pilot times, the schedule, the budgets, both provider keys, the same `sessionSecret` and the same `inviteHashes`. The template drops a secret that is passed empty, and a new session secret signs every participant out.

On 2026-10-31 the earlier app images, of `a7eba24` and of `00c7e2f`, were run locally as production with the deployed pilot times and budgets, synthetic invite digests and the beta off. Both passed `scripts/smoke_http.py` as a closed beta, answered 404 to the invite, age, consent, feedback and catalog routes, and wrote no file to their store folder. Neither image could import the purge job with its closure marker.

## Stopping the beta

- Close model calls and the beta's routes at once: `az containerapp update --name fullaura-demo --resource-group rg-fullaura-demo --subscription <subscription ID> --set-env-vars FULLAURA_BETA_ENABLED=false`. Plans saved on participants' devices stay readable in their browsers.
- Stop serving altogether: `az containerapp ingress disable --name fullaura-demo --resource-group rg-fullaura-demo --subscription <subscription ID>`.
- Delete every pilot record before the purge job does: `az storage blob delete-batch --account-name <storage account> --source pilot --auth-mode login --subscription <subscription ID>`. The operator needs the Storage Blob Data Contributor role on the account.
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
