FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY gcp/requirements-gcp.txt /app/gcp/requirements-gcp.txt
RUN pip install --no-cache-dir -r /app/gcp/requirements-gcp.txt

COPY backend /app/backend
COPY gcp /app/gcp

RUN chmod +x /app/gcp/entrypoint.sh

ENTRYPOINT ["/app/gcp/entrypoint.sh"]
