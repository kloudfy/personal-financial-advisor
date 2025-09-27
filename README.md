# Personal Financial Advisor (Hackathon build on Bank of Anthos)

> **Fork notice:** This project is a fork of Google’s
> [Bank of Anthos](https://github.com/GoogleCloudPlatform/bank-of-anthos).
> We extend it with an agent-powered **Personal Financial Advisor** layer (MCP server + monitoring/insight agent)
> and GKE/Artifact Registry/Cloud SQL/IAM integrations.

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

---

© Forked from Google’s Bank of Anthos (Apache 2.0). See upstream repo for license details.

---