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

PROJECT ?= <PROJECT-ID>
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

# Simple guard (use in targets with: $(call require_project))
require_project = @if [ "$(PROJECT)" = "<PROJECT-ID>" ] || [ -z "$(PROJECT)" ]; then \
  echo "❌ Set PROJECT=<your-gcp-project-id> (current: '$(PROJECT)')" >&2; exit 1; fi

# --- Primary Workflow Targets ---
dev-config:
	@echo "==> (re)creating vertex-config ConfigMap..."
	kubectl -n $(NS) create configmap vertex-config \
	  --from-literal=GOOGLE_CLOUD_PROJECT=$(PROJECT) \
	  --from-literal=VERTEX_LOCATION=$(VERTEX_LOCATION) \
	  --from-literal=VERTEX_AGENT_ID=$(VERTEX_AGENT_ID) \
	  --dry-run=client -o yaml | kubectl apply -f -

dev-apply: dev-config
	$(call require_project)
	@echo "==> Applying dev overlays with pinned images..."
	kustomize build $(MCP_DEV_OVERLAY) | kubectl apply -n $(NS) -f -
	kustomize build $(AG_DEV_OVERLAY)  | kubectl apply -n $(NS) -f -
	kustomize build src/ai/insight-agent/k8s/overlays/development | kubectl apply -n $(NS) -f -
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
	@grep -A 2 "name: mcp-server" $(MCP_DEV_OVERLAY)/kustomization.yaml | tail -n 2
	@grep -A 2 "name: agent-gateway" $(AG_DEV_OVERLAY)/kustomization.yaml | tail -n 2

# --- Manual Build/Push (optional) ---
build-mcp:
	docker buildx build --platform linux/amd64,linux/arm64 \
	-t $(REG)/mcp-server:$(MCP_TAG) -f src/ai/mcp-server/Dockerfile . --push

build-agw:
	docker buildx build --platform linux/amd64,linux/arm64 \
	-t $(REG)/agent-gateway:$(AGW_TAG) -f src/ai/agent-gateway/Dockerfile . --push

.PHONY: demo demo-clean dev-apply dev-smoke dev-status set-images show-images show-pins build-mcp build-agw e2e-auth-smoke ui-smoke

# --- Authenticated E2E smoke (userservice -> txhistory via mcp/agent-gateway)
e2e-auth-smoke:
	@echo "==> Running E2E authenticated smoke tests..."
	NS=$(NS) ./scripts/e2e-smoke.sh

## Note:
## e2e-smoke.sh includes built-in retry logic for the /chat call to agent-gateway (port 80).

###############################################################################
# Vertex AI (Workload Identity) – insight-agent (dev)
###############################################################################
.PHONY: vertex-enable vertex-wi-bootstrap deploy-insight-agent-vertex vertex-smoke

vertex-enable: ## Enable Vertex AI API
	$(call require_project)
	gcloud services enable aiplatform.googleapis.com --project ${PROJECT}

## Creates a GSA and binds it for WI to the KSA `insight-agent` in default NS.
## Also grants minimal Vertex permissions.
vertex-wi-bootstrap: ## Bootstrap Workload Identity for insight-agent (dev)
	$(call require_project)
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
	$(call require_project)
	@[ -n "${PROJECT}" ] || (echo "PROJECT is required"; exit 1)
	@[ -n "${REPO}" ] || (echo "REPO is required"; exit 1)
	@[ -n "${REG}" ] || (echo "REG is required"; exit 1)
	@[ -n "${INS_TAG}" ] || (echo "INS_TAG=<tag> is required, e.g. INS_TAG=vertex"; exit 1)
	docker buildx build --platform linux/amd64 \
	  -t ${REG}/insight-agent:${INS_TAG} \
	  -f src/ai/insight-agent/Dockerfile.vertex \
	  src/ai/insight-agent --push --no-cache
	( cd src/ai/insight-agent/k8s/overlays/development && \
	  kustomize edit set image insight-agent=${REG}/insight-agent:${INS_TAG} )
	kustomize build src/ai/insight-agent/k8s/overlays/development | kubectl apply -f -
	kubectl -n default rollout restart deploy/insight-agent
	kubectl set image deployment/insight-agent insight-agent=${REG}/insight-agent:${INS_TAG} -n default
	kubectl -n default rollout status  deploy/insight-agent

