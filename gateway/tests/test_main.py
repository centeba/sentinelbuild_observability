"""App wiring: request-size limit, /metrics, and env configuration parsing."""

import pytest
from fastapi.testclient import TestClient

from obs_gateway.config import Settings, settings

from .conftest import INGEST, Emitted


@pytest.fixture()
def small_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(settings, "max_event_bytes", 100)
    monkeypatch.setattr(settings, "max_batch_events", 10)
    return settings.max_request_bytes  # 1000


def test_body_over_limit_rejected_by_content_length(
    client: TestClient, captured: list[Emitted], small_limit: int
) -> None:
    resp = client.post(INGEST, content=b"x" * (small_limit + 1), headers={"content-type": "application/json"})
    assert resp.status_code == 413
    assert captured == []


def test_chunked_body_over_limit_rejected(
    client: TestClient, captured: list[Emitted], small_limit: int
) -> None:
    def chunks():  # no Content-Length => counted while streaming
        for _ in range(3):
            yield b" " * 600

    resp = client.post(INGEST, content=chunks(), headers={"content-type": "application/json"})
    assert resp.status_code == 413
    assert captured == []


def test_body_at_limit_is_processed(client: TestClient, captured: list[Emitted], small_limit: int) -> None:
    body = b'{"events": []}'
    resp = client.post(INGEST, content=body.ljust(small_limit), headers={"content-type": "application/json"})
    assert resp.status_code == 204


def test_metrics_endpoint(client: TestClient, captured: list[Emitted]) -> None:
    client.post(INGEST, json={"events": [{"message": "counted"}]})
    text = client.get("/metrics").text
    assert 'http_request_duration_seconds_count{method="POST",route="/api/telemetry/v1/ingest",status="204"}' in text
    assert 'obs_ingest_events_total{outcome="accepted"}' in text


def test_unmatched_route_label(client: TestClient) -> None:
    assert client.get("/nope").status_code == 404
    assert 'route="unmatched",status="404"' in client.get("/metrics").text


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_CORS_ALLOW_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("OBS_JWT_ALGORITHMS", '["RS256"]')
    monkeypatch.setenv("OBS_HEALTH_TARGETS", '{"users": "http://users:8000"}')
    monkeypatch.setenv("OBS_TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setenv("OBS_MAX_EVENT_BYTES", "1000")
    monkeypatch.setenv("OBS_MAX_BATCH_EVENTS", "5")
    loaded = Settings()
    assert loaded.cors_allow_origins == ["https://a.example", "https://b.example"]
    assert loaded.jwt_algorithms == ["RS256"]
    assert loaded.health_targets == {"users": "http://users:8000"}
    assert loaded.trusted_proxy_hops == 1
    assert loaded.max_request_bytes == 5000


def test_settings_single_algorithm_plain_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_JWT_ALGORITHMS", "HS256")
    assert Settings().jwt_algorithms == ["HS256"]
