"""Stack health monitor, /status/stack, and the local fallback log files."""

import asyncio
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from obs_gateway import stack
from obs_gateway.config import settings
from obs_gateway.stack import EVENTS_FILE, HEALTH_FILE, FallbackLog, StackMonitor

from .conftest import INGEST, Emitted

Behaviour = dict[str, int | Exception]


@pytest.fixture()
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(settings, "fallback_log_dir", str(tmp_path))
    yield tmp_path
    stack.fallback_log.close()


@pytest.fixture()
def make_monitor(monkeypatch: pytest.MonkeyPatch) -> Callable[[Behaviour], StackMonitor]:
    """A monitor whose components answer with a status code or raise; mutate
    the returned behaviour dict between checks to simulate outages."""

    def _make(behaviour: Behaviour) -> StackMonitor:
        monkeypatch.setattr(settings, "stack_targets", {name: f"http://{name}/health" for name in behaviour})

        def handler(request: httpx.Request) -> httpx.Response:
            outcome = behaviour[request.url.host]
            if isinstance(outcome, Exception):
                raise outcome
            return httpx.Response(outcome)

        return StackMonitor(stack.fallback_log, transport=httpx.MockTransport(handler))

    return _make


@pytest.fixture()
def shared_monitor(monkeypatch: pytest.MonkeyPatch) -> Iterator[StackMonitor]:
    """The app-wide monitor, reset around the test."""
    stack.stack_monitor.components.clear()
    yield stack.stack_monitor
    stack.stack_monitor.components.clear()


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def check(monitor: StackMonitor) -> None:
    asyncio.run(monitor.check_once())


def test_all_healthy_records_nothing(make_monitor: Callable[[Behaviour], StackMonitor], log_dir: Path) -> None:
    monitor = make_monitor({"loki": 200, "grafana": 200})
    check(monitor)
    assert not monitor.fallback_active
    assert monitor.snapshot()["status"] == "ok"
    assert read_jsonl(log_dir / HEALTH_FILE) == []


def test_transitions_are_recorded(make_monitor: Callable[[Behaviour], StackMonitor], log_dir: Path) -> None:
    behaviour: Behaviour = {"loki": 200, "grafana": 200, "tempo": 200}
    monitor = make_monitor(behaviour)
    check(monitor)

    behaviour["grafana"] = httpx.ConnectError("refused")
    behaviour["tempo"] = 503
    check(monitor)
    check(monitor)  # no change -> no new records

    behaviour["grafana"] = 200
    check(monitor)

    records = read_jsonl(log_dir / HEALTH_FILE)
    assert [(r["component"], r["state"], r["detail"]) for r in records] == [
        ("grafana", "unhealthy", "ConnectError"),
        ("tempo", "unhealthy", "HTTP 503"),
        ("grafana", "recovered", "HTTP 200"),
    ]
    assert records[0]["fallback_active"] is True
    assert records[0]["previous_detail"] == "HTTP 200"
    assert records[2]["fallback_active"] is False  # tempo is not a trigger component
    assert all("time" in r for r in records)


def test_unhealthy_at_first_check_is_recorded(
    make_monitor: Callable[[Behaviour], StackMonitor], log_dir: Path
) -> None:
    monitor = make_monitor({"otel-collector": httpx.ReadTimeout("slow"), "loki": 200})
    check(monitor)
    records = read_jsonl(log_dir / HEALTH_FILE)
    assert [(r["component"], r["state"], r["previous_detail"]) for r in records] == [
        ("otel-collector", "unhealthy", None)
    ]
    assert monitor.fallback_active


def test_fallback_trigger_components_are_configurable(
    make_monitor: Callable[[Behaviour], StackMonitor], monkeypatch: pytest.MonkeyPatch
) -> None:
    monitor = make_monitor({"tempo": 500, "grafana": 200})
    check(monitor)
    assert not monitor.fallback_active
    monkeypatch.setattr(settings, "fallback_trigger_components", ["tempo"])
    assert monitor.fallback_active


def test_non_2xx_is_unhealthy(make_monitor: Callable[[Behaviour], StackMonitor]) -> None:
    monitor = make_monitor({"loki": 204, "grafana": 302})
    check(monitor)
    health = {c.component: c.healthy for c in monitor.components.values()}
    assert health == {"loki": True, "grafana": False}


def test_metrics_reflect_health(make_monitor: Callable[[Behaviour], StackMonitor], client: TestClient) -> None:
    check(make_monitor({"loki": 200, "grafana": 500}))
    text = client.get("/metrics").text
    assert 'obs_stack_component_up{component="loki"} 1.0' in text
    assert 'obs_stack_component_up{component="grafana"} 0.0' in text
    assert "obs_fallback_active 1.0" in text


