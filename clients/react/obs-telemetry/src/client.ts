/** The telemetry client: queue + batched, throttled, fire-and-forget delivery. */

import {
  MAX_EVENTS_PER_REQUEST,
  MAX_REQUEST_BYTES,
  byteLength,
  signature,
  toWire,
  type TelemetryConfig,
  type TelemetryEvent,
  type TokenProvider,
} from './models.js';

export const INGEST_PATH = '/api/telemetry/v1/ingest';

const REQUEST_TIMEOUT_MS = 4000;
/** Browsers cap in-flight keepalive/beacon bodies at 64 KiB; stay under it. */
const UNLOAD_CHUNK_BYTES = 60000;
const DEDUPE_MAX_ENTRIES = 200;

export interface TelemetryClientOptions {
  /** Injected for tests; defaults to the global `fetch`. */
  fetch?: typeof fetch;
}

/** An event already converted to its wire JSON, so byte budgets are exact. */
interface QueuedEvent {
  json: string;
  bytes: number;
}

type Outcome = 'sent' | 'dropped' | 'retry';

/**
 * Sends batches of {@link TelemetryEvent} to the obs-gateway. Never throws into
 * the caller and never blocks app code: `enqueue` returns immediately; delivery
 * is out-of-band and swallows all its own errors (a telemetry failure must not
 * become another error to report).
 */
export class TelemetryClient {
  readonly endpoint: string;
  readonly app: string;
  readonly appVersion: string | undefined;
  readonly maxBatch: number;
  readonly flushIntervalMs: number;
  readonly dedupeWindowMs: number;
  readonly maxQueue: number;
  readonly enabled: boolean;
  private readonly getToken: TokenProvider | undefined;
  private readonly fetchImpl: typeof fetch | undefined;

  private queue: QueuedEvent[] = [];
  private readonly recent = new Map<string, number>();
  private timer: ReturnType<typeof setInterval> | undefined;
  private inFlight: Promise<void> | undefined;
  /** Set after a retryable failure: suppresses eager flushes until the next timer tick. */
  private backingOff = false;

  constructor(config: TelemetryConfig, options: TelemetryClientOptions = {}) {
    this.endpoint = config.endpoint.replace(/\/+$/, '');
    this.app = config.app ?? 'web';
    this.appVersion = config.appVersion;
    this.getToken = config.getToken;
    this.maxBatch = Math.min(MAX_EVENTS_PER_REQUEST, Math.max(1, Math.floor(config.maxBatch ?? 20)));
    this.flushIntervalMs = config.flushIntervalMs ?? 5000;
    this.dedupeWindowMs = config.dedupeWindowMs ?? 10000;
    this.maxQueue = Math.max(1, config.maxQueue ?? 500);
    this.enabled = config.enabled ?? true;
    this.fetchImpl = options.fetch ?? (typeof fetch === 'function' ? fetch.bind(globalThis) : undefined);
  }

  get url(): string {
    return `${this.endpoint}${INGEST_PATH}`;
  }

  /** Number of events waiting to be sent. */
  get queueLength(): number {
    return this.queue.length;
  }

  start(): void {
    if (this.timer !== undefined || !this.enabled) return;
    this.timer = setInterval(() => {
      this.backingOff = false;
      void this.flush();
    }, this.flushIntervalMs);
  }

  /** Enqueue an event (deduped within the window). Flushes eagerly once a batch is full. */
  enqueue(event: TelemetryEvent): void {
    if (!this.enabled) return;
    try {
      const now = Date.now();
      const sig = signature(event);
      const last = this.recent.get(sig);
      if (last !== undefined && now - last < this.dedupeWindowMs) return;
      this.recent.delete(sig);
      this.recent.set(sig, now);
      if (this.recent.size > DEDUPE_MAX_ENTRIES) this.pruneRecent(now);

      const json = JSON.stringify(toWire(event, this.app, this.appVersion));
      this.queue.push({ json, bytes: byteLength(json) });
      this.trimQueue();

      if (this.queue.length >= this.maxBatch && !this.inFlight && !this.backingOff) {
        void this.flush();
      }
    } catch {
      // Never throw into app code.
    }
  }

  /** Send everything queued, in chunks of `maxBatch`. Never rejects. */
  flush(): Promise<void> {
    if (!this.inFlight) {
      this.inFlight = this.drain().finally(() => {
        this.inFlight = undefined;
      });
    }
    return this.inFlight;
  }

