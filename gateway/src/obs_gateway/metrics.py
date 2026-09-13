"""The gateway's own Prometheus metrics, served at ``GET /metrics``.

``http_request_duration_seconds`` uses the same name and labels (``method``,
``route``, ``status``) that the bundled overview dashboard queries, so the
gateway shows up there like any other service. No tenant labels (cardinality).
"""

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "route", "status"],
)
INGEST_EVENTS = Counter(
    "obs_ingest_events_total",
    "Client telemetry events processed by the ingest endpoint.",
    ["outcome"],  # accepted | duplicate
)


async def record_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    REQUEST_DURATION.labels(
        method=request.method,
        route=getattr(route, "path", "unmatched"),
        status=str(response.status_code),
    ).observe(time.perf_counter() - start)
    return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
