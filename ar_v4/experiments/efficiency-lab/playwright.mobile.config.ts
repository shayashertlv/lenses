import {fileURLToPath} from 'node:url';
import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: '.', testMatch: ['mobile.spec.ts', 'startup.spec.ts'],
  // Ten unmodified production windows take at least 350 seconds, plus real
  // worker startup, safe switches, local video finalization and export checks.
  timeout: 480_000, expect: {timeout: 45_000}, workers: 1, fullyParallel: false,
  reporter: 'list', outputDir: `./test-results/mobile-${new Date().toISOString().replaceAll(':', '-')}`,
  use: {baseURL: 'http://127.0.0.1:8104', viewport: {width: 390, height: 844},
    deviceScaleFactor: 1, hasTouch: true, screenshot: 'only-on-failure', trace: 'retain-on-failure',
    launchOptions: {args: ['--enable-gpu', '--use-angle=d3d11']}},
  webServer: {command: 'python experiments/efficiency-lab/qa/mobile-server.py --port 8104',
    cwd: fileURLToPath(new URL('../../', import.meta.url)),
    url: 'http://127.0.0.1:8104/ar_testing/', timeout: 30_000, reuseExistingServer: false},
});
