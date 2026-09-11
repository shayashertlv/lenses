import {test, expect} from '@playwright/test';
import {PIPELINES, PIPELINE_LABELS} from './profiles.ts';

test('isolated comparison opens G camera-off and retains accepted entry pages', async ({page, request}) => {
  for (const method of ['GET', 'HEAD']) {
    const response = await request.fetch('/?entry-check=retained', {method, maxRedirects: 0});
    expect(response.status()).toBe(302);
    expect(response.headers().location).toBe('/experiments/efficiency-lab/live.html?entry-check=retained');
    expect(response.headers()['cache-control']).toBe('no-store');
  }
  await page.goto('/?entry-check=retained');
  await expect(page).toHaveURL(/\/experiments\/efficiency-lab\/live\.html\?entry-check=retained$/);
  await expect(page.locator('#pipeline-select')).toHaveValue('g');
  await expect(page.locator('#active-pipeline')).toHaveText(PIPELINE_LABELS.g);
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect(page.locator('#pipeline-select option')).toHaveCount(PIPELINES.length);
  await expect(page.locator('#eyewear-select option')).toHaveCount(2);
  await expect(page.locator('#hair-model-select option')).toHaveCount(2);
  for (const [path, title] of [
    ['/experiments/speed-lab/live.html', 'Lenses \u00b7 G Combined'],
    ['/experiments/performance-stage2/live.html', 'Lenses · Speed testing'],
    ['/experiments/hair-live-preview/live.html', 'Lenses · Long hair mirror'],
    ['/experiments/temple-sagittal/live.html', 'Lenses — Perfecto / Rear-temple candidate'],
    ['/index.html', 'Lenses — AR v4'],
  ] as const) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(200);
    expect(await response.text(), path).toContain(`<title>${title}</title>`);
    expect(response.headers()['cross-origin-opener-policy']).toBe('same-origin');
    expect(response.headers()['cross-origin-embedder-policy']).toBe('require-corp');
  }
  for (const width of [1440, 390, 360]) {
    await page.setViewportSize({width, height: 1000});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('#start')).toBeVisible();
    for(const pipeline of PIPELINES){
      await page.selectOption('#pipeline-select',pipeline);
      const bounds=await page.evaluate(()=>{
        const top=document.querySelector('.stage-top')!.getBoundingClientRect();
        const button=document.querySelector('#stage-toggle-pipeline')!.getBoundingClientRect();
        const label=document.querySelector('.stage-label')!.getBoundingClientRect();
        return {right:button.right,available:top.right,left:button.left,labelRight:label.right};
      });
      expect(bounds.right).toBeLessThanOrEqual(bounds.available+1);expect(bounds.labelRight).toBeLessThanOrEqual(bounds.left+1);
    }
    await page.selectOption('#pipeline-select','g');
  }
});
