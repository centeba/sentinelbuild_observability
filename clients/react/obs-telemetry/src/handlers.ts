/** Wire the browser's global error surfaces into {@link Telemetry}. */

import { Telemetry, type ErrorOptions } from './facade.js';

export interface InstallErrorHandlersOptions {
  /** Defaults to the global `window`. */
  window?: Window;
}

/**
 * Report uncaught errors (`error`) and unhandled promise rejections
 * (`unhandledrejection`), and drain the queue when the page is hidden
 * (`visibilitychange` → hidden, `pagehide`). Call after {@link Telemetry.init}.
 * Returns a function that removes the listeners.
 */
export function installErrorHandlers(options: InstallErrorHandlersOptions = {}): () => void {
  const win = options.window ?? (typeof window !== 'undefined' ? window : undefined);
  if (!win) return () => {};

  // Recursion guard: anything raised while reporting is not reported again.
  let reporting = false;
  const report = (err: unknown, opts?: ErrorOptions): void => {
    if (reporting) return;
    reporting = true;
    try {
      Telemetry.error(err, opts);
    } catch {
      // Never throw from a global handler.
    } finally {
      reporting = false;
    }
  };

  const onError = (e: ErrorEvent): void => {
    // Cross-origin scripts surface as "Script error." with no error object.
    report(e.error ?? e.message, e.error instanceof Error ? undefined : { message: e.message });
  };
  const onRejection = (e: PromiseRejectionEvent): void => {
    report(e.reason);
  };
  const onVisibilityChange = (): void => {
    if (win.document.visibilityState === 'hidden') Telemetry.flushOnUnload();
  };
  const onPageHide = (): void => {
    Telemetry.flushOnUnload();
  };

  win.addEventListener('error', onError);
  win.addEventListener('unhandledrejection', onRejection);
  win.document.addEventListener('visibilitychange', onVisibilityChange);
  win.addEventListener('pagehide', onPageHide);

  return () => {
    win.removeEventListener('error', onError);
    win.removeEventListener('unhandledrejection', onRejection);
    win.document.removeEventListener('visibilitychange', onVisibilityChange);
    win.removeEventListener('pagehide', onPageHide);
  };
}
