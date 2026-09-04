/**
 * The capture format, round-tripped and replayed.
 *
 * A fixture nobody can solve again is a file, not a measurement. These two
 * halves are what make a recorded session worth the wearer's time: it survives
 * the trip to disk exactly, and the estimator will take it back.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { parseCapture, serializeCapture, type Capture } from '../src/enroll/telemetry.js';
import { enroll } from '../src/enroll/enroll.js';
import { intrinsicsFromFov } from '../src/core/camera.js';
import { poseIdentity } from '../src/core/linalg.js';
import { loadBasis, loadTemplateMesh } from '../src/testkit/fixtures.js';
import { CAMERA_LADDER, generatePopulation, synthesizeCapture } from '../src/testkit/synthetic.js';

const mesh = loadTemplateMesh();
const basis = loadBasis();

/** A capture the shape a real session produces, built from the synthetic rig. */
function syntheticCapture(): Capture {
  const subject = generatePopulation(mesh, basis, { count: 1, seed: 23 })[0];
  const geometry = CAMERA_LADDER[0];
  const captured = synthesizeCapture(mesh, subject, geometry, { seed: 23 });
  const intrinsics = intrinsicsFromFov(geometry.width, geometry.height, geometry.fovDeg);
  return {
    header: {
      v: 1, subject: 'synthetic', date: '2026-08-25',
      width: geometry.width, height: geometry.height, intrinsics, intrinsicsSolved: false,
      knownPdMm: 63, card: false, note: 'round-trip fixture',
      frames: captured.frames.length,
    },
    frames: captured.frames.map((f) => ({
      landmarks: f.landmarks,
      sigmaPx: f.sigmaPx,
      visibility: f.visibility ?? new Float64Array(mesh.vertexCount).fill(1),
      silhouette: null,
      beat: f.beat ?? 'centre',
    })),
    wear: [],
  };
}

describe('a capture survives the trip to disk', () => {
  it('round-trips every landmark, and keeps an absent one absent', () => {
    const capture = syntheticCapture();
    // A detector says "this landmark is missing" with NaN, and NaN is not JSON.
    // If it came back as 0 the bundle would weight a landmark at the top-left
    // corner of the image as though it had been seen.
    capture.frames[0].landmarks[10] = NaN;
    capture.frames[0].sigmaPx[5] = Infinity;

    const back = parseCapture(serializeCapture(capture));
    assert.equal(back.frames.length, capture.frames.length);
    assert.ok(Number.isNaN(back.frames[0].landmarks[10]),
      'an absent landmark came back as a coordinate — the bundle would weight it');
    assert.ok(!Number.isFinite(back.frames[0].sigmaPx[5]),
      'an excluded landmark came back with a finite sigma — it would re-enter the solve');

    // Everything else to the declared precision.
    for (let f = 0; f < capture.frames.length; f++) {
      const a = capture.frames[f].landmarks;
      const b = back.frames[f].landmarks;
      assert.equal(a.length, b.length, `frame ${f} changed length`);
      for (let i = 0; i < a.length; i++) {
        if (!Number.isFinite(a[i])) continue;
        assert.ok(Math.abs(a[i] - b[i]) <= 5e-4,
          `frame ${f} landmark component ${i}: ${a[i]} -> ${b[i]}`);
      }
      assert.equal(back.frames[f].beat, capture.frames[f].beat);
    }
    assert.equal(back.header.knownPdMm, 63, 'the wearer\'s own PD did not survive');
  });

  it('refuses a truncated file rather than replaying part of a session', () => {
    // RED: drop the frame-count check. A fixture that silently lost its last
    // thirty frames replays to numbers nobody can compare against the session
    // that produced them — and it looks like a successful replay.
    const text = serializeCapture(syntheticCapture());
    const lines = text.trimEnd().split('\n');
    const cut = lines.slice(0, lines.length - 5).join('\n') + '\n';
    assert.throws(() => parseCapture(cut), /truncated|frames/i);
  });

  it('refuses a format it does not understand', () => {
    // v2 became READABLE on 2026-09-04 — it is the wear section — so this now
    // bumps past it. The property is unchanged and is the point: a reader that
    // shrugs at an unknown version silently mis-reads whatever the next format
    // moved, and the whole reason this file is versioned is that it is written
    // by a browser and read by a script that ships separately.
    const text = serializeCapture(syntheticCapture());
    const bumped = text.replace('"v":1', '"v":3');
    assert.throws(() => parseCapture(bumped), /v3|understands/i);
  });
});

