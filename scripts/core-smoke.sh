#!/usr/bin/env bash
set -euo pipefail
NS="${NS:-default}"

echo "==> Health: agent-gateway, insight-agent, mcp-server"
kubectl run h1-$(date +%s) -n "$NS" --rm -i --restart=Never --image=curlimages/curl -- \
  sh -lc 'curl -fsS http://agent-gateway.'"$NS"'.svc.cluster.local/healthz >/dev/null && echo agw:OK'
kubectl run h2-$(date +%s) -n "$NS" --rm -i --restart=Never --image=curlimages/curl -- \
  sh -lc 'curl -fsS http://insight-agent.'"$NS"'.svc.cluster.local/api/healthz >/dev/null && echo ia:OK'
kubectl run h3-$(date +%s) -n "$NS" --rm -i --restart=Never --image=curlimages/curl -- \
  sh -lc 'curl -fsS http://mcp-server.'"$NS"'.svc.cluster.local/healthz >/dev/null && echo mcp:OK'

echo "==> Auth: userservice GET /login?username&password"
kubectl run tok-$(date +%s) -n "$NS" --rm -i --restart=Never --image=nicolaka/netshoot -- \
  sh -lc 'set -e; T=$(curl -sS -G --data-urlencode "username=testuser" --data-urlencode "password=bankofanthos" http://userservice.'"$NS"'.svc.cluster.local:8080/login | jq -r .token); L=${#T}; echo "TOKEN_LEN=$L"'
echo "✅ smoke-core passed"
