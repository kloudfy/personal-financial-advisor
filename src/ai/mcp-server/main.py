import os
from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

TRANSACTION_HISTORY_API_URL = "http://transactionhistory:8080"

@app.get("/healthz")
def healthz():
    # No upstream calls, no auth, fast and always-200 if the process is alive.
    return "ok", 200

@app.route('/transactions/<account_id>', methods=['GET'])
def get_transactions(account_id):
    """
    Endpoint to be called by AI agents.
    Forwards the request with its auth header to the real transaction-history service.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "Authorization header is missing"}), 401

    headers = {'Authorization': auth_header}
    url = f"{TRANSACTION_HISTORY_API_URL}/transactions/{account_id}"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        print(f"Error calling upstream service: {e}")
        return jsonify({"error": "Failed to communicate with the transaction service."}), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))