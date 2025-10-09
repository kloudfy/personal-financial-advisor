# Personal Financial Advisor (Hackathon build on Bank of Anthos)

> **Fork notice:** This project is a fork of Google’s
> [Bank of Anthos](https://github.com/GoogleCloudPlatform/bank-of-anthos).
> We extend it with an agent-powered **Personal Financial Advisor** layer (MCP server + monitoring/insight agent)
> and GKE/Artifact Registry/IAM integrations.

> **Judge-Friendly Quickstart:** See **[HACKATHON-DEMO-RUNBOOK.md](./HACKATHON-DEMO-RUNBOOK.md)** for a one-page,
> copy/paste run-through (from `git clone` → deploy → smokes → Vertex check).

---

## 🔧 Scripts-first smokes (recommended)

> **Note on Smoke Test Data:** These scripts use minimal, hardcoded data payloads
> to verify service health and API contracts. They are not intended to test the
> quality of the AI model's analysis and will not produce the same rich, data-driven
> output as the full end-to-end UI flow.

All smokes are tiny bash scripts in `scripts/` so they’re easy to run locally and from CI; Make targets are thin wrappers.

**Scripts**
- `scripts/core-smoke.sh` — healthz for core services + JWT fetch
- `scripts/data-smoke.sh` — MCP `/transactions` + transform → Budget Coach
- `scripts/chat-smoke.sh` — E2E `/chat` (JWT → Agent Gateway) with truncation
- `scripts/fraud-smoke.sh` — Fraud Scout (MCP → transform → `/api/fraud/detect`)
- `scripts/spending-smoke.sh` — Spending Analyst (MCP → transform → `/api/spending/analyze`)

**Make wrappers**
```bash
make smoke-core
make smoke-data
make smoke-e2e
make smoke-fraud
make smoke-spending
make smoke-fast     # bundle of core + data + e2e + fraud
make demo-check     # alias to smoke-fast
```
**Common env overrides (work with scripts & make)**
```bash
* NS (default: default)
* ACCT (default: 1011226111)
* WINDOW (default: 30)
* SMOKE_HEAD (default: 200) – first N bytes of /chat JSON
```
Examples:
```bash
NS=default ACCT=1011226111 WINDOW=30 make smoke-data
SMOKE_HEAD=400 make smoke-e2e
make demo-check
```

---

## 💸 Budget Coach UI (Streamlit)

A lightweight UI that lets judges run the **Budget Coach** flow end-to-end using live microservices.

**Flow:** `userservice → mcp-server → insight-agent (/api/*) → Vertex AI Gemini`

### 1) Port-forward cluster services

Open three terminals (or background them):

```bash
# userservice
kubectl -n default port-forward deploy/userservice 8081:8080
# mcp-server
kubectl -n default port-forward deploy/mcp-server 8082:8080
# insight-agent (Service on :80 → Pod :8080)
kubectl -n default port-forward svc/insight-agent 8083:80
```
Then export local endpoints so the UI talks to your port-forwards (run these in the same shell where you launch Streamlit):

```bash
# 🧪 Local UI expects these variable names:
export USERSVC=http://localhost:8081
export MCPSVC=http://localhost:8082

# Choose one (Vertex build vs legacy):
# Vertex build (endpoint is /api/budget/coach)
export INSIGHT=http://localhost:8083/api
# Legacy build (endpoint is /budget/coach)
# export INSIGHT=http://localhost:8083
```

### 2) Launch UI

From repo root (Python ≥3.11 and pip installed):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r ui/requirements.txt
streamlit run ui/budget_coach_app.py 
```

Open: **[http://localhost:8501](http://localhost:8501)** and Click **Generate Budget Plan**.

> We trimmed Cloud SQL from this quickstart description; current path uses **MCP → Insight Agent → Vertex AI**.
> Balance display is **optional** in P0 (guarded): if an endpoint isn’t available, the UI still renders the plan.

### What you’ll see
* **Header:** `Account`, `Window`, optional **Current Balance**, and Latest Txn date
* **Summary:** 1-paragraph overview of income/spend
* **Budget Buckets:** 4–6 categories with `%` and monthly estimate
* **Tips:** 3–5 short recommendations
* **Raw JSON:** the API response (collapsed by default in P1)



---

## 🌐 Judges Demo: External IP (UI)

If you’ve created the optional external LB for the UI (use `make ui-judges-apply`), the URL is:

```bash
kubectl get svc budget-coach-ui-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}{"\n"}'
```
(If it outputs nothing, the LoadBalancer may still be provisioning; re-run in a moment.)

Visit the External IP printed by make ui-judges-ip.

*(Example: [http://136.115.99.13](http://136.115.99.13))*

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
    │       ├── base/{deployment.yaml,kustomization.yaml,service.yaml}
    │       └── overlays/
    │           ├── development/{kustomization.yaml,patch-dev.yaml,patch-dev-env.yaml,sa-wi.yaml}
    │           └── production/{kustomization.yaml,patch-prod.yaml}
    └── mcp-server
        ├── Dockerfile
        ├── k8s/
        │   ├── base/{deployment.yaml,kustomization.yaml,service.yaml}
        │   └── overlays/
        │       ├── development/{kustomization.yaml,patch-dev.yaml,patch-dev-env.yaml,patch-probes.yaml}
        │       └── production/{kustomization.yaml,patch-prod.yaml}
        ├── main.py
        └── requirements.txt
```

