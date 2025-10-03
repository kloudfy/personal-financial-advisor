# 🏆 Hackathon Demo Runbook

This document provides two views:
- **Judge-Friendly Quickstart** — one-page, ~10 commands for hackathon judges
- **Full Runbook** — detailed step-by-step for developers/teammates

---

## 🎯 Judge-Friendly Quickstart (10 commands)

```bash
# 1) Clone repo
git clone https://github.com/kloudfy/personal-financial-advisor.git
cd personal-financial-advisor

# 2) Set project/env
export PROJECT=<your-gcp-project-id>
export REGION=us-central1
export REPO=bank-of-anthos-repo
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"

# 3) Enable Vertex AI + Workload Identity
make vertex-enable
make vertex-wi-bootstrap PROJECT=${PROJECT}

# 4) Deploy services (dev overlays)
make dev-apply

# 5) Run smokes (AGW + Vertex AI)
make dev-smoke
make vertex-smoke

# 6) End-to-end demo: JWT → Agent-Gateway → Insight-Agent
make e2e-auth-smoke

✅ If you see “OK” from vertex-smoke and structured JSON from e2e-auth-smoke, the demo succeeded.

⸻

🚦 Pre-demo Quick Check (fast)

Use these portable smokes to confirm the core paths without rebuilding anything.

Prereqs
	•	kubectl is pointed at the target cluster
	•	Namespace defaults to default (override with NS=<ns> or SMOKE_NS=<ns>)
	•	Makefile + mk/smoke.mk present

Commands

# Core connectivity: healthz + auth
make smoke-core

# Data path: MCP returns txns; transform → insight-agent coach
make smoke-data

# End-to-end: Agent Gateway /chat with JWT (truncated output)
make smoke-e2e

# One-liner for all three (fast)
make smoke-fast

# Full original suite
make smoke-all

# Adjust truncation (bytes) for /chat preview
make smoke-e2e SMOKE_HEAD=400

Expected signals
	•	smoke-core prints three OKs and TOKEN_LEN=… (~800–900)
	•	smoke-data shows sample transactions and a JSON with a non-empty summary
	•	smoke-e2e prints the first $(SMOKE_HEAD) bytes of /chat JSON (agent + “result”)
	•	Each target ends with a ✅ confirmation (e.g., ✅ smoke-core passed)

⸻

📖 Full Runbook (Team)

1) Clone the repo

git clone https://github.com/kloudfy/personal-financial-advisor.git
cd personal-financial-advisor

2) Configure environment

export PROJECT=<your-gcp-project-id>
export REGION=us-central1
export REPO=bank-of-anthos-repo
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"

Enable required APIs:

make vertex-enable

3) Bootstrap Workload Identity (Vertex AI integration)

make vertex-wi-bootstrap PROJECT=${PROJECT}

4) Deploy services (dev overlay)

make dev-apply
make dev-status

5) Run smoke tests

5.1 MCP + Agent Gateway smokes

make dev-smoke
make e2e-auth-smoke

5.2 Vertex AI smoke (Gemini 2.5 Pro)

make vertex-smoke

Expected output:

==> Success.
OK


⸻

🖥️ Budget Coach UI (optional but nice)

Local (port-forward → Streamlit):
	1.	Start port-forwards in three shells (or background them):

kubectl -n default port-forward deploy/userservice 8081:8080
kubectl -n default port-forward deploy/mcp-server 8082:8080
kubectl -n default port-forward svc/insight-agent 8083:80

	2.	Point the UI at your local forwards (same shell where you’ll run Streamlit):

export USERSVC=http://localhost:8081
export MCPSVC=http://localhost:8082

# Vertex build (insight-agent exposes /api/budget/coach)
export INSIGHT=http://localhost:8083/api
# Legacy build (insight-agent exposes /budget/coach)
# export INSIGHT=http://localhost:8083

	3.	Run the UI locally:

make ui-demo
# open http://localhost:8501

Cluster (Judges overlay with external IP):

make ui-judges-apply
make ui-judges-ip  # prints http://EXTERNAL-IP
# open the URL in a browser


⸻

📦 UI Production (digest pinned)

Prefer immutable digests for production:

export PROJECT=<your-gcp-project-id>
export REGION=us-central1
export REG="${REGION}-docker.pkg.dev/${PROJECT}/bank-of-anthos-repo"

# Option A: start from a tag
export UI_TAG=v0.1.0
eval "$(make -s ui-prod-digest-latest UI_TAG=${UI_TAG})"  # prints: export UI_IMAGE_DIGEST=sha256:...
make ui-prod-set-digest
make ui-prod-apply
make ui-prod-verify

# Option B: if you already know the digest
export UI_IMAGE_DIGEST=sha256:<digest>
make ui-prod-set-digest
make ui-prod-apply
make ui-prod-verify


⸻

6) Demo flow (token → chat)

Note: This section requires jq.

TOKEN=$(kubectl -n default run curl --rm -i --restart=Never --image=curlimages/curl -- \
  sh -lc 'curl -s "http://userservice:8080/login?username=testuser&password=bankofanthos"' 2>/dev/null \
  | grep "^{.*" | jq -r .token)

kubectl -n default run curl --rm -i --restart=Never --image=curlimages/curl -- \
  sh -lc "curl -sS -H 'Authorization: Bearer $TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{\"query\": \"Summarize spending for account 1011226111\"}' \
  http://agent-gateway:80/chat"


⸻

7) Clean up

make demo-clean

8) Deliverables checklist
	•	✅ Hosted URL / API endpoint
	•	✅ Public repo
	•	✅ Updated README
	•	✅ Architecture diagram
	•	✅ Demo video (~3 mins)
	•	✅ Optional blog/social (#GKEHackathon, #GKETurns10)

⸻

© Forked from Google’s Bank of Anthos (Apache 2.0).