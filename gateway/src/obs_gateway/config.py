"""Gateway configuration (self-contained; no host-platform imports).

All settings come from the environment (12-factor) so the same image runs in
any deployment. Nothing here is SentinelBuild-specific — tenant/user extraction
is driven by configurable JWT claim names, and the health-probe targets are a
plain map, so the service spins off as a standalone project unchanged.
"""

from __future__ import annotations

import json

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OBS_", extra="ignore")

    # Identity
    service_name: str = "obs-gateway"
    environment: str = Field(default="development")

    # Where to forward ingested client telemetry (OTLP/HTTP collector).
    otel_endpoint: str = "http://otel-collector:4318"

    # Optional auth. When a secret/JWKS is configured the gateway extracts the
    # tenant/user from the bearer token (still accepting anonymous — crashes
    # happen pre-login); otherwise everything is anonymous.
    jwt_secret: str | None = None
    jwt_jwks_url: str | None = None
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["HS256", "RS256"])
    jwt_audience: str | None = None
    jwt_issuer: str | None = None
    # Claim names to read tenant/user from (configurable so any IdP fits).
    jwt_company_claim: str = "org_id"
    jwt_user_claim: str = "sub"

    # Optional shared secret required on service->gateway (non-frontend) calls,
    # checked with a constant-time compare. Unset => that guard is open.
    internal_api_key: str | None = None

    # Fleet health aggregation: {name: base_url}. Probed at "<base_url><path>".
    health_targets: dict[str, str] = Field(default_factory=dict)
    health_path: str = "/healthz"
    health_timeout_seconds: float = 3.0

    # Ingest hardening.
    max_event_bytes: int = 16_384
    max_batch_events: int = 100
    rate_limit_per_min: int = 600  # per client ip
    dedupe_window_seconds: float = 10.0

    # CORS. Default empty => rely on the host's same-origin proxy (recommended).
    cors_allow_origins: list[str] = Field(default_factory=list)

    log_level: str = "INFO"

    @field_validator("health_targets", mode="before")
    @classmethod
    def _parse_targets(cls, v: object) -> object:
        # Allow a JSON string in the env: OBS_HEALTH_TARGETS='{"user-master":"http://user-master:8000"}'
        if isinstance(v, str) and v.strip():
            return json.loads(v)
        return v

    @field_validator("jwt_algorithms", "cors_allow_origins", mode="before")
    @classmethod
    def _parse_list(cls, v: object) -> object:
        if isinstance(v, str) and v.strip():
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v


settings = Settings()
