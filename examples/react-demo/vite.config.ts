import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  // The library is linked via file:, which has its own dev copy of React.
  resolve: { dedupe: ['react', 'react-dom'] },
  server: {
    // `npm run dev` against a locally running gateway (docker compose publishes 8080).
    proxy: { '/api/telemetry/': 'http://127.0.0.1:8080' },
  },
});
