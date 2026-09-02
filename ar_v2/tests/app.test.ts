/**
 * Two pieces of `src/app/` that can be tested without a browser, and both
 * of them are here because the faults they guard were INVISIBLE. A third,
 * the frame lock, is in `framelock.test.ts` — it needs a fake `document` on
 * `globalThis`, and `node --test` gives each FILE its own process, so keeping
 * it out of here is what stops that fake reaching the enrollment client.
 *
 * A frozen "working out your measurements…" and a loop that quietly moved onto
 * a 60 Hz timer both present to a wearer as "this machine is too slow". Neither
 * throws, neither logs anything a wearer would send, and neither is reachable
 * from the pipeline suite, because the pipeline does not know the app exists.
 *
 * `src/app/main.ts` calls `boot()` at module scope and cannot be imported under
 * Node at all, so the loop test reads its COMPILED form and instantiates the one
 * function it is about. That is uglier than importing it, and it is deliberate:
 * the alternative is testing a copy of the loop rather than the loop.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { readFileSync } from 'node:fs';

import { createEnrollClient } from '../src/app/enroll-client.js';
import { loadBasis, loadRegions, loadTemplateMesh } from '../src/testkit/fixtures.js';

describe('the enrollment client survives a solve that fails', () => {
  const mesh = loadTemplateMesh();
  const basis = loadBasis();
  const regions = loadRegions();

  it('detaches its handshake handlers, keeps the worker, and settles the promise', async () => {
    // Finding 10(a) and 10(b) together.
    //
    // (a) The handshake's `onmessage` used to stay bound for the life of the
    //     worker, so a solve-time `{type:'error', id}` reply — the ordinary
    //     shape of a solve that threw, hours later — reached it too. It logged
    //     "enrollment worker failed to initialise" about a worker that had
    //     initialised perfectly, and terminated the live one. Every subsequent
    //     scan then posted into a dead worker and waited out the full 60 s
    //     timeout before finding out.
    //
    // (b) Both fallback sites were a bare `resolve(runInline(request))` in an
    //     executor that captured only `resolve`, so `runInline` throwing settled
    //     NOTHING: the promise stayed pending forever and the app parked on
    //     "working out your measurements…".
    let terminated = 0;
    let instance: FakeWorker | null = null;

    class FakeWorker {
      onmessage: ((e: { data: unknown }) => void) | null = null;
      onerror: ((e: unknown) => void) | null = null;
      listeners: ((e: { data: unknown }) => void)[] = [];
      constructor() { instance = this; }
      addEventListener(_t: string, fn: (e: { data: unknown }) => void) { this.listeners.push(fn); }
      removeEventListener(_t: string, fn: (e: { data: unknown }) => void) {
        this.listeners = this.listeners.filter((f) => f !== fn);
      }
      terminate() { terminated++; }
      postMessage(msg: { type?: string }) {
        if (msg.type === 'init') {
          queueMicrotask(() => this.deliver({ type: 'ready', vertexCount: 468, basisDim: 20 }));
        }
      }
      deliver(data: unknown) {
        const e = { data };
        if (this.onmessage) this.onmessage(e);
        for (const fn of [...this.listeners]) fn(e);
      }
    }

    const previous = (globalThis as Record<string, unknown>).Worker;
    (globalThis as Record<string, unknown>).Worker = FakeWorker;
    try {
      const client = await createEnrollClient('x', 'y', { mesh, basis, regions });

      // (a) The handshake let go of both handlers on the way out, and it kept
      // the worker it was handshaking with.
      assert.equal(instance!.onmessage, null, 'the handshake left onmessage bound');
      assert.equal(instance!.onerror, null, 'the handshake left onerror bound');
      assert.equal(client.available, true);

      // (b) A solve that fails on BOTH sides. The frames carry empty typed
      // arrays, which trips the client's own detached-buffer guard inside
      // `runInline` — so this exercises the SETTLE path rather than `enroll`.
      const request = {
        frames: [{
          landmarks: new Float64Array(0),
          sigmaPx: new Float64Array(0),
          visibility: new Float64Array(0),
          beat: 'neutral',
        }],
        imageWidth: 1280,
        imageHeight: 720,
        irisMm: null,
        knownPdMm: null,
      };
      const pending = client.run(request as never);
      instance!.deliver({ type: 'error', id: 1, message: 'boom' });

      // The promise must REJECT rather than hang. `assert.rejects` is what turns
      // "pending forever" from a passing test into a timeout.
      await assert.rejects(pending, /empty frames/);
      assert.equal(terminated, 0, 'a failed SOLVE killed a healthy worker');
      assert.equal(client.available, true, 'the worker was dropped over one bad solve');
      assert.equal(client.lastRanOn, 'main', 'the fallback did not record where it ran');

      // And terminate always nulls the worker, so `available` cannot lie.
      client.close();
      assert.equal(client.available, false);
      assert.equal(terminated, 1);
    } finally {
      if (previous === undefined) delete (globalThis as Record<string, unknown>).Worker;
      else (globalThis as Record<string, unknown>).Worker = previous;
    }
  });
});

describe('the wear branch keeps its wiring', () => {
  it('hands the tracker the visibility the estimator computed', () => {
    // Crude by design: main.ts boots at module scope so it cannot be
    // imported under Node, and this exact bug class — a computed signal
    // silently dropped on the floor between the estimator and its consumer —
    // already shipped once on the enroll side (visibility fill(1), caught
    // only by a real wearer's noseObservations fingerprint) and was
    // mechanized AGAIN by the tilt-pass review: deleting `visibility` from
    // the wear-branch track() call leaves the whole suite green. A textual
    // fingerprint converts that silent revert into a loud failure.
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    assert.match(text, /landmarks,\s*sigmaPx,\s*visibility,\s*intrinsics/,
      'the wear-branch track() call no longer passes visibility — the far-side cull is dead in production');
  });
});

/**
 * One compiled function body, **with its comments removed**.
 *
 * `tsc` does not strip comments, so every docstring and `//` line in `main.ts`
 * is sitting in `dist/src/app/main.js` — which is trap 5 in
 * `docs/NEXT-SESSION.md` section 6 ("a textual gate on an English word is a
 * check that cannot fail"), and it bites in BOTH directions. The two
 * assertions below are `doesNotMatch` on `fill(1)` and on `seat.pose`, and the
 * comments explaining why those are gone say the words `fill(1)` and
 * `seat.pose`. Written naively, both tests fail on a correct build, which is
 * how this helper came to exist.
 *
 * Line and block comments only. It would damage a string literal containing
 * `//`, and nothing asserted on here is one.
 */
