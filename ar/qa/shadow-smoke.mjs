/** Shadow preview verification on a Vite development server (imports the renderer source).
 * Every pixel comparison reuses one held source image and detection; no live frames are compared.
 * node qa/shadow-smoke.mjs [--base=http://127.0.0.1:8247] [--out=qa/output/shadow-preview]
 * Uses the checked-in synthetic camera only. This is not real-device or visual-acceptance evidence. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url)), args = process.argv.slice(2);
const option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8247').replace(/\/$/, '');
const output = path.resolve(option('out', path.join(here, 'output', 'shadow-preview')));
assert.equal(new URL(base).search, '', 'Use the normal page without query overrides');
await fs.mkdir(output, {recursive: true});
const fixture = await fs.readFile(path.join(here, 'fixtures', 'face-a.jpg'));
const report = {schema: 'ar-shadow-smoke-v1', createdAt: new Date().toISOString(), base, status: 'running',
  checks: [], models: [], screenshots: [], console: [], pageErrors: [], shaderErrors: [],
  limits: ['Synthetic camera from the checked-in face-a image; no real camera is opened.',
    'Pixel comparisons use a frozen source and detection in a separate renderer.',
    'This checks rendering effects and lifecycle, not wearer realism, physical light transport or phone performance.']};
const browser = await chromium.launch({headless: true, channel: 'chromium', args: [
  '--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required',
]});
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
page.setDefaultTimeout(30_000);
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('console', message => {
  const text = message.text();
  if (['warning', 'error'].includes(message.type())) report.console.push({type: message.type(), text});
  if ((message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(text))
    || /VALIDATE_STATUS.*false|shader.*(?:failed to compile|compilation failed)|ERROR:\s*\d+:\d+:/i.test(text)) report.shaderErrors.push(text);
});
await page.route('**/ar-camera-fixture', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
await page.addInitScript(() => {
  const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
  const context = source.getContext('2d'), image = new Image();
  const state = {requests: 0, streams: []}; window.__syntheticCamera = state;
  const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;});
  image.src = '/ar-camera-fixture';
  const draw = () => {
    context.fillStyle = '#163949'; context.fillRect(0, 0, source.width, source.height);
    if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, source.width, source.height);
  };
  setInterval(draw, 1000 / 30);
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
    state.requests++; await ready; draw();
    const stream = source.captureStream(30); state.streams.push(stream); return stream;
  }});
});

const check = label => {report.checks.push(label); console.log(`PASS ${label}`);};
const savePngs = async (directory, images, prefix = '') => {
  const files = {};
  await fs.mkdir(directory, {recursive: true});
  for (const [name, url] of Object.entries(images)) {
    if (!url) continue;
    assert.ok(url.startsWith('data:image/png;base64,'), `${name}: expected a lossless PNG`);
    const file = path.join(directory, `${prefix}${name.replace('PngDataUrl', '')}.png`);
    await fs.writeFile(file, Buffer.from(url.slice(url.indexOf(',') + 1), 'base64'));
    files[name] = file;
  }
  return files;
};
const session = () => page.evaluate(() => ({requests: window.__syntheticCamera.requests,
  id: document.querySelector('.stage').dataset.sessionId, open: window.__ar.isOpen()}));
