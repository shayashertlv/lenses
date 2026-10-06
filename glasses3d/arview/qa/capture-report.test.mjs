/** arview capture: report assembly, status and exit codes (qa/capture/report.ts), the input preflight
 *  (qa/capture/glb-input.ts) and the software-renderer refusal. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {assembleCaptureReport, captureOutcome, compareSpecRows, EXIT, modelWarnings, rowPass, softwareRendererReason, specDisagreements} from './capture/report.ts';
import {readGlbInput} from './capture/glb-input.ts';

const ROWS = ['format', 'units', 'origin', 'orientation', 'transforms', 'geometry', 'components'];
/** spec.py --full's real row shape: {pass, detail} per row (see qa/fixtures/spec-full-q001.json), never bare booleans. */
const rowsOf = (failing = []) => Object.fromEntries(ROWS.map(row => [row, {pass: !failing.includes(row), detail: failing.includes(row) ? [`${row}: x`] : []}]));
/** A verbatim `python -m glasses3d.g3d_host.spec --full` output for the FT1119 q001 GLB (captured 2026-10-05). */
const REAL_SPEC = JSON.parse(fs.readFileSync(new URL('./fixtures/spec-full-q001.json', import.meta.url), 'utf8'))[0];
const modelOf = (status = 'rendered', failing = [], fallbacks = [], warnings = []) => ({status, refusal: status === 'refused' ? ['§2 Components: no lens'] : [], unchecked: [],
  spec: Object.fromEntries(ROWS.map(row => [row, {pass: !failing.includes(row), detail: failing.includes(row) ? [`${row} broken`] : []}])),
  errors: failing.map(row => `${row}: ${row} broken`), warnings: [...warnings, ...fallbacks.map(entry => entry.detail)], fallbacks,
  lens_identification: {materials: []}, measurements: {temples: {left: {present: true}}, temple_clip: {clip_m: -0.14}, fit_scale: {applied: 1.08},
    units_scale: 1, bbox_mm: [1, 2, 3], width_mm: 141.9}, rendered_sha256: 'a'.repeat(64), live_site_would_fail: ['texture 4096 px > 2048']});
const specOf = (ok = true, failing = []) => ({available: true, report: {ok, rows: rowsOf(failing), errors: failing.map(row => `${row}: x`), warnings: [],
  live_site_would_fail: ['triangles 120000 > 100000']}, command: ['python', '-m', 'glasses3d.g3d_host.spec', '--full', 'm.glb'], ms: 900});
function input(overrides = {}) {
  return {createdAt: '2026-10-05T00:00:00Z', tool: {name: 'arview capture'}, input: {path: 'm.glb', bytes: 10, sha256: 'a'.repeat(64)},
    request: {poses: 'default'}, gpu: {renderer: 'ANGLE (Intel ...)'}, spec: specOf(), modelReport: modelOf(), fitting: {ready: true, scale: 1.08},
    fitFallback: false, geometry: {sides: []}, poses: [], images: [{file: 'front__skin__beauty.png', sha256: 'b'.repeat(64)}], plannedImages: 1,
    idPalette: [], portrait: null, liveProbe: null, warnings: [], errors: [], infrastructureFailure: null, timings: {total_ms: 1}, implementation: {stable: true},
    page: {}, ...overrides};
}

test('a clean run: captured, exit 0, with the runtime summary and the live-site union', () => {
  const {report, exitCode} = assembleCaptureReport(input());
  assert.equal(exitCode, EXIT.ok); assert.equal(report.status, 'captured'); assert.equal(report.exit_code, 0);
  assert.equal(report.schema, 'arview.capture/1');
  assert.equal(report.spec.available, true); assert.equal(report.spec.ok, true);
  assert.deepEqual(report.runtime.clip, {clip_m: -0.14}); assert.deepEqual(report.runtime.fit_scale, {applied: 1.08});
  assert.equal(report.runtime.units.scale, 1); assert.equal(report.runtime.rendered_sha256_matches_input, true);
  assert.deepEqual(report.runtime.fallbacks, []);
  assert.deepEqual(report.live_site_would_fail.reasons, ['spec.py: triangles 120000 > 100000', 'arview: texture 4096 px > 2048']);
  assert.equal(report.live_site_would_fail.would_fail, true);
  assert.equal(report.live_site_would_fail.probe, 'not requested (--live-probe)');
  assert.deepEqual(report.images, [{file: 'front__skin__beauty.png', sha256: 'b'.repeat(64)}]);
  assert.ok(report.notes.some(note => /unmirrored/.test(note)));
});

