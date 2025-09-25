#!/usr/bin/env bash
set -euo pipefail

ns="${NS:-default}"
ag_svc="agent-gateway.${ns}.svc.cluster.local"
curl_img="curlimages/curl:8.11.1"

run_curl_job() {
  local name="$1"; shift
  local ns="${NS:-default}"
  local img="curlimages/curl"

  # clean up any previous job
  kubectl -n "$ns" delete job "$name" --ignore-not-found >/dev/null 2>&1

  # create job
  kubectl -n "$ns" create job "$name" --image="$img" -- "$@"

  # wait for completion (prevents empty logs race)
  kubectl -n "$ns" wait --for=condition=complete "job/$name" --timeout=60s

  # print logs
  kubectl -n "$ns" logs "job/$name"

  # delete job (don’t block)
  kubectl -n "$ns" delete job "$name" --wait=false >/dev/null 2>&1
}

echo "==> Checking rollouts..."
kubectl -n "$ns" rollout status deploy/mcp-server
kubectl -n "$ns" rollout status deploy/agent-gateway

echo "==> /healthz"
run_curl_job curl-h curl -s "http://${ag_svc}/healthz"

echo
echo "==> /chat (sample analysis)"
json='{"prompt":"analyze my spend","account_id":"0000000001","window_days":30}'
run_curl_job curl-chat sh -lc "echo '$json' | curl -s -X POST -H 'Content-Type: application/json' -d @- http://${ag_svc}/chat"

echo
echo "==> Done."
