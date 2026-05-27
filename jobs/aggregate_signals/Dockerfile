# Cloud Run Jobs container for the aggregate_signals job.
# Mirrors the template the deploy workflow would auto-generate if this
# file were absent; kept explicit because psycopg needs libpq5.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
