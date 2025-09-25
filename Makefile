# --- Config ---
PROJECT ?= gke-hackathon-469600
REPO    ?= bank-of-anthos-repo
REGION  ?= us-central1
REG     ?= $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)
TAG     ?= v0.1.2
NS      ?= default

# --- Paths ---
MCP_DEV_OVERLAY := src/ai/mcp-server/k8s/overlays/development
AG_DEV_OVERLAY  := src/ai/agent-gateway/k8s/overlays/development

# --- Build/push (optional if you use pinned images) ---
build-mcp:
	docker buildx build --platform linux/amd64,linux/arm64 \
	-t $(REG)/mcp-server:$(TAG) -f src/ai/mcp-server/Dockerfile . --push

build-ag:
	docker buildx build --platform linux/amd64,linux/arm64 \
	-t $(REG)/agent-gateway:$(TAG) -f src/ai/agent-gateway/Dockerfile . --push

# --- Deploy dev (uses pinned images in overlay) ---
dev-apply:
	kustomize build $(MCP_DEV_OVERLAY) | kubectl apply -n $(NS) -f - 
	kustomize build $(AG_DEV_OVERLAY)  | kubectl apply -n $(NS) -f - 

dev-status:
	kubectl -n $(NS) rollout status deploy/mcp-server
	kubectl -n $(NS) rollout status deploy/agent-gateway

dev-smoke:
	NS=$(NS) ./scripts/smoke-dev.sh

# --- Override images (portable mode) ---
set-image:
	kubectl -n $(NS) set image deploy/mcp-server mcp-server=$(REG)/mcp-server:$(TAG)
	kubectl -n $(NS) set image deploy/agent-gateway agent-gateway=$(REG)/agent-gateway:$(TAG)
	kubectl -n $(NS) rollout status deploy/mcp-server
	kubectl -n $(NS) rollout status deploy/agent-gateway

.PHONY: build-mcp build-ag dev-apply dev-status dev-smoke set-image