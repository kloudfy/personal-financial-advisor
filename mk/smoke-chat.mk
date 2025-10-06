.PHONY: smoke-chat
smoke-chat: ## E2E: agent-gateway /chat (authorized)
	@echo "==> E2E: agent-gateway /chat (authorized)"
	NS=$(NS) SMOKE_HEAD=$(SMOKE_HEAD) ./scripts/chat-smoke.sh
