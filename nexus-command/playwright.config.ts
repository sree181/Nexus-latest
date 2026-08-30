import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  use: {
    baseURL: process.env.WALL_AUDIT_URL || 'http://127.0.0.1:4001',
    viewport: { width: 3840, height: 2160 },
  },
  webServer: process.env.WALL_AUDIT_URL ? undefined : {
    command: 'npm run dev:frontend',
    url: 'http://127.0.0.1:4001',
    reuseExistingServer: true,
  },
});
