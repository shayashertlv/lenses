/** Standalone, serialized GPU conformance; never run alongside live/browser QA.
 * node experiments/speed-lab/qa/source-edges.mjs --preflight
 * node experiments/speed-lab/qa/source-edges.mjs --base=http://127.0.0.1:8082 --backend=hardware
 * Repeat with --backend=swiftshader for explicitly labeled software evidence.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {chromium} from '@playwright/test';
import {verifyBase} from './preservation.mjs';

const args = process.argv.slice(2), option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const base = option('base', 'http://127.0.0.1:8082').replace(/\/$/, ''), backend = option('backend', 'hardware');
assert.equal(new URL(base).hostname, '127.0.0.1', 'Source-edge QA uses an explicit local server only.');
assert.ok(['hardware', 'swiftshader'].includes(backend));
const selectedModel = option('eyewear', 'both');
const eyewearModels = selectedModel === 'both' ? ['amber-horizon', 'tom-ford-clear'] : [selectedModel];
assert.ok(eyewearModels.every(value => ['amber-horizon', 'tom-ford-clear'].includes(value)));
const sourceId = option('source', '03-up-blonde-waves');
const sha = bytes => createHash('sha256').update(bytes).digest('hex'), frozen = new Map(), resources = new Map();
const reportPath = path.resolve('.recovery/hair-angle-review-2026-09-09/runs/2026-09-09T08-18-33.789Z/report.json');
const priorBytes = await fs.readFile(reportPath), prior = JSON.parse(priorBytes);
assert.equal(prior.complete, true); frozen.set(reportPath, sha(priorBytes));
const source = prior.images.find(item => item.id === sourceId), mask = prior.masks.find(item => item.id === sourceId && item.model === 'hair-only');
assert.ok(source && mask, 'Preserved fixture is unavailable.');
const readArtifact = async artifact => {
  const filename = path.resolve(artifact.path), relative = path.relative(path.resolve('.recovery'), filename);
  assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'Fixture leaves its preserved recovery archive.');
  const bytes = await fs.readFile(filename);
  assert.equal(sha(bytes), artifact.sha256); assert.equal(bytes.length, artifact.bytes);
  frozen.set(filename, artifact.sha256); resources.set(artifact.sha256, bytes);
  return {...artifact, url: `${base}/qa-source-edge-artifact/${artifact.sha256}`};
};
const fixture = {sourceId, detectionFile: await readArtifact(source.detectionFile), detectionSHA256: source.detectionSHA256,
  mask: {...mask, categoryU8: await readArtifact(mask.categoryU8)}, expectedModel: prior.hairModels.find(item => item.id === 'hair-only'),
  currentModule: '/experiments/performance-stage2/renderer.ts', candidateModule: '/experiments/speed-lab/renderer.ts'};
await readArtifact(source.serializedDetection);
const preservation = await verifyBase(), runtime = new Map();
async function scan(directory) {
  for (const entry of await fs.readdir(directory, {withFileTypes: true})) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) await scan(filename);
    else if (/\.ts$/.test(entry.name)) runtime.set(filename, sha(await fs.readFile(filename)));
  }
}
for (const directory of ['native', 'temples']) await scan(path.resolve('experiments/speed-lab', directory));
for (const filename of ['renderer.ts', 'speed-options.ts', 'profiles.ts', 'qa/source-edges.mjs', 'qa/source-edges-browser.mjs', 'qa/protocol.mjs']) {
  const absolute = path.resolve('experiments/speed-lab', filename); runtime.set(absolute, sha(await fs.readFile(absolute)));
}
const dimensions = [[641, 427], [960, 640], [1280, 720]], profiles = ['source', 'combined'];
const plannedPairs = eyewearModels.length * profiles.length * dimensions.length * 3;
if (args.includes('--preflight')) {
  console.log(JSON.stringify({preflight: true, backend, plannedPairs, alphaControls: eyewearModels.length * 2,
    revokedLeaseControls: eyewearModels.length, sourceId, eyewearModels, dimensions, profiles, preservation,
    frozenFiles: frozen.size, runtimeFiles: runtime.size, browserLaunched: false}));
  process.exit(0);
}
const out = path.resolve('experiments/speed-lab/qa/output', `source-edges-${new Date().toISOString().replaceAll(':', '-')}`);
await fs.mkdir(out, {recursive: true});
const report = {schema: 'speed-lab-source-edges-v1', createdAt: new Date().toISOString(), complete: false, passed: false,
  base, backend, out, sourceId, preservation, plannedPairs, dimensions, profiles, eyewearModels,
  fixture: {priorReport: {path: reportPath, sha256: sha(priorBytes)}, detection: source.detectionFile,
    detectionSHA256: source.detectionSHA256, category: mask.categoryU8, hairModel: 'hair-only'},
  frozenInputs: Object.fromEntries(frozen), runtimeHashes: Object.fromEntries(runtime), environments: {}, cases: [], controls: [], errors: [], consoleErrors: [],
  scope: ['Procedural opaque sRGB ramps/checkerboards use one unchanged preserved pose and category field to test color, sampling, ownership and readback conformance.',
    'The pose and category are deliberately reused test inputs, not detector/segmenter outputs from the procedural colors. No geometry is tuned and no inference is rerun.',
    'Both real renderer implementations receive the same procedural image, fixed detection and retagged synthetic category identity. This is separate from matched face/wearer evidence.',
    'All accepted, native-clean and final RGBA bytes must agree exactly; all final safeguards remain enabled. Unsupported alpha and immediate async lease revocation are separate controls.',
    'A single reused renderer pair per eyewear sees all A→B→A and dimension/profile transitions. Backend identity is read from each actual native WebGL context.',
    'No speed, physical camera, mobile, anatomical or visual-acceptance conclusion is made. PNG encoding and diagnostic reads are explicit QA work.']};
const save = () => fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
let browser;
await save();
try {
  browser = await chromium.launch({headless: true, args: backend === 'hardware' ? ['--enable-gpu', '--use-angle=d3d11']
    : ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']});
  const context = await browser.newContext(); await context.grantPermissions(['local-network-access'], {origin: base});
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.stack ?? error.message));
  page.on('console', message => {if (message.type() === 'error') report.consoleErrors.push(message.text());});
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base) return route.abort();
    if (url.pathname === '/qa-source-edges') return route.fulfill({contentType: 'text/html', body: '<!doctype html><title>Source edge conformance</title>'});
    if (url.pathname === '/favicon.ico') return route.fulfill({status: 204, body: ''});
    if (url.pathname === '/@vite/client') return route.fulfill({contentType: 'text/javascript', body:
      'export function createHotContext(){return {data:{},accept(){},acceptExports(){},dispose(){},prune(){},invalidate(){},on(){},off(){},send(){}};} export function injectQuery(u,q){return u+(u.includes("?")?"&":"?")+q;} export function updateStyle(){} export function removeStyle(){} export class ErrorOverlay extends HTMLElement {}'});
    if (url.pathname.startsWith('/qa-source-edge-artifact/')) {
      const bytes = resources.get(url.pathname.split('/').at(-1));
      return route.fulfill({status: bytes ? 200 : 404, contentType: 'application/octet-stream', body: bytes ?? 'Unknown frozen fixture'});
    }
    return route.continue();
  });
  await page.goto(`${base}/qa-source-edges`);
  await page.evaluate(async fixture => {
    window.sourceEdges = await (await import('/experiments/speed-lab/qa/source-edges-browser.mjs')).createSourceEdgeStudy(fixture);
  }, fixture);
  const storeRow = async (row, group) => {
    const id = `${String(report.cases.length + report.controls.length).padStart(3, '0')}-${row.eyewear}-${row.profile ?? 'lease'}-${row.width}x${row.height}-${row.pattern ?? ''}`;
    row.artifacts = {};
    for (const [name, dataURL] of Object.entries(row.pngs ?? {})) {
      assert.ok(dataURL.startsWith('data:image/png;base64,'));
      const bytes = Buffer.from(dataURL.slice('data:image/png;base64,'.length), 'base64'), filename = path.join(out, `${id}-${name}.png`);
      await fs.writeFile(filename, bytes); row.artifacts[name] = {path: filename, sha256: sha(bytes), bytes: bytes.length};
    }
    delete row.pngs; report[group].push(row); await save();
    console.log(JSON.stringify({id, passed: row.passed, group, caseCount: report.cases.length, plannedPairs, report: path.join(out, 'report.json')}));
    assert.equal(row.passed, true, `Source-edge ${id} failed; exact differences retained.`);
  };
  for (const eyewear of eyewearModels) {
    report.environments[eyewear] = await page.evaluate(id => window.sourceEdges.setModel(id), eyewear); await save();
    for (const profile of profiles) for (const [width, height] of dimensions) for (const [position, pattern] of ['A', 'B', 'A'].entries()) {
      const spec = {eyewear, profile, width, height, pattern, position, transparent: false};
      await storeRow(await page.evaluate(spec => window.sourceEdges.render(spec), spec), 'cases');
    }
    // Return to odd dimensions after the large-frame PBO/storage allocations.
    for (const profile of profiles) {
      const spec = {eyewear, profile, width: 641, height: 427, pattern: 'A', transparent: true, control: 'unsupported-alpha'};
      await storeRow(await page.evaluate(spec => window.sourceEdges.render(spec), spec), 'controls');
    }
    const spec = {eyewear, width: 641, height: 427};
    await storeRow({...await page.evaluate(spec => window.sourceEdges.revokedLease(spec), spec), control: 'revoked-async-lease'}, 'controls');
    await storeRow(await page.evaluate(spec => window.sourceEdges.render(spec),
      {...spec, profile: 'combined', pattern: 'A', transparent: false, control: 'recovery-after-revocation'}), 'controls');
  }
  await page.evaluate(() => window.sourceEdges.dispose());
  assert.equal(report.cases.length, plannedPairs);
  assert.deepEqual(report.errors, []); assert.deepEqual(report.consoleErrors, []);
  report.passed = true;
} catch (error) {
  report.errors.push(error.stack ?? error.message); process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  try {
    for (const [filename, expected] of [...frozen, ...runtime]) assert.equal(sha(await fs.readFile(filename)), expected, `Frozen source changed: ${filename}`);
    report.preservationAfter = await verifyBase(); report.sourceFreezeVerified = true;
  } catch (error) {report.errors.push(error.stack ?? error.message); report.passed = false; process.exitCode = 1;}
  report.complete = true; report.finishedAt = new Date().toISOString(); await save();
  console.log(JSON.stringify({passed: report.passed, complete: report.complete, cases: report.cases.length, controls: report.controls.length, report: path.join(out, 'report.json')}));
}
