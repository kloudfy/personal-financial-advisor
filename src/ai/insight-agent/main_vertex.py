import os
import json
import logging
import warnings
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple
import hashlib
from pathlib import Path
import time

from flask import Flask, jsonify, request

# ---------------------------
# Vertex AI (server-side) SDK
# ---------------------------
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

# Optional YAML (externalized prompts). If not available, we'll use in-code defaults.
try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # we will gracefully fall back

# Silence Vertex SDK deprecation chatter in logs
warnings.filterwarnings(
    "ignore",
    message="This feature is deprecated.*",
    category=UserWarning,
    module="vertexai.*",
)

# ------------------------------------------------------------------------------
# App / Logging
# ------------------------------------------------------------------------------
app = Flask(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("insight-agent")

# ------------------------------------------------------------------------------
# Environment / Config
# ------------------------------------------------------------------------------
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")  # may be empty if default creds
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
MODEL_ID = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")

# Gemini generation config (deterministic + headroom)
GEN_CONFIG: Dict[str, Any] = {
    "temperature": float(os.environ.get("INSIGHT_TEMP", "0.0")),
    "top_p": float(os.environ.get("INSIGHT_TOP_P", "0.1")),
    "top_k": int(os.environ.get("INSIGHT_TOP_K", "40")),
    "max_output_tokens": int(os.environ.get("INSIGHT_MAX_TOKENS", "2048")),
    "response_mime_type": "application/json",
}

# Feature flags
def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "y")

BL_ONLY = _env_bool("INSIGHT_BL_ONLY", False)
BL_FALLBACK = _env_bool("INSIGHT_BL_FALLBACK", True)
ENV_FAST_MODE = _env_bool("INSIGHT_FAST_MODE", False)

MAX_TXNS = int(os.environ.get("INSIGHT_MAX_TXNS", "30"))  # configurable cap

# Vertex init
try:
    vertex_init(project=PROJECT or None, location=LOCATION)
    log.info("Vertex AI initialized: project=%s location=%s", PROJECT, LOCATION)
except Exception as e:
    log.exception("Vertex AI init failed: %s", e)

model = GenerativeModel(MODEL_ID)
log.info(
    "Using Vertex model=%s | GEN_CONFIG={temp=%.2f top_p=%.2f top_k=%d max_tokens=%d mime=%s} | BL_ONLY=%s BL_FALLBACK=%s FAST_MODE=%s MAX_TXNS=%d",
    MODEL_ID,
    GEN_CONFIG["temperature"],
    GEN_CONFIG["top_p"],
    GEN_CONFIG["top_k"],
    GEN_CONFIG["max_output_tokens"],
    GEN_CONFIG["response_mime_type"],
    BL_ONLY,
    BL_FALLBACK,
    ENV_FAST_MODE,
    MAX_TXNS,
)

def _cfg_with_schema(schema_obj_or_none):
    cfg = GEN_CONFIG.copy()
    if HAS_SCHEMA and schema_obj_or_none:
        cfg["response_schema"] = schema_obj_or_none
    return cfg

# ------------------------------------------------------------------------------
# Externalized prompts (prompts.yaml) with safe fallback
# ------------------------------------------------------------------------------
PROMPTS: Dict[str, str] = {}

class _SafeDict(dict):
    def __missing__(self, key):
        return ""

def load_prompts():
    """Load prompts.yaml if present and PyYAML available; otherwise use defaults."""
    global PROMPTS
    default_prompts = {
        "coach": (
            "You are a helpful coach.\n"
            "Return JSON with keys summary, buckets, tips.\n"
            "Transactions:\n{transactions}\n"
        ),
        "spending_analyze": (
            "You analyze spending.\n"
            "Return JSON with summary, top_categories, unusual_transactions.\n"
            "Transactions:\n{transactions}\n"
        ),
        "fraud_detect": (
            "You detect fraud.\nTransactions:\n{transactions}\n{account_context}\n"
            "Return JSON with findings, overall_risk, summary.\n"
        ),
    }

    path = os.path.join(os.path.dirname(__file__), "prompts.yaml")
    if yaml is None:
        PROMPTS = default_prompts
        log.warning("PyYAML not installed; using in-code default prompts.")
        return

    try:
        with open(path, "r") as f:
            loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                raise ValueError("prompts.yaml did not deserialize to a dict")
            PROMPTS = {**default_prompts, **{k: str(v) for k, v in loaded.items()}}
            log.info("Loaded %d prompts from %s", len(PROMPTS), path)
    except Exception as e:
        PROMPTS = default_prompts
        log.warning("Could not read %s (%s); using in-code default prompts.", path, e)

def render_prompt(name: str, **kwargs) -> str:
    tmpl = PROMPTS.get(name, "")
    return tmpl.format_map(_SafeDict(kwargs))

