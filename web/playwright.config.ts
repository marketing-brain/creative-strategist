import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  use: { baseURL: 'http://localhost:3000' },
  // Everyday `npm run dev` always requires real Clerk login (.env.local sets
  // NEXT_PUBLIC_AUTH_MODE=clerk), but this suite drives the app with no login
  // step at all -- override back to the instant dev-header bypass for the
  // server this config spawns. Only takes effect on a fresh spawn: with
  // reuseExistingServer true, a dev server already running on :3000 (in
  // whatever mode it started in) is reused as-is and this override is skipped.
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: true,
    timeout: 120_000,
    env: { ...process.env, NEXT_PUBLIC_AUTH_MODE: 'dev' },
  },
});
