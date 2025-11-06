# Multi-stage build for smaller image

FROM python:3.11-slim AS builder
WORKDIR /install
COPY backend/requirements.txt .
ENV PIP_DEFAULT_TIMEOUT=120
RUN apt-get update && apt-get install -y build-essential gcc libpq-dev \
        && pip install --upgrade pip \
        && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.2.2 torchvision==0.17.2 \
        && pip install --no-cache-dir -r requirements.txt \
        && python -m spacy download en_core_web_sm --direct \
        && python -c "import nltk; nltk.download('wordnet'); nltk.download('omw-1.4')"
RUN apt-get remove -y build-essential gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

FROM python:3.11-slim

# Build-time metadata (can be overridden by CI)
ARG APP_VERSION="dev"
ARG GIT_SHA="unknown"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY backend/app /app/app


# Create data directories for CSV datasets, AI engine files, and model cache
RUN mkdir -p /app/data /app/data/ai /app/app/models_cache/all-MiniLM-L6-v2
# Bootstrap bundle will be mounted via docker-compose volume
# Legacy CSV files removed - using bootstrap bundle for faster initialization
# COPY TMDB_movie_dataset_v11.csv /app/data/
# COPY TMDB_tv_dataset_v3.csv /app/data/
# COPY backend/data/trakt_mappings_export.csv /app/data/
# Copy local sentence-transformers model files
COPY backend/app/models_cache/all-MiniLM-L6-v2 /app/app/models_cache/all-MiniLM-L6-v2

# Expose build metadata as labels and env
LABEL org.opencontainers.image.title="WatchBuddy Backend" \
          org.opencontainers.image.version="$APP_VERSION" \
          org.opencontainers.image.revision="$GIT_SHA" \
          org.opencontainers.image.source="https://github.com/steffenbb/WatchBuddy"

ENV APP_VERSION=$APP_VERSION \
    GIT_SHA=$GIT_SHA

CMD export POSTGRES_PASSWORD=$(cat ${POSTGRES_PASSWORD_FILE:-/dev/null} 2>/dev/null || echo $POSTGRES_PASSWORD) && export APP_KEY=$(cat ${POSTGRES_PASSWORD_FILE:-/dev/null} 2>/dev/null || echo $APP_KEY) && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 75 --timeout-graceful-shutdown 30 --workers 2
