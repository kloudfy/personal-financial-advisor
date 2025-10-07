import os
import json
import logging
import warnings
from flask import Flask, jsonify, request

# Vertex AI (server-side) SDK
from vertexai import init as vertex_init
from vertexai.generative_models import GenerativeModel

# Optional response_schema support (try stable then preview)
try:
    from vertexai.generative_models import Schema  # newer SDKs
    HAS_SCHEMA = True
except Exception:
    try:
        from vertexai.preview.generative_models import Schema  # older SDKs
        HAS_SCHEMA = True
    except Exception:
        Schema = None
        HAS_SCHEMA = False

# Silence Vertex SDK deprecation chatter in logs
warnings.filterwarnings(
    "ignore",
    message="This feature is deprecated.*",
    category=UserWarning,
    module="vertexai.*",
)

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

# Deterministic + JSON outputs (tunable via env)
GEN_CONFIG = {
    "temperature": float(os.environ.get("INSIGHT_TEMP", "0.0")),
    "top_p": float(os.environ.get("INSIGHT_TOP_P", "0.1")),
    "top_k": int(os.environ.get("INSIGHT_TOP_K", "40")),
    "max_output_tokens": int(os.environ.get("INSIGHT_MAX_TOKENS", "768")),
    "response_mime_type": "application/json",
}

def _cfg_with_schema(schema_obj_or_none):
    cfg = GEN_CONFIG.copy()
    if HAS_SCHEMA and schema_obj_or_none:
        cfg["response_schema"] = schema_obj_or_none
    return cfg

# ------------------------------------------------------------------------------
# Optional strict schemas (if SDK supports Schema)
# ------------------------------------------------------------------------------
if HAS_SCHEMA:
    COACH_SCHEMA = Schema(
        type=Schema.Type.OBJECT,
        properties={
            "summary": Schema(type=Schema.Type.STRING),
            "buckets": Schema(
                type=Schema.Type.ARRAY,
                items=Schema(
                    type=Schema.Type.OBJECT,
                    properties={
                        "name": Schema(type=Schema.Type.STRING),
                        "total": Schema(type=Schema.Type.NUMBER),
                        "count": Schema(type=Schema.Type.INTEGER),
                    },
                    required=["name", "total", "count"],
                ),
            ),
            "tips": Schema(type=Schema.Type.ARRAY, items=Schema(type=Schema.Type.STRING)),
        },
        required=["summary", "buckets", "tips"],
    )

    SPENDING_SCHEMA = Schema(
        type=Schema.Type.OBJECT,
        properties={
            "summary": Schema(type=Schema.Type.STRING),
            "top_categories": Schema(
                type=Schema.Type.ARRAY,
                items=Schema(
                    type=Schema.Type.OBJECT,
                    properties={
                        "name": Schema(type=Schema.Type.STRING),
                        "total": Schema(type=Schema.Type.NUMBER),
                        "count": Schema(type=Schema.Type.INTEGER),
                    },
                    required=["name", "total", "count"],
                ),
            ),
            "unusual_transactions": Schema(
                type=Schema.Type.ARRAY,
                items=Schema(
                    type=Schema.Type.OBJECT,
                    properties={
                        "date": Schema(type=Schema.Type.STRING),
                        "label": Schema(type=Schema.Type.STRING),
                        "amount": Schema(type=Schema.Type.NUMBER),
                    },
                    required=["date", "label", "amount"],
                ),
            ),
        },
        required=["summary", "top_categories", "unusual_transactions"],
    )

    FRAUD_SCHEMA = Schema(
        type=Schema.Type.OBJECT,
        properties={
            "findings": Schema(
                type=Schema.Type.ARRAY,
                items=Schema(
                    type=Schema.Type.OBJECT,
                    properties={
                        "transaction": Schema(
                            type=Schema.Type.OBJECT,
                            properties={
                                "date": Schema(type=Schema.Type.STRING),
                                "label": Schema(type=Schema.Type.STRING),
                                "amount": Schema(type=Schema.Type.NUMBER),
                            },
                            required=["date", "label", "amount"],
                        ),
                        "risk_score": Schema(type=Schema.Type.NUMBER),
                        "indicators": Schema(type=Schema.Type.ARRAY, items=Schema(type=Schema.Type.STRING)),
                        "reason": Schema(type=Schema.Type.STRING),
                        "recommendation": Schema(type=Schema.Type.STRING),
                    },
                    required=["transaction", "risk_score", "indicators", "reason", "recommendation"],
                ),
            ),
            "overall_risk": Schema(type=Schema.Type.STRING, enum=["low", "medium", "high"]),
            "summary": Schema(type=Schema.Type.STRING),
        },
        required=["findings", "overall_risk", "summary"],
    )
