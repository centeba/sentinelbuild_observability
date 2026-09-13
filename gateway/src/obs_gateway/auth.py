"""Optional, pluggable auth (self-contained).

Frontend telemetry is accepted anonymously (crashes/RUM happen pre-login); when a
bearer token IS present and JWT verification is configured, the tenant/user are
extracted from it and attached to the events. Service->gateway calls (``/status``)
may be gated by a shared internal key. Nothing here is host-specific — claim
names and keys are configuration.
"""

import functools
import hmac
import logging
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException
from jwt.types import Options

from .config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """Who a request is on behalf of. All fields optional (anonymous is valid)."""

    company_id: str | None = None
    user_id: str | None = None
    authenticated: bool = False


ANONYMOUS = Principal()


@functools.cache
def _jwk_client(url: str) -> jwt.PyJWKClient:
    # Pre-existing bug fixed: a new PyJWKClient was built per request, so every
    # authenticated ingest re-fetched the JWKS. One client per URL caches keys.
    return jwt.PyJWKClient(url)


def principal_from_bearer(authorization: str | None) -> Principal:
    """Best-effort decode of a ``Bearer`` token into a Principal.

    Returns ANONYMOUS when no token, no verification configured, or the token is
    invalid — telemetry ingest must never fail closed on auth (we'd lose the very
    crash we're trying to capture). A rejected token is logged, not raised.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return ANONYMOUS
    if not (settings.jwt_secret or settings.jwt_jwks_url):
        return ANONYMOUS
    token = authorization.split(" ", 1)[1].strip()
    try:
        options: Options = {"verify_aud": settings.jwt_audience is not None}
        key: object
        if settings.jwt_jwks_url:
            key = _jwk_client(settings.jwt_jwks_url).get_signing_key_from_jwt(token).key
        else:
            key = settings.jwt_secret
        claims = jwt.decode(
            token,
            key,  # type: ignore[arg-type]  # str secret or a cryptography public key
            algorithms=settings.jwt_algorithms,
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options=options,
        )
    except Exception as exc:  # invalid/expired/misconfigured — stay anonymous
        logger.info("jwt_decode_failed: %s", exc)
        return ANONYMOUS

    company = claims.get(settings.jwt_company_claim)
    user = claims.get(settings.jwt_user_claim)
    return Principal(
        company_id=str(company) if company is not None else None,
        user_id=str(user) if user is not None else None,
        authenticated=True,
    )


def internal_key_ok(provided: str | None) -> bool:
    """Constant-time check of the service->gateway shared key.

    Open (returns True) when no key is configured — the guard is opt-in.
    """
    expected = settings.internal_api_key
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency enforcing ``OBS_INTERNAL_API_KEY`` on internal routes."""
    # Pre-existing bug fixed: internal_key_ok existed but no route called it, so
    # OBS_INTERNAL_API_KEY had no effect.
    if not internal_key_ok(x_internal_key):
        raise HTTPException(status_code=401, detail="invalid internal key")
