#!/usr/bin/env bash
set -euo pipefail

ns="${NS:-default}"
ag_svc="agent-gateway.${ns}.svc.cluster.local"

run_curl_job() {
  local name="$1"; shift
  local ns="${NS:-default}"
  local img="curlimages/curl:8.1.1"

  # clean up any previous job
  kubectl -n "$ns" delete job "$name" --ignore-not-found >/dev/null 2>&1

  # create job
  kubectl -n "$ns" create job "$name" --image="$img" -- "$@"

  # wait for completion (prevents empty logs race)
  kubectl -n "$ns" wait --for=condition=complete "job/$name" --timeout=90s

  # print logs
  kubectl -n "$ns" logs "job/$name"

  # delete job (don’t block)
  kubectl -n "$ns" delete job "$name" --wait=false >/dev/null 2>&1
}

echo "==> Checking rollouts..."
kubectl -n "$ns" rollout status deploy/mcp-server
kubectl -n "$ns" rollout status deploy/agent-gateway

echo
echo "==> Waiting 10s for services to stabilize..."
sleep 10

echo "==> /healthz"
run_curl_job curl-h curl -s "http://${ag_svc}/healthz"

echo
echo "==> /chat (sample analysis)"

# Use a heredoc to define the multi-line command for the job.
# This avoids complex quoting/escaping issues.
# Note 'EOF' is quoted to prevent local variable expansion inside the block.
chat_command=$(cat <<'EOF'
set -eu
TOKEN=$(curl -s "http://userservice.default.svc.cluster.local:8080/login?username=testuser&password=bankofanthos" | sed -E 's/.*"token":"([^"]+)".*/\1/')
if [ -z "$TOKEN" ]; then
  echo "Failed to get token" >&2
  exit 1
fi
JSON_PAYLOAD='{"prompt":"analyze my spend","account_id":"1011226111","window_days":30}'
curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d "$JSON_PAYLOAD" "http://agent-gateway.default.svc.cluster.local/chat"
EOF
)

run_curl_job curl-chat sh -c "$chat_command"

echo
echo "==> Done."