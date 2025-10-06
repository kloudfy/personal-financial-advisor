#!/usr/bin/env bash
set -euo pipefail
NS="${NS:-default}"
ACCT="${ACCT:-1011226111}"
WINDOW="${WINDOW:-30}"

echo "==> MCP: /transactions (authorized)"
kubectl run mcp-$(date +%s) -n "$NS" --rm -i --restart=Never --image=nicolaka/netshoot -- \
  sh -lc 'set -e; T=$(curl -sS -G --data-urlencode "username=testuser" --data-urlencode "password=bankofanthos" http://userservice.'"$NS"'.svc.cluster.local:8080/login | jq -r .token); \
          curl -fsS -H "Authorization: Bearer $T" "http://mcp-server.'"$NS"'.svc.cluster.local/transactions/'"$ACCT"'?window_days='"$WINDOW"'" | jq -C ".[0:3]"'

echo "==> Coach: transform MCP payload then POST to insight-agent"
NS="$NS" ACCT="$ACCT" WINDOW="$WINDOW" ./scripts/coach-smoke.sh
echo "✅ smoke-data passed"
