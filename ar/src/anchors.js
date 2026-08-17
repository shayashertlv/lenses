/**
 * Measuring the face in front of the camera, rather than assuming the average one.
 *
 * The head node poses MediaPipe's *canonical* head — an average of everybody. That
 * is what makes the tracking cheap, and it is also what makes a naive fit wrong:
 * anchoring the pads to the canonical nose bridge puts the glasses where an average
 * person's bridge is, not where this person's is. Someone with a low bridge gets
 * glasses floating above their nose; someone with a high one gets them buried.
 *
 * The individual detail is in the 478 landmarks, but those are in image space, so
 * they cannot be used for placement directly. This module moves them into face
 * space, where the placement is solved:
 *
 *   1. For landmark i, take canonical vertex i, push it through the head pose, and
 *      read off its depth in camera space.
 *   2. Cast a ray through the *observed* landmark and walk it to that same depth.
 *      The result lands exactly on the observed feature, at approximately the right
 *      distance.
 *   3. Map it back through the inverse head pose into face space.
 *
 * What that buys, honestly: across-face (x) and up-the-face (y) become this
 * person's real geometry. Depth (z) stays the canonical head's, because step 1
 * borrows it — a single camera cannot recover it (see `measureFaceWidthRatio`).
 *
 * That split is the right one for eyewear. Where a frame sits *up and down* the
 * nose, and how wide it is, are the two things people notice; those are exactly x
 * and y. Vertex distance — how far the lenses stand off the eyes — is z, and it is
 * not measured here at all. `nose.js` recovers it by another route: the width of
 * *this* nose is an x measurement, which this recovery is good at, and how far
 * forward the skin sits under a pad follows from it.
 */

import * as THREE from 'three';
import { LM, noseSpan, IRIS_DIAMETER_CM, PD_RANGE_CM } from './canonical-face.js';

const canonicalPoint = new THREE.Vector3();
const cameraSpace = new THREE.Vector3();
const rayPoint = new THREE.Vector3();
const inverseHead = new THREE.Matrix4();

/**
 * The average face, as an anchor set. Used when adaptation is switched off, so the
 * two can be compared directly.
 */
export function canonicalAnchors(face) {
  const bridge = new THREE.Vector3(...face.point(LM.NOSE_BRIDGE));
  const nasion = new THREE.Vector3(...face.point(LM.NASION));
  const eyeR = face.point(LM.EYE_OUTER_R);
  const eyeL = face.point(LM.EYE_OUTER_L);

  return {
    measured: false,
    bridge,
    bridgeUp: bridgeDirection(bridge, nasion),
    eyeLineY: (eyeR[1] + eyeL[1]) / 2,
    eyeCentreX: (eyeR[0] + eyeL[0]) / 2,
    templeWidth: face.templeWidth,
    widthRatio: 1,
    // The average head, so by definition its own size. PD is left null rather than
    // invented: the canonical mesh has no pupil vertex, and a proxied one is exactly
    // the biased measurement `innerEyeSpan` exists to avoid.
    metricScale: 1,
    pdCm: null,
    noseWidth: face.noseWidth,
    noseWidthRatio: 1,
    ears: estimateEars(face, 1),
  };
}

/**
 * Which way is "up the nose", as a unit vector.
 *
 * Not straight up. The bridge slopes back towards the brow — about 0.82 up to 0.57
 * back on the average face — so a frame pushed vertically up its own axis leaves the
 * nose behind and hangs in front of it. Sliding along this direction instead keeps
 * the pads in contact, which is the whole point of a nose pad.
 */
function bridgeDirection(bridge, nasion) {
  const up = nasion.clone().sub(bridge);
  // Degenerate landmarks: fall back to straight up rather than a zero vector.
  return up.lengthSq() < 1e-8 ? new THREE.Vector3(0, 1, 0) : up.normalize();
}

/**
 * Where the temple arms should come to rest, per side.
 *
 * MediaPipe's mesh stops at the face silhouette — it has no ears at all, and its
 * rearmost point is only 2.4cm behind the cheek. A real ear sits well behind that,
 * so the rest point has to be extrapolated rather than read off a landmark.
 *
 * Built from the mesh's own geometry so it scales with the face: out at the head's
 * half-width, at the height of the upper silhouette, and behind the cheek by an
 * anthropometric offset. Approximate by construction — but the arms currently run
 * *inside the skull*, so approximately right is a large improvement on that.
 */
// How far behind the cheek landmark the ear attaches, in cm on an average head.
const BEHIND_CHEEK = 1.7;
// The arms rest just below the apex of the ear, not on top of it.
const BELOW_EAR_TOP = 0.5;

