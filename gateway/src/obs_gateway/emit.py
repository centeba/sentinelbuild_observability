"""Forward ingested client telemetry to the collector as OTLP logs.

The gateway is stateless: it validates + enriches browser/app events and emits
them as OTEL log records to the configured collector, which routes them to Loki.
Standard OTLP only — swap ``OBS_OTEL_ENDPOINT`` for any OTLP-compatible backend.
"""

import logging
import time
from typing import Any

# Pre-existing bug fixed: LogRecord was imported lazily from
# opentelemetry.sdk._logs, which current SDK releases (verified on 1.44) no
# longer export. The ImportError was swallowed, so every ingested event was
# dropped with "otlp_emit_failed" while the client still got 204.
from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from .config import settings

logger = logging.getLogger(__name__)

_SEVERITY = {
    "trace": SeverityNumber.TRACE,
    "debug": SeverityNumber.DEBUG,
    "info": SeverityNumber.INFO,
    "warn": SeverityNumber.WARN,
    "warning": SeverityNumber.WARN,
    "error": SeverityNumber.ERROR,
    "fatal": SeverityNumber.FATAL,
    "critical": SeverityNumber.FATAL,
}


class _Emitter:
    """Lazily-built OTEL log emitter; logs to stdout if no endpoint is configured
    so the gateway still runs and the request still succeeds."""

    def __init__(self) -> None:
        self._provider: LoggerProvider | None = None
        self._logger: Any | None = None
        self._ready = False

    def _ensure(self) -> None:
        if self._ready:
            return
        self._ready = True
        if not settings.otel_endpoint:
            return
        resource = Resource.create(
            {
                "service.name": "frontend-telemetry",
                "deployment.environment": settings.environment,
            }
        )
        provider = LoggerProvider(resource=resource)
        exporter = OTLPLogExporter(endpoint=f"{settings.otel_endpoint}/v1/logs")
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        self._provider = provider
        self._logger = provider.get_logger("obs-gateway.client")

    def emit(self, *, body: str, severity: str, attributes: dict[str, Any]) -> None:
        self._ensure()
        if self._logger is None:
            # Fallback: structured line to stdout (still collected if the host
            # ships stdout). Keeps ingest working with no collector wired.
            logger.info("client_telemetry body=%s attrs=%s", body, attributes)
            return
        try:
            now = time.time_ns()
            record = LogRecord(
                timestamp=now,
                observed_timestamp=now,
                # Pre-existing bug fixed: a plain int was passed, which the OTLP
                # encoder silently exports as severity 0 (unspecified).
                severity_number=_SEVERITY.get(severity.lower(), SeverityNumber.INFO),
                severity_text=severity.upper(),
                body=body,
                attributes={k: v for k, v in attributes.items() if v is not None},
            )
            self._logger.emit(record)
        except Exception as exc:  # defensive; never fail ingest
            logger.warning("otlp_emit_failed: %s", exc)


emitter = _Emitter()
