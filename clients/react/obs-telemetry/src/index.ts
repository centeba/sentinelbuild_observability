/**
 * @sentinelbuild/obs-telemetry — browser client for the observability platform.
 *
 * ```ts
 * Telemetry.init({ endpoint: '', app: 'sentinel-build', getToken: () => readAccessToken() });
 * installErrorHandlers();
 * ```
 *
 * React error boundary: `@sentinelbuild/obs-telemetry/react`.
 */

export { TelemetryClient, INGEST_PATH, type TelemetryClientOptions } from './client.js';
export {
  Telemetry,
  type ErrorOptions,
  type EventOptions,
  type LogOptions,
  type PerfOptions,
} from './facade.js';
export { installErrorHandlers, type InstallErrorHandlersOptions } from './handlers.js';
export {
  MAX_EVENT_BYTES,
  MAX_EVENTS_PER_REQUEST,
  MAX_REQUEST_BYTES,
  signature,
  toWire,
  type TelemetryConfig,
  type TelemetryEvent,
  type TelemetryEventType,
  type TelemetryLevel,
  type TokenProvider,
  type WireEvent,
} from './models.js';
export { createTraceContext, type TraceContext } from './trace.js';
