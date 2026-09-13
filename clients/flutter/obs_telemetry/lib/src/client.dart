/// The telemetry client: queue + batched, throttled, fire-and-forget delivery.
library;

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

/// Sends batches of [TelemetryEvent] to the obs-gateway. Never throws into the
/// caller and never blocks app code: enqueue returns immediately; delivery is
/// out-of-band and swallows all its own errors (a telemetry failure must not
/// become another error to report).
class TelemetryClient {
  TelemetryClient(this.config, {http.Client? httpClient})
      : _http = httpClient ?? http.Client();

  final TelemetryConfig config;
  final http.Client _http;

  final List<TelemetryEvent> _queue = [];
  final Map<String, DateTime> _recent = {};
  Timer? _timer;
  bool _started = false;

  void start() {
    if (_started || !config.enabled) return;
    _started = true;
    _timer = Timer.periodic(config.flushInterval, (_) => flush());
  }

  /// Enqueue an event (deduped within the configured window). Flushes eagerly
  /// once the batch is full.
  void enqueue(TelemetryEvent event) {
    if (!config.enabled) return;
    final now = DateTime.now();
    final sig = event.signature;
    final last = _recent[sig];
    if (last != null && now.difference(last) < config.dedupeWindow) return;
    _recent[sig] = now;
    if (_recent.length > 200) {
      _recent.removeWhere(
        (_, t) => now.difference(t) > config.dedupeWindow,
      );
    }
    _queue.add(event);
    if (_queue.length >= config.maxBatch) {
      unawaited(flush());
    }
  }

  /// Send everything queued (up to a sane cap per call). Safe to call anytime,
  /// including from lifecycle hooks (visibility/unload).
  Future<void> flush() async {
    if (_queue.isEmpty) return;
    final batch = List<TelemetryEvent>.from(_queue);
    _queue.clear();
    try {
      String? token;
      if (config.getToken != null) {
        token = await config.getToken!();
      }
      final body = jsonEncode({
        'events': batch
            .map((e) => e.toJson(app: config.app, appVersion: config.appVersion))
            .toList(),
      });
      final uri = Uri.parse('${config.endpoint}/api/telemetry/v1/ingest');
      await _http
          .post(
            uri,
            headers: {
              'content-type': 'application/json',
              if (token != null && token.isNotEmpty)
                'authorization': 'Bearer $token',
            },
            body: body,
          )
          .timeout(const Duration(seconds: 4));
    } catch (_) {
      // Fire-and-forget: drop on failure rather than recurse or grow unbounded.
    }
  }

  void dispose() {
    _timer?.cancel();
    _timer = null;
    _started = false;
    _http.close();
  }
}