  /**
   * Best-effort drain for page hide/unload: `fetch` with `keepalive` (chunks
   * under the 64 KiB keepalive limit), or `navigator.sendBeacon` when fetch is
   * unavailable (anonymous — beacons cannot carry headers). Only a synchronous
   * `getToken` result is used here; an async provider sends anonymously.
   */
  flushOnUnload(): void {
    if (this.queue.length === 0) return;
    try {
      const token = this.syncToken();
      while (this.queue.length > 0) {
        const chunk = this.takeChunk(UNLOAD_CHUNK_BYTES);
        const body = buildBody(chunk);
        if (this.fetchImpl) {
          this.fetchImpl(this.url, {
            method: 'POST',
            headers: headers(token),
            body,
            keepalive: true,
          })
            .then((res) => {
              if (classify(res.status) === 'retry') this.requeue(chunk);
            })
            .catch(() => this.requeue(chunk));
        } else if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
          navigator.sendBeacon(this.url, new Blob([body], { type: 'application/json' }));
        }
      }
    } catch {
      // Never throw into app code.
    }
  }

  dispose(): void {
    if (this.timer !== undefined) clearInterval(this.timer);
    this.timer = undefined;
  }

  private async drain(): Promise<void> {
    try {
      if (this.queue.length === 0) return;
      const token = await this.resolveToken();
      while (this.queue.length > 0) {
        const chunk = this.takeChunk(MAX_REQUEST_BYTES);
        const outcome = await this.send(chunk, token);
        if (outcome === 'retry') {
          this.requeue(chunk);
          this.backingOff = true;
          return;
        }
      }
      this.backingOff = false;
    } catch {
      // Fire-and-forget.
    }
  }

  private async send(chunk: QueuedEvent[], token: string | undefined): Promise<Outcome> {
    if (!this.fetchImpl) return 'dropped';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await this.fetchImpl(this.url, {
        method: 'POST',
        headers: headers(token),
        body: buildBody(chunk),
        signal: controller.signal,
      });
      return classify(res.status);
    } catch {
      return 'retry'; // network error or timeout
    } finally {
      clearTimeout(timeout);
    }
  }

  /** Token for this flush; a throwing/rejecting provider means send anonymously. */
  private async resolveToken(): Promise<string | undefined> {
    if (!this.getToken) return undefined;
    try {
      const token = await this.getToken();
      return token || undefined;
    } catch {
      return undefined;
    }
  }

  private syncToken(): string | undefined {
    if (!this.getToken) return undefined;
    try {
      const token = this.getToken();
      if (typeof token === 'string' && token) return token;
      if (token && typeof (token as Promise<unknown>).then === 'function') {
        (token as Promise<unknown>).catch(() => undefined);
      }
    } catch {
      // Anonymous.
    }
    return undefined;
  }

  /** Remove the next chunk from the queue: ≤ maxBatch events and ≤ `maxBytes` of body. */
  private takeChunk(maxBytes: number): QueuedEvent[] {
    let count = 0;
    let bytes = EMPTY_BODY_BYTES;
    for (const item of this.queue) {
      if (count >= this.maxBatch) break;
      const next = bytes + item.bytes + (count > 0 ? 1 : 0);
      if (count > 0 && next > maxBytes) break;
      bytes = next;
      count += 1;
    }
    return this.queue.splice(0, count);
  }

  /** Put a failed chunk back at the front, dropping the oldest beyond `maxQueue`. */
  private requeue(chunk: QueuedEvent[]): void {
    this.queue.unshift(...chunk);
    this.trimQueue();
  }

  private trimQueue(): void {
    const excess = this.queue.length - this.maxQueue;
    if (excess > 0) this.queue.splice(0, excess);
  }

  private pruneRecent(now: number): void {
    for (const [sig, t] of this.recent) {
      if (now - t >= this.dedupeWindowMs) this.recent.delete(sig);
    }
    // Still full of live entries (a flood of distinct events): evict oldest.
    for (const sig of this.recent.keys()) {
      if (this.recent.size <= DEDUPE_MAX_ENTRIES) break;
      this.recent.delete(sig);
    }
  }
}

const BODY_PREFIX = '{"events":[';
const BODY_SUFFIX = ']}';
const EMPTY_BODY_BYTES = BODY_PREFIX.length + BODY_SUFFIX.length;

function buildBody(chunk: QueuedEvent[]): string {
  return BODY_PREFIX + chunk.map((e) => e.json).join(',') + BODY_SUFFIX;
}

function headers(token: string | undefined): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = `Bearer ${token}`;
  return h;
}

/** 2xx sent; 429/5xx retry later; any other status (413/422/…) is dropped — retrying won't help. */
function classify(status: number): Outcome {
  if (status >= 200 && status < 300) return 'sent';
  if (status === 429 || status >= 500) return 'retry';
  return 'dropped';
}
