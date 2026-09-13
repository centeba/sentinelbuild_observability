# Observability Platform — Technical Design

Status: draft · Companion to [`REQUIREMENTS.md`](REQUIREMENTS.md).

## 1. Architecture

```
Frontends (web/mobile) ──POST /api/telemetry/v1/ingest──▶ obs-gateway ──OTLP──┐
  batched RUM / errors / logs      (same-origin proxy)     (FastAPI, stateless)│
                                                            • JWT→company_id    │
Backend services ──OTLP (logs+traces+metrics)──────────────────────────────▶ OTEL Collector
  (any OTEL SDK / instrumentation)                                             │
                                                    ┌──────────────┬───────────┴──────┐
                                                    ▼              ▼                  ▼
                                                  Loki           Tempo          Prometheus
                                                 (logs)         (traces)        (metrics)
                                                    └──────────────┴──────────────────┘
                                                                   ▼
                                                                Grafana (one pane + alerting)

obs-gateway also: GET /status → concurrent probe of each configured service /healthz → fleet health
```

**Why a gateway in front of the collector.** Browsers must not hit the collector
directly: they need a trust boundary that authenticates/enriches (tenant/user),
rate-limits, dedupes, caps field sizes, and lives at the app's same origin
(avoiding CORS/CSP relaxation). The gateway is that boundary and is otherwise
stateless — it forwards everything to the collector as OTLP.

## 2. Components

- **obs-gateway** (`gateway/src/obs_gateway/`): `main.py` (app + own health),
  `ingest.py` (frontend ingest: models, rate-limit, dedupe, enrich), `emit.py`
  (OTLP log forwarder), `health.py` (`/status` fan-out), `auth.py` (optional JWT +
  internal key), `config.py` (all `OBS_*` env). Stateless; no database.
- **OTEL Collector**: OTLP receivers → `memory_limiter` + `batch` → exporters to
  Loki (`otlphttp`), Tempo (`otlp`), Prometheus (`prometheusremotewrite`).
- **Loki / Tempo / Prometheus / Grafana**: stores + UI; Grafana datasources are
  provisioned with derived-field / tracesToLogs correlation on `trace_id`.

## 3. Data flow

1. A service handles a request; its OTEL SDK creates a span (`trace_id`) and emits
   logs/metrics/traces via OTLP to the collector. Logs carry the `trace_id`.
2. The frontend that made the request captures errors/RUM and POSTs a batch to
   `obs-gateway` (same origin → proxied to the gateway).
3. The gateway authenticates (optional), enriches with `company_id`/`user_id`,
   rate-limits/dedupes, and emits each event as an OTLP log to the collector.
4. The collector routes logs→Loki, traces→Tempo, metrics→Prometheus.
5. In Grafana, a trace's spans link to the exact Loki logs (same `trace_id`), all
   filterable by `service.name`, `deployment.environment`, and `company_id`.

## 4. API contracts (gateway)

- `POST /api/telemetry/v1/ingest` → `204` (or `429` when rate-limited).
  Body: `{ "events": [TelemetryEvent, ...] }` (≤ `OBS_MAX_BATCH_EVENTS`).
  `TelemetryEvent`: `type` (`log|error|event|perf`), `level`, `message`, `error?`,
  `stack?`, `url?`, `app?`, `app_version?`, `context?{str:str}`. Field sizes are
  capped (message 2000, stack 8000, …) → oversize yields `422`. Optional
  `Authorization: Bearer <jwt>`.
- `GET /status` → `200`/`503` with `{status, healthy, total, services:[{service,ok,...}]}`.
- `GET /health` (liveness), `GET /healthz` (readiness) → `200`.

## 5. Signal model & correlation

- **Resource attributes** on every signal: `service.name`, `deployment.environment`.
- **Trace correlation**: logs carry `trace_id`/`span_id`; Grafana's Loki→Tempo
  derived field and Tempo→Loki `tracesToLogsV2` join on `trace_id`.
- **Tenancy**: `company_id` is a log/trace attribute and a *sparingly-used* Loki
  label; it is **never** a Prometheus label (would explode cardinality). Tenant
  filtering of metrics is done via exemplars/derived views, not labels.

## 6. Logging levels (configurable)

Standard severities apply end-to-end and are **environment-configurable** with no
code change.

### 6.1 Severities

| Name | OTEL severity_number | When to use |
|---|---|---|
| `DEBUG` | 5 | Developer diagnostics; off in production by default. |
| `INFO` | 9 | Normal lifecycle events (request served, job done). |
| `WARNING` | 13 | Degraded-but-handled (retry, fallback, near-limit). |
| `ERROR` | 17 | A request/operation failed; needs attention. |
| `CRITICAL`/`FATAL` | 21 | Process-level failure; page someone. |
| `TRACE` | 1 | Ultra-verbose; alias below DEBUG. |