> **Note on legacy dirs:** You may also see top-level legacy folders (`insight-agent/`, `mcp-server/`, `transaction-monitoring-agent/`). Kept temporarily during consolidation; plan to remove on the `hackathon-submission` branch.

---

## What we added/changed (high level)
* **`insight-agent` Refactor:**
  * Migrated from Flask to FastAPI for a modern, async-first framework.
  * Replaced the deprecated `google-cloud-aiplatform` library with the recommended `google-genai` SDK.
  * Implemented a robust resilience strategy with rate limiting, concurrency control, and exponential backoff.
  * Added prompt provenance via the `X-Insight-Prompt` header.
  * Decoupled prompts from the application code using a `ConfigMap`.
* **Agents & MCP Server**
  * `src/ai/mcp-server/` – Model Context Protocol server exposing BoA tools/signals to agents.
  * `src/ai/agent-gateway/` – lightweight gateway that fronts the MCP server for clients.
  * `src/ai/insight-agent/` – new service that builds a budget plan (summary + buckets + tips) via Vertex AI.
* **Kubernetes overlays & config hygiene**
  * Kustomize overlays (`base`, `overlays/development`, `overlays/production`) following BoA conventions.
  * Dev overlays keep things quiet (e.g., metrics export off where supported).
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

We ship a prebuilt **vertex-smoke** image to avoid slow `pip install` inside ephemeral pods.
1.	Build & push once:
```bash
make vertex-smoke-image PROJECT=${PROJECT} REG=us-central1-docker.pkg.dev/${PROJECT}/bank-of-anthos-repo
```
2.	Run the smoke (fast & reliable):
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

Clean up demo resources:
```bash
make demo-clean
```

Rollout status & smokes only:
```bash
make dev-apply
make dev-status
make dev-smoke
```

---

## 🔐 Authenticated E2E Smoke (userservice → agent-gateway → mcp → transactionhistory)

A job-based smoke fetches a JWT from `userservice` and calls `agent-gateway`:
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
gcloud auth configure-docker ${REGION}-docker.pkg.dev
```

### Option A: Gemini API variant (simple for demos)
1.	Build, push, deploy, and tail logs:

```bash
make deploy-insight-agent-dev INS_TAG=gemini
```

2.	If your image reads a `GEMINI_API_KEY` from a Secret, create it (example):

```bash
# If using a Secret for API key:
kubectl create secret generic gemini-api-key \
  --from-literal=api-key=<YOUR_GEMINI_API_KEY> \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Option B: Vertex AI variant (recommended)

Ensure the Vertex API is enabled and that your GKE workload has Workload Identity configured to call Vertex:
```bash
gcloud services enable aiplatform.googleapis.com --project=${PROJECT}
make deploy-insight-agent-vertex INS_TAG=vertex
```

> The **production** overlay can add Workload Identity annotations and stricter runtime policies.

## 🧱 Dev overlays for mcp-server and agent-gateway

To apply both dev overlays and force an agent-gateway restart to pick up ConfigMap changes:
```bash
make dev-apply
make dev-status
```

Images pinned in overlays can be updated via:
```bash
make set-images MCP_TAG=v0.1.1 AGW_TAG=v0.1.5 PROJECT=${PROJECT} REPO=${REPO}
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
* Optional Ingress/IAP for exposing agent-gateway externally with authentication.
* “Quiet mode” disabled — production metrics and logging enabled.

Deploy per service:

```bash
kustomize build src/ai/mcp-server/k8s/overlays/production | kubectl apply -f -
kustomize build src/ai/agent-gateway/k8s/overlays/production | kubectl apply -f -
kustomize build src/ai/insight-agent/k8s/overlays/production | kubectl apply -f -
```

---

## Releasing New Images (dev overlay)

Typical flow for the `insight-agent`:
```bash
# 1. Build and push the image with a new tag
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
export INSIGHT_AGENT_TAG=v3.1.0 # or some other new tag
make insight-dev-build DEV_TAG=${INSIGHT_AGENT_TAG}

