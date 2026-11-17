// SPDX-License-Identifier: GPL-3.0-or-later
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// Revelado web prototype.
// The dev server proxies /api (REST + WebSocket) to the local engine so the
// browser never deals with CORS or with the engine token.
//   REVELADO_ENGINE_URL   engine origin (default http://127.0.0.1:8790)
//   REVELADO_TOKEN_FILE   file holding the engine runtime token
//                         (default <repo>/engine/.runtime_token, falls back to "dev")
const here = dirname(fileURLToPath(import.meta.url));
const engineUrl = process.env.REVELADO_ENGINE_URL || 'http://127.0.0.1:8790';
const tokenFile = process.env.REVELADO_TOKEN_FILE || resolve(here, '../../engine/.runtime_token');

function engineToken(): string {
  try {
    if (existsSync(tokenFile)) {
      const t = readFileSync(tokenFile, 'utf8').trim();
      if (t) return t;
    }
  } catch {
    /* ignore: fall back to the dev token */
  }
  return 'dev';
}

export default defineConfig({
  plugins: [react()],
  define: {
    __ENGINE_URL__: JSON.stringify(engineUrl),
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: engineUrl,
        changeOrigin: true,
        ws: true,
        configure(proxy) {
          // Read the token on every request: the engine rewrites it on restart.
          const setToken = (req: { setHeader(name: string, value: string): unknown }) => {
            const tok = engineToken();
            req.setHeader('X-Revelado-Token', tok);
          };
          proxy.on('proxyReq', setToken);
          proxy.on('proxyReqWs', setToken);
        },
      },
    },
  },
  preview: { host: '127.0.0.1', port: 4173 },
});
