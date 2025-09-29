# Personal Financial Advisor (Hackathon build on Bank of Anthos)

> **Fork notice:** This project is a fork of Google’s
> [Bank of Anthos](https://github.com/GoogleCloudPlatform/bank-of-anthos).
> We extend it with an agent-powered **Personal Financial Advisor** layer (MCP server + monitoring/insight agent)
> and GKE/Artifact Registry/Cloud SQL/IAM integrations.

> **Judge-Friendly Quickstart:** See **[HACKATHON-DEMO-RUNBOOK.md](./HACKATHON-DEMO-RUNBOOK.md)** for a one-page,
> copy/paste run-through (from `git clone` → deploy → smokes → Vertex check).

---

## Quick Start (Dev)

```bash
# deploy dev overlays and run smokes
make demo

# or run only the smokes after you’ve deployed
make dev-smoke
make e2e-auth-smoke   # userservice → agent-gateway → mcp → transactionhistory
```

The authenticated smoke uses a **retry loop** for the AGW `/chat` call (port **80**)
to absorb brief cold-start/propagation 5xx.

---

## Repository layout (AI additions)

We follow a **Kustomize base + overlays** pattern for each service under `src/ai/*`.

```
src/
└── ai
    ├── agent-gateway
    │   ├── Dockerfile
    │   ├── k8s/
    │   │   ├── base/{deployment.yaml,kustomization.yaml}
    │   │   └── overlays/
    │   │       ├── development/{kustomization.yaml,patch-dev.yaml,patch-dev-env.yaml}
    │   │       └── production/{kustomization.yaml,patch-prod.yaml}
    │   ├── main.py
    │   └── requirements.txt
    ├── insight-agent
    │   └── k8s/
    │       ├── base/{deployment.yaml,kustomization.yaml}
    │       └── overlays/
    │           ├── development/{kustomization.yaml,patch-dev.yaml,patch-dev-env.yaml}
    │           └── production/{kustomization.yaml,patch-prod.yaml}
    └── mcp-server
        ├── Dockerfile
        ├── k8s/
        │   ├── base/{deployment.yaml,kustomization.yaml}
        │   └── overlays/
        │       ├── development/{kustomization.yaml,patch-dev.yaml,patch-dev-env.yaml}
        │       └── production/{kustomization.yaml,patch-prod.yaml}
        ├── main.py
        └── requirements.txt
```

> **Note on legacy directories:** You may also see top-level legacy folders (e.g. `insight-agent/`, `mcp-server/`, `transaction-monitoring-agent/`). These are kept temporarily for reference during the merge; plan to remove them on the `hackathon-submission` branch.

---

## What we added/changed (high level)

* **Agents & MCP Server**
  * `src/ai/mcp-server/` – Model Context Protocol server exposing BoA tools/signals to agents.
  * `src/ai/agent-gateway/` – lightweight gateway that fronts the MCP server for clients.
  * `src/ai/insight-agent/` – **new** service that summarizes spend/flags anomalies via:
    * **Gemini API** (API key) — simple for demos.
    * **Vertex AI (recommended)** — Workload Identity, no keys.

* **Kubernetes overlays & config hygiene**
  * Kustomize overlays (`base`, `overlays/development`, `overlays/production`) following BoA conventions.
  * Dev overlays keep things quiet (e.g., optional metrics export disabled where supported).

* **Security/ops hygiene**
  * Prefer **Workload Identity**; avoid SA keys in git.
  * Backlog: migrate any local keys to **Secret Manager**.

---

## Prerequisites

* `gcloud` (authenticated to your GCP project)
* `docker` (Buildx optional)
* `kubectl` (pointed at your GKE cluster)
* `kustomize`
* Artifact Registry repo created (e.g. `bank-of-anthos-repo`)

Set common environment variables (or use `Makefile` defaults):

```bash
export PROJECT=<gcp-project-id>
export REGION=us-central1
export REPO=bank-of-anthos-repo
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
```

---

## Vertex AI Integration

The insight-agent can connect to Google Vertex AI using Workload Identity (WI).  
Use the provided Makefile targets to bootstrap WI, deploy the agent, and verify connectivity.

### Vertex Smoke Test (prebuilt image)

To avoid slow `pip install` inside ephemeral pods, we now ship a prebuilt **vertex-smoke** image.

1. Build & push the smoke image (one time):
   ```bash
   make vertex-smoke-image \
     PROJECT=${PROJECT} \
     REPO=bank-of-anthos-repo \
     REG=us-central1-docker.pkg.dev/${PROJECT}/bank-of-anthos-repo
   ```

2. Run the smoke test (fast and reliable):
   ```bash
   make vertex-smoke
   ```

---

## 🚀 One-command Demo

The `Makefile` provides a simple, one-command workflow that applies dev overlays and runs smoke tests.

```bash
make            # same as 'make demo'
# or
make demo
```

Clean up the demo resources:

```bash
make demo-clean
```

Check rollout status and run smoke only:

```bash
make dev-apply
make dev-status
make dev-smoke
```

---

## 🔐 Authenticated E2E Smoke (userservice → agent-gateway → mcp → transactionhistory)

We include a job-based smoke test that fetches a JWT from `userservice` and calls `agent-gateway`:

```bash
make e2e-auth-smoke
```

> Requires the BoA demo user and the `jwt-key` secret (per upstream).
> If you’ve ever deleted `jwt-key`, recreate it via the upstream `extras/jwt/jwt-secret.yaml`.

---

## 📦 Build & Deploy: insight-agent (dev overlay)

We’ve added convenience targets to streamline **build → push → apply → watch logs** for `insight-agent`.

### Configure Artifact Registry auth (once)

```bash
make ar-login
```

### Option A: Gemini API variant (simple for demos)

1. Build, push, deploy, and tail logs:

```bash
make deploy-insight-agent-dev INS_TAG=gemini
```

2. If your image reads a `GEMINI_API_KEY` from a Secret, create it (example):

```bash
kubectl create secret generic gemini-api-key \
  --from-literal=api-key=<YOUR_GEMINI_API_KEY> \
  --dry-run=client -o yaml | kubectl apply -f - 
```

### Option B: Vertex AI variant (recommended)

Ensure the Vertex API is enabled and that your GKE workload has Workload Identity configured to call Vertex:

```bash
gcloud services enable aiplatform.googleapis.com --project=${PROJECT}
make deploy-insight-agent-dev INS_TAG=vertex
```

> The **production** overlay can add Workload Identity annotations and stricter runtime policies.

### Other handy targets

```bash
# Build/push only
make insight-build INS_TAG=gemini
make insight-push  INS_TAG=gemini

# Apply manifests only (no build/push)
make insight-apply

# Rollout status and logs
make insight-status
make insight-logs
```

---

## 🧱 Dev overlays for mcp-server and agent-gateway

To apply both dev overlays and force an agent-gateway restart to pick up ConfigMap changes:

```bash
make dev-apply
make dev-status
```

Images pinned in overlays can be updated via:

```bash
make set-images MCP_TAG=v0.1.0 AGW_TAG=v0.1.2 PROJECT=${PROJECT} REPO=${REPO}
make show-pins
```

Show images currently running:

```bash
make show-images
```

---

## 🌐 Production overlay

The `overlays/production` Kustomize configs add, where applicable:

* **Workload Identity** annotations (pods run as GCP IAM service accounts; no keys in secrets).
* Optional **Ingress/IAP** for exposing agent-gateway externally with authentication.
* “Quiet mode” disabled — production metrics and logging enabled.

Deploy (per service):

```bash
kustomize build src/ai/mcp-server/k8s/overlays/production | kubectl apply -f -
kustomize build src/ai/agent-gateway/k8s/overlays/production | kubectl apply -f -
kustomize build src/ai/insight-agent/k8s/overlays/production | kubectl apply -f -
```

---

## Releasing New Images (dev overlay)

We pin images per overlay. Typical flow:

```bash
# 1) Build & push a fresh tag (example: frontend v0.1.3)
export PROJECT=<gcp-project-id>
export REGION=us-central1
export REPO=bank-of-anthos-repo
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
export FRONTEND_TAG=v0.1.3

docker buildx build --platform linux/amd64 \
  -t ${REG}/frontend:${FRONTEND_TAG} \
  -f src/frontend/Dockerfile src/frontend --push

# 2) Pin the tag in the dev overlay
(cd src/frontend/k8s/overlays/development && \
  kustomize edit set image frontend=${REG}/frontend:${FRONTEND_TAG})

# 3) Apply + restart + verify
kustomize build src/frontend/k8s/overlays/development | kubectl apply -f -
kubectl -n default rollout restart deploy/frontend
kubectl -n default rollout status  deploy/frontend
make dev-smoke
```

To avoid tag ambiguity in critical paths, you can also lock to a **digest**:
use `frontend @sha256:<digest>` instead of a tag.

---

## Project Status Update: Application Stabilization and Bug Fixes

The app is fully functional; all smokes pass. Highlights:

* **Reliable smokes:** `scripts/e2e-smoke.sh` with authenticated flow + retry loop.
* **MCP ↔ AGW:** `agent-gateway` now calls `GET /transactions/<acct>` and forwards JWTs.
* **Auth path fixed:** Smokes fetch valid JWTs and use the correct account id for authorization.
* **transactionhistory WI:** Workload Identity + Monitoring/Trace roles; pod stable.
* **TMA boot:** `transaction-monitoring-agent` waits on userservice readiness.
* **Frontend config:** Clean service addresses (`servicename:port`) and var names aligned.
* **Ledgerwriter fix:** Env mapping for `BALANCES_API_ADDR` from `service-api-config`.

---

## Submission checklist (reminder)

* Hosted URL (frontend/agent gateway, or minimal API + short demo flow)
* Text description of features, stack (GKE, AR, WI, MCP, Vertex), data sources
* Public code repo URL
* Architecture diagram
* ~3-minute demo video
* Bonus: blog/video write-up; social post with **#GKEHackathon** or **#GKETurns10**

---

## Lessons learned (short)

* **Immutable Kubernetes Selectors:** Be cautious when using Kustomize’s `commonLabels`, as they can attempt to change a Deployment’s `spec.selector`, which is an immutable field. Prefer patches to apply labels only to the pod template (`spec.template.metadata.labels`).
* **`ConfigMap` updates require pod restarts:** Changing a `ConfigMap` doesn’t trigger a rollout. Use `kubectl rollout restart deploy/...`.
* **Service endpoint stabilization:** After a rollout shows “success,” allow a brief delay before testing; add a small `sleep` or retry loop.
* **Prefer `kubectl create job` for tests:** For short-lived non-interactive checks, `Job + wait + logs` avoids attach races and warnings.
* **Workload Identity > SA keys:** Prefer WI for GKE to avoid managing service account keys.
* **Built-in retries for smokes:** Transient 5xx (cold starts, DNS/cache propagation) are normal right after rollouts. The smoke’s retry wrapper (and using **port 80** for AGW) removes flakes without masking real issues.
* **Prebuilding the **vertex-smoke** image removed transient timeout issues.**  
  Installing dependencies at runtime (`pip install` inside the Job) was too slow on GKE Autopilot.  
  By shipping a tiny prebuilt image, the smoke job now runs instantly and reliably.

---

--- 

## Architecture (quick callouts)

**Gateway & MCP path**
```
client → agent-gateway (svc:port 80) → mcp-server (svc:8080) → transactionhistory (svc:8080) → Cloud SQL
```

**Insight path (Vertex mode)**
```
agent-gateway → insight-agent (svc:8080, KSA=insight-agent) --WI→ GSA=insight-agent @${PROJECT}.iam.gserviceaccount.com → Vertex AI (Gemini)
```

**Notes**
* AGW listens via K8s Service on **port 80** (the smokes use this).
* WI binding: `roles/iam.workloadIdentityUser` on the GSA, plus `roles/aiplatform.user` for Vertex.
* Images come from Artifact Registry (`${REGION}-docker.pkg.dev/${PROJECT}/${REPO}`).

---

© Forked from Google’s Bank of Anthos (Apache 2.0). See upstream repo for license details.