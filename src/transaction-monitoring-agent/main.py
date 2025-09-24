import os
import time
import requests
import google.generativeai as genai
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MCP_API_URL = "http://mcp-server:80/transactions"
USERSERVICE_API_URL = "http://userservice:8080/login"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
POLL_INTERVAL_SECONDS = 10

def get_jwt():
    """Authenticate with userservice to get a JWT."""
    try:
        payload = {"username": "testuser", "password": "bankofanthos"}  # From accounts-db test data
        response = requests.get(USERSERVICE_API_URL, params=payload)
        response.raise_for_status()
        return response.json().get("token")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get JWT: {e}")
        return None

def get_new_transactions(jwt, account_id="1010"):  # Use test account_id
    """Poll MCP server for transactions."""
    global last_seen_transaction_id
    headers = {"Authorization": f"Bearer {jwt}"}
    try:
        response = requests.get(f"{MCP_API_URL}/{account_id}", headers=headers)
        logger.debug(f"MCP response: {response.status_code}, {response.text}")
        response.raise_for_status()
        transactions = response.json()
        if not transactions:
            return []
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

def get_fraud_risk_score(transaction):
    """Use Gemini to score transaction risk."""
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY missing")
        return None
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
    prompt = f"""
    Analyze the following bank transaction for fraud risk. Provide a risk score from 1 (low) to 10 (high) and a one-sentence explanation.
    Transaction Details:
    - Amount: {transaction['amount']}
    - From Account: {transaction['from_account_num']}
    - To Account: {transaction['to_account_num']}
    - Timestamp: {transaction['timestamp']}
    Output format:
    Score: [score]
    Explanation: [explanation]
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
        logger.error("Cannot proceed without JWT")
        return
    while True:
        logger.info("Polling for new transactions...")
        new_transactions = get_new_transactions(jwt)
        if new_transactions:
            logger.info(f"Found {len(new_transactions)} new transactions")
            for t in new_transactions:
                logger.info(f"Analyzing transaction {t['transaction_id']}")
                risk_analysis = get_fraud_risk_score(t)
                if risk_analysis:
                    logger.info(f"Risk Analysis: {risk_analysis}")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    last_seen_transaction_id = None
    main()
