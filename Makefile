.PHONY: demo demo-clean dev-apply dev-smoke dev-status set-images

# --- Default target ---
default: demo

# --- Demo Workflow ---
.PHONY: demo demo-fast

# ------------------------------------------------------------------------------
# Full end-to-end demo for judges:
#  1) Apply dev overlays
#  2) Quick smoke (health + AGW sample)
#  3) Vertex WI smoke (Gemini on Vertex via WI)
#  4) Authenticated end-to-end smoke
# Usage:
#   PROJECT=<id> REGION=us-central1 REPO=bank-of-anthos-repo REG=${REGION}-docker.pkg.dev/${PROJECT}/${REPO} make demo
# ------------------------------------------------------------------------------
demo: ## One command: dev-apply → dev-smoke → vertex-smoke → e2e-auth-smoke
	@echo "==> [1/4] Applying dev overlays…"
	@$(MAKE) --no-print-directory dev-apply
	@echo "==> [2/4] Running quick smokes…"
	@$(MAKE) --no-print-directory dev-smoke
	@echo "==> [3/4] Verifying Vertex AI access (WI)…"
	@$(MAKE) --no-print-directory vertex-smoke
	@echo "==> [4/4] Running authenticated end-to-end smokes…"
	@$(MAKE) --no-print-directory e2e-auth-smoke
	@echo "✅ Demo complete. All checks passed."

# Same as demo but skips Vertex step (useful if Vertex is not enabled yet).
demo-fast: ## One command: dev-apply → dev-smoke → e2e-auth-smoke (no Vertex)
	@echo "==> [1/3] Applying dev overlays…"
	@$(MAKE) --no-print-directory dev-apply
	@echo "==> [2/3] Running quick smokes…"
	@$(MAKE) --no-print-directory dev-smoke
	@echo "==> [3/3] Running authenticated end-to-end smokes…"
	@$(MAKE) --no-print-directory e2e-auth-smoke
	@echo "✅ Demo (fast) complete."

# --- Clean up demo resources ---
demo-clean:
	@echo "==> Cleaning up demo resources..."
	-kubectl delete deploy/mcp-server deploy/agent-gateway -n $(NS) --ignore-not-found
	-kubectl delete svc/mcp-server svc/agent-gateway -n $(NS) --ignore-not-found
	-kubectl delete sa/mcp-server sa/agent-gateway -n $(NS) --ignore-not-found
	@echo "==> Demo resources cleaned up."

PROJECT ?= gke-hackathon-469600
REPO    ?= bank-of-anthos-repo
export REGION  ?= us-central1
export GOOGLE_CLOUD_PROJECT ?= $(PROJECT)
export VERTEX_LOCATION ?= us-central1
export VERTEX_AGENT_ID ?= agent-123
export REG     ?= $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)
export MCP_TAG ?= v0.1.0
export AGW_TAG ?= v0.1.2
export NS      ?= default

# --- Paths ---
MCP_DEV_OVERLAY := src/ai/mcp-server/k8s/overlays/development
AG_DEV_OVERLAY  := src/ai/agent-gateway/k8s/overlays/development

# --- Primary Workflow Targets ---
dev-config:
	@echo "==> (re)creating vertex-config ConfigMap..."
	kubectl -n $(NS) create configmap vertex-config \
	  --from-literal=GOOGLE_CLOUD_PROJECT=$(PROJECT) \
	  --from-literal=VERTEX_LOCATION=$(VERTEX_LOCATION) \
	  --from-literal=VERTEX_AGENT_ID=$(VERTEX_AGENT_ID) \
	  --dry-run=client -o yaml | kubectl apply -f -

dev-apply: dev-config
	@echo "==> Applying dev overlays with pinned images..."
	kustomize build $(MCP_DEV_OVERLAY) | kubectl apply -n $(NS) -f -
	kustomize build $(AG_DEV_OVERLAY)  | kubectl apply -n $(NS) -f -
	@echo "==> Forcing rollout of agent-gateway to pick up ConfigMap changes..."
	kubectl -n $(NS) rollout restart deploy/agent-gateway
dev-status:
	@echo "==> Checking rollout status..."
	kubectl -n $(NS) rollout status deploy/mcp-server
	kubectl -n $(NS) rollout status deploy/agent-gateway

dev-smoke:
	@echo "==> Running smoke tests..."
	NS=$(NS) ./scripts/smoke-dev.sh

# --- Image & Pinning Management ---
set-images:
	@echo "==> Pinning images in dev overlays..."
	(cd $(MCP_DEV_OVERLAY) && kustomize edit set image mcp-server=$(REG)/mcp-server:$(MCP_TAG))
	(cd $(AG_DEV_OVERLAY) && kustomize edit set image agent-gateway=$(REG)/agent-gateway:$(AGW_TAG))

show-images:
	@echo "==> Images currently running in cluster:"
	kubectl -n $(NS) get deploy mcp-server -o=jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
	kubectl -n $(NS) get deploy agent-gateway -o=jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'

show-pins:
	@echo "==> Images currently pinned in dev overlays:"
	grep -A 2 "name: mcp-server" $(MCP_DEV_OVERLAY)/kustomization.yaml | tail -n 2
	grep -A 2 "name: agent-gateway" $(AG_DEV_OVERLAY)/kustomization.yaml | tail -n 2

# --- Manual Build/Push (optional) ---
build-mcp:
	docker buildx build --platform linux/amd64,linux/arm64 \
	-t $(REG)/mcp-server:$(MCP_TAG) -f src/ai/mcp-server/Dockerfile . --push

build-agw:
	docker buildx build --platform linux/amd64,linux/arm64 \
	-t $(REG)/agent-gateway:$(AGW_TAG) -f src/ai/agent-gateway/Dockerfile . --push

