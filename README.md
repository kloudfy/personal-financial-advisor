# Personal Financial Advisor (Hackathon build on Bank of Anthos)

> **Fork notice:** This project is a fork of Google’s
> [Bank of Anthos](https://github.com/GoogleCloudPlatform/bank-of-anthos).
> We extend it with an agent-powered **Personal Financial Advisor** layer (MCP server + monitoring/insight agent)
> and GKE/Artifact Registry/Cloud SQL/IAM integrations.

## What we added/changed (high level)

* **Agents & MCP Server**

  * `src/mcp-server/` – Model Context Protocol server exposing BoA tools/signals to agents.
  * `src/transaction-monitoring-agent/` – monitoring/advisory flows (A2A).
  * `src/insight-agent/` – **new** service that summarizes spending and flags anomalies using either:

    * **Gemini API** (API key) — simple for demos.
    * **Vertex AI (recommended)** — Workload Identity, no keys.
  * `src/ai/agent-gateway/` – lightweight gateway that fronts the MCP server for clients.

* **Transaction History service fixes (Java / Spring Boot)**

  * Aligned with **Spring Boot 3.5.x + Jakarta 3.1**; resolved JPA/Hibernate proxy clashing.
  * Optional **quiet mode** in **dev** to disable Stackdriver metrics export.

* **Kubernetes overlays & config hygiene**

  * Kustomize overlays (`base`, `overlays/development`, `overlays/production`) following BoA conventions.
  * Dev overlay sets `MANAGEMENT_METRICS_EXPORT_STACKDRIVER_ENABLED=false` by default.

* **Security/ops hygiene**

  * Sensitive/testing helpers purged from history and `.gitignore`d.
  * Use **Workload Identity** (Option A) in prod; no SA keys in git.
  * Backlog item: migrate any local keys to **Secret Manager**.

---

## Quick start: Insight Agent

Build & deploy **one** of the variants below (Gemini API or Vertex AI).

### A) Gemini API (simple for demos)

```bash
export GCP_PROJECT=<project-id>
export AR_REPO=<artifact-registry-repo>
export REGION=us-central1
export REG="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}"

docker build -t ${REG}/insight-agent:gemini -f src/insight-agent/Dockerfile . 
docker push ${REG}/insight-agent:gemini

kubectl create secret generic gemini-api-key \
  --from-literal=api-key=<YOUR_GEMINI_API_KEY> \
  --dry-run=client -o yaml | kubectl apply -f -

sed "s|PROJECT/REPO|${GCP_PROJECT}/${AR_REPO}|g" kubernetes-manifests/insight-agent.yaml | kubectl apply -f -
kubectl get pods -l app=insight-agent
```

### B) Vertex AI (recommended)

```bash
export GCP_PROJECT=<project-id>
export AR_REPO=<artifact-registry-repo>
export REGION=us-central1
export REG="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}"

gcloud services enable aiplatform.googleapis.com --project=${GCP_PROJECT}

docker build -t ${REG}/insight-agent:vertex -f src/insight-agent/Dockerfile.vertex . 
docker push ${REG}/insight-agent:vertex

kubectl create configmap environment-config \
  --from-literal=GOOGLE_CLOUD_PROJECT=${GCP_PROJECT} \
  --dry-run=client -o yaml | kubectl apply -f -

sed "s|PROJECT/REPO|${GCP_PROJECT}/${AR_REPO}|g" kubernetes-manifests/insight-agent-vertex.yaml | kubectl apply -f -
kubectl get pods -l app=insight-agent
```

---

## 🚀 Deploying Agent Gateway + MCP Server

We provide **Kustomize overlays** that pin tested images in **Google Artifact Registry** so the demo works out-of-the-box.
For hackathon judges, no extra configuration is required.

### Quickstart (judge mode)

```bash
# Deploy dev overlay
kustomize build src/ai/mcp-server/k8s/overlays/development | kubectl apply -f - 
kustomize build src/ai/agent-gateway/k8s/overlays/development | kubectl apply -f -

# Wait for pods
kubectl rollout status deploy/mcp-server
kubectl rollout status deploy/agent-gateway

# Run smoke test
./scripts/smoke-dev.sh
```

Expected output:

```json
{"agent_id":"agent-123","location":"us-central1","project":"<your-project>","status":"ok"}
```

and a sample spend analysis.

### Overriding Images (portable mode)

To deploy your **own build** of the services:

```bash
REG=us-central1-docker.pkg.dev/myproj/myrepo
TAG=v0.2.0

# edit the dev overlay in-place (from the overlay directory)
# Example one-offs:
 kustomize edit set image agent-gateway=$REG/agent-gateway:$TAG
 kustomize edit set image mcp-server=$REG/mcp-server:$TAG
```

Then redeploy with the same `kustomize build … | kubectl apply -f -` commands above.

Or use `kubectl set image` directly for a one-off:

```bash
kubectl set image deploy/mcp-server mcp-server=$REG/mcp-server:$TAG
kubectl set image deploy/agent-gateway agent-gateway=$REG/agent-gateway:$TAG
```

### Why images are pinned

* Ensures judges can deploy without errors (`InvalidImageName` avoided).
* Provides reproducibility: these are the exact images that passed our smoke tests.
* Still flexible: one-liners above (or Make targets below) let you swap in your own builds quickly.

---

## 🧰 Make-based workflow (ergonomic pins + apply)

> **Prereqs:** `kustomize` installed; `kubectl` configured to your cluster; the images you reference exist in Artifact Registry.

We include convenience targets that **pin** images into the **dev overlays** using `kustomize edit set image`, then **apply**, **check status**, and **smoke test**.

Common variables (defaults shown; override on the command line):

* `PROJECT=gke-hackathon-469600`
* `REPO=bank-of-anthos-repo`
* `REGION=us-central1`
* `REG=$(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)`
* `MCP_TAG=v0.1.0`
* `AGW_TAG=v0.1.2`
* `NS=default`

### Typical flow

```bash
# 1) Pin to specific images (edits dev overlays in-place)
make set-images REG=us-central1-docker.pkg.dev/<proj>/<repo> MCP_TAG=v0.1.0 AGW_TAG=v0.1.2

# (optional) review and commit the changes
git add src/ai/**/overlays/development/kustomization.yaml
git commit -m "dev: pin mcp-server $(MCP_TAG) and agent-gateway $(AGW_TAG)"

# 2) Apply the dev overlays
make dev-apply

# 3) Verify rollouts
make dev-status

# 4) Run smoke tests
make dev-smoke

# Helpers
make show-images   # what’s live in the cluster
make show-pins     # what’s pinned in the dev overlay files
```

These targets update only the **dev** overlays; **base/prod** remain portable. Judges can deploy dev overlays without needing to build their own images.

---

## 🌐 Production Overlay

The `overlays/production` Kustomize configs add:

* **Workload Identity annotations** (pods run as GCP IAM service accounts, no keys in secrets).
* Optional **Ingress/IAP** for exposing agent-gateway externally with authentication.
* “Quiet mode” disabled — production metrics and logging enabled.

To deploy:

```bash
kustomize build src/ai/mcp-server/k8s/overlays/production | kubectl apply -f - 
kustomize build src/ai/agent-gateway/k8s/overlays/production | kubectl apply -f -
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

* **Immutable Kubernetes Selectors:** Be cautious when using Kustomize's `commonLabels`, as they can attempt to change a Deployment's `spec.selector`, which is an immutable field. It's safer to use patches to apply labels only to the pod template (`spec.template.metadata.labels`).
* **`ConfigMap` Updates Require Pod Restarts:** Changing a `ConfigMap` does not automatically trigger a rollout of the pods that use it. You must force a restart (e.g., `kubectl rollout restart deploy/...`) to ensure the pods pick up the new configuration.
* **Service Endpoint Stabilization:** Even after a deployment rollout reports success, there can be a brief delay before the Kubernetes Service is fully routing traffic to the new pods. Test scripts should include a short `sleep` or a retry loop to account for this.
* **Prefer `kubectl create job` for Tests:** For running non-interactive tasks in temporary pods (like smoke tests), `kubectl run` can have tricky flag combinations (`--rm`, `-i`). The most robust pattern is to use `kubectl create job`, `kubectl wait`, and then `kubectl logs` to avoid race conditions and warnings.
* **Workload Identity > Service Account Keys:** Always prefer Workload Identity for authenticating to Google Cloud services from GKE to avoid managing and securing service account keys.

---

© Forked from Google’s Bank of Anthos (Apache 2.0). See upstream repo for license details.

---