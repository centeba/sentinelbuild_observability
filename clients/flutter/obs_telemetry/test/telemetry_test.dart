import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:obs_telemetry/obs_telemetry.dart';

void main() {
  test('flush posts queued events to the ingest endpoint', () async {
    final requests = <http.Request>[];
    final mock = MockClient((req) async {
      requests.add(req);
      return http.Response('', 204);
    });
    final client = TelemetryClient(
      const TelemetryConfig(endpoint: 'https://obs.example', app: 'web'),
      httpClient: mock,
    );

    client.enqueue(TelemetryEvent(type: 'error', message: 'boom'));
    await client.flush();

    expect(requests, hasLength(1));
    expect(requests.first.url.path, '/api/telemetry/v1/ingest');
    final body = jsonDecode(requests.first.body) as Map<String, dynamic>;
    final events = body['events'] as List;
    expect(events, hasLength(1));
    expect((events.first as Map)['message'], 'boom');
    expect((events.first as Map)['app'], 'web');
  });

  test('dedupes identical events within the window', () async {
    var posted = 0;
    final mock = MockClient((req) async {
      posted += (jsonDecode(req.body)['events'] as List).length;
      return http.Response('', 204);
    });
    final client = TelemetryClient(
      const TelemetryConfig(endpoint: 'https://obs.example'),
      httpClient: mock,
    );

    final ev = TelemetryEvent(type: 'error', message: 'same', stack: 'x');
    client.enqueue(ev);
    client.enqueue(TelemetryEvent(type: 'error', message: 'same', stack: 'x'));
    await client.flush();

    expect(posted, 1);
  });

  test('attaches bearer token when provided', () async {
    late http.Request seen;
    final mock = MockClient((req) async {
      seen = req;
      return http.Response('', 204);
    });
    final client = TelemetryClient(
      TelemetryConfig(
        endpoint: 'https://obs.example',
        getToken: () async => 'tok-123',
      ),
      httpClient: mock,
    );
    client.enqueue(TelemetryEvent(type: 'log', message: 'hi'));
    await client.flush();
    expect(seen.headers['authorization'], 'Bearer tok-123');
  });

  test('caps oversized fields', () {
    final ev = TelemetryEvent(type: 'log', message: 'x' * 5000);
    final json = ev.toJson();
    expect((json['message'] as String).length, 2000);
  });
}