function codeOf(file: string, fn: string): string {
  const text = readFileSync(new URL(`../src/app/${file}.js`, import.meta.url), 'utf8');
  const at = text.indexOf(`function ${fn}`);
  assert.ok(at >= 0, `${fn} has been renamed or moved out of app/${file}`);
  const body = text.slice(at, text.indexOf('\nfunction ', at + 1));
  return body.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
}

describe('the scan does not invent what it could not see', () => {
  it('collectFrame refuses the pre-pose frame instead of filling visibility with 1', () => {
    // Before the first pose there is nothing to rasterise against, so
    // `estimateSigma` is not run and `visibility` is deliberately `null` — and
    // `collectFrame` overwrote that with `fill(1)`, asserting that every
    // landmark including the far-side nose was fully visible on the one frame
    // where least was known.
    //
    // ONE frame per pose acquisition, not every frame: the review's blast
    // radius belongs to the whole-stream version of this bug, which
    // `main.ts` documents as already fixed and whose fingerprint was a real
    // wearer's `noseObservations` equal to `framesUsed`. This one reached
    // keyframe selection in 1 cell of 12 and cost 0.005 mm of REPORTED nose
    // precision there.
    const body = codeOf('main', 'collectFrame');
    assert.match(body, /if \(!visibility\)\s*return/,
      'collectFrame no longer refuses the pre-pose frame');
    assert.doesNotMatch(body, /fill\(1\)/,
      'collectFrame is asserting full visibility for a frame nothing was rasterised against');
  });

  it('the frame sanity tripwire reads the matrix that is drawn', () => {
    // This check named `render/convert.ts` in its warning string for its whole
    // life and never read a converter or a node matrix. It computed from
    // `seat.pose`, which is strictly UPSTREAM of the `applySeat` call that
    // introduces the CV->GL double flip, so a double-flipped seat put the lens
    // centre 121.3 mm out and it printed "sanity ok" — with all three numbers
    // bit-identical to the correct case.
    const body = codeOf('main', 'frameSanityTripwire');
    assert.match(body, /frameNode\.matrix/,
      'frameSanityTripwire reads seat.pose again — it is upstream of applySeat and '
      + 'cannot see the double flip its own message blames');
    assert.doesNotMatch(body, /seat\.pose/,
      'the tripwire is back on the pose as well as the matrix — two sources is how '
      + 'it came to be reading the one that could not fail');
  });
});

