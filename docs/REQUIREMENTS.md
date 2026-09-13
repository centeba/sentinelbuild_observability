# Observability Platform — Requirements

Status: draft · Owner: platform · Applies to: the self-contained `observability/`
project (exportable as a standalone open-source product).

## 1. Purpose

Provide one centralized, tenant-aware system for **logs, metrics, traces, and
frontend RUM**, plus **health/status** across a fleet of services and their
frontends — giving true end-to-end observability from a browser click through
every backend hop. It must run standalone (one command), integrate with any
service over open standards (OTLP), and be extractable into its own repo without
code changes to the host platform.

## 2. Personas

- **Operator / SRE** — needs a single pane (Grafana), fleet health at a glance,
  and alerting when SLOs break.
- **Developer** — needs to trace one request across services with the exact log
  lines for that request, filtered to a tenant, without bespoke wiring per service.
- **Frontend engineer** — needs client crashes, errors, and page/perf events
  captured and correlated to the backend request that served them.
- **Platform integrator** — needs to drop the stack into a new environment and
  point services at it via env vars only.

## 3. Functional requirements

### 3.1 Ingest
- **FR-1** Accept **OTLP** logs, metrics, and traces (gRPC + HTTP) from any
  instrumented service.
- **FR-2** Accept **frontend/app telemetry** (errors, logs, page/nav, perf) over
  an authenticated-optional HTTP endpoint, batched.
- **FR-3** Enrich frontend events with **tenant (`company_id`) and user** when a
  bearer token is present; accept **anonymous** events (crashes occur pre-login).
- **FR-4** Harden ingest: strict field-size caps, per-client **rate limiting**,
  and short-window **dedupe** so a client error loop cannot flood the pipeline.
- **FR-5** Ingest is **fire-and-forget**: a malformed/over-limit event is rejected
  (422/429) but never takes the caller down; a collector outage never fails a request.

### 3.2 Storage & query
- **FR-6** Persist logs (Loki), traces (Tempo), metrics (Prometheus) with
  configurable **retention** per signal.
- **FR-7** Present all three in **Grafana** as one pane, with **trace↔log
  correlation** (a trace links to its logs and back via `trace_id`).

### 3.3 Correlation & tenancy
- **FR-8** Every signal carries `service.name`, `deployment.environment`, and —
  where applicable — `trace_id` and `company_id`, so a view can be filtered to one
  request and/or one tenant.
- **FR-9** `company_id` is a log/trace attribute (and a sparingly-used Loki
  label), **never** a Prometheus metric label (cardinality safety).

### 3.4 Health
- **FR-10** Aggregate readiness across a **configurable set of services**
  (`GET /status`), returning per-service and rollup status; degrade (503) when any
  target is unhealthy.

### 3.5 Logging levels (see DESIGN §"Logging levels")
- **FR-11** Support standard severities **DEBUG, INFO, WARNING, ERROR, CRITICAL**
  (plus TRACE/FATAL aliases) end to end.
- **FR-12** Verbosity is **environment-configurable** per process (e.g.
  `LOG_LEVEL` / `OBS_LOG_LEVEL`) with no redeploy of code; client events carry a
  per-event level mapped to OTEL severity numbers.

## 4. Non-functional requirements

- **NFR-1 Self-contained** — one `docker compose up`; no dependency on any host
  platform's code, database, or auth. Tenant/user extraction and health targets
  are configuration, not code.
- **NFR-2 Portable** — standard OTLP in; images pinned; the gateway is stateless
  (no DB) so it scales horizontally and extracts cleanly.
- **NFR-3 Secure** — UIs bind to loopback by default; secrets via env; JWT
  verification when configured; constant-time internal-key check; no
  wildcard-CORS-with-credentials; PII minimization (field caps, opt-in claims).
- **NFR-4 Resilient** — a telemetry burst cannot OOM the collector
  (memory-limiter) or the gateway (rate-limit/dedupe); components restart-safe.
- **NFR-5 Low-friction adoption** — a service integrates by setting one env var;
  a frontend by POSTing a documented JSON shape.
- **NFR-6 Quality gates** — gateway passes ruff, `mypy --strict`, and pytest with
  a coverage floor (configured in `gateway/pyproject.toml`).

## 5. Portability (spin-off)

The directory is import-clean and self-describing. To publish as its own repo:
add a `LICENSE`, keep `docs/`, enable CI from the existing `pyproject.toml` tool
config, and ship the Grafana dashboards. The only integration surface is: the
OTLP endpoint (emit), the `/api/telemetry/v1/ingest` contract (frontend), and the
`OBS_HEALTH_TARGETS` map (health) — all configuration.

## 6. Out of scope (v1)

- Long-term/object-store backends for Loki/Tempo (filesystem in v1; documented
  swap point).
- Managed alert-receiver wiring (Slack/PagerDuty) — provided as an operator step.
- SIEM/security analytics, synthetic/uptime probing beyond `/status`, and
  session-replay RUM.

## 7. Acceptance criteria

- `docker compose up` yields a working Grafana with all three datasources and the
  overview dashboard.
- A demo service emitting OTLP produces a trace whose spans link to its Loki logs
  (shared `trace_id`) in Grafana.
- A `POST /api/telemetry/v1/ingest` error event (with and without a JWT) appears
  in Loki, tenant-tagged when the token is present; oversize → 422; flood → 429.
- `GET /status` reflects a killed dependency as degraded (503).
- `OBS_LOG_LEVEL=DEBUG` increases gateway verbosity with no code change.
