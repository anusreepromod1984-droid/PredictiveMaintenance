# Siemens / Bosch Rexroth Industrial Production Container
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Copy source code and catalogs
COPY src/ ./src/
COPY public/ ./public/
COPY data/ ./data/

# Expose FastAPI / proxy ports
EXPOSE 8000
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8000}/health || exit 0

# Launch production server via robust launcher
CMD ["python", "-m", "src.launcher"]