test('a spec failure still captures (Astra is never blocked) but exits 3, and the model error is a top-level warning', () => {
  const {report, exitCode} = assembleCaptureReport(input({spec: specOf(false, ['origin']), modelReport: modelOf('rendered', ['origin'])}));
  assert.equal(exitCode, EXIT.specFailed); assert.equal(report.status, 'captured_with_warnings');
  assert.ok(report.warnings.includes('model error: origin: origin broken'), report.warnings.join('\n'));
  assert.deepEqual(report.runtime.spec_disagreements, []);
});

test('the validator unavailable is loud, exits 3, and the run still captures', () => {
  const {report, exitCode} = assembleCaptureReport(input({spec: {available: false, reason: 'no Python interpreter in glasses3d/.venv', command: null, ms: 0}}));
  assert.equal(exitCode, EXIT.specFailed); assert.equal(report.status, 'captured_with_warnings');
  assert.match(report.spec.reason, /^VALIDATOR UNAVAILABLE: no Python/);
  assert.ok(report.warnings.some(text => /^VALIDATOR UNAVAILABLE/.test(text)));
  const notRun = assembleCaptureReport(input({spec: null})).report;
  assert.match(notRun.spec.reason, /VALIDATOR UNAVAILABLE/);
});

test('arview rows that disagree with spec.py are listed and warned; spec.py stays the authority', () => {
  assert.deepEqual(specDisagreements(specOf(true).report, modelOf('rendered', ['units'])), [{row: 'units', python: true, arview: false}]);
  const {report} = assembleCaptureReport(input({modelReport: modelOf('rendered', ['units'])}));
  assert.deepEqual(report.runtime.spec_disagreements, [{row: 'units', python: true, arview: false}]);
  assert.deepEqual(report.runtime.spec_rows_compared, ROWS); assert.deepEqual(report.runtime.spec_rows_uncompared, []);
  assert.ok(report.warnings.some(text => /disagree with spec.py on units/.test(text)));
  assert.equal(report.status, 'captured_with_warnings');
});

test('the cross-check fires on a REAL spec.py --full report (q001): its {pass, detail} rows are compared, not skipped', () => {
  assert.equal(REAL_SPEC.ok, true);
  assert.equal(typeof REAL_SPEC.rows.units, 'object'); assert.equal(REAL_SPEC.rows.units.pass, true);
  const agree = compareSpecRows(REAL_SPEC, modelOf('rendered'));
  assert.deepEqual(agree.disagreements, []); assert.deepEqual(agree.compared, ROWS); assert.deepEqual(agree.uncompared, []);
  // arview forced to fail units and origin: both must be reported (the bug read booleans and returned [] here).
  const forced = modelOf('rendered', ['units', 'origin']);
  assert.deepEqual(specDisagreements(REAL_SPEC, forced), [{row: 'units', python: true, arview: false}, {row: 'origin', python: true, arview: false}]);
  const {report} = assembleCaptureReport(input({spec: {available: true, report: REAL_SPEC, command: ['python'], ms: 400}, modelReport: forced}));
  assert.deepEqual(report.runtime.spec_disagreements.map(entry => entry.row), ['units', 'origin']);
  assert.ok(report.warnings.some(text => /disagree with spec.py on units \(spec.py pass, arview fail\), origin/.test(text)), report.warnings.join('\n'));
});

test('row verdicts: {pass} objects and bare booleans compare; anything else is reported as uncompared, never skipped silently', () => {
  assert.equal(rowPass({pass: false, detail: []}), false); assert.equal(rowPass(true), true);
  assert.equal(rowPass({detail: []}), null); assert.equal(rowPass(undefined), null); assert.equal(rowPass('pass'), null);
  const booleans = {rows: Object.fromEntries(ROWS.map(row => [row, row !== 'geometry']))};
  assert.deepEqual(specDisagreements(booleans, modelOf('rendered')), [{row: 'geometry', python: false, arview: true}]);
  const broken = {ok: true, rows: {...rowsOf(), units: {detail: ['no verdict']}}};
  const result = compareSpecRows(broken, modelOf('rendered'));
  assert.deepEqual(result.uncompared.map(entry => entry.row), ['units']);
  const {report} = assembleCaptureReport(input({spec: {available: true, report: broken, command: ['python'], ms: 1}}));
  assert.ok(report.warnings.some(text => /could not compare units/.test(text)), report.warnings.join('\n'));
  assert.equal(report.status, 'captured_with_warnings');
});

