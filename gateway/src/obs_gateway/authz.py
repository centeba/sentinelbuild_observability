"""RBAC integration seam: who may do what on the observability platform.

Every protected operation is an *action* (``status:read``, ``ui:access``,
``logs:read``, ``traces:read``, ``metrics:read``) checked by the configured
:class:`Authorizer` for a :class:`~obs_gateway.auth.Principal` from a bearer
token. Enforcement points:

- gateway routes (``/status``, ``/status/stack``) via :func:`require_action`;
- the Grafana UI and the Loki / Tempo / Prometheus APIs via the edge proxy's
  forward-auth call to ``GET /authz/verify`` (see ``edge/nginx.conf``).

``OBS_AUTHZ_MODE`` selects the implementation:

- ``none``     — :class:`AllowAllAuthorizer`: pre-RBAC behaviour (default).
- ``static``   — :class:`StaticRoleAuthorizer`: token roles -> permissions map.
- ``external`` — :class:`ExternalAuthorizer`: **integration stub** that asks an
  RBAC service (OpenFGA, OPA, Cerbos, an in-house authz API, ...) over HTTP.
  Replace or adapt it to your RBAC solution's API; the rest of the platform
  only depends on the :class:`Authorizer` protocol.

Ingest (``POST /api/telemetry/v1/ingest``) is intentionally not guarded:
anonymous pre-login crash reports must be accepted (FR-3.2).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response

from .auth import Principal, internal_key_ok, principal_from_bearer
from .config import settings

logger = logging.getLogger(__name__)

WILDCARD = "*"
GRAFANA_ROLE_RANK = {"Viewer": 1, "Editor": 2, "Admin": 3}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    # Grafana org role for UI sessions (Viewer / Editor / Admin).
    grafana_role: str | None = None


class Authorizer(Protocol):
    """Implement this to plug in an RBAC solution."""

    async def authorize(self, principal: Principal, action: str, tenant: str | None) -> Decision: ...


def _grafana_role(principal: Principal) -> str | None:
    mapped = [settings.authz_grafana_roles[r] for r in principal.roles if r in settings.authz_grafana_roles]
    return max(mapped, key=lambda role: GRAFANA_ROLE_RANK.get(role, 0), default=None)


class AllowAllAuthorizer:
    """No RBAC: everything is allowed (the platform's behaviour before RBAC)."""

    async def authorize(self, principal: Principal, action: str, tenant: str | None) -> Decision:
        return Decision(True, "authz disabled", _grafana_role(principal) or "Viewer")


class StaticRoleAuthorizer:
    """Roles from the token's ``OBS_JWT_ROLES_CLAIM`` mapped to permissions by
    ``OBS_AUTHZ_ROLE_PERMISSIONS``. Anonymous callers are denied. A role holding
    ``*`` may act on any tenant; other roles only on their own tenant."""

    async def authorize(self, principal: Principal, action: str, tenant: str | None) -> Decision:
        if not principal.authenticated:
            return Decision(False, "authentication required")
        granted = {p for role in principal.roles for p in settings.authz_role_permissions.get(role, [])}
        if WILDCARD in granted:
            return Decision(True, "wildcard role", _grafana_role(principal))
        if action not in granted:
            return Decision(False, f"no role grants {action}")
        if tenant is not None and tenant != principal.company_id:
            return Decision(False, "cross-tenant access requires a wildcard role")
        return Decision(True, "role grants action", _grafana_role(principal))


class ExternalAuthorizer:
    """**Stub** for an external RBAC service.

    Sends ``POST {OBS_AUTHZ_URL}`` with::

        {"subject": {"user_id", "company_id", "roles"}, "action": "logs:read", "tenant": "acme"}

    and expects ``200 {"allowed": bool, "reason"?: str, "grafana_role"?: str}``.
    Any error or non-200 answer denies (fail closed). Adapt the request/response
    mapping here to your RBAC product (e.g. OpenFGA ``/stores/{id}/check``, OPA
    ``/v1/data/<package>/allow``).
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def authorize(self, principal: Principal, action: str, tenant: str | None) -> Decision:
        if not principal.authenticated:
            return Decision(False, "authentication required")
        if not settings.authz_url:
            return Decision(False, "OBS_AUTHZ_URL not configured")
        payload = {
            "subject": {
                "user_id": principal.user_id,
                "company_id": principal.company_id,
                "roles": list(principal.roles),
            },
            "action": action,
            "tenant": tenant,
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=settings.authz_timeout_seconds) as client:
                resp = await client.post(settings.authz_url, json=payload)
            body = resp.json() if resp.status_code == 200 else {}
        except Exception as exc:
            logger.warning("authz_external_failed: %s", exc)
            return Decision(False, "authorization service unavailable")
        if resp.status_code != 200 or not isinstance(body, dict):
            return Decision(False, f"authorization service answered HTTP {resp.status_code}")
        grafana_role = body.get("grafana_role")
        return Decision(
            allowed=body.get("allowed") is True,
            reason=str(body.get("reason", "external decision")),
            grafana_role=str(grafana_role) if grafana_role else _grafana_role(principal),
        )


def get_authorizer() -> Authorizer:
    if settings.authz_mode == "static":
        return StaticRoleAuthorizer()
    if settings.authz_mode == "external":
        return ExternalAuthorizer()
    return AllowAllAuthorizer()


def _bearer(authorization: str | None, request: Request) -> str | None:
    if authorization:
        return authorization
    cookie = request.cookies.get(settings.authz_token_cookie)
    return f"Bearer {cookie}" if cookie else None


def require_action(action: str) -> Callable[..., Awaitable[None]]:
    """Route dependency: allow a valid internal key (service callers) or a
    principal the authorizer permits. With ``OBS_AUTHZ_MODE=none`` only the
    internal-key guard applies, exactly as before RBAC."""

    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        x_internal_key: str | None = Header(default=None),
    ) -> None:
        if settings.authz_mode == "none":
            # Pre-existing bug fixed: internal_key_ok existed but no route called
            # it, so OBS_INTERNAL_API_KEY had no effect.
            if not internal_key_ok(x_internal_key):
                raise HTTPException(status_code=401, detail="invalid internal key")
            return
        if settings.internal_api_key and x_internal_key and internal_key_ok(x_internal_key):
            return
        principal = principal_from_bearer(_bearer(authorization, request))
        if not principal.authenticated:
            raise HTTPException(status_code=401, detail="authentication required")
        decision = await get_authorizer().authorize(principal, action, None)
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.reason)

    return dependency


def action_for_path(path: str) -> str:
    for prefix, action in settings.authz_route_actions:
        if path.startswith(prefix):
            return action
    return "ui:access"


router = APIRouter(tags=["authz"])


@router.get("/authz/verify", include_in_schema=False)
async def verify(request: Request, authorization: str | None = Header(default=None)) -> Response:
    """Forward-auth for the edge proxy (nginx ``auth_request``, Traefik
    ``ForwardAuth``, Envoy ``ext_authz``).

    The proxied path comes from ``X-Original-URI`` / ``X-Forwarded-Uri`` and is
    mapped to an action by ``OBS_AUTHZ_ROUTE_ACTIONS``. 200 carries identity
    headers for the upstream (Grafana auth-proxy user and role, tenant); 401
    means no valid token; 403 means the authorizer denied the action.
    """
    original = request.headers.get("x-original-uri") or request.headers.get("x-forwarded-uri") or "/"
    action = action_for_path(original.split("?", 1)[0])
    principal = principal_from_bearer(_bearer(authorization, request))
    if not principal.authenticated and settings.authz_mode != "none":
        return Response(status_code=401, headers={"X-Obs-Authz-Reason": "authentication required"})
    decision = await get_authorizer().authorize(principal, action, None)
    if not decision.allowed:
        return Response(status_code=403, headers={"X-Obs-Authz-Reason": decision.reason})
    headers = {
        "X-Obs-Action": action,
        "X-WEBAUTH-USER": principal.user_id or "anonymous",
        "X-WEBAUTH-ROLE": decision.grafana_role or "Viewer",
    }
    if principal.company_id:
        # Loki/Tempo tenant header for per-tenant log and trace access.
        headers["X-Scope-OrgID"] = principal.company_id
    return Response(status_code=200, headers=headers)
