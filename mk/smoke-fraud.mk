.PHONY: smoke-fraud
smoke-fraud: ## Fraud Scout: MCP -> transform -> /api/fraud/detect
	@echo "==> Fraud: transform MCP payload then POST to insight-agent /api/fraud/detect"
	NS=$(NS) ./scripts/fraud-smoke.sh
