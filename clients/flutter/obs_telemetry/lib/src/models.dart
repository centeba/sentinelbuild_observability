/// Telemetry event model + config. Kept dependency-free so it is easy to test
/// and to port. Caps mirror the gateway's ingest contract.
library;

import 'dart:convert';
import 'dart:math' show max;

/// Severity levels, aligned with the gateway/OTEL severities.
enum TelemetryLevel { trace, debug, info, warning, error, critical }

extension TelemetryLevelName on TelemetryLevel {
  String get wire => switch (this) {
        TelemetryLevel.trace => 'trace',
        TelemetryLevel.debug => 'debug',
        TelemetryLevel.info => 'info',
        TelemetryLevel.warning => 'warning',
        TelemetryLevel.error => 'error',
        TelemetryLevel.critical => 'critical',
      };
}

/// Gateway ingest limits (see docs/REQUIREMENTS.md §6.1).
abstract final class IngestLimits {
  static const maxBatchEvents = 100;
  static const maxEventBytes = 16384;
  static const message = 2000;
  static const error = 2000;
  static const stack = 8000;
  static const url = 2000;
  static const app = 64;
  static const contextEntries = 32;
  static const contextKey = 128;
  static const contextValue = 1024;
}

final _traceIdPattern = RegExp(r'^[0-9a-f]{32}$');
final _spanIdPattern = RegExp(r'^[0-9a-f]{16}$');
final _appPattern = RegExp(r'^[A-Za-z0-9._-]+$');

String? _validId(String? id, RegExp pattern) {
  if (id == null) return null;
  final v = id.trim().toLowerCase();
  if (!pattern.hasMatch(v) || RegExp(r'^0+$').hasMatch(v)) return null;
  return v;
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
    this.traceId,
    this.spanId,
    Map<String, String>? context,
  }) : context = context ?? const {};

  /// One of: log | error | event | perf.
  final String type;
  final TelemetryLevel level;
  final String message;
  final String? error;
  final String? stack;
  final String? url;

  /// W3C trace id (32 hex) of the backend request this event relates to.
  final String? traceId;

  /// W3C span id (16 hex) of the backend request this event relates to.
  final String? spanId;
  final Map<String, String> context;

  static String? _cap(String? v, int max) =>
      (v != null && v.length > max) ? v.substring(0, max) : v;

  static int _bytes(Map<String, dynamic> json) =>
      utf8.encode(jsonEncode(json)).length;

  /// Wire JSON with every gateway cap applied, including the whole-event byte
  /// limit: if still too large, `context` is dropped, then `stack` shortened.
  Map<String, dynamic> toJson({String? app, String? appVersion}) {
    final json = <String, dynamic>{
      'type': type,
      'level': level.wire,
      'message': _cap(message, IngestLimits.message) ?? '',
      if (error != null) 'error': _cap(error, IngestLimits.error),
      if (stack != null) 'stack': _cap(stack, IngestLimits.stack),
      if (url != null) 'url': _cap(url, IngestLimits.url),
      if (app != null && _appPattern.hasMatch(app))
        'app': _cap(app, IngestLimits.app),
      if (appVersion != null) 'app_version': _cap(appVersion, IngestLimits.app),
      if (_validId(traceId, _traceIdPattern) case final id?) 'trace_id': id,
      if (_validId(spanId, _spanIdPattern) case final id?) 'span_id': id,
      if (context.isNotEmpty)
        'context': Map.fromEntries(
          context.entries
              .where((e) => e.key.isNotEmpty)
              .take(IngestLimits.contextEntries)
              .map((e) => MapEntry(
                    _cap(e.key, IngestLimits.contextKey)!,
                    _cap(e.value, IngestLimits.contextValue)!,
                  )),
        ),
    };
    const limit = IngestLimits.maxEventBytes;
    if (_bytes(json) > limit) json.remove('context');
    if (_bytes(json) > limit && json['stack'] is String) {
      // Every char is >= 1 encoded byte, so cutting `over` chars is enough.
      final s = json['stack'] as String;
      final over = _bytes(json) - limit;
      json['stack'] = s.substring(0, max(0, s.length - over));
    }
    if (_bytes(json) > limit) {
      // Multi-byte text in message/error/url alone can still exceed the limit.
      json
        ..remove('stack')
        ..remove('url');
      json['message'] = _cap(json['message'] as String, 1000);
      if (json['error'] != null) json['error'] = _cap(json['error'] as String, 1000);
    }
    return json;
  }

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
    this.maxQueue = 500,
    this.flushInterval = const Duration(seconds: 5),
    this.dedupeWindow = const Duration(seconds: 10),
    this.enabled = true,
  });

  /// Same-origin base path (recommended) or absolute URL of the gateway.
  /// Events POST to `$endpoint/api/telemetry/v1/ingest`.
  final String endpoint;

  /// App name; becomes the `frontend/<app>` service in Grafana. Letters,
  /// digits, `.`, `_`, `-` only.
  final String app;
  final String? appVersion;

  /// Returns the current bearer token (or null when logged out). Called per
  /// flush so a login mid-session starts enriching events.
  final Future<String?> Function()? getToken;

  /// Events per request (clamped to 1..100, the gateway's batch limit).
  final int maxBatch;

  /// Events kept for retry while the gateway is unreachable; oldest dropped.
  final int maxQueue;
  final Duration flushInterval;
  final Duration dedupeWindow;
  final bool enabled;
}
