
import os
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yaml
from google import genai
from google.genai import types
from google.api_core import exceptions

# ------------------------------------------------------------------------------
# App / Logging
# ------------------------------------------------------------------------------
app = FastAPI(title="Insight Agent", version="2.0.0")
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
MODEL_ID = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")

MAX_TXNS = int(os.environ.get("MAX_TRANSACTIONS_PER_PROMPT", "50"))

# Google GenAI Init
client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
log.info("Google GenAI client initialized for Vertex AI: project=%s location=%s", PROJECT, LOCATION)

log.info("Using Google GenAI Model: %s", MODEL_ID)

# ------------------------------------------------------------------------------
# Externalized prompts (prompts.yaml)
# ------------------------------------------------------------------------------
PROMPTS: Dict[str, str] = {}

@app.on_event("startup")
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
# Pydantic Models
# ------------------------------------------------------------------------------
class Transaction(BaseModel):
    date: str
    label: str
    amount: float

class TransactionRequest(BaseModel):
    transactions: List[Transaction]

# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
@app.get("/api/healthz")
def healthz():
    return {"status": "ok"}

# ------------------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------------------
@app.post("/api/budget/coach")
async def budget_coach(request: TransactionRequest):
    if request.transactions is None:
        raise HTTPException(status_code=400, detail="Could not find 'transactions' in the payload")

    prompt = PROMPTS["budget_coach"].format(
        transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2)
    )
    
    try:
        generation_config = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=COACH_SCHEMA,
        )
        resp = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt, 
            config=generation_config
        )
        return json.loads(resp.text)
    except exceptions.ResourceExhausted as e:
        log.error(f"Gemini budget_coach rate limited: {e}")
        raise HTTPException(status_code=429, detail="Service temporarily overloaded. Please try again in a moment.")
    except Exception as e:
        log.exception("Gemini budget_coach call failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/spending/analyze")
async def spending_analyze(request: TransactionRequest):
    if request.transactions is None:
        raise HTTPException(status_code=400, detail="Could not find 'transactions' in the payload")

    prompt = PROMPTS["spending_analyze"].format(
        transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2)
    )

    try:
        generation_config = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=SPENDING_SCHEMA,
        )
        resp = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt, 
            config=generation_config
        )
        return json.loads(resp.text)
    except exceptions.ResourceExhausted as e:
        log.error(f"Gemini spending_analyze rate limited: {e}")
        raise HTTPException(status_code=429, detail="Service temporarily overloaded. Please try again in a moment.")
    except Exception as e:
        log.exception("Gemini spending_analyze call failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fraud/detect")
async def fraud_detect(request: TransactionRequest):
    if request.transactions is None:
        raise HTTPException(status_code=400, detail="Could not find 'transactions' in the payload")

    prompt = PROMPTS["fraud_detect"].format(
        transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2)
    )

    try:
        generation_config = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=FRAUD_SCHEMA,
        )
        resp = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt, 
            config=generation_config
        )
        return json.loads(resp.text)
    except exceptions.ResourceExhausted as e:
        log.error(f"Gemini fraud_detect rate limited: {e}")
        raise HTTPException(status_code=429, detail="Service temporarily overloaded. Please try again in a moment.")
    except Exception as e:
        log.exception("Gemini fraud_detect call failed")
        raise HTTPException(status_code=500, detail=str(e))
