/**
 * End-to-end tests: the real website against the real API, in a real browser.
 *
 * Both servers are started here with a throw-away data directory, so a run
 * never touches the developer's own StudyPilot data, and with the offline
 * providers (hash embeddings, extractive answers) so it needs no model, no
 * key and no network and is the same on every machine. The Vite dev server
 * reads the API token from that same directory and injects it as it proxies
 * (see vite.base.ts), exactly as in development.
 */
import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const API_PORT = 8799;
const WEB_PORT = 5199;
const DATA_DIR = process.env.STUDYPILOT_E2E_DATA_DIR ?? mkdtempSync(join(tmpdir(), 'studypilot-e2e-'));

const apiEnv = {
  ...process.env,
  STUDYPILOT_DATA_DIR: DATA_DIR,
  STUDYPILOT_PORT: String(API_PORT),
  STUDYPILOT_HOST: '127.0.0.1',
  STUDYPILOT_LLM_PROVIDER: 'extractive',
  STUDYPILOT_EMBEDDING_PROVIDER: 'hash',
  STUDYPILOT_LOG_LEVEL: 'WARNING',
};

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `../../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: 'services/api',
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      env: apiEnv,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      cwd: 'apps/web',
      url: `http://localhost:${WEB_PORT}`,
      env: { ...process.env, STUDYPILOT_DATA_DIR: DATA_DIR, STUDYPILOT_PORT: String(API_PORT) },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
