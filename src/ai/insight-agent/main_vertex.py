import os
import json
import logging
import asyncio, time, random
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yaml
from google import genai
from google.genai import types, errors as genai_errors
from google.genai.types import ThinkingConfig

# ------------------------------------------------------------------------------
# App / Logging
# ------------------------------------------------------------------------------
app = FastAPI(title="Insight Agent", version="2.0.1")
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
MODEL_ID = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")
MAX_TXNS = int(os.environ.get("MAX_TRANSACTIONS_PER_PROMPT", "50"))
GENAI_MAX_TOKENS = int(os.getenv("GENAI_MAX_TOKENS", "2048"))
GENAI_THINK_TOKENS = int(os.getenv("GENAI_THINK_TOKENS", "1024"))
CONCURRENCY = int(os.getenv("GENAI_CONCURRENCY", "2"))
RPM_LIMIT = int(os.getenv("GENAI_RPM", "18"))
_sem = asyncio.Semaphore(CONCURRENCY)
_req_ts: list[float] = []
_RPM_WINDOW = 60.0

# Google GenAI Init
client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
log.info("Google GenAI client initialized for Vertex AI: project=%s location=%s", PROJECT, LOCATION)
log.info("Using Google GenAI Model: %s", MODEL_ID)

# --- Per-pod throttling (smooths DSQ traffic) ---
async def _throttle_rpm():
    now = time.monotonic()
    while _req_ts and (now - _req_ts[0]) > _RPM_WINDOW:
        _req_ts.pop(0)
    while len(_req_ts) >= RPM_LIMIT:
        await asyncio.sleep(0.2)
        now = time.monotonic()
        while _req_ts and (now - _req_ts[0]) > _RPM_WINDOW:
            _req_ts.pop(0)
    _req_ts.append(now)

# --- Outer retry: truncated exponential backoff + jitter; honors Retry-After ---
async def _call_with_retry(make_call, *, max_retries:int=8, base:float=0.6, cap:float=12.0):
    for attempt in range(max_retries):
        try:
            return await asyncio.wait_for(make_call(), timeout=60)
        except genai_errors.APIError as e:
            if getattr(e, "code", None) == 429:
                retry_after = 0.0
                try:
                    retry_after = float(getattr(e.response, "headers", {}).get("retry-after", 0))
                except Exception:
                    pass
                sleep_s = max(retry_after, min(cap, (base * (2 ** attempt)) + random.uniform(0, 0.3)))
                log.warning("429 RESOURCE_EXHAUSTED; backing off %.2fs (attempt %d/%d)", sleep_s, attempt+1, max_retries)
                await asyncio.sleep(sleep_s)
                continue
            raise
    raise HTTPException(status_code=503, detail="Vertex AI capacity is temporarily saturated. Please retry shortly.")

def _clamped_thinking_budget(model_id: str, budget: int) -> int:
    mid = (model_id or "").lower()
    if "flash" in mid:
        return max(0, budget)
    if "pro" in mid:
        return max(128, budget)
    return max(0, budget)

def _get_prompt(name: str) -> str:
    try:
        return PROMPTS[name]
    except KeyError:
        log.error("Prompt '%s' not found in prompts.yaml", name)
        raise HTTPException(status_code=500, detail=f"Prompt '{name}' not configured")

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

    prompt = _get_prompt("budget_coach").format(
        transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2)
    )
    
    try:
        generation_config = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=GENAI_MAX_TOKENS,
            response_mime_type="application/json",
            response_schema=COACH_SCHEMA,
            thinking_config=ThinkingConfig(thinking_budget=_clamped_thinking_budget(MODEL_ID, GENAI_THINK_TOKENS)),
        )
        async with _sem:
            await _throttle_rpm()
            loop = asyncio.get_running_loop()
            async def _do():
                return await loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=MODEL_ID, contents=prompt, config=generation_config
                    ),
                )
            resp = await _call_with_retry(_do)
            return json.loads(resp.text)
    except genai_errors.APIError as e:
        log.exception("Gemini budget_coach API error: %s", e)
        if getattr(e, "code", None) == 429:
            raise HTTPException(status_code=429, detail="Temporarily rate limited. Please retry.")
        raise HTTPException(status_code=502, detail="Upstream model error.")
    except Exception as e:
        log.exception("Gemini budget_coach call failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/spending/analyze")
async def spending_analyze(request: TransactionRequest):
    if request.transactions is None:
        raise HTTPException(status_code=400, detail="Could not find 'transactions' in the payload")

    prompt = _get_prompt("spending_analyze").format(
        transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2)
    )

    try:
        generation_config = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=GENAI_MAX_TOKENS,
            response_mime_type="application/json",
            response_schema=SPENDING_SCHEMA,
            thinking_config=ThinkingConfig(thinking_budget=_clamped_thinking_budget(MODEL_ID, GENAI_THINK_TOKENS)),
        )
        async with _sem:
            await _throttle_rpm()
            loop = asyncio.get_running_loop()
            async def _do():
                return await loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=MODEL_ID, contents=prompt, config=generation_config
                    ),
                )
            resp = await _call_with_retry(_do)
            return json.loads(resp.text)
    except genai_errors.APIError as e:
        log.exception("Gemini spending_analyze API error: %s", e)
        if getattr(e, "code", None) == 429:
            raise HTTPException(status_code=429, detail="Temporarily rate limited. Please retry.")
        raise HTTPException(status_code=502, detail="Upstream model error.")
    except Exception as e:
        log.exception("Gemini spending_analyze call failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fraud/detect")
async def fraud_detect(request: TransactionRequest):
    if request.transactions is None:
        raise HTTPException(status_code=400, detail="Could not find 'transactions' in the payload")

    prompt = _get_prompt("fraud_detect").format(
        transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2)
    )

    try:
        generation_config = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=GENAI_MAX_TOKENS,
            response_mime_type="application/json",
            response_schema=FRAUD_SCHEMA,
            thinking_config=ThinkingConfig(thinking_budget=_clamped_thinking_budget(MODEL_ID, GENAI_THINK_TOKENS)),
        )
        async with _sem:
            await _throttle_rpm()
            loop = asyncio.get_running_loop()
            async def _do():
                return await loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=MODEL_ID, contents=prompt, config=generation_config
                    ),
                )
            resp = await _call_with_retry(_do)
            return json.loads(resp.text)
    except genai_errors.APIError as e:
        log.exception("Gemini fraud_detect API error: %s", e)
        if getattr(e, "code", None) == 429:
            raise HTTPException(status_code=429, detail="Temporarily rate limited. Please retry.")
        raise HTTPException(status_code=502, detail="Upstream model error.")
    except Exception as e:
        log.exception("Gemini fraud_detect call failed")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1)