import {fileURLToPath} from 'node:url';
import {defineConfig} from '@playwright/test';
import previous from '../performance-stage2/playwright.base.config.ts';

export default defineConfig({...previous,
  testDir: fileURLToPath(new URL('../performance-stage2/', import.meta.url)),
  outputDir: './test-results/previous-test2',
  webServer: {command: 'npm run preview:test2 -- --port 8075',
    cwd: fileURLToPath(new URL('../../', import.meta.url)), url: 'http://127.0.0.1:8075/',
    timeout: 30_000, reuseExistingServer: false},
});
