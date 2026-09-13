import { afterEach, describe, expect, it } from 'vitest';

import { Telemetry } from '../src/index.js';
import { mockFetch } from './helpers.js';

afterEach(() => {
  Telemetry.dispose();
});

function init() {
  const mock = mockFetch();
  Telemetry.init({ endpoint: '', app: 'facade', flushIntervalMs: 60000 }, { fetch: mock.fetch });
  return mock;
}

describe('Telemetry facade', () => {
  it('is a no-op before init', async () => {
    expect(Telemetry.isInitialized).toBe(false);
    Telemetry.log('x');
    Telemetry.event('x');
    Telemetry.perf('x');
    Telemetry.error(new Error('x'));
    Telemetry.flushOnUnload();
    await expect(Telemetry.flush()).resolves.toBeUndefined();
  });

  it('maps log/event/perf/error onto wire events', async () => {
    const mock = init();
    expect(Telemetry.isInitialized).toBe(true);
    Telemetry.log('opened', { level: 'debug', context: { a: '1' }, url: '/x', traceId: 'c'.repeat(32), spanId: 'd'.repeat(16) });
    Telemetry.event('nav', { context: { route: '/jobs/42' } });
    Telemetry.perf('render', { durationMs: 12.5, context: { page: 'home' } });
    Telemetry.error('string failure', { level: 'critical' });
    Telemetry.error({ code: 7 });
    Telemetry.error(new TypeError('bad'), { message: 'save failed' });
    await Telemetry.flush();

    const [log, event, perf, strErr, objErr, typeErr] = mock.calls[0]!.events;
    expect(log).toMatchObject({ type: 'log', level: 'debug', message: 'opened', context: { a: '1' }, url: '/x', trace_id: 'c'.repeat(32), span_id: 'd'.repeat(16) });
    expect(event).toMatchObject({ type: 'event', level: 'info', message: 'nav', context: { route: '/jobs/42' } });
    expect(perf).toMatchObject({ type: 'perf', message: 'render', context: { page: 'home', duration_ms: '12.5' } });
    expect(strErr).toMatchObject({ type: 'error', level: 'critical', message: 'string failure', error: 'string failure' });
    expect(strErr!.stack).toBeUndefined();
    expect(objErr).toMatchObject({ message: '{"code":7}', level: 'error' });
    expect(typeErr).toMatchObject({ message: 'save failed', error: 'TypeError: bad' });
    expect(typeErr!.stack).toContain('bad');
  });

  it('re-init replaces the client and dispose resets', () => {
    init();
    init();
    expect(Telemetry.isInitialized).toBe(true);
    Telemetry.dispose();
    expect(Telemetry.isInitialized).toBe(false);
  });
});