else:
    COACH_SCHEMA = SPENDING_SCHEMA = FRAUD_SCHEMA = None

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

def _resp_to_text(resp) -> str:
    """Robustly extract text from Vertex responses (handles multi-part)."""
    # 1) Preferred convenience property
    t = getattr(resp, "text", None)
    if t:
        return t

    # 2) Aggregate candidates/parts text
    try:
        pieces = []
        for cand in getattr(resp, "candidates", []) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                pt = getattr(part, "text", None)
                if pt:
                    pieces.append(pt)
        if pieces:
            return "\n".join(pieces)
    except Exception:
        pass

    # 3) Last-ditch: to_dict() or str()
    try:
        return json.dumps(resp.to_dict())
    except Exception:
        return str(resp)

def _clean_fences(text: str) -> str:
    cleaned = (text or "").strip()
    for fence in ("```json", "```JSON", "```"):
        cleaned = cleaned.replace(fence, "")
    return cleaned.strip()

def _extract_balanced_json(s: str):
    """
    Return the first balanced top-level JSON object/array as a string, or None.
    """
    start = None
    for i, ch in enumerate(s):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None
    open_ch = s[start]
    close_ch = "}" if open_ch == "{" else "]"

    depth = 0
    for j in range(start, len(s)):
        c = s[j]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return s[start:j+1]
    return None

def _to_json_response(text: str):
    """
    Try very hard to return valid JSON from model output:
      1) strip code fences
      2) json.loads directly
      3) extract a balanced {...} or [...] region and parse
      4) structured fallback
    """
    cleaned = _clean_fences(text)

    try:
        obj = json.loads(cleaned)
        return json.dumps(obj), 200, {"Content-Type": "application/json"}
    except Exception:
        pass

    candidate = _extract_balanced_json(cleaned)
    if candidate:
        try:
            obj = json.loads(candidate)
            return json.dumps(obj), 200, {"Content-Type": "application/json"}
        except Exception:
            pass

    fallback = {
        "summary": "Model returned non-JSON output. Treating as low risk / empty analysis.",
        "findings": [],
        "overall_risk": "low",
        "top_categories": [],
        "unusual_transactions": [],
        "buckets": [],
        "tips": []
    }
    return json.dumps(fallback), 200, {"Content-Type": "application/json"}

# ------------------------------------------------------------------------------
# Budget Coach (for UI)
# ------------------------------------------------------------------------------
@app.post("/api/budget/coach")
def budget_coach():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

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
        resp = model.generate_content(prompt, generation_config=_cfg_with_schema(COACH_SCHEMA))
        return _to_json_response(_resp_to_text(resp) or "")
    except Exception as e:
        log.exception("Gemini coach failed")
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# Spending Analysis
# ------------------------------------------------------------------------------
@app.post("/api/spending/analyze")
def spending_analyze():
    """
    Input may be just a list, or {"transactions":[...]}.
    Output:
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
        resp = model.generate_content(prompt, generation_config=_cfg_with_schema(SPENDING_SCHEMA))
        return _to_json_response(_resp_to_text(resp) or "")
    except Exception as e:
        log.exception("Gemini spending_analyze failed")
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# Fraud Detection
# ------------------------------------------------------------------------------
@app.post("/api/fraud/detect")
def fraud_detect():
    """
    Input:
      { "transactions": [...], "account_context": {...} }  OR just [...]
    Output (strict JSON):
      {
        "findings": [ { "transaction": {...}, "risk_score": 0.0-1.0, "indicators": [], "reason": "...", "recommendation": "..." }, ... ],
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
    baseline_text = (
        f"Account baseline:\n{json.dumps(context, indent=2)}"
        if context else
        "No historical baseline provided."
    )

    prompt = f"""
You are a fraud detection analyst for a personal finance app.

Transactions to analyze:
{json.dumps(txns[:30], indent=2)}

{baseline_text}

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
      "transaction": {{"date": "...", "label": "...", "amount": 0}},
      "risk_score": 0.85,
      "indicators": ["unusual_amount", "suspicious_merchant"],
      "reason": "specific explanation",
      "recommendation": "actionable advice"
    }}
  ],
  "overall_risk": "low|medium|high",
  "summary": "brief assessment"
}}

Scoring policy:
- Repeated inbound transfers >= 100,000 from the same source within 60 days = HIGH unless strong benign explanation is present.

Be conservative — legitimate large purchases are common. Focus on truly suspicious patterns.
"""
    try:
        resp = model.generate_content(prompt, generation_config=_cfg_with_schema(FRAUD_SCHEMA))
        return _to_json_response(_resp_to_text(resp) or "")
    except Exception as e:
        log.exception("Gemini fraud_detect failed")
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    # Gunicorn in container handles prod; this is only for local debug.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)