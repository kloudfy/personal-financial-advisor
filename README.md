# Personal Financial Advisor (Hackathon build on Bank of Anthos)

> **Fork notice:** This project is a fork of Google’s
> [Bank of Anthos](https://github.com/GoogleCloudPlatform/bank-of-anthos).
> See the original repository for upstream history, design, and licensing.
> Our fork customizes and extends BoA for the GKE Hackathon.

## What we added/changed (high level)

- **Agents & MCP Server**
  - `src/mcp-server/` – lightweight Model Context Protocol server to surface BOA signals/tools to the agent.
  - `src/transaction-monitoring-agent/` – companion agent microservice for advisory/monitoring flows.

- **Transaction History service fixes (Java / Spring Boot)**
  - Align with **Spring Boot 3.5.x + Jakarta 3.1** to resolve `EntityManagerFactory` / Hibernate proxy clash.
  - New multi-arch image build (linux/amd64, linux/arm64).
  - Optional **“quiet mode”** in **dev** to disable GCP Stackdriver metrics export.

- **Kubernetes overlays & config hygiene**
  - Introduced Kustomize overlays (`base`, `overlays/development`, `overlays/production`) following BoA conventions.
  - Dev overlay sets `MANAGEMENT_METRICS_EXPORT_STACKDRIVER_ENABLED=false` by default.
  - Label/selector alignment to be paste-and-go with existing Deployments/Services.

- **Security/ops hygiene for the hackathon**
  - Sensitive/testing helpers removed from repo history and ignored going forward.
  - Plan to migrate any local keys to **Secret Manager** (tracked in backlog).

## Quick start (dev)

```bash
# Build and push multi-arch TransactionHistory image
IMG=us-central1-docker.pkg.dev/<GCP_PROJECT>/<registry>/transactionhistory:v0.6.10
docker buildx build --platform linux/amd64,linux/arm64 \
  -t "$IMG" -f src/ledger/transactionhistory/Dockerfile . --push

# Roll out TransactionHistory
kubectl set image deploy/transactionhistory transactionhistory="$IMG"
kubectl rollout status deploy/transactionhistory

# Ensure dev quiet mode is on
kubectl set env deploy/transactionhistory MANAGEMENT_METRICS_EXPORT_STACKDRIVER_ENABLED=false