function estimateEars(face, widthRatio) {
  const templeR = face.point(LM.TEMPLE_R);
  const earTopR = face.point(LM.EAR_TOP_R);
  const cheekR = face.point(LM.CHEEK_R);

  const halfWidth = Math.abs(templeR[0]) * widthRatio;
  const y = earTopR[1] - BELOW_EAR_TOP;
  const z = cheekR[2] - BEHIND_CHEEK;

  return {
    right: new THREE.Vector3(-halfWidth, y, z),
    left: new THREE.Vector3(halfWidth, y, z),
  };
}

/**
 * Ear rest points measured from *this* face's ear-top and cheek landmarks.
 *
 * The canonical estimate above puts every ear at the average head's height, and ears
 * are one of the most variable features on a face — an arm aimed at the average ear
 * visibly crosses a real one mid-pinna. The ear-top landmarks (162/389) are tracked
 * like any other, so height and depth can be per-side measurements; only x stays
 * derived from the temple width, because the pinna itself sits outside the mesh
 * silhouette and has no landmark of its own.
 */
function measuredEars(face, widthRatio, { earTopR, earTopL, cheekR, cheekL }) {
  const halfWidth = Math.abs(face.point(LM.TEMPLE_R)[0]) * widthRatio;
  return {
    right: new THREE.Vector3(-halfWidth, earTopR.y - BELOW_EAR_TOP, cheekR.z - BEHIND_CHEEK),
    left: new THREE.Vector3(halfWidth, earTopL.y - BELOW_EAR_TOP, cheekL.z - BEHIND_CHEEK),
  };
}

/**
 * How much bigger this head actually is than the canonical one — in real units.
 *
 * Everything else in this module is a *ratio* against the average head, and that is
 * the honest limit of a single camera: MediaPipe's transform is rigid, its scale is
 * always 1, and it explains a large face by moving the average one nearer rather
 * than by resizing it. So `templeWidth` is really "the canonical head's 15.49 cm,
 * reshaped" — correct in proportion, anchored to the wrong absolute size. On a face
 * proportioned like the average but genuinely 12% smaller, every centimetre this
 * pipeline reports is 12% too large, and the *True size* verdict inherits all of it.
 *
 * The iris closes that gap, and it is the only feature on a face that can. Its
 * diameter is ~11.7 mm on essentially every adult, so a span measured against it
 * **in the same image** comes out in centimetres — no camera model, no distance, no
 * field of view. The measurement is a pure image-space ratio, so it survives the
 * one thing we cannot check: MediaPipe assumes a 63° vertical field of view
 * whatever the real webcam has, and every absolute number that flows through that
 * assumption is biased by it.
 *
 * **Everything is measured at the iris plane, and only there.** That constraint is
 * what makes the result trustworthy, and it was arrived at by getting it wrong three
 * times:
 *
 *  - Proxying pupils from the eye corners to get a canonical reference put every face
 *    at 0.94x — the palpebral fissure is not symmetric about the pupil.
 *  - Using the mesh's own inner-eye span put them at 0.78x: it measures 37.1 mm there
 *    against 29.0 mm on real faces whose pupil distance is simultaneously plausible.
 *    The canonical mesh's job is to be self-consistent with the landmark detector,
 *    not to be anthropometrically exact vertex by vertex.
 *  - Measuring the temple span against the iris needed a perspective correction, the
 *    temples being centimetres further from the lens — and that correction is built
 *    on the canonical head's *depth*, the one dimension a single camera genuinely
 *    cannot recover. It came out at 1.38 and put heads at 18 cm across.
 *
 * Pupil separation has none of those problems. Both irises lie in the same plane as
 * the ruler measuring them, so there is no perspective term to get wrong; both are
 * read from the same landmarks on both sides of the ratio, so no anatomical
 * correspondence has to be assumed; and PD is a quantity with a well-known human
 * range, so the answer can be sanity-checked against the world rather than against
 * this codebase.
 *
 * This function returns only what can be had from the image: PD in real centimetres,
 * and the iris size it was measured against. Turning that into a scale factor needs
 * the same span in face space, which is `measureAnchors`' business, not this one's.
 *
 * Returns `null` when it cannot be measured, which the caller must treat as "keep
 * the previous estimate" and never as 1.0.
 */
/**
 * How much yaw the PD foreshortening correction will believe, in radians.
 *
 * The outer edge of the pose-trust yaw ramp in `frame.js` (POSE_TRUST.yaw[1]) —
 * past it the weight is zero and no sample enters the window anyway, so a larger
 * correction could only ever inflate a reading nothing consumes. Together with
 * the clamp on `eyeDepthShare` this floors the divisor at ~0.85, so the
 * correction can never grow a PD by more than ~18% whatever a wild euler says.
 */
const PD_YAW_LIMIT = 0.45;

/** How far in front of the head origin the eye chord may claim to sit, as z/d. */
const PD_EYE_SHARE_LIMIT = 0.3;

