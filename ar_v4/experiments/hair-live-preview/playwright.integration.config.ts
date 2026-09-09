import {fileURLToPath} from 'node:url';
import {defineConfig} from '@playwright/test';

// Same bounds as the existing isolated live suite; reference-suite deadlines stay unchanged.
export default defineConfig({
  testDir: '.', testMatch: 'integration.spec.ts', timeout: 180_000, expect: {timeout: 55_000},
  workers: 1, fullyParallel: false, reporter: 'list',
  outputDir: '../../test-results/hair-integration',
  use: {baseURL: 'http://127.0.0.1:8042', viewport: {width: 1440, height: 1000},
    screenshot: 'only-on-failure', trace: 'retain-on-failure',
    launchOptions: {args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']}},
  webServer: {command: 'npm run preview -- --port 8042',
    cwd: fileURLToPath(new URL('../../', import.meta.url)), url: 'http://127.0.0.1:8042',
    timeout: 30_000, reuseExistingServer: false},
});
