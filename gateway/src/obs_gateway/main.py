"""obs-gateway — the observability platform's ingest + health-aggregation API.

Self-contained FastAPI service:
- ``POST /api/telemetry/v1/ingest`` — frontend/app RUM, errors, and logs, enriched
  (tenant/user from an optional JWT) and forwarded to the collector as OTLP.
- ``GET /status`` — fleet health across configured services.
- ``GET /health`` (liveness) / ``GET /healthz`` (readiness) — the gateway's own.

No host-platform imports; everything is driven by ``OBS_*`` env vars.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import settings
from .health import router as health_router
from .ingest import router as ingest_router

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

app = FastAPI(title="obs-gateway", version="0.1.0")

if settings.cors_allow_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,  # never wildcard-credentials; ingest is tokened or anon
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["*"],
    )

app.include_router(ingest_router)
app.include_router(health_router)


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Shallow liveness — no dependencies."""
    return JSONResponse({"status": "ok", "service": settings.service_name})


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Readiness. The gateway is stateless (forwards OTLP, fans out health), so
    readiness == liveness; kept distinct for probe-convention parity."""
    return JSONResponse({"status": "ok", "service": settings.service_name})
