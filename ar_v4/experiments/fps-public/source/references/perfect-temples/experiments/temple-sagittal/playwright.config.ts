import {defineConfig} from '@playwright/test';

const runStamp = new Date().toISOString().replaceAll(':', '-');
export default defineConfig({
  testDir: '.', testMatch: 'mirror.spec.ts', timeout: 90_000, expect: {timeout: 25_000},
  fullyParallel: false, workers: 1, reporter: 'list',
  outputDir: `../../.recovery/temple-rethink-2026-09-08/browser-runs/${runStamp}`,
  use: {baseURL: 'http://127.0.0.1:8062', viewport: {width: 1440, height: 1000},
    trace: 'retain-on-failure', screenshot: 'only-on-failure',
    launchOptions: {args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']}},
  webServer: {
    command: 'npx vite --config experiments/temple-sagittal/vite.config.ts --host 127.0.0.1 --port 8062 --strictPort',
    cwd: '../..', url: 'http://127.0.0.1:8062', reuseExistingServer: false, timeout: 30_000,
  },
});