load_prompts()

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
# Helpers: txns, parsing, JSON
# ------------------------------------------------------------------------------
def _extract_transactions(payload: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Accepts either:
      - {"transactions": [...]}  OR
      - [...]
    Returns the list (or None).
    """
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload  # type: ignore[return-value]
    if isinstance(payload, dict):
        tx = payload.get("transactions")
        if isinstance(tx, list):
            return tx  # type: ignore[return-value]
    return None

def _resp_to_text(resp) -> str:
    """Collect text from candidates/parts (newer SDKs), else use .text."""
    if not resp:
        return ""
    try:
        out: List[str] = []
        for c in getattr(resp, "candidates", []) or []:
            content = getattr(c, "content", None)
            parts = getattr(content, "parts", None) if content else None
            if parts:
                for p in parts:
                    t = getattr(p, "text", None)
                    if t:
                        out.append(t)
        if out:
            return "\n".join(out)
    except Exception:
        pass
    return getattr(resp, "text", "") or ""

def _clean_fences(text: str) -> str:
    cleaned = (text or "").strip()
    for fence in ("```json", "```JSON", "```"):
        cleaned = cleaned.replace(fence, "")
    return cleaned.strip()

def _extract_balanced_json(s: str) -> Optional[str]:
    """Return the first balanced top-level JSON object/array as a string, or None."""
    if not s:
        return None
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
    in_str = False
    esc = False
    for j in range(start, len(s)):
        c = s[j]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"' and not esc:
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return s[start : j + 1]
    return None

def _parse_json_to_obj(text: str) -> Optional[Any]:
    """Attempt to parse to a Python object (clean fences, then load, then balanced JSON)."""
    cleaned = _clean_fences(text)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    candidate = _extract_balanced_json(cleaned)
    if candidate:
        try:
            return json.loads(candidate)
        except Exception:
            return None
    return None

def _json_response(obj: Any) -> Tuple[str, int, Dict[str, str]]:
    return json.dumps(obj), 200, {"Content-Type": "application/json"}

def _generic_fallback() -> Dict[str, Any]:
    return {
        "summary": "Model returned non-JSON output. Treating as low risk / empty analysis.",
        "findings": [],
        "overall_risk": "low",
        "top_categories": [],
        "unusual_transactions": [],
        "buckets": [],
        "tips": [],
    }

# ------------------------------------------------------------------------------
# Simple BL fallbacks
# ------------------------------------------------------------------------------
def bl_spending(txns: List[Dict[str, Any]]) -> Dict[str, Any]:
    income = sum(float(t.get("amount", 0) or 0) for t in txns if float(t.get("amount", 0) or 0) > 0)
    spend = sum(-float(t.get("amount", 0) or 0) for t in txns if float(t.get("amount", 0) or 0) < 0)
    amounts = [abs(float(t.get("amount", 0) or 0)) for t in txns]
    mu = mean(amounts) if amounts else 0.0
    sigma = pstdev(amounts) if len(amounts) > 1 else 0.0
    thresh = mu + 3 * (sigma if sigma > 1e-9 else 1e-9)
    unusual = [t for t in txns if abs(float(t.get("amount", 0) or 0)) > thresh]

    top_categories = []
    if spend > 0:
        top_categories.append({"name": "Expenses", "total": round(spend, 2), "count": sum(1 for t in txns if float(t.get("amount", 0) or 0) < 0)})
    if income > 0:
        top_categories.append({"name": "Income", "total": round(income, 2), "count": sum(1 for t in txns if float(t.get("amount", 0) or 0) > 0)})

    return {
        "summary": f"Income {income:.2f}, spending {spend:.2f}.",
        "top_categories": top_categories[:5],
        "unusual_transactions": unusual,
    }

def bl_coach(txns: List[Dict[str, Any]]) -> Dict[str, Any]:
    s = bl_spending(txns)
    income_total = next((c["total"] for c in s["top_categories"] if c["name"] == "Income"), 0.0)
    spend_total = next((c["total"] for c in s["top_categories"] if c["name"] == "Expenses"), 0.0)
    net = income_total - spend_total
    buckets = [
        {"name": "Income", "total": round(income_total, 2), "count": sum(1 for t in txns if float(t.get("amount", 0) or 0) > 0)},
        {"name": "Spending", "total": round(spend_total, 2), "count": sum(1 for t in txns if float(t.get("amount", 0) or 0) < 0)},
    ]
    tips = []
    if spend_total > 0:
        tips.append("Set a weekly spending cap and monitor exceptions.")
    if income_total > 0 and net > 0:
        tips.append("Auto-transfer a % of income to savings after each paycheck.")
    tips.append("Review any large or unusual transactions for accuracy.")
    return {
        "summary": f"Income {income_total:.2f}, spending {spend_total:.2f}. Net {net:.2f}.",
        "buckets": buckets,
        "tips": tips[:3],
    }

def bl_fraud(txns: List[Dict[str, Any]]) -> Dict[str, Any]:
    findings = []
    # Simple policies: very large inbound, round dollar large amounts
    for t in txns:
        amt = float(t.get("amount", 0) or 0)
        indicators = []
        risk = 0.0
        if amt >= 100000:
            indicators.append("unusual_amount")
            risk = max(risk, 0.9)
        if abs(amt) >= 10000 and abs(amt) % 100 == 0:
            indicators.append("round_dollar_amount")
            risk = max(risk, 0.6)
        if indicators:
            findings.append({
                "transaction": {"date": t.get("date"), "label": t.get("label"), "amount": amt},
                "risk_score": round(risk, 2),
                "indicators": indicators,
                "reason": "Heuristic screening flagged patterns requiring review.",
                "recommendation": "Place a hold and verify the source with the customer if warranted.",
            })

    overall = "low"
    if any(f["risk_score"] >= 0.85 for f in findings):
        overall = "high"
    elif any(f["risk_score"] >= 0.6 for f in findings):
        overall = "medium"

    return {
        "findings": findings,
        "overall_risk": overall,
        "summary": "Heuristic baseline screening results.",
    }

# ------------------------------------------------------------------------------
# Endpoint helpers
# ------------------------------------------------------------------------------
def _maybe_fast_filter(txns: List[Dict[str, Any]], fast_flag: bool) -> List[Dict[str, Any]]:
    """If fast_flag, keep only strong outliers to shorten prompts; otherwise passthrough."""
    if not fast_flag or not txns:
        return txns
    amounts = [abs(float(t.get("amount", 0) or 0)) for t in txns]
    if not amounts:
        return txns
    mu = mean(amounts)
    sigma = pstdev(amounts) if len(amounts) > 1 else 0.0
    threshold = mu + 3 * (sigma if sigma > 1e-9 else 1e-9)
    anomalies = [t for t in txns if abs(float(t.get("amount", 0) or 0)) > threshold]
    return anomalies or txns  # fall back to all if none flagged

def _handle_llm_json_or_fallback(raw_text: str, bl_obj: Dict[str, Any]) -> Tuple[str, int, Dict[str, str]]:
    """Parse model text; if fail and BL_FALLBACK, return BL; else generic fallback."""
    obj = _parse_json_to_obj(raw_text)
    if obj is not None:
        return _json_response(obj)
    if BL_FALLBACK:
        return _json_response(bl_obj)
    # last resort generic
    return _json_response(_generic_fallback())

# ------------------------------------------------------------------------------
# Budget Coach (for UI)
# ------------------------------------------------------------------------------
@app.post("/api/budget/coach")
def budget_coach():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

    if BL_ONLY:
        return _json_response(bl_coach(txns))

    prompt = render_prompt(
        "coach",
        transactions=json.dumps(txns[:MAX_TXNS], indent=2),
    )

    try:
        resp = model.generate_content(prompt, generation_config=_cfg_with_schema(COACH_SCHEMA))
        text = _resp_to_text(resp) or ""
        log.debug("coach: model text head=%r", text[:200])
        return _handle_llm_json_or_fallback(text, bl_coach(txns))
    except Exception as e:
        log.exception("Gemini coach failed")
        if BL_FALLBACK or BL_ONLY:
            return _json_response(bl_coach(txns))
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# Spending Analysis
# ------------------------------------------------------------------------------
@app.post("/api/spending/analyze")
def spending_analyze():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

    if BL_ONLY:
        return _json_response(bl_spending(txns))

    prompt = render_prompt(
        "spending_analyze",
        transactions=json.dumps(txns[:MAX_TXNS], indent=2),
    )

    try:
        resp = model.generate_content(prompt, generation_config=_cfg_with_schema(SPENDING_SCHEMA))
        text = _resp_to_text(resp) or ""
        log.debug("spending: model text head=%r", text[:200])
        return _handle_llm_json_or_fallback(text, bl_spending(txns))
    except Exception as e:
        log.exception("Gemini spending_analyze failed")
        if BL_FALLBACK or BL_ONLY:
            return _json_response(bl_spending(txns))
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# Fraud Detection
# ------------------------------------------------------------------------------
@app.post("/api/fraud/detect")
def fraud_detect():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

    # Determine fast mode: query param overrides env default
    fast_qs = request.args.get("fast")
    fast_mode = ENV_FAST_MODE if fast_qs is None else (fast_qs.lower() in ("1", "true", "yes", "y"))

    txns_for_prompt = _maybe_fast_filter(txns, fast_mode)
    account_context = {}
    if isinstance(data, dict):
        account_context = data.get("account_context") or {}

    if BL_ONLY:
        return _json_response(bl_fraud(txns_for_prompt))

    account_ctx_text = (
        f"Account context:\n{json.dumps(account_context, indent=2)}"
        if account_context else
        ""
    )

    prompt = render_prompt(
        "fraud_detect",
        transactions=json.dumps(txns_for_prompt[:MAX_TXNS], indent=2),
        account_context=account_ctx_text,
    )

    try:
        resp = model.generate_content(prompt, generation_config=_cfg_with_schema(FRAUD_SCHEMA))
        text = _resp_to_text(resp) or ""
        log.debug("fraud: model text head=%r", text[:200])
        return _handle_llm_json_or_fallback(text, bl_fraud(txns_for_prompt))
    except Exception as e:
        log.exception("Gemini fraud_detect failed")
        if BL_FALLBACK or BL_ONLY:
            return _json_response(bl_fraud(txns_for_prompt))
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# Local debug
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # Gunicorn in container handles prod; this is only for local debug.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)