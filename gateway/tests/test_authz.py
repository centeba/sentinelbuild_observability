"""RBAC seam: authorizers, route guard, and the edge forward-auth endpoint."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from obs_gateway import authz
from obs_gateway.auth import ANONYMOUS, Principal
from obs_gateway.authz import (
    AllowAllAuthorizer,
    ExternalAuthorizer,
    StaticRoleAuthorizer,
    action_for_path,
)
from obs_gateway.config import settings

from .conftest import make_token

ADMIN = Principal("acme", "root", True, ("obs-admin",))
VIEWER = Principal("acme", "vera", True, ("obs-viewer",))
EDITOR = Principal("acme", "ed", True, ("obs-viewer", "obs-editor"))
NO_ROLES = Principal("acme", "nobody", True, ())


def decide(authorizer: object, principal: Principal, action: str, tenant: str | None = None) -> authz.Decision:
    return asyncio.run(authorizer.authorize(principal, action, tenant))  # type: ignore[attr-defined]


# ── authorizers ───────────────────────────────────────────────────────────────


def test_allow_all() -> None:
    decision = decide(AllowAllAuthorizer(), ANONYMOUS, "logs:read")
    assert decision.allowed and decision.grafana_role == "Viewer"


@pytest.mark.parametrize(
    ("principal", "action", "tenant", "allowed", "grafana_role"),
    [
        (ANONYMOUS, "ui:access", None, False, None),
        (NO_ROLES, "ui:access", None, False, None),
        (VIEWER, "logs:read", None, True, "Viewer"),
        (VIEWER, "status:read", None, False, None),
        (EDITOR, "status:read", None, True, "Editor"),  # highest mapped role wins
        (VIEWER, "logs:read", "acme", True, "Viewer"),
        (VIEWER, "logs:read", "globex", False, None),  # cross-tenant
        (ADMIN, "logs:read", "globex", True, "Admin"),  # wildcard
        (ADMIN, "anything:else", None, True, "Admin"),
    ],
)
def test_static_roles(
    principal: Principal, action: str, tenant: str | None, allowed: bool, grafana_role: str | None
) -> None:
    decision = decide(StaticRoleAuthorizer(), principal, action, tenant)
    assert decision.allowed is allowed, decision.reason
    if allowed:
        assert decision.grafana_role == grafana_role


def test_static_roles_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "authz_role_permissions", {"sre": ["status:read"]})
    sre = Principal("acme", "s", True, ("sre",))
    assert decide(StaticRoleAuthorizer(), sre, "status:read").allowed
    assert not decide(StaticRoleAuthorizer(), sre, "logs:read").allowed


def external(handler: object) -> ExternalAuthorizer:
    return ExternalAuthorizer(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_external_sends_subject_and_uses_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "authz_url", "http://rbac/check")
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"allowed": True, "reason": "policy p1", "grafana_role": "Editor"})

    decision = decide(external(handler), VIEWER, "traces:read", "acme")
    assert decision == authz.Decision(True, "policy p1", "Editor")
    assert seen == [
        {
            "subject": {"user_id": "vera", "company_id": "acme", "roles": ["obs-viewer"]},
            "action": "traces:read",
            "tenant": "acme",
        }
    ]


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"allowed": False}),
        httpx.Response(200, json={"allowed": "yes"}),  # only literal true allows
        httpx.Response(500),
        httpx.ConnectError("down"),
    ],
    ids=["denied", "non-bool", "http-500", "unreachable"],
)
def test_external_fails_closed(monkeypatch: pytest.MonkeyPatch, answer: httpx.Response | Exception) -> None:
    monkeypatch.setattr(settings, "authz_url", "http://rbac/check")

    def handler(_: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert not decide(external(handler), VIEWER, "logs:read").allowed


def test_external_requires_url_and_authentication() -> None:
    assert decide(ExternalAuthorizer(), VIEWER, "logs:read").reason == "OBS_AUTHZ_URL not configured"
    assert not decide(ExternalAuthorizer(), ANONYMOUS, "logs:read").allowed


@pytest.mark.parametrize(
    ("mode", "cls"), [("none", AllowAllAuthorizer), ("static", StaticRoleAuthorizer), ("external", ExternalAuthorizer)]
)
def test_mode_selects_authorizer(monkeypatch: pytest.MonkeyPatch, mode: str, cls: type) -> None:
    monkeypatch.setattr(settings, "authz_mode", mode)
    assert isinstance(authz.get_authorizer(), cls)


def test_roles_claim_parsing(jwt_secret: str) -> None:
    from obs_gateway.auth import principal_from_bearer

    as_list = principal_from_bearer(f"Bearer {make_token({'roles': ['obs-viewer', 'obs-editor']})}")
    as_string = principal_from_bearer(f"Bearer {make_token({'roles': 'obs-viewer, obs-editor'})}")
    assert as_list.roles == as_string.roles == ("obs-viewer", "obs-editor")
    assert principal_from_bearer(f"Bearer {make_token({})}").roles == ()


# ── route guard (/status) ─────────────────────────────────────────────────────


@pytest.fixture()
def static_rbac(monkeypatch: pytest.MonkeyPatch, jwt_secret: str) -> None:
    monkeypatch.setattr(settings, "authz_mode", "static")


def bearer(**claims: object) -> dict[str, str]:
    return {"authorization": f"Bearer {make_token({'org_id': 'acme', 'sub': 'u', **claims})}"}


def test_status_guard_with_rbac(client: TestClient, static_rbac: None) -> None:
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers=bearer(roles=["obs-viewer"])).status_code == 403
    assert client.get("/status", headers=bearer(roles=["obs-editor"])).status_code == 200
    assert client.get("/status/stack", headers=bearer(roles=["obs-admin"])).status_code == 200


def test_status_guard_accepts_token_cookie(client: TestClient, static_rbac: None) -> None:
    client.cookies.set("obs_token", make_token({"roles": ["obs-editor"]}))
    assert client.get("/status").status_code == 200


def test_internal_key_still_works_for_services(
    client: TestClient, static_rbac: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "internal_api_key", "svc-key")
    assert client.get("/status", headers={"x-internal-key": "svc-key"}).status_code == 200
    assert client.get("/status", headers={"x-internal-key": "wrong"}).status_code == 401


def test_ingest_is_not_guarded(client: TestClient, static_rbac: None, captured: list[object]) -> None:
    assert client.post("/api/telemetry/v1/ingest", json={"events": [{"message": "anon crash"}]}).status_code == 204


# ── forward auth (/authz/verify) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "action"),
    [("/loki/api/v1/query_range?x=1", "logs:read"), ("/tempo/api/traces/1", "traces:read"),
     ("/prometheus/api/v1/query", "metrics:read"), ("/d/obs-overview", "ui:access"), ("", "ui:access")],
)
def test_action_for_path(path: str, action: str) -> None:
    assert action_for_path(path.split("?")[0]) == action


def test_verify_unauthenticated(client: TestClient, static_rbac: None) -> None:
    resp = client.get("/authz/verify", headers={"x-original-uri": "/"})
    assert resp.status_code == 401
    assert resp.headers["x-obs-authz-reason"] == "authentication required"


def test_verify_denied(client: TestClient, static_rbac: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "authz_role_permissions", {"ui-only": ["ui:access"]})
    headers = {"x-original-uri": "/loki/api/v1/query_range", **bearer(roles=["ui-only"])}
    resp = client.get("/authz/verify", headers=headers)
    assert resp.status_code == 403
    assert resp.headers["x-obs-authz-reason"] == "no role grants logs:read"


def test_verify_allowed_sets_identity_headers(client: TestClient, static_rbac: None) -> None:
    headers = {"x-forwarded-uri": "/loki/api/v1/labels", **bearer(roles=["obs-editor"])}
    resp = client.get("/authz/verify", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["x-obs-action"] == "logs:read"
    assert resp.headers["x-webauth-user"] == "u"
    assert resp.headers["x-webauth-role"] == "Editor"
    assert resp.headers["x-scope-orgid"] == "acme"


def test_verify_passthrough_when_rbac_disabled(client: TestClient) -> None:
    resp = client.get("/authz/verify", headers={"x-original-uri": "/"})
    assert resp.status_code == 200
    assert resp.headers["x-webauth-user"] == "anonymous"
    assert "x-scope-orgid" not in resp.headers
