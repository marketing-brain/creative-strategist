import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  use: { baseURL: 'http://localhost:3000' },
  // There is no dev-mode auth bypass: this suite needs a real, signed-in Clerk
  // session to get past the app's login before it can drive anything (see the
  // Known limitations section in README.md) -- not yet wired up here.
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
