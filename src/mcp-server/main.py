import os
import logging
import sys
import traceback
from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

TRANSACTION_HISTORY_API_URL = "http://transactionhistory:8080"

@app.route('/transactions/<account_id>', methods=['GET'])
def get_transactions(account_id):
    """
    Endpoint to be called by AI agents.
    Forwards the request with its auth header to the real transaction-history service.
    """
    logger.debug(f"Received request for account_id: {account_id}")
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        logger.error("Authorization header is missing")
        return jsonify({"error": "Authorization header is missing"}), 401

    headers = {'Authorization': auth_header}
    url = f"{TRANSACTION_HISTORY_API_URL}/transactions/{account_id}"
    logger.debug(f"Forwarding to upstream: {url} with headers: {headers}")

    try:
        response = requests.get(url, headers=headers)
        logger.debug(f"Upstream response status: {response.status_code}, body: {response.text}")
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling transaction-history service: {str(e)}")
        error_details = e.response.text if hasattr(e, 'response') and e.response else str(e)
        logger.error(f"Upstream error details: {error_details}")
        traceback.print_exc(file=sys.stdout)
        return jsonify({"error": "Failed to communicate with the transaction service.", "details": error_details}), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=True)
