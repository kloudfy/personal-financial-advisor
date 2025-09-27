FROM python:3.11-slim

WORKDIR /app

# Copy the specific requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the specific application code
COPY main.py .

EXPOSE 8080
CMD ["python", "main.py"]
