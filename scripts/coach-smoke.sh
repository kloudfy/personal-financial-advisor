#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-default}"
ACCT="${ACCT:-1011226111}"
WINDOW="${WINDOW:-30}"

echo "==> Coach: MCP -> transform -> insight-agent /api/budget/coach (ns=${NS} acct=${ACCT} window=${WINDOW})"

kubectl run coach-$(date +%s) -n "$NS" --rm -i --restart=Never --image=nicolaka/netshoot -- sh -lc '
set -euo pipefail
ACCT='"$ACCT"'
WINDOW='"$WINDOW"'

T=$(curl -sS -G \
  --data-urlencode "username=testuser" \
  --data-urlencode "password=bankofanthos" \
  http://userservice.'"$NS"'.svc.cluster.local:8080/login | jq -r .token)

cat > /tmp/xform.jq <<'"'"'JQ'"'"'
map({
  date: (.timestamp | sub("\\.000\\+00:00$"; "Z")),
  label: (if .toAccountNum == $acct
          then "Inbound from \(.fromAccountNum)"
          else "Outbound to \(.toAccountNum)" end),
  amount: (if .toAccountNum == $acct then .amount else -(.amount) end)
})
JQ

curl -fsS -H "Authorization: Bearer $T" \
  "http://mcp-server.'"$NS"'.svc.cluster.local/transactions/$ACCT?window_days=$WINDOW" \
| jq --arg acct "$ACCT" -f /tmp/xform.jq \
| jq "{transactions: .}" \
| curl -fsS -X POST -H "Content-Type: application/json" -d @- \
    http://insight-agent.'"$NS"'.svc.cluster.local/api/budget/coach \
| jq -C "{summary, top_categories: (.top_categories[0:3])}"
'
