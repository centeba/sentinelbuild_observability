"""Frontend telemetry ingest: validate -> enrich -> forward as OTLP logs.

Public-friendly (crashes happen pre-login) but hardened: strict Pydantic size
caps, per-IP rate limiting, and short-window dedupe so a client error loop can't
flood the pipeline. Never fails the caller — telemetry is fire-and-forget.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import principal_from_bearer
from .config import settings
from .emit import emitter

router = APIRouter(prefix="/api/telemetry/v1", tags=["telemetry"])

EventType = Literal["log", "error", "event", "perf"]
LEVELS = ("debug", "info", "warn", "warning", "error", "fatal", "critical")


class TelemetryEvent(BaseModel):
    type: EventType = "log"
    level: str = Field(default="info", max_length=16)
    message: str = Field(default="", max_length=2000)
    error: str | None = Field(default=None, max_length=2000)
    stack: str | None = Field(default=None, max_length=8000)
    url: str | None = Field(default=None, max_length=2000)
    app: str | None = Field(default=None, max_length=64)
    app_version: str | None = Field(default=None, max_length=64)
    # Free-form small context; values are stringified downstream.
    context: dict[str, str] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    events: list[TelemetryEvent] = Field(default_factory=list)


# ── per-IP fixed-window rate limit + short-window dedupe (in-memory) ──────────
_hits: dict[str, deque[float]] = defaultdict(deque)
_recent: dict[str, float] = {}


def _rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    window = _hits[client_ip]
    while window and now - window[0] > 60.0:
        window.popleft()
    if len(window) >= settings.rate_limit_per_min:
        return True
    window.append(now)
    return False


def _is_duplicate(signature: str) -> bool:
    now = time.monotonic()
    # opportunistic cleanup so the map can't grow unbounded
    if len(_recent) > 4096:
        cutoff = now - settings.dedupe_window_seconds
        for k in [k for k, t in _recent.items() if t < cutoff]:
            _recent.pop(k, None)
    last = _recent.get(signature)
    _recent[signature] = now
    return last is not None and (now - last) < settings.dedupe_window_seconds


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/ingest", status_code=204)
async def ingest(
    batch: TelemetryBatch,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    """Accept a batch of client telemetry events. Always returns 204."""
    ip = _client_ip(request)
    if _rate_limited(ip):
        return Response(status_code=429)

    principal = principal_from_bearer(authorization)
    for event in batch.events[: settings.max_batch_events]:
        body = event.message or event.error or event.type
        sig = hashlib.sha256(
            f"{event.type}|{body}|{event.stack or ''}".encode()
        ).hexdigest()
        if _is_duplicate(sig):
            continue
        attributes = {
            "telemetry.type": event.type,
            "service.name": f"frontend/{event.app}" if event.app else "frontend",
            "app.version": event.app_version,
            "url": event.url,
            "company_id": principal.company_id,
            "user_id": principal.user_id,
            "error": event.error,
            "stack": event.stack,
            **{f"ctx.{k}": v for k, v in list(event.context.items())[:32]},
        }
        emitter.emit(body=body, severity=event.level, attributes=attributes)
    return Response(status_code=204)
