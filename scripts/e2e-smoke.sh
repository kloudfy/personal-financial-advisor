#!/usr/bin/env bash
set -euo pipefail

ns="${NS:-default}"
ag_svc="agent-gateway.${ns}.svc.cluster.local"

# Function to run a curl command inside a temporary Kubernetes Job
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
  kubectl -n "$ns" logs "job/$name"

  # delete job
  kubectl -n "$ns" delete job "$name" --wait=false >/dev/null 2>&1
}

echo "==> Running E2E authenticated smoke test..."

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

# Run the command in a job
run_curl_job e2e-auth-job sh -c "$chat_command"

echo
echo "==> Done."
