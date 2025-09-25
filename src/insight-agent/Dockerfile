FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY src/insight-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/insight-agent/main.py ./
EXPOSE 8080
CMD ["gunicorn", "-b", ":8080", "main:app"]
