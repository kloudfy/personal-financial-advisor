.PHONY: demo demo-clean dev-apply dev-smoke dev-status set-images

# --- Default target ---
default: demo

# --- Demo Workflow ---
demo:
	@echo "==> Running demo: apply dev overlays + smoke tests"
	$(MAKE) dev-apply
	$(MAKE) dev-smoke

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
	@echo "==> E2E auth smoke"
	@kubectl -n $(NS) delete job/e2e-auth --ignore-not-found >/dev/null 2>&1 || true
	@kubectl -n $(NS) create job e2e-auth --image=curlimages/curl -- \
sh -lc 'set -eu; \
USERSVC="http://userservice.$(NS).svc.cluster.local:8080"; \
AGW="http://agent-gateway.$(NS).svc.cluster.local:80"; \
TOKEN=$$(curl -s "$$USERSVC/login?username=testuser&password=bankofanthos" \
  | sed -E ''s/.*"token":"([^"]+)".*/\1/''); \
test -n "$$TOKEN"; \
curl -s -X POST \
  -H "content-type: application/json" \
  -H "Authorization: Bearer $$TOKEN" \
  -d '''{"prompt":"analyze my spend","account_id":"0000000001","window_days":30}''' \
  $$AGW/chat'
	@kubectl -n $(NS) wait --for=condition=complete job/e2e-auth --timeout=90s
	@kubectl -n $(NS) logs job/e2e-auth
	@kubectl -n $(NS) delete job/e2e-auth --ignore-not-found >/dev/null 2>&1 || true
