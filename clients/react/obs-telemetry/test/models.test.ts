import { describe, expect, it } from 'vitest';

import { MAX_EVENT_BYTES, signature, toWire } from '../src/index.js';

const bytes = (v: unknown) => new TextEncoder().encode(JSON.stringify(v)).length;

describe('toWire', () => {
  it('produces the snake_case wire shape', () => {
    const wire = toWire(
      {
        type: 'error',
        level: 'warning',
        message: 'boom',
        error: 'Error: boom',
        stack: 'at x',
        url: 'https://app/x',
        traceId: 'a'.repeat(32),
        spanId: 'b'.repeat(16),
        context: { route: '/jobs/42' },
      },
      'web',
      '1.2.3',
    );
    expect(wire).toEqual({
      type: 'error',
      level: 'warning',
      message: 'boom',
      error: 'Error: boom',
      stack: 'at x',
      url: 'https://app/x',
      app: 'web',
      app_version: '1.2.3',
      trace_id: 'a'.repeat(32),
      span_id: 'b'.repeat(16),
      context: { route: '/jobs/42' },
    });
  });

  it('defaults level to info and omits absent optionals', () => {
    expect(toWire({ type: 'log' }, 'web')).toEqual({ type: 'log', level: 'info', message: '', app: 'web' });
  });

  it('caps message, error, stack, url and app_version', () => {
    const wire = toWire(
      {
        type: 'log',
        message: 'm'.repeat(5000),
        error: 'e'.repeat(5000),
        stack: 's'.repeat(9000),
        url: 'u'.repeat(3000),
      },
      'web',
      'v'.repeat(100),
    );
    expect(wire.message).toHaveLength(2000);
    expect(wire.error).toHaveLength(2000);
    expect(wire.stack).toHaveLength(8000);
    expect(wire.url).toHaveLength(2000);
    expect(wire.app_version).toHaveLength(64);
  });

  it('sanitizes app to the gateway pattern', () => {
    expect(toWire({ type: 'log' }, 'my app/β').app).toBe('my_app__');
    expect(toWire({ type: 'log' }, 'a'.repeat(80)).app).toHaveLength(64);
  });

  it('keeps the first 32 context entries and truncates keys and values', () => {
    const context: Record<string, string> = {};
    for (let i = 0; i < 40; i++) context[`k${i}`] = `v${i}`;
    context['k0'] = 'x'.repeat(2000);
    const wire = toWire({ type: 'log', context }, 'web');
    expect(Object.keys(wire.context!)).toHaveLength(32);
    expect(wire.context!['k31']).toBe('v31');
    expect(wire.context!['k32']).toBeUndefined();
    expect(wire.context!['k0']).toHaveLength(1024);

    const longKey = toWire({ type: 'log', context: { ['k'.repeat(300)]: 'v' } }, 'web');
    expect(Object.keys(longKey.context!)[0]).toHaveLength(128);

    expect(toWire({ type: 'log', context: { '': 'x', ok: 'y' } }, 'web').context).toEqual({ ok: 'y' });
    expect(toWire({ type: 'log', context: { '': 'x' } }, 'web').context).toBeUndefined();
  });

  it('drops invalid trace and span ids', () => {
    const cases = ['xyz', 'A'.repeat(31), '0'.repeat(32), 'g'.repeat(32)];
    for (const traceId of cases) {
      expect(toWire({ type: 'error', traceId }, 'web').trace_id).toBeUndefined();
    }
    expect(toWire({ type: 'error', spanId: '0'.repeat(16) }, 'web').span_id).toBeUndefined();
    expect(toWire({ type: 'error', spanId: 'abc' }, 'web').span_id).toBeUndefined();
  });

  it('shrinks an oversized event under 16384 bytes: context first, then stack', () => {
    const context: Record<string, string> = {};
    for (let i = 0; i < 32; i++) context[`key${i}`] = 'é'.repeat(1024);
    const wire = toWire(
      { type: 'error', message: '€'.repeat(2000), stack: '😀'.repeat(4000), context },
      'web',
    );
    expect(bytes(wire)).toBeLessThanOrEqual(MAX_EVENT_BYTES);
    expect(wire.context).toBeUndefined();
    expect(wire.message).toHaveLength(2000);
    expect(wire.stack!.length).toBeGreaterThan(0);
    expect(wire.stack!.length).toBeLessThan(8000);
    // no dangling surrogate
    expect(wire.stack!.length % 2).toBe(0);
  });

  it('drops context without touching stack when that suffices', () => {
    const context: Record<string, string> = {};
    for (let i = 0; i < 32; i++) context[`key${i}`] = 'x'.repeat(1024);
    const wire = toWire({ type: 'log', message: 'hi', stack: 's'.repeat(100), context }, 'web');
    expect(bytes(wire)).toBeLessThanOrEqual(MAX_EVENT_BYTES);
    expect(wire.context).toBeUndefined();
    expect(wire.stack).toHaveLength(100);
  });

  it('stays under the byte limit for pathological escaped input', () => {
    const ctrl = String.fromCharCode(1);
    const wire = toWire(
      { type: 'error', message: ctrl.repeat(2000), error: ctrl.repeat(2000), url: ctrl.repeat(2000), stack: ctrl.repeat(8000) },
      'web',
    );
    expect(bytes(wire)).toBeLessThanOrEqual(MAX_EVENT_BYTES);
  });
});

describe('signature', () => {
  it('is type|message|stack', () => {
    expect(signature({ type: 'error', message: 'm', stack: 's' })).toBe('error|m|s');
    expect(signature({ type: 'log' })).toBe('log||');
  });
});
