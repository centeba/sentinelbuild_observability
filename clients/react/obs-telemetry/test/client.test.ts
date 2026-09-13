import { afterEach, describe, expect, it, vi } from 'vitest';

import { TelemetryClient, type TelemetryConfig } from '../src/index.js';
import { mockFetch } from './helpers.js';

function makeClient(config: Partial<TelemetryConfig>, ...responses: Array<number | Error>) {
  const mock = mockFetch(...responses);
  const client = new TelemetryClient({ endpoint: 'https://obs.example', ...config }, { fetch: mock.fetch });
  return { client, ...mock };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('TelemetryClient.flush', () => {
  it('posts queued events to the ingest endpoint in the wire shape', async () => {
    const { client, calls } = makeClient({ app: 'web', appVersion: '1.0.0' });
    client.enqueue({ type: 'error', message: 'boom', traceId: 'ab'.repeat(16) });
    await client.flush();

    expect(calls).toHaveLength(1);
    const call = calls[0]!;
    expect(call.url).toBe('https://obs.example/api/telemetry/v1/ingest');
    expect(call.init.method).toBe('POST');
    expect(call.headers['Content-Type']).toBe('application/json');
    expect(call.headers['Authorization']).toBeUndefined();
    expect(call.events).toEqual([
      { type: 'error', level: 'info', message: 'boom', app: 'web', app_version: '1.0.0', trace_id: 'ab'.repeat(16) },
    ]);
    expect(client.queueLength).toBe(0);
  });

  it('uses a same-origin path when endpoint is empty', async () => {
    const { client, calls } = makeClient({ endpoint: '' });
    client.enqueue({ type: 'log', message: 'hi' });
    await client.flush();
    expect(calls[0]!.url).toBe('/api/telemetry/v1/ingest');
    expect(calls[0]!.events[0]!.app).toBe('web');
  });

  it('does nothing with an empty queue', async () => {
    const { client, calls } = makeClient({});
    await client.flush();
    expect(calls).toHaveLength(0);
  });

  it('dedupes identical events within the window', async () => {
    const { client, calls } = makeClient({ dedupeWindowMs: 10000 });
    client.enqueue({ type: 'error', message: 'same', stack: 'x' });
    client.enqueue({ type: 'error', message: 'same', stack: 'x' });
    client.enqueue({ type: 'error', message: 'same', stack: 'y' });
    await client.flush();
    expect(calls[0]!.events).toHaveLength(2);
  });

  it('accepts the same event again after the dedupe window', async () => {
    vi.useFakeTimers();
    const { client, calls } = makeClient({ dedupeWindowMs: 1000 });
    client.enqueue({ type: 'error', message: 'same' });
    vi.advanceTimersByTime(1500);
    client.enqueue({ type: 'error', message: 'same' });
    await client.flush();
    expect(calls[0]!.events).toHaveLength(2);
  });

  it('attaches the bearer token from getToken', async () => {
    const { client, calls } = makeClient({ getToken: () => Promise.resolve('tok-123') });
    client.enqueue({ type: 'log', message: 'hi' });
    await client.flush();
    expect(calls[0]!.headers['Authorization']).toBe('Bearer tok-123');
  });

  it('sends anonymously when getToken throws or rejects', async () => {
    for (const getToken of [
      () => {
        throw new Error('no session');
      },
      () => Promise.reject(new Error('refresh failed')),
    ]) {
      const { client, calls } = makeClient({ getToken });
      client.enqueue({ type: 'log', message: 'hi' });
      await client.flush();
      expect(calls).toHaveLength(1);
      expect(calls[0]!.headers['Authorization']).toBeUndefined();
      expect(calls[0]!.events).toHaveLength(1);
    }
  });

  it('sends in chunks of maxBatch', async () => {
    const { client, calls } = makeClient({ maxBatch: 3 });
    // The eager flush at 3 starts draining after its token await, so all 7 are queued by then.
    for (let i = 0; i < 7; i++) client.enqueue({ type: 'log', message: `m${i}` });
    await client.flush();
    await client.flush();
    expect(calls.map((c) => c.events.length)).toEqual([3, 3, 1]);
    expect(calls.flatMap((c) => c.events.map((e) => e.message))).toEqual(['m0', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6']);
  });

  it('flushes eagerly when a batch fills', async () => {
    const { client, calls } = makeClient({ maxBatch: 2 });
    client.enqueue({ type: 'log', message: 'a' });
    expect(calls).toHaveLength(0);
    client.enqueue({ type: 'log', message: 'b' });
    await client.flush();
    expect(calls).toHaveLength(1);
    expect(calls[0]!.events).toHaveLength(2);
  });

  it('clamps maxBatch to 1..100', () => {
    expect(makeClient({ maxBatch: 1000 }).client.maxBatch).toBe(100);
    expect(makeClient({ maxBatch: 0 }).client.maxBatch).toBe(1);
  });

  it.each([
    ['500', 500],
    ['503', 503],
    ['429', 429],
    ['network error', new TypeError('Failed to fetch')],
  ])('re-queues the chunk on %s', async (_name, failure) => {
    const { client, calls } = makeClient({}, failure, 204);
    client.enqueue({ type: 'log', message: 'a' });
    client.enqueue({ type: 'log', message: 'b' });
    await client.flush();
    expect(calls).toHaveLength(1);
    expect(client.queueLength).toBe(2);

    client.enqueue({ type: 'log', message: 'c' });
    await client.flush();
    expect(calls).toHaveLength(2);
    expect(calls[1]!.events.map((e) => e.message)).toEqual(['a', 'b', 'c']);
    expect(client.queueLength).toBe(0);
  });

  it.each([422, 413, 400, 401])('drops the chunk on %i', async (status) => {
    const { client, calls } = makeClient({}, status);
    client.enqueue({ type: 'log', message: 'a' });
    await client.flush();
    expect(calls).toHaveLength(1);
    expect(client.queueLength).toBe(0);
  });

  it('stops draining after a failed chunk and keeps the rest in order', async () => {
    const { client, calls } = makeClient({ maxBatch: 2 }, 204, 500, 204);
    for (let i = 0; i < 5; i++) client.enqueue({ type: 'log', message: `m${i}` });
    await client.flush();
    // [m0,m1] sent, [m2,m3] fails -> re-queued ahead of m4, and draining stops.
    expect(calls.map((c) => c.events.map((e) => e.message))).toEqual([['m0', 'm1'], ['m2', 'm3']]);
    expect(client.queueLength).toBe(3);
    await client.flush();
    expect(calls.slice(2).map((c) => c.events.map((e) => e.message))).toEqual([['m2', 'm3'], ['m4']]);
  });

  it('bounds the queue at maxQueue, dropping the oldest', async () => {
    const { client, calls } = makeClient({ maxQueue: 5, maxBatch: 100 }, 500, 204);
    for (let i = 0; i < 4; i++) client.enqueue({ type: 'log', message: `m${i}` });
    await client.flush(); // fails -> 4 re-queued
    for (let i = 4; i < 8; i++) client.enqueue({ type: 'log', message: `m${i}` });
    expect(client.queueLength).toBe(5);
    await client.flush();
    expect(calls[1]!.events.map((e) => e.message)).toEqual(['m3', 'm4', 'm5', 'm6', 'm7']);
  });

  it('aborts a request after 4 seconds and re-queues', async () => {
    vi.useFakeTimers();
    const fetchFn = vi.fn(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const client = new TelemetryClient({ endpoint: '' }, { fetch: fetchFn as unknown as typeof fetch });
    client.enqueue({ type: 'log', message: 'slow' });
    const done = client.flush();
    await vi.advanceTimersByTimeAsync(4000);
    await done;
    expect(client.queueLength).toBe(1);
  });

  it('never rejects even if fetch throws synchronously', async () => {
    const client = new TelemetryClient(
      { endpoint: '' },
      {
        fetch: (() => {
          throw new Error('sync');
        }) as unknown as typeof fetch,
      },
    );
    client.enqueue({ type: 'log', message: 'x' });
    await expect(client.flush()).resolves.toBeUndefined();
  });

  it('ignores everything when disabled', async () => {
    const { client, calls } = makeClient({ enabled: false });
    client.enqueue({ type: 'log', message: 'x' });
    await client.flush();
    expect(calls).toHaveLength(0);
  });
});

describe('TelemetryClient timer', () => {
  it('flushes periodically after start()', async () => {
    vi.useFakeTimers();
    const { client, calls } = makeClient({ flushIntervalMs: 5000 });
    client.start();
    client.enqueue({ type: 'log', message: 'tick' });
    await vi.advanceTimersByTimeAsync(4999);
    expect(calls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toHaveLength(1);

    client.dispose();
    client.enqueue({ type: 'log', message: 'after dispose' });
    await vi.advanceTimersByTimeAsync(20000);
    expect(calls).toHaveLength(1);
  });
});

describe('TelemetryClient.flushOnUnload', () => {
  it('uses fetch keepalive with chunks under 60KB and a sync token', () => {
    const { client, calls } = makeClient({ maxBatch: 100, getToken: () => 'sync-tok' });
    for (let i = 0; i < 20; i++) client.enqueue({ type: 'log', message: `${i}`, stack: 's'.repeat(8000) });
    client.flushOnUnload();
    expect(client.queueLength).toBe(0);
    expect(calls.length).toBeGreaterThan(1);
    for (const call of calls) {
      expect(call.init.keepalive).toBe(true);
      expect(new TextEncoder().encode(call.init.body as string).length).toBeLessThanOrEqual(60000);
      expect(call.headers['Authorization']).toBe('Bearer sync-tok');
    }
    expect(calls.reduce((n, c) => n + c.events.length, 0)).toBe(20);
  });

  it('sends anonymously when getToken is async', () => {
    const { client, calls } = makeClient({ getToken: () => Promise.resolve('async-tok') });
    client.enqueue({ type: 'log', message: 'bye' });
    client.flushOnUnload();
    expect(calls[0]!.headers['Authorization']).toBeUndefined();
  });

  it('falls back to navigator.sendBeacon when fetch is unavailable', async () => {
    const beacon = vi.fn((...args: [url: string | URL, data?: BodyInit | null]) => args.length > 0);
    Object.defineProperty(navigator, 'sendBeacon', { value: beacon, configurable: true });
    vi.stubGlobal('fetch', undefined);
    try {
      const client = new TelemetryClient({ endpoint: '', getToken: () => 'tok' });
      client.enqueue({ type: 'log', message: 'bye' });
      client.flushOnUnload();
      expect(beacon).toHaveBeenCalledTimes(1);
      const [url, data] = beacon.mock.calls[0]!;
      expect(url).toBe('/api/telemetry/v1/ingest');
      const blob = data as Blob;
      expect(blob.type).toBe('application/json');
      const body = JSON.parse(await blob.text()) as { events: Array<{ message: string }> };
      expect(body.events[0]!.message).toBe('bye');
    } finally {
      vi.unstubAllGlobals();
      delete (navigator as { sendBeacon?: unknown }).sendBeacon;
    }
  });
});
