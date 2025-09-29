#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-default}"
JOB="vertex-smoke-$$"
KSA="insight-agent"

cat <<'PY' > /tmp/vertex_smoke.py
from google.cloud import aiplatform
from vertexai import init
from vertexai.generative_models import GenerativeModel
import os

project = os.environ.get("GOOGLE_CLOUD_PROJECT")
location = os.environ.get("VERTEX_LOCATION", "us-central1")
model_id = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")

init(project=project, location=location)
model = GenerativeModel(model_id)
resp = model.generate_content("Reply with exactly: OK")
text = resp.text.strip()
print(text)
assert text == "OK", f"Unexpected response: {text!r}"
PY

echo "==> Creating one-off Job '${JOB}' to verify Vertex AI access via Workload Identity..."
kubectl -n "${NS}" delete job "${JOB}" --ignore-not-found >/dev/null 2>&1 || true
kubectl -n "${NS}" delete configmap vertex-smoke-src --ignore-not-found >/dev/null 2>&1 || true

kubectl -n "${NS}" create configmap vertex-smoke-src --from-file=/tmp/vertex_smoke.py

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
        image: python:3.11-slim
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
        command: ["/bin/sh","-lc"]
        args:
        - |
          python -V
          pip install --no-cache-dir --upgrade google-cloud-aiplatform "vertexai>=1.66.0"
          python /work/vertex_smoke.py
        volumeMounts:
        - name: work
          mountPath: /work
      volumes:
      - name: work
        configMap:
          name: vertex-smoke-src
EOF

kubectl -n "${NS}" wait --for=condition=complete --timeout=180s job/${JOB}
echo "==> Logs:"
kubectl -n "${NS}" logs job/${JOB}
echo "==> Success."