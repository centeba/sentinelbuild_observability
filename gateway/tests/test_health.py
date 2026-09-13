"""GET /status fleet aggregation, the internal-key guard, and own probes."""

from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from obs_gateway import health
from obs_gateway.config import settings


@pytest.fixture()
def fleet(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, int | Exception]], None]:
    """Configure targets whose /healthz answers with a status code or raises."""

    def _configure(behaviour: dict[str, int | Exception]) -> None:
        monkeypatch.setattr(
            settings, "health_targets", {name: f"http://{name}:8000/" for name in behaviour}
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/healthz"
            outcome = behaviour[request.url.host]
            if isinstance(outcome, Exception):
                raise outcome
            return httpx.Response(outcome)

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(health.httpx, "AsyncClient", lambda: real_client(transport=transport))

    return _configure


def test_own_probes(client: TestClient) -> None:
    for path in ("/health", "/healthz"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "obs-gateway"}


def test_status_no_targets(client: TestClient) -> None:
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "healthy": 0,
        "total": 0,
        "services": [],
        "note": "no targets configured",
    }


def test_status_all_healthy(client: TestClient, fleet: Callable[[dict[str, int | Exception]], None]) -> None:
    fleet({"users": 200, "billing": 200})
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["status"], body["healthy"], body["total"]) == ("ok", 2, 2)
    assert [s["service"] for s in body["services"]] == ["billing", "users"]


def test_status_degraded(client: TestClient, fleet: Callable[[dict[str, int | Exception]], None]) -> None:
    fleet({"users": 200, "billing": 503, "search": httpx.ConnectError("refused")})
    resp = client.get("/status")
    assert resp.status_code == 503
    body = resp.json()
    assert (body["status"], body["healthy"], body["total"]) == ("degraded", 1, 3)
    assert body["services"] == [
        {"service": "billing", "ok": False, "status_code": 503},
        {"service": "search", "ok": False, "error": "ConnectError"},
        {"service": "users", "ok": True, "status_code": 200},
    ]


def test_status_timeout_is_unhealthy(
    client: TestClient, fleet: Callable[[dict[str, int | Exception]], None]
) -> None:
    fleet({"slow": httpx.ReadTimeout("timed out")})
    body = client.get("/status").json()
    assert body["services"] == [{"service": "slow", "ok": False, "error": "ReadTimeout"}]


def test_status_requires_internal_key_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "internal_api_key", "k-123")
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"x-internal-key": "wrong"}).status_code == 401
    assert client.get("/status", headers={"x-internal-key": "k-123"}).status_code == 200
    assert client.get("/health").status_code == 200  # probes stay open
