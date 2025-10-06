import os, time, json, datetime as dt
import requests
import streamlit as st

# ------------------------------------------------------------------
# Endpoints (overridable via env; align with README)
# - Local (port-forward): USERSVC=http://localhost:8081, MCPSVC=http://localhost:8082, INSIGHT=http://localhost:8083/api
# - In-cluster: defaults below resolve via Cluster DNS
# ------------------------------------------------------------------
USERSVC = os.getenv("USERSVC") or os.getenv("USERSERVICE_URI") or "http://userservice:8080"
MCPSVC  = os.getenv("MCPSVC")  or os.getenv("MCP_SERVER_URI")  or "http://mcp-server:8080"
INSIGHT = os.getenv("INSIGHT") or os.getenv("INSIGHT_URI")     or "http://insight-agent/api" # Vertex build expects /api base

DEFAULT_ACCT   = os.getenv("DEMO_ACCOUNT", "1011226111")
DEFAULT_WINDOW = int(os.getenv("DEMO_WINDOW_DAYS", "30"))

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
        # Parse with timezone; prefer max timestamp
        def _p(ts):
            s = ts.replace("Z", "+00:00")
            return dt.datetime.fromisoformat(s)
        latest = max(_p(t["timestamp"]) for t in txns if t.get("timestamp"))
        return latest.date().isoformat()
    except Exception:
        return "—"

def transform_for_coach(raw, acct: str):
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

def call_budget_coach(transformed_txns):
    url = f"{INSIGHT}/budget/coach"  # Vertex build
    r = requests.post(url, json={"transactions": transformed_txns}, timeout=90)
    r.raise_for_status()
    # The service already strips fences; ensure JSON anyway
    return r.json() if r.headers.get("content-type","").startswith("application/json") else json.loads(r.text)

# ------------------------------------------------------------------
# UI
# ------------------------------------------------------------------
st.set_page_config(page_title="Budget Coach", page_icon="💸", layout="wide")

st.title("Budget Coach")
st.caption("Flow: userservice → mcp-server → insight-agent (/api/budget/coach) → Vertex AI Gemini")

col1, col2 = st.columns(2)
with col1:
    acct = st.text_input("Account", value=DEFAULT_ACCT)
with col2:
    window_days = st.number_input("Window (days)", min_value=7, max_value=120, value=DEFAULT_WINDOW, step=1)

if st.button("Generate Budget Plan", type="primary"):
    t0 = time.time()
    latest_str = "—"
    try:
        token = get_token()
        raw_txns = fetch_transactions(acct, window_days, token)
        latest_str = latest_txn_iso(raw_txns)
        txns_for_coach = transform_for_coach(raw_txns, acct)
        resp = call_budget_coach(txns_for_coach)
        dt_s = time.time() - t0

        # Header panel
        st.info(f"**Account:** `{acct}` • **Window:** {window_days} days • **Latest txn:** {latest_str}")
        # Runtime badge
        if dt_s < 5:
            st.success(f"Done in {dt_s:.1f}s")
        elif dt_s < 15:
            st.warning(f"Done in {dt_s:.1f}s")
        else:
            st.error(f"Done in {dt_s:.1f}s")

        # Render response
        summary = resp.get("summary") or resp.get("Summary")
        if summary:
            st.subheader("Summary")
            st.write(summary)

        buckets = resp.get("buckets") or resp.get("top_categories")
        if buckets:
            st.subheader("Budget Buckets")
            # show a compact table
            try:
                import pandas as pd
                df = pd.DataFrame(buckets)
                st.dataframe(df, use_container_width=True, hide_index=True)
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