test('rows arview refused before checking ("not checked") are uncompared with the reason, never false disagreements', () => {
  // A missing external image: arview refuses at Format before parsing, every other row fails as "not checked"; spec.py
  // fails Format and passes the rest.
  const why = 'not checked: missing external resource(s) next to the file: ["image 0: missing_texture.png"]';
  const refused = {...modelOf('refused'), spec: Object.fromEntries(ROWS.map(row => [row, row === 'format'
    ? {pass: false, detail: ['missing external resource(s) next to the file']} : {pass: false, detail: [why]}]))};
  const result = compareSpecRows({ok: false, rows: rowsOf(['format'])}, refused);
  assert.deepEqual(result.compared, ['format']); assert.deepEqual(result.disagreements, []);
  assert.deepEqual(result.uncompared.map(entry => entry.row), ROWS.filter(row => row !== 'format'));
  assert.match(result.uncompared[0].reason, /^arview did not check it: missing external resource/);
  const {report, exitCode} = assembleCaptureReport(input({spec: specOf(false, ['format']), modelReport: refused, images: []}));
  assert.equal(exitCode, EXIT.refused); assert.equal(report.status, 'refused');
  assert.ok(!report.warnings.some(text => /disagree with spec.py/.test(text)), report.warnings.join('\n'));
  // One clause for the six rows that share the reason.
  assert.ok(report.warnings.some(text => text.startsWith('the arview-vs-spec.py §2 cross-check could not compare units, origin, orientation, transforms, '
    + 'geometry, components (arview did not check it: missing external resource')), report.warnings.join('\n'));
});

test('model warnings, errors and fallbacks (posed and portrait) are top-level warnings and never leave the status captured', () => {
  const warned = modelOf('rendered', [], [], ['FIT SCALE: the live fit draws this model at x0.956 (-4.4 %)', 'ORIGIN: bridge underside is at y +10.24 mm']);
  const outcome = assembleCaptureReport(input({modelReport: warned}));
  assert.equal(outcome.report.status, 'captured_with_warnings'); assert.equal(outcome.exitCode, EXIT.ok);
  assert.ok(outcome.report.warnings.includes('model: FIT SCALE: the live fit draws this model at x0.956 (-4.4 %)'));
  assert.ok(outcome.report.warnings.includes('model: ORIGIN: bridge underside is at y +10.24 mm'));
  assert.equal(captureOutcome(input({modelReport: warned})).status, 'captured_with_warnings');
  // A render-time fallback seen only by the portrait renderer.
  const portrait = {model_report: {status: 'rendered', warnings: ['SHADOWS: off (render)'], errors: [],
    fallbacks: [{subsystem: 'shadows', detail: 'SHADOWS: off (render)', stage: 'render'}, {subsystem: 'guard', detail: 'guard disabled', stage: 'render'}]}};
  assert.deepEqual(modelWarnings(modelOf('rendered'), portrait), ['portrait model: SHADOWS: off (render)', 'portrait model fallback: guard disabled']);
  const withPortrait = assembleCaptureReport(input({portrait}));
  assert.equal(withPortrait.report.status, 'captured_with_warnings');
  assert.ok(withPortrait.report.warnings.includes('portrait model: SHADOWS: off (render)'));
  // The portrait repeating the posed renderer's own load warnings adds nothing.
  assert.deepEqual(modelWarnings(warned, {model_report: {status: 'rendered', warnings: warned.warnings, errors: [], fallbacks: []}}),
    warned.warnings.map(text => `model: ${text}`));
  // A refused model's report is the refusal, not warnings of a capture.
  assert.deepEqual(modelWarnings(modelOf('refused', [], [], ['x']), null), []);
});

