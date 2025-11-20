# syntax=docker/dockerfile:1.4

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

# Install runtime dependencies and setup in one layer
RUN apt-get update && apt-get install -y libgomp1 && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/data /app/data/ai /app/app/models_cache/all-MiniLM-L6-v2 /app/data/parts

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy bootstrap parts EARLY (rarely change, cached layer, each ~700MB for Docker Hub limit)
COPY backend/data/watchbuddy_bootstrap.part.00 /app/data/parts/
COPY backend/data/watchbuddy_bootstrap.part.01 /app/data/parts/
COPY backend/data/watchbuddy_bootstrap.part.02 /app/data/parts/
COPY backend/data/watchbuddy_bootstrap.part.03 /app/data/parts/
COPY backend/data/watchbuddy_bootstrap.part.04 /app/data/parts/

# Copy local sentence-transformers model files EARLY (rarely change, cached layer)
COPY backend/app/models_cache/all-MiniLM-L6-v2 /app/app/models_cache/all-MiniLM-L6-v2

# Copy application code LATE (changes frequently, invalidates fewer layers above)
COPY backend/app /app/app
COPY backend/entrypoint.sh /app/entrypoint.sh
COPY backend/*.py /app/
RUN chmod +x /app/entrypoint.sh

# Expose build metadata as labels and env
LABEL org.opencontainers.image.title="WatchBuddy Backend" \
          org.opencontainers.image.version="$APP_VERSION" \
          org.opencontainers.image.revision="$GIT_SHA" \
          org.opencontainers.image.source="https://github.com/steffenbb/WatchBuddy"

ENV APP_VERSION=$APP_VERSION \
    GIT_SHA=$GIT_SHA

CMD ["/app/entrypoint.sh"]
