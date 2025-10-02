import os
import logging
from datetime import datetime
from collections import defaultdict
from flask import Flask, jsonify, request

# Optional Gemini
USE_GEMINI = False
try:
    import google.generativeai as genai  # type: ignore
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        USE_GEMINI = True
except Exception:
    USE_GEMINI = False

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("insight-agent")

@app.get("/healthz")
@app.get("/api/healthz")
def healthz():
    return "ok", 200

# ---------- helpers ----------

def _get_txns(payload):
    """Accept either a list[...] or {'transactions': [...]}"""
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("transactions")
    return None

def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    # best-effort: date-only if all else fails
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None

def _simple_categorize(label):
    L = (label or "").lower()
    if any(k in L for k in ["grocery", "market", "supermarket"]): return "Groceries"
    if any(k in L for k in ["uber", "lyft", "ride", "taxi", "transport"]): return "Transport"
    if any(k in L for k in ["rent", "mortgage"]): return "Housing"
    if any(k in L for k in ["netflix", "spotify", "subscription", "prime"]): return "Subscriptions"
    if any(k in L for k in ["restaurant", "dining", "food", "cafe"]): return "Dining"
    if any(k in L for k in ["salary", "paycheck", "payroll", "income", "deposit"]): return "Income"
    return "Misc"

def _analyze_spending(txns):
    # txns: list of dicts with at least amount (float), label (str), date (str)
    total_spend = 0.0
    total_income = 0.0
    days = set()
    buckets = defaultdict(float)

    for t in txns:
        amt = float(t.get("amount", 0.0))
        label = t.get("label", "")
        d = t.get("date") or t.get("timestamp") or ""
        dt = _parse_date(d)
        if dt:
            days.add(dt.date())
        cat = _simple_categorize(label)
        if amt < 0:
            total_spend += -amt
            buckets[cat] += -amt
        else:
            total_income += amt

    day_count = max(len(days), 1)
    avg_per_day = total_spend / day_count

    # top buckets (descending)
    top = sorted(buckets.items(), key=lambda x: x[1], reverse=True)[:5]
    top_buckets = [{"category": k, "total": round(v, 2)} for k, v in top]

    return {
        "total_spend": round(total_spend, 2),
        "total_income": round(total_income, 2),
        "avg_per_day": round(avg_per_day, 2),
        "buckets": top_buckets,
        "days_observed": day_count,
    }

def _gemini_summary(txns, analysis):
    prompt = f"""
You are a concise personal financial advisor.

Transactions (JSON list):
{txns}

Precomputed analysis (JSON):
{analysis}

Write a short 3–5 sentence summary of spending patterns and actionable tips.
Avoid repeating raw numbers already shown; focus on insights and next steps.
Return ONLY plain text (no code fences, no JSON).
"""
    try:
        model = genai.GenerativeModel("gemini-2.5-pro")
        resp = model.generate_content(prompt)
        text = (resp.text or "").strip()
        return text
    except Exception as e:
        log.warning("Gemini summary failed: %s", e)
        return None

# ---------- endpoints ----------

# Existing coach endpoint (kept as-is, JSON passthrough to LLM)
@app.post("/api/budget/coach")
@app.post("/budget/coach")  # backward-compat
def budget_coach():
    data = request.get_json(silent=True)
    txns = _get_txns(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]} G"), 400

    if USE_GEMINI:
        prompt = f"""
You are a personal financial coach. Analyze the transactions and provide:
1) A concise summary (2–4 sentences).
2) 3 practical budgeting tips tailored to the data.
3) A compact JSON array named 'buckets' with {{'name','total'}} for top categories.
Return strict JSON with keys: summary, tips, buckets. Transactions:
{txns}
"""
        try:
            model = genai.GenerativeModel("gemini-2.5-pro")
            resp = model.generate_content(prompt)
            text = (resp.text or "").strip()
            cleaned = text.replace("```json", "").replace("```", "").strip()
            return cleaned, 200, {"Content-Type": "application/json"}
        except Exception as e:
            log.exception("Gemini (coach) failed")
            return jsonify(error=str(e)), 500
    else:
        # Deterministic fallback
        a = _analyze_spending(txns)
        fallback = {
            "summary": "Budget coach is running without an LLM key; showing computed totals and buckets.",
            "tips": [
                "Set category budgets based on the largest buckets.",
                "Automate savings transfers right after income posts.",
                "Track recurring subscriptions and cancel those unused."
            ],
            "buckets": [{"name": b["category"], "total": b["total"]} for b in a["buckets"]],
        }
        return jsonify(fallback), 200

# New spending analysis endpoint
@app.post("/api/spending/analyze")
@app.post("/spending/analyze")  # local alias
def spending_analyze():
    data = request.get_json(silent=True)
    txns = _get_txns(data)
    if not txns:
        return jsonify(error="Expected JSON list or {'transactions': [...]}"), 400

    analysis = _analyze_spending(txns)
    summary = None
    if USE_GEMINI:
        summary = _gemini_summary(txns, analysis)
    if not summary:
        # Fallback summary
        summary = (
            f"Observed {analysis['days_observed']} days of activity. "
            f"Total spend {analysis['total_spend']}, avg per day {analysis['avg_per_day']}. "
            f"Top buckets: " + ", ".join(f"{b['category']} ({b['total']})" for b in analysis["buckets"])
        )

    result = {
        "summary": summary,
        "analysis": analysis,
    }
    return jsonify(result), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
