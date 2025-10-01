import requests
r = requests.get("http://mcp-server.default.svc.cluster.local:80/transactions/1011226111?window_days=30", timeout=5)
print("HTTP", r.status_code, r.text[:120])
