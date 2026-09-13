/** W3C trace-context ids for correlating frontend events with backend traces. */

export interface TraceContext {
  /** 32 lowercase hex chars. */
  traceId: string;
  /** 16 lowercase hex chars. */
  spanId: string;
  /** `00-<traceId>-<spanId>-01` — send as the `traceparent` request header. */
  traceparent: string;
}

function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes);
  do {
    globalThis.crypto.getRandomValues(buf);
  } while (buf.every((b) => b === 0)); // all-zero ids are invalid
  return Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Create a fresh trace context. Set `traceparent` on the outgoing API request
 * and pass `traceId`/`spanId` to `Telemetry.error(...)` so the frontend event
 * links to the backend trace.
 */
export function createTraceContext(): TraceContext {
  const traceId = randomHex(16);
  const spanId = randomHex(8);
  return { traceId, spanId, traceparent: `00-${traceId}-${spanId}-01` };
}
