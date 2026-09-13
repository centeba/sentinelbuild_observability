"""The OTLP emitter, exercised against the real OTEL SDK with an in-memory exporter."""

import logging

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
from opentelemetry.sdk._logs import LogRecordProcessor, ReadableLogRecord
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

from obs_gateway import emit
from obs_gateway.config import settings
from obs_gateway.emit import Emitter

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN_ID = "00f067aa0ba902b7"


@pytest.fixture()
def exporter() -> InMemoryLogRecordExporter:
    return InMemoryLogRecordExporter()


@pytest.fixture()
def emitter(exporter: InMemoryLogRecordExporter) -> Emitter:
    return Emitter(processor_factory=lambda: SimpleLogRecordProcessor(exporter))


def _emit(emitter: Emitter, app: str | None = "web", level: str = "error", **kw: object) -> None:
    emitter.emit(
        app=app,
        body=str(kw.get("body", "boom")),
        level=level,
        trace_id=kw.get("trace_id"),  # type: ignore[arg-type]
        span_id=kw.get("span_id"),  # type: ignore[arg-type]
        attributes=kw.get("attributes", {"company_id": "acme", "user_id": None}),  # type: ignore[arg-type]
    )


def _records(exporter: InMemoryLogRecordExporter) -> list[ReadableLogRecord]:
    return list(exporter.get_finished_logs())


def test_emits_log_record(emitter: Emitter, exporter: InMemoryLogRecordExporter) -> None:
    _emit(emitter, trace_id=TRACE_ID, span_id=SPAN_ID)
    (record,) = _records(exporter)
    lr = record.log_record
    assert lr.body == "boom"
    assert lr.severity_number == SeverityNumber.ERROR
    assert lr.severity_text == "ERROR"
    assert lr.trace_id == int(TRACE_ID, 16)
    assert lr.span_id == int(SPAN_ID, 16)
    assert dict(lr.attributes or {}) == {"company_id": "acme"}  # None dropped
    assert record.resource.attributes["service.name"] == "frontend/web"
    assert record.resource.attributes["deployment.environment"] == settings.environment


def test_record_encodes_as_otlp(emitter: Emitter, exporter: InMemoryLogRecordExporter) -> None:
    """The OTLP/protobuf encoder used by the HTTP exporter must accept the record."""
    _emit(emitter, trace_id=TRACE_ID, span_id=SPAN_ID)
    encoded = encode_logs(_records(exporter))
    log = encoded.resource_logs[0].scope_logs[0].log_records[0]
    assert log.severity_number == 17
    assert log.trace_id.hex() == TRACE_ID


def test_no_trace_context(emitter: Emitter, exporter: InMemoryLogRecordExporter) -> None:
    _emit(emitter)
    (record,) = _records(exporter)
    assert not record.log_record.trace_id


@pytest.mark.parametrize(
    ("level", "number", "text"),
    [
        ("trace", SeverityNumber.TRACE, "TRACE"),
        ("debug", SeverityNumber.DEBUG, "DEBUG"),
        ("info", SeverityNumber.INFO, "INFO"),
        ("warn", SeverityNumber.WARN, "WARNING"),
        ("warning", SeverityNumber.WARN, "WARNING"),
        ("fatal", SeverityNumber.FATAL, "CRITICAL"),
        ("critical", SeverityNumber.FATAL, "CRITICAL"),
    ],
)
def test_severity_mapping(
    emitter: Emitter, exporter: InMemoryLogRecordExporter, level: str, number: SeverityNumber, text: str
) -> None:
    _emit(emitter, level=level)
    (record,) = _records(exporter)
    assert (record.log_record.severity_number, record.log_record.severity_text) == (number, text)


def test_service_name_per_app_and_fallback(
    emitter: Emitter, exporter: InMemoryLogRecordExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(emit, "MAX_APP_SERVICES", 2)
    for app in (None, "web", "mobile", "web"):
        _emit(emitter, app=app)
    names = [r.resource.attributes["service.name"] for r in _records(exporter)]
    assert names == ["frontend", "frontend/web", "frontend", "frontend/web"]


def test_stdout_fallback_without_endpoint(
    emitter: Emitter,
    exporter: InMemoryLogRecordExporter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "otel_endpoint", "")
    with caplog.at_level(logging.INFO, logger="obs_gateway.emit"):
        _emit(emitter, body="no-collector")
    assert _records(exporter) == []
    assert "client_telemetry" in caplog.text and "no-collector" in caplog.text


def test_export_failure_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    class _Broken(LogRecordProcessor):
        def on_emit(self, log_record: object) -> None:
            raise RuntimeError("collector down")

        def emit(self, log_record: object) -> None:
            raise RuntimeError("collector down")

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    broken = Emitter(processor_factory=_Broken)
    with caplog.at_level(logging.WARNING):
        _emit(broken)
    assert "otlp_emit_failed" in caplog.text or "collector down" in caplog.text


def test_shutdown_flushes_batched_records(exporter: InMemoryLogRecordExporter) -> None:
    batched = Emitter(processor_factory=lambda: BatchLogRecordProcessor(exporter, schedule_delay_millis=60_000))
    _emit(batched, body="pending")
    assert _records(exporter) == []
    batched.shutdown()
    assert [r.log_record.body for r in _records(exporter)] == ["pending"]
