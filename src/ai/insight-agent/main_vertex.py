import os, time, json, logging, hashlib, math
from collections import defaultdict, Counter
from flask import Flask, jsonify, request
from typing import Any, List

# Backend selection (default = vertex). ADK stays optional behind a flag.
BACKEND = os.getenv("INSIGHT_BACKEND", "vertex").lower()

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger("insight-agent")

FAST_MODE = os.getenv("INSIGHT_FAST_MODE", "true").lower() == "true"
CACHE_TTL_SEC = int(os.getenv("INSIGHT_CACHE_TTL_SEC", "180"))  # 3 min default
_cache = {}  # { key: (expires_epoch, value_json) }

def _cache_get(key: str):
  now = time.time()
  rec = _cache.get(key)
  if not rec:
    return None
  exp, val = rec
  if exp < now:
    _cache.pop(key, None)
    return None
  return val

def _cache_put(key: str, val):
  _cache[key] = (time.time() + CACHE_TTL_SEC, val)

def _txn_hash(transactions) -> str:
  # stable hash to reuse results on repeat clicks
  raw = json.dumps(transactions, sort_keys=True, ensure_ascii=False)
  return hashlib.md5(raw.encode("utf-8")).hexdigest()

def _compact_transactions(transactions, max_rows=300):
  """
  Reduce tokens while gaining signal:
    - limit rows
    - aggregate by counterparty/day
    - precompute totals and top categories
  """
  tx = transactions[:max_rows]
  total_in, total_out = 0.0, 0.0
  by_party = defaultdict(float)
  dates = []
  for t in tx:
    amt = float(t.get("amount", 0))
    # Use 'description' or 'label' if available, otherwise use account numbers
    desc = (t.get("label") or t.get("description") or \
            (t.get("toAccountNum") if amt > 0 else t.get("fromAccountNum")) or \
            "Unknown").strip()[:60]
    # Use 'timestamp' and extract date part
    timestamp = t.get("timestamp") or ""
    date = timestamp[:10] if timestamp else ""
    dates.append(date)
    if amt >= 0:
      total_in += amt
    else:
      total_out += -amt
    by_party[desc] += abs(amt)
  top = sorted(by_party.items(), key=lambda kv: kv[1], reverse=True)[:8]
  latest = max((d for d in dates if d), default="")
  days = os.environ.get("WINDOW_DAYS")  # may be unset here (UI sends only to MCP). okay if None
  summary = {
    "latest_txn": latest,
    "window_days_hint": days,
    "rows_used": len(tx),
    "total_in": round(total_in, 2),
    "total_out": round(total_out, 2),
    "top_parties": [{"name": n, "total": round(v, 2)} for n, v in top],
  }
  # compact table the model will see (party, amount, sign)
  mini = [{"date": (t.get("timestamp") or "")[:10], # Use timestamp here
           "party": (t.get("label") or t.get("description") or \
                     (t.get("toAccountNum") if t.get("amount", 0) > 0 else t.get("fromAccountNum")) or \
                     "Unknown")[:40], # Use account numbers as fallback
           "amount": float(t.get("amount", 0))}
          for t in tx]
  return {"features": summary, "sample": mini}

@app.get("/api/healthz")
def healthz():
    return "ok", 200

def _coerce_json(text_or_obj):
  """
  Best-effort: accept dict or JSON string; if the model returned extra prose,
  try to locate the first JSON object block.
  """
  if isinstance(text_or_obj, (dict, list)):
    return text_or_obj
  s = str(text_or_obj).strip()
  # Fast path
  try:
    return json.loads(s)
  except Exception:
    pass
  # Heuristic: find first {...} block
  start = s.find("{")
  end = s.rfind("}")
  if start >= 0 and end > start:
    try:
      return json.loads(s[start:end+1])
    except Exception:
      pass
  raise ValueError("Backend did not return valid JSON")

# NOTE: ADK is optional. We only import/build if backend=adk to avoid
# forcing google-adk into the default Vertex image.
_adk_agent = None
def _get_adk_agent():
  global _adk_agent
  if _adk_agent is not None:
    return _adk_agent
  try:
    # Lazy import when (and only when) ADK backend is used
    from adk.api import adk as _adk_api  # noqa
    from adk.builders import AgentBuilder as _AgentBuilder  # noqa
  except Exception as e:
    raise RuntimeError(
      "ADK backend requested but 'google-adk' is not installed in this image. "
      "Install google-adk and rebuild, or set INSIGHT_BACKEND=vertex."
    ) from e
  # Reuse the existing tool defined above
  _adk_agent = _AgentBuilder().with_tools(financial_analyzer).build()
  return _adk_agent

