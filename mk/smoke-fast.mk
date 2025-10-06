.PHONY: smoke-fast
smoke-fast: smoke-core smoke-data smoke-e2e smoke-fraud smoke-spending
	@echo "✅ smoke-fast passed"
.PHONY: demo-check
demo-check: smoke-fast ## Umbrella quick verification (alias)
	@echo "✅ demo-check passed"
