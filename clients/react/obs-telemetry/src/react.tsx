/** React integration (entry point `@sentinelbuild/obs-telemetry/react`). */

import { Component, type ErrorInfo, type ReactNode } from 'react';

import { Telemetry } from './facade.js';

const MAX_COMPONENT_STACK = 1024;

export interface TelemetryErrorBoundaryProps {
  /** Rendered after a render error; a function receives the error and a `reset` callback. */
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode);
  children?: ReactNode;
}

interface State {
  error: Error | null;
}

/** Error boundary that reports render errors via {@link Telemetry.error}. */
export class TelemetryErrorBoundary extends Component<TelemetryErrorBoundaryProps, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    Telemetry.error(error, {
      message: error.message,
      context: { component_stack: (info.componentStack ?? '').slice(0, MAX_COMPONENT_STACK) },
    });
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  override render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    const { fallback } = this.props;
    return typeof fallback === 'function' ? fallback(error, this.reset) : (fallback ?? null);
  }
}
