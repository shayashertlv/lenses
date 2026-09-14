// Read-only final UI inspection. All non-GET/HEAD browser requests are blocked.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

process.env.PLAYWRIGHT_BROWSERS_PATH = resolve('data/cache/playwright');
const folder = resolve('data/browser/live-readonly');
await mkdir(folder, { recursive: true });
const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
const attempts = [], external = [], errors = [];
await page.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url());
  if (!['GET', 'HEAD'].includes(request.method())) { attempts.push({ method: request.method(), url: request.url() }); await route.abort(); return; }
  if (url.origin !== 'http://127.0.0.1:8060') { external.push(request.url()); await route.abort(); return; }
  await route.continue();
});
page.on('pageerror', error => errors.push(error.message));
let report;
try {
  await page.goto('http://127.0.0.1:8060/', { waitUntil: 'networkidle' });
  const health = await page.evaluate(async () => (await fetch('/api/health')).json());
  const before = await page.evaluate(async () => (await fetch('/api/jobs')).json());
  await page.getByRole('heading', { name: 'Start with your references.' }).waitFor();
  const fileInputCount = await page.locator('input[type=file]').count();
  await page.getByRole('button', { name: 'API setup', exact: true }).click();
  const keyFieldsEmpty = (await page.getByLabel('OpenAI API key', { exact: true }).inputValue()) === '' && (await page.getByLabel('Meshy API key', { exact: true }).inputValue()) === '';
  await page.screenshot({ path: resolve(folder, 'setup-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'Close API setup' }).click();
  await page.screenshot({ path: resolve(folder, 'new-desktop.png'), fullPage: true });
  await page.reload({ waitUntil: 'networkidle' });
  const after = await page.evaluate(async () => (await fetch('/api/jobs')).json());
  report = {
    checked_at: new Date().toISOString(), url: page.url(), health,
    five_uploads: fileInputCount === 5, key_fields_empty: keyFieldsEmpty,
    jobs_unchanged: JSON.stringify(before) === JSON.stringify(after), job_count: after.jobs.length,
    blocked_mutations: attempts, blocked_external_requests: external, page_errors: errors,
    ok: health.runtime_ready === true && health.blender_available === true && health.active_job_id === null
      && fileInputCount === 5 && keyFieldsEmpty && JSON.stringify(before) === JSON.stringify(after)
      && attempts.length === 0 && external.length === 0 && errors.length === 0,
  };
  await writeFile(resolve(folder, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exitCode = 1;
} finally { await browser.close(); }
