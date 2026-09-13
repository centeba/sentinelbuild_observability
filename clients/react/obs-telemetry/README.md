# @sentinelbuild/obs-telemetry (React / browser client)

Self-contained browser client for the observability platform. Captures crashes,
errors, logs, and page/perf events and ships them **batched** and
**fire-and-forget** to the `obs-gateway` ingest endpoint. Zero runtime
dependencies; `react` (>=18) is a peer dependency used only by the
`@sentinelbuild/obs-telemetry/react` entry point. Mirrors the Flutter client
(`clients/flutter/obs_telemetry`).

## Install

The package is not published; depend on it from the repo and build it first:

```bash
cd clients/react/obs-telemetry && npm install && npm run build
```

```json
"dependencies": {
  "@sentinelbuild/obs-telemetry": "file:../../clients/react/obs-telemetry"
}
```

With Vite, add `resolve: { dedupe: ['react', 'react-dom'] }` so the linked
package uses the app's React.

## Use

```tsx
// main.tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Telemetry, installErrorHandlers } from '@sentinelbuild/obs-telemetry';
import { TelemetryErrorBoundary } from '@sentinelbuild/obs-telemetry/react';
import App from './App';

Telemetry.init({
  endpoint: '',                         // same-origin: POSTs to /api/telemetry/v1/ingest
  app: 'sentinel-build',
  appVersion: '1.4.2',
  getToken: () => readAccessToken(),    // string | null, sync or async
});
installErrorHandlers();                 // window error + unhandledrejection + flush on hide

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TelemetryErrorBoundary fallback={(error, reset) => <Crash error={error} onRetry={reset} />}>
      <App />
    </TelemetryErrorBoundary>
  </StrictMode>,
);
```

```ts
// anywhere:
Telemetry.log('checkout opened', { level: 'info' });
Telemetry.event('nav', { context: { route: '/jobs/42' } });
Telemetry.perf('search', { durationMs: 182 });   // context.duration_ms = "182"
Telemetry.error(e, { message: 'save failed' });
```

## Configuration

| Option            | Default | Notes                                                        |
| ----------------- | ------- | ------------------------------------------------------------ |
| `endpoint`        | —       | Gateway base URL, or `''` for same-origin (recommended)      |
| `app`             | `'web'` | ≤64 chars `[A-Za-z0-9._-]` (other chars are replaced by `_`) |
| `appVersion`      | —       | ≤64 chars                                                    |
| `getToken`        | —       | Called per flush; returns a bearer token or null             |
| `maxBatch`        | `20`    | Events per request, clamped to 1..100                        |
| `flushIntervalMs` | `5000`  |                                                              |
| `dedupeWindowMs`  | `10000` |                                                              |
| `maxQueue`        | `500`   | Bound on queued events while delivery is failing             |
| `enabled`         | `true`  |                                                              |

## Behaviour

- **Batched**: events queue and flush on `maxBatch` or every `flushIntervalMs`.
  `installErrorHandlers()` drains the queue when the page is hidden
  (`visibilitychange`) or unloaded (`pagehide`).
- **Deduped**: identical events (`type|message|stack`) within `dedupeWindowMs`
  are dropped (crash-loop safe).
- **Capped**: every field is truncated to the gateway's limits (message/error
  2000, stack 8000, url 2000, context 32 entries with keys 128 / values 1024);
  invalid trace/span ids are dropped; an event whose JSON is still over 16 KiB
  loses its `context`, then its `stack` is shortened until it fits — one bad
  event can never get the whole batch rejected.
- **Retried, bounded**: a network error, timeout (4 s), `429` or `5xx` puts the
  batch back at the front of the queue (oldest events dropped beyond
  `maxQueue`); other `4xx` such as `422`/`413` are dropped, since retrying cannot
  succeed.
- **Safe**: uses `fetch` directly (not app interceptors), swallows all delivery
  errors, and never reports errors raised while reporting — telemetry never
  throws into app code.
- **Tenant enrichment**: attaches `Authorization: Bearer <token>` from
  `getToken` when present; the gateway derives tenant/user from it. If
  `getToken` throws or rejects the batch is sent anonymously (anonymous is
  accepted for pre-login crashes).

## Transport

`POST ${endpoint}/api/telemetry/v1/ingest` with `{ "events": [ ... ] }`
(snake_case fields, `Content-Type: application/json`), at most 100 events and
100 × 16384 bytes per request. Responses: `204` accepted, `413` too large,
`422` invalid, `429` rate limited.

On page hide the queue is sent with `fetch(..., { keepalive: true })` in chunks
under 60 KB (browsers cap keepalive bodies at 64 KiB). Only a synchronous
`getToken` result can be used there; an async provider sends those final events
anonymously. Without `fetch`, `navigator.sendBeacon` is used (always anonymous —
beacons cannot carry headers).

Prefer a **same-origin** `endpoint` (empty string) so the host's reverse proxy
forwards `/api/telemetry/` to the gateway — no CORS/CSP changes needed. See
`examples/react-demo/nginx.conf` and the platform `docs/DESIGN.md`.

## Trace correlation

Start a W3C trace context in the browser, send it on the API call, and attach
the same ids to the error so the frontend event links to the backend trace in
Grafana:

```ts
import { Telemetry, createTraceContext } from '@sentinelbuild/obs-telemetry';

async function saveJob(job: Job) {
  const trace = createTraceContext();   // { traceId, spanId, traceparent }
  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', traceparent: trace.traceparent },
      body: JSON.stringify(job),
    });
    if (!res.ok) throw new Error(`save failed: HTTP ${res.status}`);
  } catch (e) {
    Telemetry.error(e, { message: 'save job failed', traceId: trace.traceId, spanId: trace.spanId });
    throw e;
  }
}
```

## Development

```bash
npm install
npm run typecheck
npm test          # vitest run (jsdom)
npm run build     # tsc -> dist/ (ESM + .d.ts)
```
