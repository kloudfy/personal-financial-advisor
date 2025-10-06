.PHONY: smoke-e2e
smoke-e2e: ## E2E /chat with JWT
	@echo "==> E2E: /chat"
	NS=$(NS) SMOKE_HEAD=$(SMOKE_HEAD) ACCT=$(ACCT) WINDOW=$(WINDOW) ./scripts/chat-smoke.sh
	@echo "✅ smoke-e2e passed"
