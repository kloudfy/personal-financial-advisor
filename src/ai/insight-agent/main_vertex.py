import os
import json
import logging
from flask import Flask, jsonify, request
from typing import Any, List

# Backend selection (default = vertex). ADK stays optional behind a flag.
BACKEND = os.getenv("INSIGHT_BACKEND", "vertex").lower()

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
log = logging.getLogger("insight-agent")

@app.get("/healthz")
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
  from vertexai.generative_models import GenerativeModel
  project = os.environ.get("GOOGLE_CLOUD_PROJECT")
  location = os.environ.get("VERTEX_LOCATION", "us-central1")
  init(project=project, location=location)
  model = GenerativeModel(model_id)
  prompt = (
    "You are a budget coach. Given the user's bank transactions, "
    "produce a concise JSON with keys:\n"
    " - summary: string (one short paragraph)\n"
    " - budget_buckets: array of objects [{name, pct, monthly}]\n"
    " - tips: array of short strings (actionable)\n"
    "Rules: percentages total ≈100; monthly is estimated USD; respond as pure JSON only.\n"
    f"Transactions:\n{json.dumps(transactions)[:20000]}"
  )
  resp = model.generate_content(prompt)
  return _coerce_json(getattr(resp, "text", None) or getattr(resp, "candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text"))

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

@app.post("/analyze")
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

@app.post("/budget/coach")
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
    if backend == "vertex":
      model_id = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")
      result = _vertex_generate_budget(transactions, model_id)
    elif backend == "adk":
      result = _adk_generate_budget(transactions)
    else:
      raise RuntimeError(f"Unknown INSIGHT_BACKEND: {backend}")
    return jsonify(result), 200
  except Exception as e:
    log.exception("Budget coach failed")
    return jsonify(error=str(e)), 502

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)