export function measureMetricScale({
  landmarks, width, height, trueYaw = 0, eyeDepthShare = 0,
}) {
  // The iris points are the detector's refinement to 478. A build without them
  // simply has no ruler, and says so rather than indexing past the end.
  if (!landmarks || landmarks.length <= LM.IRIS_L_CONTOUR[3]) return null;
  if (!(width > 0) || !(height > 0)) return null;

  // Into pixels before measuring anything. Normalised x and y are each 0..1 over a
  // rectangle that is not square, so a distance taken in normalised units is
  // stretched along whichever axis it happens to lie — which would make the iris
  // diameter depend on where in the eye socket the detector put the contour.
  const px = (index) => [landmarks[index].x * width, landmarks[index].y * height];

  // The mean distance from the iris centre to its contour is the radius, and taking
  // all four points rather than one nominated pair means the answer does not depend
  // on MediaPipe's contour ordering — which is undocumented and has no reason to
  // stay fixed. Near head-on the projected iris is a circle to better than a
  // percent, so the mean is unbiased; the caller gates on yaw for the rest.
  const radius = (centreIndex, contour) => {
    const [cx, cy] = px(centreIndex);
    let sum = 0;
    for (const index of contour) {
      const [x, y] = px(index);
      sum += Math.hypot(x - cx, y - cy);
    }
    return sum / contour.length;
  };

  // Sum of the two mean radii == mean of the two diameters.
  const irisPx = radius(LM.IRIS_R_CENTRE, LM.IRIS_R_CONTOUR)
    + radius(LM.IRIS_L_CENTRE, LM.IRIS_L_CONTOUR);
  // A face far enough away that the iris is a couple of pixels across carries more
  // quantisation than signal. Refuse rather than report noise as a measurement.
  if (irisPx < 6) return null;

  // Pupil distance, for reporting: iris centre to iris centre, against the same
  // ruler. This is the number an optician writes down.
  //
  // Divided by cos(yaw), because the ruler and the span foreshorten differently.
  // The pupil separation is a *horizontal* chord of the head, so its image
  // shrinks with the cosine of the turn; the iris the ruler is made of is a
  // circle, and a circle's mean projected radius barely moves at these angles.
  // The old note here — "the caller gates on yaw for the rest" — described the
  // binary gate this pipeline no longer has: under continuous pose-trust (C4)
  // samples are admitted out to ~24° of yaw, where an uncorrected PD reads ~9%
  // low. And unlike noise, the bias is one-signed — every yawed sample errs
  // short — so the weighted median dilutes it but can never cancel it. The pose
  // euler is measured per frame anyway; dividing the span by its own
  // foreshortening makes a yawed reading agree with a frontal one instead of
  // merely being outvoted by it.
  //
  // Not bare cos(yaw), and the extra term was measured before it was believed:
  // the pose rotates the head about its own ORIGIN, and the eye chord rides
  // ~5 cm in front of it, so under yaw the two chord ends also swing toward and
  // away from the camera asymmetrically. For chord ends at face space
  // (±s, ·, z), posed by a yaw θ about the origin at distance d, the exact
  // projected-span ratio against frontal is
  //
  //     R = (cosθ − z/d)·(1 − z/d) / (1 − (z/d)·cosθ)²
  //
  // (the s² term enters at (s·sinθ/d)², under 0.1% here and dropped). At z = 0
  // this IS cosθ — a chord pivoting about its own centre — and at the eye
  // plane's real offset it sits ~2% below it at 24°, which is exactly the
  // residual the cos-only form measured on the harness's synthetic truth
  // before this term went in. `z/d` arrives as `eyeDepthShare` from the caller
  // that knows the geometry; zero keeps the pure-cos law. Pitch needs no term:
  // it moves both chord ends to the SAME depth, which the iris ruler shares and
  // the ratio cancels; roll is in-plane and touches nothing. Both inputs are
  // clamped — `PD_YAW_LIMIT`, `PD_EYE_SHARE_LIMIT` — flooring the divisor at
  // ~0.85.
  const [rx, ry] = px(LM.IRIS_R_CENTRE);
  const [lx, ly] = px(LM.IRIS_L_CENTRE);
  const cy = Math.cos(Math.min(Math.abs(trueYaw), PD_YAW_LIMIT));
  const share = Math.min(Math.max(eyeDepthShare, 0), PD_EYE_SHARE_LIMIT);
  const foreshorten = ((cy - share) * (1 - share)) / ((1 - share * cy) ** 2);
  const pdCm = (Math.hypot(lx - rx, ly - ry) / irisPx) * IRIS_DIAMETER_CM / foreshorten;

  // The plausibility gate rides on PD rather than on the span below, because PD is
  // the quantity with a known human range. A reading outside it means the iris
  // landmarks are wrong — a half-closed eye, a highlight — and the span measured on
  // the same frame is no more trustworthy than the PD is.
  if (!(pdCm >= PD_RANGE_CM[0] && pdCm <= PD_RANGE_CM[1])) return null;

  // `irisPx` travels with the answer. This is a chain of ratios and a constant, and
  // when it comes out wrong — which it did three times, from three different bad
  // reference spans — the only way to tell *which* link is wrong is to see them.
  return { pdCm, irisPx };
}

