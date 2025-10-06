#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-default}"
ACCT="${ACCT:-1011226111}"
WINDOW="${WINDOW:-30}"
SMOKE_HEAD="${SMOKE_HEAD:-200}"

echo "==> E2E /chat: userservice -> agent-gateway (ns=${NS}, head=${SMOKE_HEAD})"

kubectl run chat-$(date +%s) -n "$NS" --rm -i --restart=Never --image=nicolaka/netshoot -- sh -lc '
set -eu
T=$(curl -sS -G --data-urlencode "username=testuser" \
              --data-urlencode "password=bankofanthos" \
              http://userservice.'"$NS"'.svc.cluster.local:8080/login | jq -r .token)
echo "TOKEN_LEN=${#T}" 1>&2
[ -n "$T" ] || { echo "ERROR: empty token" 1>&2; exit 2; }

curl -sS -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $T" \
  -d "{\"account_id\":\"'"$ACCT"'\",\"window_days\":'"$WINDOW"', \
       \"messages\":[{\"role\":\"user\",\"content\":\"Give me a 30-day budget summary\"}]}" \
  http://agent-gateway.'"$NS"'.svc.cluster.local/chat \
| head -c '"$SMOKE_HEAD"'; echo
'
