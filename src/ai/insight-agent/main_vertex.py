import os
import json
import logging
import asyncio, time, random
from typing import Any, Dict, List, Optional, Tuple
import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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

PROMPTS_FILE = os.getenv("PROMPTS_FILE", os.path.join(os.path.dirname(__file__), "prompts.yaml"))

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

# ------------------------------------------------------------------------------
# Prompt Store with Live-Reload
# ------------------------------------------------------------------------------
DEFAULT_PROMPTS: Dict[str, str] = {
    "coach": "... {transactions}\n",
    "spending_analyze": "... {transactions}\n",
    "fraud_detect": "... {transactions}\n{account_context}\n",
}

class PromptStore:
    def __init__(self, file_path: str, defaults: Dict[str, str]):
        self.path = Path(file_path)
        self.defaults = defaults
        self._mtime: Optional[float] = None
        self._prompts = defaults
        self._map_sha8 = {k: self._sha8(v) for k, v in defaults.items()}
        self._maybe_reload(initial=True)

    def _sha8(self, s: str) -> str:
        return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:8]

    def _maybe_reload(self, initial=False):
        try:
            if self.path.exists():
                m = self.path.stat().st_mtime
                if self._mtime is None or m > self._mtime:
                    import yaml
                    data = yaml.safe_load(self.path.read_text()) or {}
                    assert isinstance(data, dict)
                    merged = {**self.defaults, **{k: str(v) for k, v in data.items()}}
                    self._prompts = merged
                    self._map_sha8 = {k: self._sha8(v) for k, v in merged.items()}
                    self._mtime = m
        except Exception:
            if initial:
                self._prompts = self.defaults
                self._map_sha8 = {k: self._sha8(v) for k, v in self.defaults.items()}

    def render(self, key: str, **vars) -> Tuple[str, str]:
        self._maybe_reload()
        tmpl = self._prompts.get(key, self.defaults.get(key, ""))
        tag = f"{key}@{self._map_sha8.get(key, '00000000')}"
        try:
            text = tmpl.format(**vars)
        except Exception:
            text = tmpl
        return text, tag

prompts = PromptStore(PROMPTS_FILE, DEFAULT_PROMPTS)

def _to_json_response(text: str, tag: str) -> JSONResponse:
    def _balanced(s: str) -> Optional[str]:
        start = s.find("{")
        end = s.rfind("}")
        return s[start:end+1] if start != -1 and end != -1 and end > start else None

    cleaned = text.strip().strip("`")
    obj = None
    try:
        obj = json.loads(cleaned)
    except Exception:
        candidate = _balanced(cleaned)
        if candidate:
            try: obj = json.loads(candidate)
            except Exception: pass
    if obj is None:
        obj = {
            "summary": "Model returned non-JSON output; using empty analysis.",
            "findings": [], "overall_risk": "low",
            "top_categories": [], "unusual_transactions": [],
            "buckets": [], "tips": []
        }
    return JSONResponse(content=obj, headers={"X-Insight-Prompt": tag})

# --- JSON Mode Schemas (Fraud / Spending / Coach) ---
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
        "tips": {"type": "array", "items": {"type": "string"}},
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
        "unusual_transactions": {"type": "array", "items": {"type": "object"}},
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

    prompt_text, tag = prompts.render("coach", transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2))
    
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
                        model=MODEL_ID, contents=prompt_text, config=generation_config
                    ),
                )
            resp = await _call_with_retry(_do)
            return _to_json_response(resp.text, tag)
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

    prompt_text, tag = prompts.render("spending_analyze", transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2))

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
                        model=MODEL_ID, contents=prompt_text, config=generation_config
                    ),
                )
            resp = await _call_with_retry(_do)
            return _to_json_response(resp.text, tag)
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

    prompt_text, tag = prompts.render("fraud_detect", transactions=json.dumps([t.dict() for t in request.transactions[:MAX_TXNS]], indent=2))

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
                        model=MODEL_ID, contents=prompt_text, config=generation_config
                    ),
                )
            resp = await _call_with_retry(_do)
            return _to_json_response(resp.text, tag)
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