.PHONY: smoke-fast
smoke-fast: smoke-core smoke-data smoke-e2e smoke-fraud ## Run quick suite incl. Fraud
	@echo "✅ smoke-fast passed"
