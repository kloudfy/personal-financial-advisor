import os, time, json, datetime as dt
import requests
import streamlit as st

# ------------------------------------------------------------------
# Endpoints (overridable via env; align with README)
# - Local (port-forward):
#     USERSVC=http://localhost:8081
#     MCPSVC=http://localhost:8082
#     INSIGHT=http://localhost:8083/api
# - In-cluster defaults resolve via Cluster DNS
# ------------------------------------------------------------------
USERSVC = os.getenv("USERSVC") or os.getenv("USERSERVICE_URI") or "http://userservice:8080"
MCPSVC  = os.getenv("MCPSVC")  or os.getenv("MCP_SERVER_URI")  or "http://mcp-server"
INSIGHT = os.getenv("INSIGHT") or os.getenv("INSIGHT_URI")     or "http://insight-agent/api"  # Vertex build uses /api base

DEFAULT_ACCT   = os.getenv("DEMO_ACCOUNT", "1011226111")
DEFAULT_WINDOW = int(os.getenv("DEMO_WINDOW_DAYS", "30"))

UI_WARN_SEC = float(os.getenv("UI_WARN_SEC", "15"))
UI_ERR_SEC  = float(os.getenv("UI_ERR_SEC",  "40"))

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def get_token(username="testuser", password="bankofanthos") -> str:
    r = requests.get(f"{USERSVC}/login", params={"username": username, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json().get("token", "")

def fetch_transactions(acct: str, window_days: int, token: str):
    hdr = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{MCPSVC}/transactions/{acct}", params={"window_days": window_days}, headers=hdr, timeout=30)
    r.raise_for_status()
    return r.json()  # raw BoA shape

def normalize_ts(ts: str) -> str:
    # e.g. "2025-10-04T20:09:07.000+00:00" -> "2025-10-04T20:09:07Z"
    return ts.replace(".000+00:00", "Z").replace("+00:00", "Z")

def latest_txn_iso(txns) -> str:
    if not txns:
        return "—"
    try:
        def _p(ts):
            s = ts.replace("Z", "+00:00")
            return dt.datetime.fromisoformat(s)
        latest = max(_p(t["timestamp"]) for t in txns if t.get("timestamp"))
        return latest.date().isoformat()
    except Exception:
        return "—"

def transform_for_agent(raw, acct: str):
    """Map BoA txns → {date,label,amount} as used by Coach/Spending/Fraud."""
    out = []
    for t in raw:
        ts = t.get("timestamp", "")
        to_acct = str(t.get("toAccountNum", ""))
        from_acct = str(t.get("fromAccountNum", ""))
        amt = float(t.get("amount", 0))
        inbound = (to_acct == str(acct))
        out.append({
            "date": normalize_ts(ts),
            "label": f"Inbound from {from_acct}" if inbound else f"Outbound to {to_acct}",
            "amount": amt if inbound else -amt,
        })
    return out

# ----- Calls to insight-agent endpoints
def call_budget_coach(transformed_txns):
    url = f"{INSIGHT}/budget/coach"
    r = requests.post(url, json={"transactions": transformed_txns}, timeout=90)
    r.raise_for_status()
    return r.json() if r.headers.get("content-type","").startswith("application/json") else json.loads(r.text)

def call_spending_analyze(transformed_txns):
    url = f"{INSIGHT}/spending/analyze"
    r = requests.post(url, json={"transactions": transformed_txns}, timeout=90)
    r.raise_for_status()
    return r.json() if r.headers.get("content-type","").startswith("application/json") else json.loads(r.text)

def call_fraud_detect(transformed_txns, use_fast=True, account_context=None):
    url = f"{INSIGHT}/fraud/detect"
    if use_fast:
        url += "?fast=true"
    payload = {"transactions": transformed_txns}
    if account_context:
        payload["account_context"] = account_context
    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    return r.json() if r.headers.get("content-type","").startswith("application/json") else json.loads(r.text)

def runtime_badge(dt_s: float):
    if dt_s < UI_WARN_SEC:
        st.success(f"Done in {dt_s:.1f}s")
    elif dt_s < UI_ERR_SEC:
        st.warning(f"Done in {dt_s:.1f}s")
    else:
        st.error(f"Done in {dt_s:.1f}s")

# ------------------------------------------------------------------
# UI
# ------------------------------------------------------------------
st.set_page_config(page_title="Budget Coach", page_icon="💸", layout="wide")
st.title("Budget Coach")

st.caption("Flow: userservice → mcp-server → insight-agent (/api/*) → Vertex AI Gemini")
with st.expander("Endpoints", expanded=False):
    st.code(json.dumps({"USERSVC": USERSVC, "MCPSVC": MCPSVC, "INSIGHT": INSIGHT}, indent=2))

col1, col2 = st.columns(2)
with col1:
    acct = st.text_input("Account", value=DEFAULT_ACCT)
with col2:
    window_days = st.number_input("Window (days)", min_value=7, max_value=120, value=DEFAULT_WINDOW, step=1)

tab_coach, tab_spend, tab_fraud = st.tabs(["Coach", "Spending", "Fraud"])

# --- Coach tab
with tab_coach:
    if st.button("Generate Budget Plan", type="primary"):
        t0 = time.time()
        try:
            token = get_token()
            raw_txns = fetch_transactions(acct, window_days, token)
            latest_str = latest_txn_iso(raw_txns)
            tx = transform_for_agent(raw_txns, acct)
            resp = call_budget_coach(tx)
            runtime_badge(time.time() - t0)

            st.info(f"**Account:** `{acct}` • **Window:** {window_days} days • **Latest txn:** {latest_str}")

            summary = resp.get("summary") or resp.get("Summary")
            if summary:
                st.subheader("Summary")
                st.write(summary)

            buckets = resp.get("buckets") or resp.get("top_categories")
            if buckets:
                st.subheader("Budget Buckets")
                try:
                    import pandas as pd
                    st.dataframe(pd.DataFrame(buckets), use_container_width=True, hide_index=True)
                except Exception:
                    st.json(buckets)

            tips = resp.get("tips") or []
            if tips:
                st.subheader("Tips")
                if isinstance(tips, list):
                    for t in tips:
                        st.markdown(f"- {t}")
                else:
                    st.write(tips)

            with st.expander("Raw JSON"):
                st.code(json.dumps(resp, indent=2), language="json")

        except requests.HTTPError as e:
            st.error(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
        except Exception as e:
            st.exception(e)
    else:
        st.info(f"**Account:** `{DEFAULT_ACCT}` • **Window:** {DEFAULT_WINDOW} days • **Latest txn:** —")

# --- Spending tab
with tab_spend:
    if st.button("Analyze Spending"):
        t0 = time.time()
        try:
            token = get_token()
            raw_txns = fetch_transactions(acct, window_days, token)
            latest_str = latest_txn_iso(raw_txns)
            tx = transform_for_agent(raw_txns, acct)
            resp = call_spending_analyze(tx)
            runtime_badge(time.time() - t0)

            st.info(f"**Account:** `{acct}` • **Window:** {window_days} days • **Latest txn:** {latest_str}")

            st.subheader("Summary")
            st.write(resp.get("summary", ""))

            top = resp.get("top_categories") or []
            if top:
                st.subheader("Top Categories")
                try:
                    import pandas as pd
                    st.dataframe(pd.DataFrame(top), use_container_width=True, hide_index=True)
                except Exception:
                    st.json(top)

            n_unusual = resp.get("n_unusual") or len(resp.get("unusual_transactions", []) or [])
            st.caption(f"Unusual transactions: **{n_unusual}**")

            with st.expander("Raw JSON"):
                st.code(json.dumps(resp, indent=2), language="json")
        except Exception as e:
            st.exception(e)

# --- Fraud tab
with tab_fraud:
    use_fast = st.checkbox("Fast pre-screen (z-score)", value=True, help="Pre-scan amounts; only send anomalies to the model.")
    if st.button("Run Fraud Scout"):
        t0 = time.time()
        try:
            token = get_token()
            raw_txns = fetch_transactions(acct, window_days, token)
            latest_str = latest_txn_iso(raw_txns)
            tx = transform_for_agent(raw_txns, acct)

            # Optional baseline you might wire later:
            context = {}
            resp = call_fraud_detect(tx, use_fast=use_fast, account_context=context)
            runtime_badge(time.time() - t0)

            st.info(f"**Account:** `{acct}` • **Window:** {window_days} days • **Latest txn:** {latest_str}")

            overall = (resp.get("overall_risk") or "").lower()
            if overall == "high":
                st.error("Overall risk: **HIGH**")
            elif overall == "medium":
                st.warning("Overall risk: **MEDIUM**")
            else:
                st.success("Overall risk: **LOW**")

            findings = resp.get("findings") or []
            st.write(f"Findings: **{len(findings)}**")
            for i, f in enumerate(findings[:10], 1):
                with st.expander(f"Finding {i} — score {f.get('risk_score',0):.2f}"):
                    st.write(f.get("reason",""))
                    st.write(f"**Indicators:** {', '.join(f.get('indicators', []))}")
                    t = f.get("transaction") or {}
                    st.code(json.dumps(t, indent=2), language="json")
                    reco = f.get("recommendation")
                    if reco:
                        st.info(reco)

            with st.expander("Raw JSON"):
                st.code(json.dumps(resp, indent=2), language="json")
        except Exception as e:
            st.exception(e)