/**
 * How far the fitted depth may move a landmark from its borrowed depth, in cm —
 * the one documented default for `blendFittedDepth` below.
 *
 * The same number as the occluder's `DEPTH_LIMIT`, for the same reason: depth is
 * the one axis a single camera cannot really see, so the fit's answer is bounded
 * against the borrowed canonical depth however confident it claims to be. There
 * used to be two defaults — 0.8 here, 1.6 in `measureAnchors` — which was not a
 * decision anyone made: the blend was copy-pasted and the copies drifted. No
 * production caller ever reached the 0.8 (the occluder passes its own limit
 * explicitly), so one default now states the law once.
 */
export const DEPTH_BLEND_LIMIT = 1.6;

/**
 * The fitted-depth blend — the ONE law every consumer of the depth fit applies.
 *
 * `borrowed + (solved − borrowed) · fitWeight`, clamped against the borrowed
 * depth. The fit is over 468 points and is stable, but its input is the noisiest
 * thing the detector reports, and the clamp is what keeps one bad frame from
 * turning a head inside out. A non-finite solve (missing landmark z, degenerate
 * fit) falls back to the borrowed depth — no reading is not a reading of zero.
 *
 * `stats.clamped` counts landings on the bound when a caller passes a counter:
 * a fit that describes this head leaves it near zero, and one that does not pins
 * vertex after vertex against it — the instrument that exposed the wrong-axis
 * fit (see `DEPTH_LIMIT` in `occluder.js`).
 */
function blendFittedDepth(borrowed, z, fitted, fitWeight, depthLimit, stats = null) {
  const solved = fitted.a * z + fitted.b;
  if (!Number.isFinite(solved)) return borrowed;
  const guess = borrowed + (solved - borrowed) * fitWeight;
  const depth = Math.min(Math.max(guess, borrowed - depthLimit), borrowed + depthLimit);
  if (stats !== null && depth !== guess) stats.clamped++;
  return depth;
}

/**
 * Carries *every* canonical vertex onto the observed face, in face space.
 *
 * The same recovery `measureAnchors` runs on a dozen landmarks, run on all 468 — and
 * that is the whole of what makes the occluder this person's head rather than the
 * average one. It was always affordable: 468 unprojects is well under a tenth of a
 * millisecond, and the reason it was not being done was that nothing asked for it.
 *
 * What comes back is honest about its axes, the same way the anchors are: x and y are
 * this face's real geometry, z is the canonical head's borrowed depth. `occluder.js`
 * corrects z separately where it can (see `fitLandmarkDepth`), because the depth of a
 * nose is exactly what a borrowed depth cannot supply.
 *
 * `headMatrixWorld` is passed rather than the node, because the caller needs to hand
 * over the *raw* pose the landmarks were solved with — by the time the occluder is
 * updated, the node itself is carrying the smoothed one.
 */
