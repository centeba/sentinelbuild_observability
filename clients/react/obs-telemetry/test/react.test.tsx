import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Telemetry } from '../src/index.js';
import { TelemetryErrorBoundary } from '../src/react.js';
import { mockFetch } from './helpers.js';

afterEach(() => {
  cleanup();
  Telemetry.dispose();
});

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error('render boom');
  return <p>all good</p>;
}

describe('TelemetryErrorBoundary', () => {
  it('reports the render error with the component stack and renders the fallback', async () => {
    const mock = mockFetch();
    Telemetry.init({ endpoint: '', app: 'react', flushIntervalMs: 60000 }, { fetch: mock.fetch });
    vi.spyOn(console, 'error').mockImplementation(() => {});

    render(
      <TelemetryErrorBoundary fallback={<p>something went wrong</p>}>
        <Bomb explode />
      </TelemetryErrorBoundary>,
    );

    expect(screen.getByText('something went wrong')).toBeTruthy();
    await Telemetry.flush();
    const events = mock.calls.flatMap((c) => c.events);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: 'error', level: 'error', message: 'render boom', error: 'Error: render boom' });
    const componentStack = events[0]!.context!['component_stack']!;
    expect(componentStack).toContain('Bomb');
    expect(componentStack.length).toBeLessThanOrEqual(1024);
  });

  it('passes error and reset to a function fallback', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});

    function App() {
      const [explode, setExplode] = useState(true);
      return (
        <TelemetryErrorBoundary
          fallback={(error, reset) => (
            <button
              onClick={() => {
                setExplode(false);
                reset();
              }}
            >
              retry after {error.message}
            </button>
          )}
        >
          <Bomb explode={explode} />
        </TelemetryErrorBoundary>
      );
    }

    render(<App />);
    fireEvent.click(screen.getByText('retry after render boom'));
    expect(screen.getByText('all good')).toBeTruthy();
  });

  it('renders children when nothing throws', () => {
    render(
      <TelemetryErrorBoundary>
        <Bomb explode={false} />
      </TelemetryErrorBoundary>,
    );
    expect(screen.getByText('all good')).toBeTruthy();
  });
});
