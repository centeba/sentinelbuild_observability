"""End-to-end smoke test against a running stack (see docker-compose.smoke.yml).

Checks the acceptance criteria that need real components: OTLP traces and logs
land in Tempo/Loki and share a trace_id, frontend ingest reaches Loki enriched
with tenant and linked to the backend trace, Grafana is provisioned, Prometheus
scrapes the gateway, /status degrades on an unreachable target, and the ingest
limits answer 413/422/429.

Exit code 0 when every check passes. Prints one PASS/FAIL line per check.
"""

import os
import secrets
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

GATEWAY = os.environ.get("SMOKE_GATEWAY", "http://obs-gateway:8080")
COLLECTOR = os.environ.get("SMOKE_COLLECTOR", "http://otel-collector:4318")
LOKI = os.environ.get("SMOKE_LOKI", "http://loki:3100")
TEMPO = os.environ.get("SMOKE_TEMPO", "http://tempo:3200")
PROMETHEUS = os.environ.get("SMOKE_PROMETHEUS", "http://prometheus:9090")
GRAFANA = os.environ.get("SMOKE_GRAFANA", "http://grafana:3000")
GRAFANA_AUTH = (os.environ.get("GRAFANA_ADMIN_USER", "admin"), os.environ.get("GRAFANA_ADMIN_PASSWORD", "admin"))
JWT_SECRET = os.environ["SMOKE_JWT_SECRET"]
INTERNAL_KEY = os.environ["SMOKE_INTERNAL_KEY"]

RUN_ID = secrets.token_hex(6)
failures: list[str] = []
http = httpx.Client(timeout=10)


def check(name: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:
        failures.append(name)
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)
    else:
        print(f"PASS  {name}", flush=True)


def eventually(fn: Callable[[], Any], timeout: float = 90, interval: float = 3) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return fn()
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(interval)


def loki_streams(query: str) -> list[dict[str, Any]]:
    now = time.time_ns()
    resp = http.get(
        f"{LOKI}/loki/api/v1/query_range",
        params={"query": query, "start": now - 15 * 60 * 10**9, "end": now + 60 * 10**9, "limit": 50},
    )
    resp.raise_for_status()
    streams: list[dict[str, Any]] = resp.json()["data"]["result"]
    assert streams, f"no log lines yet for {query}"
    return streams


# ── backend service: a trace plus a log line emitted inside its span ─────────
def emit_backend_signals() -> str:
    resource = Resource.create({"service.name": "smoke-backend", "deployment.environment": "smoke"})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{COLLECTOR}/v1/traces")))
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(OTLPLogExporter(endpoint=f"{COLLECTOR}/v1/logs")))

    tracer = tracer_provider.get_tracer("smoke")
    with tracer.start_as_current_span("GET /smoke") as parent, tracer.start_as_current_span("db.query"):
        logger_provider.get_logger("smoke").emit(
            severity_number=SeverityNumber.ERROR,
            severity_text="ERROR",
            body=f"backend failure {RUN_ID}",
            attributes={"run_id": RUN_ID},
        )
        trace_id = format(parent.get_span_context().trace_id, "032x")
    tracer_provider.shutdown()
    logger_provider.shutdown()
    return trace_id


