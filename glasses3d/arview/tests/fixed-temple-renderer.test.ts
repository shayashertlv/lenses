/** Production fixed temples retain geometry, contact, endpoint history and protection through live/audit draws. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {BoxGeometry, BufferAttribute, Euler, Group, Matrix4, Mesh, MeshBasicMaterial, MeshPhysicalMaterial,
  MeshStandardMaterial, PerspectiveCamera, Quaternion, SRGBColorSpace, Vector3, WebGLRenderTarget} from 'three';
import {TryOnRenderer} from '../src/render/renderer.ts';
import type {RendererOptions} from '../src/render/renderer.ts';
import {LiveRenderer} from '../src/render/live-renderer.ts';
import {FaceSurface} from '../src/render/face-surface.ts';
import {createRearDrop} from '../src/render/rear-drop.ts';
import {createTempleClip} from '../src/render/temple-clip.ts';
import {createHairOcclusion} from '../src/render/hair-occlusion.ts';
import {createTempleTerminalFit, createTempleTerminalFitEvaluator} from '../src/render/temple-terminal-fit.ts';
import {EyewearFitSession} from '../src/render/eyewear-fit.ts';
import {TempleCheekContactEstimator} from '../src/render/temple-cheek-contact.ts';
import type {TempleVisibilityConfiguration} from '../src/render/temple-visibility.ts';
import {eyewearById, DEFAULT_EYEWEAR_ID} from '../src/eyewear/catalog.ts';
import type {Detection} from '../src/face/protocol.ts';

const canonical = JSON.parse(await readFile(new URL('../public/models/canonical-face.json', import.meta.url), 'utf8')) as {positions: number[]; indices: number[]};
const frame = {width: 640, height: 480} as HTMLCanvasElement;
const mask = {width: 4, height: 4, category: new Uint8Array(16).fill(1), hairIndex: 1};
const missing: Detection = {matrix: null, landmarks: [], inferenceMs: 0};

function detection(pitchDegrees = 28, distanceCm = 45): Detection {
  const matrix = new Matrix4().compose(new Vector3(0, 0, -distanceCm),
    new Quaternion().setFromEuler(new Euler(pitchDegrees * Math.PI / 180, 0, 0)), new Vector3(1, 1, 1));
  const camera = new PerspectiveCamera(63, 4 / 3, 1, 10_000); camera.updateMatrixWorld();
  return {matrix: matrix.toArray(), inferenceMs: 0, landmarks: Array.from({length: 478}, (_, i) => {
    if (i >= 468) return {x: .5, y: .5, z: 0};
    const projected = new Vector3().fromArray(canonical.positions, i * 3).applyMatrix4(matrix).project(camera);
    return {x: (projected.x + 1) / 2, y: (1 - projected.y) / 2, z: 0};
  })};
}

function harness(options: RendererOptions = {}) {
  const draws: {face: boolean; head: boolean; clip: boolean; relief: boolean; eyewear: boolean}[] = [];
  const backend = {capabilities: {samples: 0}, setSize() {}, setRenderTarget() {}, autoClear: true,
    render() {draws.push({face: surface.visible, head: head.visible, clip: clip.configuration !== null,
      relief: visibility.configuration !== null, eyewear: internal.eyewearPose.visible});}};
  const renderer = new (TryOnRenderer as unknown as new (backend: unknown, gl: unknown, eyewear: unknown, options: RendererOptions) => TryOnRenderer)(
    backend, {}, eyewearById(DEFAULT_EYEWEAR_ID), {sync: false, guard: false, ...options});
  const internal = renderer as unknown as {facePose: Group; eyewearPose: Group};
  // Occlusion-only fixtures start after presentation; fitting fixtures explicitly supply a fresh session.
  if (!options.fitSession) Object.assign(renderer, {fitRevealed: true});
  const root = new Group(), material = new MeshStandardMaterial();
  for (const sign of [-1, 1]) {
    const arm = new BoxGeometry(.004, .004, .17, 1, 1, 16); arm.translate(sign * .065, 0, -.1);
    root.add(new Mesh(arm, material));
  }
  const lens = new BoxGeometry(.05, .025, .002); lens.translate(0, 0, -.01);
  root.add(new Mesh(lens, new MeshPhysicalMaterial({transmission: 1})));
  internal.eyewearPose.add(root);
  const clip = createTempleClip(root), rearDrop = createRearDrop(root, -.14), hair = createHairOcclusion(root);
  const visibility = {configuration: null as TempleVisibilityConfiguration | null,
    set(value: TempleVisibilityConfiguration | null) {this.configuration = value;}, prepare() {}};
  const depth = new MeshBasicMaterial({colorWrite: false, depthWrite: true});
  const surface = new Mesh(new BoxGeometry(), depth), head = new Mesh(new BoxGeometry(), depth);
  internal.facePose.add(head);
  const observed = new BufferAttribute(new Float32Array(canonical.positions.length), 3);
  Object.assign(renderer, {canonicalPositions: canonical.positions, rearDrop, templeClip: clip,
    templeTerminalFit: createTempleTerminalFit(root, {offsetCm: renderer.eyewear.offsetCm, spreadM: .018,
      spreadStartZM: rearDrop.spreadStartZM, modelCutoffZM: -.14, maximumZM: -.115}),
    templeVisibility: visibility, hairOcclusion: hair, surfaceMesh: surface, templeHeadShell: head,
    faceSurface: new FaceSurface(canonical.positions), observedCheekAttribute: observed,
    cheekContact: new TempleCheekContactEstimator(canonical.positions, canonical.indices),
    nasalShape: {apply: ({surfacePositions}: {surfacePositions: Float32Array}) => ({surfacePositions})}});
  const positions = () => root.children.map(mesh => [...(mesh as Mesh).geometry.getAttribute('position').array]);
  return {renderer, root, rearDrop, clip, visibility, surface, head, draws, positions, observed};
}

test('ordinary renderer uses fixed return and 18mm spread across nods, camera distance and audit variants', () => {
  const {renderer, positions, rearDrop, clip, visibility, surface, head} = harness();
  const original = positions();
  renderer.pose(frame, detection(), 100); renderer.render(null);
  const fixed = positions();
  assert.notDeepEqual(fixed, original, 'the production shape includes the authored spread and inward return');
  assert.equal(rearDrop.spreadM, .018); assert.equal(rearDrop.dropM, 0);
  assert.equal(renderer.captureSnapshot?.templeTerminalFit?.startZM, -.075);
  assert.equal(typeof renderer.poseSample?.steadyLagDeg, 'number', 'production enables responsive pose smoothing by default');
  assert.equal(surface.visible, true); assert.equal(head.visible, true);
  assert.ok(clip.configuration); assert.equal(visibility.configuration?.method, 'temple-behind-head-v4');
  assert.equal(visibility.configuration?.excludeArmsFromLensInput, true);
  assert.equal(visibility.configuration?.cheekTransitionPx, 2);
  for (const [i, [pitch, distance]] of [[8, 20], [40, 70], [-30, 45]].entries()) {
    renderer.pose(frame, detection(pitch, distance), 133 + i * 33);
    for (const variant of [{}, {hair: false}, {eyewear: false}, {guard: false}]) {
      const timing = renderer.render(mask, variant);
      assert.deepEqual(positions(), fixed, 'pose and audit variants cannot bend or lengthen local geometry');
      assert.equal(timing.armSpreadM, .018); assert.equal(rearDrop.dropM, 0);
      for (const removed of ['templePreview', 'dropM', 'widthFit', 'continuity']) assert.equal(removed in timing, false);
    }
  }
  assert.equal('templePreview' in renderer, false); assert.equal('setTemplePreview' in renderer, false);
  assert.equal('rearDrop' in renderer.captureSnapshot!, false);
});

test('tracking loss clears every presented occlusion layer and reacquisition restores the same fixed geometry', () => {
  const {renderer, positions, draws} = harness();
  renderer.pose(frame, detection(), 100); renderer.render(mask); const fixed = positions();
  assert.equal(renderer.pose(frame, missing, 133), false);
  assert.equal(renderer.captureSnapshot, null); assert.equal(renderer.render(mask).hairApplied, false);
  assert.deepEqual(draws.at(-1), {face: false, head: false, clip: false, relief: false, eyewear: false});
  renderer.pose(frame, detection(), 166); renderer.render(null);
  assert.deepEqual(draws.at(-1), {face: true, head: true, clip: true, relief: true, eyewear: true});
  assert.deepEqual(positions(), fixed);
});

test('face shadows share one GPU underlay across hair audits and bypass clean-camera/no-face variants', () => {
  const {renderer, positions} = harness();
  const target = new WebGLRenderTarget(640, 480); target.texture.colorSpace = SRGBColorSpace;
  let shadowDraws = 0;
  Object.assign(renderer, {shadows: {setTempleClip() {}, render() {shadowDraws++; return target.texture;}}});
  renderer.pose(frame, detection(), 100);
  const fixed = positions();
  assert.equal(renderer.render(mask).shadowsApplied, true);
  assert.equal(renderer.render(mask, {hair: false}).shadowsApplied, true);
  assert.equal(renderer.render(mask, {eyewear: false}).shadowsApplied, false);
  assert.equal(renderer.render(mask, {shadows: false}).shadowsApplied, false);
  assert.equal(renderer.render(mask).shadowsApplied, true);
  assert.equal(shadowDraws, 1, 'variants reuse the held face and identical mask instead of advancing a second effect');
  renderer.setShadows({frameStrength: .4}); renderer.render(mask);
  assert.equal(shadowDraws, 2, 'controls invalidate the underlay without changing pose or geometry');
  renderer.setShadows({enabled: false});
  assert.equal(renderer.render(mask).shadowsApplied, false); assert.equal(shadowDraws, 2);
  renderer.setShadows({enabled: true}); renderer.pose(frame, missing, 133);
  assert.equal(renderer.render(mask).shadowsApplied, false); assert.equal(shadowDraws, 2);
  renderer.pose(frame, detection(), 166); renderer.render(mask);
  assert.equal(shadowDraws, 3); assert.deepEqual(positions(), fixed);
  target.dispose();
});

test('observed cheek changes visibility without reshaping the frame or changing its hair endpoint', () => {
  const {renderer, positions, visibility, clip} = harness();
  const input = detection(-40);
  renderer.pose(frame, input, 100); renderer.render(null);
  const local = positions(), endpoint = clip.configuration, contact = visibility.configuration!.cheekContact;
  assert.ok(contact && contact.polygon.length >= 3);
  renderer.render(null, {hair: false});
  assert.deepEqual(visibility.configuration!.cheekContact, contact, 'audit variants retain the posed contact');
  const wider = {...input, landmarks: input.landmarks.map(p => ({...p, x: .5 + (p.x - .5) * 1.08}))};
  renderer.pose(frame, wider, 133); renderer.render(null);
  assert.notDeepEqual(visibility.configuration!.cheekContact?.polygon, contact.polygon);
  assert.deepEqual(positions(), local); assert.deepEqual(clip.configuration, endpoint);
  renderer.pose(frame, missing, 166);
  assert.equal(visibility.configuration, null, 'a missing face cannot retain stale contact');
});

test('the private cheek surface is captured before nasal and rigid-side substitutions', () => {
  const {renderer, observed} = harness(), raw = new Float32Array(canonical.positions.length);
  const matrix = new Matrix4().fromArray(detection(-30).matrix!), point = new Vector3();
  for (let i = 0; i < raw.length; i += 3) point.fromArray(canonical.positions, i).applyMatrix4(matrix).toArray(raw, i);
  const expected = raw.slice();
  Object.assign(renderer, {faceSurface: {positions: raw, reconstruct: () => true},
    nasalShape: {apply: () => {raw[2]! += 12; return {surfacePositions: raw};}}});
  renderer.pose(frame, detection(-30), 100);
  assert.deepEqual(observed.array, expected, 'the first contact frame contains exact current observed depth');
  assert.notDeepEqual(raw, expected, 'the subsequent main-face substitutions exercised this fixture');
});

test('cheek smoothing preserves current rays and attachment, and does not advance during audits', () => {
  const {renderer, observed, visibility, positions} = harness(), current = new Float32Array(canonical.positions.length);
  Object.assign(renderer, {faceSurface: {positions: new Float32Array(current.length),
    reconstruct() {this.positions.set(current); return true;}}});
  const point = new Vector3(); let fixed: number[][] | null = null;
  for (let step = 0; step < 4; step++) {
    const input = detection(-30 + step * 3), transform = new Matrix4().fromArray(input.matrix!);
    for (let i = 0; i < current.length; i += 3) {
      point.fromArray(canonical.positions, i).applyMatrix4(transform);
      point.multiplyScalar((point.z + (step % 2 ? .06 : -.06)) / point.z).toArray(current, i);
    }
    renderer.pose(frame, input, 100 + step * 33);
    const filtered = observed.array;
    let differences = 0;
    for (let i = 0; i < current.length; i += 3) {
      if (Math.abs(filtered[i + 2]! - current[i + 2]!) > 1e-5) differences++;
      assert.ok(Math.abs(filtered[i + 2]! - current[i + 2]!) <= .07501);
      assert.ok(Math.abs(filtered[i]! / filtered[i + 2]! - current[i]! / current[i + 2]!) < 1e-7);
      assert.ok(Math.abs(filtered[i + 1]! / filtered[i + 2]! - current[i + 1]! / current[i + 2]!) < 1e-7);
    }
    if (step > 0) assert.ok(differences > 400, 'local shape noise is attenuated');
    fixed ??= positions(); assert.deepEqual(positions(), fixed);
    const held = filtered.slice(), version = observed.version, attachment = renderer.captureSnapshot?.eyewearMatrix;
    for (const variant of [{}, {hair: false}, {eyewear: false}, {guard: false}]) renderer.render(null, variant);
    assert.deepEqual(filtered, held); assert.equal(observed.version, version);
    assert.deepEqual(renderer.captureSnapshot?.eyewearMatrix, attachment);
  }
  renderer.pose(frame, missing, 250); renderer.pose(frame, detection(-21), 283);
  assert.deepEqual(observed.array, current, 'reacquisition seeds only the current face');
  current[2] = NaN; renderer.pose(frame, detection(-21), 316);
  assert.equal(visibility.configuration?.cheekContact, null, 'bad input cannot present a stale depth target');
});

test('consecutive renderer poses retain shadow-shape smoothing, audits hold it, and tracking loss resets it', () => {
  const {renderer, observed} = harness(), input = detection(0), rawPose = new Matrix4().fromArray(input.matrix!),
    rawInverse = rawPose.clone().invert(), current = new Float32Array(canonical.positions.length),
    shadow = new BufferAttribute(new Float32Array(canonical.positions.length), 3), point = new Vector3();
  Object.assign(renderer, {shadowReceiverAttribute: shadow, faceSurface: {positions: new Float32Array(current.length),
    reconstruct() {this.positions.set(current); return true;}}});
  const updateObserved = (localDepthChangeCm: number): void => {
    for (let i = 0; i < current.length; i += 3) {
      point.fromArray(canonical.positions, i); point.z += localDepthChangeCm;
      point.applyMatrix4(rawPose).toArray(current, i);
    }
  };
  updateObserved(0); assert.equal(renderer.pose(frame, input, 100), true);
  let observedEnergy = 0, shadowEnergy = 0;
  for (let step = 1; step <= 80; step++) {
    updateObserved(step % 2 ? .15 : -.15);
    assert.equal(renderer.pose(frame, input, 100 + step * 33), true);
    const attachedInverse = new Matrix4().fromArray(renderer.captureSnapshot!.eyewearMatrix).invert();
    if (step > 20) {
      const observedError = point.fromBufferAttribute(observed, 0).applyMatrix4(rawInverse).z - canonical.positions[2]!;
      const shadowError = point.fromBufferAttribute(shadow, 0).applyMatrix4(attachedInverse).z - canonical.positions[2]!;
      observedEnergy += observedError ** 2; shadowEnergy += shadowError ** 2;
    }
    const held = (shadow.array as Float32Array).slice(), version = shadow.version, currentObserved = observed.array.slice();
    for (const variant of [{}, {hair: false}, {eyewear: false}, {shadows: false}]) renderer.render(null, variant);
    assert.deepEqual(shadow.array, held); assert.equal(shadow.version, version, 'audit draws cannot advance the receiver filter');
    assert.deepEqual(observed.array, currentObserved, 'shadow stabilization cannot reshape the observed cheek');
  }
  assert.ok(observedEnergy > .01, 'the production cheek pass still contains meaningful local noise');
  assert.ok(Math.sqrt(shadowEnergy / observedEnergy) < .2,
    'normal pose preparation must retain enough receiver history to remove at least 80% of resting shape noise');
  const held = (shadow.array as Float32Array).slice();
  assert.equal(renderer.pose(frame, missing, 2800), false);
  updateObserved(.4); assert.equal(renderer.pose(frame, input, 2833), true);
  assert.notDeepEqual(shadow.array, held);
  const attached = new Matrix4().fromArray(renderer.captureSnapshot!.eyewearMatrix);
  for (let i = 0; i < shadow.count; i++) {
    point.fromBufferAttribute(observed, i).applyMatrix4(rawInverse).applyMatrix4(attached);
    assert.ok(point.distanceTo(new Vector3().fromBufferAttribute(shadow, i)) < 1e-5,
      'reacquisition seeds only the current local face, with no pre-loss shadow history');
  }
});

test('each camera pose advances the hair endpoint once, while audit variants preserve the exact ending', () => {
  const {renderer, clip} = harness(), maximum = renderer.eyewear.templeClipLocalZM + .025;
  let updates = 0;
  const report = {negativeZM: maximum + .01, positiveZM: maximum + .02,
    negativeFadeM: .001, positiveFadeM: .003, negativeState: 'tracking', positiveState: 'tracking', maximumZM: maximum,
    zoneStartZM: maximum + .035, negativeCandidateZM: maximum + .01, positiveCandidateZM: maximum + .02};
  Object.assign(renderer, {continuityModel: {sides: [], startZM: -.03, cutoffZM: -.14},
    projectPaths: () => null, templeEndpointTracker: {update() {updates++; return report;}, reset() {return report;}}});
  renderer.pose(frame, detection(), 100); const first = renderer.render(mask), endpoint = clip.configuration;
  assert.equal(first.templeEndNegativeZM, report.negativeZM); assert.equal(first.templeEndPositiveZM, report.positiveZM);
  assert.equal(endpoint?.negativeXFadeLengthLocalM, .001); assert.equal(endpoint?.positiveXFadeLengthLocalM, .003);
  for (const variant of [{hair: false}, {eyewear: false}, {guard: false}, {}]) renderer.render(null, variant);
  assert.equal(updates, 1); assert.deepEqual(clip.configuration, endpoint);
  renderer.pose(frame, detection(), 133); renderer.render(null);
  assert.equal(updates, 2); assert.deepEqual(clip.configuration, endpoint);
});

test('the buried cap is always applied without hair, and short external frames never grow a tail', () => {
  const {renderer, clip} = harness();
  renderer.pose(frame, detection(), 100);
  for (const variant of [{}, {hair: false}, {eyewear: false}]) {
    const timing = renderer.render(null, variant);
    assert.ok(timing.templeEndMaximumZM! > renderer.eyewear.templeClipLocalZM);
    assert.equal(clip.configuration!.negativeXCutoffLocalZM, timing.templeEndMaximumZM);
    assert.equal(clip.configuration!.positiveXCutoffLocalZM, timing.templeEndMaximumZM);
    assert.equal(clip.configuration!.fadeLengthLocalM, .005);
  }
  Object.assign(renderer, {eyewear: {...renderer.eyewear, templeClipLocalZM: -.045},
    continuityModel: {startZM: -.035, cutoffZM: -.045, sides: [[], []]}});
  assert.equal(renderer.pose(frame, detection(), 133), true);
  const timing = renderer.render(null);
  assert.equal(timing.templeEndMaximumZM, -.045); assert.equal(timing.templeEndNegativeZM, -.045);
});

test('brief tracking loss preserves a hidden hair ending, while sustained loss clears its history', () => {
  const {renderer} = harness(), maximum = -.115;
  const report = {negativeZM: -.07, positiveZM: maximum, negativeState: 'held', positiveState: 'fallback', maximumZM: maximum,
    zoneStartZM: -.035, negativeCandidateZM: null, positiveCandidateZM: null};
  let resets = 0;
  Object.assign(renderer, {continuityModel: {sides: [], startZM: -.03, cutoffZM: -.14}, projectPaths: () => null,
    templeEndpointTracker: {update() {return report;}, reset() {resets++; return report;}}});
  renderer.pose(frame, detection(), 100); renderer.render(mask);
  assert.equal(renderer.pose(frame, missing, 200), false); renderer.render(null); assert.equal(resets, 0);
  renderer.pose(frame, detection(), 300); assert.equal(renderer.render(null).templeEndNegativeZM, -.07);
  renderer.pose(frame, missing, 1401); assert.equal(resets, 1);
});

test('anterior head calibration observes before face shaping and never changes the fixed eyewear', () => {
  const {renderer, positions, head} = harness(), calls: string[] = [];
  let ratio = 1, fitted = 1;
  const report = {ratio: .94, state: 'stable', observedRatio: .94, acceptedSamples: 60, rejection: null};
  Object.assign(renderer, {
    templeHeadFit: {get ratio() {return ratio;}, get report() {return {...report, ratio};},
      observe() {calls.push('observe'); ratio = .94;}, miss() {calls.push('miss');}, reset() {ratio = 1;}},
    templeHeadShellFit: {set(value: number) {fitted = value;}, reset() {fitted = 1;}},
    nasalShape: {apply: ({surfacePositions}: {surfacePositions: Float32Array}) => {calls.push('nose'); return {surfacePositions};}},
  });
  renderer.pose(frame, detection(0), 100);
  assert.deepEqual(calls, ['observe', 'nose']); assert.equal(fitted, .94); assert.equal(head.scale.x, 1);
  assert.equal(renderer.captureSnapshot?.templeHeadFit?.ratio, .94); const fixed = positions();
  for (const variant of [{hair: false}, {eyewear: false}, {guard: false}, {}]) renderer.render(null, variant);
  assert.deepEqual(calls, ['observe', 'nose']); assert.deepEqual(positions(), fixed);
  renderer.pose(frame, detection(35), 133); assert.deepEqual(positions(), fixed);
  renderer.pose(frame, missing, 166); assert.equal(calls.at(-1), 'miss');
});

test('LiveRenderer retains ordinary hair toggling without a comparison mode or alternate geometry', async () => {
  const {renderer, positions} = harness();
  const live = new (LiveRenderer as unknown as new (renderer: TryOnRenderer) => LiveRenderer)(renderer);
  await live.prepare(frame, detection(), {} as never, {} as never, true, 100); live.finish(mask as never);
  const fixed = positions(); assert.equal(live.stats?.hairEnabled, true); assert.equal(live.stats?.hasMask, true);
  live.setHairEnabled(false);
  await live.prepare(frame, detection(), {} as never, {} as never, false, 133); live.finish(mask as never);
  assert.equal(live.stats?.hairEnabled, false); assert.equal(live.stats?.hasMask, false);
  assert.equal(live.stats?.fallbackReason, null); assert.deepEqual(positions(), fixed);
  assert.equal('templePreview' in live, false);
});

function fittingHarness(fitSession = new EyewearFitSession()) {
  const fixture = harness({fitSession, steady: null});
  const {renderer, root, rearDrop} = fixture;
  const pose = (renderer as unknown as {eyewearPose: Group}).eyewearPose;
  const asset = new Group(); asset.position.set(...renderer.eyewear.offsetCm); asset.scale.setScalar(100);
  asset.add(root); pose.add(asset);
  const front = rearDrop.opticalBounds;
  Object.assign(renderer, {eyewearAsset: asset, fitFrontBounds: front, fitFrontWidthCm: (front.max.x - front.min.x) * 100,
    terminalFitAtScale: createTempleTerminalFitEvaluator(root, {offsetCm: renderer.eyewear.offsetCm, spreadM: .018,
      spreadStartZM: rearDrop.spreadStartZM, modelCutoffZM: -.14, maximumZM: -.115})});
  return {...fixture, asset, fitSession};
}

test('glasses, colored shadows and hair composition wait for calibration and scale settling together', () => {
  const {renderer, draws} = fittingHarness(), input = detection(0);
  const target = new WebGLRenderTarget(640, 480); target.texture.colorSpace = SRGBColorSpace;
  let shadowDraws = 0, endpointUpdates = 0, endpointMask: unknown = null;
  const report = {negativeZM: -.08, positiveZM: -.09, negativeFadeM: .002, positiveFadeM: .002,
    negativeState: 'tracking', positiveState: 'tracking', maximumZM: -.115, zoneStartZM: -.035,
    negativeCandidateZM: -.08, positiveCandidateZM: -.09};
  Object.assign(renderer, {shadows: {setTempleClip() {}, render() {shadowDraws++; return target.texture;}},
    continuityModel: {sides: [], startZM: -.03, cutoffZM: -.14}, projectPaths: () => null,
    templeEndpointTracker: {update({mask: currentMask}: {mask: unknown}) {
      endpointUpdates++; endpointMask = currentMask; return report;
    }, reset() {}}});
  let sawCollecting = false, sawSettling = false;
  for (let i = 0; i < 90; i++) {
    assert.equal(renderer.pose(frame, input, 100 + i * 75), true, 'face tracking stays active during fitting');
    const fit = renderer.fitting;
    if (fit.state === 'fitted') break;
    sawCollecting ||= fit.state === 'collecting'; sawSettling ||= fit.state === 'settling';
    assert.equal(fit.ready, false);
    const held = {...fit};
    const timing = renderer.render(mask, {guard: true});
    assert.equal(timing.shadowsApplied, false); assert.equal(timing.hairApplied, false);
    assert.equal(timing.guarded, false); assert.equal(timing.safeFallback, false); assert.equal(timing.passes, 1);
    assert.equal(draws.at(-1)?.eyewear, false); assert.equal(shadowDraws, 0);
    const internals = renderer as unknown as {scene: {background: unknown}; backgroundTexture: unknown};
    assert.equal(internals.scene.background, internals.backgroundTexture, 'hidden fitting uses the clean camera underlay');
    assert.equal(endpointMask, mask, 'hair evidence prepares the first visible temple ending');
    assert.equal(endpointUpdates, i + 1);
    for (const variant of [{}, {hair: false}, {eyewear: false}, {shadows: false}]) renderer.render(mask, variant);
    assert.deepEqual(renderer.fitting, held, 'audit variants cannot finish calibration or reveal the glasses');
    assert.equal(endpointUpdates, i + 1, 'held variants cannot advance the hidden endpoint twice');
  }
  assert.ok(sawCollecting && sawSettling, 'both hidden phases were exercised');
  assert.equal(renderer.fitting.state, 'fitted'); assert.equal(renderer.fitting.ready, true);
  const visible = renderer.render(mask);
  assert.equal(draws.at(-1)?.eyewear, true); assert.equal(visible.shadowsApplied, true); assert.equal(visible.hairApplied, true);
  assert.equal(shadowDraws, 1);
  renderer.refit();
  assert.equal(renderer.fitting.ready, false);
  const hiddenAgain = renderer.render(mask);
  assert.equal(draws.at(-1)?.eyewear, false); assert.equal(hiddenAgain.shadowsApplied, false); assert.equal(hiddenAgain.hairApplied, false);
  assert.equal(shadowDraws, 1, 'Refit also hides a rerender of the previous pose');
  renderer.pose(frame, input, 8000); renderer.render(mask);
  assert.equal(renderer.fitting.state, 'collecting'); assert.equal(draws.at(-1)?.eyewear, false);
  for (let i = 1; i < 90; i++) renderer.pose(frame, input, 8000 + i * 75);
  renderer.render(mask);
  assert.equal(renderer.fitting.ready, true); assert.equal(draws.at(-1)?.eyewear, true);
  target.dispose();
});

test('a new frame reuses the fitted profile but waits for its own size to settle before appearing', () => {
  const first = fittingHarness(), input = detection(0);
  for (let i = 0; i < 90; i++) first.renderer.pose(frame, input, 100 + i * 75);
  const previous = first.renderer.fitting;
  assert.equal(previous.ready, true);
  const next = fittingHarness(first.fitSession);
  Object.assign(next.renderer, {eyewear: {...next.renderer.eyewear,
    preferredFrontRatio: next.renderer.eyewear.preferredFrontRatio! * .94}});
  assert.equal(next.renderer.fitting.state, 'fitted', 'the shared profile still describes the previous frame before posing');
  assert.equal(next.renderer.fitting.ready, false, 'readiness belongs to each frame renderer');
  next.renderer.pose(frame, input, 7000); next.renderer.render(mask);
  assert.equal(next.renderer.fitting.faceWidthCm, previous.faceWidthCm);
  assert.equal(next.renderer.fitting.state, 'settling'); assert.equal(next.renderer.fitting.ready, false);
  assert.equal(next.draws.at(-1)?.eyewear, false, 'a previously fitted session cannot flash the new frame at its initial size');
  for (let i = 1; i < 70; i++) next.renderer.pose(frame, input, 7000 + i * 75);
  next.renderer.render(mask);
  assert.equal(next.renderer.fitting.state, 'fitted'); assert.equal(next.renderer.fitting.ready, true);
  assert.equal(next.draws.at(-1)?.eyewear, true);
  assert.ok(next.renderer.fitting.scale < previous.scale);
});

test('automatic frame fitting locks one size through distance, nods, audits and tracking loss', () => {
  const {renderer, asset, positions, draws} = fittingHarness();
  const baseline = detection(0);
  const input = {...baseline, landmarks: baseline.landmarks.map(point => ({...point, x: .5 + (point.x - .5) * .9}))};
  for (let i = 0; i < 90; i++) renderer.pose(frame, input, 100 + i * 75);
  assert.equal(renderer.fitting.state, 'fitted');
  assert.equal(renderer.fitting.ready, true);
  const fitted = renderer.fitting, local = positions(), bridge = asset.position.clone();
  assert.ok(fitted.faceWidthCm !== null);
  assert.ok(Math.abs(fitted.scale - 1) > .005, 'this fixture exercises a real size correction');
  assert.equal(asset.scale.x, 100 * fitted.scale);
  assert.equal(asset.scale.x, asset.scale.y); assert.equal(asset.scale.y, asset.scale.z);
  for (const [i, [pitch, depth]] of [[35, 30], [-35, 80], [0, 45]].entries()) {
    renderer.pose(frame, detection(pitch, depth), 7000 + i * 100);
    assert.equal(renderer.fitting.scale, fitted.scale);
    assert.equal(renderer.fitting.faceWidthCm, fitted.faceWidthCm);
    assert.deepEqual(positions(), local, 'locked size keeps all local frame geometry fixed');
    for (const variant of [{}, {hair: false}, {eyewear: false}, {shadows: false}]) renderer.render(null, variant);
    assert.deepEqual(renderer.fitting, fitted, 'held-frame variants cannot advance calibration or scale');
  }
  renderer.pose(frame, missing, 7500); renderer.pose(frame, missing, 16000);
  renderer.render(mask); assert.equal(draws.at(-1)?.eyewear, false);
  assert.equal(renderer.fitting.ready, true, 'tracking loss preserves the completed fit');
  renderer.pose(frame, detection(0), 16100);
  renderer.render(mask); assert.equal(draws.at(-1)?.eyewear, true);
  assert.deepEqual(renderer.fitting, fitted);
  assert.deepEqual(asset.position, bridge, 'uniform sizing leaves the bridge anchor unchanged');
});

test('manual size follows the fitted asset and guard while refit starts fresh without a size snap', () => {
  const {renderer, asset, draws} = fittingHarness(), input = detection(0);
  for (let i = 0; i < 90; i++) renderer.pose(frame, input, 100 + i * 75);
  const fitted = renderer.fitting, originalGuard = renderer.protection!.protectedRects[0]!;
  renderer.setFitAdjustment(.08);
  assert.equal(renderer.fitting.state, 'settling'); assert.equal(renderer.fitting.ready, true);
  renderer.render(mask); assert.equal(draws.at(-1)?.eyewear, true, 'manual changes do not blink out a completed fit');
  for (let i = 0; i < 70; i++) renderer.pose(frame, input, 7000 + i * 75);
  assert.equal(renderer.fitting.faceWidthCm, fitted.faceWidthCm);
  assert.equal(renderer.fitting.adjustment, .08);
  assert.ok(renderer.fitting.scale > fitted.scale * 1.075);
  const resizedGuard = renderer.protection!.protectedRects[0]!;
  assert.ok(resizedGuard.x1 - resizedGuard.x0 > originalGuard.x1 - originalGuard.x0);
  assert.equal(asset.scale.x, 100 * renderer.fitting.scale);
  const beforeReset = renderer.fitting.scale;
  renderer.refit();
  assert.equal(renderer.fitting.state, 'collecting'); assert.equal(renderer.fitting.faceWidthCm, null);
  assert.equal(renderer.fitting.adjustment, 0); assert.equal(renderer.fitting.scale, beforeReset);
  const narrow = {...input, landmarks: input.landmarks.map(point => ({...point, x: .5 + (point.x - .5) * .9}))};
  for (let i = 0; i < 90; i++) renderer.pose(frame, narrow, 13000 + i * 75);
  assert.equal(renderer.fitting.state, 'fitted');
  assert.ok(renderer.fitting.faceWidthCm! < fitted.faceWidthCm! * .95);
  assert.ok(renderer.fitting.scale < fitted.scale);
});

test('unsupported terminal enlargement reports the actual size limit instead of silently claiming the requested fit', () => {
  const {renderer} = fittingHarness(), input = detection(0);
  for (let i = 0; i < 90; i++) renderer.pose(frame, input, 100 + i * 75);
  const internals = renderer as unknown as {terminalFitAtScale: (scale: number) => ReturnType<typeof createTempleTerminalFit>};
  const evaluate = internals.terminalFitAtScale, limit = renderer.fitting.scale * 1.02;
  internals.terminalFitAtScale = scale => scale > limit ? null : evaluate(scale);
  renderer.setFitAdjustment(.08);
  for (let i = 0; i < 70; i++) renderer.pose(frame, input, 7000 + i * 75);
  assert.equal(renderer.fitting.limited, true);
  assert.ok(renderer.fitting.scale <= limit);
  renderer.setFitAdjustment(0);
  for (let i = 0; i < 70; i++) renderer.pose(frame, input, 13000 + i * 75);
  assert.equal(renderer.fitting.limited, false);
});
