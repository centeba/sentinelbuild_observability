import { useState } from 'react';
import { Telemetry, createTraceContext } from '@sentinelbuild/obs-telemetry';
import { TelemetryErrorBoundary } from '@sentinelbuild/obs-telemetry/react';

function Bomb(): never {
  throw new Error('Demo render error');
}

export function App() {
  const [status, setStatus] = useState('Ready');
  const [explode, setExplode] = useState(false);

  const errorWithTrace = () => {
    const trace = createTraceContext();
    // A real app would send `traceparent: trace.traceparent` on its API request.
    Telemetry.error(new Error('Demo API call failed'), {
      message: 'Demo API call failed',
      traceId: trace.traceId,
      spanId: trace.spanId,
      context: { traceparent: trace.traceparent },
    });
    setStatus(`Queued error with trace ${trace.traceId}`);
  };

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', maxWidth: 560, margin: '2rem auto', padding: '0 1rem' }}>
      <h1>obs-telemetry React demo</h1>
      <p>Events are batched; use "Flush now" to send immediately.</p>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        <button
          onClick={() => {
            Telemetry.log('Demo log message', { level: 'info', context: { source: 'button' } });
            setStatus('Queued log');
          }}
        >
          Send log
        </button>
        <button
          onClick={() => {
            Telemetry.event('demo_click', { context: { button: 'send-event' } });
            setStatus('Queued event');
          }}
        >
          Send event
        </button>
        <button onClick={() => setExplode(true)}>Throw render error</button>
        <button
          onClick={() => {
            void Promise.reject(new Error('Demo unhandled rejection'));
            setStatus('Rejected a promise');
          }}
        >
          Unhandled promise rejection
        </button>
        <button onClick={errorWithTrace}>Error with trace context</button>
        <button
          onClick={() => {
            void Telemetry.flush().then(() => setStatus('Flushed'));
          }}
        >
          Flush now
        </button>
      </div>

      <TelemetryErrorBoundary
        fallback={(error, reset) => (
          <p role="alert">
            Caught: {error.message}{' '}
            <button
              onClick={() => {
                setExplode(false);
                reset();
              }}
            >
              Reset
            </button>
          </p>
        )}
      >
        {explode ? <Bomb /> : <p>Component is healthy.</p>}
      </TelemetryErrorBoundary>

      <p>
        <small>{status}</small>
      </p>
    </main>
  );
}
