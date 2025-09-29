#!/usr/bin/env bash
set -euo pipefail

# One-off cluster smoke for Budget Coach:
#  - Fetch JWT from userservice
#  - Pull transactions via mcp-server (authorized)
#  - POST to insight-agent /budget/coach

NS="${NS:-default}"
JOB="budget-smoke-$$"
ACCOUNT="${ACCOUNT:-1011226111}"
WINDOW_DAYS="${WINDOW_DAYS:-30}"

cat <<'PY' > /tmp/budget_smoke.py
import os, sys, json, time, re
import urllib.request as u

def http_get(url, headers=None, timeout=10):
  req = u.Request(url, headers=headers or {}, method="GET")
  with u.urlopen(req, timeout=timeout) as r:
    return r.read().decode("utf-8")

def http_post(url, data, headers=None, timeout=15):
  body = json.dumps(data).encode("utf-8")
  h = {"Content-Type":"application/json"}
  if headers: h.update(headers)
  req = u.Request(url, data=body, headers=h, method="POST")
  with u.urlopen(req, timeout=timeout) as r:
    return r.read().decode("utf-8")

def main():
  acct = os.getenv("ACCOUNT")
  window = os.getenv("WINDOW_DAYS","30")
  # 1) JWT
  raw = http_get("http://userservice.default.svc.cluster.local:8080/login?username=testuser&password=bankofanthos")
  m = re.search(r'"token":"([^"]+)"', raw)
  if not m:
    print("Failed to obtain JWT", file=sys.stderr)
    sys.exit(2)
  jwt = m.group(1)
  # 2) Transactions (via MCP)
  tx_raw = http_get(f"http://mcp-server.default.svc.cluster.local/transactions/{acct}?window_days={window}",
                    headers={"Authorization": f"Bearer {jwt}"})
  try:
    tx = json.loads(tx_raw)
  except Exception:
    print("Transactions response not JSON:", tx_raw[:500], file=sys.stderr)
    sys.exit(3)
  # 3) Budget coach
  resp_raw = http_post("http://insight-agent.default.svc.cluster.local:8080/budget/coach",
                       {"transactions": tx})
  try:
    obj = json.loads(resp_raw)
  except Exception:
    print("Budget coach response not JSON:", resp_raw[:500], file=sys.stderr)
    sys.exit(4)
  # Basic assertions
  for k in ("summary","budget_buckets","tips"):
    if k not in obj:
      print("Missing key:", k, "in", obj, file=sys.stderr)
      sys.exit(5)
  print(json.dumps(obj, indent=2)[:2000])

if __name__ == "__main__":
  main()
PY

echo "==> Creating one-off Job '${JOB}' for Budget Coach smoke..."
kubectl -n "${NS}" delete job "${JOB}" --ignore-not-found >/dev/null 2>&1 || true
kubectl -n "${NS}" delete configmap budget-smoke-src --ignore-not-found >/dev/null 2>&1 || true
kubectl -n "${NS}" create configmap budget-smoke-src --from-file=/tmp/budget_smoke.py

kubectl -n "${NS}" apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
spec:
  backoffLimit: 0
  template:
    spec:
      serviceAccountName: insight-agent
      restartPolicy: Never
      containers:
      - name: runner
        image: python:3.11-slim
        env:
        - name: ACCOUNT
          value: "${ACCOUNT}"
        - name: WINDOW_DAYS
          value: "${WINDOW_DAYS}"
        command: ["/bin/sh","-lc"]
        args:
        - |
          python -V
          python /work/budget_smoke.py
        volumeMounts:
        - name: work
          mountPath: /work
      volumes:
      - name: work
        configMap:
          name: budget-smoke-src
EOF

kubectl -n "${NS}" wait --for=condition=complete --timeout=180s job/${JOB}
echo "==> Logs:"
kubectl -n "${NS}" logs job/${JOB}
echo "==> Success."
