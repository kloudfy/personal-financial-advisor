import os
from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Upstreams (override via env if needed)
TRANSACTION_HISTORY_API_URL = os.getenv("TRANSACTION_HISTORY_API_URL", "http://transactionhistory:8080")
BALANCE_READER_API_URL      = os.getenv("BALANCE_READER_API_URL",      "http://balancereader:8080")

@app.get("/healthz")
def healthz():
    # Fast liveness probe
    return "ok", 200

@app.get('/transactions/<account_id>')
def get_transactions(account_id):
    """
    Bridge for AI agents -> BoA transaction-history.
    - Forwards Authorization header (JWT)
    - Forwards query params (e.g., ?window_days=30)
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "Authorization header is missing"}), 401

    headers = {'Authorization': auth_header}
    url = f"{TRANSACTION_HISTORY_API_URL}/transactions/{account_id}"
    try:
        # forward any query params like window_days
        resp = requests.get(url, headers=headers, params=request.args, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json())
    except requests.exceptions.RequestException as e:
        print(f"[mcp] upstream transactions error: {e}")
        return jsonify({"error": "Failed to communicate with the transaction service."}), 502

@app.get('/balance/<account_id>')
def get_balance(account_id):
    """
    Balance proxy -> BoA balancereader.
    - No auth required by balancereader (BoA default), so we don’t forward JWT.
    - Returns upstream JSON as-is.
    """
    url = f"{BALANCE_READER_API_URL}/balances/{account_id}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        # balancereader returns: {"accountNum": "...", "balance": 1234.56}
        return jsonify(resp.json())
    except requests.exceptions.RequestException as e:
        print(f"[mcp] upstream balance error: {e}")
        return jsonify({"error": "Failed to fetch balance."}), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