vertex-smoke-image: ## Build/push the prebuilt vertex-smoke image
	$(call require_project)
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

# ------------------------------------------------------------
# UI helpers (local demo)
# ------------------------------------------------------------
.PHONY: pfw-usersvc pfw-mcp pfw-insight ui-demo

pfw-usersvc:
	kubectl -n default port-forward deploy/userservice 8081:8080

pfw-mcp:
	kubectl -n default port-forward deploy/mcp-server 8082:8080

pfw-insight:
	kubectl -n default port-forward svc/insight-agent 8083:80

ui-demo:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r ui/requirements.txt && streamlit run ui/budget_coach_app.py

# ======================= SMOKE TARGETS (safe to append) =======================

.PHONY: ui-smoke svc-smoke

## ui-smoke: Curl Streamlit UI root locally + in-cluster /budget/coach mock POST
## Assumes you started the local UI with:  make ui-demo   (port 8501)
ui-smoke:
	@echo "==> Checking local Streamlit UI (http://localhost:8501/)"
	@set -e; \
	  curl -fsS http://localhost:8501/ | head -n 40 | sed -e 's/<[^>]*>//g' | sed 's/^[ \t]*//' | awk 'NR<=25{print}'; \
	  echo; echo "==> In-cluster mock POST to insight-agent /budget/coach"; \
	  kubectl run coach-smoke-$$RANDOM --rm -i --restart=Never --image=curlimages/curl -- \
	    sh -lc 'cat <<JSON | curl -fsS -H "Content-Type: application/json" -d @- \
	      http://insight-agent.default.svc.cluster.local:80/budget/coach | head -c 800; echo; \
	      echo "OK"'
	@echo '  {"transactions":[{"date":"2025-09-30","label":"Test","amount":-12.34},{"date":"2025-09-30","label":"Pay","amount":1000.00}]}'
	@echo "JSON"

