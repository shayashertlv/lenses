import {test, expect} from '@playwright/test';

test('current G root preserves queries, camera-off startup and real prior checkpoint pages', async ({page, request}) => {
  for (const method of ['GET', 'HEAD']) {
    const response = await request.fetch('/?entry-check=retained', {method, maxRedirects: 0});
    expect(response.status()).toBe(302);
    expect(response.headers().location).toBe('/experiments/speed-lab/live.html?entry-check=retained');
    expect(response.headers()['cache-control']).toBe('no-store');
  }
  await page.goto('/?entry-check=retained');
  await expect(page).toHaveURL(/\/experiments\/speed-lab\/live\.html\?entry-check=retained$/);
  await expect(page.locator('#pipeline-select')).toHaveValue('combined');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect(page.locator('#eyewear-select option')).toHaveCount(2);
  for (const [path, title] of [
    ['/experiments/performance-stage2/live.html', 'Lenses · Speed testing'],
    ['/experiments/performance-stage3/live.html', 'Lenses · Test 3 · GPU hair comparison'],
    ['/experiments/hair-live-preview/live.html', 'Lenses · Long hair mirror'],
    ['/experiments/temple-sagittal/live.html', 'Lenses — Perfecto / Rear-temple candidate'],
    ['/index.html', 'Lenses — AR v4'],
  ] as const) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(200);
    const html = await response.text();
    expect(html, path).toContain(`<title>${title}</title>`);
    expect(html, path).not.toContain('Current · G Combined');
    expect(response.headers()['cross-origin-opener-policy']).toBe('same-origin');
    expect(response.headers()['cross-origin-embedder-policy']).toBe('require-corp');
  }
});
