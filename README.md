# Personal Financial Advisor (Hackathon build on Bank of Anthos)

> **Fork notice:** This project is a fork of Google’s
> [Bank of Anthos](https://github.com/GoogleCloudPlatform/bank-of-anthos).
> We extend it with an agent-powered **Personal Financial Advisor** layer (MCP server + monitoring/insight agent)
> and GKE/Artifact Registry/IAM integrations.

> **Judge-Friendly Quickstart:** See **[HACKATHON-DEMO-RUNBOOK.md](./HACKATHON-DEMO-RUNBOOK.md)** for a one-page,
> copy/paste run-through (from `git clone` → deploy → smokes → Vertex check).

---

## 🔧 Scripts-first smokes (recommended)

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

Common env overrides (work with scripts & make)
	•	NS (default: default)
	•	ACCT (default: 1011226111)
	•	WINDOW (default: 30)
	•	SMOKE_HEAD (default: 200) – first N bytes of /chat JSON

Examples:

NS=default ACCT=1011226111 WINDOW=30 make smoke-data
SMOKE_HEAD=400 make smoke-e2e
make demo-check


⸻

💸 Budget Coach UI (Streamlit)

A lightweight UI that lets judges run the Budget Coach flow end-to-end using live microservices.

Flow: userservice → mcp-server → insight-agent (/api/budget/coach in Vertex build; /budget/coach in legacy) → Vertex AI Gemini

1) Port-forward cluster services

Open three terminals (or background them):

# userservice
kubectl -n default port-forward deploy/userservice 8081:8080
# mcp-server
kubectl -n default port-forward deploy/mcp-server 8082:8080
# insight-agent (Service on :80 → Pod :8080)
kubectl -n default port-forward svc/insight-agent 8083:80

Then export local endpoints so the UI talks to your port-forwards (run these in the same shell where you launch Streamlit):

# 🧪 Local UI expects these variable names:
export USERSVC=http://localhost:8081
export MCPSVC=http://localhost:8082

# Choose one (Vertex build vs legacy):
# Vertex build (endpoint is /api/budget/coach)
export INSIGHT=http://localhost:8083/api
# Legacy build (endpoint is /budget/coach)
# export INSIGHT=http://localhost:8083

2) Launch UI

From repo root (Python ≥3.11 and pip installed):

python3 -m venv .venv && source .venv/bin/activate
pip install -r ui/requirements.txt
streamlit run ui/budget_coach_app.py 

Open: http://localhost:8501 and Click Generate Budget Plan.

We trimmed Cloud SQL from this quickstart description; current path uses MCP → Insight Agent → Vertex AI.
Balance display is optional in P0 (guarded): if an endpoint isn’t available, the UI still renders the plan.

What you’ll see
	•	Header: Account, Window, optional Current Balance, and Latest Txn date
	•	Summary: 1-paragraph overview of income/spend
	•	Budget Buckets: 4–6 categories with % and monthly estimate
	•	Tips: 3–5 short recommendations
	•	Raw JSON: the API response (collapsed by default in P1)

Fast-mode (default on insight-agent)
	•	Compacts transactions, requests strict JSON from Gemini
	•	Normalizes bucket percentages to ≈100
	•	In-pod TTL cache (default 180s) for instant repeats

Tune at runtime:

kubectl set env deploy/insight-agent INSIGHT_FAST_MODE=true INSIGHT_CACHE_TTL_SEC=180
kubectl -n default rollout restart deploy/insight-agent
kubectl -n default rollout status deploy/insight-agent


⸻

🌐 Judges Demo: External IP (UI)

If you’ve created the optional external LB for the UI (use make ui-judges-apply), you can hand judges a URL:

kubectl get svc budget-coach-ui-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}{"\n"}'

(If it outputs nothing, the LoadBalancer may still be provisioning; re-run in a moment.)

Visit the External IP printed by make ui-judges-ip.

