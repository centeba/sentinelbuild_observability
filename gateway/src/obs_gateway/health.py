"""Fleet health aggregation.

``GET /status`` concurrently probes every configured service's readiness endpoint
and returns a per-service + rollup view. Targets are plain config
(``OBS_HEALTH_TARGETS`` = ``{name: base_url}``), so this works for any fleet.
Guarded by ``OBS_INTERNAL_API_KEY`` and, when RBAC is enabled, ``status:read``.
"""

import asyncio

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .authz import require_action
from .config import settings
from .stack import stack_monitor

router = APIRouter(tags=["health"])


async def _probe(client: httpx.AsyncClient, name: str, base_url: str) -> dict[str, object]:
    url = base_url.rstrip("/") + settings.health_path
    try:
        resp = await client.get(url, timeout=settings.health_timeout_seconds)
    except Exception as exc:
        return {"service": name, "ok": False, "error": type(exc).__name__}
    return {"service": name, "ok": resp.status_code == 200, "status_code": resp.status_code}


@router.get("/status/stack", dependencies=[Depends(require_action("status:read"))])
async def stack_status() -> JSONResponse:
    """Health of the observability stack itself, from the background monitor.

    503 when any component is unhealthy; ``unknown`` (200) before the first check.
    """
    snapshot = stack_monitor.snapshot()
    return JSONResponse(snapshot, status_code=503 if snapshot["status"] == "degraded" else 200)


@router.get("/status", dependencies=[Depends(require_action("status:read"))])
async def status() -> JSONResponse:
    """Aggregate readiness across the configured fleet."""
    targets = settings.health_targets
    if not targets:
        return JSONResponse(
            {"status": "ok", "healthy": 0, "total": 0, "services": [], "note": "no targets configured"}
        )

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_probe(client, name, url) for name, url in targets.items())
        )
    healthy = sum(1 for r in results if r["ok"])
    return JSONResponse(
        status_code=200 if healthy == len(results) else 503,
        content={
            "status": "ok" if healthy == len(results) else "degraded",
            "healthy": healthy,
            "total": len(results),
            "services": sorted(results, key=lambda r: str(r["service"])),
        },
    )
