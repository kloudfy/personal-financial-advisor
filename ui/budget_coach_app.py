import os, json, re, time, unicodedata
import requests
import pandas as pd
import streamlit as st
import logging

def _clean(u: str) -> str:
    return (u or "").strip().rstrip("/")

USERSVC = _clean(os.getenv("USERSERVICE_URI", "http://userservice.default.svc.cluster.local:8080"))
MCPSVC  = _clean(os.getenv("MCP_SERVER_URI",  "http://mcp-server.default.svc.cluster.local:80"))
INSIGHT = _clean(os.getenv("INSIGHT_URI",     "http://insight-agent.default.svc.cluster.local:80"))
ACCOUNT = os.getenv("ACCOUNT", "1011226111")
WINDOW_DAYS = int(os.getenv("WINDOW_DAYS", "30"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("budget-coach")
log.info("USERSVC=%s", USERSVC)
log.info("MCPSVC=%s", MCPSVC)
log.info("INSIGHT=%s", INSIGHT)

st.set_page_config(page_title="Budget Coach", page_icon="💸", layout="wide")

st.title("Budget Coach")

DEFAULT_ACCOUNT = os.getenv("DEMO_ACCOUNT", "1011226111")
DEFAULT_WINDOW  = int(os.getenv("DEMO_WINDOW_DAYS", "30"))
st.caption("Flow: userservice → mcp-server → insight-agent (/budget/coach) → Vertex AI Gemini")

account = ACCOUNT
window_days = WINDOW_DAYS

run_btn = st.button("Generate Budget Plan", type="primary")

def _http_get(url, headers=None, timeout=8):
    log.info("HTTP GET %s", url)
    r = requests.get(url, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r

def _http_post(url, body, headers=None, timeout=120):
    r = requests.post(url, json=body, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r

def login_and_get_jwt():
    url = f"{USERSVC}/login?username=testuser&password=bankofanthos"
    r = _http_get(url)  # raises if bad
    data = r.json()
    token = data.get("token")
    if not token:
        raise RuntimeError(f"Login ok but missing token: {data}")
    return token

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
            st.write(t)

def clean_text(s: str) -> str:
    if not isinstance(s, str): return s
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\\s+", " ", s)
    return s.strip()

def _fetch_jwt():
    r = requests.get(
        f"{USERSVC}/login",
        params={"username": "testuser", "password": "bankofanthos"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("token")

def _fetch_balance(acct: str, jwt: str) -> float:
    url = f"{USERSVC}/accounts/{acct}/balance"
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=20,
    )
    r.raise_for_status()
    return float(r.json().get("balance", 0.0))

def _latest_txn_date(txns):
    try:
        return max(
            (t.get("date", "") for t in txns if t.get("date")),
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

        # P0-safe: balance is optional (userservice has no balance endpoint right now)
        balance = None
        try:
            jwt2 = _fetch_jwt()
            balance = _fetch_balance(ACCOUNT, jwt2)
        except Exception as e:
            log.warning("Skipping balance fetch (non-fatal): %s", e)

        info = (
            f"**Account:** `{ACCOUNT}` · **Window:** {WINDOW_DAYS} days · "
            f"**Latest txn:** {_latest_txn_date(txns)}"
        )
        if isinstance(balance, (int, float)):
            info = (
                f"**Account:** `{ACCOUNT}` · **Window:** {WINDOW_DAYS} days · "
                f"**Current balance:** ${balance:,.2f} · **Latest txn:** {_latest_txn_date(txns)}"
            )
        st.info(info)

        st.success(f"Done in {t1 - t0:.1f}s")
        render_result(result)
        with st.expander("Raw JSON", expanded=False):
            st.code(json.dumps(result, indent=2))
    except requests.HTTPError as e:
        st.error(f"HTTP error: {e.response.status_code} {e.response.text[:300]}")
    except Exception as e:
        st.exception(e)

st.caption("Flow: userservice → mcp-server → insight-agent (/budget/coach) → Vertex AI Gemini")