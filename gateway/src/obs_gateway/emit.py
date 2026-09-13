"""Forward ingested client telemetry to the collector as OTLP logs.

The gateway is stateless: it validates + enriches browser/app events and emits
them as OTEL log records to the configured collector, which routes them to Loki.
Standard OTLP only — swap ``OBS_OTEL_ENDPOINT`` for any OTLP-compatible backend.

Each client ``app`` gets its own resource ``service.name`` (``frontend/<app>``),
so frontend logs are filterable per app like any backend service. The number of
per-app resources is bounded; overflow apps are recorded under ``frontend``.
"""

import logging
import time
from collections.abc import Callable

# Pre-existing bug fixed: LogRecord was imported lazily from
# opentelemetry.sdk._logs, which current SDK releases (verified on 1.44) no
# longer export. The ImportError was swallowed, so every ingested event was
# dropped with "otlp_emit_failed" while the client still got 204.
from opentelemetry._logs import Logger, LogRecord, SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LogRecordProcessor
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import TraceFlags

from .config import settings

logger = logging.getLogger(__name__)

# level (lower-case) -> (OTEL severity number, canonical severity text)
SEVERITY: dict[str, tuple[SeverityNumber, str]] = {
    "trace": (SeverityNumber.TRACE, "TRACE"),
    "debug": (SeverityNumber.DEBUG, "DEBUG"),
    "info": (SeverityNumber.INFO, "INFO"),
    "warn": (SeverityNumber.WARN, "WARNING"),
    "warning": (SeverityNumber.WARN, "WARNING"),
    "error": (SeverityNumber.ERROR, "ERROR"),
    "fatal": (SeverityNumber.FATAL, "CRITICAL"),
    "critical": (SeverityNumber.FATAL, "CRITICAL"),
}

FALLBACK_SERVICE = "frontend"
MAX_APP_SERVICES = 32


def _otlp_processor() -> LogRecordProcessor:
    return BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{settings.otel_endpoint}/v1/logs"))


class Emitter:
    """Emits client events as OTEL log records, one LoggerProvider per app.

    Falls back to a structured stdout line when no collector endpoint is
    configured, so ingest keeps working with nothing wired.
    """

    def __init__(self, processor_factory: Callable[[], LogRecordProcessor] = _otlp_processor) -> None:
        self._processor_factory = processor_factory
        self._providers: dict[str, LoggerProvider] = {}
        self._loggers: dict[str, Logger] = {}

    def _logger_for(self, app: str | None) -> Logger:
        service = f"{FALLBACK_SERVICE}/{app}" if app else FALLBACK_SERVICE
        if service not in self._loggers and len(self._loggers) >= MAX_APP_SERVICES:
            service = FALLBACK_SERVICE
        existing = self._loggers.get(service)
        if existing is not None:
            return existing
        provider = LoggerProvider(
            resource=Resource.create(
                {"service.name": service, "deployment.environment": settings.environment}
            )
        )
        provider.add_log_record_processor(self._processor_factory())
        new_logger = provider.get_logger("obs-gateway.client")
        self._providers[service] = provider
        self._loggers[service] = new_logger
        return new_logger

    def emit(
        self,
        *,
        app: str | None,
        body: str,
        level: str,
        trace_id: str | None,
        span_id: str | None,
        attributes: dict[str, str | None],
    ) -> None:
        clean = {k: v for k, v in attributes.items() if v is not None}
        if not settings.otel_endpoint:
            logger.info("client_telemetry app=%s level=%s body=%s attrs=%s", app, level, body, clean)
            return
        severity_number, severity_text = SEVERITY[level]
        now = time.time_ns()
        record = LogRecord(
            timestamp=now,
            observed_timestamp=now,
            trace_id=int(trace_id, 16) if trace_id else None,
            span_id=int(span_id, 16) if span_id else None,
            trace_flags=TraceFlags(TraceFlags.SAMPLED) if trace_id else None,
            severity_number=severity_number,
            severity_text=severity_text,
            body=body,
            attributes=clean,
        )
        try:
            self._logger_for(app).emit(record)
        except Exception as exc:  # never fail ingest because telemetry export failed
            logger.warning("otlp_emit_failed: %s", exc)

    def shutdown(self) -> None:
        """Flush buffered records and stop exporters (called on app shutdown)."""
        for provider in self._providers.values():
            provider.shutdown()
        self._providers.clear()
        self._loggers.clear()


emitter = Emitter()
