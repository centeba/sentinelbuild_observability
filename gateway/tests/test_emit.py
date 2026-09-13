"""The OTLP emitter against the real OpenTelemetry SDK (no collector needed)."""

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

from obs_gateway import emit
from obs_gateway.config import Settings, settings


@pytest.fixture()
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemoryLogRecordExporter:
    memory = InMemoryLogRecordExporter()
    monkeypatch.setattr(emit, "OTLPLogExporter", lambda endpoint: memory)
    monkeypatch.setattr(settings, "otel_endpoint", "http://collector:4318")
    return memory


def test_emit_reaches_exporter_and_encodes_as_otlp(exporter: InMemoryLogRecordExporter) -> None:
    emitter = emit._Emitter()
    emitter.emit(body="boom", severity="error", attributes={"company_id": "acme", "user_id": None})
    assert emitter._provider is not None
    emitter._provider.force_flush()

    (record,) = exporter.get_finished_logs()
    assert record.log_record.body == "boom"
    assert record.log_record.severity_number == SeverityNumber.ERROR
    assert dict(record.log_record.attributes or {}) == {"company_id": "acme"}
    # The OTLP/protobuf encoder used by the HTTP exporter must accept the record.
    encoded = encode_logs([record])
    assert encoded.resource_logs[0].scope_logs[0].log_records[0].severity_number == 17


def test_unknown_severity_defaults_to_info(exporter: InMemoryLogRecordExporter) -> None:
    emitter = emit._Emitter()
    emitter.emit(body="x", severity="verbose", attributes={})
    assert emitter._provider is not None
    emitter._provider.force_flush()
    (record,) = exporter.get_finished_logs()
    assert record.log_record.severity_number == SeverityNumber.INFO


def test_list_settings_accept_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pre-existing bug: pydantic-settings JSON-decoded list env vars, so the
    # documented comma-separated form crashed the gateway at startup.
    monkeypatch.setenv("OBS_CORS_ALLOW_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("OBS_JWT_ALGORITHMS", "RS256")
    loaded = Settings()
    assert loaded.cors_allow_origins == ["https://a.example", "https://b.example"]
    assert loaded.jwt_algorithms == ["RS256"]
