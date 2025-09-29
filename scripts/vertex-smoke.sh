#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-default}"
JOB="vertex-smoke-$$"
KSA="insight-agent"

echo "==> Creating one-off Job '${JOB}' using prebuilt vertex-smoke image..."
kubectl -n "${NS}" delete job "${JOB}" --ignore-not-found >/dev/null 2>&1 || true

kubectl -n "${NS}" apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
spec:
  backoffLimit: 0
  template:
    spec:
      serviceAccountName: ${KSA}
      restartPolicy: Never
      containers:
      - name: runner
        image: ${REG}/vertex-smoke:latest
        env:
        - name: GOOGLE_CLOUD_PROJECT
          valueFrom:
            configMapKeyRef:
              name: environment-config
              key: GOOGLE_CLOUD_PROJECT
        - name: VERTEX_LOCATION
          value: us-central1
        - name: VERTEX_MODEL
          value: gemini-2.5-pro
EOF

kubectl -n "${NS}" wait --for=condition=complete --timeout=120s job/${JOB}
echo "==> Logs:"
kubectl -n "${NS}" logs job/${JOB}
echo "==> Success."
