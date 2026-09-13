import {defineConfig} from '@playwright/test';
import {fileURLToPath} from 'node:url';
export default defineConfig({testDir:'.',testMatch:'native.spec.ts',workers:1,timeout:180_000,reporter:'list',
  outputDir:`../test-results/render-worker-native-${new Date().toISOString().replaceAll(':','-')}`,
  use:{baseURL:'http://127.0.0.1:8107',trace:'retain-on-failure',launchOptions:{args:['--enable-gpu','--use-angle=d3d11']}},
  webServer:{command:'npx vite --config experiments/efficiency-lab/vite.config.ts --port 8107 --strictPort',
    cwd:fileURLToPath(new URL('../../../',import.meta.url)),url:'http://127.0.0.1:8107/experiments/efficiency-lab/render-worker/native.html',reuseExistingServer:false,timeout:30_000}});
