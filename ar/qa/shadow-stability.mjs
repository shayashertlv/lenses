/** Deterministic temporal shadow measurement; synthetic fixture only, no real camera.
 * node qa/shadow-stability.mjs [--label=current] [--base=http://127.0.0.1:8247]
 * To measure a deliberately preserved source copy:
 * node qa/shadow-stability.mjs --label=before --module=/qa/output/shadow-stability-source-before/src/render/renderer.ts
 * Optional --reference-module=/path/to/render/face-surface.ts pins the input reconstruction independently;
 * it defaults to /src/render/face-surface.ts. --frames=48 and --input=qa/output/shadow-stability-input.json
 * select the sequence length and saved audited detection/PNG. --out overrides the output directory.
 * All runs reuse the saved input, seed, noise sequence and timestamps. This measures flicker, not acceptance. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url)), root = path.resolve(here, '..'), args = process.argv.slice(2);
const option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const label = option('label', 'current'), base = option('base', 'http://127.0.0.1:8247').replace(/\/$/, '');
assert.match(label, /^[a-z0-9-]+$/i);
const modulePath = option('module', '/src/render/renderer.ts');
const referenceModulePath = option('reference-module', '/src/render/face-surface.ts');
const output = path.resolve(option('out', path.join(here, 'output', `shadow-stability-${label}`)));
const inputFile = path.resolve(option('input', path.join(here, 'output', 'shadow-stability-input.json')));
const frames = Number(option('frames', '48')), warmup = 12, dtMs = 33, seed = 0x53ad0127;
assert.ok(Number.isInteger(frames) && frames >= 12 && frames <= 120);
const hash = value => createHash('sha256').update(value).digest('hex');
await fs.mkdir(output, {recursive: true});
const fixture = await fs.readFile(path.join(here, 'fixtures', 'face-a.jpg'));
const sourceHashes = {};
for (const name of ['renderer.ts', 'eyewear-shadow.ts', 'face-surface.ts']) {
  const file = path.join(root, modulePath.slice(1).replace(/renderer\.ts$/, name));
  try {sourceHashes[name] = hash(await fs.readFile(file));}
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    throw new Error(`Renderer source is unavailable: ${file}. Use --module=/src/render/renderer.ts for current source; a baseline --module requires a source copy preserved before editing.`);
  }
}
const referenceModuleSHA256 = hash(await fs.readFile(path.join(root, referenceModulePath.slice(1))));
const report = {schema: 'ar-shadow-stability-v1', label, createdAt: new Date().toISOString(), base, modulePath, sourceHashes,
  referenceModulePath, referenceModuleSHA256,
  status: 'running', frames, warmup, dtMs, seed, scenarios: [], pageErrors: [], shaderErrors: [],
  noise: {yawDegrees: .3, pitchDegrees: .25, depthCm: .12, independentLandmarkDepthCm: .1,
    generator: 'Seeded xorshift32; uniform bounded independent noise. Pose jitter transforms the reconstructed face consistently; additional depth noise changes landmark z only.'},
  limits: ['Fixed synthetic camera pixels isolate geometry and attenuation from exposure/video noise.',
    'The saved real detection supplies a face shape; synthetic perturbations are a controlled regression, not measured human detector noise.',
    'No pass threshold is inferred from the baseline. Timings include explicit GPU readbacks and are not live performance evidence.']};
const browser = await chromium.launch({headless: true, channel: 'chromium', args: [
  '--enable-gpu', '--use-angle=d3d11', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required',
]});
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('console', message => {
  const text = message.text();
  if ((message.type() === 'error' && /THREE\.WebGLProgram|shader|WebGL: INVALID|program not valid/i.test(text))
    || /VALIDATE_STATUS.*false|shader.*(?:failed to compile|compilation failed)|ERROR:\s*\d+:\d+:/i.test(text)) report.shaderErrors.push(text);
});
try {
  let input;
  try {input = JSON.parse(await fs.readFile(inputFile, 'utf8'));}
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    await page.route('**/ar-camera-fixture', route => route.fulfill({contentType: 'image/jpeg', body: fixture}));
    await page.addInitScript(() => {
      const source = document.createElement('canvas'); source.width = 1280; source.height = 720;
      const context = source.getContext('2d'), image = new Image();
      const ready = new Promise((resolve, reject) => {image.onload = resolve; image.onerror = reject;});
      image.src = '/ar-camera-fixture';
      const draw = () => {if (image.complete && image.naturalWidth) context.drawImage(image, 0, 0, source.width, source.height);};
      setInterval(draw, 1000 / 30);
      Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
        await ready; draw(); return source.captureStream(30);
      }});
    });
    await page.goto(`${base}/`, {waitUntil: 'load'});
    await page.waitForFunction(() => !!window.__ar);
    await page.locator('#start').click();
    await page.waitForFunction(() => window.__ar.samplesAfter(0).filter(row => row.hasFace && row.hasMask).length >= 8, null, {timeout: 120_000});
    const audit = await page.evaluate(() => window.__ar.audit());
    assert.ok(audit?.detection?.matrix && audit.images.sourcePngDataUrl, 'A tracked fixture audit is required');
    input = {schema: 'ar-shadow-stability-input-v1', fixtureSHA256: hash(fixture), detection: audit.detection,
      sourcePngDataUrl: audit.images.sourcePngDataUrl, sourcePair: audit.pair};
    await fs.writeFile(inputFile, JSON.stringify(input));
    await page.evaluate(() => window.__ar.close());
  }
  assert.equal(input.fixtureSHA256, hash(fixture), 'Saved input belongs to a different fixture');
  report.inputSHA256 = hash(JSON.stringify(input)); report.inputFile = inputFile;
  // A plain image document has no Vite HMR client: changes to live source cannot reload this baseline run.
  await page.goto(`${base}/qa/fixtures/face-a.jpg`, {waitUntil: 'load'});
  for (const eyewear of ['amber-horizon', 'tom-ford-clear']) for (const scenario of ['steady', 'pose-noise', 'shape-noise', 'combined-noise']) {
    const result = await page.evaluate(async ({modulePath, referenceModulePath, input, eyewear, scenario, frames, warmup, dtMs, seed}) => {
      const {TryOnRenderer} = await import(modulePath);
      // The input geometry helper can be pinned independently of the renderer under test.
      const {FaceSurface} = await import(referenceModulePath);
      const {Matrix4, Quaternion, Euler, Vector3} = await import('/node_modules/.vite/deps/three.js');
      const canonical = await fetch('/models/canonical-face.json').then(response => response.json());
      const image = new Image(); image.src = input.sourcePngDataUrl; await image.decode();
      const source = document.createElement('canvas'); source.width = image.naturalWidth; source.height = image.naturalHeight;
      source.getContext('2d').drawImage(image, 0, 0);
      const canvas = document.createElement('canvas'), controller = new AbortController();
      const renderer = await TryOnRenderer.create(canvas, controller.signal, eyewear, {sync: false});
      const surface = new FaceSurface(canonical.positions), aspect = source.width / source.height;
      if (!surface.reconstruct(input.detection.landmarks, input.detection.matrix, aspect)) throw new Error('Fixture face reconstruction failed');
      const basePositions = surface.positions.slice(), baseMatrix = new Matrix4().fromArray(input.detection.matrix), inverseBase = baseMatrix.clone().invert();
      const position = new Vector3(), quaternion = new Quaternion(), scale = new Vector3(); baseMatrix.decompose(position, quaternion, scale);
      const vertical = 2 * Math.tan(63 * Math.PI / 360), horizontal = vertical * aspect, point = new Vector3();
      const rng = (() => {let state = seed >>> 0; return () => {state ^= state << 13; state ^= state >>> 17; state ^= state << 5; return (state >>> 0) / 0x100000000 * 2 - 1;};})();
      const noiseRows = Array.from({length: frames + warmup}, () => ({yaw: rng() * .3, pitch: rng() * .25, depth: rng() * .12,
        vertexDepth: Array.from({length: 478}, () => rng() * .1)}));
      const detectionAt = index => {
        const noise = noiseRows[index], poseNoise = scenario === 'pose-noise' || scenario === 'combined-noise';
        const shapeNoise = scenario === 'shape-noise' || scenario === 'combined-noise';
        const delta = new Quaternion().setFromEuler(new Euler((poseNoise ? noise.pitch : 0) * Math.PI / 180,
          (poseNoise ? noise.yaw : 0) * Math.PI / 180, 0, 'YXZ'));
        const shifted = position.clone(); shifted.z += poseNoise ? noise.depth : 0;
        const matrix = new Matrix4().compose(shifted, quaternion.clone().multiply(delta), scale), relative = matrix.clone().multiply(inverseBase);
        const positions = new Float64Array(468 * 3);
        let meanDepth = 0;
        for (let i = 0; i < 468; i++) {point.fromArray(basePositions, i * 3).applyMatrix4(relative).toArray(positions, i * 3); meanDepth -= point.z / 468;}
        const landmarks = Array.from({length: 478}, (_, i) => {
          if (i >= 468) return {...input.detection.landmarks[i]};
          const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
          return {x: .5 + x / (-z * horizontal), y: .5 - y / (-z * vertical),
            z: (-z / meanDepth - 1) / horizontal + (shapeNoise ? noise.vertexDepth[i] / (meanDepth * horizontal) : 0)};
        });
        return {matrix: matrix.toArray(), landmarks, inferenceMs: input.detection.inferenceMs};
      };
      const png = pixels => {const output = document.createElement('canvas'); output.width = pixels.width; output.height = pixels.height;
        output.getContext('2d').putImageData(pixels, 0, 0); const url = output.toDataURL('image/png'); output.width = output.height = 0; return url;};
      const readUnderlay = () => {const target = renderer.shadows.compositeTarget, bytes = new Uint8Array(target.width * target.height * 4);
        renderer.renderer.readRenderTargetPixels(target, 0, 0, target.width, target.height, bytes);
        const pixels = new ImageData(target.width, target.height), stride = target.width * 4;
        for (let y = 0; y < target.height; y++) pixels.data.set(bytes.subarray(y * stride, (y + 1) * stride), (target.height - y - 1) * stride);
        return pixels;};
      const linear = Float64Array.from({length: 256}, (_, byte) => {const v = byte / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;});
      const count = source.width * source.height, support = new Uint8Array(count), temporalSum = new Float64Array(count);
      const bounds = {left: Math.floor(Math.min(...input.detection.landmarks.slice(0, 468).map(p => p.x)) * source.width),
        right: Math.ceil(Math.max(...input.detection.landmarks.slice(0, 468).map(p => p.x)) * source.width),
        top: Math.floor(Math.min(...input.detection.landmarks.slice(0, 468).map(p => p.y)) * source.height),
        bottom: Math.ceil(Math.max(...input.detection.landmarks.slice(0, 468).map(p => p.y)) * source.height)};
      const rows = [], images = {};
      let previous = null, rawFirst = null, rawMaxDifference = 0, largestTransition = -1, worstPair = null, previousImage = null;
      try {
        // Calibration is outside the noise sequence: its size transition is not shadow jitter.
        for (let timestamp = 0; timestamp <= 5000; timestamp += 100) {
          if (!renderer.pose(source, input.detection, timestamp)) throw new Error('Calibration pose rejected');
        }
        if (renderer.fitting && (renderer.fitting.state !== 'fitted' || renderer.fitting.ready === false))
          throw new Error('Calibration did not complete');
        for (let frame = 0; frame < frames + warmup; frame++) {
          if (!renderer.pose(source, detectionAt(frame), 6000 + frame * dtMs)) throw new Error(`Pose rejected at ${frame}`);
          renderer.render(null, {eyewear: false, shadows: false}); const raw = renderer.readback();
          const timing = renderer.render(null, {hair: false});
          if (!timing.shadowsApplied) throw new Error(`Shadows unavailable at ${frame}`);
          const shaded = readUnderlay();
          if (frame < warmup) continue;
          rawFirst ??= raw.data.slice();
          const attenuation = new Float32Array(count);
          let activePixels = 0, totalAttenuation = 0, frameDelta = 0, frameSupport = 0, maxDelta = 0;
          for (let y = 0; y < source.height; y++) for (let x = 0; x < source.width; x++) {
            const pixel = y * source.width + x, i = pixel * 4;
            for (let c = 0; c < 3; c++) rawMaxDifference = Math.max(rawMaxDifference, Math.abs(raw.data[i + c] - rawFirst[i + c]));
            if (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom || [0, 1, 2].some(c => raw.data[i + c] < 16)) continue;
            const value = ((linear[raw.data[i]] - linear[shaded.data[i]]) / linear[raw.data[i]]
              + (linear[raw.data[i + 1]] - linear[shaded.data[i + 1]]) / linear[raw.data[i + 1]]
              + (linear[raw.data[i + 2]] - linear[shaded.data[i + 2]]) / linear[raw.data[i + 2]]) / 3;
            attenuation[pixel] = value;
            const active = Math.abs(value) > .003;
            if (active) {support[pixel] = 1; activePixels++; totalAttenuation += value;}
            if (previous) {
              const change = Math.abs(value - previous[pixel]); temporalSum[pixel] += change; maxDelta = Math.max(maxDelta, change);
              if (active || Math.abs(previous[pixel]) > .003) {frameDelta += change; frameSupport++;}
            }
          }
          const mae = frameSupport ? frameDelta / frameSupport : 0;
          rows.push({frame: frame - warmup, timestampMs: 1000 + frame * dtMs, activePixels,
            meanAttenuation: activePixels ? totalAttenuation / activePixels : 0, transitionSupportPixels: frameSupport,
            temporalMae: mae, temporalMax: maxDelta, pose: renderer.poseSample});
          if (!previous) images.first = png(shaded);
          if (previous && mae > largestTransition) {largestTransition = mae; worstPair = {before: previousImage, after: shaded};}
          previous = attenuation; previousImage = shaded;
          if (frame === frames + warmup - 1) images.last = png(shaded);
          await new Promise(resolve => setTimeout(resolve, 0));
        }
        let supportPixels = 0, totalTemporal = 0;
        const heat = new ImageData(source.width, source.height);
        for (let p = 0; p < count; p++) {if (support[p]) {supportPixels++; totalTemporal += temporalSum[p];}
          const value = Math.min(255, Math.round(temporalSum[p] / Math.max(1, frames - 1) * 255 * 40));
          heat.data[p * 4] = value; heat.data[p * 4 + 1] = Math.round(value * .25); heat.data[p * 4 + 3] = 255;}
        images.temporalDifference40x = png(heat);
        if (worstPair) {images.worstBefore = png(worstPair.before); images.worstAfter = png(worstPair.after);}
        const ordered = rows.slice(1).map(row => row.temporalMae).sort((a, b) => a - b);
        const summary = {unionShadowSupportPixels: supportPixels, rawCameraMaxByteDifference: rawMaxDifference,
          unionSupportTemporalMae: supportPixels ? totalTemporal / supportPixels / (frames - 1) : 0,
          transitionMaeMedian: ordered[Math.floor(ordered.length / 2)], transitionMaeP95: ordered[Math.ceil(ordered.length * .95) - 1],
          transitionMaeMax: ordered.at(-1), maximumPixelAttenuationChange: Math.max(...rows.map(row => row.temporalMax))};
        return {eyewear, scenario, summary, rows, bounds, images, noiseRows};
      } finally {renderer.dispose(); controller.abort(); source.width = source.height = canvas.width = canvas.height = 0;}
    }, {modulePath, referenceModulePath, input, eyewear, scenario, frames, warmup, dtMs, seed});
    const directory = path.join(output, eyewear, scenario); await fs.mkdir(directory, {recursive: true});
    for (const [name, url] of Object.entries(result.images)) {
      const file = path.join(directory, `${name}.png`); await fs.writeFile(file, Buffer.from(url.slice(url.indexOf(',') + 1), 'base64')); result.images[name] = file;
    }
    result.noiseSHA256 = hash(JSON.stringify(result.noiseRows)); delete result.noiseRows;
    assert.equal(result.summary.rawCameraMaxByteDifference, 0, `${eyewear}/${scenario}: camera pixels changed during the test`);
    report.scenarios.push(result);
    console.log(`${eyewear} ${scenario}: support MAE ${(result.summary.unionSupportTemporalMae * 100).toFixed(5)} percentage points, transition p95 ${(result.summary.transitionMaeP95 * 100).toFixed(5)} pp`);
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  }
  assert.deepEqual(report.pageErrors, [], 'Uncaught browser errors'); assert.deepEqual(report.shaderErrors, [], 'Shader errors');
  report.status = 'measured';
} catch (error) {report.status = 'failed'; report.error = error.stack ?? String(error); throw error;}
finally {await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close(); console.log(`Saved ${path.join(output, 'report.json')}`);}
