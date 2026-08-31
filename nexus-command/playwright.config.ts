import { defineConfig } from '@playwright/test';

// Wall audit only. Runs the Vite dev server against a review-mode API unless WALL_AUDIT_URL points at a deployment.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: {
    baseURL: process.env.WALL_AUDIT_URL || 'http://127.0.0.1:4001',
    viewport: { width: 3840, height: 2160 },
    deviceScaleFactor: 1,
  },
  webServer: process.env.WALL_AUDIT_URL ? undefined : [
    { command: 'NEXUS_REPOSITORY=review NEXUS_AUTH_MODE=review PORT=4002 npx tsx server/index.ts', url: 'http://127.0.0.1:4002/api/health', reuseExistingServer: true },
    { command: 'npx vite --port 4001', url: 'http://127.0.0.1:4001/wall.html', reuseExistingServer: true },
  ],
});
