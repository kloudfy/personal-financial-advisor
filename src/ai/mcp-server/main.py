import os
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("mcp-server")

# --- Configuration ---
# If MOCK_MODE=true, we return synthetic insights without calling BoA.
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"

# If not mocking, we can optionally fetch transactions from transactionhistory.
TRANSACTION_HISTORY_URL = os.getenv("TRANSACTION_HISTORY_URL", "http://transactionhistory.default.svc.cluster.local:8080")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SEC", "8.0"))

# Example: if you later protect this with IAP/JWT, add an auth header builder here.


@app.get("/healthz")
def healthz():
    return jsonify(status="ok", service="mcp-server", mock_mode=MOCK_MODE), 200


@app.post("/tools/get_transaction_insights")
def get_transaction_insights():
    """
    Tool endpoint expected by the agent-gateway (and eventually Vertex Agent).
    Input JSON:
    {
      "account_id": "0000000001",
      "window_days": 30,
      "prompt": "optional, user prompt"
    }

    Output JSON (example):
    {
      "summary": "...",
      "top_categories": [{"name": "Groceries", "amount": 123.45}, ...],
      "unusual_transactions": [{"id": "...", "amount": 999.99, "reason": "High outlier"}, ...]
    }
    """
    try:
        body: Dict[str, Any] = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify(error="Invalid JSON body"), 400

    account_id = str(body.get("account_id", "")).strip()
    window_days = int(body.get("window_days", 30))

    if not account_id:
        return jsonify(error="account_id is required"), 400

    # 1) Gather transactions
    if MOCK_MODE:
        txns = _mock_transactions(account_id, window_days)
    else:
        try:
            txns = _fetch_transactions(account_id, window_days)
        except Exception as e:
            log.exception("Failed to fetch transactions from transactionhistory")
            return jsonify(error=f"transactionhistory fetch failed: {e}"), 502

    # Modified: Return raw transactions instead of insights
    return jsonify(txns), 200


# ----- Helpers -----

def _mock_transactions(account_id: str, window_days: int) -> List[Dict[str, Any]]:
    """Generate a tiny synthetic transaction set for quick demos."""
    now = datetime.utcnow()
    txns = [
        {"id": "t1", "account_id": account_id, "timestamp": (now - timedelta(days=3)).isoformat() + "Z",
         "merchant": "Fresh Mart", "category": "Groceries", "amount": 42.75},
        {"id": "t2", "account_id": account_id, "timestamp": (now - timedelta(days=2)).isoformat() + "Z",
         "merchant": "City Transport", "category": "Transport", "amount": 18.00},
        {"id": "t3", "account_id": account_id, "timestamp": (now - timedelta(days=1)).isoformat() + "Z",
         "merchant": "Coffee Corner", "category": "Dining", "amount": 6.50},
        {"id": "t4", "account_id": account_id, "timestamp": (now - timedelta(days=1)).isoformat() + "Z",
         "merchant": "TechZone", "category": "Electronics", "amount": 399.99},
    ]
    return txns


def _fetch_transactions(account_id: str, window_days: int) -> List[Dict[str, Any]]:
    """
    This is a best-effort placeholder; wire to your real transactionhistory API if needed.
    Adjust path/params to match your service.
    """
    # TODO: A valid JWT should be passed from the caller (agent-gateway)
    # and included in the headers.
    headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"} # Dummy token

    url = f"{TRANSACTION_HISTORY_URL.rstrip('/')}/transactions/{account_id}"
    # The real service does not use window_days, but we'll keep it for now.
    params = {"window_days": window_days}
    log.info(f"GET {url}")
    rsp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    rsp.raise_for_status()
    data = rsp.json()
    # Expecting data to be a list of transaction dicts
    return data


def _analyze_transactions_simple(txns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Very simple heuristics to produce a structured insight payload."""
    total = sum(float(t.get("amount", 0.0)) for t in txns)
    by_cat: Dict[str, float] = {}
    for t in txns:
        cat = t.get("category", "Other")
        by_cat[cat] = by_cat.get(cat, 0.0) + float(t.get("amount", 0.0))

    top = sorted(
        [{"name": k, "amount": round(v, 2)} for k, v in by_cat.items()],
        key=lambda x: x["amount"],
        reverse=True,
    )[:3]

    # Unusual txns: naive high outlier > 2x average
    avg = (total / max(len(txns), 1)) if txns else 0.0
    unusual = [
        {
            "id": t.get("id"),
            "amount": float(t.get("amount", 0.0)),
            "merchant": t.get("merchant"),
            "reason": f"High relative to avg ({avg:.2f})",
        }
        for t in txns if float(t.get("amount", 0.0)) > 2.0 * avg and avg > 0
    ]

    summary = f"{len(txns)} transactions analyzed. Total spend ${total:.2f}. " \
              f"Top categories: " + ", ".join([c['name'] for c in top]) if top else "No categories."

    return {
        "summary": summary,
        "top_categories": top,
        "unusual_transactions": unusual,
        "total_spend": round(total, 2),
        "count": len(txns),
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))