# 2. Update the dev overlay to use the new tag
(cd src/ai/insight-agent/k8s/overlays/development && \
  kustomize edit set image insight-agent=${REG}/insight-agent:${INSIGHT_AGENT_TAG})

# 3. Apply the changes and verify the rollout
make insight-dev-rollout DEV_TAG=${INSIGHT_AGENT_TAG}
make dev-smoke
```

Prefer digests for critical paths: `insight-agent@sha256:<digest>`.

---

## Project Status Update: `insight-agent` Refactor and Modernization

The `insight-agent` service has been completely refactored and modernized. Highlights:
* **FastAPI Migration:** The `insight-agent` has been successfully migrated from Flask to FastAPI.
* **`google-genai` SDK:** The application now uses the recommended `google-genai` SDK.
* **Resilience and Performance:** We have implemented a robust resilience strategy with rate limiting, concurrency control, and exponential backoff.
* **Provenance and Decoupling:** The application now includes prompt provenance in the `X-Insight-Prompt` header and the prompts are decoupled from the application code using a `ConfigMap`.
* **UI Fixes:** The UI has been updated to work with the new backend and all known issues have been resolved.

---

## Lessons learned (short)
* **Immutable `spec.selector`:** Don’t patch Deployment selectors; patch pod template labels instead.
* **`ConfigMap` changes need restarts:** Use `kubectl rollout restart deploy/...` or use a hash in the `ConfigMap` name to trigger a rolling update.
* **Service stabilization & retries:** After rollout “`Success`,” allow DNS/NEG to settle; keep a short retry loop.
* **Prefer `Job` for tests:** `Job + wait + logs` avoids attach races in CI.
* **Workload Identity > SA keys:** Prefer WI for GKE.
* **Prebuilt smoke images:** Shipping the **vertex-smoke** image removed transient timeout issues on Autopilot.
* **API contract changes:** When migrating a backend, be mindful of the API contract with the frontend.
* **Kustomize image pinning:** Be aware of how Kustomize overlays pin images and how to update them.

---

## Architecture

```
                        +------------------+
                        |                  |
                        |   Browser / UI   |
                        |  (Streamlit)     |
                        |                  |
                        +--------+---------+
                                 |
                                 | HTTP/REST
                                 |
                        +--------v---------+
                        |                  |
                        |  Agent Gateway   |
                        |  (Flask)         |
                        |                  |
                        +--------+---------+
                                 |
                                 | gRPC
                                 |
+-----------------+     +--------v---------+      +-----------------+
|                 |     |                  |      |                 |
|  userservice    +----->   mcp-server     <------>  transaction-   |
|  (Java)         |     |   (Python)       |      |   history       |
|                 |     |                  |      |   (Java)        |
+-----------------+     +--------+---------+      +-----------------+
                                 |
                                 | gRPC
                                 |
                        +--------v---------+
                        |                  |
                        |  insight-agent   |
                        |  (FastAPI)       |
                        |                  |
                        +--------+---------+
                                 |
                                 | HTTPS
                                 |
                        +--------v---------+
                        |                  |
                        |  Vertex AI       |
                        |  (Gemini)        |
                        |                  |
                        +------------------+
```

**Gateway & MCP path**
```bash
client → agent-gateway (svc:port 80) → mcp-server (svc:80) → transactionhistory (svc:8080) → Cloud SQL
```
**Insight path (Vertex mode)**
```bash
agent-gateway → insight-agent (svc:80→pod:8080, KSA=insight-agent) --WI→ GSA=insight-agent@${PROJECT}.iam.gserviceaccount.com → Vertex AI (Gemini)
```
**Notes**
* AGW is exposed internally via K8s Service on **port 80** (smokes use this).
* `mcp-server` is exposed internally via K8s Service on **port 80**.
* WI binding: roles/iam.workloadIdentityUser on the GSA, plus `roles/aiplatform.user` for Vertex.
* Images come from Artifact Registry (`${REGION}-docker.pkg.dev/${PROJECT}/${REPO}`).

---

© Forked from Google’s Bank of Anthos (Apache 2.0). See upstream repo for license details.
