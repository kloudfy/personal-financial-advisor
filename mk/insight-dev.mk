# Insight Agent: dev loop + reproducible pins (Option A: Vertex by default)
NS ?= default
# Full Artifact Registry repo, e.g. "us-central1-docker.pkg.dev/${PROJECT}/${REPO}"
REG ?= ${REG}

IA_DIR  := src/ai/insight-agent
IA_IMG  := $(REG)/insight-agent

# Default to the Vertex build (has /api/* routes wired to Vertex SDK)
IA_DOCKERFILE ?= $(IA_DIR)/Dockerfile.vertex

# Use a unique tag if you like: DEV_TAG=vertex-$(shell date +%s)
DEV_TAG ?= dev

# --- helpers ---
define _require_reg
	@if ! echo "$(REG)" | grep -Eq '^[a-z0-9-]+-docker\.pkg\.dev/[^/]+/[^/]+$$'; then \
		echo 'REG "$(REG)" is invalid. Expected "<region>-docker.pkg.dev/<project>/<repo>"' >&2; \
		exit 2; \
	fi
endef

.PHONY: insight-dev-build
insight-dev-build: ## Build & push :$(DEV_TAG) (defaults to Vertex Dockerfile)
	$(call _require_reg)
	@echo "→ Building $(IA_IMG):$(DEV_TAG) with $(IA_DOCKERFILE)"
	docker buildx build --no-cache --platform linux/amd64 \
	  -t $(IA_IMG):$(DEV_TAG) \
	  -f $(IA_DOCKERFILE) $(IA_DIR) --push
	@echo "✅ Pushed $(IA_IMG):$(DEV_TAG)"

.PHONY: insight-dev-rollout
insight-dev-rollout: ## Point deploy at :$(DEV_TAG) and roll
	$(call _require_reg)
	kubectl -n $(NS) set image deploy/insight-agent insight-agent=$(IA_IMG):$(DEV_TAG)
	kubectl -n $(NS) rollout status deploy/insight-agent
	@echo "✅ insight-agent rolled to $(IA_IMG):$(DEV_TAG)"

.PHONY: insight-dev-restart
insight-dev-restart: ## Just restart (useful if imagePullPolicy=Always on :dev)
	kubectl -n $(NS) rollout restart deploy/insight-agent
	kubectl -n $(NS) rollout status  deploy/insight-agent
	@echo "✅ insight-agent restarted"

.PHONY: insight-show-image
insight-show-image: ## Show the live image in the Deployment
	kubectl -n $(NS) get deploy/insight-agent -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'

# ---------- Reproducible pin (known good) ----------
# Usage: make insight-pin IA_TAG=v0.2.1   (or any pushed tag)
.PHONY: insight-pin
insight-pin: ## Pin dev overlay to a tag's digest (commit this for judges/CI)
	$(call _require_reg)
	@[ -n "$(IA_TAG)" ] || { echo "IA_TAG is required, e.g. IA_TAG=v0.2.1" >&2; exit 2; }
	@echo "→ Resolving digest for $(IA_IMG):$(IA_TAG)"
	@DIGEST=$$(gcloud artifacts docker images describe $(IA_IMG):$(IA_TAG) --format='get(image_summary.digest)'); \
	  test -n "$$DIGEST" || { echo "Could not resolve digest" >&2; exit 3; }; \
	  echo "→ Pinning dev overlay to $$DIGEST"; \
	  cd $(IA_DIR)/k8s/overlays/development && \
	    kustomize edit set image insight-agent=$(IA_IMG)@$$DIGEST && \
	    echo "Pinned to: $$(grep -A1 'images:' -n kustomization.yaml || true)"
	@echo "✅ Now: git add && git commit (this is your reproducible checkpoint)"

# --- OPTIONAL: build the non-Vertex image on demand ---
.PHONY: insight-dev-build-nonvertex
insight-dev-build-nonvertex: ## Build non-Vertex variant (main.py)
	$(MAKE) insight-dev-build IA_DOCKERFILE=$(IA_DIR)/Dockerfile
	