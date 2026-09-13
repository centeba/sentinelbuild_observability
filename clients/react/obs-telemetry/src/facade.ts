/** App-wide facade over a single {@link TelemetryClient} instance. */

import { TelemetryClient, type TelemetryClientOptions } from './client.js';
import type { TelemetryConfig, TelemetryLevel } from './models.js';

export interface LogOptions {
  level?: TelemetryLevel;
  context?: Record<string, string>;
  url?: string;
  traceId?: string;
  spanId?: string;
}

export interface EventOptions {
  context?: Record<string, string>;
  url?: string;
}

export interface PerfOptions {
  context?: Record<string, string>;
  /** Recorded as `context.duration_ms`. */
  durationMs?: number;
}

export interface ErrorOptions {
  /** Defaults to the Error's message (or the stringified value). */
  message?: string;
  level?: TelemetryLevel;
  context?: Record<string, string>;
  url?: string;
  traceId?: string;
  spanId?: string;
}

let client: TelemetryClient | undefined;

function describe(err: unknown): { message: string; error: string; stack?: string } {
  if (err instanceof Error) {
    return {
      message: err.message || err.name,
      error: String(err),
      stack: typeof err.stack === 'string' ? err.stack : undefined,
    };
  }
  let text: string;
  if (typeof err === 'string') {
    text = err;
  } else {
    try {
      text = JSON.stringify(err) ?? String(err);
    } catch {
      text = String(err);
    }
  }
  return { message: text, error: text };
}

/**
 * App-wide entry point. Call {@link Telemetry.init} once at startup, then use
 * `log`/`event`/`perf`/`error` anywhere. All methods are no-ops until `init`
 * runs, so calls during early boot are safe.
 */
export const Telemetry = {
  init(config: TelemetryConfig, options?: TelemetryClientOptions): void {
    client?.dispose();
    client = new TelemetryClient(config, options);
    client.start();
  },

  get isInitialized(): boolean {
    return client !== undefined;
  },

  log(message: string, options: LogOptions = {}): void {
    client?.enqueue({
      type: 'log',
      level: options.level ?? 'info',
      message,
      context: options.context,
      url: options.url,
      traceId: options.traceId,
      spanId: options.spanId,
    });
  },

  event(name: string, options: EventOptions = {}): void {
    client?.enqueue({ type: 'event', message: name, context: options.context, url: options.url });
  },

  perf(name: string, options: PerfOptions = {}): void {
    const context =
      options.durationMs === undefined
        ? options.context
        : { ...options.context, duration_ms: String(options.durationMs) };
    client?.enqueue({ type: 'perf', message: name, context });
  },

  error(err: unknown, options: ErrorOptions = {}): void {
    if (!client) return;
    try {
      const d = describe(err);
      client.enqueue({
        type: 'error',
        level: options.level ?? 'error',
        message: options.message ?? d.message,
        error: d.error,
        stack: d.stack,
        context: options.context,
        url: options.url,
        traceId: options.traceId,
        spanId: options.spanId,
      });
    } catch {
      // Never throw into app code.
    }
  },

  flush(): Promise<void> {
    return client?.flush() ?? Promise.resolve();
  },

  /** Best-effort drain for page hide/unload (see {@link TelemetryClient.flushOnUnload}). */
  flushOnUnload(): void {
    client?.flushOnUnload();
  },

  dispose(): void {
    client?.dispose();
    client = undefined;
  },
};
