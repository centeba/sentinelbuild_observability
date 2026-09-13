"""The gateway's own probes and GET /status with no targets."""

from fastapi.testclient import TestClient


def test_own_probes(client: TestClient) -> None:
    for path in ("/health", "/healthz"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "obs-gateway"}


def test_status_no_targets(client: TestClient) -> None:
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json()["services"] == []
