.PHONY: smoke-core
smoke-core: ## Health + Auth smoke
	@echo "==> Health/Auth core smokes"
	NS=$(NS) ./scripts/core-smoke.sh
