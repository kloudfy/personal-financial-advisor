#!/usr/bin/env bash
set -euo pipefail

# Params (env override-able)
NS="${NS:-default}"
ACCT="${ACCT:-1011226111}"
WINDOW="${WINDOW:-30}"
# Optional: FRAUD_FAST=1 (adds ?fast=true to skip LLM if no anomalies)
FRAUD_FAST="${FRAUD_FAST:-}"

echo "==> Fraud: MCP -> transform -> insight-agent /api/fraud/detect (ns=${NS} acct=${ACCT} window=${WINDOW})"

kubectl run fraud-$(date +%s) -n "$NS" --rm -i --restart=Never --image=nicolaka/netshoot -- \
  sh -lc '
set -euo pipefail
ACCT='"$ACCT"'
WINDOW='"$WINDOW"'
FAST_FLAG='"${FRAUD_FAST}"'

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
QS=""
[ -n "$FAST_FLAG" ] && QS="?fast=true"

curl -fsS -m 30 --connect-timeout 3 -H "Authorization: Bearer $T" \
  "http://mcp-server.'"$NS"'.svc.cluster.local/transactions/$ACCT?window_days=$WINDOW" \
| jq --arg acct "$ACCT" -f /tmp/xform.jq \
| jq "{transactions: .}" \
| curl -fsS -m 60 --connect-timeout 3 -X POST -H "Content-Type: application/json" -d @- \
    "http://insight-agent.'"$NS"'.svc.cluster.local/api/fraud/detect$QS" \
| jq -C '"'"'if .findings
             then {overall_risk, n_findings: (.findings|length), sample_finding: (.findings[0])}
             else {note:"model output fallback", overall_risk: (.overall_risk // "unknown"), n_findings: 0, sample_finding: null}
           end'"'"'
'