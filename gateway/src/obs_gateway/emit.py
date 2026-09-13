"""Forward ingested client telemetry to the collector as OTLP logs.

The gateway is stateless: it validates + enriches browser/app events and emits
them as OTEL log records to the configured collector, which routes them to Loki.
Standard OTLP only — swap ``OBS_OTEL_ENDPOINT`` for any OTLP-compatible backend.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .config import settings

logger = logging.getLogger(__name__)

_SEVERITY = {
    "trace": 1,
    "debug": 5,
    "info": 9,
    "warn": 13,
    "warning": 13,
    "error": 17,
    "fatal": 21,
    "critical": 21,
}


class _Emitter:
    """Lazily-built OTEL log emitter; no-ops (logs to stdout) if the SDK/endpoint
    is unavailable so the gateway still runs and the request still succeeds."""

    def __init__(self) -> None:
        self._logger: Any | None = None
        self._ready = False

    def _ensure(self) -> None:
        if self._ready:
            return
        self._ready = True
        if not settings.otel_endpoint:
            return
        try:
            from opentelemetry._logs import set_logger_provider
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from opentelemetry.sdk.resources import Resource
        except Exception as exc:  # pragma: no cover - optional dep
            logger.warning("otlp_logs_unavailable: %s", exc)
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
        set_logger_provider(provider)
        self._logger = provider.get_logger("obs-gateway.client")

    def emit(self, *, body: str, severity: str, attributes: dict[str, Any]) -> None:
        self._ensure()
        if self._logger is None:
            # Fallback: structured line to stdout (still collected if the host
            # ships stdout). Keeps ingest working with no collector wired.
            logger.info("client_telemetry body=%s attrs=%s", body, attributes)
            return
        try:
            from opentelemetry.sdk._logs import LogRecord

            now = time.time_ns()
            sev = _SEVERITY.get(severity.lower(), 9)
            record = LogRecord(
                timestamp=now,
                observed_timestamp=now,
                severity_number=sev,
                severity_text=severity.upper(),
                body=body,
                attributes={k: v for k, v in attributes.items() if v is not None},
            )
            self._logger.emit(record)
        except Exception as exc:  # pragma: no cover - defensive; never fail ingest
            logger.warning("otlp_emit_failed: %s", exc)


emitter = _Emitter()
