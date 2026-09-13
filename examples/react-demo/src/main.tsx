import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Telemetry, installErrorHandlers } from '@sentinelbuild/obs-telemetry';

import { App } from './App';

Telemetry.init({
  endpoint: '', // same-origin: nginx proxies /api/telemetry/ to obs-gateway
  app: 'react-demo',
  appVersion: '0.1.0',
  getToken: () => import.meta.env.VITE_DEMO_TOKEN || null,
});
installErrorHandlers();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
