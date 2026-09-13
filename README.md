# Observability Platform

A self-contained, end-to-end observability stack for a fleet of services and their web/mobile frontends, covering:

- logs, metrics, traces and frontend RUM,
- built-in health checks, with local log files when the stack is unhealthy,
- an RBAC seam for securing the UI and data access.

It runs standalone with one `docker compose up`, talks to apps only over **standard OTLP**, and is designed to be spun off as its own open-source project.

## What's in the box

| Component | Role |
|---|---|
| **obs-gateway** (`gateway/`) | Stateless FastAPI service. It ingests frontend telemetry, enriches it with tenant and user from an optional JWT, and forwards it as OTLP. It also aggregates fleet health (`/status`), monitors the stack itself (`/status/stack`), writes local fallback logs, and makes RBAC decisions (`/authz/verify`). |
| **OTEL Collector 0.160** | Single OTLP ingest → Loki, Tempo, Prometheus. When Loki is unreachable, logs fail over to a local file (modes `failover`, `mirror`, `queue`). |
| **Loki / Tempo / Prometheus** | Log, trace and metric stores (Tempo's metrics-generator powers the service graph). |
| **Grafana** | One pane with trace↔log correlation. |
| **obs-edge** (`edge/`, secure overlay) | nginx forward-auth proxy in front of Grafana and the store APIs. |
| **Clients** | `clients/react/obs-telemetry` (React/browser) and `clients/flutter/obs_telemetry` (Flutter). |
| **Demo** | `examples/react-demo`: React app wired to the gateway through a same-origin proxy. |

## Quick start

```bash
cp .env.example .env          # change Grafana admin creds before any non-local run
docker compose up -d
```

| URL | What |
|---|---|
| <http://127.0.0.1:3000> | Grafana (`admin`/`admin`) |
| <http://127.0.0.1:8080/status/stack> | Health of every stack component |
| <http://127.0.0.1:9090> | Prometheus |
| `127.0.0.1:4317` / `127.0.0.1:4318` | OTLP gRPC / HTTP for your services |

Try the demo app with `docker compose --profile demo up -d`, then open <http://127.0.0.1:5173>.

## Integrate

- **Backend services:** set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` (any OTEL SDK).
- **Frontends:** use the React or Flutter client, or `POST /api/telemetry/v1/ingest` with `{"events": [...]}` through a same-origin proxy. Attach `Authorization: Bearer <jwt>` to enrich events with tenant and user.

## Compose files

| File | Use |
|---|---|
| `docker-compose.yml` | Development stack (plus the `demo` profile) |
| `docker-compose.prod.yml` | Production: required secrets, OTLP token auth, resource limits |
| `docker-compose.secure.yml` | RBAC-secured UI and log/trace/metric access through `obs-edge` |
| `docker-compose.test.yml` | Unit tests and linters in Docker |
| `docker-compose.smoke.yml` | End-to-end smoke, outage and RBAC tests |

## When the stack is unhealthy

The gateway probes the collector, Loki, Tempo, Prometheus and Grafana every 15 s. Health changes are written to `logs/obs-gateway/stack-health.jsonl`. While the collector, Loki or Grafana is down, frontend events also go to `logs/obs-gateway/events.jsonl`, and backend logs go to `logs/otel-collector/logs.jsonl`. The directory is set with `LOCAL_LOG_DIR`.

## Tests and lint

```bash
docker compose -f docker-compose.test.yml run --rm --build gateway-tests
docker compose -f docker-compose.yml -f docker-compose.smoke.yml run --rm --build smoke-test
```

CI (`.github/workflows/ci.yml`) runs:

- the gateway suite (ruff, mypy, pytest),
- the React suite (ESLint, tsc, vitest) and the Flutter suite (analyze, test),
- hadolint, yamllint and markdownlint,
- config validation,
- the smoke, outage and RBAC edge tests.

## Documentation

- [Requirements](docs/REQUIREMENTS.md)
- [Technical design](docs/DESIGN.md)
- [User manual](docs/USER_MANUAL.md)
- [Test cases](docs/TEST_CASES.md)

## License

MIT — see [`LICENSE`](LICENSE). The gateway, clients and demo in this repo are MIT.

### Third-party components

The stack runs upstream Docker images unmodified, under their own licenses:

- **OpenTelemetry Collector** and **Prometheus**: Apache-2.0
- **Grafana**, **Loki** and **Tempo**: AGPL-3.0
- **nginx**: BSD-2-Clause

Running unmodified images imposes no license obligation on your own code.
