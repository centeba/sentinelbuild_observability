# obs_telemetry (Flutter client)

Self-contained Flutter/Dart client for the observability platform. Captures
crashes, errors, logs, and page/perf events and ships them **batched** and
**fire-and-forget** to the `obs-gateway` ingest endpoint. Dependency-light
(`package:http` only).

## Use

```dart
import 'package:obs_telemetry/obs_telemetry.dart';

void main() {
  runGuarded(() {
    Telemetry.init(TelemetryConfig(
      endpoint: '',                 // same-origin: POSTs to /api/telemetry/v1/ingest
      app: 'sentinel-build',
      appVersion: '1.4.2',
      getToken: () async => readAccessToken(),  // null when logged out
    ));
    installErrorHandlers();          // FlutterError + async + error-widget
    runApp(const MyApp());
  });
}

// anywhere:
Telemetry.log('checkout opened', level: TelemetryLevel.info);
Telemetry.event('nav', context: {'route': '/jobs/42'});
Telemetry.error(e, stack: s, message: 'save failed');
```

## Behaviour

- **Batched**: events queue and flush on `maxBatch` or every `flushInterval`.
  Call `Telemetry.flush()` from lifecycle hooks (e.g. on web `visibilitychange`/
  `beforeunload`) to drain before the page unloads.
- **Deduped**: identical events within `dedupeWindow` are dropped (crash-loop safe).
- **Capped**: message/stack/url sizes are truncated to the gateway's limits.
- **Safe**: uses its own `http.Client`, times out fast, and swallows all delivery
  errors — telemetry never throws into app code or recurses through app interceptors.
- **Tenant enrichment**: attaches the bearer token from `getToken` when present;
  the gateway derives tenant/user from it (anonymous accepted for pre-login crashes).

## Transport

`POST $endpoint/api/telemetry/v1/ingest` with `{ "events": [ ... ] }`. Prefer a
**same-origin** `endpoint` (empty string) so the host's reverse proxy forwards to
the gateway — no CORS/CSP changes needed. See the platform `docs/DESIGN.md`.
