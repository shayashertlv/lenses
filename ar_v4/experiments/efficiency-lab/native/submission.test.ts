import assert from 'node:assert/strict';
import {test} from 'node:test';
import {Matrix4} from 'three';
import type {Scene} from 'three';
import {TryOnRenderer} from './renderer.ts';
import {TryOnRenderer as GRenderer} from '../../speed-lab/native/renderer.ts';
import {DEFAULT_SPEED_OPTIONS} from '../speed-options.ts';
import type {Detection} from '../../../references/perfect-temples/src/runtime/detector.ts';

for (const ownedPackState of [false, true]) test(`submission preserves G geometry/pixels and invalidates intervening draws only under owned PACK state: ${ownedPackState}`, async () => {
  const build = (Constructor: typeof TryOnRenderer | typeof GRenderer) => {
    const events: string[] = [];
    const backend = {capabilities: {samples: 4}, setSize: () => {}, getContext: () => ({isContextLost: () => false}),
      render: (scene: Scene) => {events.push(scene.children.length ? 'beauty' : 'clean');}};
    const renderer = Reflect.construct(Constructor, [backend]) as TryOnRenderer;
    const surface = new Float32Array(468 * 3); for (let index = 2; index < surface.length; index += 3) surface[index] = -60;
    Reflect.set(renderer, 'canonicalPositions', Array(468 * 3).fill(0));
    Reflect.set(renderer, 'faceSurface', {positions: surface, reconstruct: () => true});
    const pair = {beauty: {data: new Uint8ClampedArray(24).fill(9), width: 3, height: 2},
      camera: {data: new Uint8ClampedArray(24).fill(77), width: 3, height: 2}};
    let release!: () => void;
    let lease: {isCurrent: () => boolean} | undefined;
    const pending = new Promise<typeof pair>(resolve => {release = () => resolve(pair);});
    Reflect.set(renderer, 'pbo', {begin: (_width: number, _height: number, _scratch: boolean, owner?: typeof lease) => {lease = owner; events.push('begin');},
      invalidatePackState: () => {events.push('invalidate');}, capture: (slot: number) => {events.push(`capture${slot}`);},
      finish: () => {events.push('await'); return pending;}, cancel: () => {}, metrics: {queuedCalls: 2, retrievedCalls: 2, completed: true}});
    return {renderer, events, surface, pair, release, get lease() {return lease;}};
  };
  const g = build(GRenderer), candidate = build(TryOnRenderer);
  const detection: Detection = {landmarks: Array.from({length: 478}, () => ({x: .5, y: .5, z: 0})),
    matrix: new Matrix4().makeTranslation(0, 0, -60).toArray(), inferenceMs: 0};
  const frame = {width: 3, height: 2} as HTMLCanvasElement;
  const options = {...DEFAULT_SPEED_OPTIONS, reuseSourcePixels: true, fewerCopies: true, asyncReadback: true, prewarmTemples: true};
  g.renderer.setFrameOptions(options);
  candidate.renderer.setFrameOptions({...options, ownedPackState}, undefined, () => {candidate.events.push('submitted');});
  const gp = g.renderer.presentAsync(frame, detection, Array.from(g.surface), null, null, true);
  const cp = candidate.renderer.presentAsync(frame, detection, Array.from(candidate.surface), null, null, true);
  assert.deepEqual(candidate.events, ['beauty', 'begin', 'capture0', ...(ownedPackState ? ['invalidate'] : []), 'clean', 'capture1', 'submitted', 'await']);
  assert.deepEqual(candidate.events.filter(event => event !== 'submitted' && event !== 'invalidate'), g.events);
  assert.equal(g.lease, undefined); assert.equal(candidate.lease?.isCurrent() ?? false, ownedPackState);
  assert.equal(candidate.renderer.nativeImagePair, null);
  assert.deepEqual(candidate.renderer.captureSnapshot, g.renderer.captureSnapshot);
  g.release(); candidate.release(); assert.deepEqual(await Promise.all([gp, cp]), [true, true]);
  assert.deepEqual(candidate.renderer.nativeImagePair, g.renderer.nativeImagePair);
  assert.deepEqual(candidate.renderer.captureSnapshot, g.renderer.captureSnapshot);
  assert.equal(candidate.events.filter(event => event === 'submitted').length, 1);
  // Mock backends own no browser/GPU resources; mark unavailable without invoking
  // the real WebGL destruction path against these intentionally small fakes.
  Reflect.set(g.renderer, 'disposed', true); Reflect.set(candidate.renderer, 'disposed', true);
  assert.equal(candidate.lease?.isCurrent() ?? false, false, 'dispose revokes the same-pair state owner');
});