const close = async () => {
  await page.evaluate(() => window.__ar.close());
  await page.waitForFunction(() => !window.__ar.isOpen()
    && window.__syntheticCamera.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')));
};

try {
  await page.goto(`${base}/`, {waitUntil: 'load'});
  await page.waitForFunction(() => !!window.__ar?.shadowSettings);
  assert.equal((await session()).requests, 0, 'Opening the page must not request a camera');
  const defaults = await page.evaluate(() => window.__ar.shadowSettings());
  assert.deepEqual(defaults, {enabled: true, frameStrength: .16, lensStrength: .18, softness: 1.9});
  assert.equal(await page.locator('#shadow-toggle').isChecked(), true);
  check('Shadows are enabled by default and the camera remains user-opened');

  for (const eyewear of ['amber-horizon', 'tom-ford-clear']) {
    const directory = path.join(output, eyewear);
    await fs.mkdir(directory, {recursive: true});
    await page.locator('#eyewear-select').selectOption(eyewear);
    const serial = await page.evaluate(() => window.__ar.samplesAfter(0).at(-1)?.serial ?? 0);
    await page.locator('#start').click();
    await page.waitForFunction(after => window.__ar.samplesAfter(after)
      .filter(row => row.hasFace && row.hasMask && row.native?.['render.hairApplied']).length >= 8,
    serial, {timeout: 120_000});
    const active = await session();
    assert.equal(active.open, true); assert.ok(active.id);

    await page.locator('#shadow-toggle').uncheck();
    assert.equal(await page.evaluate(() => window.__ar.shadowSettings().enabled), false);
    assert.equal(await page.locator('#shadow-frame').isDisabled(), true);
    await page.locator('#shadow-toggle').check();
    for (const [id, expected, key] of [['shadow-frame', .01, 'frameStrength'], ['shadow-lens', .01, 'lensStrength'], ['shadow-softness', .4, 'softness']]) {
      await page.locator(`#${id}`).focus(); await page.locator(`#${id}`).press('Home'); await page.locator(`#${id}`).press('ArrowRight');
      const value = await page.evaluate(key => window.__ar.shadowSettings()[key], key);
      assert.ok(Math.abs(value - expected) < 1e-9, `${eyewear}: ${id} updates its renderer setting`);
    }
    await page.evaluate(settings => window.__ar.setShadows(settings), defaults);
    assert.deepEqual(await session(), active, `${eyewear}: shadow controls restarted the camera or session`);
    assert.equal(await page.locator('#shadow-frame-value').innerText(), '16%');
    assert.equal(await page.locator('#shadow-lens-value').innerText(), '18%');
    assert.equal(await page.locator('#shadow-softness-value').innerText(), '1.9×');
    check(`${eyewear}: toggle and all sliders update within the same camera session`);

    const audit = await page.evaluate(() => window.__ar.audit());
    assert.ok(audit?.detection?.matrix, `${eyewear}: a real tracked audit is required`);
    const {images: auditImages, detection, ...auditSummary} = audit;
    const model = {eyewear, session: active, audit: {...auditSummary, images: await savePngs(directory, auditImages, 'audit-')}};
    report.models.push(model);
    for (const [selector, filename] of [['.stage', 'live-stage.png'], ['.shadow-controls', 'controls.png']]) {
      const file = path.join(directory, filename); await page.locator(selector).screenshot({path: file}); report.screenshots.push(file);
    }
    await close();

    // The browser imports the exact Vite source module. After calibration, all variants share one held pose,
    // source canvas and detection; temporal smoothing, camera drift and inference cannot contaminate differences.
    const comparison = await page.evaluate(async ({eyewear, sourceUrl, detection, defaults}) => {
      const {TryOnRenderer} = await import('/src/render/renderer.ts');
      const image = new Image(); image.src = sourceUrl; await image.decode();
      const source = document.createElement('canvas'); source.width = image.naturalWidth; source.height = image.naturalHeight;
      source.getContext('2d').drawImage(image, 0, 0);
      const canvas = document.createElement('canvas'), controller = new AbortController();
      const renderer = await TryOnRenderer.create(canvas, controller.signal, eyewear, {sync: false, steady: null});
      const png = pixels => {
        const output = document.createElement('canvas'); output.width = pixels.width; output.height = pixels.height;
        output.getContext('2d').putImageData(pixels, 0, 0); const url = output.toDataURL('image/png'); output.width = output.height = 0; return url;
      };
      const draw = (settings, variant = {}) => {
        renderer.setShadows(settings); const metadata = renderer.render(null, {hair: false, ...variant});
        let underlay = null;
        if (metadata.shadowsApplied) {
          // Read the camera-and-shadow target before the physical lens mixes reflections/refraction into it.
          // TypeScript private fields remain ordinary properties; this inspection is confined to explicit QA.
          const target = renderer.shadows.compositeTarget, raw = new Uint8Array(target.width * target.height * 4);
          renderer.renderer.readRenderTargetPixels(target, 0, 0, target.width, target.height, raw);
          const pixels = new ImageData(target.width, target.height), stride = target.width * 4;
          for (let y = 0; y < target.height; y++) pixels.data.set(raw.subarray(y * stride, (y + 1) * stride), (target.height - y - 1) * stride);
          underlay = {pixels};
        }
        return {pixels: renderer.readback(), metadata, underlay};
      };
      const compare = (before, after) => {
        const a = before.pixels, b = after.pixels, map = new ImageData(a.width, a.height);
        let changedPixels = 0, changedOver8 = 0, maxDelta = 0, outsideFaceBounds = 0, outsideFaceBoundsOver1 = 0;
        const sumsBefore = [0, 0, 0], reduction = [0, 0, 0], normalizedSum = [0, 0, 0], srgbNormalizedSum = [0, 0, 0];
        const linear = byte => {const value = byte / 255; return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;};
        let colorPixels = 0;
        const points = detection.landmarks.slice(0, 468);
        const faceBounds = {left: Math.floor(Math.min(...points.map(p => p.x)) * a.width) - 4,
          right: Math.ceil(Math.max(...points.map(p => p.x)) * a.width) + 4,
          top: Math.floor(Math.min(...points.map(p => p.y)) * a.height) - 4,
          bottom: Math.ceil(Math.max(...points.map(p => p.y)) * a.height) + 4};
        for (let i = 0; i < a.data.length; i += 4) {
          const delta = Math.max(...[0, 1, 2].map(channel => Math.abs(a.data[i + channel] - b.data[i + channel])));
          map.data[i + 3] = 255;
          if (!delta) continue;
          changedPixels++; if (delta > 8) changedOver8++; maxDelta = Math.max(maxDelta, delta);
          const x = i / 4 % a.width, y = Math.floor(i / 4 / a.width);
          if (x < faceBounds.left || x > faceBounds.right || y < faceBounds.top || y > faceBounds.bottom) {
            outsideFaceBounds++; if (delta > 1) outsideFaceBoundsOver1++;
          }
          const v = delta > 8 ? 255 : 96; map.data[i] = map.data[i + 1] = map.data[i + 2] = v;
          if ([0, 1, 2].every(channel => a.data[i + channel] >= 16)) {
            colorPixels++;
            for (let channel = 0; channel < 3; channel++) {
              sumsBefore[channel] += a.data[i + channel]; reduction[channel] += a.data[i + channel] - b.data[i + channel];
              const originalLinear = linear(a.data[i + channel]), shadedLinear = linear(b.data[i + channel]);
              normalizedSum[channel] += (originalLinear - shadedLinear) / originalLinear;
              srgbNormalizedSum[channel] += (a.data[i + channel] - b.data[i + channel]) / a.data[i + channel];
            }
          }
        }
        return {changedPixels, changedOver8, maxDelta, outsideFaceBounds, outsideFaceBoundsOver1, faceBounds, colorPixels,
          // Per-pixel linear-light ratios prevent skin's unequal RGB brightness and sRGB transfer from faking a tint.
          channelAttenuation: normalizedSum.map(sum => colorPixels ? sum / colorPixels : 0),
          srgbChannelAttenuation: srgbNormalizedSum.map(sum => colorPixels ? sum / colorPixels : 0),
          weightedChannelAttenuation: reduction.map((sum, channel) => sumsBefore[channel] ? sum / sumsBefore[channel] : 0),
          differencePngDataUrl: png(map)};
      };
      try {
        for (let timestamp = 0; timestamp <= 5000; timestamp += 100) {
          if (!renderer.pose(source, detection, timestamp)) throw new Error('The held detection did not produce a renderable face');
        }
        if (!renderer.fitting.ready) throw new Error('The held detection did not complete fitting');
        const off = draw(defaults, {shadows: false});
        const on = draw(defaults);
        const onRepeat = draw(defaults);
        const frame = draw({...defaults, lensStrength: 0});
        const lens = draw({...defaults, frameStrength: 0});
        const disabled = draw({...defaults, enabled: false});
        const noEyewearOn = draw(defaults, {eyewear: false}), noEyewearOff = draw(defaults, {eyewear: false, shadows: false});
        renderer.pose(source, {matrix: null, landmarks: [], inferenceMs: 0}, 5033);
        const noFaceOn = draw(defaults), noFaceOff = draw(defaults, {shadows: false});
        const variants = {off, on, frame, lens, disabled, noEyewearOn, noFaceOn};
        const underlays = {on: on.underlay, frame: frame.underlay, lens: lens.underlay};
        return {width: source.width, height: source.height, settings: defaults,
          metadata: Object.fromEntries(Object.entries(variants).map(([name, value]) => [name, value.metadata])),
          differences: {on: compare(off, on), frame: compare(off, frame), lens: compare(off, lens),
            deterministic: compare(on, onRepeat), disabled: compare(off, disabled),
            noEyewear: compare(noEyewearOff, noEyewearOn), noFace: compare(noFaceOff, noFaceOn)},
          underlayDifferences: Object.fromEntries(Object.entries(underlays).map(([name, value]) => [name, compare(noEyewearOff, value)])),
          images: Object.fromEntries([...Object.entries(variants).map(([name, value]) => [name, png(value.pixels)]),
            ['underlay-background', png(noEyewearOff.pixels)],
            ...Object.entries(underlays).map(([name, value]) => [`underlay-${name}`, png(value.pixels)])])};
      } finally {renderer.dispose(); controller.abort(); source.width = source.height = canvas.width = canvas.height = 0;}
    }, {eyewear, sourceUrl: auditImages.sourcePngDataUrl, detection, defaults});
    const differenceImages = {};
    for (const [name, value] of Object.entries(comparison.differences)) {
      differenceImages[`difference-${name}`] = value.differencePngDataUrl; delete value.differencePngDataUrl;
    }
    for (const [name, value] of Object.entries(comparison.underlayDifferences)) {
      differenceImages[`difference-underlay-${name}`] = value.differencePngDataUrl; delete value.differencePngDataUrl;
    }
    comparison.images = await savePngs(directory, {...comparison.images, ...differenceImages});
    model.comparison = comparison;

    assert.equal(audit.checkError, null, `${eyewear}: live audit checks unavailable`);
    for (const key of ['protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck']) {
      assert.equal(audit.checks?.[key]?.changedPixels, 0, `${eyewear}: ${key}`);
    }
    assert.equal(audit.shadows?.applied, true, `${eyewear}: live shadows not applied`);
    assert.ok(audit.shadows.onVsOff.differentPixels > 0, `${eyewear}: live shadows have no visible effect`);
    check(`${eyewear}: live shadow audit changes pixels and retains all four hair protection checks`);
    for (const name of ['on', 'frame', 'lens']) {
      assert.equal(comparison.metadata[name].shadowsApplied, true, `${eyewear}: ${name} shadow metadata`);
      assert.ok(comparison.differences[name].changedPixels > 0, `${eyewear}: ${name} produced no shadow effect`);
    }
    for (const name of ['deterministic', 'disabled', 'noEyewear', 'noFace']) {
      assert.equal(comparison.differences[name].changedPixels, 0, `${eyewear}: ${name} render differs`);
    }
    for (const name of ['disabled', 'noEyewearOn', 'noFaceOn']) assert.equal(comparison.metadata[name].shadowsApplied, false);
    check(`${eyewear}: frame/lens effects render independently; toggling off, removing eyewear and losing the face leave no shadow`);
    for (const [name, value] of Object.entries(comparison.underlayDifferences)) {
      assert.equal(value.outsideFaceBoundsOver1, 0, `${eyewear}: ${name} underlay changes background outside the face by more than one byte`);
    }
    check(`${eyewear}: shadow underlays preserve background outside the observed face`);
    const attenuation = comparison.underlayDifferences.lens.channelAttenuation;
    assert.ok(comparison.underlayDifferences.lens.colorPixels > 0, `${eyewear}: lens underlay color has no measurable pixels`);
    if (eyewear === 'amber-horizon') {
      assert.ok(attenuation[2] > attenuation[0], `Amber lens must attenuate blue more than red: ${attenuation}`);
      check('Amber lens shadow retains its warm color: blue attenuation exceeds red attenuation');
    } else {
      assert.ok(Math.max(...attenuation) < .05, `Clear lens attenuation must stay below 5%: ${attenuation}`);
      assert.ok(Math.max(...attenuation) - Math.min(...attenuation) < .015, `Clear lens must stay nearly neutral: ${attenuation}`);
      check('Clear lens shadow stays weak and nearly neutral');
    }
  }
  assert.deepEqual(report.pageErrors, [], 'Uncaught browser errors');
  assert.deepEqual(report.shaderErrors, [], 'WebGL/shader errors');
  check('Both shipped models complete without uncaught browser or shader errors');
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack ?? String(error);
  await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true}).catch(() => {});
  throw error;
} finally {
  await page.evaluate(() => window.__ar?.close()).catch(() => {});
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(`Saved ${path.join(output, 'report.json')}`);
}
