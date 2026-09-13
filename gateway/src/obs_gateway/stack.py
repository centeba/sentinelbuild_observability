"""Stack health monitor and local fallback logs.

A background task probes each observability component (collector, Loki, Tempo,
Prometheus, Grafana) every ``OBS_STACK_CHECK_INTERVAL_SECONDS``. Every change in
a component's health is appended to ``stack-health.jsonl``; while any
``OBS_FALLBACK_TRIGGER_COMPONENTS`` component is unhealthy, ingested frontend
events are also appended to ``events.jsonl`` — so nothing is invisible when
Grafana (or the pipeline behind it) cannot be reached. Both files live in
``OBS_FALLBACK_LOG_DIR`` and rotate by size.
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx
from prometheus_client import Counter, Gauge

from .config import settings

logger = logging.getLogger(__name__)

EVENTS_FILE = "events.jsonl"
HEALTH_FILE = "stack-health.jsonl"

COMPONENT_UP = Gauge(
    "obs_stack_component_up", "1 when the stack component's health endpoint answers 2xx.", ["component"]
)
FALLBACK_ACTIVE = Gauge("obs_fallback_active", "1 while frontend events are written to the local fallback log.")
FALLBACK_EVENTS = Counter("obs_fallback_events_total", "Frontend events written to the local fallback log.")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class FallbackLog:
    """Rotating JSON-lines files under ``settings.fallback_log_dir``."""

    def __init__(self) -> None:
        self._handlers: dict[Path, RotatingFileHandler] = {}

    def _handler(self, name: str) -> RotatingFileHandler | None:
        if not settings.fallback_log_dir:
            return None
        path = Path(settings.fallback_log_dir) / name
        handler = self._handlers.get(path)
        if handler is None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    path,
                    maxBytes=settings.fallback_log_max_bytes,
                    backupCount=settings.fallback_log_backup_count,
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.warning("fallback_log_unavailable path=%s: %s", path, exc)
                return None
            self._handlers[path] = handler
        return handler

    def _write(self, name: str, record: dict[str, object]) -> None:
        handler = self._handler(name)
        if handler is not None:
            handler.handle(logging.makeLogRecord({"msg": json.dumps(record, default=str)}))

    def write_event(self, record: dict[str, object]) -> None:
        self._write(EVENTS_FILE, {"time": _now(), **record})
        FALLBACK_EVENTS.inc()

    def write_health(self, record: dict[str, object]) -> None:
        self._write(HEALTH_FILE, {"time": _now(), **record})

    def close(self) -> None:
        for handler in self._handlers.values():
            handler.close()
        self._handlers.clear()


@dataclass(frozen=True)
class ComponentHealth:
    component: str
    healthy: bool
    detail: str  # "HTTP 200" or the exception class name
    checked_at: str


class StackMonitor:
    def __init__(self, fallback: FallbackLog, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.fallback = fallback
        self._transport = transport
        self.components: dict[str, ComponentHealth] = {}

    @property
    def fallback_active(self) -> bool:
        return any(
            not self.components[name].healthy
            for name in settings.fallback_trigger_components
            if name in self.components
        )

    async def _probe(self, client: httpx.AsyncClient, name: str, url: str) -> ComponentHealth:
        try:
            resp = await client.get(url, timeout=settings.health_timeout_seconds)
        except Exception as exc:
            return ComponentHealth(name, False, type(exc).__name__, _now())
        return ComponentHealth(name, 200 <= resp.status_code < 300, f"HTTP {resp.status_code}", _now())

    async def check_once(self) -> None:
        async with httpx.AsyncClient(transport=self._transport) as client:
            results = await asyncio.gather(
                *(self._probe(client, name, url) for name, url in settings.stack_targets.items())
            )
        transitions: list[tuple[ComponentHealth, ComponentHealth | None]] = []
        for result in results:
            previous = self.components.get(result.component)
            self.components[result.component] = result
            COMPONENT_UP.labels(component=result.component).set(1 if result.healthy else 0)
            # First observation is only recorded when unhealthy; after that, every flip.
            if (previous.healthy != result.healthy) if previous else not result.healthy:
                transitions.append((result, previous))
        for result, previous in transitions:
            self._record_transition(result, previous)
        FALLBACK_ACTIVE.set(1 if self.fallback_active else 0)

    def _record_transition(self, result: ComponentHealth, previous: ComponentHealth | None) -> None:
        state = "recovered" if result.healthy else "unhealthy"
        log = logger.info if result.healthy else logger.warning
        log("stack_component_%s component=%s detail=%s", state, result.component, result.detail)
        self.fallback.write_health(
            {
                "component": result.component,
                "state": state,
                "healthy": result.healthy,
                "detail": result.detail,
                "previous_detail": previous.detail if previous else None,
                "fallback_active": self.fallback_active,
            }
        )

    async def run(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception as exc:  # keep monitoring whatever happens
                logger.warning("stack_check_failed: %s", exc)
            await asyncio.sleep(settings.stack_check_interval_seconds)

    def snapshot(self) -> dict[str, object]:
        components = [asdict(c) for c in sorted(self.components.values(), key=lambda c: c.component)]
        if not components:
            status = "unknown"
        elif all(c["healthy"] for c in components):
            status = "ok"
        else:
            status = "degraded"
        return {
            "status": status,
            "fallback_active": self.fallback_active,
            "fallback_log_dir": settings.fallback_log_dir or None,
            "components": components,
        }


fallback_log = FallbackLog()
stack_monitor = StackMonitor(fallback_log)
