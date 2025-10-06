.PHONY: smoke-spending
smoke-spending: ## Spending Analyst: MCP -> transform -> /api/spending/analyze
	@echo "==> Spending: transform MCP payload then POST to insight-agent"
	NS=$(NS) ACCT=$(ACCT) WINDOW=$(WINDOW) ./scripts/spending-smoke.sh
