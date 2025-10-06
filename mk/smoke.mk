SMOKE_HEAD ?= 200
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




smoke-all: smoke-health smoke-auth-token smoke-mcp smoke-coach smoke-chat ## Run full portable smoke suite
	@echo "✅ smoke-all passed"

