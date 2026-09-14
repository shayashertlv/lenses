import {fileURLToPath} from 'node:url';
import {defineConfig} from '@playwright/test';
export default defineConfig({
  testDir:'.',testMatch:['comparison.spec.ts','controls.spec.ts'],timeout:240_000,
  expect:{timeout:45_000},workers:1,fullyParallel:false,reporter:'list',
  outputDir:'./test-results/production',
  use:{baseURL:'http://127.0.0.1:8084',viewport:{width:1440,height:1080},
    screenshot:'only-on-failure',trace:'retain-on-failure',
    launchOptions:{args:['--enable-gpu','--use-angle=d3d11']}},
  webServer:{command:'npm run preview:speed-lab -- --port 8084',cwd:fileURLToPath(new URL('../../',import.meta.url)),
    url:'http://127.0.0.1:8084/experiments/speed-lab/live.html',timeout:30_000,reuseExistingServer:false},
});