test('refused and unchecked exit 4; infrastructure failures exit 1 whatever else is true', () => {
  assert.deepEqual(captureOutcome(input({modelReport: modelOf('refused')})), {status: 'refused', exitCode: EXIT.refused});
  assert.deepEqual(captureOutcome(input({modelReport: modelOf('unchecked')})), {status: 'unchecked', exitCode: EXIT.refused});
  assert.deepEqual(captureOutcome(input({infrastructureFailure: 'STALL: step "gpu" did not finish within 5 s'})), {status: 'infrastructure_failed', exitCode: EXIT.infrastructure});
  assert.deepEqual(captureOutcome(input({modelReport: null})), {status: 'infrastructure_failed', exitCode: EXIT.infrastructure});
  const {report} = assembleCaptureReport(input({infrastructureFailure: 'STALL: x', images: []}));
  assert.equal(report.infrastructure_failure, 'STALL: x'); assert.equal(report.status, 'infrastructure_failed');
  // WP1 review E/F: arview RENDERED a model whose external resource could not be read; §1 refuses it, so the capture does
  // (status refused, exit 4) whatever arview's own status says, and says why in capture_refusal.
  const why = 'arview RENDERED a model whose external resource(s) ["a.png"] could not be read';
  assert.deepEqual(captureOutcome(input({modelReport: modelOf('rendered'), captureRefusal: why})), {status: 'refused', exitCode: EXIT.refused});
  const refused = assembleCaptureReport(input({modelReport: modelOf('rendered'), captureRefusal: why, errors: [why]}));
  assert.equal(refused.exitCode, EXIT.refused); assert.equal(refused.report.status, 'refused'); assert.equal(refused.report.capture_refusal, why);
  assert.equal(assembleCaptureReport(input({})).report.capture_refusal, null);
});

test('missing images, capture warnings, errors and model fallbacks make the status captured_with_warnings, never silently captured', () => {
  const fallback = modelOf('rendered', [], [{subsystem: 'units', detail: 'UNITS: millimetres, rescaled', stage: 'load'}]);
  assert.deepEqual(captureOutcome(input({modelReport: fallback})), {status: 'captured_with_warnings', exitCode: EXIT.ok});
  assert.deepEqual(assembleCaptureReport(input({modelReport: fallback})).report.runtime.fallbacks, fallback.fallbacks);
  // A fallback with no matching warning sentence is mirrored on its own.
  const bare = {...modelOf('rendered'), fallbacks: [{subsystem: 'hair', detail: 'hair occlusion off', stage: 'render'}]};
  assert.ok(assembleCaptureReport(input({modelReport: bare})).report.warnings.includes('model fallback: hair occlusion off'));
  assert.equal(captureOutcome(input({plannedImages: 3})).status, 'captured_with_warnings');
  assert.equal(captureOutcome(input({warnings: ['no left junction crop']})).status, 'captured_with_warnings');
  assert.equal(captureOutcome(input({errors: ['page error: x']})).status, 'captured_with_warnings');
});