export function carryLandmarks({
  face, camera, headMatrixWorld, landmarks, out, depthFit = null,
  depthLimit = DEPTH_BLEND_LIMIT,
  /**
   * The person model (stage 4), for the zConf depth crossfade: a vertex whose
   * accumulated parallax has earned prior-free depth trust walks its ray to
   * the person surface's own camera depth instead of the borrowed/fitted one
   * — `person.depthFor` applies the law, including the G8 gate that ships it
   * dark. Null (or an empty model) leaves every depth exactly as before.
   */
  person = null,
}) {
  inverseHead.copy(headMatrixWorld).invert();
  const headElements = headMatrixWorld.elements;
  const count = Math.min(face.vertexCount, landmarks.length);
  const fitted = depthFit?.used === true ? depthFit : null;
  // How much of the fitted depth to believe, 0..1 — smooth in the fit's own r2, so
  // frames dithering around the old 0.90 threshold move the recovered depth by
  // fractions of a millimetre instead of swapping the whole nose between its fitted
  // protrusion and the borrowed average one. A fit without a weight is trusted whole,
  // which keeps older callers exact.
  const fitWeight = fitted ? Math.min(Math.max(fitted.weight ?? 1, 0), 1) : 0;
  const stats = { clamped: 0 };

  for (let i = 0; i < count; i++) {
    canonicalPoint.fromArray(face.positions, i * 3).applyMatrix4(headMatrixWorld);
    const borrowed = canonicalPoint.z;

    const landmark = landmarks[i];

    // How far down the ray to walk, and this one line is the whole of what a single
    // camera can and cannot see.
    //
    // Borrowed, the depth is the *average* head's at this landmark — which is why the
    // note above says z stays canonical. Fitted, it is this head's own, recovered from
    // MediaPipe's relative landmark depth through an affine solved across all 468
    // points (see `fitLandmarkDepth`).
    //
    // It has to be a *camera* depth and not a face-space one, and getting that wrong
    // is silent rather than obvious: the two coincide exactly head-on and diverge with
    // yaw, so the mistake reads as a fit that works on a test rig and degrades on real
    // faces. Measured on a real capture at only 10.6 degrees of turn, MediaPipe's z
    // explains 97.1% of the variance in camera depth and 63.5% of the variance in
    // face-space z. It is a depth along the view axis. Walk the ray to it, and the
    // inverse pose below distributes it back across face-space x, y and z correctly at
    // any angle — which is the same reason this function borrows a camera depth rather
    // than copying a face-space one.
    let depth = fitted
      ? blendFittedDepth(borrowed, landmark.z, fitted, fitWeight, depthLimit, stats)
      : borrowed;

    // The crossfade sits ON TOP of the unified blend, per vertex: at zero
    // trust it is the identity, at full trust the fitted path is retired for
    // this vertex entirely — depth from the person's own converged surface,
    // prior-free. Clamped against the borrowed depth by the same law as the
    // fit, because depth is still the axis one bad estimate hurts most.
    if (person) {
      const faded = person.depthFor(i, depth, headElements);
      if (faded !== depth) {
        depth = Math.min(Math.max(faded, borrowed - depthLimit), borrowed + depthLimit);
      }
    }

    rayPoint.set(landmark.x * 2 - 1, -(landmark.y * 2 - 1), 0.5).unproject(camera);

    const t = depth / rayPoint.z;
    cameraSpace.copy(rayPoint).multiplyScalar(t).applyMatrix4(inverseHead);
    out[i * 3] = cameraSpace.x;
    out[i * 3 + 1] = cameraSpace.y;
    out[i * 3 + 2] = cameraSpace.z;
  }

  // `depthClamped` is the diagnostic that matters: a fit that describes this head
  // leaves it near zero, and one that does not pins vertex after vertex against the
  // bound. See `DEPTH_LIMIT`.
  return { count, depthClamped: stats.clamped };
}

/**
 * Measures this face's anchors, in face space.
 *
 * `landmarks` are MediaPipe's normalised image-space points; `head` must already
 * have its world matrix up to date for this frame.
 */
