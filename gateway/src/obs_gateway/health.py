"""Fleet health aggregation.

``GET /status`` concurrently probes every configured service's readiness endpoint
and returns a per-service + rollup view. Targets are plain config
(``OBS_HEALTH_TARGETS`` = ``{name: base_url}``), so this works for any fleet.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


async def _probe(client: httpx.AsyncClient, name: str, base_url: str) -> dict[str, object]:
    url = base_url.rstrip("/") + settings.health_path
    try:
        resp = await client.get(url, timeout=settings.health_timeout_seconds)
        ok = resp.status_code == 200
        return {"service": name, "ok": ok, "status_code": resp.status_code}
    except Exception as exc:
        return {"service": name, "ok": False, "error": type(exc).__name__}


@router.get("/status")
async def status() -> JSONResponse:
    """Aggregate readiness across the configured fleet."""
    targets = settings.health_targets
    if not targets:
        return JSONResponse({"status": "ok", "services": [], "note": "no targets configured"})

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_probe(client, name, url) for name, url in targets.items())
        )
    healthy = all(r["ok"] for r in results)
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "healthy": sum(1 for r in results if r["ok"]),
            "total": len(results),
            "services": sorted(results, key=lambda r: str(r["service"])),
        },
    )
