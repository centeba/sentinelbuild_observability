"""Shared fixtures. No collector or network needed: emit is captured in memory."""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import jwt
import pytest
from fastapi.testclient import TestClient

from obs_gateway import emit, ingest
from obs_gateway.config import settings
from obs_gateway.main import app

JWT_SECRET = "test-secret-that-is-at-least-32-bytes-long"
INGEST = "/api/telemetry/v1/ingest"


@dataclass
class Emitted:
    app: str | None
    body: str
    level: str
    trace_id: str | None
    span_id: str | None
    attributes: dict[str, str | None] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    """Rate-limit and dedupe state is module-global; reset around every test."""
    ingest._hits.clear()
    ingest._recent.clear()
    yield
    ingest._hits.clear()
    ingest._recent.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> list[Emitted]:
    """Capture what would be forwarded to the collector."""
    out: list[Emitted] = []

    def _emit(
        *,
        app: str | None,
        body: str,
        level: str,
        trace_id: str | None,
        span_id: str | None,
        attributes: dict[str, str | None],
    ) -> None:
        out.append(Emitted(app, body, level, trace_id, span_id, attributes))

    monkeypatch.setattr(emit.emitter, "emit", _emit)
    return out


@pytest.fixture()
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "jwt_secret", JWT_SECRET)
    monkeypatch.setattr(settings, "jwt_algorithms", ["HS256"])
    return JWT_SECRET


def make_token(claims: dict[str, object], secret: str = JWT_SECRET, ttl: int = 300) -> str:
    return jwt.encode({"exp": int(time.time()) + ttl, **claims}, secret, algorithm="HS256")
