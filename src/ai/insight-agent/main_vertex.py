import os
import json
import logging
from flask import Flask, Blueprint, jsonify, request
import vertexai
from vertexai.generative_models import GenerativeModel

# ------------------------------------------------------------------------------
# App & logging
# ------------------------------------------------------------------------------
app = Flask(__name__)
api = Blueprint("api", __name__, url_prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("insight-agent")

# ------------------------------------------------------------------------------
# Vertex init (project/location from env or defaults)
# ------------------------------------------------------------------------------
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT")
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

if not PROJECT:
    log.warning("GOOGLE_CLOUD_PROJECT not set; Vertex calls will fail. "
                "Ensure ConfigMap 'vertex-config' is applied and WI is configured.")

try:
    vertexai.init(project=PROJECT, location=LOCATION)
    model = GenerativeModel("gemini-2.5-pro")
    log.info("Vertex AI initialized: project=%s location=%s", PROJECT, LOCATION)
except Exception as e:
    model = None
    log.exception("Failed to init Vertex AI: %s", e)

# ------------------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------------------
def _json_required():
    data = request.get_json(silent=True)
    if not data:
        return None, (jsonify(error="No JSON body"), 400)
    return data, None

def _ensure_transactions(data):
    txns = data if isinstance(data, list) else data.get("transactions")
    if not txns:
        return None, (jsonify(error="Expected JSON list or {'transactions': [...]}"), 400)
    return txns, None

def _call_gemini(prompt: str):
    if model is None:
        return None, (jsonify(error="Vertex model not initialized"), 500)
    try:
        resp = model.generate_content(prompt)
        text = (getattr(resp, "text", "") or "").strip()
        cleaned = text.replace("```json", "").replace("```", "").strip()
        # Ensure valid JSON string response
        try:
            _ = json.loads(cleaned)
            return cleaned, None
        except Exception:
            # Fallback: wrap as JSON payload
            return json.dumps({"summary": cleaned}), None
    except Exception as e:
        log.exception("Vertex Gemini call failed")
        return None, (jsonify(error=str(e)), 500)

# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return "ok", 200

# ------------------------------------------------------------------------------
# Budget Coach
# ------------------------------------------------------------------------------
@api.post("/budget/coach")
def budget_coach():
    data, err = _json_required()
    if err: return err
    txns, err = _ensure_transactions(data)
    if err: return err

    prompt = f"""
You are a personal financial advisor. Analyze the following bank transactions and provide:
1) A concise paragraph summarizing spending/income habits over the period.
2) A short list of budget buckets with brief rationales.
3) 3 actionable tips to improve budgeting.

Respond strictly as JSON with keys: summary, buckets, tips.
Transactions:
{txns}
"""
    out, err = _call_gemini(prompt)
    if err: return err
    return out, 200, {"Content-Type": "application/json"}

# ------------------------------------------------------------------------------
# Spending Analysis
# ------------------------------------------------------------------------------
@api.post("/spending/analyze")
def spending_analyze():
    data, err = _json_required()
    if err: return err
    txns, err = _ensure_transactions(data)
    if err: return err

    prompt = f"""
You are a spending analyst. Review the transactions and return:
- "summary": concise monthly/weekly spend picture.
- "top_categories": array of {{name, total, count}} (3–5 items).
- "unusual": array of suspicious or outlier items (with brief reason).

Strictly return JSON with keys: summary, top_categories, unusual.
Transactions:
{txns}
"""
    out, err = _call_gemini(prompt)
    if err: return err
    return out, 200, {"Content-Type": "application/json"}

# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.register_blueprint(api)
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    # Ensure blueprint is registered when run by gunicorn
    app.register_blueprint(api)