def main() -> int:
    eventually(lambda: http.get(f"{GATEWAY}/health").raise_for_status(), timeout=120)
    print(f"run id {RUN_ID}", flush=True)
    trace_id = emit_backend_signals()
    span_id = secrets.token_hex(8)
    token = jwt.encode({"org_id": "acme", "sub": "smoke-user", "exp": int(time.time()) + 600}, JWT_SECRET, algorithm="HS256")

    def ingest_anonymous() -> None:
        resp = http.post(
            f"{GATEWAY}/api/telemetry/v1/ingest",
            json={"events": [{"type": "error", "level": "error", "message": f"frontend anon {RUN_ID}", "app": "smoke-web",
                              "trace_id": trace_id, "span_id": span_id}]},
        )
        assert resp.status_code == 204, resp.status_code

    def ingest_authenticated() -> None:
        resp = http.post(
            f"{GATEWAY}/api/telemetry/v1/ingest",
            headers={"authorization": f"Bearer {token}"},
            json={"events": [{"type": "log", "level": "warning", "message": f"frontend tenant {RUN_ID}", "app": "smoke-web"}]},
        )
        assert resp.status_code == 204, resp.status_code

    check("AC-3 ingest anonymous event -> 204", ingest_anonymous)
    check("AC-3 ingest authenticated event -> 204", ingest_authenticated)

    def backend_trace_in_tempo() -> None:
        def get() -> None:
            resp = http.get(f"{TEMPO}/api/traces/{trace_id}")
            assert resp.status_code == 200, resp.status_code

        eventually(get)

    def backend_log_linked_to_trace() -> None:
        streams = eventually(lambda: loki_streams(f'{{service_name="smoke-backend"}} |= "{RUN_ID}"'))
        labels = streams[0]["stream"]
        assert labels.get("trace_id") == trace_id, labels

    def frontend_anon_in_loki() -> None:
        streams = eventually(lambda: loki_streams(f'{{service_name="frontend/smoke-web"}} |= "frontend anon {RUN_ID}"'))
        labels = streams[0]["stream"]
        assert labels.get("trace_id") == trace_id, labels
        assert "company_id" not in labels, labels

    def frontend_tenant_in_loki() -> None:
        streams = eventually(lambda: loki_streams(f'{{service_name="frontend/smoke-web"}} |= "frontend tenant {RUN_ID}"'))
        labels = streams[0]["stream"]
        assert labels.get("company_id") == "acme", labels
        assert labels.get("user_id") == "smoke-user", labels

    check("AC-2 backend trace stored in Tempo", backend_trace_in_tempo)
    check("AC-2 backend log in Loki carries the trace_id", backend_log_linked_to_trace)
    check("FR-8 frontend event in Loki under frontend/<app>, linked to backend trace", frontend_anon_in_loki)
    check("AC-3 authenticated frontend event tagged with company_id/user_id", frontend_tenant_in_loki)

    def grafana_provisioned() -> None:
        resp = http.get(f"{GRAFANA}/api/datasources", auth=GRAFANA_AUTH)
        resp.raise_for_status()
        uids = {d["uid"] for d in resp.json()}
        assert {"obs-prometheus", "obs-loki", "obs-tempo"} <= uids, uids
        http.get(f"{GRAFANA}/api/dashboards/uid/obs-overview", auth=GRAFANA_AUTH).raise_for_status()

    def prometheus_scrapes_gateway() -> None:
        def get() -> None:
            resp = http.get(f"{PROMETHEUS}/api/v1/query", params={"query": 'sum(obs_ingest_events_total{job="obs-gateway",outcome="accepted"})'})
            result = resp.json()["data"]["result"]
            assert result and float(result[0]["value"][1]) >= 2, result

        eventually(get)

    def tempo_span_metrics() -> None:
        def get() -> None:
            resp = http.get(f"{PROMETHEUS}/api/v1/query", params={"query": 'traces_spanmetrics_calls_total{service="smoke-backend"}'})
            assert resp.json()["data"]["result"], "no span metrics yet"

        eventually(get, timeout=120)

    check("AC-1 Grafana datasources + overview dashboard provisioned", grafana_provisioned)
    check("NFR-7 Prometheus scrapes gateway metrics", prometheus_scrapes_gateway)
    check("FR-7.2 Tempo metrics-generator writes span metrics", tempo_span_metrics)

    def status_requires_key() -> None:
        assert http.get(f"{GATEWAY}/status").status_code == 401

    def status_degraded() -> None:
        resp = http.get(f"{GATEWAY}/status", headers={"x-internal-key": INTERNAL_KEY})
        assert resp.status_code == 503, resp.status_code
        body = resp.json()
        by_name = {s["service"]: s["ok"] for s in body["services"]}
        assert body["status"] == "degraded" and by_name == {"gateway": True, "unreachable": False}, body

    check("NFR-3 /status requires the internal key", status_requires_key)
    check("AC-6 /status reports an unreachable dependency as degraded (503)", status_degraded)

    def oversized_422() -> None:
        resp = http.post(f"{GATEWAY}/api/telemetry/v1/ingest", json={"events": [{"message": "x" * 2001}]})
        assert resp.status_code == 422, resp.status_code

    def batch_422() -> None:
        resp = http.post(f"{GATEWAY}/api/telemetry/v1/ingest", json={"events": [{"message": str(i)} for i in range(101)]})
        assert resp.status_code == 422, resp.status_code

    def body_413() -> None:
        resp = http.post(f"{GATEWAY}/api/telemetry/v1/ingest", content=b" " * (100 * 16384 + 1), headers={"content-type": "application/json"})
        assert resp.status_code == 413, resp.status_code

    def flood_429() -> None:  # last: leaves this client rate limited for a minute
        codes = [http.post(f"{GATEWAY}/api/telemetry/v1/ingest", json={"events": []}).status_code for _ in range(int(os.environ.get("SMOKE_RATE_LIMIT", "60")) + 5)]
        assert 429 in codes, sorted(set(codes))

    check("AC-4 oversized field -> 422", oversized_422)
    check("FR-4.4 batch over 100 events -> 422", batch_422)
    check("FR-4.1 request body over limit -> 413", body_413)
    check("AC-5 request flood -> 429", flood_429)

    print(f"\n{'FAILED' if failures else 'OK'}: {len(failures)} failing check(s)", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