export function measureAnchors({
  face, camera, head, landmarks, width, height, depthFit = null,
  depthLimit = DEPTH_BLEND_LIMIT,
  /** True yaw off the pose euler, radians — the PD's foreshortening correction. */
  trueYaw = 0,
  /** The person model's depth crossfade — same law as `carryLandmarks`. */
  person = null,
}) {
  inverseHead.copy(head.matrixWorld).invert();
  const headElements = head.matrixWorld.elements;

  // The same depth correction the occluder's shape recovery earns, applied to the
  // anchors — and its absence here was a real artefact, not an economy. A borrowed
  // depth is the AVERAGE head's; the difference from this face is a constant that
  // hides in pure z while the head is square-on (the seat absorbs it) and rotates
  // into the image the moment the head tilts — the recovered bridge slides, and the
  // frame pinned to it pushes forward off the nose, taking the occluder (translated
  // onto the same bridge) with it so nothing downstream can tell. Walking the ray to
  // the fitted depth instead lands the pin correctly at any pose, for exactly the
  // reason documented on `carryLandmarks`. Blended by the fit's own weight, clamped
  // against the borrowed depth like every other use of it.
  const fitted = depthFit?.used === true ? depthFit : null;
  const fitWeight = fitted ? Math.min(Math.max(fitted.weight ?? 1, 0), 1) : 0;

  /**
   * Landmark `index`, carried into face space at the depth of vertex `depthFrom` —
   * which defaults to the matching vertex, and is only ever passed separately for
   * the iris.
   *
   * The iris landmarks are the detector's refinement to 478 and have no counterpart
   * on the 468-vertex mesh, so there is no "canonical vertex 468" to borrow a depth
   * from. They sit in the eye aperture, within a couple of millimetres of the inner
   * corner, so that is the depth they borrow. Any error in it is shared by both
   * irises and cancels in the separation between them, which is the only thing they
   * are used for.
   */
  const at = (index, depthFrom = index) => {
    // Where the canonical version of this feature sits, in camera space. We want
    // its depth and nothing else.
    canonicalPoint.fromArray(face.positions, depthFrom * 3).applyMatrix4(head.matrixWorld);
    const borrowed = canonicalPoint.z;

    // The ray through the observed landmark. The camera sits at the origin looking
    // down -Z, so an unprojected point is already the ray direction.
    const landmark = landmarks[index];

    // The same law as `carryLandmarks`, from the same helper — the two used to
    // carry drifted copies of this arithmetic, and a blend that disagrees between
    // the anchors and the occluder is two bridges in flight.
    let depth = fitted
      ? blendFittedDepth(borrowed, landmark.z, fitted, fitWeight, depthLimit)
      : borrowed;

    // And the same crossfade, keyed by the vertex whose depth is being
    // borrowed (`depthFrom` — the iris rays ride the inner corner's estimate,
    // exactly as they ride its borrowed depth). The bridge pin is the first
    // beneficiary: once vertex 6 has parallax, its depth stops depending on
    // the depth fit entirely — the r2-band exposure at the one point
    // everything hangs from, gone.
    if (person) {
      const faded = person.depthFor(depthFrom, depth, headElements);
      if (faded !== depth) {
        depth = Math.min(Math.max(faded, borrowed - depthLimit), borrowed + depthLimit);
      }
    }

    rayPoint.set(landmark.x * 2 - 1, -(landmark.y * 2 - 1), 0.5).unproject(camera);

    // Walk it out to that depth, then back into face space.
    const t = depth / rayPoint.z;
    return cameraSpace.copy(rayPoint).multiplyScalar(t).applyMatrix4(inverseHead).clone();
  };

  const bridge = at(LM.NOSE_BRIDGE);
  const nasion = at(LM.NASION);
  const eyeR = at(LM.EYE_OUTER_R);
  const eyeL = at(LM.EYE_OUTER_L);
  const templeR = at(LM.TEMPLE_R);
  const templeL = at(LM.TEMPLE_L);
  const earTopR = at(LM.EAR_TOP_R);
  const earTopL = at(LM.EAR_TOP_L);
  const cheekR = at(LM.CHEEK_R);
  const cheekL = at(LM.CHEEK_L);

  const templeWidth = templeR.distanceTo(templeL);
  const widthRatio = templeWidth / face.templeWidth;

  // How wide this nose is across the strip a pad bears on. Nose width varies
  // independently of face width — a broad face can carry a narrow nose — and it is
  // what decides how far forward the skin sits under the pads, so it is measured
  // rather than scaled off the temples. It is also a pure x measurement, which is
  // the axis this recovery is good at.
  const noseWidth = noseSpan(
    at(LM.NOSE_WALL_HIGH_R).x, at(LM.NOSE_WALL_HIGH_L).x,
    at(LM.NOSE_WALL_LOW_R).x, at(LM.NOSE_WALL_LOW_L).x,
  );

  // The absolute ruler. Null when it could not be read — a wink, an iris on the
  // eyelid, a face too far off to resolve — and the caller carries the previous
  // value forward rather than falling back to "average", which would be a silent
  // error dressed as a measurement.
  //
  // `metricScale` is formed *here*, by dividing the real temple width by the
  // face-space one measured a few lines above. That division is what cancels the
  // canonical head's absolute size out of the answer: both sides describe the same
  // span on the same face in the same frame, one in centimetres and one in whatever
  // units face space turns out to be in.
  // The chord's pivot-offset share for the PD correction: the iris plane's
  // canonical depth (the same inner-corner convention `at` borrows for the iris
  // rays) over the pose's own distance. Zero when the pose is degenerate, which
  // degrades the correction to the pure-cos law rather than inventing geometry.
  const headDistance = Math.abs(head.matrixWorld.elements[14]);
  const eyeDepthShare = headDistance > 1
    ? ((face.point(LM.EYE_INNER_R)[2] + face.point(LM.EYE_INNER_L)[2]) / 2) / headDistance
    : 0;
  const metric = measureMetricScale({ landmarks, width, height, trueYaw, eyeDepthShare });

  // The same pupil separation, in face space, from the same two landmarks. Dividing
  // the real one by this one gives the factor that turns any face-space centimetre
  // into a real one — and because both sides describe the identical span on the
  // identical frame, everything else cancels: the canonical head's true size, the
  // assumed 63° field of view, and the distance the pose put the head at.
  //
  // Only defined when the irises were actually readable; `pdFace` on its own is a
  // face-space number like any other and says nothing absolute.
  let metricScale = null;
  if (metric) {
    const pdFace = at(LM.IRIS_R_CENTRE, LM.EYE_INNER_R)
      .distanceTo(at(LM.IRIS_L_CENTRE, LM.EYE_INNER_L));
    if (pdFace > 1e-6) metricScale = metric.pdCm / pdFace;
  }

  return {
    measured: true,
    bridge,
    bridgeUp: bridgeDirection(bridge, nasion),
    eyeLineY: (eyeR.y + eyeL.y) / 2,
    eyeCentreX: (eyeR.x + eyeL.x) / 2,
    templeWidth,
    /** This face's width against the average one. 1.0 is average. */
    widthRatio,
    /**
     * True size against the canonical head, from the iris. `null` if unmeasured
     * this frame. Placement never reads it — only the readouts and the *True size*
     * verdict do, because it is the only quantity here that claims real units.
     */
    metricScale,
    /** Pupil separation in centimetres, or null. What an optician would measure. */
    pdCm: metric ? metric.pdCm : null,
    noseWidth,
    /** This nose's width against the average one. 1.0 is average. */
    noseWidthRatio: noseWidth / face.noseWidth,
    // Height and depth per side from this face's own ear landmarks; x from the
    // measured width, since the pinna sits outside the mesh silhouette.
    ears: measuredEars(face, widthRatio, { earTopR, earTopL, cheekR, cheekL }),
  };
}

