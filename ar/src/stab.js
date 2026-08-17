/**
 * The live stillness meter — `__ar.stab` (anchoring-v3 R0 instrument).
 *
 * One number decides every gate in the v3 plan's live protocol: how much the
 * drawn frame moves ON SCREEN while the head is holding still. Until now that
 * number existed only in the offline replays; live sessions read it by
 * impression, which is exactly the kind of testimony the process forbids. This
 * meter computes it from state the pipeline already has in memory — the placed
 * frame's origin, the smoothed head pose it is composed with, and the pose
 * smoother's own measured speeds — and exposes it as a readout that a wearer
 * (or whoever is running the capture script) can read out loud mid-session.
 *
 * The definition, and why each part is what it is:
 *
 *  - **The composed glasses origin, in image pixels.** `placement.position`
 *    through the head's world matrix through the projection — the identical
 *    arithmetic the diag replay's `screen.rmsPx` pins, so live and offline
 *    numbers are the same quantity and can be compared directly.
 *  - **Gated on pose stillness**, because screen motion while the head moves is
 *    tracking, not jitter — the meter must never average a deliberate turn into
 *    a stillness verdict. The gate is the project's own stillness definition
 *    (memory: stillness = RMS while pose speed < 2 cm/s and 5°/s), read off the
 *    pose smoother's measured (post-dCutoff) speed and rate — the same signals
 *    the pin's activity arm trusts.
 *  - **Trailing 5 s**, long enough to hold ~2.5 periods of the measured 0.5 Hz
 *    gaze coupling (so an oscillation cannot hide between reads) and short
 *    enough that the readout answers "is it still NOW", not "was the last
 *    minute calm on average".
 *  - **RMS about the window mean + the single worst step.** The pair the whole
 *    rework reports everywhere: an sd can hide one pop, a max step can hide a
 *    slow breathe; together they cannot hide much.
 *
 * Steps are counted only between CONSECUTIVE still frames — a gap through a
 * movement must not bill the movement's displacement to the stillness meter.
 *
 * Pure arithmetic, no DOM, no allocation after construction: the harness
 * drives it directly, and `main.js` (and the telemetry recorder) polls
 * `readout` without waking the GC.
 */

/**
 * The stillness gate: 2 cm/s of translation, 5°/s of turn. The project's own
 * stillness definition (stage-6 protocol; also the telemetry replay's segment
 * gate), sized between detector noise on a held head (the G10 floors run
 * 1–5 cm/s of rectified noise BEFORE the dCutoff pole; the smoothed signal
 * idles well under 1) and the slowest deliberate motion anyone produces on
 * purpose (~5 cm/s).
 */
export const STILL_SPEED_CMS = 2;
export const STILL_RATE_RADS = 5 * (Math.PI / 180);

/** Trailing window, seconds — see the header. */
export const STAB_WINDOW_S = 5;

/** Ring capacity: 5 s at 60 fps with headroom. Power of two for the mask. */
const CAPACITY = 512;

/**
 * Fewer still samples than this and the meter reports null rather than a
 * number: an RMS over a handful of frames is noise about noise. Half a second
 * at the 15 fps the live sessions actually run.
 */
const MIN_SAMPLES = 8;

import * as THREE from 'three';

const projScratch = new THREE.Vector3();
const projOut = { x: NaN, y: NaN };

/**
 * The composed glasses origin in image pixels — placement.position through the
 * head's world matrix through the camera, into the source's pixel rectangle.
 * The identical mapping `diag-replay.js` pins as `screen.*`, stated once here
 * so the live meter, the recorder and the replays cannot drift apart. Returns
 * a reused object; copy out to keep.
 */
export function screenPointOf(position, headMatrixWorld, camera, width, height) {
  projScratch.copy(position).applyMatrix4(headMatrixWorld).project(camera);
  projOut.x = (projScratch.x * 0.5 + 0.5) * width;
  projOut.y = (1 - (projScratch.y * 0.5 + 0.5)) * height;
  return projOut;
}

export function createStabMeter() {
  const t = new Float64Array(CAPACITY);
  const x = new Float64Array(CAPACITY);
  const y = new Float64Array(CAPACITY);
  const still = new Uint8Array(CAPACITY);
  let head = 0; // next write slot
  let count = 0; // total writes, capped at CAPACITY

  /** One reused readout object — `__ar.stab` IS this. */
  const readout = {
    rmsPx: null,
    maxStepPx: null,
    /** How many still samples the numbers stand on. */
    n: 0,
    /** Fraction of the trailing window's frames that passed the gate. */
    stillFrac: 0,
    /** Echoed so a consumer knows what "still" meant. */
    windowS: STAB_WINDOW_S,
    gateSpeedCms: STILL_SPEED_CMS,
    gateRateDps: 5,
  };

  return {
    readout,

    /**
     * Folds in one frame and refreshes the readout.
     *
     * `screenX/screenY`: the composed glasses origin in image pixels — the
     * caller projects, because only the caller knows its camera and buffer
     * (frame.js's own projection scratch does it in one place for the app).
     * `speedCms/rateRads`: the pose smoother's measured translation speed and
     * turn rate. `tS`: this frame's time in seconds on any monotonic clock.
     */
    update(screenX, screenY, speedCms, rateRads, tS) {
      const at = head;
      t[at] = tS;
      x[at] = screenX;
      y[at] = screenY;
      still[at] = (Number.isFinite(screenX) && Number.isFinite(screenY)
        && speedCms < STILL_SPEED_CMS && rateRads < STILL_RATE_RADS) ? 1 : 0;
      head = (head + 1) % CAPACITY;
      if (count < CAPACITY) count++;

      // One pass over the trailing window, oldest → newest: mean first needs a
      // second pass, so accumulate Σx, Σx² instead — RMS about the mean is
      // √(E[x²] − E[x]² + E[y²] − E[y]²), numerically fine at image-pixel
      // magnitudes with float64.
      const horizon = tS - STAB_WINDOW_S;
      let n = 0;
      let inWindow = 0;
      let sx = 0; let sy = 0; let sxx = 0; let syy = 0;
      let maxStep = null;
      let prevStill = false;
      let prevX = 0; let prevY = 0;
      const start = count < CAPACITY ? 0 : head;
      for (let k = 0; k < count; k++) {
        const i = (start + k) % CAPACITY;
        if (t[i] < horizon) { prevStill = false; continue; }
        inWindow++;
        if (!still[i]) { prevStill = false; continue; }
        n++;
        sx += x[i]; sy += y[i];
        sxx += x[i] * x[i]; syy += y[i] * y[i];
        if (prevStill) {
          const step = Math.hypot(x[i] - prevX, y[i] - prevY);
          if (maxStep === null || step > maxStep) maxStep = step;
        }
        prevStill = true;
        prevX = x[i]; prevY = y[i];
      }

      readout.stillFrac = inWindow > 0 ? n / inWindow : 0;
      readout.n = n;
      if (n >= MIN_SAMPLES) {
        const varSum = Math.max(sxx / n - (sx / n) ** 2, 0)
          + Math.max(syy / n - (sy / n) ** 2, 0);
        readout.rmsPx = Math.sqrt(varSum);
        readout.maxStepPx = maxStep;
      } else {
        readout.rmsPx = null;
        readout.maxStepPx = null;
      }
      return readout;
    },

    reset() {
      head = 0;
      count = 0;
      readout.rmsPx = null;
      readout.maxStepPx = null;
      readout.n = 0;
      readout.stillFrac = 0;
    },
  };
}
