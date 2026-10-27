# Azure deployment plan

**Status:** a Dockerfile and CI build are provided. No Azure resources, deployments, or cloud credentials have been created by this scaffold. The separate feasibility script has made Meta requests; see [FEASIBILITY.md](FEASIBILITY.md).

## Initial configuration

Use one Azure Container App on a Consumption workload profile. FastAPI serves the compiled frontend and `/api` on port 8000. All future Meta requests originate on the backend.

| Setting | Proposed initial value |
| --- | --- |
| Region | West Europe, subject to subscription availability and quota |
| Compute | 0.5 vCPU / 1 GiB; revise after measuring image-request memory |
| Ingress | External HTTPS; target port 8000 |
| Revisions | Single active revision |
| Replicas | Minimum 0, maximum 1 |
| Probes | HTTP `/api/health` for liveness; `/api/ready` for readiness, port 8000 |
| Container image | Built from a recorded Git commit; deploy by immutable image digest |
| Secrets | Container App secrets; inject into backend environment variables |

Scaling to zero reduces idle application usage but introduces a cold start. A replica limit is not a spending cap. Logging, storage, registry, and network charges must also be checked. The Azure and Meta credits are separate budgets; neither balance has been verified.

Use a public GHCR image containing code and compiled assets if available. Never bake `.env`, customer images, or provider keys into an image. No publishing or deployment workflow runs automatically in this repository.

## Information needed for the first deploy

- Azure subscription ID, active credit expiry, and permitted region.
- A dedicated resource group name (suggested: `rg-fullaura-demo`).
- An authenticated Azure session with permission to create Container Apps resources.
- A reachable container image and the tested commit/digest.
- For model features: Meta API access, Standard-mode model IDs, confirmed credits, and a backend secret named `MODEL_API_KEY`. Enter secret values directly in local configuration or Azure secret settings.

## Deployment sequence

1. Build and smoke-test the image as described in `README.md`.
2. Inspect the subscription, region quota, and proposed resources. Configure a budget alert; verify the credit offer's actual spending-limit behavior separately.
3. Publish the image to the chosen registry and record its digest.
4. Create the dedicated Container Apps environment and app with the settings above. Configure explicit HTTP probes; Docker `HEALTHCHECK` alone does not configure Azure probes.
5. Verify the HTTPS root page, frontend assets, `/api/health`, `/api/ready`, and a JSON 404 for an unknown `/api` route.
6. Add model secrets only when the provider adapter exists. Before public model access, implement bounded request/image usage and the photo retention/deletion flow.
7. Record the URL, commit, resource names, and rollback image digest in this document. Review usage after the first real session and after the demo.

Container-local files are ephemeral. The scaffold stores no photographs. If the MVP needs persistent image storage, add private Azure Blob Storage with short-lived access and explicit deletion, then document that behavior before accepting uploads.

## Official references

Reviewed 26 October 2026:

- [Container Apps scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)
- [Container Apps health probes](https://learn.microsoft.com/en-us/azure/container-apps/health-probes)
- [FastAPI container deployment](https://fastapi.tiangolo.com/deployment/docker/)
- [Meta API documentation](https://dev.meta.ai/docs/overview)

Resource provisioning and end-to-end Azure validation remain pending.
