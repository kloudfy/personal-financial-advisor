#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-default}"
ACCT="${ACCT:-1011226111}"
WINDOW="${WINDOW:-30}"

echo "==> Spending: MCP -> transform -> insight-agent /api/spending/analyze (ns=${NS} acct=${ACCT} window=${WINDOW})"

kubectl run spend-$(date +%s) -n "$NS" --rm -i --restart=Never --image=nicolaka/netshoot -- \
  sh -lc '
set -euo pipefail
ACCT='"$ACCT"'
WINDOW='"$WINDOW"'

# 1) JWT
T=$(curl -sS -m 20 --connect-timeout 3 -G \
  --data-urlencode "username=testuser" \
  --data-urlencode "password=bankofanthos" \
  http://userservice.'"$NS"'.svc.cluster.local:8080/login | jq -r .token)
[ -n "$T" ] || { echo "ERROR: empty token" >&2; exit 2; }

# 2) jq transform (robust timestamp)
cat > /tmp/xform.jq <<'"'"'JQ'"'"'
map({
  date: (
    (try (.timestamp | sub("\\.000\\+00:00$"; "Z")) catch null)
    // (try (.timestamp | sub("\\+00:00$"; "Z")) catch null)
    // (.timestamp // "")
  ),
  label: (if .toAccountNum == $acct
          then "Inbound from \(.fromAccountNum)"
          else "Outbound to \(.toAccountNum)" end),
  amount: (if .toAccountNum == $acct then .amount else -(.amount) end)
})
JQ

# 3) Fetch → transform → POST → concise view
curl -fsS -m 30 --connect-timeout 3 -H "Authorization: Bearer $T" \
  "http://mcp-server.'"$NS"'.svc.cluster.local/transactions/$ACCT?window_days=$WINDOW" \
| jq --arg acct "$ACCT" -f /tmp/xform.jq \
| jq "{transactions: .}" \
| curl -fsS -m 60 --connect-timeout 3 -X POST -H "Content-Type: application/json" -d @- \
    http://insight-agent.'"$NS"'.svc.cluster.local/api/spending/analyze \
| jq -C '"'"'{summary, top_categories: (.top_categories[0:3]), n_unusual: ((.unusual_transactions // []) | length)}'"'"'
'