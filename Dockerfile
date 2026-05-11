FROM python:3.11-slim AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ---- deps layer (cached unless pyproject/lockfile changes) ----
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime ----
FROM base AS runtime

# Copy venv from deps stage
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY app/ ./app/

# Non-root user for security
RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /app/data && chown appuser /app/data
USER appuser

# Cloud Run injects PORT=8080; HF Spaces uses 7860. Default covers both.
EXPOSE 8080

# Cloud Run ignores HEALTHCHECK; kept for local/HF Spaces use.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
