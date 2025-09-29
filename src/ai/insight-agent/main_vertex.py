import json
import logging
import os
from typing import Any, List

from flask import Flask, jsonify, request

# Backend selection (default = vertex). ADK stays optional behind a flag.
BACKEND = os.getenv("INSIGHT_BACKEND", "vertex").lower()

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("insight-agent")

@app.get("/healthz")
def healthz():
    return "ok", 200

def _coerce_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        # Graceful fallback if model returns plain text
        return {"summary": text.strip(), "top_categories": [], "unusual_transactions": []}

# -----------------------------
# Vertex path (default)
# -----------------------------
if BACKEND == "vertex":
    import vertexai
    from vertexai.generative_models import GenerativeModel

    PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
    LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
    MODEL_ID = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")

    vertexai.init(project=PROJECT, location=LOCATION)
    _model = GenerativeModel(MODEL_ID)

    def _analyze_with_backend(transactions: List[Any]) -> dict:
        prompt = (
            "You are a personal financial advisor. Analyze the transactions and return JSON with keys: "
            "`summary` (string), `top_categories` (array of {name,total}), "
            "`unusual_transactions` (array of {amount,reason,index}).\n"
            f"Transactions (JSON array):\n{json.dumps(transactions)}"
        )
        resp = _model.generate_content(prompt)
        text = (resp.text or "").strip()
        return _coerce_json(text)

# -----------------------------
# ADK path (optional)
# -----------------------------
elif BACKEND == "adk":
    try:
        # Only import ADK if explicitly requested
        from adk.api import adk
        from adk.builders import AgentBuilder
    except Exception as e:
        raise RuntimeError("INSIGHT_BACKEND=adk but ADK is not installed") from e

    @adk.tool
    def financial_analyzer(transactions: str) -> str:
        return (
            "You are a personal financial advisor. Analyze the transactions and return JSON with keys: "
            "`summary` (string), `top_categories` (array of {name,total}), "
            "`unusual_transactions` (array of {amount,reason,index}).\n"
            f"Transactions (JSON array):\n{transactions}"
        )

    _agent = AgentBuilder().with_tools(financial_analyzer).build()

    def _analyze_with_backend(transactions: List[Any]) -> dict:
        # NOTE: this assumes adk.run returns model output text; coerce to JSON.
        result_text = adk.run(_agent, {"transactions": json.dumps(transactions)})
        return _coerce_json(result_text)

else:
    raise RuntimeError(f"Unknown INSIGHT_BACKEND: {BACKEND}. Expected 'vertex' or 'adk'.")

@app.post("/analyze")
def analyze():
    data = request.get_json(silent=True) or {}
    transactions = data if isinstance(data, list) else data.get("transactions")
    if not transactions:
        return jsonify(error="Expected JSON list or {'transactions': [...]} G"), 400
    try:
        out = _analyze_with_backend(transactions)
        return jsonify(out), 200
    except Exception as e:
        log.exception("Analyze failed")
        return jsonify(error=str(e)), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
