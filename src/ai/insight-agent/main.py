import os
import logging
from flask import Flask, jsonify, request
import google.generativeai as genai

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("insight-agent")

@app.get("/healthz")
def healthz():
  return "ok", 200

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
  genai.configure(api_key=GEMINI_API_KEY)
else:
  log.warning("GEMINI_API_KEY not set; /analyze will fail until provided.")

model = genai.GenerativeModel("gemini-1.5-pro")

@app.post("/analyze")
def analyze():
  data = request.get_json(silent=True)
  if not data:
    return jsonify(error="No JSON body"), 400
  transactions = data if isinstance(data, list) else data.get("transactions")
  if not transactions:
    return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

  prompt = f"""
  You are a personal financial advisor. Analyze the following list of bank transactions and provide:
  1) A concise paragraph summarizing spending habits.
  2) The top 3 spending categories (name + total amount).
  3) Any unusual or potentially fraudulent transactions.
  Respond strictly as JSON with keys: summary, top_categories, unusual_transactions.
  Transactions:
  {transactions}
  """

  try:
    resp = model.generate_content(prompt)
    text = (resp.text or "").strip()
    cleaned = text.replace("```json", "").replace("```", "").strip()
    return cleaned, 200, {"Content-Type": "application/json"}
  except Exception as e:
    log.exception("Gemini call failed")
    return jsonify(error=str(e)), 500

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
