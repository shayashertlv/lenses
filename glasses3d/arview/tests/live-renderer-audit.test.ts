/** Hold & audit with a hair schedule: the CPU reference composes only a frame's own mask, so an audit passes over frames
 *  that draw a mask reused from another frame, for at most AUDIT_OWN_MASK_FRAMES, and says so after that. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AUDIT_OWN_MASK_FRAMES, LiveRenderer} from '../src/render/live-renderer.ts';

function harness() {
  const inner = {
    async waitForPreviousFrame() {return {gpuWaitMs: 0, polls: 1, timedOut: false};},
    fitting: {ready: true},
    pose() {return true;}, setMaskWarp() {},
    render(): {hairApplied: boolean; safeFallback: boolean} {return {hairApplied: inner.fitting.ready, safeFallback: false};},
    poseSample: null, dispose() {},
  };
  const live = new (LiveRenderer as unknown as new (renderer: unknown) => LiveRenderer)(inner);
  const mask = {width: 4, height: 4, category: new Uint8Array(16), hairIndex: 1} as never;
  /** Draws one frame; true when it was audited (the fake renderer cannot run the audit itself, which is caught). */
  const frame = async (carried: boolean, withMask = true): Promise<boolean> => {
    await live.prepare({width: 4, height: 4} as never, {landmarks: []} as never, {sourceSHA256: 'a', detectionSHA256: 'b', eyewearModel: 'x'} as never,
      {id: 'hair-only'} as never, true, 0);
    live.finish(withMask ? mask : null, null, carried);
    return live.stats!.audit.ran;
  };
  return {live, frame, setReady: (value: boolean) => {inner.fitting.ready = value;}};
}

async function quietly<T>(run: () => Promise<T>): Promise<T> {
  const warn = console.warn; console.warn = () => {};
  try {return await run();} finally {console.warn = warn;}
}

test('an audit passes over frames with a reused mask and holds the first frame that draws its own', () => quietly(async () => {
  const {live, frame} = harness();
  assert.equal(await frame(false), false, 'nothing is audited without a request');
  assert.equal(live.auditRequestPending, false);
  live.requestAudit(); assert.equal(live.auditRequestPending, true);
  for (let k = 0; k < 5; k++) assert.equal(await frame(true), false, `reused mask ${k} is passed over`);
  assert.equal(live.auditRequestPending, true);
  assert.equal(await frame(false), true, 'the own mask is audited');
  assert.equal(live.auditRequestPending, false);
  assert.equal(await frame(false), false, 'one request, one audit');
}));

test('after AUDIT_OWN_MASK_FRAMES reused masks the audit takes a reused one; a frame without a mask is audited at once', () => quietly(async () => {
  const {live, frame} = harness();
  live.requestAudit();
  for (let k = 0; k < AUDIT_OWN_MASK_FRAMES; k++) assert.equal(await frame(true), false);
  assert.equal(await frame(true), true, 'the reused mask is audited once the wait is spent');
  live.requestAudit();
  assert.equal(await frame(true), false, 'a new request starts a new wait');
  assert.equal(await frame(true, false), true, 'no mask drawn: nothing to wait for');
}));

test('an audit waits for fitting and does not spend its own-mask wait on hidden calibration frames', () => quietly(async () => {
  const {live, frame, setReady} = harness();
  setReady(false); live.requestAudit();
  for (let k = 0; k <= AUDIT_OWN_MASK_FRAMES; k++) assert.equal(await frame(true), false);
  assert.equal(await frame(false), false, 'even a fresh mask cannot audit glasses before they appear');
  assert.equal(live.auditRequestPending, true);
  assert.equal(live.stats?.hasFace, true, 'hidden glasses do not mean missing face tracking');
  assert.equal(live.stats?.fallbackReason, 'Fitting glasses before showing them.');
  setReady(true);
  assert.equal(await frame(true), false, 'the own-mask wait begins after fitting completes');
  assert.equal(await frame(false), true, 'the first fitted frame with its own mask is audited');
  assert.equal(live.auditRequestPending, false);
}));