def test_run_survives_check_errors(make_monitor: Callable[[Behaviour], StackMonitor], monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = make_monitor({"loki": 200})
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor, "check_once", flaky)
    monkeypatch.setattr(settings, "stack_check_interval_seconds", 0)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.run())
    assert calls == 2


# ── /status/stack ─────────────────────────────────────────────────────────────


def test_status_stack_unknown_before_first_check(client: TestClient, shared_monitor: StackMonitor) -> None:
    resp = client.get("/status/stack")
    assert resp.status_code == 200
    assert resp.json() == {"status": "unknown", "fallback_active": False, "fallback_log_dir": None, "components": []}


def test_status_stack_degraded(
    client: TestClient, shared_monitor: StackMonitor, make_monitor: Callable[[Behaviour], StackMonitor], log_dir: Path
) -> None:
    probe = make_monitor({"loki": 200, "grafana": httpx.ConnectError("down")})
    check(probe)
    shared_monitor.components.update(probe.components)
    resp = client.get("/status/stack")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["fallback_active"] is True
    assert body["fallback_log_dir"] == str(log_dir)
    assert [(c["component"], c["healthy"], c["detail"]) for c in body["components"]] == [
        ("grafana", False, "ConnectError"),
        ("loki", True, "HTTP 200"),
    ]


def test_status_stack_requires_internal_key(
    client: TestClient, shared_monitor: StackMonitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "internal_api_key", "k")
    assert client.get("/status/stack").status_code == 401
    assert client.get("/status/stack", headers={"x-internal-key": "k"}).status_code == 200


# ── events fallback on ingest ─────────────────────────────────────────────────


def _degrade(shared: StackMonitor, make_monitor: Callable[[Behaviour], StackMonitor], grafana: int | Exception) -> None:
    probe = make_monitor({"grafana": grafana})
    check(probe)
    shared.components.clear()
    shared.components.update(probe.components)


def test_events_written_locally_while_stack_unhealthy(
    client: TestClient,
    captured: list[Emitted],
    shared_monitor: StackMonitor,
    make_monitor: Callable[[Behaviour], StackMonitor],
    log_dir: Path,
) -> None:
    _degrade(shared_monitor, make_monitor, httpx.ConnectError("grafana down"))
    event = {"type": "error", "level": "error", "message": "while down", "app": "web", "trace_id": "a" * 32}
    assert client.post(INGEST, json={"events": [event]}).status_code == 204

    (record,) = read_jsonl(log_dir / EVENTS_FILE)
    assert record["service"] == "frontend/web"
    assert (record["level"], record["body"], record["trace_id"]) == ("error", "while down", "a" * 32)
    assert record["attributes"]["telemetry.type"] == "error"  # type: ignore[index]
    assert "company_id" not in record["attributes"]  # type: ignore[operator]
    assert len(captured) == 1  # still forwarded over OTLP too


def test_events_not_written_while_healthy(
    client: TestClient,
    captured: list[Emitted],
    shared_monitor: StackMonitor,
    make_monitor: Callable[[Behaviour], StackMonitor],
    log_dir: Path,
) -> None:
    _degrade(shared_monitor, make_monitor, 200)
    assert client.post(INGEST, json={"events": [{"message": "all good"}]}).status_code == 204
    assert not (log_dir / EVENTS_FILE).exists()


def test_no_files_when_log_dir_unset(
    client: TestClient,
    captured: list[Emitted],
    shared_monitor: StackMonitor,
    make_monitor: Callable[[Behaviour], StackMonitor],
    tmp_path: Path,
) -> None:
    _degrade(shared_monitor, make_monitor, 500)
    assert client.post(INGEST, json={"events": [{"message": "x"}]}).status_code == 204
    assert list(tmp_path.iterdir()) == []


# ── file handling ─────────────────────────────────────────────────────────────


def test_fallback_log_rotates(log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "fallback_log_max_bytes", 300)
    monkeypatch.setattr(settings, "fallback_log_backup_count", 2)
    log = FallbackLog()
    for i in range(20):
        log.write_event({"body": f"event {i}", "padding": "x" * 50})
    log.close()
    names = sorted(p.name for p in log_dir.iterdir())
    assert names == [EVENTS_FILE, f"{EVENTS_FILE}.1", f"{EVENTS_FILE}.2"]
    assert read_jsonl(log_dir / EVENTS_FILE)[-1]["body"] == "event 19"


def test_unwritable_log_dir_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file in the way")
    monkeypatch.setattr(settings, "fallback_log_dir", str(blocker / "logs"))
    log = FallbackLog()
    log.write_health({"component": "loki"})  # must not raise
    assert "fallback_log_unavailable" in caplog.text
