"""Frontend telemetry ingest: validate -> enrich -> forward as OTLP logs.

Public-friendly (crashes happen pre-login) but hardened: strict Pydantic caps on
every field and on the whole event, a batch-size cap, per-IP rate limiting, and
short-window dedupe so a client error loop can't flood the pipeline. Never
fails the caller because of the downstream pipeline — telemetry is
fire-and-forget.
"""

import hashlib
import time
from collections import defaultdict, deque
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .auth import principal_from_bearer
from .config import settings
from .emit import SEVERITY, emitter

router = APIRouter(prefix="/api/telemetry/v1", tags=["telemetry"])

EventType = Literal["log", "error", "event", "perf"]
ContextKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]
ContextValue = Annotated[str, StringConstraints(max_length=1024)]

MAX_CONTEXT_ENTRIES = 32


def _hex_id(value: object, length: int) -> object:
    if not isinstance(value, str):
        return value
    v = value.strip().lower()
    if len(v) != length or any(c not in "0123456789abcdef" for c in v) or set(v) == {"0"}:
        raise ValueError(f"must be {length} hex characters and not all zeros")
    return v


class TelemetryEvent(BaseModel):
    type: EventType = "log"
    level: str = "info"
    message: str = Field(default="", max_length=2000)
    error: str | None = Field(default=None, max_length=2000)
    stack: str | None = Field(default=None, max_length=8000)
    url: str | None = Field(default=None, max_length=2000)
    app: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    app_version: str | None = Field(default=None, max_length=64)
    # W3C trace context of the backend request this event relates to, so the
    # frontend log links to the backend trace in Grafana.
    trace_id: str | None = None
    span_id: str | None = None
    context: dict[ContextKey, ContextValue] = Field(
        default_factory=dict, max_length=MAX_CONTEXT_ENTRIES
    )

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_level(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip().lower()
            if v not in SEVERITY:
                raise ValueError(f"level must be one of {', '.join(SEVERITY)}")
        return v

    @field_validator("trace_id", mode="before")
    @classmethod
    def _trace_id(cls, v: object) -> object:
        return _hex_id(v, 32)

    @field_validator("span_id", mode="before")
    @classmethod
    def _span_id(cls, v: object) -> object:
        return _hex_id(v, 16)

    @model_validator(mode="after")
    def _event_size(self) -> "TelemetryEvent":
        size = len(self.model_dump_json(exclude_defaults=True).encode())
        if size > settings.max_event_bytes:
            raise ValueError(f"event is {size} bytes; limit is {settings.max_event_bytes}")
        return self


class TelemetryBatch(BaseModel):
    events: list[TelemetryEvent] = Field(default_factory=list)

    @field_validator("events")
    @classmethod
    def _batch_size(cls, v: list[TelemetryEvent]) -> list[TelemetryEvent]:
        # Pre-existing bug fixed: events past the limit were silently dropped
        # while the caller got 204; an oversized batch is now rejected.
        if len(v) > settings.max_batch_events:
            raise ValueError(f"at most {settings.max_batch_events} events per batch")
        return v


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


@router.post(
    "/ingest",
    status_code=204,
    responses={
        413: {"description": "Request body too large"},
        422: {"description": "Invalid event or batch; the whole batch is rejected"},
        429: {"description": "Rate limited"},
    },
)
async def ingest(
    batch: TelemetryBatch,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    """Accept a batch of client telemetry events."""
    ip = _client_ip(request)
    if _rate_limited(ip):
        return Response(status_code=429)

    principal = principal_from_bearer(authorization)
    for event in batch.events:
        body = event.message or event.error or event.type
        sig = hashlib.sha256(f"{event.type}|{body}|{event.stack or ''}".encode()).hexdigest()
        if _is_duplicate(sig):
            continue
        attributes: dict[str, str | None] = {
            "telemetry.type": event.type,
            "level": event.level,
            "app": event.app,
            "app.version": event.app_version,
            "url": event.url,
            "company_id": principal.company_id,
            "user_id": principal.user_id,
            "error": event.error,
            "stack": event.stack,
            **{f"ctx.{k}": v for k, v in event.context.items()},
        }
        emitter.emit(
            app=event.app,
            body=body,
            level=event.level,
            trace_id=event.trace_id,
            span_id=event.span_id,
            attributes=attributes,
        )
    return Response(status_code=204)