The gateway maps a client event's free-text `level` to these numbers (`emit.py`
`_SEVERITY`), defaulting unknown values to `INFO`.

### 6.2 Where level is set (env-driven)

| Scope | Variable | Values | Effect |
|---|---|---|---|
| **Gateway process** | `OBS_LOG_LEVEL` | `DEBUG…CRITICAL` | Verbosity of obs-gateway's own stdlib logging (`main.py` `logging.basicConfig`). |
| **Instrumented service** (host side) | `LOG_LEVEL` | `DEBUG…CRITICAL` | Passed to the service's logger setup (e.g. `configure_logging(level=…)`); controls what the service emits before it ever reaches the collector. |
| **Per-event (frontend)** | `event.level` in the payload | `debug…critical` | Severity of that single client event. |
| **Collector filtering (optional)** | collector `filter` processor | — | A pipeline-level severity floor can drop below-threshold logs centrally (documented extension; not enabled by default so nothing is silently lost). |

Guidance: services set `LOG_LEVEL=INFO` in production and `DEBUG` in
development/incident triage; the gateway mirrors this via `OBS_LOG_LEVEL`. Because
level is env-driven, raising verbosity during an incident is a restart, not a
deploy. Dropping noisy levels is best done at the **source** (service `LOG_LEVEL`)
to save ingest cost; a central collector filter is available when you can't change
a source.

### 6.3 Structured levels

Logs are structured JSON; `level` (text) and severity number travel as fields so
LogQL can filter (`{service_name="x"} | json | level="error"`) and Grafana can
colour by severity.

## 7. Auth & multi-tenancy

- **Frontend ingest**: auth-optional. When `OBS_JWT_SECRET` or `OBS_JWT_JWKS_URL`
  is set and a bearer token is present, the gateway verifies it and reads tenant/
  user from configurable claims (`OBS_JWT_COMPANY_CLAIM` default `org_id`,
  `OBS_JWT_USER_CLAIM` default `sub`). A bad token → anonymous, never a hard fail.
- **Service→gateway**: optional shared key (`OBS_INTERNAL_API_KEY`), constant-time
  compared; open when unset.
- No RLS/DB: tenant scoping is an attribute on emitted signals; query-time
  filtering enforces per-tenant views in Grafana.

## 8. Deployment

- **Local/standalone**: `docker compose up` (bundles gateway + collector + Loki +
  Tempo + Prometheus + Grafana; UIs loopback-bound; images pinned).
- **Host integration**: services set `OTEL_EXPORTER_OTLP_ENDPOINT` at the
  collector; frontends POST to the gateway via a same-origin proxy route; add
  `OBS_HEALTH_TARGETS` for `/status`.
- **Cloud**: run each component as its own service (gateway image is non-root,
  digest-pinned, honours `$PORT`); swap Loki/Tempo filesystem for object storage
  and enable auth on the stores.

## 9. Retention

Loki 30d, Tempo 14d, Prometheus 15d by default (all configurable). Retention is a
policy dial per signal; the compactors enforce it.

## 10. Security

Loopback-bound UIs; secrets via env; JWT verification when configured; constant-
time key compare; field-size caps + rate-limit + dedupe on ingest; no wildcard
CORS with credentials; PII minimization (opt-in claims, capped payloads, no
default PII in Sentry/OTEL). Harden further for production: enable store auth,
put the gateway behind the app's authenticated proxy, and set real Grafana creds.

## 11. Extensibility

- New signal sources: anything OTLP works with no gateway change.
- New client event types: extend `TelemetryEvent.type` + attributes.
- New backends: swap collector exporters (e.g. ClickHouse, S3) — the emit contract
  is unchanged.
- Alerting: add Prometheus rules + Alertmanager (operator wires the receiver).

## 12. Testing

- Gateway unit tests (`gateway/tests/`): ingest 204/422/429, dedupe, health,
  enrichment — collector stubbed, no external deps.
- Integration (compose): emit a demo trace+logs, assert correlation in Grafana;
  `/status` degrades on a killed dependency.
- Quality: ruff + `mypy --strict` + pytest coverage floor via `pyproject.toml`.

## 13. Spin-off notes

Import-clean and dependency-free of any host platform. To extract: move this
directory to a new repo, add `LICENSE`, enable CI from the existing tool config,
and publish dashboards. Integration surface is entirely configuration (OTLP
endpoint, ingest contract, health targets).
