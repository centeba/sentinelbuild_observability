/// obs_telemetry — Flutter client for the observability platform.
///
/// Quick start (in `main.dart`)::
///
///     void main() {
///       runGuarded(() {
///         Telemetry.init(TelemetryConfig(
///           endpoint: '',            // same-origin; POSTs to /api/telemetry/v1/ingest
///           app: 'sentinel-build',
///           getToken: () async => await readAccessToken(),
///         ));
///         installErrorHandlers();
///         runApp(const MyApp());
///       });
///     }
library;

export 'src/client.dart' show TelemetryClient;
export 'src/facade.dart' show Telemetry;
export 'src/handlers.dart' show installErrorHandlers, runGuarded;
export 'src/models.dart'
    show TelemetryConfig, TelemetryEvent, TelemetryLevel, TelemetryLevelName;