describe('a capture can be solved again', () => {
  it('replays through the real estimator and lands on the same face', () => {
    // The half that makes a recording worth a wearer's time. If a fixture
    // cannot be fed back through `enroll`, it is a file rather than a
    // measurement — and the day this stops working is the day every recorded
    // session becomes unreadable, which is exactly when nobody is looking.
    const capture = syntheticCapture();
    const replayed = parseCapture(serializeCapture(capture));

    const framesOf = (c: Capture) => c.frames.map((f) => ({ ...f }));
    const direct = enroll({
      mesh, basis, frames: framesOf(capture),
      imageWidth: capture.header.width, imageHeight: capture.header.height,
    });
    const viaDisk = enroll({
      mesh, basis, frames: framesOf(replayed),
      imageWidth: replayed.header.width, imageHeight: replayed.header.height,
    });

    assert.ok(direct.model, 'the synthetic capture did not solve at all');
    assert.ok(viaDisk.model, 'the round-tripped capture did not solve');

    // Same face, to well inside the format's own rounding. Landmarks go out at
    // 3 decimals of a pixel, so a disagreement here is a real one.
    let worst = 0;
    for (let i = 0; i < direct.model.positions.length; i++) {
      worst = Math.max(worst, Math.abs(direct.model.positions[i] - viaDisk.model.positions[i]));
    }
    assert.ok(worst < 0.05,
      `the replayed scan differs from the live one by ${worst.toFixed(4)} mm at worst`);
  });
});

describe('the wear half of a capture survives the trip too', () => {
  // The scan half has been round-tripped since 2026-08-25. The wear half is
  // new on 2026-09-04, and it is what makes the tracker — the part a wearer
  // looks at every frame — replayable at all. Everything decided about the
  // filter, the motion prior and the stillness latch before this existed was
  // decided on a synthetic stimulus or on a spoken report of a session nobody
  // could run again.
  const NL = String.fromCharCode(10);

  it('carries landmarks, dt and both poses, and a refused frame stays refused', () => {
    const capture = syntheticCapture();
    const emitted = poseIdentity();
    emitted.R[0] = 0.9848; emitted.R[2] = 0.1736;
    emitted.R[6] = -0.1736; emitted.R[8] = 0.9848;
    emitted.t.set([1.25, -3.5, 412.75]);
    const raw = poseIdentity();
    raw.t.set([1.5, -3.25, 413]);

    capture.wear = [
      { landmarks: Float64Array.from([10.5, 20.25, NaN, 40.125]), dt: 1 / 30, emitted, raw },
      // A frame the tracker REFUSED. Both poses null, and they have to come
      // back null: a refused frame that read as a solved one would let a
      // replay score the tracker on frames it declined to answer.
      { landmarks: Float64Array.from([1, 2, 3, 4]), dt: 0.05, emitted: null, raw: null },
    ];

    const back = parseCapture(serializeCapture(capture));
    assert.equal(back.header.v, 2, 'a capture carrying wear frames is not stamped v2');
    assert.equal(back.header.wear, 2, 'the header does not declare its wear frames');
    assert.equal(back.wear.length, 2);

    assert.ok(Number.isNaN(back.wear[0].landmarks[2]),
      'an absent wear landmark came back as a coordinate');
    assert.ok(Math.abs(back.wear[0].dt - 1 / 30) < 1e-6, 'dt did not survive the round trip');
    assert.ok(back.wear[0].emitted && back.wear[0].raw, 'the poses did not survive');
    for (let i = 0; i < 9; i++) {
      assert.ok(Math.abs(back.wear[0].emitted!.R[i] - emitted.R[i]) < 1e-6,
        `rotation element ${i} moved in the round trip`);
    }
    for (let i = 0; i < 3; i++) {
      assert.ok(Math.abs(back.wear[0].emitted!.t[i] - emitted.t[i]) < 1e-4,
        `translation element ${i} moved in the round trip`);
    }
    assert.equal(back.wear[1].emitted, null, 'a refused frame came back with a pose');
    assert.equal(back.wear[1].raw, null, 'a refused frame came back with a raw pose');

    // The scan half is untouched by any of this: a v2 file is still a whole
    // scan, and a reader that wants only the scan never has to know.
    assert.equal(back.frames.length, capture.frames.length);
  });

  it('reads a v1 file, which has no wear section at all', () => {
    const back = parseCapture(serializeCapture(syntheticCapture()));
    assert.equal(back.header.v, 1, 'a wear-free capture should still stamp v1');
    assert.deepEqual(back.wear, [], 'a v1 file must read as an empty wear section, not undefined');
  });

  it('refuses a wear section the header disagrees with', () => {
    const capture = syntheticCapture();
    capture.wear = [
      { landmarks: Float64Array.from([1, 2]), dt: 0.03, emitted: null, raw: null },
      { landmarks: Float64Array.from([3, 4]), dt: 0.03, emitted: null, raw: null },
    ];
    const lines = serializeCapture(capture).trimEnd().split(NL);
    assert.throws(
      () => parseCapture(lines.slice(0, lines.length - 1).join(NL)),
      /wear frames/,
      'a truncated wear section replayed as though it were the whole session',
    );
  });
});