.PHONY: demo demo-clean dev-apply dev-smoke dev-status set-images show-images show-pins build-mcp build-agw e2e-auth-smoke

# --- Authenticated E2E smoke (userservice -> txhistory via mcp/agent-gateway)
e2e-auth-smoke:
	@echo "==> Running E2E authenticated smoke tests..."
	NS=$(NS) ./scripts/e2e-smoke.sh

## Note:
## e2e-smoke.sh includes built-in retry logic for the /chat call to agent-gateway (port 80).
## This improves stability under cold starts and avoids transient 502 errors.
## See scripts/e2e-smoke.sh for inline comments explaining why retries are necessary
## (cold starts, DNS/cache propagation) and details on the port fix (80, not 8080).
## If you copy this logic elsewhere, keep the retry loop + short sleep, or expect
## occasional 5xx during rapid rollouts.

###############################################################################
# Vertex AI (Workload Identity) – insight-agent (dev)
###############################################################################
.PHONY: vertex-enable vertex-wi-bootstrap deploy-insight-agent-vertex vertex-smoke

vertex-enable: ## Enable Vertex AI API
	gcloud services enable aiplatform.googleapis.com --project ${PROJECT}

## Creates a GSA and binds it for WI to the KSA `insight-agent` in default NS.
## Also grants minimal Vertex permissions.
vertex-wi-bootstrap: ## Bootstrap Workload Identity for insight-agent (dev)
	@[ -n "${PROJECT}" ] || (echo "PROJECT is required"; exit 1)
	gcloud iam service-accounts create insight-agent \
	  --project ${PROJECT} \
	  --display-name "Insight Agent (Vertex)" || true
	@echo "Waiting 5 seconds for service account to propagate..."
	sleep 5
	gcloud projects add-iam-policy-binding ${PROJECT} \
	  --member "serviceAccount:insight-agent@${PROJECT}.iam.gserviceaccount.com" \
	  --role "roles/aiplatform.user"
	# Allow KSA to impersonate GSA via WI
	gcloud iam service-accounts add-iam-policy-binding \
	  insight-agent@${PROJECT}.iam.gserviceaccount.com \
	  --role "roles/iam.workloadIdentityUser" \
	  --member "serviceAccount:${PROJECT}.svc.id.goog[default/insight-agent]"
	# Patch annotation into the KSA manifest & apply overlay (dev)
	sed -i.bak 's#^\s*iam.gke.io/gcp-service-account:.*#    iam.gke.io/gcp-service-account: insight-agent@'"${PROJECT}"'.iam.gserviceaccount.com#' \
	  src/ai/insight-agent/k8s/overlays/development/sa-wi.yaml
	kustomize build src/ai/insight-agent/k8s/overlays/development | kubectl apply -f -
	kubectl -n default rollout status deploy/insight-agent

deploy-insight-agent-vertex: ## Build/push (if needed) and deploy insight-agent in Vertex mode
	@[ -n "${PROJECT}" ] || (echo "PROJECT is required"; exit 1)
	@[ -n "${REPO}" ] || (echo "REPO is required"; exit 1)
	@[ -n "${REG}" ] || (echo "REG is required"; exit 1)
	@[ -n "${INS_TAG}" ] || (echo "INS_TAG=<tag> is required, e.g. INS_TAG=vertex"; exit 1)
	docker buildx build --platform linux/amd64 \
	  -t ${REG}/insight-agent:${INS_TAG} \
	  -f src/ai/insight-agent/Dockerfile \
	  src/ai/insight-agent --push
	( cd src/ai/insight-agent/k8s/overlays/development && \
	  kustomize edit set image insight-agent=${REG}/insight-agent:${INS_TAG} )
	kustomize build src/ai/insight-agent/k8s/overlays/development | kubectl apply -f -
	kubectl -n default rollout restart deploy/insight-agent
	kubectl -n default rollout status  deploy/insight-agent

vertex-smoke-image: ## Build/push the prebuilt vertex-smoke image
	@[ -n "${PROJECT}" ] || (echo "PROJECT is required"; exit 1)
	@[ -n "${REPO}" ] || (echo "REPO is required"; exit 1)
	@[ -n "${REG}" ] || (echo "REG is required"; exit 1)
	docker buildx build --platform linux/amd64 \
	  -f Dockerfile.smoke \
	  -t ${REG}/vertex-smoke:latest . --push

vertex-smoke: ## One-off pod calls Vertex Gemini with WI (expects WI bootstrap done)
	chmod +x scripts/vertex-smoke.sh
	./scripts/vertex-smoke.sh

.PHONY: budget-smoke
budget-smoke: ## One-off pod that fetches txns via MCP and hits /budget/coach on insight-agent
	chmod +x scripts/budget-smoke.sh
	./scripts/budget-smoke.sh


###############################################################################
# Docs / Helpers
###############################################################################
.PHONY: runbook
runbook: ## Print Judge-Friendly Quickstart from runbook
	@awk '/## 🎯 Judge-Friendly Quickstart/,/^---/' HACKATHON-DEMO-RUNBOOK.md \
	| sed \
	  -e 's/^## 🎯.*/\x1b[36;1m&\x1b[0m/' \
	  -e 's/^# \(.*\)/\x1b[33m# \1\x1b[0m/' \
	  -e 's/^\(git\|make\|export\|kubectl\|docker\)/\x1b[32m\1\x1b[0m/' \
	  -e 's/^\(\s\+\)\(git\|make\|export\|kubectl\|docker\)/\1\x1b[32m\2\x1b[0m/'

.PHONY: runbook-plain
runbook-plain: ## Print Judge-Friendly Quickstart (no color)
	@awk '/## 🎯 Judge-Friendly Quickstart/,/^---/' HACKATHON-DEMO-RUNBOOK.md
