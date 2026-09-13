"""obs-gateway — the observability platform's ingest + health-aggregation API.

Self-contained FastAPI service:
- ``POST /api/telemetry/v1/ingest`` — frontend/app RUM, errors, and logs, enriched
  (tenant/user from an optional JWT) and forwarded to the collector as OTLP.
- ``GET /status`` — fleet health across configured services.
- ``GET /health`` (liveness) / ``GET /healthz`` (readiness) — the gateway's own.

No host-platform imports; everything is driven by ``OBS_*`` env vars.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings
from .emit import emitter
from .health import router as health_router
from .ingest import router as ingest_router

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


class BodySizeLimit:
    """Reject request bodies over ``settings.max_request_bytes`` with 413.

    Checks Content-Length up front and also counts streamed (chunked) bytes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = settings.max_request_bytes
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            response = JSONResponse({"detail": "request body too large"}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status_code=413, detail="request body too large")
            return message

        await self.app(scope, limited_receive, send)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    # Pre-existing bug fixed: providers were never shut down, so records still
    # batched at SIGTERM were lost.
    emitter.shutdown()


app = FastAPI(title="obs-gateway", version="0.2.0", lifespan=lifespan)

# Last added is outermost: CORS -> body-size limit -> routes.
app.add_middleware(BodySizeLimit)
if settings.cors_allow_origins:
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
