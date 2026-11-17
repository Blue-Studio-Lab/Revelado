// SPDX-License-Identifier: GPL-3.0-or-later
import { defineConfig, devices } from '@playwright/test';

// Smoke tests run against the Vite dev server with every /api call mocked in
// the browser (route interception), so no engine is needed.
const PORT = 5179;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.pw.ts',
  timeout: 30_000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  // PW_CHANNEL=chrome uses the installed Google Chrome instead of Playwright's Chromium.
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], channel: process.env.PW_CHANNEL || undefined } }],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
