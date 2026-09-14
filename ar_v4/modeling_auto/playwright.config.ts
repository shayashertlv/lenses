import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

process.env['PLAYWRIGHT_BROWSERS_PATH'] ??= resolve('data/cache/playwright');

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  workers: 2,
  timeout: 30_000,
  outputDir: 'data/browser/test-results',
  reporter: [['list'], ['json', { outputFile: 'data/browser/results.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:8061',
    viewport: { width: 1440, height: 1050 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] },
  },
  webServer: {
    command: 'node scripts/browser_fixture_server.mjs',
    url: 'http://127.0.0.1:8061',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
