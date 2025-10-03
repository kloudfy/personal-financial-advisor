# mk/smoke.mk - portable smoke tests for Bank of Anthos PFA stack
# Compatible with GNU make 3.81 (macOS default)

NS ?= default

.PHONY: smoke-health smoke-auth-token smoke-mcp smoke-coach smoke-chat smoke-all

smoke-health: ## 1) Basic health checks
	@echo "==> Health: agent-gateway, insight-agent, mcp-server"
	kubectl run h1-`date +%s` -n $(NS) --rm -i --restart=Never --image=curlimages/curl -- \
	  sh -lc 'curl -fsS http://agent-gateway.$(NS).svc.cluster.local/healthz >/dev/null && echo agw:OK'
	kubectl run h2-`date +%s` -n $(NS) --rm -i --restart=Never --image=curlimages/curl -- \
	  sh -lc 'curl -fsS http://insight-agent.$(NS).svc.cluster.local/api/healthz >/dev/null && echo ia:OK'
	kubectl run h3-`date +%s` -n $(NS) --rm -i --restart=Never --image=curlimages/curl -- \
	  sh -lc 'curl -fsS http://mcp-server.$(NS).svc.cluster.local/healthz >/dev/null && echo mcp:OK'

smoke-auth-token: ## 2) Get JWT from userservice (prints token length)
	@echo "==> Auth: userservice GET /login?username&password"
	kubectl run tok-`date +%s` -n $(NS) --rm -i --restart=Never --image=nicolaka/netshoot -- \
	  sh -lc 'set -e; T=$$(curl -sS -G \
	    --data-urlencode "username=testuser" \
	    --data-urlencode "password=bankofanthos" \
	    http://userservice.$(NS).svc.cluster.local:8080/login | jq -r .token); \
	    L=$${#T}; echo "TOKEN_LEN=$$L"; echo "$$T" >/tmp/token && cat /tmp/token >/dev/stderr' 2>/dev/null

smoke-mcp: ## 3) MCP honors Authorization, returns JSON (first 3 rows)
	@echo "==> MCP: /transactions (authorized)"
	kubectl run mcp-`date +%s` -n $(NS) --rm -i --restart=Never --image=nicolaka/netshoot -- \
	  sh -lc 'set -e; T=$$(curl -sS -G \
	      --data-urlencode "username=testuser" \
	      --data-urlencode "password=bankofanthos" \
	      http://userservice.$(NS).svc.cluster.local:8080/login | jq -r .token); \
	    curl -fsS -H "Authorization: Bearer $$T" \
	      "http://mcp-server.$(NS).svc.cluster.local/transactions/1011226111?window_days=30" \
	      | jq -C ".[0:3]"'

smoke-coach: ## 4) MCP -> transform -> insight-agent /api/budget/coach
	@echo "==> Coach: transform MCP payload then POST to insight-agent"
	kubectl run coach-`date +%s` -n $(NS) --rm -i --restart=Never --image=nicolaka/netshoot -- \
	  sh -lc 'set -e; ACCT=1011226111; WINDOW=30; \
	    T=$$(curl -sS -G --data-urlencode "username=testuser" --data-urlencode "password=bankofanthos" \
	      http://userservice.$(NS).svc.cluster.local:8080/login | jq -r .token); \
	    curl -fsS -H "Authorization: Bearer $$T" \
	      "http://mcp-server.$(NS).svc.cluster.local/transactions/$$ACCT?window_days=$$WINDOW" \
	    | jq --arg acct "$$ACCT" '\''map({ \
	          date: (.timestamp | sub("\\.000\\+00:00$$"; "Z")), \
	          label: (if .toAccountNum == $$acct then "Inbound from \(.fromAccountNum)" else "Outbound to \(.toAccountNum)" end), \
	          amount: (if .toAccountNum == $$acct then .amount else -(.amount) end) \
	        })'\'' \
	    | jq "{transactions: .}" \
	    | curl -sS -X POST -H "Content-Type: application/json" -d @- \
	      http://insight-agent.$(NS).svc.cluster.local/api/budget/coach \
	    | jq -C "{summary, top_categories: (.top_categories[0:3])}"'

smoke-chat:  ## 5) E2E AGW /chat with JWT
	@echo "==> E2E: agent-gateway /chat (authorized)"
	kubectl run chat-`date +%s` -n $(NS) --rm -i --restart=Never --image=nicolaka/netshoot -- \
	  sh -lc '\
set -eu; \
T=$$(curl -sS -G \
  --data-urlencode "username=testuser" \
  --data-urlencode "password=bankofanthos" \
  http://userservice.$(NS).svc.cluster.local:8080/login | jq -r .token); \
echo "TOKEN_LEN=$${#T}" 1>&2; \
[ -n "$$T" ] || { echo "ERROR: empty token" 1>&2; exit 2; }; \
(curl -sS -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $$T" \
  -d "{\"account_id\":\"1011226111\",\"window_days\":30, \
       \"messages\":[{\"role\":\"user\",\"content\":\"Give me a 30-day budget summary\"}]}" \
  http://agent-gateway.$(NS).svc.cluster.local/chat) \
| head -c $(SMOKE_HEAD); echo'

smoke-all: smoke-health smoke-auth-token smoke-mcp smoke-coach smoke-chat ## Run full portable smoke suite
	@echo "✅ smoke-all passed"

# -------- portable smoke aliases & knobs --------
SMOKE_NS   ?= $(NS)
SMOKE_HEAD ?= 200

.PHONY: smoke-core smoke-data smoke-e2e smoke-fast
smoke-core: smoke-health smoke-auth-token
	@echo "✅ smoke-core passed"

smoke-data: smoke-mcp smoke-coach
	@echo "✅ smoke-data passed"

smoke-e2e: smoke-chat
	@echo "✅ smoke-e2e passed"

# quick one to wrap them all (fast)
smoke-fast: smoke-core smoke-data smoke-e2e
	@echo "✅ smoke-fast passed"