def _vertex_generate_budget(transactions, model_id):
  """
  Calls Vertex AI (GenerativeModel) to produce a budget plan JSON.
  """
  from vertexai import init
  from vertexai.generative_models import GenerativeModel, GenerationConfig
  project = os.environ.get("GOOGLE_CLOUD_PROJECT")
  location = os.environ.get("VERTEX_LOCATION", "us-central1")
  init(project=project, location=location)
  model = GenerativeModel(model_id)

  # Fast-mode: compact the payload + strict JSON response
  if FAST_MODE:
    compact = _compact_transactions(transactions)
    prompt = (
      "You are a precise budget coach. Using the compacted transaction features below, "
      "return *only* JSON with keys: summary (str), budget_buckets (array of objects with name (str), pct (number), monthly (number)), tips (array of str). "
      "IMPORTANT: Only return the JSON object, no other text. Guidelines:\n"
      "- budget_buckets.pct should sum ~100 (normalize if needed)\n"
      "- monthly values are USD estimates rounded to dollars\n"
      "- tips: 3–5 short, actionable items\n\n"
      f"FEATURES:\n{json.dumps(compact, ensure_ascii=False)}\n"
    )
    genconf = GenerationConfig(
      response_mime_type="application/json",
      temperature=0.4,
      top_p=0.9,
    )
    resp = model.generate_content(prompt, generation_config=genconf)
    obj = _coerce_json(getattr(resp, "text", None))
  else:
    prompt = (
      "You are a budget coach. Given the user's bank transactions, "
      "produce a concise JSON with keys: summary, budget_buckets[{name,pct,monthly}], tips[]. "
      "Rules: percentages total ≈100; respond as pure JSON only.\n"
      f"Transactions:\n{json.dumps(transactions)[:20000]}"
    )
    resp = model.generate_content(prompt)
    obj = _coerce_json(getattr(resp, "text", None))

  # guard-rails: normalize bucket pct to ~100 and clamp
  try:
    buckets = obj.get("budget_buckets", [])
    total = sum(max(0.0, float(b.get("pct", 0))) for b in buckets) or 1.0
    for b in buckets:
      # Map model's keys to expected keys
      if "bucket_name" in b:
        b["name"] = b.pop("bucket_name")
      if "monthly_amt" in b:
        b["monthly"] = b.pop("monthly_amt")

      pct = max(0.0, float(b.get("pct", 0)))
      b["pct"] = round(100.0 * pct / total, 1)
      if "monthly" in b:
        b["monthly"] = float(b["monthly"])
    obj["budget_buckets"] = buckets[:6]  # keep it tight
  except Exception:
    pass
  if "summary" not in obj:
    obj["summary"] = "Analysis could not be generated."
  return obj

def _adk_generate_budget(transactions):
  """
  ADK helper (kept for completeness if you want to call it elsewhere).
  """
  from adk.api import adk as _adk_api
  agent = _get_adk_agent()
  raw = _adk_api.run(agent, {"transactions": json.dumps(transactions)})
  obj = _coerce_json(raw)
  # Best-effort normalization
  return {
    "summary": obj.get("summary") or "Summary unavailable.",
    "budget_buckets": obj.get("top_categories") or [],
    "tips": obj.get("unusual_transactions") or []
  }

@app.post("/api/analyze")
def analyze():
  data = request.get_json(silent=True)
  if not data:
    return jsonify(error="No JSON body"), 400
  transactions = data if isinstance(data, list) else data.get("transactions")
  if not transactions:
    return jsonify(error="Expected JSON list or {'transactions': [...]} G"), 400

  backend = os.environ.get("INSIGHT_BACKEND", "vertex").strip().lower()
  try:
    if backend == "vertex":
      # Keep existing Vertex behavior (generate via Gemini on Vertex AI).
      model_id = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")
      obj = _vertex_generate_budget(transactions, model_id)  # reuse common JSON logic
      return jsonify(obj), 200
    elif backend == "adk":
      # Lazy usage of ADK
      from adk.api import adk as _adk_api
      agent = _get_adk_agent()
      raw = _adk_api.run(agent, {"transactions": json.dumps(transactions)})
      return (_coerce_json(raw), 200, {"Content-Type": "application/json"})
    else:
      raise RuntimeError(f"Unknown INSIGHT_BACKEND: {backend}")
  except Exception as e:
    log.exception("Analyze failed")
    return jsonify(error=str(e)), 502

@app.post("/api/budget/coach")
def budget_coach():
  """
  Body: either a raw JSON list of transactions or {"transactions":[...]}.
  Env:
    INSIGHT_BACKEND = vertex | adk  (default: vertex)
    VERTEX_MODEL    = gemini-2.5-pro (default)
  """
  data = request.get_json(silent=True)
  if data is None:
    return jsonify(error="No JSON body"), 400
  transactions = data if isinstance(data, list) else data.get("transactions")
  if not transactions:
    return jsonify(error="Expected JSON list or {'transactions': [...]} G"), 400

  backend = os.environ.get("INSIGHT_BACKEND", "vertex").strip().lower()
  try:
    # cache key: backend + model + txn hash
    h = _txn_hash(transactions)
    model_id = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")
    cache_key = f"{os.environ.get('INSIGHT_BACKEND','vertex')}::{model_id}::{h}::fast={int(FAST_MODE)}"
    cached = _cache_get(cache_key)
    if cached:
      return jsonify(cached), 200
    if backend == "vertex":
      result = _vertex_generate_budget(transactions, model_id)
    elif backend == "adk":
      result = _adk_generate_budget(transactions)
    else:
      raise RuntimeError(f"Unknown INSIGHT_BACKEND: {backend}")
    _cache_put(cache_key, result)
    return jsonify(result), 200
  except Exception as e:
    log.exception("Budget coach failed")
    return jsonify(error=str(e)), 502

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)