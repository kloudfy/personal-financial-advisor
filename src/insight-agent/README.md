# insight-agent

Small Flask service that accepts a list of transactions and returns JSON insights
(summary, top categories, unusual transactions). Two deployment variants:

- `main.py` + `Dockerfile` → **Gemini API** (uses `GEMINI_API_KEY` secret).
- `main_vertex.py` + `Dockerfile.vertex` → **Vertex AI** (recommended; Workload Identity, no keys).

## Endpoints
- `GET /healthz` → 200 ok
- `POST /analyze` → body: JSON list or `{ "transactions": [...] }`, returns JSON

## Build & deploy

See top-level README for full commands. Quick gist:

**Gemini API**
1. Build & push image `insight-agent:gemini` with `Dockerfile`.
2. Create secret:
   ```bash
   kubectl create secret generic gemini-api-key        --from-literal=api-key=<YOUR_GEMINI_API_KEY>
   ```
3. Apply `kubernetes-manifests/insight-agent.yaml` (replace `PROJECT/REPO`).

**Vertex AI**
1. Enable API: `gcloud services enable aiplatform.googleapis.com`.
2. Build & push image `insight-agent:vertex` with `Dockerfile.vertex`.
3. Ensure ConfigMap `environment-config` contains `GOOGLE_CLOUD_PROJECT`.
4. Apply `kubernetes-manifests/insight-agent-vertex.yaml` (replace `PROJECT/REPO`).

## Notes
- Dev overlay can keep quiet mode (`MANAGEMENT_METRICS_EXPORT_STACKDRIVER_ENABLED=false`).
- In prod, ensure your KSA ↔ GSA Workload Identity binding is configured if you want logs/metrics publishing.