/**
 * Sanity bounds, in centimetres from the canonical position.
 *
 * A landmark that jumps — a hand across the face, a hard turn, a bad frame — must
 * not be allowed to fling the glasses off the head. Real human variation in these
 * measurements is well inside these limits, so clamping costs nothing on a good
 * frame and contains the damage on a bad one.
 */
const LIMITS = {
  bridgeX: 1.5,
  bridgeY: 2.0,
  bridgeZ: 2.0,
  widthRatio: [0.75, 1.3],
  /**
   * True head size against the average, from the iris.
   *
   * Wider than `widthRatio` on the low side because this one has to admit
   * children — a proportionally normal face can still be genuinely small — while
   * `widthRatio` describes shape, where a 0.75 really is the edge of human.
   */
  metricScale: [0.7, 1.3],
  // Nose width is the more variable of the two — noses differ more between people
  // than head widths do — so its bounds are wider than the face's.
  noseWidthRatio: [0.7, 1.4],
  earY: 1.5,
  earZ: 1.5,
};

/** Keeps a measured anchor set inside plausible human range. */
export function clampAnchors(anchors, face) {
  const reference = canonicalAnchors(face);
  const limit = (value, base, span) => Math.min(Math.max(value, base - span), base + span);

  anchors.bridge.set(
    limit(anchors.bridge.x, reference.bridge.x, LIMITS.bridgeX),
    limit(anchors.bridge.y, reference.bridge.y, LIMITS.bridgeY),
    limit(anchors.bridge.z, reference.bridge.z, LIMITS.bridgeZ),
  );
  anchors.widthRatio = Math.min(Math.max(anchors.widthRatio, LIMITS.widthRatio[0]), LIMITS.widthRatio[1]);
  anchors.templeWidth = face.templeWidth * anchors.widthRatio;
  anchors.noseWidthRatio = Math.min(
    Math.max(anchors.noseWidthRatio ?? 1, LIMITS.noseWidthRatio[0]), LIMITS.noseWidthRatio[1],
  );
  anchors.noseWidth = face.noseWidth * anchors.noseWidthRatio;
  anchors.eyeLineY = limit(anchors.eyeLineY, reference.eyeLineY, LIMITS.bridgeY);

  // Deliberately left alone when null. An unmeasurable iris this frame means "no
  // reading", and clamping that to 1.0 would quietly assert an average-sized head —
  // the exact claim this measurement exists to stop the pipeline making.
  if (anchors.metricScale !== null && anchors.metricScale !== undefined) {
    anchors.metricScale = Math.min(
      Math.max(anchors.metricScale, LIMITS.metricScale[0]), LIMITS.metricScale[1],
    );
  }

  // The measured ear heights and depths are kept — they are the point of measuring
  // them — but bounded against the average head, and x re-derived from the clamped
  // width so the rest points always sit at the face's silhouette.
  const halfWidth = Math.abs(face.point(LM.TEMPLE_R)[0]) * anchors.widthRatio;
  if (anchors.measured && anchors.ears) {
    for (const side of ['right', 'left']) {
      const ear = anchors.ears[side];
      ear.set(
        side === 'right' ? -halfWidth : halfWidth,
        limit(ear.y, reference.ears[side].y, LIMITS.earY),
        limit(ear.z, reference.ears[side].z, LIMITS.earZ),
      );
    }
  } else {
    anchors.ears = estimateEars(face, anchors.widthRatio);
  }

  // A bridge direction wildly off the canonical one means the two landmarks it is
  // built from have collapsed together or crossed; keep the average instead.
  if (anchors.bridgeUp.dot(reference.bridgeUp) < 0.7) {
    anchors.bridgeUp.copy(reference.bridgeUp);
  }
  return anchors;
}

