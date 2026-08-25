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
    const text = serializeCapture(syntheticCapture());
    const bumped = text.replace('"v":1', '"v":2');
    assert.throws(() => parseCapture(bumped), /v2|understands/i);
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
