import {fileURLToPath} from 'node:url';
import {defineConfig} from '@playwright/test';

const hardware = process.env.PERFORMANCE_QA_D3D11 === '1';

export default defineConfig({
  testDir: '.', testMatch: ['comparison.spec.ts', 'controls.spec.ts'], timeout: 180_000,
  expect: {timeout: 45_000}, workers: 1, fullyParallel: false, reporter: 'list',
  outputDir: `./test-results/integration-${hardware ? 'd3d11' : 'default'}`,
  use: {baseURL: 'http://127.0.0.1:8067', viewport: {width: 1440, height: 1080},
    screenshot: 'only-on-failure', trace: 'retain-on-failure',
    launchOptions: hardware ? {args: ['--enable-gpu', '--use-angle=d3d11']} : {}},
  webServer: {command: 'npm run preview:performance -- --port 8067',
    cwd: fileURLToPath(new URL('../../', import.meta.url)), url: 'http://127.0.0.1:8067/experiments/performance-candidate/live.html',
    timeout: 30_000, reuseExistingServer: false},
});
