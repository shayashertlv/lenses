import {fileURLToPath} from 'node:url';
import {defineConfig} from '@playwright/test';
import comparison from './playwright.config.ts';

process.env.COMBINED_BASE_QA = '1';
export default defineConfig({...comparison,
  testMatch: ['comparison.spec.ts', 'controls.spec.ts', 'entry.spec.ts'],
  outputDir: './test-results/current-g',
  use: {...comparison.use, baseURL: 'http://127.0.0.1:8086'},
  webServer: {command: 'npm run preview -- --port 8086',
    cwd: fileURLToPath(new URL('../../', import.meta.url)), url: 'http://127.0.0.1:8086/',
    timeout: 30_000, reuseExistingServer: false},
});
