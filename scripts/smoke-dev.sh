#!/usr/bin/env bash
#
# smoke-dev.sh — basic smoke test for dev environment
# - Gets a JWT from userservice
# - Calls agent-gateway /chat with the JWT
# - Retries the AGW call up to 3 times (handles brief MCP cold starts)

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

  # wait for completion
  kubectl -n "$ns" wait --for=condition=complete "job/$name" --timeout=90s

  # print logs
  local pod_name=$(kubectl get pods -n "$ns" -l job-name="$name" -o jsonpath='{.items[0].metadata.name}')
  sleep 1 # Give the system a moment to process job completion before fetching logs
  kubectl -n "$ns" logs "$pod_name"

  # delete job
  kubectl -n "$ns" delete job "$name" --wait=false >/dev/null 2>&1 || true
}

echo "==> Checking rollouts..."
kubectl -n "$ns" rollout status deploy/mcp-server
kubectl -n "$ns" rollout status deploy/agent-gateway

echo
echo ">= Waiting 10s for services to stabilize..."
sleep 10

echo ">= /healthz"
run_curl_job curl-h curl -s "http://${ag_svc}/healthz"

echo ">= /chat (sample analysis)"

# Use a heredoc to define the multi-line command for the job.
# This avoids complex quoting/escaping issues.
# Note 'EOF' is quoted to prevent local variable expansion inside the block.
chat_command=$(cat <<'EOF'
set -eu
# small helper: retry with backoff (2s, 5s, 10s)
curl_agw_with_retry() {
  local url="$1"
  local json_payload="$2"
  local token="$3"
  local attempt=1
  while :; do
    echo "==> Attempt ${attempt}: agent-gateway /chat"
    if curl -sS -H "Authorization: Bearer ${token}" \
          -H "Content-Type: application/json" \
          -X POST -d "${json_payload}" "${url}"; then return 0; fi
    if [ ${attempt} -ge 3 ]; then return 1; fi
    if [ ${attempt} -eq 1 ]; then
      sleep 2s
    elif [ ${attempt} -eq 2 ]; then
      sleep 5s
    elif [ ${attempt} -eq 3 ]; then
      sleep 10s
    else
      sleep 1s
    fi
    attempt=$((attempt+1))
  done
}
TOKEN=$(curl -s "http://userservice.default.svc.cluster.local:8080/login?username=testuser&password=bankofanthos" | sed -E 's/.*"token":"([^"]+)".*/\1/')
if [ -z "$TOKEN" ]; then
  echo "Failed to get token from raw output." >&2
  exit 1
fi
JSON_PAYLOAD='{"prompt":"analyze my spend","account_id":"1011226111","window_days":30}'
AGW_URL="http://agent-gateway.default.svc.cluster.local:80/chat"

# Retry the AGW call up to 3 times (covers transient MCP 502 on cold start)
if ! curl_agw_with_retry "$AGW_URL" "$JSON_PAYLOAD" "$TOKEN"; then
  echo "AGW call failed after retries." >&2
  exit 2
fi
EOF
)

run_curl_job curl-chat sh -c "$chat_command"

echo ">= Done."

exit 0