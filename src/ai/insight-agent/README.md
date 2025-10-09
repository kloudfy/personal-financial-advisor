# insight-agent

A small **FastAPI** service that accepts a list of bank transactions and returns **strict JSON** insights
(summary, budget buckets / top categories, unusual transactions). Two deployment variants:

- `main.py` + `Dockerfile` → **Gemini API** (uses `GEMINI_API_KEY` secret).
- `main_vertex.py` + `Dockerfile.vertex` → **Vertex AI** via **google-genai** (recommended; Workload Identity, no keys).

Key features (Vertex build):

- JSON Mode + response **schemas** for reliable contracts
- Per-pod **throttling** and **exponential backoff** retries
- **Prompt provenance** header: `X-Insight-Prompt: <key>@<sha8>`
- Prompts externalized (`prompts.yaml`) with Kustomize-friendly mount

---

## Endpoints (Vertex build)

- `GET  /api/healthz` → `{"status":"ok"}`
- `POST /api/budget/coach`
- `POST /api/spending/analyze`
- `POST /api/fraud/detect`

**Request body (all POSTs)**

```json
{
  "transactions": [
    { "date": "2025-09-22", "label": "Inbound from 9099791699", "amount": 250000.0 }
  ]
}
```

Required transaction fields (Pydantic)
*date — string (YYYY-MM-DD or ISO date)
*label — string
*amount — number (positive = income, negative = expense)

Breaking change: older UIs that send timestamp must be updated to send date.

Example (spending)

```bash
curl -sS -X POST http://localhost:8083/api/spending/analyze \
  -H 'Content-Type: application/json' \
  -d '{"transactions":[{"date":"2025-09-22","label":"Inbound","amount":250000}]}' | jq .
```

Provenance header

```bash
curl -isS -X POST http://localhost:8083/api/spending/analyze \
  -H 'Content-Type: application/json' \
  -d '{"transactions":[{"date":"2025-09-22","label":"Inbound","amount":250000}]}' \
  | grep -i ^X-Insight-Prompt
# → X-Insight-Prompt: spending_analyze@<sha8>
```

If a browser must read this header (CORS), configure your ingress/LB to expose it:

`Access-Control-Expose-Headers: X-Insight-Prompt`


---

Configuration (env)

| Variable | Default | Notes |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | — | Required for Vertex. |
| `VERTEX_LOCATION` | `us-central1` | Vertex region. |
| `VERTEX_MODEL` | `gemini-2.5-pro` | google-genai model id. |
| `MAX_TRANSACTIONS_PER_PROMPT` | `50` | Caps txns sent to model. |
| `GENAI_MAX_TOKENS` | `2048` | Max output tokens. |
| `GENAI_THINK_TOKENS` | `1024` | Thinking budget (clamped per model). |
| `GENAI_CONCURRENCY` | `2` | In-pod semaphore size. |
| `GENAI_RPM` | `18` | Per-pod requests-per-minute throttle. |
| `PROMPTS_FILE` | `/app/prompts.yaml` | Externalized prompts path (ConfigMap mount friendly). |
| `LOG_LEVEL` | `INFO` | Set DEBUG for verbose logs. |


---

Prompts (decoupled, zero code changes)

Prompts live in `prompts.yaml`. Mount them via Kustomize ConfigMap so editing only `prompts.yaml` triggers a rollout with updated templates.

Overlay: `src/ai/insight-agent/k8s/overlays/development/kustomization.yaml`

```yaml
configMapGenerator:
  - name: insight-prompts
    files:
      - prompts.yaml=../../../prompts.yaml
generatorOptions:
  disableNameSuffixHash: false  # any edit → new name → rollout

patchesStrategicMerge:
  - patch-prompts-volume.yaml
```

Patch: `src/ai/insight-agent/k8s/overlays/development/patch-prompts-volume.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: insight-agent
spec:
  template:
    spec:
      volumes:
        - name: insight-prompts
          configMap:
            name: insight-prompts  # kustomize rewrites to hashed name
      containers:
        - name: insight-agent
          env:
            - name: PROMPTS_FILE
              value: /app/prompts/prompts.yaml
          volumeMounts:
            - name: insight-prompts
              mountPath: /app/prompts
              readOnly: true
```

Apply:

```bash
kubectl -n default apply -k src/ai/insight-agent/k8s/overlays/development
kubectl -n default rollout status deploy/insight-agent
```


---

Build & deploy

**Vertex AI (recommended)**

```bash
# Auth to Artifact Registry (once)
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Build & push image
docker buildx build --platform linux/amd64 \
  -t ${REG}/insight-agent:vertex \
  -f src/ai/insight-agent/Dockerfile.vertex src/ai/insight-agent --push

# Apply dev overlay
kustomize build src/ai/insight-agent/k8s/overlays/development | kubectl apply -f - 
kubectl -n default rollout status deploy/insight-agent
```

**Gemini API (optional/simple)**

```bash
# Create secret for API key (if your Dockerfile uses it)
kubectl create secret generic gemini-api-key \
  --from-literal=api-key=<YOUR_GEMINI_API_KEY> \
  --dry-run=client -o yaml | kubectl apply -f -

# Build & push
docker buildx build --platform linux/amd64 \
  -t ${REG}/insight-agent:gemini \
  -f src/ai/insight-agent/Dockerfile src/ai/insight-agent --push

# Apply overlay
kustomize build src/ai/insight-agent/k8s/overlays/development | kubectl apply -f -
kubectl -n default rollout status deploy/insight-agent
```


---

Quick smokes

```bash
# Health
curl -sS http://localhost:8083/api/healthz | jq .

# Budget Coach
curl -sS -X POST http://localhost:8083/api/budget/coach \
  -H 'Content-Type: application/json' \
  -d '{"transactions":[{"date":"2025-09-22","label":"Inbound","amount":250000}]}' | jq .

# Spending (provenance header demo)
curl -isS -X POST http://localhost:8083/api/spending/analyze \
  -H 'Content-Type: application/json' \
  -d '{"transactions":[{"date":"2025-09-22","label":"Coffee","amount":-5}]}' \
  | sed -n '1,25p'
```


---

Troubleshooting
*   **HTTP 422 (Unprocessable Entity)**
    Caller sent legacy fields (e.g., `timestamp`). Update to `{date,label,amount}`.
*   **Timeout to mcp-server from UI**
    Ensure the UI talks to in-cluster services:
    `USERSVC=http://userservice:8080`, `MCPSVC=http://mcp-server:8080`, `INSIGHT=http://insight-agent/api`
    Verify with `kubectl get svc` and a busybox `curl`.
*   **429 / rate limiting**
    Lower `GENAI_CONCURRENCY` or `GENAI_RPM`. Backoff honors `Retry-After`.
*   **Missing provenance header in browser**
    Add `Access-Control-Expose-Headers: X-Insight-Prompt` at the ingress/LB.

---

Notes
*   Vertex AI path uses Workload Identity (no API keys).
*   Enable Vertex API once per project:

```bash

gcloud services enable aiplatform.googleapis.com
```


*   Production overlays should pin images by digest and lock down RBAC as appropriate.