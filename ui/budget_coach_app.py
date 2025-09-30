import os, json, re, time, unicodedata
import requests
import pandas as pd
import streamlit as st

INSIGHT_AGENT_URL = os.getenv("INSIGHT_AGENT_URL", "http://insight-agent.default.svc.cluster.local")
ACCOUNT = os.getenv("ACCOUNT", "1011226111")
WINDOW_DAYS = int(os.getenv("WINDOW_DAYS", "30"))

st.set_page_config(page_title="Budget Coach", page_icon="💸", layout="wide")

st.title("Budget Coach")
st.caption("Flow: userservice → mcp-server → insight-agent (/budget/coach) → Vertex AI Gemini")

with st.expander("Configuration", expanded=False):
    USERSVC = st.text_input("Userservice URL", USERSVC)
    MCPSVC  = st.text_input("MCP Server URL", MCPSVC)
    INSIGHT = st.text_input("Insight Agent URL", INSIGHT)

colA, colB = st.columns([2,1])
with colA:
    account = st.text_input("Account ID", DEFAULT_ACCOUNT)
with colB:
    window_days = st.number_input("Window (days)", min_value=7, max_value=180, value=DEFAULT_WINDOW, step=1)

run_btn = st.button("Generate Budget Plan", type="primary")

def _http_get(url, headers=None, timeout=20):
    r = requests.get(url, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r

def _http_post(url, body, headers=None, timeout=120):
    r = requests.post(url, json=body, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r

def login_and_get_jwt():
    # BoA demo user
    url = f"{USERSVC}/login?username=testuser&password=bankofanthos"
    r = _http_get(url)
    try:
        obj = r.json()
        return obj["token"]
    except Exception:
        raise RuntimeError(f"Login not JSON / missing token: {r.text[:300]}")

def fetch_transactions(jwt, acct, window):
    url = f"{MCPSVC}/transactions/{acct}?window_days={window}"
    r = _http_get(url, headers={"Authorization": f"Bearer {jwt}"})
    return r.json()  # array

def call_budget_coach(transactions):
    url = f"{INSIGHT}/budget/coach"
    r = _http_post(url, {"transactions": transactions})
    return r.json()

def render_result(result):
    st.subheader("Summary")
    st.write(result.get("summary", "—"))

    buckets = result.get("budget_buckets", [])
    if buckets:
        import pandas as pd
        df = pd.DataFrame(buckets)
        st.subheader("Budget Buckets")
        st.dataframe(df, use_container_width=True)
        try:
            st.bar_chart(df.set_index("name")[["pct"]])
        except Exception:
            pass

    tips = result.get("tips", [])
    if tips:
        st.subheader("Tips")
        for t in tips:
def clean_text(s: str) -> str:
    if not isinstance(s, str): return s
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _fetch_jwt():
    r = requests.get(
        "http://userservice.default.svc.cluster.local:8080/login",
        params={"username": "testuser", "password": "bankofanthos"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("token")

def _fetch_balance(acct: str, jwt: str) -> float:
    url = f"http://userservice.default.svc.cluster.local:8080/accounts/{acct}/balance"
    r = requests.get(url, headers={"Authorization": f"Bearer {jwt}"},
        timeout=20,
    )
    r.raise_for_status()
    return float(r.json().get("balance", 0.0))

def _latest_txn_date(txns):
    try:
        return max((t.get("date","") for t in txns if t.get("date")),
            default="—"
        )
    except Exception:
        return "—"

if run_btn:
    try:
        with st.spinner("Authenticating..."):
            jwt = login_and_get_jwt()
        with st.spinner("Fetching transactions..."):
            txns = fetch_transactions(jwt, account, window_days)
        with st.spinner("Calling Budget Coach (Vertex Gemini)..."):
            t0 = time.time()
            result = call_budget_coach(txns)
            t1 = time.time()
        jwt = _fetch_jwt()
        balance = _fetch_balance(ACCOUNT, jwt)
        st.info(
            f"**Account:** `{ACCOUNT}` · **Window:** {WINDOW_DAYS} days · "
            f"**Current balance:** ${balance:,.2f} · **Latest txn:** {_latest_txn_date(txns)}"
        )
        st.success(f"Done in {t1 - t0:.1f}s")
        render_result(result, time.time() - t0) # Pass elapsed time to render_result
        with st.expander("Raw JSON", expanded=False):
            st.code(json.dumps(result, indent=2))
    except requests.HTTPError as e:
        st.error(f"HTTP error: {e.response.status_code} {e.response.text[:300]}")
    except Exception as e:
        st.exception(e)

st.caption("Flow: userservice → mcp-server → insight-agent (/budget/coach) → Vertex AI Gemini")