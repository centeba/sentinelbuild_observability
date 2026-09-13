import { describe, expect, it } from 'vitest';

import { createTraceContext, toWire } from '../src/index.js';

describe('createTraceContext', () => {
  it('produces W3C traceparent-compatible ids', () => {
    const ctx = createTraceContext();
    expect(ctx.traceId).toMatch(/^[0-9a-f]{32}$/);
    expect(ctx.spanId).toMatch(/^[0-9a-f]{16}$/);
    expect(ctx.traceId).not.toMatch(/^0+$/);
    expect(ctx.spanId).not.toMatch(/^0+$/);
    expect(ctx.traceparent).toBe(`00-${ctx.traceId}-${ctx.spanId}-01`);
  });

  it('is random and accepted by toWire', () => {
    const a = createTraceContext();
    const b = createTraceContext();
    expect(a.traceId).not.toBe(b.traceId);
    const wire = toWire({ type: 'error', traceId: a.traceId, spanId: a.spanId }, 'web');
    expect(wire.trace_id).toBe(a.traceId);
    expect(wire.span_id).toBe(a.spanId);
  });
});
