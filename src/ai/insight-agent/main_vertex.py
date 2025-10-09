
import os
import json
import logging
import warnings
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request
import yaml
from google.api_core import retry

# ---------------------------
# Vertex AI (server-side) SDK
# ---------------------------
from vertexai import init as vertex_init
from vertexai.generative_models import GenerationConfig, GenerativeModel

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
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

# Model Selection
SUPPORTED_MODELS = {
    "fast": "gemini-2.5-pro",
    "quality": "gemini-2.5-pro",
}
# Prioritize the specific VERTEX_MODEL env var if it's set
MODEL_ID_FROM_ENV = os.environ.get("VERTEX_MODEL")
if MODEL_ID_FROM_ENV:
    MODEL_ID = MODEL_ID_FROM_ENV
    SELECTED_MODEL_KEY = "custom"
else:
    SELECTED_MODEL_KEY = os.environ.get("MODEL_PROFILE", "quality")
    MODEL_ID = SUPPORTED_MODELS.get(SELECTED_MODEL_KEY, SUPPORTED_MODELS["quality"])


MAX_TXNS = int(os.environ.get("MAX_TRANSACTIONS_PER_PROMPT", "50"))

# Vertex init
try:
    vertex_init(project=PROJECT or None, location=LOCATION)
    log.info("Vertex AI initialized: project=%s location=%s", PROJECT, LOCATION)
except Exception as e:
    log.exception("Vertex AI init failed: %s", e)

model = GenerativeModel(MODEL_ID)
log.info("Using Vertex AI Model: %s (Profile: %s)", MODEL_ID, SELECTED_MODEL_KEY)

# ------------------------------------------------------------------------------
# Externalized prompts (prompts.yaml)
# ------------------------------------------------------------------------------
PROMPTS: Dict[str, str] = {}

def load_prompts():
    """Load prompts.yaml."""
    global PROMPTS
    path = os.path.join(os.path.dirname(__file__), "prompts.yaml")
    try:
        with open(path, "r") as f:
            PROMPTS = yaml.safe_load(f)
            log.info("Loaded %d prompts from %s", len(PROMPTS), path)
    except Exception as e:
        log.critical("Could not load prompts.yaml: %s", e)
        raise

load_prompts()

# ------------------------------------------------------------------------------
# Schemas for JSON Mode (plain dicts)
# ------------------------------------------------------------------------------
COACH_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "budget_buckets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "total": {"type": "number"},
                    "count": {"type": "integer"},
                },
                "required": ["name", "total", "count"],
            },
        },
        "tips": {
            "type": "array",
            "items": {"type": "string"}
        },
    },
    "required": ["summary", "budget_buckets", "tips"],
}

SPENDING_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "top_categories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "total": {"type": "number"},
                    "count": {"type": "integer"},
                },
                "required": ["name", "total", "count"],
            },
        },
        "unusual_transactions": {
            "type": "array",
            "items": {"type": "object"},
        },
    },
    "required": ["summary", "top_categories", "unusual_transactions"],
}

FRAUD_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "transaction": {"type": "object"},
                    "risk_score": {"type": "number"},
                    "reason": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["transaction", "risk_score", "reason", "recommendation"],
            },
        },
        "overall_risk": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["findings", "overall_risk", "summary"],
}

# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
@app.get("/api/healthz")
def healthz():
    return "ok", 200

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def _extract_transactions(payload: Any) -> Optional[List[Dict[str, Any]]]:
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("transactions")
    return None

@retry.Retry()
def _generate_content_with_retry(prompt, generation_config):
    return model.generate_content(prompt, generation_config=generation_config)

# ------------------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------------------
@app.post("/api/budget/coach")
def budget_coach():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if txns is None:
        return jsonify(error="Could not find 'transactions' in the payload"), 400

    prompt = PROMPTS["budget_coach"].format(
        transactions=json.dumps(txns[:MAX_TXNS], indent=2)
    )
    
    try:
        generation_config = GenerationConfig(
            temperature=0.0,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=COACH_SCHEMA,
        )
        resp = _generate_content_with_retry(prompt, generation_config)
        return resp.text, 200, {"Content-Type": "application/json"}
    except Exception as e:
        log.exception("Gemini budget_coach call failed")
        return jsonify(error=str(e)), 500

@app.post("/api/spending/analyze")
def spending_analyze():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if txns is None:
        return jsonify(error="Could not find 'transactions' in the payload"), 400

    prompt = PROMPTS["spending_analyze"].format(
        transactions=json.dumps(txns[:MAX_TXNS], indent=2)
    )

    try:
        generation_config = GenerationConfig(
            temperature=0.0,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=SPENDING_SCHEMA,
        )
        resp = _generate_content_with_retry(prompt, generation_config)
        return resp.text, 200, {"Content-Type": "application/json"}
    except Exception as e:
        log.exception("Gemini spending_analyze call failed")
        return jsonify(error=str(e)), 500

@app.post("/api/fraud/detect")
def fraud_detect():
    data = request.get_json(silent=True)
    txns = _extract_transactions(data)
    if txns is None:
        return jsonify(error="Could not find 'transactions' in the payload"), 400

    prompt = PROMPTS["fraud_detect"].format(
        transactions=json.dumps(txns[:MAX_TXNS], indent=2)
    )

    try:
        generation_config = GenerationConfig(
            temperature=0.0,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=FRAUD_SCHEMA,
        )
        resp = _generate_content_with_retry(prompt, generation_config)
        return resp.text, 200, {"Content-Type": "application/json"}
    except Exception as e:
        log.exception("Gemini fraud_detect call failed")
        return jsonify(error=str(e)), 500

# ------------------------------------------------------------------------------
# Local debug
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
