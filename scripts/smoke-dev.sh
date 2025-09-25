#!/usr/bin/env bash
set -euo pipefail

ns="${NS:-default}"
ag_svc="agent-gateway.${ns}.svc.cluster.local"
curl_img="curlimages/curl"
json='{"prompt":"analyze my spend","account_id":"0000000001","window_days":30}'

echo "==> Checking rollouts..."
kubectl -n "$ns" rollout status deploy/mcp-server
kubectl -n "$ns" rollout status deploy/agent-gateway

echo "==> /healthz"
kubectl -n "$ns" run curl-h --rm --restart=Never --image="$curl_img" -- \
  curl -s "http://${ag_svc}/healthz" || true
echo

echo "==> /chat (sample analysis)"
kubectl -n "$ns" run curl-chat --rm --restart=Never --image="$curl_img" -- \
  sh -lc "curl -s -X POST -H 'Content-Type: application/json' -d '$json' http://${ag_svc}/chat" || true
echo

echo "==> Done."
