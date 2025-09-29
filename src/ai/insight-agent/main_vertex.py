import os
import logging
from flask import Flask, jsonify, request
from adk.api import adk
from adk.builders import AgentBuilder

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("insight-agent-vertex")

@app.get("/healthz")
def healthz():
  return "ok", 200

# Define the financial analysis tool
@adk.tool
def financial_analyzer(transactions: str) -> str:
    """Analyzes a list of bank transactions to summarize spending habits,
    identify top spending categories, and flag unusual transactions.

    Args:
        transactions: A string representation of the list of transactions.

    Returns:
        A JSON string with keys: summary, top_categories, unusual_transactions.
    """
    prompt = f"""
    You are a personal financial advisor. Analyze the following list of bank transactions and provide:
    1) A concise paragraph summarizing spending habits.
    2) The top 3 spending categories (name + total amount).
    3) Any unusual or potentially fraudulent transactions.
    Respond strictly as JSON with keys: summary, top_categories, unusual_transactions.
    Transactions:
    {transactions}
    """
    # The ADK will handle the model interaction and return the response.
    # For now, we just return the prompt to show the flow.
    # In a real implementation, the ADK would execute this prompt.
    return prompt

# Build the agent
agent = AgentBuilder().with_tools(financial_analyzer).build()

@app.post("/analyze")
def analyze():
  data = request.get_json(silent=True)
  if not data:
    return jsonify(error="No JSON body"), 400
  transactions = data if isinstance(data, list) else data.get("transactions")
  if not transactions:
    return jsonify(error="Expected JSON list or {'transactions': [...]} G"), 400

  try:
    # Use the ADK to run the agent
    result = adk.run(agent, {"transactions": str(transactions)})
    # The ADK's response will be a string, which we assume is the JSON we want.
    return result, 200, {"Content-Type": "application/json"}
  except Exception as e:
    log.exception("ADK agent run failed")
    return jsonify(error=str(e)), 500

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)