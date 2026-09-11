import {fileURLToPath} from 'node:url';
import {defineConfig} from '@playwright/test';
export default defineConfig({
  testDir:'.',testMatch:['comparison.spec.ts','controls.spec.ts','entry.spec.ts','rate.spec.ts','extraction.spec.ts','review-preview.spec.ts'],timeout:240_000,
  expect:{timeout:45_000},workers:1,fullyParallel:false,reporter:'list',
  outputDir:`./test-results/production-${new Date().toISOString().replaceAll(':','-')}`,
  use:{baseURL:'http://127.0.0.1:8098',viewport:{width:1440,height:1080},
    screenshot:'only-on-failure',trace:'retain-on-failure',
    launchOptions:{args:['--enable-gpu','--use-angle=d3d11']}},
  webServer:{command:'npx vite preview --config experiments/efficiency-lab/vite.config.ts --port 8098 --strictPort',cwd:fileURLToPath(new URL('../../',import.meta.url)),
    url:'http://127.0.0.1:8098/experiments/efficiency-lab/live.html',timeout:30_000,reuseExistingServer:false},
});
