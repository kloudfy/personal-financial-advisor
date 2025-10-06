.PHONY: smoke-coach
smoke-coach: ## Budget Coach: MCP -> transform -> /api/budget/coach
	@echo "==> Coach: transform MCP payload then POST to insight-agent"
	NS=$(NS) ./scripts/coach-smoke.sh
