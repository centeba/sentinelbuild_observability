/// Static facade over a single [TelemetryClient] instance.
library;

import 'client.dart';
import 'models.dart';

/// App-wide entry point. Call [init] once at startup, then use [log]/[event]/
/// [perf]/[error] anywhere. All methods are no-ops until [init] runs, so calls
/// during early boot are safe.
class Telemetry {
  Telemetry._();

  static TelemetryClient? _client;

  static void init(TelemetryConfig config) {
    _client?.dispose();
    _client = TelemetryClient(config)..start();
  }

  static bool get isInitialized => _client != null;

  static void log(
    String message, {
    TelemetryLevel level = TelemetryLevel.info,
    Map<String, String>? context,
    String? url,
    String? traceId,
    String? spanId,
  }) {
    _client?.enqueue(TelemetryEvent(
      type: 'log',
      level: level,
      message: message,
      context: context,
      url: url,
      traceId: traceId,
      spanId: spanId,
    ));
  }

  static void event(String name, {Map<String, String>? context, String? url}) {
    _client?.enqueue(TelemetryEvent(
      type: 'event',
      message: name,
      context: context,
      url: url,
    ));
  }

  static void perf(String name, {Map<String, String>? context}) {
    _client?.enqueue(TelemetryEvent(type: 'perf', message: name, context: context));
  }

  /// Report an error. Pass the [traceId]/[spanId] of the failed backend call
  /// (the values sent in its `traceparent` header) to link the two in Grafana.
  static void error(
    Object error, {
    StackTrace? stack,
    String? message,
    TelemetryLevel level = TelemetryLevel.error,
    Map<String, String>? context,
    String? url,
    String? traceId,
    String? spanId,
  }) {
    _client?.enqueue(TelemetryEvent(
      type: 'error',
      level: level,
      message: message ?? error.toString(),
      error: error.toString(),
      stack: stack?.toString(),
      context: context,
      url: url,
      traceId: traceId,
      spanId: spanId,
    ));
  }

  static Future<void> flush() => _client?.flush() ?? Future<void>.value();

  static void dispose() {
    _client?.dispose();
    _client = null;
  }
}