(Example: http://34.44.9.68)

⸻

Quick Start (Dev)

# deploy dev overlays and run smokes
make demo

# or run only the smokes after you’ve deployed
make dev-smoke
make e2e-auth-smoke   # userservice → agent-gateway → mcp → transactionhistory

The authenticated smoke uses a retry loop for the AGW /chat call (port 80)
to absorb brief cold-start/propagation 5xx.

⸻

Repository layout (AI additions)

We follow a Kustomize base + overlays pattern for each service under src/ai/*.

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

Note on legacy dirs: You may also see top-level legacy folders (insight-agent/, mcp-server/, transaction-monitoring-agent/). Kept temporarily during consolidation; plan to remove on the hackathon-submission branch.

⸻

What we added/changed (high level)
	•	Agents & MCP Server
	•	src/ai/mcp-server/ – Model Context Protocol server exposing BoA tools/signals to agents.
	•	src/ai/agent-gateway/ – lightweight gateway that fronts the MCP server for clients.
	•	src/ai/insight-agent/ – new service that builds a budget plan (summary + buckets + tips) via:
	•	Gemini API (API key) — simple for demos.
	•	Vertex AI (recommended) — Workload Identity, no keys.
	•	Kubernetes overlays & config hygiene
	•	Kustomize overlays (base, overlays/development, overlays/production) following BoA conventions.
	•	Dev overlays keep things quiet (e.g., metrics export off where supported).
	•	Security/ops hygiene
	•	Prefer Workload Identity; avoid SA keys in git.
	•	Backlog: migrate any local keys to Secret Manager.

⸻

Prerequisites
	•	gcloud (authenticated to your GCP project)
	•	docker (Buildx optional)
	•	kubectl (pointed at your GKE cluster)
	•	kustomize
	•	Artifact Registry repo created (e.g. bank-of-anthos-repo)

Set common environment variables (or use Makefile defaults):

export PROJECT=<gcp-project-id>
export REGION=us-central1
export REPO=bank-of-anthos-repo
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"


⸻

Vertex AI Integration

The insight-agent can connect to Google Vertex AI using Workload Identity (WI).
Use the provided Makefile targets to bootstrap WI, deploy the agent, and verify connectivity.

Vertex Smoke Test (prebuilt image)

We ship a prebuilt vertex-smoke image to avoid slow pip install inside ephemeral pods.
	1.	Build & push once:

make vertex-smoke-image PROJECT=${PROJECT} REG=us-central1-docker.pkg.dev/${PROJECT}/bank-of-anthos-repo

	2.	Run the smoke (fast & reliable):

make vertex-smoke


⸻

🚀 One-command Demo

The Makefile provides a simple, one-command workflow that applies dev overlays and runs smoke tests.

make            # same as 'make demo'
# or
make demo

Clean up demo resources:

make demo-clean

Rollout status & smokes only:

make dev-apply
make dev-status
make dev-smoke


⸻

🔐 Authenticated E2E Smoke (userservice → agent-gateway → mcp → transactionhistory)

A job-based smoke fetches a JWT from userservice and calls agent-gateway:

make e2e-auth-smoke

Requires the BoA demo user and the jwt-key secret (per upstream).
If you’ve ever deleted jwt-key, recreate it via the upstream extras/jwt/jwt-secret.yaml.

⸻

📦 Build & Deploy: insight-agent (dev overlay)

We’ve added convenience targets to streamline build → push → apply → watch logs for insight-agent.

Configure Artifact Registry auth (once)

gcloud auth configure-docker ${REGION}-docker.pkg.dev

Option A: Gemini API variant (simple for demos)
	1.	Build, push, deploy, and tail logs:

make deploy-insight-agent-dev INS_TAG=gemini

	2.	If your image reads a GEMINI_API_KEY from a Secret, create it (example):

# If using a Secret for API key:
kubectl create secret generic gemini-api-key \
  --from-literal=api-key=<YOUR_GEMINI_API_KEY> \
  --dry-run=client -o yaml | kubectl apply -f -

Option B: Vertex AI variant (recommended)

Ensure the Vertex API is enabled and that your GKE workload has Workload Identity configured to call Vertex:

gcloud services enable aiplatform.googleapis.com --project=${PROJECT}
make deploy-insight-agent-vertex INS_TAG=vertex

The production overlay can add Workload Identity annotations and stricter runtime policies.

🧱 Dev overlays for mcp-server and agent-gateway

To apply both dev overlays and force an agent-gateway restart to pick up ConfigMap changes:

make dev-apply
make dev-status

Images pinned in overlays can be updated via:

make set-images MCP_TAG=v0.1.1 AGW_TAG=v0.1.5 PROJECT=${PROJECT} REPO=${REPO}
make show-pins

Show images currently running:

make show-images


⸻

🌐 Production overlay

The overlays/production Kustomize configs add, where applicable:
	•	Workload Identity annotations (pods run as GCP IAM service accounts; no keys in secrets).
	•	Optional Ingress/IAP for exposing agent-gateway externally with authentication.
	•	“Quiet mode” disabled — production metrics and logging enabled.

Deploy per service:

kustomize build src/ai/mcp-server/k8s/overlays/production | kubectl apply -f -
kustomize build src/ai/agent-gateway/k8s/overlays/production | kubectl apply -f -
kustomize build src/ai/insight-agent/k8s/overlays/production | kubectl apply -f -


⸻

Releasing New Images (dev overlay)

Typical flow:

# Example: frontend v0.1.3
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
export FRONTEND_TAG=v0.1.3

docker buildx build --platform linux/amd64 \
  -t ${REG}/frontend:${FRONTEND_TAG} \
  -f src/frontend/Dockerfile src/frontend --push

# Pin tag in the dev overlay
(cd src/frontend/k8s/overlays/development && \
  kustomize edit set image frontend=${REG}/frontend:${FRONTEND_TAG})

# Apply + restart + verify
kustomize build src/frontend/k8s/overlays/development | kubectl apply -f -
kubectl -n default rollout restart deploy/frontend
kubectl -n default rollout status deploy/frontend
make dev-smoke

Prefer digests for critical paths: frontend@sha256:<digest>.

⸻

Project Status Update: Application Stabilization and Bug Fixes

The app is fully functional; all smokes pass. Highlights:
	•	Reliable smokes: scripts/e2e-smoke.sh with authenticated flow + retry loop.
	•	MCP ↔ AGW: agent-gateway calls GET /transactions/<acct> and forwards JWTs.
	•	Auth path fixed: Smokes fetch valid JWTs and use the correct account id for authorization.
	•	transactionhistory WI: Workload Identity + Monitoring/Trace roles; pod stable.
	•	TMA boot: transaction-monitoring-agent waits on userservice readiness.
	•	Frontend config: Clean service addresses (servicename:port) and var names aligned.
	•	Ledgerwriter fix: Env mapping for BALANCES_API_ADDR from service-api-config.

⸻

Lessons learned (short)
	•	Immutable spec.selector: Don’t patch Deployment selectors; patch pod template labels instead.
	•	ConfigMap changes need restarts: Use kubectl rollout restart deploy/....
	•	Service stabilization & retries: After rollout “success,” allow DNS/NEG to settle; keep a short retry loop.
	•	Prefer Job for tests: Job + wait + logs avoids attach races in CI.
	•	Workload Identity > SA keys: Prefer WI for GKE.
	•	Prebuilt smoke images: Shipping the vertex-smoke image removed transient timeout issues on Autopilot.

⸻

Architecture (quick callouts)

Budget Coach UI (Streamlit) consumes insight-agent /budget/coach and is exposed either locally (port-forward) or via LB for judges.
For Vertex builds, the UI points to /api/budget/coach by exporting INSIGHT=http://…/api.

Gateway & MCP path

client → agent-gateway (svc:port 80) → mcp-server (svc:8080) → transactionhistory (svc:8080) → Cloud SQL

Insight path (Vertex mode)

agent-gateway → insight-agent (svc:80→pod:8080, KSA=insight-agent) --WI→ GSA=insight-agent@${PROJECT}.iam.gserviceaccount.com → Vertex AI (Gemini)

Notes
	•	AGW is exposed internally via K8s Service on port 80 (smokes use this).
	•	WI binding: roles/iam.workloadIdentityUser on the GSA, plus roles/aiplatform.user for Vertex.
	•	Images come from Artifact Registry (${REGION}-docker.pkg.dev/${PROJECT}/${REPO}).

⸻

© Forked from Google’s Bank of Anthos (Apache 2.0). See upstream repo for license details.