## svc-smoke: Curl a Service by DNS and via PodIP; optional local port-forward
## Vars:
##   SVC=<service-name> (required)  NS=default  PORT=80  PATH=/healthz  PF=true|false
svc-smoke:
	@[ -n "${SVC}" ] || (echo "SVC=<service-name> is required, e.g. SVC=insight-agent" && exit 1)
	@NS="${NS:-default}"; \
	PORT="${PORT:-80}"; \
	PATH_PFX="$${PATH:-/healthz}"; \
	echo "==> Service overview"; \
	kubectl -n "$$NS" get svc "${SVC}" -o wide || exit 1; \
	echo; \
	echo "==> Endpoints"; \
	kubectl -n "$$NS" get endpoints "${SVC}" -o wide || true; \
	kubectl -n "$$NS" get endpointslices -l kubernetes.io/service-name="${SVC}" || true; \
	echo; \
	echo "==> In-cluster DNS curl"; \
	kubectl run curl-$$RANDOM --rm -i --restart=Never --image=curlimages/curl -- \
	  curl -fsS "http://${SVC}.${NS}.svc.cluster.local:$${PORT}$${PATH_PFX}" || { echo "DNS curl failed"; exit 2; }; \
	echo; \
	echo "==> PodIP curl (bypass Service)"; \
	POD=$$(kubectl -n "$$NS" get pod -l "app=${SVC}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true); \
	if [ -n "$$POD" ]; then \
	  IP=$$(kubectl -n "$$NS" get pod "$$POD" -o jsonpath='{.status.podIP}'); \
	  kubectl run curl-$$RANDOM --rm -i --restart=Never --image=curlimages/curl -- \
	    curl -fsS "http://$${IP}:$${PORT}$${PATH_PFX}" || { echo "PodIP curl failed"; exit 3; }; \
	else \
	  echo "No pod found with label app=${SVC}; skipping PodIP curl."; \
	fi; \
	if [ "$${PF:-}" = "true" ]; then \
	  echo; echo "==> Local port-forward + curl localhost"; \
	  kubectl -n "$$NS" port-forward "svc/${SVC}" 18080:$${PORT} >/tmp/pf.$${SVC}.log 2>&1 & PF_PID=$$!; \
	  sleep 2; \
	  (curl -fsS "http://localhost:18080$${PATH_PFX}" && echo "OK") || { kill $$PF_PID || true; exit 4; }; \
	  kill $$PF_PID || true; \
	fi; \
	echo; echo "✅ svc-smoke passed for ${SVC} (ns=$$NS)"

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

# ==== Budget Coach UI (Kustomize) ====

UI_BASE := ui/k8s/base
UI_DEV  := ui/k8s/overlays/development
UI_JUDG := ui/k8s/overlays/judges
UI_PROD := ui/k8s/overlays/production

# Expect these to be exported in your shell:
#   PROJECT, REGION, REG="${REGION}-docker.pkg.dev/${PROJECT}/bank-of-anthos-repo"
#   UI_TAG (e.g., v0.1.0)
# You can also pass UI_IMAGE_DIGEST directly to skip the lookup.

.PHONY: ui-dev-apply ui-dev-status ui-dev-prune ui-dev-smoke \
        ui-judges-apply ui-judges-ip ui-judges-delete ui-dev-logs ui-judges-logs \
        ui-prod-digest-latest ui-prod-set-digest ui-prod-apply ui-prod-status ui-prod-smoke ui-prod-verify \
        ui-image ui-rs-clean ui-clean

# ----- DEV / JUDGES (tag based) -----
ui-dev-apply:
	@echo "==> Apply UI development overlay"
	kustomize build $(UI_DEV) | kubectl apply -f -
	kubectl rollout status deploy/budget-coach-ui

ui-dev-logs:
	@kubectl logs deploy/budget-coach-ui --tail=200

ui-dev-status:
	@echo "==> Deployment image and envs"
	@kubectl get deploy budget-coach-ui -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
	@kubectl get deploy budget-coach-ui -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'

ui-dev-prune:
	@echo "==> Prune stray ReplicaSets (keep only current image)"
	@kubectl get rs -l app=budget-coach-ui \
	 -o jsonpath='{range .items[*]}{.metadata.name}{"=>"}{.spec.template.spec.containers[0].image}{"\n"}{end}'
	@echo "If any RS shows a wrong image, delete it: kubectl delete rs <name>"

ui-dev-smoke:
	@echo "==> UI smoke: root 200 and agent /api/budget/coach reachable"
	@kubectl run curl-ui --rm -it --restart=Never --image=curlimages/curl:8.7.1 -- \
	  sh -lc "set -e; \
	  AG=http://insight-agent.default.svc.cluster.local/api; \
	  echo 'POST $$AG/budget/coach (should 400 with message if body minimal)'; \
	  curl -sS -D- -X POST -H 'Content-Type: application/json' \
	    -d '{\"account_id\":\"1011226111\",\"window_days\":30}' \
	    $$AG/budget/coach | head -n 20"

ui-judges-apply:
	@echo "==> Apply UI judges overlay (LB)"
	kustomize build $(UI_JUDG) | kubectl apply -f -
	kubectl rollout status deploy/budget-coach-ui

ui-judges-ip:
	@echo "==> External IP:"
	@kubectl get svc budget-coach-ui-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}'; echo

ui-judges-delete:
	@echo "==> Delete judges LB + svc + deploy"
	-kubectl delete svc budget-coach-ui-lb --ignore-not-found
	-kubectl delete svc budget-coach-ui --ignore-not-found
	-kubectl delete deploy budget-coach-ui --ignore-not-found

ui-judges-logs:
	@kubectl logs deploy/budget-coach-ui --tail=200

# ----- PROD (digest pinned) -----

# Compute latest digest for a given tag (defaults: UI_TAG=v0.1.0)
# Usage: make ui-prod-digest-latest [UI_TAG=v0.1.0]
ui-prod-digest-latest:
	$(call require_project)
	@[ -n "$$REG" ] || { echo "Set REG, e.g. REG=us-central1-docker.pkg.dev/$(PROJECT)/bank-of-anthos-repo"; exit 1; }
	@UI_TAG=$${UI_TAG:-v0.1.0}; \
	echo "Resolving digest for $$REG/budget-coach-ui:$$UI_TAG ..."; \
	DIG=$$(gcloud artifacts docker images describe "$$REG/budget-coach-ui:$$UI_TAG" \
	  --format='value(image_summary.digest)' --project "$(PROJECT)"); \
	[ -n "$$DIG" ] || { echo "No digest found. Is the tag pushed?"; exit 2; } ; \
	echo "export UI_IMAGE_DIGEST=$$DIG"

