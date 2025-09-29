# 🏆 Hackathon Demo Runbook

This document provides two views:
- **Judge-Friendly Quickstart** — one-page, ~10 commands for hackathon judges
- **Full Runbook** — detailed step-by-step for developers/teammates

---

## 🎯 Judge-Friendly Quickstart (10 commands)

```bash
# 1. Clone repo & switch to hackathon branch
git clone https://github.com/kloudfy/personal-financial-advisor.git
cd personal-financial-advisor
git checkout hackathon-submission

# 2. Set project/env
export PROJECT=<your-gcp-project-id>
export REGION=us-central1
export REPO=bank-of-anthos-repo
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"

# 3. Enable Vertex AI + WI
make vertex-enable
make vertex-wi-bootstrap PROJECT=${PROJECT}

# 4. Deploy services
make dev-apply

# 5. Run smokes (AGW + Vertex AI)
make dev-smoke
make vertex-smoke

# 6. End-to-end demo: JWT → AGW → Insight-agent
make e2e-auth-smoke
```

✅ If you see **“OK”** from `vertex-smoke` and structured JSON from `e2e-auth-smoke`, the demo succeeded.

---

## 📖 Full Runbook (Team)

### 1. Clone the repo
```bash
git clone https://github.com/kloudfy/personal-financial-advisor.git
cd personal-financial-advisor
git checkout hackathon-submission
```

### 2. Configure environment
```bash
export PROJECT=<your-gcp-project-id>
export REGION=us-central1
export REPO=bank-of-anthos-repo
export REG="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
```
Enable required APIs:
```bash
make vertex-enable
```

### 3. Bootstrap Workload Identity (Vertex AI integration)
```bash
make vertex-wi-bootstrap PROJECT=${PROJECT}
```

### 4. Deploy services (dev overlay)
```bash
make dev-apply
make dev-status
```

### 5. Run smoke tests
**5.1 MCP + Agent Gateway smokes**
```bash
make dev-smoke
make e2e-auth-smoke
```

**5.2 Vertex AI smoke (Gemini 2.5 Pro)**
```bash
make vertex-smoke
```
Expected output:
```
==> Success.
OK
```

### 6. Demo flow
> **Note:** This section requires `jq` to be installed.

**6.1 Get JWT and call Agent Gateway**
```bash
TOKEN=$(kubectl -n default run curl --rm -i --restart=Never --image=curlimages/curl -- \
  sh -lc 'curl -s "http://userservice:8080/login?username=testuser&password=bankofanthos"' 2>/dev/null | grep "^{" | jq -r .token)

kubectl -n default run curl --rm -i --restart=Never --image=curlimages/curl -- \
  sh -lc "curl -sS -H 'Authorization: Bearer $TOKEN' \
  http://agent-gateway:80/chat -d '{\"query\": \"Summarize spending for account 1011226111\"}' \
  -H 'Content-Type: application/json'"
```

### 7. Clean up
```bash
make demo-clean
```

### 8. Deliverables checklist
- ✅ Hosted URL / API endpoint
- ✅ Public repo
- ✅ Updated README
- ✅ Architecture diagram
- ✅ Demo video (~3 mins)
- ✅ Optional blog/social (#GKEHackathon, #GKETurns10)

---

© Forked from Google’s Bank of Anthos (Apache 2.0).