describe('the scan supplies the silhouette the bundle asks for', () => {
  // Textual, because `main.ts` boots at module scope and the two other files
  // here need a `Worker`. This is the third instance in this file of the same
  // failure class — a computed signal dropped on the floor between the place
  // that has it and the place that reads it — and this one survived for the
  // whole life of the feature because the field that would have shown it,
  // `BundleReport.silhouetteResiduals`, had no consumers at all.
  //
  // It took THREE sites to be right and only one to be wrong: `collectFrame`
  // hard-coded null, `enroll-client` omitted the field from the postMessage,
  // and the worker's message type did not carry it. The inline fallback passed
  // `request.frames` straight through, so the two solve paths would have
  // disagreed about which problem they were solving.
  const read = (name: string) =>
    readFileSync(new URL(`../src/app/${name}.js`, import.meta.url), 'utf8');

  it('collectFrame no longer hard-codes silhouette: null', () => {
    const text = read('main');
    const body = codeOf('main', 'collectFrame');
    assert.doesNotMatch(body, /silhouette: null/,
      'collectFrame is back to hard-coding silhouette: null — every silhouette '
      + 'path in bundle.ts then continues, and production runs the harness\'s '
      + 'no-silhouette ablation on every real scan');
    assert.match(text, /function scanSilhouette\(/,
      'the scan-phase silhouette is gone; the contour term is dead again');
  });

  it('the worker path carries it too, and it is the path that runs', () => {
    // `enroll-client` posts a hand-written subset of each frame's fields. A
    // field missing from THAT list is dropped in silence, and the worker is
    // the path that runs whenever a Worker can be constructed at all.
    assert.match(read('enroll-client'), /silhouette: f\.silhouette/,
      'enroll-client drops the silhouette on the way to the worker again');
    assert.doesNotMatch(read('enroll.worker'), /silhouette: null/,
      'the worker rebuilds every frame with silhouette: null again');
  });
});

describe('a stored camera is not planted on a source of another size', () => {
  it('neither intrinsics site takes a solved record verbatim', () => {
    // Textual for the same reason the two fingerprints above are: main.ts
    // boots at module scope and cannot be imported under Node.
    //
    // This defect is silent by construction and no residual can see it. PnP
    // absorbs a wrong focal length into DEPTH, so the reprojection rms stays
    // at 4.95-5.90 px against a 22 px gate and `t[2] > 50` passes on 90 of 90
    // frames, while the pose is up to 802 mm out and the frame is drawn 185 to
    // 663 px off the face. The deterministic reproducer needs no hardware
    // change: scan on a camera, reload with the camera unavailable, and
    // `startSource` falls back to a 1024x1024 still while the model carries
    // 1280x720.
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    assert.doesNotMatch(text, /app\.intrinsics = model\.intrinsicsSolved\s*\?\s*model\.intrinsics\b/,
      'adoptModel takes a stored record verbatim again - 185 px of misalignment at a '
      + 'changed camera resolution, with a 5 px reprojection residual that no gate sees');
    assert.doesNotMatch(text, /app\.intrinsics = app\.model\?\.intrinsics\s*\?\?/,
      'startSource takes a stored record verbatim again, and it does not even check '
      + 'intrinsicsSolved - it is masked only by boot ordering');
    assert.match(text, /function intrinsicsForSource\(/,
      'the rescale helper is gone; both sites are back to planting a record');
  });
});

describe('the source is not a boot-time fact', () => {
  it('the loop asks the lock whether the source changed shape, and re-derives when it did', () => {
    // Textual for the reason its neighbours are: `main.ts` boots at module
    // scope and cannot be imported under Node. The BEHAVIOUR of the check is
    // pinned properly in `framelock.test.ts`; what is pinned here is that the
    // loop performs it at all, and what it does about a scan in progress.
    //
    // `lock.resize` and the intrinsics assignment ran exactly once, in
    // `startSource`, whose only caller is `boot`. A camera `Source`'s width and
    // height are live getters over `video.videoWidth`, so a rotation or a
    // renegotiation left `submit` mapping the whole new frame onto the whole
    // old canvas. Measured through the real `solvePnP` against boot intrinsics
    // (scratchpad/f29-squash.mjs): a 4:3 renegotiation costs **50.9 mm of depth
    // at 7.59 px of residual**, under `SCAN_MAX_RMS_PX` (22) and under the
    // tracker's `maxRmsPx` (14) — nothing refuses. A rotation costs 296 mm at
    // 38.5 px, which refuses permanently and says only "hold steady".
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');

    assert.match(text, /app\.lock\.syncTo\(app\.source\.width, app\.source\.height\)/,
      'the render loop no longer asks whether the source changed shape — a rotation or a '
      + 'renegotiation goes back to being drawn into the boot-sized canvas, 50.9 mm out '
      + 'with a residual no gate looks at');

    // One place that derives from the size, called from both entry points.
    assert.match(text, /function adoptSourceSize\(/,
      'adoptSourceSize is gone; the size-derived quantities are inline again and will '
      + 'drift apart between the boot path and the mid-session one');
    const calls = text.match(/adoptSourceSize\(app,/g) ?? [];
    assert.ok(calls.length >= 2,
      `adoptSourceSize is called ${calls.length} time(s) — it exists to be the one place `
      + 'BOTH the source switch and the mid-session change go through');

    // And the scan cannot straddle the change.
    assert.match(text, /syncTo[\s\S]{0,900}?scanGen\+\+/,
      'a shape change no longer abandons the scan in progress. `BundleFrame` carries no '
      + 'intrinsics of its own, so frames from before and after the change describe two '
      + 'different projections and the bundle fits one camera to both');
  });
});

describe('a new face gets a new occluder calibration', () => {
  it('adopting a model clears the edge-snap field', () => {
    // `CalibrationField.reset()` had NO production caller until the
    // landed-code review found it, and the snap test that claimed "a rescan
    // starts a fresh face" was calling reset() directly — asserting a
    // behaviour production never reached. The cost was not theoretical:
    // after ~15 wear frames every touched vertex sits at `weightCap`, so the
    // next face moves the estimate by at most a sixteenth per observation
    // AND the agreement gate rejects any push more than `agreementMm` from
    // the previous wearer's value. The old boundary would have been frozen
    // in for the rest of the session.
    //
    // Textual, for the same reason the wear-branch visibility fingerprint is:
    // main.ts boots at module scope and cannot be imported under Node, and
    // this is exactly the silent-revert class that has shipped here before.
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    const adopt = text.slice(text.indexOf('function adoptModel'));
    const body = adopt.slice(0, adopt.indexOf('\nfunction ', 1));
    assert.match(body, /app\.snapField = null/,
      'adoptModel no longer clears app.snapField — a rescan would inherit the previous face boundary');
    assert.match(body, /app\.snapBuffer = null/,
      'adoptModel no longer clears app.snapBuffer — it caches intrinsics the new model may change');
  });

  it('the sigma estimate is fed the detector surface, and built once per model', () => {
    // `estimateSigma` answers two questions about the DETECTOR — which vertices
    // this pose lets it see, and how far each landmark moved against its
    // neighbours — and the visibility it returns is what gates the tracker's
    // rigidity ramp. So it has to describe the same surface `track` solves
    // against, which since 2026-09-02 is `landmarkSurface(model)` and not
    // `model.positions`. `model.positions` is skin: `enroll.ts` subtracts the
    // detector bias before the model leaves. Identical while that bias is zero,
    // which is why nothing but a fingerprint can see this.
    //
    // Positively bound at the CALL SITE rather than banned by spelling — the
    // lesson from the intrinsics guard, which two rewordings could walk past
    // while a helper it never called sat there satisfying it.
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    assert.match(text, /positions:\s*geometry\b/,
      'the wear-branch estimateSigma call no longer takes `geometry`');
    assert.match(text, /const geometry = detectorGeometry\(app\)/,
      'the per-frame geometry is no longer `detectorGeometry(app)` — if it went back to '
      + '`app.model.positions`, the sigma estimate describes the SKIN surface while the tracker '
      + 'solves against the detector one, and at a nonzero detector bias the visibility gating '
      + 'the rigidity ramp is a statement about the wrong geometry');
    const fn = text.slice(text.indexOf('function detectorGeometry'));
    const fnBody = fn.slice(0, fn.indexOf('\nfunction ', 1));
    assert.match(fnBody, /landmarkSurface\(app\.model\)/,
      'detectorGeometry no longer builds the surface with landmarkSurface');
    // The memo, and its key. A boolean flag would need clearing at three
    // assignment sites; the model object cannot go stale against itself.
    assert.match(fnBody, /app\.landmarkGeometryFor !== app\.model/,
      'detectorGeometry no longer keys its cache on the model object — a per-frame '
      + '`landmarkSurface` allocates 11 KB every frame, and a flag-keyed one goes stale the '
      + 'first time somebody adds a fourth `app.model =`');
  });
});

describe('the render loop', () => {
  /**
   * `startLoop` out of the compiled build, wired to a virtual clock.
   *
   * `main.ts` calls `boot()` at module scope, so importing it under Node runs
   * the app. Reading the one function out of `dist/` keeps this a test of the
   * shipped loop rather than of a copy of it — the copy is exactly what would
   * stop tracking the real thing the first time somebody edited main.ts.
   */
  function instantiate() {
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    const start = text.indexOf('function startLoop(');
    assert.ok(start >= 0, 'startLoop has been renamed or moved out of app/main');
    let depth = 0;
    let end = start;
    for (let i = text.indexOf('{', start); i < text.length; i++) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}' && --depth === 0) { end = i + 1; break; }
    }

    let now = 0;
    let nextTimer = 1;
    const timers = new Map<number, { fn: () => void; every: number; next: number }>();
    let rafQueue: ((t: number) => void)[] = [];
    let rafAlive = true;
    const ticks: { at: number; by: 'raf' | 'timer' }[] = [];
    let firingPeriod: number | null = null;

    const listeners: (() => void)[] = [];
    const doc = {
      hidden: false,
      addEventListener(type: string, fn: () => void) {
        if (type === 'visibilitychange') listeners.push(fn);
      },
      fire() { for (const fn of [...listeners]) fn(); },
    };

    const api = {
      performance: { now: () => now },
      document: doc,
      setInterval: (fn: () => void, every: number) => {
        const id = nextTimer++;
        timers.set(id, { fn, every, next: now + every });
        return id;
      },
      clearInterval: (id: number) => { timers.delete(id); },
      requestAnimationFrame: (fn: (t: number) => void) => { rafQueue.push(fn); return 0; },
      // A tick is tagged by which clock is firing it: the 1000/60 fallback
      // interval is the only one under 30 ms, the 500 ms watchdog the only one
      // over it.
      tick: () => { ticks.push({ at: now, by: firingPeriod !== null && firingPeriod < 30 ? 'timer' : 'raf' }); },
      console: { warn: () => {}, info: () => {}, log: () => {} },
    };

    const startLoop = new Function(
      'app', 'tick', 'performance', 'document', 'setInterval', 'clearInterval',
      'requestAnimationFrame', 'console',
      `${text.slice(start, end)}; return startLoop;`,
    )(
      null, api.tick, api.performance, api.document, api.setInterval, api.clearInterval,
      api.requestAnimationFrame, api.console,
    ) as (app: { loopDriver: string }) => void;

    const advanceMs = (ms: number) => {
      const until = now + ms;
      const STEP = 1000 / 60;
      while (now < until) {
        now = Math.min(now + STEP, until);
        // Deliver at most ONE pending rAF callback per display frame, and only
        // while rAF is alive and the tab is visible.
        if (rafAlive && !doc.hidden && rafQueue.length > 0) {
          const due = rafQueue;
          rafQueue = [];
          firingPeriod = null;
          for (const fn of due) fn(now);
        }
        for (const [, t] of [...timers]) {
          while (t.next <= now) {
            t.next += t.every;
            firingPeriod = t.every;
            t.fn();
            firingPeriod = null;
          }
        }
      }
    };

    return {
      startLoop,
      advanceMs,
      doc,
      ticks,
      timerCount: () => timers.size,
      pendingRaf: () => rafQueue.length,
      setRafAlive: (v: boolean) => { rafAlive = v; },
      countBy: (by: 'raf' | 'timer') => ticks.filter((t) => t.by === by).length,
    };
  }

  it('does not fall back to a timer when the tab is merely hidden', () => {
    // Finding 11, and the highest-value entry on the whole list: the bug was
    // invisible (it presented as "this machine is too slow"), it is trivially
    // re-introducible by anyone touching the watchdog, and it fired on every
    // ordinary tab switch after about 1.2 seconds.
    //
    // A hidden tab is the one environment in which "rAF has not fired for a
    // second" is correct behaviour rather than a fault.
    const h = instantiate();
    const app = { loopDriver: 'raf' };
    h.startLoop(app);

    // Phase 1: visible, rAF alive.
    h.advanceMs(2000);
    assert.equal(app.loopDriver, 'raf');
    assert.equal(h.countBy('raf'), 120, `${h.countBy('raf')} rAF-driven ticks in two seconds`);
    assert.equal(h.timerCount(), 1, 'more than the 500 ms watchdog is running');
    assert.ok(h.pendingRaf() <= 1, 'the rAF queue has grown past one');

    // Phase 2: hidden. THIS IS THE REGRESSION — the old code latched to 'timer'
    // at 1.66 s here, and a wearer coming back to the tab found the loop on a
    // worse clock than their display with no way to know why.
    const before = h.ticks.length;
    h.doc.hidden = true;
    h.doc.fire();
    h.advanceMs(4000);
    assert.equal(
      app.loopDriver, 'raf',
      'four seconds of a HIDDEN tab were read as a broken requestAnimationFrame',
    );
    assert.equal(h.ticks.length, before, 'the loop kept ticking while the tab was hidden');
    assert.ok(h.pendingRaf() <= 1, 'the rAF queue has grown past one');

    // Phase 3: visible again, and back at the display rate immediately.
    h.doc.hidden = false;
    h.doc.fire();
    h.advanceMs(2000);
    assert.equal(app.loopDriver, 'raf');
    assert.equal(h.countBy('raf'), 240, `${h.countBy('raf')} rAF-driven ticks after returning`);
    assert.ok(h.pendingRaf() <= 1, 'the rAF queue has grown past one');

    // Phase 4: a genuinely dead rAF while the tab is VISIBLE. This is the case
    // the fallback exists for, and it still has to work.
    h.setRafAlive(false);
    h.advanceMs(3000);
    assert.equal(app.loopDriver, 'timer', 'a dead rAF on a visible tab never fell back');
    assert.ok(h.countBy('timer') > 50, `only ${h.countBy('timer')} timer-driven ticks in 3 s`);
    assert.equal(
      h.timerCount(), 1,
      'the watchdog was not cleared when the fallback interval replaced it — two clocks ' +
      'are now driving the same frame',
    );

    // Phase 5: rAF returns, and the loop takes it back.
    const timerTicks = h.countBy('timer');
    h.setRafAlive(true);
    h.advanceMs(2000);
    assert.equal(app.loopDriver, 'raf', 'rAF came back and the loop stayed on the timer');
    assert.ok(
      h.countBy('timer') <= timerTicks + 1,
      `the timer kept firing after rAF returned: ${h.countBy('timer')} against ${timerTicks}`,
    );
    assert.equal(h.timerCount(), 1, 'the fallback interval outlived the fallback');
    assert.ok(
      h.pendingRaf() <= 1,
      'a tick that schedules is a tick that can be scheduled twice — the rAF queue has ' +
      'more than one callback in it, which once cost a wearer a 10,000 fps readout and ' +
      '402 dropped frames',
    );
  });
});

describe('forgetting the wearer forgets ALL of the wearer', () => {
  /**
   * Slices `resetPerson` out of the compiled app and runs it against a stub.
   *
   * The manifest above it — `PERSON_STATE` — is a `Record<keyof App, ...>`, so
   * TypeScript already refuses to compile an `App` field nobody classified.
   * That caught the identity watch the day it was added and it is the stronger
   * of the two checks. What it CANNOT check is whether the reset actually does
   * what the manifest says: a field can be classified `'person'` and never
   * assigned, which is exactly the state `rescan` was in for seven fields.
   *
   * So this runs the real function over a stub app whose every person-owned
   * field holds a recognisable sentinel, and asserts that none of them survives.
   */
  function instantiateReset() {
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    const start = text.indexOf('function resetPerson(');
    assert.ok(start >= 0, 'resetPerson has been renamed or moved');
    const bodyAt = text.indexOf('{', text.indexOf(')', start));
    let depth = 0, end = start;
    for (let i = bodyAt; i < text.length; i++) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}' && --depth === 0) { end = i + 1; break; }
    }

    const calls: string[] = [];
    const resetPerson = new Function(
      'createProtocol', 'createUncertainty', 'intrinsicsFromFov',
      'MEDIAPIPE_ASSUMED_VERTICAL_FOV', 'detachFrame', 'forgetWearer',
      'refreshFaceControls',
      `${text.slice(start, end)}
return resetPerson;`,
    )(
      () => ({ stub: 'protocol' }),
      () => ({ stub: 'uncertainty' }),
      () => ({ stub: 'intrinsics' }),
      63,
      () => calls.push('detachFrame'),
      (w: { armed: boolean; reference: number }) => {
        calls.push('forgetWearer'); w.armed = false; w.reference = NaN;
      },
      () => calls.push('refreshFaceControls'),
    ) as (app: any, reason: string) => void;
    return { resetPerson, calls };
  }

  /**
   * Every field the manifest calls the wearer's, READ OUT OF THE MANIFEST.
   *
   * Not a list repeated here. A copy would drift the first time somebody
   * classified a new field as `'person'` and forgot this file, and the drift
   * would be silent in exactly the direction that matters — a field nobody
   * checks is a field nobody clears. This is what v1 meant by calling its
   * equivalent "machine-readable": the classification is data, and the test
   * consumes it rather than restating it.
   */
  function personFields(): string[] {
    const text = readFileSync(new URL('../src/app/main.js', import.meta.url), 'utf8');
    const at = text.indexOf('const PERSON_STATE = {');
    assert.ok(at >= 0, 'PERSON_STATE was renamed or removed — the reset is unreviewable');
    const open = text.indexOf('{', at);
    let depth = 0, end = open;
    for (let i = open; i < text.length; i++) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}' && --depth === 0) { end = i + 1; break; }
    }
    const manifest = new Function(`return ${text.slice(open, end)};`)() as Record<string, string>;
    const fields = Object.entries(manifest)
      .filter(([, cls]) => cls === 'person')
      .map(([key]) => key);
    assert.ok(fields.length >= 15,
      `the manifest calls only ${fields.length} fields the wearer's — it has been gutted`);
    return fields;
  }

  const PERSON_FIELDS = personFields();

  function stubApp() {
    const app: any = {
      mesh: { vertexCount: 468 },
      source: { width: 1280, height: 720, kind: 'camera' },
      scene: { setHeadPose: () => {} },
      ui: { frameNote: () => {}, status: () => {} },
      identity: { armed: true, reference: 7, window: [1, 2], strikes: 3, convictions: 4 },
    };
    for (const key of PERSON_FIELDS) {
      if (key === 'identity') continue;
      app[key] = key === 'scanGen' ? 41 : `SENTINEL:${key}`;
    }
    // The choices, which must SURVIVE. A change of wearer is not a change of taste.
    app.frame = 'SENTINEL:frame';
    app.wantedFrameId = 'SENTINEL:wantedFrameId';
    app.softHook = true;
    app.meshFrames = 'SENTINEL:meshFrames';
    return app;
  }

  it("clears every field the manifest calls the wearer's", () => {
    // RED: delete any single `app.<field> = ...` line from `resetPerson`. This
    // is the check that would have caught `rescan` keeping `lastCapture`,
    // `lastPose`, `uncertainty`, `intrinsics` and `knownPdMm` — five fields
    // whose survival meant the next wearer was measured with the previous
    // wearer's PD, warm-started from their pose, and scored against their
    // landmark history.
    const { resetPerson } = instantiateReset();
    const app = stubApp();
    resetPerson(app, 'identity');

    for (const key of PERSON_FIELDS) {
      if (key === 'identity') continue;
      if (key === 'scanGen') {
        assert.equal(app.scanGen, 42,
          'scanGen must ADVANCE, not clear — a solve suspended inside enroller.run '
          + 'compares it across the await to find out its scan was abandoned');
        continue;
      }
      assert.notEqual(app[key], `SENTINEL:${key}`,
        `resetPerson left the previous wearer's ${key} in place`);
    }
  });

  it("keeps the wearer's CHOICES, and the watch's lifetime counters", () => {
    // RED: add `app.frame = null` to resetPerson, or zero `convictions` in
    // forgetWearer. The second is the one that matters: a counter that resets
    // with the thing it counts cannot report the reset, and `convictions` is
    // the only way a diagnostics paste says whether this ever fired.
    const { resetPerson, calls } = instantiateReset();
    const app = stubApp();
    resetPerson(app, 'identity');

    assert.equal(app.frame, 'SENTINEL:frame', 'the chosen glasses were thrown away');
    assert.equal(app.wantedFrameId, 'SENTINEL:wantedFrameId');
    assert.equal(app.softHook, true);
    assert.equal(app.meshFrames, 'SENTINEL:meshFrames', 'the loaded assets were discarded');
    assert.equal(app.identity.convictions, 4, 'the conviction count was reset with the reset');
    assert.equal(app.identity.armed, false, 'the watch stayed armed on a face it no longer knows');
    assert.ok(calls.includes('detachFrame'),
      'the previous glasses stayed on the face being re-measured');
    assert.ok(calls.includes('forgetWearer'));
  });

  it('lands in acquire, so nothing is drawn until there is a face to draw on', () => {
    // RED: drop the phase assignment. `fitFrame` returns early without a model,
    // so the app would sit in `wear` with no model and no frame, drawing
    // nothing and offering no route back.
    const { resetPerson } = instantiateReset();
    const app = stubApp();
    resetPerson(app, 'rescan');
    assert.equal(app.phase, 'acquire');
  });
});