# Bake digest by cd'ing into the overlay dir (works with all kustomize versions)
ui-prod-set-digest:
	$(call require_project)
	@if [ -z "$$REG" ] || [ -z "$$PROJECT" ]; then \
	  echo "REG and PROJECT must be set in the environment"; exit 1; fi
	@if [ -z "$$UI_IMAGE_DIGEST" ]; then \
	  if [ -z "$$UI_TAG" ]; then echo "Set UI_TAG (e.g. v0.1.0) or UI_IMAGE_DIGEST"; exit 1; fi; \
	  echo "==> Resolving digest for $$REG/budget-coach-ui:$$UI_TAG"; \
	  export UI_IMAGE_DIGEST=$$(gcloud artifacts docker images describe \
	    "$$REG/budget-coach-ui:$$UI_TAG" --project "$$PROJECT" \
	    --format='value(image_summary.digest)'); \
	  if [ -z "$$UI_IMAGE_DIGEST" ]; then echo "Digest not found for tag $$UI_TAG"; exit 1; fi; \
	  echo "==> Digest: $$UI_IMAGE_DIGEST"; \
	  (cd $(UI_PROD) && kustomize edit set image REPLACE_ME_UI_IMAGE=$$REG/budget-coach-ui@$$UI_IMAGE_DIGEST); \
	else \
	  echo "==> Using provided digest: $$UI_IMAGE_DIGEST"; \
	  (cd $(UI_PROD) && kustomize edit set image REPLACE_ME_UI_IMAGE=$$REG/budget-coach-ui@$$UI_IMAGE_DIGEST); \
	fi
	@echo "==> Production images block:"
	@sed -n '/^images:/,$$p' $(UI_PROD)/kustomization.yaml

ui-prod-apply:
	@echo "==> Apply PROD overlay (digest-pinned)"
	kubectl apply -k $(UI_PROD)
	kubectl rollout status deploy/budget-coach-ui

ui-prod-status:
	@echo "==> Deployment image and envs"
	@kubectl get deploy budget-coach-ui -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
	@kubectl get deploy budget-coach-ui -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'

ui-prod-smoke:
	@echo "==> Agent POST smoke (/api/budget/coach should return 200/400 JSON)"
	@kubectl run curl-ui-prod --rm -it --restart=Never --image=curlimages/curl:8.7.1 -- \
	  sh -lc "set -e; \
	  AG=http://insight-agent.default.svc.cluster.local/api; \
	  curl -sS -D- -X POST -H 'Content-Type: application/json' \
	    -d '{\"account_id\":\"1011226111\",\"window_days\":30}' \
	    $$AG/budget/coach | head -n 20"

ui-prod-verify:
	@echo "==> Deployment image:"
	@kubectl get deploy budget-coach-ui -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
	@echo "==> ReplicaSets -> image:"
	@kubectl get rs -l app=budget-coach-ui \
	  -o 'custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image' || true
	@echo "==> Pod env snapshot:"
	@POD=$$(kubectl get pods -l app=budget-coach-ui -o jsonpath='{.items[?(@.status.phase=="Running")].metadata.name}' | awk '{print $$NF}'); \
	[ -n "$$POD" ] && kubectl exec $$POD -- printenv | egrep '^(USERSVC|MCPSVC|INSIGHT|INSIGHT_URI|INSIGHT_AGENT_URL|ACCOUNT|WINDOW_DAYS)=' || true

# ----- Utilities -----
ui-image:
	@kubectl get deploy budget-coach-ui -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'

ui-rs-clean:
	@echo "==> Deleting ReplicaSets not matching current Deployment image…"
	@CUR=$$(kubectl get deploy budget-coach-ui -o jsonpath='{.spec.template.spec.containers[0].image}'); \
	for rs in $$(kubectl get rs -l app=budget-coach-ui -o name); do \
	  IMG=$$(kubectl get $$rs -o jsonpath='{.spec.template.spec.containers[0].image}'); \
	  echo "$$rs -> $$IMG"; \
	  [ "$$IMG" = "$$CUR" ] || kubectl delete $$rs; \
	done

ui-clean:
	-@kubectl delete svc budget-coach-ui-lb --ignore-not-found
	-@kubectl delete svc budget-coach-ui --ignore-not-found
	-@kubectl delete deploy budget-coach-ui --ignore-not-found
