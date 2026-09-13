# SentinelBuild Observability Platform — User Manual

| | |
|---|---|
| **Version** | 1.1 — obs-gateway 0.2.0 · obs_telemetry (Flutter) 0.1.0 · @sentinelbuild/obs-telemetry (React) 0.1.0 · OTEL Collector 0.160.0 |
| **Audience** | Operators/SREs, backend developers, frontend engineers, platform integrators, security owners |
| **Companions** | [REQUIREMENTS.md](REQUIREMENTS.md) · [DESIGN.md](DESIGN.md) · [TEST_CASES.md](TEST_CASES.md) |

## Contents

1. [What the platform does](#1-what-the-platform-does)
2. [Installation and quick start](#2-installation-and-quick-start)
3. [Using Grafana](#3-using-grafana)
4. [Connecting backend services](#4-connecting-backend-services)
5. [Connecting frontends](#5-connecting-frontends)
6. [Configuration reference](#6-configuration-reference)
7. [Fleet and stack health](#7-fleet-and-stack-health)
8. [Production deployment](#8-production-deployment)
9. [Operations](#9-operations)
10. [Running the tests and linters](#10-running-the-tests-and-linters)
11. [Troubleshooting](#11-troubleshooting)
12. [FAQ](#12-faq)
13. [Securing access (RBAC)](#13-securing-access-rbac)

---

## 1. What the platform does

The platform collects four kinds of telemetry and shows them in one place (Grafana):

| Signal | Comes from | Stored in | Kept for |
|---|---|---|---|
| **Logs** | Backend services (OTLP), frontend apps (via obs-gateway) | Loki | 30 days |
| **Traces** | Backend services (OTLP) | Tempo | 14 days |
| **Metrics** | Services (Prometheus scrape or OTLP), Tempo span metrics, the platform itself | Prometheus | 15 days |
| **Frontend events** (errors, logs, navigation, performance) | Web/mobile apps using a client library or plain HTTP | Loki (as logs) | 30 days |

Three ideas run through everything:

- **`service_name`** identifies where a signal came from. Backend services set it themselves. Frontend apps appear as `frontend/<app>` (for example `frontend/web`).
- **`trace_id`** links a request's spans in Tempo with the log lines written while handling it, including a frontend error about that request when the app passes the trace id along.
- **`company_id`** (tenant) and **`user_id`** are attached to logs, so you can filter to one customer. They are never used as metric labels.

---

## 2. Installation and quick start

### 2.1 Prerequisites

- Docker Engine with **Docker Compose v2.24 or later** (Docker Desktop on Windows/macOS works).
- About 4 GB of free memory for the full stack.
- Free host ports: `3000` (Grafana), `8080` (gateway), `9090` (Prometheus), `4317`/`4318` (OTLP), plus `5173` for the optional demo.

### 2.2 Start the stack

```bash
git clone https://github.com/centeba/sentinelbuild_observability
cd sentinelbuild_observability
cp .env.example .env
docker compose up -d
```

The first start pulls about 1.5 GB of images and builds the gateway. Services start in dependency order and wait for each other's healthchecks. Check progress with:

```bash
docker compose ps
```

All services should show `running`; those with healthchecks show `(healthy)`. The collector has no healthcheck because its image contains no shell.

### 2.3 What is running

| URL | What |
|---|---|
| <http://127.0.0.1:3000> | **Grafana** — log in with `admin` / `admin` (change it: see §8) |
| <http://127.0.0.1:8080/health> | Gateway liveness |
| <http://127.0.0.1:8080/status/stack> | Health of every platform component (§7.2) |
| <http://127.0.0.1:8080/docs> | Gateway OpenAPI (Swagger UI) |
| <http://127.0.0.1:9090> | Prometheus |
| `127.0.0.1:4317` (gRPC), `127.0.0.1:4318` (HTTP) | OTLP ingest for your services |

Everything binds to **127.0.0.1** only, so nothing is reachable from other machines by default. Loki (3100) and Tempo (3200) are reachable only inside the Docker network; use them through Grafana.

### 2.4 Verify it works (2 minutes)

Send a frontend error through the gateway:

```bash
curl -i -X POST http://127.0.0.1:8080/api/telemetry/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"events":[{"type":"error","level":"error","message":"hello from curl","app":"web"}]}'
```

Expect `HTTP/1.1 204 No Content`. Then in Grafana:

1. Open **Explore**.
2. Pick the **Loki** datasource.
3. Run `{service_name="frontend/web"}`.

The line `hello from curl` appears within about 10 seconds.

Alternatively, run the automated end-to-end check (§10.2), which verifies every part of the pipeline.

### 2.5 Try the React demo app

```bash
docker compose --profile demo up -d
```

Open <http://127.0.0.1:5173> and click the buttons (log, event, render error, unhandled rejection, error with trace context), then **Flush now**. Query `{service_name="frontend/react-demo"}` in Grafana to see the five events.

### 2.6 Stop, restart, reset

| Action | Command |
|---|---|
| Stop, keeping data | `docker compose down` |
| Start again | `docker compose up -d` |
| Rebuild the gateway after code changes | `docker compose up -d --build obs-gateway` |
| Delete **all** stored logs, traces, metrics and Grafana state | `docker compose down -v` |
| Follow a service's logs | `docker compose logs -f obs-gateway` |

### 2.7 How the config files reach the containers

Each store reads its configuration from a file in this repo. Compose **bind-mounts** it into the container at the path the program expects:

| Repo file | Path inside the container |
|---|---|
| `loki/config.yml` | `/etc/loki/config.yml` |
| `tempo/config.yml` | `/etc/tempo/config.yml` |
| `prometheus/prometheus.yml` | `/etc/prometheus/prometheus.yml` |
| `otel-collector/config.yml` | `/etc/otelcol/config.yml` |
| `grafana/provisioning/` | `/etc/grafana/provisioning/` |
| `grafana/dashboards/` | `/var/lib/grafana/dashboards/` |

Edit the repo file, then restart that service (for example `docker compose restart loki`).

---

## 3. Using Grafana

### 3.1 Where things are

- **Dashboards → Observability → Observability — Overview**: request rate, 5xx error %, p95 latency per service, and recent logs. Use the **Service** drop-down to filter.
- **Explore**: ad-hoc queries against **Loki** (logs), **Tempo** (traces) and **Prometheus** (metrics).

### 3.2 Finding logs (Loki / LogQL)

A query starts with a stream selector on indexed labels (`service_name`, `deployment_environment`). Filters on everything else follow a `|`.

| Goal | Query |
|---|---|
| All logs of one service | `{service_name="billing"}` |
| All frontend apps | `{service_name=~"frontend/.+"}` |
| Only errors | `{service_name="frontend/web"} \| severity_text="ERROR"` |
| One tenant | `{service_name=~"frontend/.+"} \| company_id="acme"` |
| One user | `{service_name=~"frontend/.+"} \| user_id="user-123"` |
| One request, across services | `{deployment_environment="production"} \| trace_id="4bf92f3577b34da6a3ce929d0e0e4736"` |
| Text search | `{service_name="billing"} \|= "timeout"` |
| Frontend event type | `{service_name="frontend/web"} \| telemetry_type="perf"` |
| A context value sent by the app | `{service_name="frontend/web"} \| ctx_route="/jobs/42"` |
| Error count per app, 5-min buckets | `sum by (service_name) (count_over_time({service_name=~"frontend/.+"} \| severity_text="ERROR" [5m]))` |

Attribute names have dots replaced by underscores in Loki: `telemetry.type` becomes `telemetry_type`, `app.version` becomes `app_version`, and a context key `route` becomes `ctx_route`.

Severity values are `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

### 3.3 Following a request: logs ⇄ traces

- **From a log line to its trace.** Expand a log line that has a `trace_id` and click the **TraceID** link. Tempo opens the full trace.
- **From a trace to its logs.** In a Tempo trace view, click the logs icon on a span (**Logs for this span**). Grafana queries Loki for the same `trace_id` and service within ±1 hour.
- **Find a trace directly.** Explore → Tempo → **Search** (by service, span name, duration, status), or paste a trace id in **TraceQL**, e.g. `{ resource.service.name = "billing" && status = error }`.

### 3.4 Service graph

Explore → Tempo → **Service Graph** shows which services call which, with request rates and error rates. It is computed from traces by Tempo's metrics-generator, so it appears **1–2 minutes** after traces start flowing. It needs spans with client/server relationships between services.

### 3.5 Metrics (Prometheus / PromQL)

| Goal | Query |
|---|---|
| Is every scrape target up? | `up` |
| Gateway ingest volume | `sum by (outcome) (rate(obs_ingest_events_total[5m]))` |
| Gateway rejections and rate limiting | `sum by (status) (rate(http_request_duration_seconds_count{job="obs-gateway",route="/api/telemetry/v1/ingest"}[5m]))` |
| Per-service request rate (from traces) | `sum by (service) (rate(traces_spanmetrics_calls_total[5m]))` |
| Collector refusing data | `rate(otelcol_receiver_refused_log_records_total[5m])` |

---

## 4. Connecting backend services

### 4.1 Traces, logs and metrics over OTLP

Any service with an OpenTelemetry SDK or agent sends to the collector. Set these environment variables on the service:

```bash
OTEL_SERVICE_NAME=billing
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production
OTEL_TRACES_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
```

`deployment.environment` defaults to the stack's `ENVIRONMENT` when the service doesn't send one.

Language quick starts, using zero-code instrumentation:

| Language | Setup |
|---|---|
| **Python** | `pip install opentelemetry-distro opentelemetry-exporter-otlp && opentelemetry-bootstrap -a install`, set `OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true`, run with `opentelemetry-instrument uvicorn app:app` |
| **Node.js** | `npm i @opentelemetry/auto-instrumentations-node`, run with `node --require @opentelemetry/auto-instrumentations-node/register app.js` |
| **Java** | Download `opentelemetry-javaagent.jar`, run with `java -javaagent:opentelemetry-javaagent.jar -jar app.jar` |
| **.NET** | Add `OpenTelemetry.AutoInstrumentation` (or the `OpenTelemetry.Extensions.Hosting` + OTLP exporter packages) |
| **Go** | Use the `go.opentelemetry.io/otel` SDK with the `otlptracehttp` / `otlploghttp` exporters |

With these set up, logs written inside a request automatically carry that request's `trace_id`, which is what makes the Grafana log⇄trace links work.

### 4.2 Network access to the collector

| Where the service runs | Endpoint to use |
|---|---|
| In another Docker Compose project on the same host | Join the platform network (below), then use `http://otel-collector:4318` |
| Directly on the host | `http://127.0.0.1:4318` |
| In a container on Docker Desktop, not joined to the network | `http://host.docker.internal:4318` |
| On another machine | Production setup with token auth (§8.3) |

To join the platform network from another compose file:

```yaml
services:
  billing:
    networks: [default, observability]
networks:
  observability:
    external: true
    name: observability_default
```

### 4.3 Tagging telemetry with the tenant

Add the tenant as a span and log attribute named `company_id` (and `user_id` if useful) in your request middleware. Example for Python:

```python
from opentelemetry import trace
trace.get_current_span().set_attribute("company_id", org_id)
logger.info("invoice created", extra={"company_id": org_id})
```

Never put `company_id` on a **metric** label. Each tenant would create new time series and overload Prometheus.

### 4.4 Metrics by scraping

If a service exposes Prometheus metrics at `/metrics`, add it to `prometheus/prometheus.yml`:

```yaml
  - job_name: billing
    static_configs: [{ targets: ["billing:8000"] }]
```

Then run `docker compose restart prometheus`.

The **Overview dashboard** expects a histogram named `http_request_duration_seconds` with a `status` label, the common Prometheus-client convention; the gateway itself exposes one. Services that only send OTLP semantic-convention metrics produce `http_server_request_duration_seconds_*` with `http_response_status_code` instead. Adapt the panel queries, or use the Tempo span metrics (`traces_spanmetrics_*`).

### 4.5 Log levels

Set the service's own log level via its environment, for example `LOG_LEVEL=INFO` in production and `DEBUG` while investigating an incident. Changing it is a restart, not a deploy. Filter at the source: this saves ingest and storage, and the platform deliberately does not drop logs centrally.

---

## 5. Connecting frontends

Frontends send telemetry to the **gateway**, never to the collector. Serve the gateway at your app's **own origin** through the app's reverse proxy, so no CORS or CSP changes are needed:

```nginx
location /api/telemetry/ {
    client_max_body_size 2m;
    proxy_pass http://obs-gateway:8080;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Then set `OBS_TRUSTED_PROXY_HOPS=1` on the gateway, so rate limiting uses the real client IP rather than the proxy's. A complete working example is `examples/react-demo/nginx.conf`.

### 5.1 React / browser apps

**Install.** The package isn't published to npm yet; link it from this repo:

```bash
cd clients/react/obs-telemetry && npm install && npm run build
```

```json
"dependencies": { "@sentinelbuild/obs-telemetry": "file:../path/to/clients/react/obs-telemetry" }
```

With Vite, add `resolve: { dedupe: ['react', 'react-dom'] }` to `vite.config.ts`.

**Initialise** once, in `main.tsx`:

```tsx
import { Telemetry, installErrorHandlers } from '@sentinelbuild/obs-telemetry';
import { TelemetryErrorBoundary } from '@sentinelbuild/obs-telemetry/react';

Telemetry.init({
  endpoint: '',                        // same origin → POST /api/telemetry/v1/ingest
  app: 'web',                          // becomes service_name "frontend/web"
  appVersion: import.meta.env.VITE_APP_VERSION,
  getToken: () => auth.accessToken,    // string | null (sync or async)
});
installErrorHandlers();                // uncaught errors, unhandled rejections, flush on page hide

createRoot(document.getElementById('root')!).render(
  <TelemetryErrorBoundary fallback={(error, reset) => <ErrorPage onRetry={reset} />}>
    <App />
  </TelemetryErrorBoundary>,
);
```

**Record events** anywhere:

```ts
Telemetry.log('checkout opened', { level: 'info', context: { cart_size: '3' } });
Telemetry.event('nav', { context: { route: location.pathname } });
Telemetry.perf('search', { durationMs: 182 });
Telemetry.error(err, { message: 'save failed' });
await Telemetry.flush();               // optional: send now
```

**Link a frontend error to the backend trace**, so both show up together in Grafana:

```ts
import { Telemetry, createTraceContext } from '@sentinelbuild/obs-telemetry';

const trace = createTraceContext();
try {
  const res = await fetch('/api/jobs', { method: 'POST', headers: { traceparent: trace.traceparent }, body });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
} catch (e) {
  Telemetry.error(e, { message: 'save job failed', traceId: trace.traceId, spanId: trace.spanId });
}
```

The backend's OTEL SDK continues the trace from the `traceparent` header. If the API is on a different origin, allow `traceparent` in its CORS `Access-Control-Allow-Headers`.

**Options:**

| Option | Default | Meaning |
|---|---|---|
| `endpoint` | — | Gateway base URL, or `''` for same origin |
| `app` | `web` | App name (letters, digits, `.`, `_`, `-`; max 64) |
| `appVersion` | — | Release/version string |
| `getToken` | — | Returns the user's bearer token, or null |
| `maxBatch` | 20 | Events per request (max 100) |
| `flushIntervalMs` | 5000 | Periodic send interval |
| `dedupeWindowMs` | 10000 | Identical events inside this window are sent once |
| `maxQueue` | 500 | Events kept for retry while the gateway is unreachable |
| `enabled` | true | Turn telemetry off (e.g. in tests) |

### 5.2 Flutter apps

Add the package from the repo:

```yaml
dependencies:
  obs_telemetry:
    path: ../path/to/clients/flutter/obs_telemetry
```

Then initialise it in `main.dart`:

```dart
import 'package:obs_telemetry/obs_telemetry.dart';

void main() {
  runGuarded(() {
    Telemetry.init(TelemetryConfig(
      endpoint: '',                       // same origin (web) or https://your-app.example.com
      app: 'mobile',
      appVersion: '1.4.2',
      getToken: () async => readAccessToken(),
    ));
    installErrorHandlers();               // FlutterError + PlatformDispatcher
    runApp(const MyApp());
  });
}

// anywhere:
Telemetry.log('checkout opened');
Telemetry.event('nav', context: {'route': '/jobs/42'});
Telemetry.error(e, stack: s, message: 'save failed', traceId: traceId, spanId: spanId);
```

`TelemetryConfig` accepts the same options as the React client (`maxBatch`, `maxQueue`, `flushInterval`, `dedupeWindow`, `enabled`).

On Flutter web, call `Telemetry.flush()` from your lifecycle hooks before the page unloads.

### 5.3 Any other client: the HTTP contract

```http
POST /api/telemetry/v1/ingest
Content-Type: application/json
Authorization: Bearer <jwt>        (optional)

{ "events": [ { ...event... }, ... ] }
```

| Field | Required | Rules |
|---|---|---|
| `type` | no (default `log`) | `log`, `error`, `event`, `perf` |
| `level` | no (default `info`) | `trace`, `debug`, `info`, `warn`/`warning`, `error`, `fatal`/`critical` (any case) |
| `message` | no | up to 2000 chars |
| `error` | no | up to 2000 chars |
| `stack` | no | up to 8000 chars |
| `url` | no | up to 2000 chars |
| `app` | no | up to 64 chars of `A-Z a-z 0-9 . _ -` |
| `app_version` | no | up to 64 chars |
| `trace_id` | no | 32 hex characters, not all zeros |
| `span_id` | no | 16 hex characters, not all zeros |
| `context` | no | object with string values; up to 32 keys, key ≤ 128 chars, value ≤ 1024 chars |

Limits per request: at most **100 events**, each event's JSON at most **16 KB**, and the whole body at most **100 × 16 KB**.

| Response | Meaning | Client should |
|---|---|---|
| `204` | Accepted | — |
| `413` | Body too large | Send smaller batches; don't retry as-is |
| `422` | An event or the batch broke a rule; **the whole batch was rejected**. The JSON body lists what failed | Fix the client; don't retry |
| `429` | Rate limit hit (default 600 requests/min per client IP) | Retry later |

A missing, expired or invalid token does **not** cause an error: the events are stored without tenant or user.

---

## 6. Configuration reference

The gateway reads settings from the environment. With Docker Compose, put them in `.env` (loaded by the `obs-gateway` service).

### 6.1 Gateway (`OBS_*`)

| Variable | Default | Description |
|---|---|---|
| `OBS_ENVIRONMENT` | `development` | `deployment.environment` on frontend logs (compose sets it from `ENVIRONMENT`) |
| `OBS_OTEL_ENDPOINT` | `http://otel-collector:4318` | Collector OTLP/HTTP base URL. Empty means print events to the gateway's stdout |
| `OBS_LOG_LEVEL` | `INFO` | Gateway's own log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `OBS_JWT_SECRET` | — | HMAC secret for verifying bearer tokens (use ≥ 32 random bytes) |
| `OBS_JWT_JWKS_URL` | — | JWKS URL for verifying RS/ES-signed tokens (instead of a secret) |
| `OBS_JWT_ALGORITHMS` | `HS256,RS256` | Allowed algorithms; comma-separated or JSON list. Set just the one your IdP uses |
| `OBS_JWT_AUDIENCE` | — | Required `aud` (checked only when set) |
| `OBS_JWT_ISSUER` | — | Required `iss` (checked only when set) |
| `OBS_JWT_COMPANY_CLAIM` | `org_id` | Claim holding the tenant id → `company_id` |
| `OBS_JWT_USER_CLAIM` | `sub` | Claim holding the user id → `user_id` |
| `OBS_INTERNAL_API_KEY` | — | When set, `GET /status` requires header `X-Internal-Key` with this value |
| `OBS_HEALTH_TARGETS` | `{}` | JSON object `{"name": "http://host:port"}` of services probed by `/status` |
| `OBS_HEALTH_PATH` | `/healthz` | Path appended to each target URL |
| `OBS_HEALTH_TIMEOUT_SECONDS` | `3` | Per-target probe timeout |
| `OBS_MAX_EVENT_BYTES` | `16384` | Maximum JSON size of one event |
| `OBS_MAX_BATCH_EVENTS` | `100` | Maximum events per request (body limit = this × `OBS_MAX_EVENT_BYTES`) |
| `OBS_RATE_LIMIT_PER_MIN` | `600` | Requests per client IP per sliding minute, **per gateway replica** |
| `OBS_DEDUPE_WINDOW_SECONDS` | `10` | Identical events from the same user or IP inside this window are stored once |
| `OBS_TRUSTED_PROXY_HOPS` | `0` | Number of reverse proxies whose `X-Forwarded-For` entries are trusted |
| `OBS_CORS_ALLOW_ORIGINS` | — | Comma-separated origins allowed to call the gateway cross-origin (prefer same origin) |
| `OBS_SERVICE_NAME` | `obs-gateway` | Name returned by `/health` |
| `PORT` | `8080` | Listen port inside the container |
| **Stack health and local fallback** | | |
| `OBS_STACK_TARGETS` | collector `:13133/`, Loki `/ready`, Tempo `/ready`, Prometheus `/-/ready`, Grafana `/api/health` | JSON `{component: health URL}` probed by the stack monitor. `{}` disables it |
| `OBS_STACK_CHECK_INTERVAL_SECONDS` | `15` | Probe interval |
| `OBS_FALLBACK_TRIGGER_COMPONENTS` | `otel-collector,loki,grafana` | While any of these is unhealthy, frontend events are also written to `events.jsonl` |
| `OBS_FALLBACK_LOG_DIR` | *(empty; compose sets `/var/log/obs`)* | Directory for `stack-health.jsonl` and `events.jsonl`. Empty disables the files |
| `OBS_FALLBACK_LOG_MAX_BYTES` | `10485760` | Rotate each file at this size |
| `OBS_FALLBACK_LOG_BACKUP_COUNT` | `5` | Rotated files kept |
| **RBAC** (§13) | | |
| `OBS_AUTHZ_MODE` | `none` | `none` (no RBAC), `static` (role map), `external` (your RBAC service). The secure overlay defaults to `static` |
| `OBS_JWT_ROLES_CLAIM` | `roles` | Claim holding the user's roles (list or comma/space-separated) |
| `OBS_AUTHZ_ROLE_PERMISSIONS` | `obs-admin: *`, `obs-editor`, `obs-viewer` (§13.2) | JSON `{role: [permissions]}` |
| `OBS_AUTHZ_GRAFANA_ROLES` | `obs-admin→Admin`, `obs-editor→Editor`, `obs-viewer→Viewer` | JSON `{role: Grafana role}` |
| `OBS_AUTHZ_URL` | — | External mode: RBAC decision endpoint |
| `OBS_AUTHZ_TIMEOUT_SECONDS` | `2` | External mode timeout (a timeout denies) |
| `OBS_AUTHZ_ROUTE_ACTIONS` | `/loki/`→`logs:read`, `/tempo/`→`traces:read`, `/prometheus/`→`metrics:read`, `/`→`ui:access` | JSON list of `[path prefix, action]` for forward-auth |
| `OBS_AUTHZ_TOKEN_COOKIE` | `obs_token` | Cookie carrying the bearer token for browser access |

### 6.2 Stack (`.env`, used by compose)

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | Default `deployment.environment` for all signals |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` | `admin` / `admin` | Grafana admin account, **applied on first start only** (see §11) |
| `GRAFANA_ROOT_URL` | `http://127.0.0.1:3000` | Public Grafana URL (production override) |
| `PROMETHEUS_RETENTION` | `15d` | Metrics retention |
| `OTEL_INGEST_TOKEN` | — | Production: bearer token required by the collector |
| `LOCAL_LOG_DIR` | `./logs` | Host directory for local log files: `obs-gateway/` and `otel-collector/` |
| `COLLECTOR_FALLBACK_MODE` | `failover` | Backend-log fallback: `failover`, `mirror` or `queue` (§7.3) |
| `COLLECTOR_FALLBACK_MAX_MB` | `50` | Collector `logs.jsonl` rotation size |
| `COLLECTOR_FALLBACK_MAX_BACKUPS` | `5` | Rotated collector files kept |
| `GRAFANA_AUTH_PROXY_WHITELIST` | private IP ranges | Secure overlay: addresses Grafana trusts identity headers from |

### 6.3 Retention

| Signal | Change in | Setting |
|---|---|---|
| Logs | `loki/config.yml` | `limits_config.retention_period` (default `720h`) |
| Traces | `tempo/config.yml` | `compactor.compaction.block_retention` (default `336h`) |
| Metrics | `.env` | `PROMETHEUS_RETENTION` (default `15d`) |

---

## 7. Fleet and stack health

The platform has health checks at every layer:

| What | How to see it |
|---|---|
| Each container (gateway, Loki, Tempo, Prometheus, Grafana) | `docker compose ps` shows `(healthy)`; services start only after the ones they depend on are healthy |
| Every platform component, including the collector (no container healthcheck possible) | `GET /status/stack`, metric `obs_stack_component_up`, file `stack-health.jsonl` (§7.2) |
| Your own services | `GET /status` (§7.1) |
| Anything Prometheus scrapes | `up` in Prometheus/Grafana |

### 7.1 Fleet health (`/status`)

Configure the services to probe:

```bash
OBS_HEALTH_TARGETS={"user-master":"http://user-master:8000","billing":"http://billing:8000"}
OBS_INTERNAL_API_KEY=some-long-random-value
```

Then query:

```bash
curl -s -H "X-Internal-Key: some-long-random-value" http://127.0.0.1:8080/status
```

```json
{
  "status": "degraded",
  "healthy": 1,
  "total": 2,
  "services": [
    {"service": "billing", "ok": false, "error": "ConnectError"},
    {"service": "user-master", "ok": true, "status_code": 200}
  ]
}
```

- HTTP **200** means every target answered `200` on `<url>/healthz` within the timeout; **503** means at least one did not.
- Each unhealthy entry shows either the `status_code` it returned or the `error` type (e.g. `ConnectError`, `ReadTimeout`).
- **401** means the key is missing or wrong.
- Point an uptime monitor or load-balancer health check at `/status` for a fleet-level signal. For the gateway's own liveness and readiness use `/health` and `/healthz`, which need no key.
- With RBAC enabled (§13), `/status` also accepts a user token whose role grants `status:read`.

### 7.2 Stack health and local log files

The gateway checks the observability stack itself every 15 seconds:

```bash
curl -s -H "X-Internal-Key: some-long-random-value" http://127.0.0.1:8080/status/stack
```

```json
{
  "status": "degraded",
  "fallback_active": true,
  "fallback_log_dir": "/var/log/obs",
  "components": [
    {"component": "grafana", "healthy": false, "detail": "ConnectError", "checked_at": "2026-09-13T15:00:03.120+00:00"},
    {"component": "loki", "healthy": true, "detail": "HTTP 200", "checked_at": "2026-09-13T15:00:03.118+00:00"}
  ]
}
```

- `status` is `unknown` until the first check, then `ok` (HTTP 200) or `degraded` (HTTP 503).

**When something is unhealthy, the platform writes local log files** under `LOCAL_LOG_DIR` (default `./logs` next to the compose file), so you can still see what is happening without Grafana:

| File | Contains | Written |
|---|---|---|
| `logs/obs-gateway/stack-health.jsonl` | One line each time a component goes down or recovers | Always, on every change |
| `logs/obs-gateway/events.jsonl` | Every frontend event received | While the collector, Loki or Grafana is unhealthy (`OBS_FALLBACK_TRIGGER_COMPONENTS`) |
| `logs/otel-collector/logs.jsonl` | Backend and frontend OTLP log records | Depends on `COLLECTOR_FALLBACK_MODE` (§7.3) |

Read them with any tool, for example:

```bash
tail -f logs/obs-gateway/stack-health.jsonl
```

```bash
grep '"level": "error"' logs/obs-gateway/events.jsonl
```

Example health record:

```json
{"time": "2026-09-13T15:00:03.121+00:00", "component": "grafana", "state": "unhealthy", "healthy": false, "detail": "ConnectError", "previous_detail": "HTTP 200", "fallback_active": true}
```

Files rotate by size (`OBS_FALLBACK_LOG_MAX_BYTES` × `OBS_FALLBACK_LOG_BACKUP_COUNT`, `COLLECTOR_FALLBACK_MAX_MB` × `COLLECTOR_FALLBACK_MAX_BACKUPS`). Frontend events are still sent to the collector as well, so when only Grafana was down the same events are also in Loki.

### 7.3 Collector fallback modes

Set `COLLECTOR_FALLBACK_MODE` in `.env`, then run `docker compose up -d otel-collector`:

| Mode | What happens to logs when Loki is unreachable | Choose when |
|---|---|---|
| `failover` (default) | Written to `logs/otel-collector/logs.jsonl` until Loki is back (retried every 30 s), then delivery switches back automatically | You want readable local logs only during outages |
| `mirror` | Always written to Loki **and** `logs.jsonl` | You always want a local copy, e.g. for a log shipper |
| `queue` | Loki and Tempo exports buffer to disk in `logs/otel-collector/queue/` and replay when the store recovers, even across collector restarts | You want no gaps in Loki/Tempo and don't need to read the buffer |

In `failover` mode even a short Loki hiccup diverts that batch to the file; use `queue` if you prefer a delay over a split.

### 7.4 Alerting on platform health

Useful Grafana alert rules:

- `obs_stack_component_up == 0` for 2 minutes — a platform component is down.
- `obs_fallback_active == 1` — telemetry is being written locally.
- `increase(obs_fallback_events_total[10m]) > 0`.

---

## 8. Production deployment

### 8.1 Use the production override

```bash
# .env — set at minimum:
ENVIRONMENT=production
GRAFANA_ADMIN_PASSWORD=<strong password>
GRAFANA_ROOT_URL=https://grafana.example.com
OTEL_INGEST_TOKEN=<long random token>
OBS_INTERNAL_API_KEY=<long random key>
OBS_JWT_JWKS_URL=https://login.example.com/.well-known/jwks.json
OBS_JWT_ALGORITHMS=RS256
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The override **refuses to start** if `GRAFANA_ADMIN_PASSWORD`, `OTEL_INGEST_TOKEN` or `OBS_INTERNAL_API_KEY` is missing. It then:

- requires the token on all OTLP ingest,
- opens the OTLP ports to the network,
- stops publishing Prometheus,
- trusts one proxy hop for client IPs,
- adds resource limits and `restart: always`.

To also require login and roles for the UI and data access, add the secure overlay (§13):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.secure.yml up -d
```

### 8.2 Put TLS in front

Terminate HTTPS on a reverse proxy or load balancer:

- `https://grafana.example.com` → `127.0.0.1:3000`
- your app's origin `/api/telemetry/` → `127.0.0.1:8080`
- `otlp.example.com:443` → `4318` (and gRPC → `4317`) for services on other hosts

Do not expose Loki, Tempo or Prometheus to the internet. They have no authentication of their own.

### 8.3 Services sending to a token-protected collector

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.example.com
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<OTEL_INGEST_TOKEN>
```

Requests without a valid token are rejected with `401 Unauthenticated`.

### 8.4 Scaling and durability

| Topic | Guidance |
|---|---|
| Gateway | Stateless: run several replicas behind a load balancer. Rate-limit and dedupe counters are per replica, so the effective limit is replicas × `OBS_RATE_LIMIT_PER_MIN`. |
| Loki / Tempo | The bundled config stores data on local disk. For production volume or HA, switch `storage` to S3/GCS/Azure and run Grafana's distributed or simple-scalable modes. |
| Grafana | Its SQLite database lives in the `grafana-data` volume. For several Grafana instances, configure an external database. |
| Backups | Back up the `grafana-data` volume (users, edited dashboards, alerts). Logs, traces and metrics are normally treated as reproducible and expire by retention. |

### 8.5 Alerting

The platform ships no alert rules. Create alerts in **Grafana → Alerting**, for example on `sum(rate(http_request_duration_seconds_count{status=~"5.."}[5m])) by (job)` or on a LogQL error count. Connect a contact point (Slack, PagerDuty, email).

---

## 9. Operations

### 9.1 Raising gateway verbosity during an incident

```bash
# .env
OBS_LOG_LEVEL=DEBUG
```

```bash
docker compose up -d obs-gateway
docker compose logs -f obs-gateway
```

Useful gateway log messages:

| Message | Meaning |
|---|---|
| `jwt_decode_failed: <reason>` (INFO) | A token was rejected; the event was stored anonymously |
| `otlp_emit_failed: <reason>` (WARNING) | An event could not be handed to the exporter |
| `client_telemetry …` (INFO) | `OBS_OTEL_ENDPOINT` is empty, so events are only printed |

### 9.2 Monitoring the platform itself

Prometheus scrapes every platform component (`up{stack="observability"}`):

| Metric | Watch for |
|---|---|
| `up == 0` | A component is down |
| `otelcol_exporter_send_failed_log_records_total`, `…_spans_total` | Collector can't reach Loki or Tempo |
| `otelcol_receiver_refused_*` | Collector memory limiter is shedding load |
| `http_request_duration_seconds_count{job="obs-gateway",status="429"}` | Clients are being rate limited |
| `obs_ingest_events_total{outcome="duplicate"}` | A client is in an error loop |
| `obs_stack_component_up == 0` | A platform component fails its health check |
| `obs_fallback_active == 1` | Frontend events are being written to local files |

### 9.3 Upgrading component versions

Image versions are pinned in `docker-compose.yml`. Change one tag at a time, read that project's upgrade notes (Loki and Tempo config keys change between minors), then run:

```bash
docker compose up -d <service>
```

Run the smoke test (§10.2) afterwards.

---

## 10. Running the tests and linters

Nothing needs to be installed locally except Docker.

### 10.1 Unit tests (with their linters)

```bash
docker compose -f docker-compose.test.yml run --rm --build gateway-tests
```

```bash
docker compose -f docker-compose.test.yml run --rm --build react-client-tests
```

```bash
docker compose -f docker-compose.test.yml run --rm --build react-demo-lint
```

```bash
docker compose -f docker-compose.test.yml run --rm --build flutter-client-tests
```

- **Gateway:** ruff, `mypy --strict`, and pytest with a 90 % coverage floor.
- **React client:** ESLint, typecheck, vitest and build.
- **React demo:** ESLint and typecheck.
- **Flutter:** analyze and test.

Each command exits non-zero on failure.

With local toolchains instead:

- gateway: `pip install -e "gateway[dev]"`, then `ruff check src tests && mypy src && pytest` from `gateway/`
- React: `npm ci && npm run lint && npm test`
- Flutter: `flutter analyze && flutter test`

### 10.1a Repository linters

```bash
docker compose -f docker-compose.test.yml run --rm lint-dockerfiles
```

```bash
docker compose -f docker-compose.test.yml run --rm lint-yaml
```

```bash
docker compose -f docker-compose.test.yml run --rm lint-markdown
```

Their settings live in `.hadolint.yaml`, `.yamllint.yaml` and `.markdownlint-cli2.jsonc`.

### 10.2 End-to-end smoke test

```bash
docker compose -f docker-compose.yml -f docker-compose.smoke.yml run --rm --build smoke-test
```

```bash
docker compose -f docker-compose.yml -f docker-compose.smoke.yml down -v
```

This starts the full stack with test settings. It sends a real backend trace and logs plus frontend events, then checks Tempo, Loki, Grafana, Prometheus, `/status`, `/status/stack` and the ingest limits. It prints `PASS`/`FAIL` per check and exits non-zero if any fail. Allow 2–3 minutes.

With the smoke stack still running, two more scripts need bash and curl (Git Bash works on Windows):

```bash
tests/smoke/outage_test.sh
```

```bash
tests/smoke/rbac_edge_test.sh
```

- **`outage_test.sh`** stops Loki and Grafana, checks that the outage is detected and that events land in the local log files, restarts them, and checks recovery.
- **`rbac_edge_test.sh`** starts the secure overlay and checks 401, 403 and role-based access through the edge.

The test catalogue is [TEST_CASES.md](TEST_CASES.md). CI (`.github/workflows/ci.yml`) runs all of the above on every push and pull request.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Can't log in to Grafana after changing `GRAFANA_ADMIN_PASSWORD` | The password is only applied when the `grafana-data` volume is first created | `docker compose exec grafana grafana cli admin reset-admin-password <new>` |
| `docker compose up` fails: `port is already allocated` | Another program uses 3000/8080/9090/4317/4318 | Stop it, or change the host side of `ports:` in `docker-compose.yml` |
| Frontend requests return **422** | Invalid field (unknown `level`, `app` with spaces, too-long text, >32 context keys, >100 events) | Read the response body; it names the field. The client libraries prevent this automatically |
| Frontend requests return **429** for many users at once | Gateway sees the proxy IP for everyone | Set `OBS_TRUSTED_PROXY_HOPS` to the number of proxies in front of the gateway |
| Frontend requests return **413** | Body larger than 100 × 16 KB | Send smaller batches (client libraries do this) |
| Browser console shows a CORS error | Frontend calls the gateway on another origin | Proxy `/api/telemetry/` through the app origin (§5), or set `OBS_CORS_ALLOW_ORIGINS` |
| 204 returned but nothing in Loki | Collector unreachable from the gateway, or wrong query | `docker compose logs obs-gateway` (look for `otlp_emit_failed`) and `docker compose logs otel-collector`; query `{service_name=~"frontend/.+"}` over the last 15 minutes |
| Frontend events missing tenant | No token, token invalid or expired, or verification not configured | Set `OBS_JWT_SECRET` or `OBS_JWT_JWKS_URL`; check `OBS_JWT_COMPANY_CLAIM`; look for `jwt_decode_failed` with `OBS_LOG_LEVEL=INFO` |
| Log line has no **TraceID** link | The log was written outside a traced request, or the service's logging isn't OTEL-instrumented | Enable logging instrumentation (§4.1); for frontend events pass `traceId`/`spanId` |
| **Overview** dashboard panels empty | Services don't expose `http_request_duration_seconds` | See §4.4; the `obs-gateway` job should always show data |
| **Service Graph** empty | Fewer than two services in traces, or less than 2 minutes of data | Send traced calls between services; wait |
| Service in another compose project can't reach `otel-collector` | Not on the same Docker network | Join `observability_default` (§4.2) |
| Collector returns **401** (production) | Missing or wrong OTLP token | Set `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<token>` |
| `/status` returns **401** | `OBS_INTERNAL_API_KEY` set | Send `X-Internal-Key` |
| `/status` returns `"note": "no targets configured"` | `OBS_HEALTH_TARGETS` empty or not valid JSON | Set it as a JSON object (§7) |
| Loki or Tempo stays `(health: starting)` for ~30 s | Normal ring warm-up | Wait; dependants start once they are healthy |
| `/status/stack` shows a component unhealthy but Grafana works | That component (e.g. Tempo) is down; it isn't a fallback trigger | `docker compose logs <component>`; `docker compose restart <component>` |
| No files appear in `logs/` during an outage | `OBS_FALLBACK_LOG_DIR` empty, the directory isn't writable, or no trigger component is down | Check `/status/stack` → `fallback_log_dir` and `fallback_active`; look for `fallback_log_unavailable` in the gateway log; on Linux ensure `log-dir-init` ran (`docker compose ps -a`) |
| `logs/otel-collector/logs.jsonl` keeps growing although Loki is up | `COLLECTOR_FALLBACK_MODE=mirror`, or repeated short Loki failures in `failover` mode | Expected for mirror; otherwise check `docker compose logs loki` |
| `obs-edge` returns **500** | The gateway is unreachable or too old to have `/authz/verify` | `docker compose ... up -d --build obs-gateway`; `docker compose logs obs-edge` |
| `obs-edge` returns **401** | No token, or an invalid or expired one | Send `Authorization: Bearer <jwt>` or set the `obs_token` cookie; check the gateway's JWT settings |
| `obs-edge` returns **403** | The token's roles don't grant the action (see the `X-Obs-Authz-Reason` header on the gateway's `/authz/verify` response) | Adjust the IdP roles or `OBS_AUTHZ_ROLE_PERMISSIONS` |
| Grafana shows everyone as the wrong role in the secure overlay | Role mapping missing | Set `OBS_AUTHZ_GRAFANA_ROLES` for your role names |

---

## 12. FAQ

**The compose file says Loki uses `/etc/loki/config.yml`, but there's no `etc` folder in the repo. Where is it?**
That path is inside the Loki container. Compose mounts the repo file `loki/config.yml` there (`./loki/config.yml:/etc/loki/config.yml:ro`). The same applies to Tempo, Prometheus and the collector; see §2.7.

**Do my backend services need the gateway?**
No. Services send OTLP directly to the collector. The gateway is only for browsers and mobile apps, plus the `/status` fleet view.

**Is anonymous telemetry accepted?**
Yes. Crashes before login must be captured. Events without a valid token are stored without `company_id`/`user_id`.

**How do I see only one customer's data?**
Filter logs with `| company_id="<tenant>"` in LogQL. Traces can be searched by the `company_id` span attribute if your services set it. Metrics intentionally have no tenant dimension.

**Can I turn telemetry off in a frontend build?**
Pass `enabled: false` in the client config. All calls become no-ops.

**Does the platform store personal data?**
Only what you send. The gateway adds the tenant and user ids from the token, and caps every field. Avoid putting emails or other PII in messages or `context`. Local fallback files contain the same data: protect `LOCAL_LOG_DIR` accordingly.

**Where do I look if Grafana itself is down?**
In `logs/obs-gateway/stack-health.jsonl` (what is down, and since when) and `logs/obs-gateway/events.jsonl` (frontend events received meanwhile), plus `logs/otel-collector/logs.jsonl` if Loki is also down (§7.2).

---

## 13. Securing access (RBAC)

By default (development) anyone who can reach the loopback ports can use Grafana and the APIs. The **secure overlay** puts the Grafana UI and log, trace and metric access behind authentication and role-based authorization, with a seam for plugging in your organisation's RBAC solution.

### 13.1 Turn it on

1. Configure token verification in `.env` (`OBS_JWT_JWKS_URL` for your IdP, or `OBS_JWT_SECRET`). Make sure tokens carry the tenant (`org_id`), user (`sub`) and a `roles` claim.
2. Start the stack with the overlay:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.secure.yml up -d
   ```

3. Use <http://127.0.0.1:8443> (the **obs-edge** proxy) instead of port 3000. Grafana's own port and login form are disabled; users are created automatically from the token.

| Through obs-edge | Needs permission | Goes to |
|---|---|---|
| `/` | `ui:access` | Grafana, signed in as the token's user with the mapped role |
| `/loki/…` | `logs:read` | Loki HTTP API, with `X-Scope-OrgID` set to the caller's tenant |
| `/tempo/…` | `traces:read` | Tempo HTTP API, with `X-Scope-OrgID` |
| `/prometheus/…` | `metrics:read` | Prometheus HTTP API |

Authenticate with a header or a cookie:

```bash
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8443/loki/loki/api/v1/labels"
```

For browsers, have your login flow set the `obs_token` cookie (name configurable with `OBS_AUTHZ_TOKEN_COOKIE`) for the edge's domain.

The gateway's `/status` and `/status/stack` also require `status:read` when RBAC is on; services can keep using `X-Internal-Key`. Telemetry ingest stays open, because pre-login crashes must still be captured.

### 13.2 Roles (static mode)

`OBS_AUTHZ_MODE=static` (the overlay default) maps token roles to permissions:

| Role | Permissions | Grafana role |
|---|---|---|
| `obs-admin` | everything (`*`), including other tenants | Admin |
| `obs-editor` | `ui:access`, `logs:read`, `traces:read`, `metrics:read`, `status:read` | Editor |
| `obs-viewer` | `ui:access`, `logs:read`, `traces:read`, `metrics:read` | Viewer |

Rename or extend these to match your IdP:

```bash
OBS_AUTHZ_ROLE_PERMISSIONS={"sre":["*"],"developer":["ui:access","logs:read","traces:read"]}
OBS_AUTHZ_GRAFANA_ROLES={"sre":"Admin","developer":"Viewer"}
```

A user with several roles gets the union of their permissions and the highest Grafana role.

### 13.3 Integrating your RBAC solution (external mode)

Set `OBS_AUTHZ_MODE=external` and `OBS_AUTHZ_URL`. For each decision the gateway sends:

```http
POST <OBS_AUTHZ_URL>
Content-Type: application/json

{"subject": {"user_id": "vera", "company_id": "acme", "roles": ["obs-viewer"]}, "action": "logs:read", "tenant": null}
```

It expects:

```json
{"allowed": true, "reason": "policy obs-read", "grafana_role": "Viewer"}
```

- Anything else denies: an error, a timeout (`OBS_AUTHZ_TIMEOUT_SECONDS`), a non-200 status, or `allowed` that isn't `true`.
- If your product has a different API (OpenFGA `check`, OPA `data.<package>.allow`, Cerbos, an internal service), adapt `ExternalAuthorizer` in `gateway/src/obs_gateway/authz.py`, or write a new class with the same `authorize(principal, action, tenant)` method and select it in `get_authorizer()`.
- To protect more paths through the edge, add `location` blocks to `edge/nginx.conf` and prefixes to `OBS_AUTHZ_ROUTE_ACTIONS`.

### 13.4 Per-tenant log isolation

The edge already sends each caller's tenant as `X-Scope-OrgID` to Loki and Tempo. To make the stores **enforce** it:

1. Set `auth_enabled: true` in `loki/config.yml` and enable multitenancy in `tempo/config.yml`.
2. Have the collector write each tenant's data with the matching `X-Scope-OrgID` (for example a `headers_setter` extension keyed on `company_id`).
3. Configure Grafana datasources per tenant, or use a tenant-aware Grafana setup.

Until then, tenant filtering in Grafana is by query (`| company_id="acme"`) and store APIs are protected by role only.
