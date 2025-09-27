import os
import time
import requests
import google.generativeai as genai
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MCP_API_URL = "http://mcp-server.default.svc.cluster.local:80/tools/get_transaction_insights"
USERSERVICE_API_URL = "http://userservice:8080/login"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
POLL_INTERVAL_SECONDS = 10
last_seen_transaction_id = None

def get_jwt():
    """Authenticate with userservice to get a JWT."""
    try:
        payload = {"username": "testuser", "password": "bankofanthos"}
        response = requests.get(USERSERVICE_API_URL, params=payload)
        response.raise_for_status()
        return response.json().get("token")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get JWT: {e}")
        return None

def get_new_transactions(jwt, account_id="1010"):
    """Poll MCP server for transactions."""
    global last_seen_transaction_id
    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    payload = {"account_id": account_id, "window_days": 30}
    try:
        response = requests.post(MCP_API_URL, headers=headers, json=payload)
        logger.debug(f"MCP response: {response.status_code}, {response.text}")
        response.raise_for_status()
        transactions = response.json()
        if not transactions:
            return []
        # Logic to process only new transactions
        if last_seen_transaction_id:
            for i, t in enumerate(transactions):
                if t['transaction_id'] == last_seen_transaction_id:
                    new_transactions = transactions[i+1:]
                    if new_transactions:
                        last_seen_transaction_id = new_transactions[-1]['transaction_id']
                    return new_transactions
        last_seen_transaction_id = transactions[-1]['transaction_id']
        return transactions
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling MCP: {e}")
        return []

def get_financial_advice(transaction):
    """Use Gemini to get financial advice."""
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY missing")
        return None
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
    prompt = f"""
    Analyze the following bank transaction and provide a one-sentence analysis of the spending category.
    Transaction Details:
    - Amount: {transaction['amount']}
    - From Account: {transaction['from_account_num']}
    - To Account: {transaction['to_account_num']}
    - Timestamp: {transaction['timestamp']}
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return None

def main():
    logger.info("Starting Financial Advisor Agent...")
    jwt = get_jwt()
    if not jwt:
        logger.error("Cannot proceed without JWT. Exiting.")
        return
    while True:
        logger.info("Polling for new transactions...")
        new_transactions = get_new_transactions(jwt)
        if new_transactions:
            logger.info(f"Found {len(new_transactions)} new transactions.")
            for t in new_transactions:
                logger.info(f"Analyzing transaction {t['transaction_id']}...")
                advice = get_financial_advice(t)
                if advice:
                    logger.info(f"Financial Advice: {advice}")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()