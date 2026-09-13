/**
 * Telemetry event model + config. Dependency-free so it is easy to test and to
 * port. Field caps mirror the obs-gateway ingest contract.
 */

/** Severity levels, aligned with the gateway/OTEL severities. */
export type TelemetryLevel = 'trace' | 'debug' | 'info' | 'warning' | 'error' | 'critical';

export type TelemetryEventType = 'log' | 'error' | 'event' | 'perf';

/** One client telemetry event (camelCase; converted to the snake_case wire shape by {@link toWire}). */
export interface TelemetryEvent {
  type: TelemetryEventType;
  level?: TelemetryLevel;
  message?: string;
  error?: string;
  stack?: string;
  url?: string;
  /** W3C trace id (32 lowercase hex) linking this event to a backend trace. */
  traceId?: string;
  /** W3C span id (16 lowercase hex). */
  spanId?: string;
  context?: Record<string, string>;
}

export type TokenProvider = () => string | null | undefined | Promise<string | null | undefined>;

/** Configuration for {@link Telemetry} / {@link TelemetryClient}. */
export interface TelemetryConfig {
  /**
   * Base URL of the gateway, or `''` for same-origin (recommended).
   * Events POST to `${endpoint}/api/telemetry/v1/ingest`.
   */
  endpoint: string;
  /** Default `'web'`. Must be ≤64 chars of `[A-Za-z0-9._-]`. */
  app?: string;
  appVersion?: string;
  /**
   * Returns the current bearer token (or null when logged out). Called per
   * flush so a login mid-session starts enriching events.
   */
  getToken?: TokenProvider;
  /** Events per request; default 20, clamped to 1..100. */
  maxBatch?: number;
  /** Default 5000. */
  flushIntervalMs?: number;
  /** Default 10000. */
  dedupeWindowMs?: number;
  /** Maximum queued events while delivery is failing; default 500. */
  maxQueue?: number;
  /** Default true. */
  enabled?: boolean;
}

/** The snake_case event shape the gateway accepts. */
export interface WireEvent {
  type: TelemetryEventType;
  level: TelemetryLevel;
  message: string;
  error?: string;
  stack?: string;
  url?: string;
  app: string;
  app_version?: string;
  trace_id?: string;
  span_id?: string;
  context?: Record<string, string>;
}

export const MAX_EVENTS_PER_REQUEST = 100;
export const MAX_EVENT_BYTES = 16384;
export const MAX_REQUEST_BYTES = MAX_EVENTS_PER_REQUEST * MAX_EVENT_BYTES;

const MAX_MESSAGE = 2000;
const MAX_ERROR = 2000;
const MAX_STACK = 8000;
const MAX_URL = 2000;
const MAX_APP = 64;
const MAX_APP_VERSION = 64;
const MAX_CONTEXT_ENTRIES = 32;
const MAX_CONTEXT_KEY = 128;
const MAX_CONTEXT_VALUE = 1024;

const TRACE_ID_RE = /^[0-9a-f]{32}$/;
const SPAN_ID_RE = /^[0-9a-f]{16}$/;
const APP_INVALID_CHARS_RE = /[^A-Za-z0-9._-]/g;

const encoder = new TextEncoder();

/** UTF-8 byte length of a string. */
export function byteLength(s: string): number {
  return encoder.encode(s).length;
}

/** Truncate to `max` UTF-16 units without leaving a dangling high surrogate. */
function cap(value: string, max: number): string {
  if (value.length <= max) return value;
  let end = max;
  const last = value.charCodeAt(end - 1);
  if (last >= 0xd800 && last <= 0xdbff) end -= 1;
  return value.slice(0, end);
}

function validId(value: string | undefined, re: RegExp): string | undefined {
  if (value === undefined) return undefined;
  const id = value.toLowerCase();
  return re.test(id) && /[^0]/.test(id) ? id : undefined;
}

/** Make `app` satisfy the gateway pattern; an invalid app would 422 the whole batch. */
export function sanitizeApp(app: string): string {
  const cleaned = cap(app.replace(APP_INVALID_CHARS_RE, '_'), MAX_APP);
  return cleaned === '' ? 'web' : cleaned;
}

/**
 * Convert to the wire shape, applying every gateway cap so a single event can
 * never cause the batch to be rejected. If the serialized event still exceeds
 * {@link MAX_EVENT_BYTES}, `context` is dropped and then `stack` is shortened
 * until it fits (and, for pathological input, `error`, `url` and `message`).
 */
export function toWire(event: TelemetryEvent, app: string, appVersion?: string): WireEvent {
  const wire: WireEvent = {
    type: event.type,
    level: event.level ?? 'info',
    message: cap(event.message ?? '', MAX_MESSAGE),
    app: sanitizeApp(app),
  };
  if (event.error != null) wire.error = cap(event.error, MAX_ERROR);
  if (event.stack != null) wire.stack = cap(event.stack, MAX_STACK);
  if (event.url != null) wire.url = cap(event.url, MAX_URL);
  if (appVersion != null) wire.app_version = cap(appVersion, MAX_APP_VERSION);
  const traceId = validId(event.traceId, TRACE_ID_RE);
  if (traceId) wire.trace_id = traceId;
  const spanId = validId(event.spanId, SPAN_ID_RE);
  if (spanId) wire.span_id = spanId;

  if (event.context) {
    // The gateway rejects empty keys (min_length=1).
    const entries = Object.entries(event.context)
      .filter(([k]) => k !== '')
      .slice(0, MAX_CONTEXT_ENTRIES);
    if (entries.length > 0) {
      wire.context = Object.fromEntries(
        entries.map(([k, v]) => [cap(k, MAX_CONTEXT_KEY), cap(String(v), MAX_CONTEXT_VALUE)]),
      );
    }
  }

  let overflow = byteLength(JSON.stringify(wire)) - MAX_EVENT_BYTES;
  if (overflow <= 0) return wire;

  delete wire.context;
  overflow = byteLength(JSON.stringify(wire)) - MAX_EVENT_BYTES;

  // Every removed char removes at least one serialized byte, so cutting
  // `overflow` chars per step converges quickly.
  for (const field of ['stack', 'error', 'url', 'message'] as const) {
    let value = wire[field];
    while (overflow > 0 && value) {
      value = cap(value, Math.max(0, value.length - overflow));
      wire[field] = value;
      overflow = byteLength(JSON.stringify(wire)) - MAX_EVENT_BYTES;
    }
  }
  return wire;
}

/** Signature for short-window dedupe. */
export function signature(event: TelemetryEvent): string {
  return `${event.type}|${event.message ?? ''}|${event.stack ?? ''}`;
}
