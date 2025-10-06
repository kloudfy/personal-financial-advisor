import os
import json
import logging
from flask import Flask, jsonify, request

# Vertex AI (server-side) SDK
from vertexai import init as vertex_init
from vertexai.generative_models import GenerativeModel

app = Flask(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("insight-agent")

# ------------------------------------------------------------------------------
# Config / Init
# ------------------------------------------------------------------------------
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")  # may be empty if default creds
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
MODEL_ID = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")

try:
    vertex_init(project=PROJECT or None, location=LOCATION)
    log.info("Vertex AI initialized: project=%s location=%s", PROJECT, LOCATION)
except Exception as e:
    log.exception("Vertex AI init failed: %s", e)

model = GenerativeModel(MODEL_ID)

# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
@app.get("/api/healthz")
def healthz():
    return "ok", 200

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def _extract_transactions(payload):
    """
    Accepts either:
      - {"transactions": [...]}  OR
      - [...]
    Returns the list (or None).
    """
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("transactions")
    return None

def _to_json_response(text: str):
    """
    Vertex/Gemini often wraps JSON in markdown fences. Strip them.
    """
    cleaned = (text or "").strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    try:
        json.loads(cleaned)
        return cleaned, 200, {"Content-Type": "application/json"}
    except Exception:
        return json.dumps({"raw": cleaned}, ensure_ascii=False), 200, {"Content-Type": "application/json"}

# ------------------------------------------------------------------------------
# Existing coach endpoint (kept for UI)
# ------------------------------------------------------------------------------
@app.post("/api/budget/coach")
def budget_coach():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]} G"), 400

    prompt = f"""
You are a helpful personal financial coach. Analyze the user's bank transactions.
Return strictly JSON with keys:
  summary: short paragraph summarizing spending/income pattern
  buckets: array of {{name, total, count}} for 3–6 meaningful categories
  tips: array of 3 short actionable suggestions

Transactions:
{txns}
"""
    try:
        resp = model.generate_content(prompt)
        return _to_json_response(getattr(resp, "text", "") or "")
    except Exception as e:
        log.exception("Gemini coach failed")
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# Spending Analysis endpoint
# ------------------------------------------------------------------------------
@app.post("/api/spending/analyze")
def spending_analyze():
    """
    Input:
      {
        "transactions": [
          {"date":"YYYY-MM-DD","label":"..","amount":-123.45}, ...
        ]
      }
    Or just the list above.
    Output (strict JSON):
      {
        "summary": "...",
        "top_categories": [ {"name":"...", "total":1234.56, "count":10}, ... ],
        "unusual_transactions": [ {"date":"...","label":"...","amount":...}, ... ]
      }
    """
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

    prompt = f"""
You are a personal financial analyst. Given these transactions, produce JSON ONLY with:
  summary: one concise paragraph about spending patterns
  top_categories: top 3–5 categories as an array of objects {{name, total, count}}
  unusual_transactions: any outliers/anomalies as an array of the original objects

Rules:
- Do not include any prose outside JSON.
- If amounts are negative for expenses and positive for income, treat negatives as spending.
- Categories should be human-meaningful based on labels.
- Keep numbers reasonable (two decimal places).

Transactions:
{txns}
"""
    try:
        resp = model.generate_content(prompt)
        return _to_json_response(getattr(resp, "text", "") or "")
    except Exception as e:
        log.exception("Gemini spending_analyze failed")
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# NEW: Fraud Detection endpoint (Gemini 2.5 Pro)
# ------------------------------------------------------------------------------
@app.post("/api/fraud/detect")
def fraud_detect():
    """
    Input:
      { "transactions": [...], "account_context": {...} }  OR just [...]
    Output (strict JSON):
      {
        "findings": [
          {
            "transaction": {...},
            "risk_score": 0.0-1.0,
            "indicators": ["..."],
            "reason": "...",
            "recommendation": "..."
          }
        ],
        "overall_risk": "low|medium|high",
        "summary": "..."
      }
    """
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

    # Optional fast-path: only escalate to Gemini if statistical outliers exist
    use_fast_screen = request.args.get("fast", "false").lower() == "true"
    if use_fast_screen:
        from statistics import mean, pstdev
        amounts = [abs(float(t.get("amount", 0))) for t in txns if isinstance(t, dict)]
        if amounts:
            mu = mean(amounts)
            sigma = pstdev(amounts) if len(amounts) > 1 else 0.0
            # Avoid zero sigma: use + 3*max(sigma, 1e-9)
            anomalies = [
                t for t in txns
                if abs(float(t.get("amount", 0))) > mu + 3 * (sigma if sigma > 1e-9 else 1e-9)
            ]
            if not anomalies:
                return jsonify({
                    "findings": [],
                    "overall_risk": "low",
                    "summary": "No statistical anomalies detected in transaction amounts."
                }), 200
            txns = anomalies

    context = data.get("account_context", {}) if isinstance(data, dict) else {}

    prompt = f"""
You are a fraud detection analyst for a personal finance app.

Transactions to analyze:
{json.dumps(txns[:30], indent=2)}

{"Account baseline: " + json.dumps(context, indent=2) if context else "No historical baseline provided."}

Detect fraud indicators:
- Amount anomalies (unusually high/low)
- Suspicious merchant names (typos, generic names, crypto)
- Geographic inconsistencies
- Rapid succession of transactions (velocity)
- Round dollar amounts
- Unusual transaction times
- Duplicate charges

Return ONLY valid JSON:
{{
  "findings": [
    {{
      "transaction": {{"date": "...", "label": "...", "amount": ...}},
      "risk_score": 0.85,
      "indicators": ["unusual_amount", "suspicious_merchant"],
      "reason": "specific explanation",
      "recommendation": "actionable advice"
    }}
  ],
  "overall_risk": "low|medium|high",
  "summary": "brief assessment"
}}

Be conservative - legitimate large purchases are common. Focus on truly suspicious patterns.
"""
    try:
        resp = model.generate_content(prompt)
        return _to_json_response(getattr(resp, "text", "") or "")
    except Exception as e:
        log.exception("Gemini fraud_detect failed")
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    # Gunicorn in container handles prod; this is only for local debug.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
