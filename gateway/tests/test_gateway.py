"""Tests for obs-gateway (self-contained; no collector needed — emit is stubbed)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from obs_gateway import emit, ingest
from obs_gateway.config import settings
from obs_gateway.main import app


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture what would be forwarded to the collector."""
    out: list[dict[str, object]] = []

    def _emit(*, body: str, severity: str, attributes: dict[str, object]) -> None:
        out.append({"body": body, "severity": severity, "attributes": attributes})

    monkeypatch.setattr(emit.emitter, "emit", _emit)
    # ingest.py imported `emitter` by name — patch that reference too.
    monkeypatch.setattr(ingest.emitter, "emit", _emit)
    # isolate rate/dedupe state per test
    ingest._hits.clear()
    ingest._recent.clear()
    return out


def test_health_ok() -> None:
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_status_no_targets() -> None:
    resp = TestClient(app).get("/status")
    assert resp.status_code == 200
    assert resp.json()["services"] == []


def test_ingest_forwards_events(captured: list[dict[str, object]]) -> None:
    payload = {"events": [{"type": "error", "level": "error", "message": "boom", "app": "web"}]}
    resp = TestClient(app).post("/api/telemetry/v1/ingest", json=payload)
    assert resp.status_code == 204
    assert len(captured) == 1
    assert captured[0]["body"] == "boom"
    assert captured[0]["attributes"]["service.name"] == "frontend/web"


def test_ingest_dedupes_identical(captured: list[dict[str, object]]) -> None:
    ev = {"type": "error", "message": "same", "stack": "x"}
    client = TestClient(app)
    client.post("/api/telemetry/v1/ingest", json={"events": [ev, ev]})
    assert len(captured) == 1  # second identical event in-window is dropped


def test_rate_limit(monkeypatch: pytest.MonkeyPatch, captured: list[dict[str, object]]) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)
    client = TestClient(app)
    codes = [
        client.post("/api/telemetry/v1/ingest", json={"events": []}).status_code
        for _ in range(3)
    ]
    assert codes[-1] == 429


def test_oversized_field_rejected() -> None:
    big = "x" * 5000  # message cap is 2000
    resp = TestClient(app).post(
        "/api/telemetry/v1/ingest", json={"events": [{"message": big}]}
    )
    assert resp.status_code == 422
