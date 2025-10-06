from datetime import datetime
from collections import defaultdict
import math, re

KEYWORDS = [
    (re.compile(r'\b(uber|lyft|ride)\b', re.I), "Transport"),
    (re.compile(r'\b(grocery|market|supermart|whole\s*foods|trader)\b', re.I), "Groceries"),
    (re.compile(r'\b(rent|mortgage)\b', re.I), "Housing"),
    (re.compile(r'\b(utilit(y|ies)|power|electric|water|gas)\b', re.I), "Utilities"),
    (re.compile(r'\b(amazon|target|walmart)\b', re.I), "Shopping"),
]

def _is_transfer(lbl: str) -> bool:
    s = (lbl or "")
    return s.startswith("Inbound from ") or s.startswith("Outbound to ")

def _guess_window_days(tx):
    if not tx: return 30
    days = set((t.get("date") or "")[:10] for t in tx if t.get("date"))
    return max(1, len(days))

def _categorize(lbl: str, amt: float, is_transfer: bool):
    if amt > 0:
        return "Income" if not is_transfer else "Transfers In"
    if is_transfer:
        return "Transfers Out"
    for rx, cat in KEYWORDS:
        if rx.search(lbl or ""):
            return cat
    return "Expenses"

def analyze_spending(body):
    tx = body.get("transactions", []) or []
    window_days = int(body.get("window_days") or _guess_window_days(tx))
    balance = body.get("balance")

    inbound = [t for t in tx if float(t.get("amount", 0)) > 0]
    inbound_sorted = sorted([float(t["amount"]) for t in inbound])
    big_cut = inbound_sorted[int(0.75 * len(inbound_sorted))] if inbound_sorted else 0.0

    breakdown = defaultdict(float)
    income = expenses = tin = tout = 0.0

    for t in tx:
        amt = float(t.get("amount", 0))
        lbl = t.get("label", "")
        transfer = _is_transfer(lbl)
        cat = _categorize(lbl, amt, transfer)
        if amt > 0 and not transfer and abs(amt) >= big_cut:
            cat = "Income"
        breakdown[cat] += amt
        if cat == "Income": income += amt
        elif cat == "Transfers In": tin += amt
        elif cat == "Transfers Out": tout += abs(amt)
        elif amt < 0: expenses += abs(amt)

    net = income - expenses - tout + tin
    savings_rate = (net / income * 100.0) if income > 0 else None
    avg_daily_burn = (expenses / max(1, window_days)) if expenses > 0 else 0.0
    runway = math.floor(balance / avg_daily_burn) if balance and avg_daily_burn > 0 else None

    cat_list = [{"category": k, "amount": round(v, 2)} for k, v in sorted(breakdown.items())]

    summary = []
    if income > 0: summary.append("strong income" if income > expenses*2 else "steady income")
    if expenses > 0: summary.append("moderate spending" if expenses < income else "high spending")
    summary.append("net positive cash flow" if net >= 0 else "net negative cash flow")
    if runway: summary.append(f"~{runway} days runway")
    summary = (", ".join(summary).capitalize() + ".") if summary else "No activity."

    return {
        "summary": summary,
        "totals": {
            "income": round(income, 2),
            "expenses": round(expenses, 2),
            "transfers_in": round(tin, 2),
            "transfers_out": round(tout, 2),
            "net": round(net, 2),
        },
        "savings_rate_pct": round(savings_rate, 1) if savings_rate is not None else None,
        "avg_daily_burn": round(avg_daily_burn, 2),
        "runway_days": runway,
        "category_breakdown": cat_list,
        "notes": ["keyword_cats", "recurring_large_inbound_as_income", "transfers_via_label_prefix"]
    }

