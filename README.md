# Observability Platform

A self-contained, end-to-end observability stack — **logs, metrics, traces, and
frontend RUM** — for a fleet of services and their web/mobile frontends. Runs
standalone with one `docker compose up`, talks to apps only over **standard
OTLP**, and is designed to be spun off into its own open-source repository (this
directory is self-contained: no imports from any host platform).

## What's in the box

| Component | Role |
|---|---|
| **obs-gateway** (`gateway/`) | Stateless FastAPI service: ingests **frontend/app** telemetry (RUM, errors, logs), enriches it (tenant/user from an optional JWT), and forwards it to the collector as OTLP. Also aggregates **fleet health** (`GET /status`). The trust boundary browsers talk to. |
| **OTEL Collector** | Single OTLP ingest (gRPC 4317 / HTTP 4318) → fans out to the three stores. |
| **Loki** | Log store. |
| **Tempo** | Trace store. |
| **Prometheus** | Metrics store (scrape + OTLP remote-write). |
| **Grafana** | Single pane; datasources pre-wired with **trace↔log correlation**. |

## Quick start

```bash
cd observability
cp .env.example .env          # change Grafana admin creds before any non-local run
docker compose up -d
```

- Grafana → http://127.0.0.1:3000 (default `admin`/`admin`)
- Gateway → http://127.0.0.1:8080 (`/health`, `/status`, `/api/telemetry/v1/ingest`)
- Prometheus → http://127.0.0.1:9090

## Integrate your services (emit)

Point any OTEL-instrumented service at the collector — nothing platform-specific:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
```

Metrics can be scraped by adding the service to `prometheus/prometheus.yml`, or
pushed as OTLP through the collector.

## Integrate your frontend (RUM)

Send batched client events to the gateway (same-origin, via your app's reverse
proxy is recommended — see `docs/DESIGN.md`):

```
POST /api/telemetry/v1/ingest
{ "events": [ { "type": "error", "level": "error", "message": "...", "stack": "...", "url": "...", "app": "web" } ] }
```

Attach `Authorization: Bearer <jwt>` when the user is logged in and the gateway
will enrich events with tenant/user; anonymous (pre-login) events are accepted too.

## Logging levels

Everything is level-aware and env-configurable — see the "Logging levels" section
of [`docs/DESIGN.md`](docs/DESIGN.md). The gateway's own verbosity is
`OBS_LOG_LEVEL` (`DEBUG|INFO|WARNING|ERROR|CRITICAL`); ingested client events
carry a per-event `level` mapped to OTEL severity.

## Configuration

All gateway settings use the `OBS_` prefix; see [`.env.example`](.env.example)
for the full list. Requirements and design live in [`docs/`](docs/).

## License

MIT — see [`LICENSE`](LICENSE). The gateway and client code in this repo are MIT.

### Third-party components

The bundled stack runs upstream Docker images under their own licenses (used
unmodified, not vendored): **OpenTelemetry Collector** and **Prometheus**
(Apache-2.0); **Grafana**, **Loki**, and **Tempo** (AGPL-3.0). Running these
unmodified images imposes no license obligation on your own code; obligations
attach only if you modify and redistribute them.
