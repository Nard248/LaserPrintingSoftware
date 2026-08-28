# ============================================================
# Labgate 2PP Control Platform - Dockerfile
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LABGATE_HOST=0.0.0.0 \
    LABGATE_PORT=8523

WORKDIR /app

# Install system dependencies for OpenCV and general utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy package declaration & source files
COPY pyproject.toml .
COPY README.md .
COPY config/ ./config/
COPY src/ ./src/
COPY Docs/ ./Docs/

# Install python dependencies and labgate package
RUN pip install --no-cache-dir -e .

# Create storage directory for persistent plans, telemetry, audit logs, and models
RUN mkdir -p labgate_data

EXPOSE 8523

# Liveness healthcheck
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8523/health || exit 1

# Default entrypoint: launch labgate-serve API platform
CMD ["labgate-serve"]
