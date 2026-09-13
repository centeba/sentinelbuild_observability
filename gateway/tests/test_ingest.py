"""POST /api/telemetry/v1/ingest — validation, enrichment, dedupe, rate limit."""

import pytest
from fastapi.testclient import TestClient

from obs_gateway.config import settings

from .conftest import INGEST, Emitted, make_token


def post(client: TestClient, *events: dict[str, object], **kwargs: object) -> int:
    resp = client.post(INGEST, json={"events": list(events)}, **kwargs)  # type: ignore[arg-type]
    return resp.status_code


# ── forwarding ────────────────────────────────────────────────────────────────


def test_forwards_event_with_attributes(client: TestClient, captured: list[Emitted]) -> None:
    event = {
        "type": "error",
        "level": "error",
        "message": "boom",
        "error": "TypeError: x",
        "stack": "at a()",
        "url": "https://app/x",
        "app": "web",
        "app_version": "1.2.3",
        "context": {"route": "/jobs/42"},
    }
    assert post(client, event) == 204
    assert len(captured) == 1
    got = captured[0]
    assert (got.app, got.body, got.level) == ("web", "boom", "error")
    assert got.attributes["telemetry.type"] == "error"
    assert got.attributes["app.version"] == "1.2.3"
    assert got.attributes["stack"] == "at a()"
    assert got.attributes["ctx.route"] == "/jobs/42"
    assert got.attributes["company_id"] is None


def test_defaults_and_body_fallback(client: TestClient, captured: list[Emitted]) -> None:
    assert post(client, {}, {"type": "error", "error": "only-error"}) == 204
    assert [(e.body, e.level) for e in captured] == [("log", "info"), ("only-error", "info")]


def test_empty_batch_is_accepted(client: TestClient, captured: list[Emitted]) -> None:
    assert post(client) == 204
    assert captured == []


@pytest.mark.parametrize(
    ("sent", "normalized"),
    [("WARN", "warn"), (" Error ", "error"), ("critical", "critical"), ("trace", "trace")],
)
def test_level_is_normalized(
    client: TestClient, captured: list[Emitted], sent: str, normalized: str
) -> None:
    assert post(client, {"level": sent, "message": sent}) == 204
    assert captured[0].level == normalized


# ── validation (whole batch rejected with 422) ───────────────────────────────


@pytest.mark.parametrize(
    "event",
    [
        {"level": "verbose"},
        {"type": "metric"},
        {"message": "x" * 2001},
        {"error": "x" * 2001},
        {"stack": "x" * 8001},
        {"url": "x" * 2001},
        {"app": "x" * 65},
        {"app": "web app!"},
        {"app_version": "x" * 65},
        {"context": {f"k{i}": "v" for i in range(33)}},
        {"context": {"k" * 129: "v"}},
        {"context": {"": "v"}},
        {"context": {"k": "v" * 1025}},
        {"context": {"k": 1}},
        {"trace_id": "abc"},
        {"trace_id": "0" * 32},
        {"trace_id": "g" * 32},
        {"span_id": "abc"},
        {"span_id": "0" * 16},
    ],
    ids=lambda e: next(iter(e)),
)
def test_invalid_event_rejected(client: TestClient, captured: list[Emitted], event: dict[str, object]) -> None:
    assert post(client, {"message": "valid"}, event) == 422
    assert captured == []


def test_event_byte_cap(client: TestClient, captured: list[Emitted], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_event_bytes", 500)
    assert post(client, {"message": "x" * 400}) == 204
    assert post(client, {"message": "y" * 400, "context": {"k": "v" * 200}}) == 422
    assert len(captured) == 1


def test_event_byte_cap_counts_utf8_bytes(
    client: TestClient, captured: list[Emitted], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_event_bytes", 500)
    assert post(client, {"message": "é" * 300}) == 422  # 300 chars, 600 bytes


def test_batch_over_limit_rejected(
    client: TestClient, captured: list[Emitted], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_batch_events", 3)
    assert post(client, *({"message": f"m{i}"} for i in range(3))) == 204
    assert post(client, *({"message": f"n{i}"} for i in range(4))) == 422
    assert len(captured) == 3


# ── trace correlation ─────────────────────────────────────────────────────────


def test_trace_context_forwarded_lowercase(client: TestClient, captured: list[Emitted]) -> None:
    trace_id = "4BF92F3577B34DA6A3CE929D0E0E4736"
    assert post(client, {"message": "m", "trace_id": trace_id, "span_id": "00F067AA0BA902B7"}) == 204
    assert captured[0].trace_id == trace_id.lower()
    assert captured[0].span_id == "00f067aa0ba902b7"


# ── dedupe ────────────────────────────────────────────────────────────────────


def test_dedupes_identical_in_window(client: TestClient, captured: list[Emitted]) -> None:
    ev = {"type": "error", "message": "same", "stack": "x"}
    assert post(client, ev, ev) == 204
    assert post(client, ev) == 204
    assert len(captured) == 1


def test_distinct_stack_is_not_duplicate(client: TestClient, captured: list[Emitted]) -> None:
    assert post(client, {"message": "same", "stack": "a"}, {"message": "same", "stack": "b"}) == 204
    assert len(captured) == 2


def test_dedupe_window_expires(
    client: TestClient, captured: list[Emitted], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "dedupe_window_seconds", 0.0)
    ev = {"message": "same"}
    assert post(client, ev, ev) == 204
    assert len(captured) == 2


# ── rate limit ────────────────────────────────────────────────────────────────


def test_rate_limit(client: TestClient, captured: list[Emitted], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)
    assert [post(client) for _ in range(3)] == [204, 204, 429]


def test_rate_limited_batch_is_not_forwarded(
    client: TestClient, captured: list[Emitted], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_min", 1)
    assert post(client, {"message": "a"}) == 204
    assert post(client, {"message": "b"}) == 429
    assert [e.body for e in captured] == ["a"]


# ── enrichment ────────────────────────────────────────────────────────────────


def test_enriches_tenant_and_user_from_jwt(
    client: TestClient, captured: list[Emitted], jwt_secret: str
) -> None:
    token = make_token({"org_id": "acme", "sub": "user-1"})
    assert post(client, {"message": "m"}, headers={"authorization": f"Bearer {token}"}) == 204
    assert captured[0].attributes["company_id"] == "acme"
    assert captured[0].attributes["user_id"] == "user-1"


def test_invalid_token_is_accepted_anonymously(
    client: TestClient, captured: list[Emitted], jwt_secret: str
) -> None:
    expired = make_token({"org_id": "acme"}, ttl=-60)
    forged = make_token({"org_id": "acme"}, secret="another-secret-that-is-at-least-32-bytes")
    for name, token in (("expired", expired), ("forged", forged), ("garbage", "not-a-jwt")):
        assert post(client, {"message": name}, headers={"authorization": f"Bearer {token}"}) == 204
    assert len(captured) == 3
    assert all(e.attributes["company_id"] is None for e in captured)


def test_token_ignored_when_verification_not_configured(
    client: TestClient, captured: list[Emitted]
) -> None:
    token = make_token({"org_id": "acme"})
    assert post(client, {"message": "m"}, headers={"authorization": f"Bearer {token}"}) == 204
    assert captured[0].attributes["company_id"] is None
