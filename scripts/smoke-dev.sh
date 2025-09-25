#!/usr/bin/env bash
set -euo pipefail

ns="${NS:-default}"
ag_svc="agent-gateway.${ns}.svc.cluster.local"
curl_img="curlimages/curl:8.11.1"

echo "==> Checking rollouts..."
kubectl -n "$ns" rollout status deploy/mcp-server
kubectl -n "$ns" rollout status deploy/agent-gateway

run_and_capture () {
  local name="$1"
  shift
  # Ensure a clean slate
  kubectl -n "$ns" delete pod "$name" --ignore-not-found=true >/dev/null 2>&1 || true

  # Start the ephemeral pod (no attach, no TTY)
  kubectl -n "$ns" run "$name" --restart=Never --image="$curl_img" -- \
    sh -lc "$*"

  # Wait for it to complete (Succeeded) or time out gracefully
  kubectl -n "$ns" wait --for=condition=Succeeded "pod/$name" --timeout=60s >/dev/null 2>&1 || true

  # Print whatever the container wrote to stdout/stderr
  kubectl -n "$ns" logs "$name" || true

  # Clean up without waiting
  kubectl -n "$ns" delete pod "$name" --wait=false >/dev/null 2>&1 || true
}

echo "==> /healthz"
run_and_capture curl-h "curl -s http://${ag_svc}/healthz"

echo
echo "==> /chat (sample analysis)"
json='{"prompt":"analyze my spend","account_id":"0000000001","window_days":30}'
run_and_capture curl-chat "curl -s -X POST -H 'Content-Type: application/json' -d '$json' http://${ag_svc}/chat"

echo
echo "==> Done."