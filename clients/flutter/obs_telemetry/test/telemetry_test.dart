import 'dart:convert';
import 'dart:io' show SocketException;

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:obs_telemetry/obs_telemetry.dart';

List<Map<String, dynamic>> eventsOf(http.Request req) =>
    ((jsonDecode(req.body) as Map<String, dynamic>)['events'] as List)
        .cast<Map<String, dynamic>>();

/// Gateway double that records requests and answers with [statuses] in order
/// (last one repeats). A status of -1 throws a network error.
class FakeGateway {
  FakeGateway([this.statuses = const [204]]);

  final List<int> statuses;
  final requests = <http.Request>[];

  late final client = MockClient((req) async {
    requests.add(req);
    final i = requests.length - 1;
    final status = statuses[i < statuses.length ? i : statuses.length - 1];
    if (status == -1) throw const SocketException('unreachable');
    return http.Response('', status);
  });

  int get delivered => requests.fold(0, (n, r) => n + eventsOf(r).length);
}

TelemetryClient clientFor(FakeGateway gw, [TelemetryConfig? config]) =>
    TelemetryClient(
      config ?? const TelemetryConfig(endpoint: 'https://obs.example', app: 'web'),
      httpClient: gw.client,
    );

void main() {
  group('delivery', () {
    test('flush posts queued events to the ingest endpoint', () async {
      final gw = FakeGateway();
      final client = clientFor(gw);

      client.enqueue(TelemetryEvent(type: 'error', message: 'boom'));
      await client.flush();

      expect(gw.requests, hasLength(1));
      expect(gw.requests.first.url.path, '/api/telemetry/v1/ingest');
      final events = eventsOf(gw.requests.first);
      expect(events, hasLength(1));
      expect(events.first['message'], 'boom');
      expect(events.first['app'], 'web');
    });

    test('dedupes identical events within the window', () async {
      final gw = FakeGateway();
      final client = clientFor(gw);

      client.enqueue(TelemetryEvent(type: 'error', message: 'same', stack: 'x'));
      client.enqueue(TelemetryEvent(type: 'error', message: 'same', stack: 'x'));
      await client.flush();

      expect(gw.delivered, 1);
    });

    test('attaches bearer token when provided', () async {
      final gw = FakeGateway();
      final client = clientFor(
        gw,
        TelemetryConfig(
          endpoint: 'https://obs.example',
          getToken: () async => 'tok-123',
        ),
      );
      client.enqueue(TelemetryEvent(type: 'log', message: 'hi'));
      await client.flush();
      expect(gw.requests.single.headers['authorization'], 'Bearer tok-123');
    });

    test('a throwing getToken still delivers anonymously', () async {
      final gw = FakeGateway();
      final client = clientFor(
        gw,
        TelemetryConfig(
          endpoint: 'https://obs.example',
          getToken: () async => throw StateError('storage locked'),
        ),
      );
      client.enqueue(TelemetryEvent(type: 'error', message: 'pre-login crash'));
      await client.flush();
      expect(gw.delivered, 1);
      expect(gw.requests.single.headers.containsKey('authorization'), isFalse);
    });

    test('sends in chunks of maxBatch', () async {
      final gw = FakeGateway();
      final client = clientFor(
        gw,
        const TelemetryConfig(endpoint: 'https://obs.example', maxBatch: 2),
      );
      for (var i = 0; i < 5; i++) {
        client.enqueue(TelemetryEvent(type: 'log', message: 'm$i'));
      }
      await client.flush();
      await client.flush();
      expect(gw.requests.map((r) => eventsOf(r).length), [2, 2, 1]);
    });

    test('maxBatch is clamped to the gateway batch limit', () async {
      final gw = FakeGateway();
      final client = clientFor(
        gw,
        const TelemetryConfig(endpoint: 'https://obs.example', maxBatch: 500, maxQueue: 1000),
      );
      for (var i = 0; i < 150; i++) {
        client.enqueue(TelemetryEvent(type: 'log', message: 'm$i'));
      }
      await client.flush();
      expect(gw.requests.map((r) => eventsOf(r).length), [100, 50]);
    });
  });

  group('retry', () {
    for (final status in [500, 503, 429, -1]) {
      test('re-queues on ${status == -1 ? 'network error' : status}', () async {
        final gw = FakeGateway([status, 204]);
        final client = clientFor(gw);
        client.enqueue(TelemetryEvent(type: 'error', message: 'retry me'));

        await client.flush();
        expect(client.pending, 1);

        await client.flush();
        expect(client.pending, 0);
        expect(gw.requests, hasLength(2));
        expect(eventsOf(gw.requests.last).single['message'], 'retry me');
      });
    }

    test('drops a batch the gateway rejects (422)', () async {
      final gw = FakeGateway([422]);
      final client = clientFor(gw);
      client.enqueue(TelemetryEvent(type: 'log', message: 'bad'));
      await client.flush();
      expect(client.pending, 0);
      await client.flush();
      expect(gw.requests, hasLength(1));
    });

    test('queue is bounded by maxQueue, dropping the oldest', () async {
      final gw = FakeGateway([-1]);
      final client = clientFor(
        gw,
        const TelemetryConfig(endpoint: 'https://obs.example', maxBatch: 100, maxQueue: 3),
      );
      for (var i = 0; i < 5; i++) {
        client.enqueue(TelemetryEvent(type: 'log', message: 'm$i'));
      }
      expect(client.pending, 3);
      await client.flush();
      expect(eventsOf(gw.requests.single).map((e) => e['message']), ['m2', 'm3', 'm4']);
    });

    test('disabled client neither queues nor sends', () async {
      final gw = FakeGateway();
      final client = clientFor(
        gw,
        const TelemetryConfig(endpoint: 'https://obs.example', enabled: false),
      );
      client.enqueue(TelemetryEvent(type: 'log', message: 'x'));
      await client.flush();
      expect(client.pending, 0);
      expect(gw.requests, isEmpty);
    });
  });

  group('wire caps', () {
    test('caps oversized fields', () {
      final json = TelemetryEvent(
        type: 'log',
        message: 'x' * 5000,
        error: 'e' * 5000,
        url: 'u' * 5000,
      ).toJson(appVersion: 'v' * 100);
      expect((json['message'] as String).length, IngestLimits.message);
      expect((json['error'] as String).length, IngestLimits.error);
      expect((json['url'] as String).length, IngestLimits.url);
      expect((json['app_version'] as String).length, 64);
    });

    test('caps context entries, keys and values', () {
      final json = TelemetryEvent(
        type: 'log',
        context: {
          '': 'dropped',
          'k' * 200: 'v' * 2000,
          for (var i = 0; i < 40; i++) 'key$i': 'v',
        },
      ).toJson();
      final context = json['context'] as Map<String, String>;
      expect(context, hasLength(IngestLimits.contextEntries));
      expect(context.containsKey(''), isFalse);
      expect(context.keys.first.length, IngestLimits.contextKey);
      expect(context.values.first.length, IngestLimits.contextValue);
    });

    test('shrinks an oversized event under the byte limit', () {
      final json = TelemetryEvent(
        type: 'error',
        message: 'm' * 2000,
        error: 'e' * 2000,
        stack: 's' * 8000,
        url: 'u' * 2000,
        context: {for (var i = 0; i < 32; i++) 'k$i': 'v' * 1024},
      ).toJson();
      expect(utf8.encode(jsonEncode(json)).length, lessThanOrEqualTo(IngestLimits.maxEventBytes));
      expect(json.containsKey('context'), isFalse);
      expect(json['message'], 'm' * 2000);
    });

    test('multi-byte text is shrunk by bytes, not chars', () {
      final json = TelemetryEvent(
        type: 'error',
        message: '界' * 2000,
        error: '界' * 2000,
        url: '界' * 2000,
      ).toJson();
      expect(utf8.encode(jsonEncode(json)).length, lessThanOrEqualTo(IngestLimits.maxEventBytes));
    });

    test('includes valid trace context lower-cased and drops invalid ids', () {
      final valid = TelemetryEvent(
        type: 'error',
        traceId: '4BF92F3577B34DA6A3CE929D0E0E4736',
        spanId: '00f067aa0ba902b7',
      ).toJson();
      expect(valid['trace_id'], '4bf92f3577b34da6a3ce929d0e0e4736');
      expect(valid['span_id'], '00f067aa0ba902b7');

      final invalid = TelemetryEvent(type: 'error', traceId: '0' * 32, spanId: 'xyz').toJson();
      expect(invalid.containsKey('trace_id'), isFalse);
      expect(invalid.containsKey('span_id'), isFalse);
    });

    test('omits an app name the gateway would reject', () {
      expect(TelemetryEvent(type: 'log').toJson(app: 'my app!').containsKey('app'), isFalse);
      expect(TelemetryEvent(type: 'log').toJson(app: 'my-app_1.0')['app'], 'my-app_1.0');
    });

    test('every level has a wire name the gateway accepts', () {
      const accepted = {'trace', 'debug', 'info', 'warning', 'error', 'critical'};
      expect(TelemetryLevel.values.map((l) => l.wire).toSet(), accepted);
    });
  });

  group('facade', () {
    tearDown(Telemetry.dispose);

    test('calls before init are no-ops', () async {
      expect(Telemetry.isInitialized, isFalse);
      Telemetry.log('early');
      Telemetry.error(StateError('early'));
      await Telemetry.flush();
    });

    test('error carries message, stack and trace ids', () {
      final event = TelemetryEvent(
        type: 'error',
        message: 'save failed',
        error: 'StateError',
        stack: StackTrace.current.toString(),
        traceId: 'a' * 32,
        spanId: 'b' * 16,
      ).toJson(app: 'web');
      expect(event['type'], 'error');
      expect(event['trace_id'], 'a' * 32);
      expect(event['stack'], isNotEmpty);
    });
  });
}
