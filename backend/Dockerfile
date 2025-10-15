# Multi-stage build for smaller image

FROM python:3.11-slim AS builder
WORKDIR /install
COPY backend/requirements.txt .
RUN apt-get update && apt-get install -y build-essential gcc libpq-dev \
        && pip install --upgrade pip \
        && pip install --no-cache-dir -r requirements.txt \
        && apt-get remove -y build-essential gcc \
        && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY backend/app /app/app

# Create data directory for CSV datasets (persistent candidate bootstrap)
RUN mkdir -p /app/data
# Copy TMDB datasets from project root
COPY TMDB_movie_dataset_v11.csv /app/data/
COPY TMDB_tv_dataset_v3.csv /app/data/

CMD export POSTGRES_PASSWORD=$(cat ${POSTGRES_PASSWORD_FILE:-/dev/null} 2>/dev/null || echo $POSTGRES_PASSWORD) && export APP_KEY=$(cat ${APP_KEY_FILE:-/dev/null} 2>/dev/null || echo $APP_KEY) && exec uvicorn app.main:app --host 0.0.0.0 --port 8000
