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