test('the live probe verdict feeds would_fail; static and runtime verdicts are kept apart', () => {
  const clean = {...modelOf(), live_site_would_fail: []};
  const noStatic = {...specOf(), report: {...specOf().report, live_site_would_fail: []}};
  const refused = assembleCaptureReport(input({modelReport: clean, spec: noStatic, liveProbe: {verdict: 'refused', text: 'no eligible posterior opaque shaft'}})).report;
  assert.equal(refused.live_site_would_fail.would_fail, true); assert.equal(refused.live_site_would_fail.probe.verdict, 'refused');
  assert.equal(refused.live_site_would_fail.static_would_fail, false); assert.equal(refused.live_site_would_fail.runtime_would_fail, true);
  assert.equal(refused.live_site_would_fail.verdicts_agree, false);
  assert.ok(refused.warnings.some(text => /^LIVE VERDICTS DISAGREE: the static live-site rules pass this model, but main's unmodified ar\/ ended 'refused': no eligible/.test(text)));
  const loaded = assembleCaptureReport(input({modelReport: clean, spec: noStatic, liveProbe: {verdict: 'loaded', text: 'ok'}})).report;
  assert.equal(loaded.live_site_would_fail.would_fail, false); assert.equal(loaded.live_site_would_fail.verdicts_agree, true);
  assert.equal(loaded.live_site_would_fail.contradiction, null);
  assert.equal(loaded.status, 'captured');
  const notProbed = assembleCaptureReport(input()).report.live_site_would_fail;
  assert.equal(notProbed.runtime_verdict, 'not_probed'); assert.equal(notProbed.runtime_would_fail, null); assert.equal(notProbed.verdicts_agree, null);
  assert.equal(notProbed.static_would_fail, true);
});

test('q001: main\'s ar/ loaded it although the static rules predict a failure: the contradiction is a field and a loud warning', () => {
  // The round-2 review case: --live-probe on q001 loaded (create ok, 51/51 poses tracked, first frame guarded) while
  // spec.py and arview both flag "frame material 'Clear pads.001' is not fully opaque".
  const opaque = "frame material 'Clear pads.001' is not fully opaque";
  const model = {...modelOf(), live_site_would_fail: [opaque]};
  const spec = {...specOf(), report: {...specOf().report, live_site_would_fail: [opaque]}};
  const probe = {verdict: 'loaded', text: 'Loaded model.glb', steps: [{step: 'create', ok: true}, {step: 'first_frame', ok: true, tracked_poses: 51, guarded: true}]};
  const {report, exitCode} = assembleCaptureReport(input({modelReport: model, spec, liveProbe: probe}));
  const live = report.live_site_would_fail;
  assert.equal(live.static_would_fail, true); assert.equal(live.runtime_verdict, 'loaded'); assert.equal(live.runtime_would_fail, false);
  assert.equal(live.verdicts_agree, false); assert.equal(live.would_fail, true);
  assert.deepEqual(live.reasons, [`spec.py: ${opaque}`, `arview: ${opaque}`]);
  assert.match(live.contradiction, /^LIVE VERDICTS DISAGREE: main's unmodified ar\/ LOADED this model \(create ok, 51 poses tracked, first frame guarded\), but the static/);
  assert.ok(report.warnings.includes(live.contradiction));
  assert.equal(report.status, 'captured_with_warnings'); assert.equal(exitCode, EXIT.ok);
  // A probe that could not run has no runtime verdict and no contradiction.
  const failed = assembleCaptureReport(input({modelReport: model, spec, liveProbe: {verdict: 'probe_failed', text: 'x'}})).report.live_site_would_fail;
  assert.equal(failed.runtime_would_fail, null); assert.equal(failed.verdicts_agree, null); assert.equal(failed.contradiction, null); assert.equal(failed.would_fail, true);
});

test('software renderers are refused, hardware ANGLE passes, an unavailable string cannot be verified', () => {
  assert.equal(softwareRendererReason('ANGLE (Intel, Intel(R) Arc(TM) 140T GPU (16GB) (0x00007D51) Direct3D11 vs_5_0 ps_5_0, D3D11)'), null);
  assert.equal(softwareRendererReason('ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)'), null);
  for (const software of ['ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)', 'llvmpipe (LLVM 15.0.7, 256 bits)',
    'ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0, D3D11-10.0)', 'Google SwiftShader']) {
    assert.match(softwareRendererReason(software), /software rasteriser/, software);
  }
  assert.match(softwareRendererReason(null), /cannot be verified/); assert.match(softwareRendererReason(''), /cannot be verified/);
});

/** A minimal GLB container around a JSON object. */
function glb(json) {
  const text = Buffer.from(JSON.stringify(json)); const padded = Buffer.concat([text, Buffer.alloc((4 - text.length % 4) % 4, 0x20)]);
  const header = Buffer.alloc(20);
  header.writeUInt32LE(0x46546c67, 0); header.writeUInt32LE(2, 4); header.writeUInt32LE(20 + padded.length, 8);
  header.writeUInt32LE(padded.length, 12); header.writeUInt32LE(0x4e4f534a, 16);
  return new Uint8Array(Buffer.concat([header, padded]));
}

test('the input preflight lists external resources and extensions, and names a broken container without deciding acceptance', () => {
  const external = readGlbInput(glb({asset: {version: '2.0'}, buffers: [{byteLength: 4, uri: 'model.bin'}, {byteLength: 4}],
    images: [{uri: 'data:image/png;base64,AAAA'}, {uri: 'tex/a b.png'}, {uri: 'model.bin'}], extensionsUsed: ['KHR_draco_mesh_compression'],
    extensionsRequired: ['KHR_draco_mesh_compression']}));
  assert.equal(external.containerError, null);
  assert.deepEqual(external.externalUris, ['model.bin', 'tex/a b.png']);
  assert.deepEqual(external.extensionsUsed, ['KHR_draco_mesh_compression']); assert.deepEqual(external.extensionsRequired, ['KHR_draco_mesh_compression']);
  assert.match(readGlbInput(new Uint8Array(8)).containerError, /too short/);
  const notGlb = glb({}); notGlb[0] = 0;
  assert.match(readGlbInput(notGlb).containerError, /magic/);
  const truncated = glb({asset: {}}).slice(0, 24);
  assert.match(readGlbInput(truncated).containerError, /declares/);
});
