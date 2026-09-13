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
  Future<void>? _inFlight;

  int get _batchSize => config.maxBatch.clamp(1, IngestLimits.maxBatchEvents);

  /// Events waiting to be sent (including ones re-queued for retry).
  int get pending => _queue.length;

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
    _trimQueue();
    if (_queue.length >= _batchSize) {
      unawaited(flush());
    }
  }

  void _trimQueue() {
    if (_queue.length > config.maxQueue) {
      _queue.removeRange(0, _queue.length - config.maxQueue);
    }
  }

  /// Send everything queued, in gateway-sized chunks. Safe to call anytime,
  /// including from lifecycle hooks. Failed chunks that may succeed later
  /// (network error, timeout, 429, 5xx) are re-queued; rejected ones (other
  /// 4xx) are dropped.
  ///
  /// While a flush is in flight, callers get that same future, so awaiting
  /// `flush()` always waits for delivery to finish.
  Future<void> flush() {
    final inFlight = _inFlight;
    if (inFlight != null) return inFlight;
    if (_queue.isEmpty) return Future<void>.value();
    return _inFlight = _drain().whenComplete(() => _inFlight = null);
  }

  Future<void> _drain() async {
    final token = await _token();
    while (_queue.isNotEmpty) {
      final chunk = _queue.take(_batchSize).toList();
      _queue.removeRange(0, chunk.length);
      final done = await _send(chunk, token);
      if (!done) {
        _queue.insertAll(0, chunk);
        _trimQueue();
        return; // retry on the next flush
      }
    }
  }

  Future<String?> _token() async {
    try {
      return await config.getToken?.call();
    } catch (_) {
      // Pre-existing bug fixed: a throwing getToken dropped the whole batch.
      // Send anonymously instead — the gateway accepts it.
      return null;
    }
  }

  /// True when the chunk is done with (delivered, or permanently rejected).
  Future<bool> _send(List<TelemetryEvent> chunk, String? token) async {
    try {
      final body = jsonEncode({
        'events': chunk
            .map((e) => e.toJson(app: config.app, appVersion: config.appVersion))
            .toList(),
      });
      final response = await _http
          .post(
            Uri.parse('${config.endpoint}/api/telemetry/v1/ingest'),
            headers: {
              'content-type': 'application/json',
              if (token != null && token.isNotEmpty)
                'authorization': 'Bearer $token',
            },
            body: body,
          )
          .timeout(const Duration(seconds: 4));
      final code = response.statusCode;
      return !(code == 429 || code >= 500);
    } catch (_) {
      return false;
    }
  }

  void dispose() {
    _timer?.cancel();
    _timer = null;
    _started = false;
    _http.close();
  }
}
