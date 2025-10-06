.PHONY: smoke-data
smoke-data: ## MCP + Budget Coach smokes
	@echo "==> Data smokes (MCP + Coach)"
	NS=$(NS) ACCT=$(ACCT) WINDOW=$(WINDOW) ./scripts/data-smoke.sh
