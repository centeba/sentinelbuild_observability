"""Bearer-token principal extraction and the internal-key guard."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from obs_gateway import auth
from obs_gateway.auth import ANONYMOUS, internal_key_ok, principal_from_bearer
from obs_gateway.config import settings

from .conftest import make_token


def test_no_header_or_non_bearer_is_anonymous(jwt_secret: str) -> None:
    assert principal_from_bearer(None) is ANONYMOUS
    assert principal_from_bearer("Basic dXNlcjpwYXNz") is ANONYMOUS


def test_hs256_claims(jwt_secret: str) -> None:
    principal = principal_from_bearer(f"Bearer {make_token({'org_id': 42, 'sub': 'u1'})}")
    assert principal == auth.Principal(company_id="42", user_id="u1", authenticated=True)


def test_bearer_scheme_is_case_insensitive(jwt_secret: str) -> None:
    assert principal_from_bearer(f"bearer {make_token({'org_id': 'acme'})}").company_id == "acme"


def test_custom_claim_names(jwt_secret: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jwt_company_claim", "tenant")
    monkeypatch.setattr(settings, "jwt_user_claim", "email")
    principal = principal_from_bearer(f"Bearer {make_token({'tenant': 't1', 'email': 'a@b.c'})}")
    assert (principal.company_id, principal.user_id) == ("t1", "a@b.c")


def test_missing_claims_still_authenticated(jwt_secret: str) -> None:
    principal = principal_from_bearer(f"Bearer {make_token({})}")
    assert principal.authenticated
    assert (principal.company_id, principal.user_id) == (None, None)


def test_audience_and_issuer_enforced(jwt_secret: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jwt_audience", "obs")
    monkeypatch.setattr(settings, "jwt_issuer", "https://issuer")
    good = make_token({"org_id": "a", "aud": "obs", "iss": "https://issuer"})
    wrong_aud = make_token({"org_id": "a", "aud": "other", "iss": "https://issuer"})
    wrong_iss = make_token({"org_id": "a", "aud": "obs", "iss": "https://evil"})
    assert principal_from_bearer(f"Bearer {good}").authenticated
    assert principal_from_bearer(f"Bearer {wrong_aud}") is ANONYMOUS
    assert principal_from_bearer(f"Bearer {wrong_iss}") is ANONYMOUS


def test_disallowed_algorithm_rejected(jwt_secret: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jwt_algorithms", ["RS256"])
    assert principal_from_bearer(f"Bearer {make_token({'org_id': 'a'})}") is ANONYMOUS


def test_jwks_rs256(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {"org_id": "acme", "sub": "u9", "exp": int(time.time()) + 60},
        private_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )

    class _SigningKey:
        key = private_key.public_key()

    class _FakeJwkClient:
        def get_signing_key_from_jwt(self, _token: str) -> _SigningKey:
            return _SigningKey()

    monkeypatch.setattr(settings, "jwt_jwks_url", "https://issuer/.well-known/jwks.json")
    monkeypatch.setattr(settings, "jwt_algorithms", ["RS256"])
    monkeypatch.setattr(auth, "_jwk_client", lambda _url: _FakeJwkClient())
    principal = principal_from_bearer(f"Bearer {token}")
    assert (principal.company_id, principal.user_id) == ("acme", "u9")


def test_jwk_client_is_cached_per_url() -> None:
    auth._jwk_client.cache_clear()
    first = auth._jwk_client("https://a/jwks")
    assert auth._jwk_client("https://a/jwks") is first
    assert auth._jwk_client("https://b/jwks") is not first
    auth._jwk_client.cache_clear()


def test_internal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert internal_key_ok(None)  # open when unset
    monkeypatch.setattr(settings, "internal_api_key", "k-123")
    assert not internal_key_ok(None)
    assert not internal_key_ok("")
    assert not internal_key_ok("k-124")
    assert internal_key_ok("k-123")
