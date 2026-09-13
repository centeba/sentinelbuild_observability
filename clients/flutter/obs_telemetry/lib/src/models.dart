/// Telemetry event model + config. Kept dependency-free so it is easy to test
/// and to port. Field caps mirror the gateway's Pydantic limits.
library;

/// Severity levels, aligned with the gateway/OTEL severities.
enum TelemetryLevel { debug, info, warning, error, critical }

extension TelemetryLevelName on TelemetryLevel {
  String get wire => switch (this) {
        TelemetryLevel.debug => 'debug',
        TelemetryLevel.info => 'info',
        TelemetryLevel.warning => 'warning',
        TelemetryLevel.error => 'error',
        TelemetryLevel.critical => 'critical',
      };
}

/// One client telemetry event. Matches the gateway ingest contract.
class TelemetryEvent {
  TelemetryEvent({
    required this.type,
    this.level = TelemetryLevel.info,
    this.message = '',
    this.error,
    this.stack,
    this.url,
    Map<String, String>? context,
  }) : context = context ?? const {};

  /// One of: log | error | event | perf.
  final String type;
  final TelemetryLevel level;
  final String message;
  final String? error;
  final String? stack;
  final String? url;
  final Map<String, String> context;

  static String? _cap(String? v, int max) =>
      (v != null && v.length > max) ? v.substring(0, max) : v;

  Map<String, dynamic> toJson({String? app, String? appVersion}) => {
        'type': type,
        'level': level.wire,
        'message': _cap(message, 2000) ?? '',
        if (error != null) 'error': _cap(error, 2000),
        if (stack != null) 'stack': _cap(stack, 8000),
        if (url != null) 'url': _cap(url, 2000),
        if (app != null) 'app': app,
        if (appVersion != null) 'app_version': appVersion,
        if (context.isNotEmpty)
          'context': Map.fromEntries(
            context.entries.take(32).map((e) => MapEntry(e.key, e.value)),
          ),
      };

  /// Signature for short-window dedupe.
  String get signature => '$type|$message|${stack ?? ''}';
}

/// Configuration for [Telemetry].
class TelemetryConfig {
  const TelemetryConfig({
    required this.endpoint,
    this.app = 'web',
    this.appVersion,
    this.getToken,
    this.maxBatch = 20,
    this.flushInterval = const Duration(seconds: 5),
    this.dedupeWindow = const Duration(seconds: 10),
    this.enabled = true,
  });

  /// Same-origin base path (recommended) or absolute URL of the gateway.
  /// Events POST to `$endpoint/api/telemetry/v1/ingest`.
  final String endpoint;
  final String app;
  final String? appVersion;

  /// Returns the current bearer token (or null when logged out). Called per
  /// flush so a login mid-session starts enriching events.
  final Future<String?> Function()? getToken;

  final int maxBatch;
  final Duration flushInterval;
  final Duration dedupeWindow;
  final bool enabled;
}
