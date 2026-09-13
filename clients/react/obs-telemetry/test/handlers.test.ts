import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { Telemetry, installErrorHandlers } from '../src/index.js';
import { mockFetch } from './helpers.js';

let mock: ReturnType<typeof mockFetch>;

beforeEach(() => {
  mock = mockFetch();
  Telemetry.init({ endpoint: '', app: 'test', flushIntervalMs: 60000 }, { fetch: mock.fetch });
});

afterEach(() => {
  Telemetry.dispose();
});

function dispatchError(error: unknown, message: string) {
  window.dispatchEvent(new ErrorEvent('error', { error, message }));
}

function dispatchRejection(reason: unknown) {
  const event = new Event('unhandledrejection') as PromiseRejectionEvent;
  Object.defineProperty(event, 'reason', { value: reason });
  window.dispatchEvent(event);
}

describe('installErrorHandlers', () => {
  it('reports window errors and unhandled rejections', async () => {
    const uninstall = installErrorHandlers({ window });
    dispatchError(new Error('window boom'), 'Uncaught Error: window boom');
    dispatchRejection(new Error('rejected boom'));
    dispatchRejection('plain reason');
    await Telemetry.flush();
    uninstall();

    const events = mock.calls.flatMap((c) => c.events);
    expect(events.map((e) => e.message)).toEqual(['window boom', 'rejected boom', 'plain reason']);
    expect(events.every((e) => e.type === 'error' && e.level === 'error' && e.app === 'test')).toBe(true);
    expect(events[0]!.error).toBe('Error: window boom');
    expect(events[0]!.stack).toContain('window boom');
  });

  it('uses the event message when there is no error object (cross-origin script)', async () => {
    const uninstall = installErrorHandlers({ window });
    dispatchError(null, 'Script error.');
    await Telemetry.flush();
    uninstall();
    expect(mock.calls[0]!.events[0]!.message).toBe('Script error.');
  });

  it('stops reporting after uninstall', async () => {
    const uninstall = installErrorHandlers({ window });
    uninstall();
    // No error object: vitest's own jsdom listener treats a lone Error event as uncaught.
    dispatchError(null, 'ignored');
    dispatchRejection(new Error('ignored too'));
    await Telemetry.flush();
    expect(mock.calls).toHaveLength(0);
  });

  it('flushes on pagehide and when the document becomes hidden', () => {
    const uninstall = installErrorHandlers({ window });
    Telemetry.log('before pagehide');
    window.dispatchEvent(new Event('pagehide'));
    expect(mock.calls).toHaveLength(1);
    expect(mock.calls[0]!.init.keepalive).toBe(true);

    Telemetry.log('before hidden');
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    try {
      document.dispatchEvent(new Event('visibilitychange'));
    } finally {
      delete (document as { visibilityState?: unknown }).visibilityState;
    }
    expect(mock.calls).toHaveLength(2);
    uninstall();
  });

  it('does not report errors raised while reporting', async () => {
    const uninstall = installErrorHandlers({ window });
    // An error value whose stringification itself dispatches another window error.
    const nasty = {
      toJSON() {
        dispatchError(new Error('recursive'), 'recursive');
        return 'nasty';
      },
    };
    dispatchError(nasty, 'nasty');
    await Telemetry.flush();
    uninstall();
    const messages = mock.calls.flatMap((c) => c.events.map((e) => e.message));
    expect(messages).toEqual(['nasty']);
  });
});
