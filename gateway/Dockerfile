# obs-gateway — self-contained image. Digest-pin the base for reproducible,
# supply-chain-safe builds; runs as a non-root user.
#
#   docker build -t obs-gateway gateway/                 # runtime (default, last stage)
#   docker build --target test -t obs-gateway-test gateway/ && docker run --rm obs-gateway-test
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install .

# ── quality gates: ruff + mypy --strict + pytest (coverage floor in pyproject) ──
FROM base AS test
RUN pip install ".[dev]"
COPY tests/ ./tests/
CMD ["sh", "-c", "ruff check src tests && mypy src && pytest"]

# ── runtime ───────────────────────────────────────────────────────────────────
FROM base AS runtime

# SEC: drop root.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Container-level liveness for runtimes honouring HEALTHCHECK (no curl in slim).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8080')+'/health', timeout=4).status==200 else 1)"

# Honour Railway/K8s injected $PORT (fallback 8080 for local/compose). exec so
# uvicorn is PID 1 and receives SIGTERM (graceful shutdown flushes telemetry).
CMD ["sh", "-c", "exec uvicorn obs_gateway.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