/**
 * The scan's estimate: the per-field WEIGHTED median of every sample taken so far.
 *
 * The plain median replaced a bank of very-low-cutoff One Euro filters, and the
 * difference is the difference between a scan and a convergence. A 0.3–0.4 Hz
 * low-pass takes seconds to arrive at the answer and is *visibly on its way there*
 * the whole time — the glasses slide slowly into place on a new face, and do it
 * again after every tracking dropout, which reads as the fit being unsure of
 * itself. A median has no time constant at all: ten samples in, it already sits at
 * the middle of what was actually measured, it does not drift while collecting,
 * and one wild frame — a blink, a hand, a detector glitch — cannot move it, which
 * is the one property the filters were really there for.
 *
 * The weights are the pose-trust each sample was admitted at (design C4). A
 * binary gate used to decide which samples got in at all, and it decided wrongly
 * on exactly the poses the user actually browses in; now every sample enters at
 * its honest worth, and the median generalises rather than changes: a weighted
 * median is still a median, so one tail — however many low-trust samples pile up
 * in it — cannot drag the estimate past the mass of trustworthy ones. Samples
 * without a weight count as 1, which keeps every equal-weight caller, and every
 * pre-weighting session, bit-for-bit on the old semantics.
 *
 * The estimate assembles the same invariants the filter maintained: `widthRatio`
 * re-derived from the median width, ear x re-derived from that ratio so the rest
 * points always sit at the face's silhouette, and the bridge direction averaged
 * as a (weighted) vector and renormalised.
 */
export function medianAnchors(samples, face) {
  /** The trust this sample was admitted at; 1 when untagged (equal-weight callers). */
  const weightOf = (s) => (Number.isFinite(s.wPose) ? Math.max(s.wPose, 0) : 1);

  /**
   * Weighted median: sort by value, walk the cumulative weight, and take the
   * first value at or past half the total. The tie case is the part that keeps
   * this a *generalisation* of the plain median rather than a quiet change: when
   * the cumulative weight lands exactly ON half — which is precisely what happens
   * at the lower middle of an even count of equal weights — the answer is the
   * mean of the two straddling values, exactly as `(sorted[mid-1]+sorted[mid])/2`
   * was. Equal weights therefore reproduce the old median bit for bit, odd and
   * even counts alike; the tolerance on the tie is relative and tiny, there only
   * because equal *float* weights can accumulate a few ulps of summation error.
   */
  const median = (values, weights) => {
    const order = values.map((_, i) => i).sort((a, b) => values[a] - values[b]);
    let total = 0;
    for (const w of weights) total += w;
    // Weightless input (cannot happen through the admission path, which floors
    // the trust at 0.05) degrades to the plain median rather than to nonsense.
    if (!(total > 0)) {
      const mid = order.length >> 1;
      return order.length % 2
        ? values[order[mid]]
        : (values[order[mid - 1]] + values[order[mid]]) / 2;
    }
    const half = total / 2;
    const tie = total * 1e-9;
    let cum = 0;
    for (let k = 0; k < order.length; k++) {
      cum += weights[order[k]];
      if (cum >= half - tie) {
        return cum <= half + tie && k + 1 < order.length
          ? (values[order[k]] + values[order[k + 1]]) / 2
          : values[order[k]];
      }
    }
    return values[order[order.length - 1]];
  };

  const allWeights = samples.map(weightOf);
  const of = (pick) => median(samples.map(pick), allWeights);

  const bridgeUp = new THREE.Vector3();
  for (const sample of samples) bridgeUp.addScaledVector(sample.bridgeUp, weightOf(sample));
  if (bridgeUp.lengthSq() < 1e-8) bridgeUp.set(0, 1, 0);
  bridgeUp.normalize();

  const templeWidth = of((s) => s.templeWidth);
  const widthRatio = templeWidth / face.templeWidth;
  const noseWidth = of((s) => s.noseWidth);
  const halfWidth = Math.abs(face.point(LM.TEMPLE_R)[0]) * widthRatio;

  // The iris reading is the one field that can be absent on a sample — a blink is
  // enough — so it is taken over whichever samples have it rather than over all of
  // them, each still carrying its own weight. `null` when none do, which the
  // caller carries forward.
  const readings = [];
  const readingWeights = [];
  const pupils = [];
  const pupilWeights = [];
  for (const s of samples) {
    const w = weightOf(s);
    if (s.metricScale !== null && s.metricScale !== undefined) {
      readings.push(s.metricScale);
      readingWeights.push(w);
    }
    if (s.pdCm !== null && s.pdCm !== undefined) {
      pupils.push(s.pdCm);
      pupilWeights.push(w);
    }
  }

  return {
    measured: true,
    metricScale: readings.length ? median(readings, readingWeights) : null,
    pdCm: pupils.length ? median(pupils, pupilWeights) : null,
    bridge: new THREE.Vector3(of((s) => s.bridge.x), of((s) => s.bridge.y), of((s) => s.bridge.z)),
    bridgeUp,
    eyeLineY: of((s) => s.eyeLineY),
    eyeCentreX: of((s) => s.eyeCentreX),
    templeWidth,
    widthRatio,
    noseWidth,
    noseWidthRatio: noseWidth / face.noseWidth,
    ears: {
      right: new THREE.Vector3(-halfWidth, of((s) => s.ears.right.y), of((s) => s.ears.right.z)),
      left: new THREE.Vector3(halfWidth, of((s) => s.ears.left.y), of((s) => s.ears.left.z)),
    },
  };
}
