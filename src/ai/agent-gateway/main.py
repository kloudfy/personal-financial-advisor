import os
import logging
from typing import Any, Dict

from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("agent-gateway")

# --- Configuration (env) ---
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://mcp-server.default.svc.cluster.local")
MCP_TOOL_PATH = os.getenv("MCP_TOOL_PATH", "/tools/get_transaction_insights")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SEC", "8.0"))

# Optional Vertex/Project context (not strictly needed by this stub, but handy to surface)
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "changeme")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
VERTEX_AGENT_ID = os.getenv("VERTEX_AGENT_ID", "agent-123")


@app.get("/healthz")
def healthz():
    return jsonify(
        status="ok",
        service="agent-gateway",
        project=GOOGLE_CLOUD_PROJECT,
        location=VERTEX_LOCATION,
        agent_id=VERTEX_AGENT_ID,
    ), 200


@app.post("/chat")
def chat():
    """
    Minimal “chat” entrypoint for the demo.
    Expects JSON like:
    {
      "prompt": "analyze my spending",
      "account_id": "0000000001",
      "window_days": 30
    }

    In this stub, we ignore the model call and simply request the MCP tool directly,
    returning its JSON result to the caller.
    """
    try:
        body: Dict[str, Any] = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify(error="Invalid JSON body"), 400

    account_id = str(body.get("account_id", "")).strip()
    window_days = int(body.get("window_days", 30))
    prompt = body.get("prompt", "")

    if not account_id:
        return jsonify(error="account_id is required"), 400

    payload = {
        "account_id": account_id,
        "window_days": window_days,
        "prompt": prompt,
    }

    tool_url = f"{MCP_SERVER_URL.rstrip('/')}{MCP_TOOL_PATH}"
    log.info(f"Calling MCP tool: {tool_url} (account_id={account_id}, window_days={window_days})")

    try:
        rsp = requests.post(tool_url, json=payload, timeout=TIMEOUT)
        rsp.raise_for_status()
        data = rsp.json()
    except requests.HTTPError as e:
        log.exception("MCP call failed (HTTP)")
        return jsonify(error=f"MCP tool error: {e}", details=rsp.text if 'rsp' in locals() else None), 502
    except Exception as e:
        log.exception("MCP call failed")
        return jsonify(error=f"MCP tool request failed: {e}"), 502

    # Wrap the tool output as the “assistant” final
    return jsonify(
        project=GOOGLE_CLOUD_PROJECT,
        agent="agent-gateway",
        result=data,
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))