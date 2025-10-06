.PHONY: ui-demo
ui-demo:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r ui/requirements.txt && \
	USERSVC=http://localhost:8081 \
	MCPSVC=http://localhost:8082 \
	INSIGHT=http://localhost:8083/api \
	USERSERVICE_URI=http://localhost:8081 \
	MCP_SERVER_URI=http://localhost:8082 \
	INSIGHT_URI=http://localhost:8083/api \
	streamlit run ui/budget_coach_app.py
