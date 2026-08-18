/**
 * Fitting: deciding where on a head a given pair of glasses belongs.
 *
 * The trick that makes this tractable is choosing the right coordinate space.
 * We never solve placement in screen space, and never per frame. Instead we solve
 * it *once*, in the canonical face model's own space — the average head described
 * in `canonical-face.js`. MediaPipe hands us a matrix that maps that head onto the
 * real one every frame, so a placement solved there is automatically correct at
 * every pose, distance and angle, for free.
 *
 * So the per-frame cost is a matrix multiply, and the interesting work happens
 * once at load time, here.
 *
 * Two measurements do the whole job:
 *
 *   1. Where the frame touches the nose. Real glasses are carried by the nose,
 *      not the ears — the pads are the load-bearing contact and everything else
 *      follows from them. We find that point on the model geometrically.
 *
 *   2. How wide the frame is. Combined with the head width MediaPipe infers,
 *      this decides whether the frame is scaled to true physical size or
 *      normalised to always look proportionate.
 */

import * as THREE from 'three';
import { LM } from './canonical-face.js';
import {
  sideInterference, PAD_SINK, GUARD_BAND, GUARD_MAX,
} from './nose.js';

/**
 * Frames on which the guard hit `GUARD_MAX` used to be counted HERE, in a
 * module-level `let` that no reset could reach.
 *
 * It is gone, and the shape of the bug is worth keeping in front of whoever
 * next reaches for a module-level counter. `resetFit` and `remeasure` clear
 * per-session state through the state object; module scope is not on the state
 * object, so the count accumulated across identity convictions, source
 * switches and re-measures for the whole life of the page — and
 * `noseSeat.guardOverflow` reported the previous person's cap overflows into
 * the next person's session. It also made the telemetry replay
 * order-dependent WITHIN one process, so the instrument the seat's constants
 * were justified against depended on which fixture ran first.
 *
 * The counter now belongs to whoever owns the session (`solvePlacement`'s
 * optional `stats`), exactly the pattern `blendFittedDepth` in `anchors.js`
 * already used for `stats.clamped`, and `frame.js` hands it the seat state —
 * which `resetFit` clears whole. A caller that passes no `stats` gets zero and
 * counts nothing, which is the honest answer for a one-shot solve.
 */

/** The canonical face model is in centimetres; glTF is in metres. */
export const FACE_UNITS_PER_METRE = 100;

const UP = new THREE.Vector3(0, 1, 0);
const FACE_Z = new THREE.Vector3(0, 0, 1);
const placedMatrix = new THREE.Matrix4();
const scaleTriple = new THREE.Vector3();
const scratchRollQuaternion = new THREE.Quaternion();

/**
 * Measures a loaded glasses model.
 *
 * glTF's convention (+Y up, +Z toward the viewer, metres) means a well-authored
 * frame arrives already oriented: X spans ear to ear, Z runs from the lenses back
 * along the temples. We assume that and verify it, rather than trying to guess an
 * orientation, because guessing wrong is worse than telling the user.
 */
export function analyseModel(object3D) {
  object3D.updateMatrixWorld(true);

  // `precise`, because a model can arrive rotated. Left to itself `setFromObject`
  // transforms each mesh's *bounding box* rather than its vertices, and the AABB of
  // a rotated box is bigger than the AABB of the geometry inside it — on the parts
  // scan, which the exporter left 43° off axis, that reads 1.25 units wide for a
  // frame that is 0.75 wide. Every measurement downstream inherits the error.
  const box = new THREE.Box3().setFromObject(object3D, true);
  const size = box.getSize(new THREE.Vector3());
  const centre = box.getCenter(new THREE.Vector3());

  // Gather every vertex in the model's own space so we can reason about regions
  // of the frame, not just its bounding box.
  const points = [];
  const v = new THREE.Vector3();
  object3D.traverse((node) => {
    if (!node.isMesh) return;
    const position = node.geometry?.attributes?.position;
    if (!position) return;
    for (let i = 0; i < position.count; i++) {
      v.fromBufferAttribute(position, i).applyMatrix4(node.matrixWorld);
      points.push(v.x, v.y, v.z);
    }
  });

  if (points.length === 0) throw new Error('model contains no mesh geometry');

  const noseContact = findNoseContact(points, box, centre, size);
  const noseContacts = findBridgeSurface(points, centre, size);
  const noseSides = splitContactSides(noseContacts, centre, size);
  const { box: frontBox, isolated: frontIsolated, rakeDeg } = findFrontBox(points, box, centre, size);
  const endpiece = findEndpiece(points, centre, size);

  return {
    box,
    /**
     * Just the frame front — lenses, rims, bridge — with the temple arms left out.
     * Anything reasoning about where the lens sits on the face has to use this: the
     * arms run backwards and, once tilted, hang well below the lens, so the full
     * bounding box is nearly twice the height of the thing a wearer looks through.
     */
    frontBox,
    /**
     * False when the front/arm cuts found too little to trust and `frontBox` fell
     * back to the whole model. Worth reporting rather than swallowing: the fallback
     * silently doubles the height the pupil solver thinks it is aiming into, so the
     * frame ends up sitting well too high with nothing looking wrong.
     */
    frontIsolated,
    lensHeightM: frontBox.max.y - frontBox.min.y,
    /**
     * The rake the asset was authored with, in degrees — positive is pantoscopic.
     * `solvePlacement` subtracts it from the requested tilt, so "tilt 8°" means the
     * frame *wears* at 8° whichever pipeline exported it. See `findFrontBox`.
     */
    nativeRakeDeg: rakeDeg,
    size,
    centre,
    /** Ear-to-ear span of the frame front, in metres. */
    widthM: size.x,
    /**
     * WHERE the frame's widest line is — the endpiece, in the model's own
     * space. `widthM` says how wide the frame is; this says at what depth and
     * height it is that wide, which is what turns a width into a fit. See
     * `findEndpiece` and `widthVerdict`.
     */
    endpiece,
    /**
     * Where `widthM` came from: `authored`, `stated` or `assumed`. Carried off
     * the loaded root because on an `assumed` asset the vertices were SCALED to
     * a guess before they were measured, so `widthM` is that guess read back.
     * Anything making a physical claim to a wearer has to be able to say so —
     * see the catalogue note in `models.js`. `null` for a hand-built model in a
     * harness, which is neither a product nor a claim.
     */
    widthSource: object3D.userData?.widthSource ?? null,
    /** Where the pads rest on the nose, in the model's own space (metres). */
    noseContact,
    /**
     * The back of the bridge, as a handful of samples — what actually meets the
     * nose. `noseContact` is the average of these and is where the frame is *hung*;
     * this set is what stops it going any further in. See `nose.js`.
     */
    noseContacts,
    /**
     * The same set, split by side of the centreline — the shape the seat
     * equilibrium reasons in. A wedge bears when BOTH sidewall sets carry
     * load; a single undivided set can only answer "does anything touch",
     * which is how the old seat could report a perfect half-millimetre while
     * one pad floated 3 mm off the nose. See `splitContactSides`.
     */
    noseSides,
    /** The pad lever geometry derived from that split — see `splitContactSides`. */
    padSepM: noseSides.padSepM,
    xbarPadM: noseSides.xbarPadM,
    hasPads: noseSides.hasPads,
    vertexCount: points.length / 3,
    /** True when the model looks like it follows the glTF orientation convention. */
    orientationLooksSane: size.x >= size.y,
  };
}

/**
 * Locates the pad contact point on the model.
 *
 * Take a narrow vertical column through the centre of the frame. Everything in it
 * belongs to the bridge and the nose pads — the lenses and temples are out at the
 * sides. Within that column the rearmost geometry is what touches the face, so we
 * average the back of it to get a contact point.
 *
 * The column widens if it comes up empty, which it will on rimless or
 * heavily-cut-away bridges where there is simply nothing near the centreline.
 */
function findNoseContact(points, box, centre, size) {
  const collect = (halfWidth) => {
    const found = [];
    for (let i = 0; i < points.length; i += 3) {
      if (Math.abs(points[i] - centre.x) <= halfWidth) {
        found.push(points[i], points[i + 1], points[i + 2]);
      }
    }
    return found;
  };

  let column = [];
  let halfWidth = size.x * 0.06;
  for (let attempt = 0; attempt < 5 && column.length < 30 * 3; attempt++) {
    column = collect(halfWidth);
    halfWidth *= 1.8;
  }

  // Nothing near the centreline at all — fall back to the bounding box.
  if (column.length === 0) {
    return new THREE.Vector3(centre.x, centre.y, box.min.z);
  }

  let zMin = Infinity;
  let zMax = -Infinity;
  for (let i = 2; i < column.length; i += 3) {
    if (column[i] < zMin) zMin = column[i];
    if (column[i] > zMax) zMax = column[i];
  }

  // The rearmost slice of the column, with a floor on the band so a perfectly
  // flat bridge still admits its own vertices.
  const band = Math.max((zMax - zMin) * 0.2, size.z * 1e-3, 1e-6);
  const cutoff = zMin + band;

  let sumY = 0;
  let sumZ = 0;
  let n = 0;
  for (let i = 0; i < column.length; i += 3) {
    if (column[i + 2] <= cutoff) {
      sumY += column[i + 1];
      sumZ += column[i + 2];
      n++;
    }
  }

  // X is pinned to the model's centreline rather than averaged: the contact point
  // is symmetric by construction, and averaging would let an asymmetric bridge
  // detail pull the whole frame sideways.
  return new THREE.Vector3(centre.x, sumY / n, sumZ / n);
}

/**
 * The back surface of the bridge region, as a small point set.
 *
 * `findNoseContact` above answers "where does this frame touch a nose", as one
 * point. That is the right thing to *hang* the frame from and the wrong thing to
 * rest it on, because a bridge is not a point: pads sit 5–9 mm either side of the
 * centreline, a saddle spans 10 mm of it, and the nose falls away 5.5 mm across that
 * span. Averaging them puts half the geometry inside the face.
 *
 * So take the same central column, bin it across x and y, and keep the *rearmost*
 * vertex in each bin. That is the frame's back surface at a couple of millimetres'
 * resolution — the set of points that can touch skin, without any assumption about
 * which of them is a pad.
 *
 * The *whole* column, top to bottom, rather than a band around the pads. On all four
 * frames here the pads do win — the seat comes to rest on the nasal sidewall every
 * time — but that is a fact about these frames and not a property of the solve. What
 * the solve is expressing is "no part of the frame goes inside the face", and a
 * sample set that leaves geometry out cannot express it: a frame with a deeper brow
 * bar than these have would rest on the glabella, and bounded to the pads the solve
 * would happily bury it there.
 */
const BRIDGE_COLUMN = 0.13; // of the frame's width, each side of the centreline

function findBridgeSurface(points, centre, size) {
  const halfWidth = size.x * BRIDGE_COLUMN;
  // ~0.8 mm cells on a 140 mm frame. The bin keeps its rearmost vertex, so the cell
  // size is how far the *skin* is allowed to vary underneath one sample — a coarser
  // grid lets the deepest point of the frame fall between two samples and sink a
  // little further in than the solve believes. Measured over every vertex of every
  // frame in the catalogue: 2.8 mm cells overshot the intended pad sink by up to
  // 0.6 mm, 1.7 mm cells by 0.3 mm, and 0.8 mm cells by 0.1 mm.
  //
  // 1.7 mm was enough until the aviator, whose bridge column falls away over 11 mm of
  // depth against 8-9 mm on the others — a steeper surface puts more skin under one
  // cell, and it sank 1.3 mm into the nose while its own solve reported a perfect
  // half-millimetre. The cost of the finer grid is a few hundred samples instead of a
  // few hundred, once, at load.
  const cell = Math.max(size.x * 0.006, 1e-4);

  const bins = new Map();
  for (let i = 0; i < points.length; i += 3) {
    const x = points[i];
    const y = points[i + 1];
    const z = points[i + 2];
    if (Math.abs(x - centre.x) > halfWidth) continue;

    const key = `${Math.round(x / cell)},${Math.round(y / cell)}`;
    const found = bins.get(key);
    if (!found || z < found.z) bins.set(key, new THREE.Vector3(x, y, z));
  }

  // Nothing in the column — a lens-only asset, or a bridge cut away entirely. The
  // seat then has nothing to solve and leaves the placement alone, which is the
  // right answer rather than a guessed one.
  return [...bins.values()];
}

/**
 * Splits the bridge-surface samples into left / centre / right sets, once, at
 * load — the contact-side geometry the seat equilibrium stands on (stage 5,
 * design B.3).
 *
 * The dead band ε around the centreline is what makes the split mean "pad"
 * rather than "slightly off-centre saddle vertex": 15% of the column's own
 * half-width, floored at 3 mm — below 3 mm the two sides of a saddle's crown
 * would be called pads and the solve would find two-sided bearing on geometry
 * that is actually one bar. (In the model's space +x is the wearer's LEFT,
 * matching the face-space convention the ears use.)
 *
 * Also derived here, because they are properties of the same split:
 *
 *  - `padCentroids`: the mean of the rearmost 30% (by z) of each side — where
 *    the pad actually meets skin, as opposed to the side set's whole extent,
 *    which includes rim geometry that never touches anything.
 *  - `padSepM`: the x-span between those centroids — the frame's effective
 *    pad separation, the number the wedge trades against height (≈1.9 mm of
 *    resting height per mm of separation mismatch).
 *  - `xbarPadM`: the mean |x|-lever of the pads about the centreline — the arm
 *    the G5 roll balance divides by.
 *  - `hasPads`: both sides carry at least 8 samples. Below that the "side" is
 *    a couple of stray vertices and the two-sided solve would be standing on
 *    sampling accident; the equilibrium then falls back to the 1-DOF push.
 */
function splitContactSides(noseContacts, centre, size) {
  const halfWidth = size.x * BRIDGE_COLUMN;
  const epsilon = Math.max(0.15 * halfWidth, 0.003);

  const L = [];
  const R = [];
  const C = [];
  for (let i = 0; i < noseContacts.length; i++) {
    const dx = noseContacts[i].x - centre.x;
    if (dx > epsilon) L.push(i);
    else if (dx < -epsilon) R.push(i);
    else C.push(i);
  }

  // The rearmost 30% of a side, BY DEPTH — the pad proper. On a glasses
  // bridge the geometry that reaches furthest back IS the pad; the rest of
  // the side set is rim and bridge-bar surface that only meets skin when
  // something is already wrong. The subset matters as much as the centroid:
  // the equilibrium's bearing question is asked of these indices (a side's
  // near-centreline contacts ride the nose's ridge at ANY height, so the
  // full set can never report a floating pad — the stage-5 probe measured
  // exactly that: full-set bearing flat within 0.06 mm across the entire
  // ±4/−12 mm sweep on every catalogue asset, while the pad subsets swing
  // by up to 1.5 mm).
  const padOf = (indices) => {
    const sorted = [...indices].sort((a, b) => noseContacts[a].z - noseContacts[b].z);
    return sorted.slice(0, Math.max(1, Math.ceil(sorted.length * 0.3)));
  };
  const padL = padOf(L);
  const padR = padOf(R);

  const centroidOf = (indices) => {
    if (indices.length === 0) return null;
    const mean = new THREE.Vector3();
    for (const index of indices) mean.add(noseContacts[index]);
    return mean.divideScalar(indices.length);
  };

  const left = centroidOf(padL);
  const right = centroidOf(padR);
  return {
    L,
    R,
    C,
    padL,
    padR,
    epsilonM: epsilon,
    padCentroids: { left, right },
    padSepM: left && right ? left.x - right.x : 0,
    xbarPadM: left && right
      ? (Math.abs(left.x - centre.x) + Math.abs(right.x - centre.x)) / 2 : 0,
    hasPads: L.length >= 8 && R.length >= 8,
  };
}

/**
 * The endpiece: where the frame is at its widest, in the model's own space.
 *
 * A width on its own cannot answer a fit question, and that is the whole of why
 * this exists. A head is not a cylinder — it narrows sharply from the ear
 * forward (measured on the canonical mesh in the frame's own height band: 154.9
 * mm across at the ear plane, 144.3 mm a centimetre forward of it, 111.8 mm at
 * the lens plane). So "is this 140 mm frame wide enough" has no answer until you
 * say where the 140 mm is: a flat frame carries its widest line forward, where
 * the head is narrow, and a wrapped one carries it back, where the head is
 * broad. Both fit; they fit at different widths. `widthVerdict` turns that into
 * a verdict by asking where the two silhouettes cross.
 *
 * The outer 2% of the half-width on each side, by MEDIAN depth and height rather
 * than by extremum: an endpiece is a region a few millimetres across, and the
 * single outermost vertex of a photogrammetry scan is a spike on a hinge screw.
 */
function findEndpiece(points, centre, size) {
  const cut = size.x * 0.48;
  const zs = [];
  const ys = [];
  for (let i = 0; i < points.length; i += 3) {
    if (Math.abs(points[i] - centre.x) < cut) continue;
    zs.push(points[i + 2]);
    ys.push(points[i + 1]);
  }
  // Nothing out at the edges is not a failure mode any real frame has, but a
  // lens-only asset would land here; the frame's own centre is the honest
  // fallback, and it makes the verdict read the widest depth as the centre depth.
  if (zs.length === 0) return { z: centre.z, y: centre.y, count: 0 };
  const median = (a) => {
    a.sort((p, q) => p - q);
    const mid = a.length >> 1;
    return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
  };
  return { z: median(zs), y: median(ys), count: zs.length };
}

/**
 * Isolates the frame front from the temple arms.
 *
 * Two cuts, both from the model's own proportions. Depth: the front sits in the
 * frontmost slice, the arms run away behind it. Width: the arms live at the extreme
 * outer edges, so anything further out than the lenses reach is an arm.
 *
 * Falls back to the whole model if the cuts find almost nothing, which is what a
 * lens-only asset with no arms would do. The return says which happened, because
 * the fallback is a silent failure on an asset that *does* have arms.
 */
function findFrontBox(points, box, centre, size) {
  const zCut = box.max.z - size.z * 0.18;
  const xCut = size.x * 0.45;

  const found = new THREE.Box3();
  const v = new THREE.Vector3();
  let count = 0;
  // For the rake fit below: the front slice's least-squares dz/dy.
  let sy = 0; let sz = 0; let syy = 0; let syz = 0;

  for (let i = 0; i < points.length; i += 3) {
    if (points[i + 2] >= zCut && Math.abs(points[i] - centre.x) <= xCut) {
      found.expandByPoint(v.set(points[i], points[i + 1], points[i + 2]));
      count++;
      sy += points[i + 1];
      sz += points[i + 2];
      syy += points[i + 1] * points[i + 1];
      syz += points[i + 1] * points[i + 2];
    }
  }

  if (count <= 20) return { box: box.clone(), isolated: false, rakeDeg: 0 };

  // How raked the asset's front already IS, before any tilt is applied to it.
  //
  // Scans arrive worn: a photogrammetry or image-to-3D capture records the frame the
  // way it sits on a face, pantoscopic tilt included — the crystal pair's orient
  // quaternion was deliberately solved to put its front at its own measured 7.2°.
  // Applying the fit's tilt on top of that double-counts: measured across the
  // catalogue, one 8° constant produced effective tilts from 2.2° (khronos, whose
  // front leans the other way) to 15.2° (the Meshy scans — twice a real frame),
  // tucking lower rims millimetres into the cheek band where the occluder eats them.
  //
  // The front slice's dz/dy is that rake: a pantoscopically-leaning front has its
  // top rim further +z than its bottom, so the slope is positive. `solvePlacement`
  // subtracts it, making the tilt setting mean the same worn angle on every asset.
  const varY = syy - (sy * sy) / count;
  const slope = varY > 1e-9 ? (syz - (sy * sz) / count) / varY : 0;
  let rakeDeg = THREE.MathUtils.radToDeg(Math.atan(slope));
  // A rake past what any frame is manufactured with means the slice caught arms or
  // debris, and subtracting it would tilt the frame to cancel a measurement error.
  if (!Number.isFinite(rakeDeg) || Math.abs(rakeDeg) > 18) rakeDeg = 0;

  return { box: found, isolated: true, rakeDeg };
}

/**
 * How the frame is sized onto the head.
 *
 * `physical` renders the frame at its true manufactured size. A 148mm frame stays
 * 148mm, so it reads as too wide on a narrow face and too narrow on a broad one —
 * which is the honest answer, and the one that makes a size recommendation mean
 * something.
 *
 * `proportional` rescales the frame to span the face whatever the face measures.
 * It always looks flattering and it is always a lie about fit. Useful when a model
 * was authored at an arbitrary scale, which many are.
 */
export const FIT_MODES = ['physical', 'proportional'];

export const DEFAULT_FIT = {
  mode: 'physical',
  /** Multiplies the solved size. 1 = leave it alone. */
  sizeMultiplier: 1.0,
  /**
   * In `proportional` mode, frame width as a fraction of face width.
   *
   * It shipped at 1.0, which is not a neutral default — it is one of the two
   * failure modes, exactly. `proportional` sizes the frame to
   * `anchors.templeWidth × widthRatio`, and `templeWidth` is the span between
   * landmarks 127 and 356: the WIDEST vertex pair in the whole mesh. So 1.0 asks
   * for a frame exactly as wide as the wearer's head is at its widest point,
   * which is the width at which the temple arm stops being able to touch the
   * head at all — see `widthVerdict`. Measured: on the mean face at 1.0, all
   * eleven catalogue frames return no contact, i.e. `wide`. Every frame in
   * fit-to-face mode was rendered a full head-breadth across.
   *
   * The default is now the MAXIMIN point between the two failures the mode's own
   * docstring names — the arm contacting halfway along its run, as far from
   * fouling the front as from sliding off the back. Measured on the mean face
   * over the whole catalogue, that is `widthRatio` 0.9255 (per asset 0.864 to
   * 0.966, the spread being how far back each frame carries its widest line);
   * quantised to the control's own 0.01 step, which moves the mean contact from
   * 0.500 to 0.510.
   *
   * Grading that contact into "slightly tight" and "slightly loose" would need a
   * soft-tissue compression model or real frames measured on real faces. Neither
   * exists here, so the default sits at the centre and says so.
   */
  widthRatio: 0.93,
  /** Shifts along the face's own axes, in centimetres. */
  offsetX: 0.0,
  offsetY: 0.0,
  /**
   * Extra standoff from the nose, in centimetres — vertex distance, on top of
   * wherever the frame comes to rest.
   *
   * It is applied *after* the seating solve, not before, and that is what gives it a
   * meaning. Added to the anchor target beforehand it would fight the seat and lose:
   * the solve would simply push the frame back onto the nose and the slider would do
   * nothing. Applied afterwards, zero means resting on the nose and the number is how
   * far off it the wearer wants it — which is what an optician measures.
   */
  offsetZ: 0.0,
  /** Forward tilt of the frame front, degrees. Real frames sit at roughly 8°. */
  pantoscopicTilt: 8.0,
  /** Sets the frame's height from the wearer's eye line. See `solvePlacement`. */
  alignToPupils: true,
  /** Swings the temple arms onto the ears. See `temples.js`. */
  fitTemples: true,
  /** Rests the frame on the nose's surface rather than on one landmark. See `nose.js`. */
  seatOnNose: true,
  /**
   * The G5 pad-balance roll: lets the seat solve a roll about the face's Z
   * axis (clamped ±3°) that equalises the two pads' bearing on an asymmetric
   * nose, and — since 2026-08-18 — lets the height search run over BALANCED
   * configurations, which is the only way a laterally deviated nose reaches
   * two-sided bearing at all. See `seat-equilibrium.js` for both.
   *
   * STILL DARK, and the 2026-08-18 run that decided it is in the harness. The
   * objection that kept it dark at stage 5 is GONE; a different one stopped it.
   *
   * What went: the ≈−1.2° roll that appeared on every average face was blamed
   * on the canonical mesh. That mesh is exactly symmetric — all 468 vertices
   * have an exact mirror partner. The handedness was the FRAME's own scan
   * asymmetry, which the solve now measures on the wearer's own surface with a
   * mirror probe and subtracts. Measured on the production path over twenty
   * seconds, against 0.235° (half a pixel at the frame's own ends, 1.74 px/mm
   * at 45 cm) as the angle a wearer could see:
   *
   *   symmetric wearer, clean landmarks       φ = 0.000°, held and wandering
   *   symmetric wearer, 0.3 mm noise          |φ| ≤ 0.19°, four seeds and 60 s
   *   ±1.5 mm skew, clean landmarks           −1.001° / +1.000°, mean −0.0005°
   *   all eleven catalogue frames, mean face  φ = 0.000°, asym ≤ 0.006 mm
   *
   * And what it buys: the deviation at which the seat gives up two-sided
   * bearing moves from +1.5/−2.0 mm — INSIDE the 1–3 mm Ferrario puts normal
   * adult asymmetry at — to ±5 mm, its documented edge; S12 and S12m go from
   * `unbalanced` to a solved wedge at BOTH signs, and seat measurable 1 goes
   * from 3/15 failing to clear.
   *
   * What stopped it: the wearer's own recording. With the balance lit the
   * telemetry replay's hard gate `seat-z at >40° yaw: glances mean ≤ 2.0 mm`
   * reads 2.035 mm — a forward push at exactly the pose the original complaint
   * was about, on a gate that passes with the flag dark. It is not the roll
   * channel easing on unreadable evidence: latching it off-square with every
   * other channel moved the figure by 0.002 mm. Everything ELSE on that
   * recording improves, and by a lot — still rmsPx 5.94 → 2.34 and placeZP95Mm
   * 6.44 → 0.68 against a 3.95/4.35 pin, yaw over40MeanMm +1.48 → +0.45 — so
   * this is a trade, not a defect, and a trade on one wearer's recording is a
   * live-session decision rather than a harness one. G8's own discipline, and
   * the same discipline that kept it dark at stage 5.
   */
  padBalance: false,
  /**
   * How far, in centimetres, the seating solve may move the frame in or out.
   *
   * The seat is a correction to a placement that is already roughly right — this is
   * there to contain a contact sample that landed on the wrong geometry or a depth
   * lookup off the edge of the modelled patch, not to shape ordinary results.
   *
   * 1.5, recalibrated from 1.0 when the tilt became rake-conditioned. The old chain
   * rotated every asset by the full tilt constant, and the seat's legitimate pushes
   * measured under ±6 mm; conditioning the tilt on each asset's baked rake moves the
   * pre-seat hang by up to ~sin(rake) of the contact's forward reach — ~5 mm on the
   * Meshy scans — through the pupil-height slide's z-component, and the seat is the
   * solver whose job is to close exactly that gap. At 1.0 it clamped ~10 mm short on
   * the default pair and the frame visibly floated off the nose.
   */
  maxNoseSeat: 1.5,
  /** Where the pupils should fall in the lens: 0 is the top edge, 1 the bottom. */
  pupilTarget: 0.45,
  /**
   * How far, in centimetres, the eye line may move the frame off the nose-bridge
   * anchor.
   *
   * Generous on purpose. The anchor is a *guess about the asset* — where the pads
   * appear to be — and how good that guess is varies wildly: a model with pads
   * modelled separately lands within a couple of millimetres, while one whose
   * bridge is a single bar can be 15mm out. The eye line is a measurement of the
   * face and does not vary like that, so it is the one that should win.
   *
   * This is not just working around bad assets, either. Real nose pads are
   * adjustable — an optician bends them to set exactly this height — so treating
   * the contact point as approximate and the pupil height as the target is what
   * actually happens in a dispensary.
   *
   * It stays bounded so a bad landmark cannot fling the frame off the face.
   */
  maxPupilCorrection: 1.5,
};

/**
 * Solves the frame's transform within face space.
 *
 * `anchors` is this face's own geometry, measured by `anchors.js` — where the nose
 * bridge actually is, where the eye line actually is, how wide the face actually
 * is. Passing the canonical set instead gives the average-face behaviour, which is
 * what the *Adapt to face* toggle switches between.
 *
 * `seatConfig`, when present, is the frame's SOLVED resting configuration —
 * `{ s, zeta, phi }`, the eased face constants `frame.js` owns (stage 5),
 * plus (2026-08-17) `needRef`, the trust-admitted carried surface reference
 * the guard baselines on — optional, and absent means the raw baseline:
 * `s` cm of vertical resting height relative to the optical target, applied
 * along the bridge; `zeta` cm of standoff along +Z; `phi` radians of pad-
 * balance roll about face Z. With it, this function only APPLIES — the per-
 * frame work is one soft-max contact pass, the raw non-penetration guard
 * (G2). Without it (harness callers, no-surface sessions) the standoff is
 * solved standalone from this frame's own contacts, which is the pre-stage-5
 * behaviour with the argmax softened. `fit.seatOnNose: false` overrides a
 * passed `seatConfig` entirely — every seat channel dark, the pure hang.
 *
 * `stats`, when present, is a CALLER-OWNED counter object — today one field,
 * `guardOverflow` — carrying the diagnostics that describe a session rather
 * than a frame. Caller-owned rather than module-owned so a reset can reach
 * them: see the note where the module-level counter used to live.
 *
 * Returns a transform to apply to the model *inside* the head node.
 */
export function solvePlacement({
  model, anchors, fit, face = null, surface = null, seatConfig = null,
  stats = null,
}) {
  const scale = fit.mode === 'physical'
    // Model metres -> face-space centimetres. No division by head scale: the head
    // node is posed so the canonical head covers the observed face, so a frame at
    // true size here is already at true size against that face.
    //
    // **And deliberately not divided by `anchors.metricScale` either**, though the
    // argument for doing so is real and nearly convincing. Face space is measured in
    // the *canonical* head's centimetres, so a 140 mm frame drawn at 14.0 units spans
    // 140/154.9 of the head whoever is wearing it — which does make this mode less
    // personal than its name promises, and the iris now measures exactly the factor
    // that would fix it.
    //
    // It is not applied because the *verdict* on the other side of the comparison is
    // calibrated against `templeWidth`, and `templeWidth` is the span between two
    // silhouette landmarks rather than anatomical head breadth: converted to real
    // units it puts these faces at 171 and 175 mm across, where a human head is
    // 145-155. The measurement is sound — the pupil distances behind it are 59 mm on
    // both faces, and the scale factors differ between them the way two faces should
    // — but the *thresholds* it would feed were tuned in the old units, and re-basing
    // them needs real frames on real faces, not two sample photographs. Shipping half
    // of that swaps a known, uniform bias for an unknown, uneven one.
    //
    // So the number is measured, reported, and used where only a ratio matters (see
    // `isDifferentFace`). Sizing waits for the calibration.
    ? FACE_UNITS_PER_METRE * fit.sizeMultiplier
    // Span a fixed fraction of *this* face, using its measured width. Untouched by
    // any of the above: this mode is a ratio against the face by construction, and a
    // ratio is the one thing that was never biased.
    : (anchors.templeWidth * fit.widthRatio / model.widthM) * fit.sizeMultiplier;

  // Tilt the frame forward about the contact point, the way a real frame pivots
  // on its pads.
  //
  // Sign matters and is easy to get backwards. Face space is +Y up and +Z out of
  // the face, so a positive rotation about X carries the top rim outwards and tucks
  // the bottom rim in towards the cheeks. That is pantoscopic tilt: the lens plane
  // leans to meet the wearer's downgaze. Negating it gives the retroscopic
  // opposite — bottom rims flaring off the cheeks, top rims into the brow — which
  // is what this did until it was measured.
  //
  // Minus whatever rake the asset already carries. `pantoscopicTilt` states the
  // angle the frame should WEAR at, and a scanned asset arrives already leaning —
  // applied on top, one constant produced 2.2° to 15.2° across the catalogue. See
  // `findFrontBox` for the measurement.
  const quaternion = new THREE.Quaternion().setFromEuler(
    new THREE.Euler(
      THREE.MathUtils.degToRad(fit.pantoscopicTilt - (model.nativeRakeDeg ?? 0)), 0, 0,
    ),
  );

  // `seatOnNose` governs EVERY seat channel, not only the standoff it is
  // named after: with the toggle off, the placement is the pure landmark
  // hang, exactly as it was before the equilibrium existed. The solved
  // configuration is ignored whole rather than trusted-but-frozen — a
  // resting height or roll applied without the standoff and its guard is a
  // frame lowered into a nose nothing is protecting, which is precisely the
  // state the toggle exists to switch OFF.
  const seatCfg = fit.seatOnNose !== false ? seatConfig : null;

  // The pad-balance roll (G5), folded into the ORIENTATION stage so the staged
  // order stays clean: rotation about the face's own Z axis, pre-multiplied so
  // it acts after the tilt in face space. Because the position below is solved
  // as `target − R·(scale·contact)`, the contact point re-pins itself under
  // the rolled quaternion — the roll pivots about the hang point, which is how
  // a frame actually rolls on its pads, and no later stage needs to know it
  // happened. Zero when the flag is dark or the channel has nothing, and a
  // zero angle composes as the identity quaternion bit-exactly.
  const phi = seatCfg?.phi ?? 0;
  if (phi !== 0) {
    quaternion.premultiply(
      scratchRollQuaternion.setFromAxisAngle(FACE_Z, phi),
    );
  }

  // Where the pads should end up: on *this* nose. Taking x from the measured
  // bridge rather than the face's centreline also keeps the frame centred on
  // people whose nose does not sit exactly mid-face.
  //
  // z carries no offset here — the standoff is solved against the nose's surface
  // below, and `offsetZ` is added on top of the answer.
  const target = new THREE.Vector3(
    anchors.bridge.x + fit.offsetX,
    anchors.bridge.y + fit.offsetY,
    anchors.bridge.z,
  );
  // The hang point survives the in-place subtraction below — the seat solver
  // pivots its roll about it, and the readouts name it.
  const hangTarget = target.clone();

  // Place the model so its contact point lands on that target once scaled and
  // tilted: position = target - R * (scale * contact).
  const contact = model.noseContact.clone().multiplyScalar(scale).applyQuaternion(quaternion);
  const position = target.sub(contact);

  // --- vertical, from the eye line ---
  //
  // The nose bridge alone is not enough to set the height. Its landmark carries a
  // fixed offset against the canonical mesh — both sample faces sit about 1.5mm low
  // by the same amount, which is the detector disagreeing with the average head, not
  // two people who happen to share a nose. Anchoring to it inherits that error on
  // every face.
  //
  // The eye corners do not have that problem, and they are what actually matters:
  // an optician fits a frame so the pupils sit a little above the lens centre. So
  // the eye line sets the height, and the nose bridge bounds how far it may.
  let verticalCorrection = 0;
  if (fit.alignToPupils) {
    const placed = model.frontBox.clone().applyMatrix4(
      new THREE.Matrix4().compose(
        position, quaternion, new THREE.Vector3(scale, scale, scale),
      ),
    );
    const lensHeight = placed.max.y - placed.min.y;

    if (lensHeight > 1e-6) {
      const achieved = (placed.max.y - anchors.eyeLineY) / lensHeight;
      // Translation moves the lens without resizing it, so one step is exact.
      const wanted = (fit.pupilTarget - achieved) * lensHeight;
      verticalCorrection = Math.min(Math.max(wanted, -fit.maxPupilCorrection), fit.maxPupilCorrection);

      // Slide *along the nose*, not straight up.
      //
      // The bridge slopes back towards the brow — about 0.82 up per 0.57 back — so
      // lifting the frame vertically walks it off the front of the nose. A 1cm lift
      // leaves the pads 7mm clear of the surface, and the frame visibly floats in
      // front of the face instead of resting on it.
      //
      // Moving along the bridge direction keeps the pads in contact. `bridgeUp` is a
      // unit vector, so the distance is scaled to still deliver the vertical change
      // the optics asked for.
      const up = anchors.bridgeUp ?? UP;
      const along = verticalCorrection / Math.max(up.y, 0.2);
      position.addScaledVector(up, along);
    }
  }

  // --- the resting configuration's base, for the equilibrium solver ---
  //
  // Everything above this line is the OPTICAL placement: scale, orientation
  // (tilt + any applied roll), position, height from the eye line. The seat
  // equilibrium (see `seat-equilibrium.js`) sweeps its candidate heights from
  // exactly here — s = 0 IS this position — so the base is captured before the
  // seat channels move anything. Cheap: two clones and a shared reference.
  const up = anchors.bridgeUp ?? UP;
  const seatBase = {
    position: position.clone(),
    quaternion: quaternion.clone(),
    scale,
    target: hangTarget,
    up,
    phi,
  };

  // --- resting height, from the seat's solved equilibrium ---
  //
  // The eased `s` channel: how far below (or above) the optical height this
  // frame actually RESTS on this nose, solved on events by the wedge-descent
  // equilibrium and eased by `frame.js`. Applied along the bridge direction
  // with the same 1/û_y scaling as the optical slide — s is stated in vertical
  // centimetres, and moving along the nose is what keeps the pads on it (the
  // same argument as the pupil slide above). Zero whenever there is no solved
  // configuration, which keeps this stage a bit-exact no-op for every caller
  // that has none.
  const restHeight = seatCfg?.s ?? 0;
  if (restHeight !== 0) {
    position.addScaledVector(up, restHeight / Math.max(up.y, 0.2));
  }

  // --- standoff, from the nose's surface ---
  //
  // Last, and along z only, because that is the axis nothing above it cares about.
  // Scale, tilt, roll and height are all settled by now; pushing the frame in or
  // out changes none of them, so this cannot undo any of their work — and it is the
  // one remaining degree of freedom the placement had simply been guessing at. See
  // `nose.js` for what it is solving against and why a landmark cannot answer it.
  //
  // `surface` is the occluder's own field and is what the app always passes. It is the
  // same triangles that are about to write the depth buffer, so the frame is seated
  // against the nose the viewer will actually see it against — which is the one thing
  // this solve could not previously claim. `face.surface` remains as a fallback for
  // callers with no occluder: it is the canonical nose, and seating against it while
  // something else draws a different one is exactly the defect the occluder rewrite
  // removed, so it is a last resort rather than an equivalent.
  //
  // Two modes, one measurement. The soft-max interference over the contact set
  // is taken at the APPLIED placement either way; what differs is what is done
  // with it. With a `seatConfig`, the eased `zeta` channel is applied and the
  // measurement only GUARDS (G2): if the raw law wants more than the eased
  // standoff by over `GUARD_BAND`, the excess lands raw this frame — skin is a
  // constraint and never waits for a time constant — while every inward move
  // arrives through the eased channel only. Without one, the raw law is
  // applied directly: today's behaviour with the argmax softened, which is
  // what harness measurements and surface-less sessions get.
  let noseSeat = null;
  const skin = surface ?? face?.surface ?? null;
  if (fit.seatOnNose !== false && skin && model.noseContacts?.length) {
    placedMatrix.compose(position, quaternion, scaleTriple.setScalar(scale));
    const felt = sideInterference({
      surface: skin,
      contacts: model.noseContacts,
      sides: model.noseSides ?? null,
      toFace: placedMatrix,
      anchors,
    });

    const limit = fit.maxNoseSeat ?? DEFAULT_FIT.maxNoseSeat;
    // The raw law's own answer at this placement — reported always, applied
    // only when no eased channel owns the standoff. The LEVEL is the argmax's
    // (`max − PAD_SINK`, today's law bit for bit — see the amendment note on
    // SOFTMAX_TAU in nose.js); the soft reductions beside it are what the
    // equilibrium and the readouts compare.
    const needed = felt.touched > 0 ? felt.max - PAD_SINK : 0;

    let guard = 0;
    let applied;
    let needAdm = needed;
    if (seatCfg) {
      const zeta = seatCfg.zeta ?? 0;
      // The guard's baseline is the CARRIED surface reference (2026-08-17,
      // the ">40° forward push" fix) — the trust-admitted window median
      // `frame.js` publishes as `seatConfig.needRef` — not this frame's raw
      // reading. The guard answers true penetration against the reference:
      // it fires when the admitted estimate of the law jumps past the eased
      // channel by more than the band, and lands the excess raw, exactly
      // its old contract with "the law" now meaning "the law as admitted at
      // pose trust". What the raw baseline did at >40° of yaw was measured
      // end to end by the z-decomposition: 60–100% guard duty, 0.8–3.3 mm
      // mean, 28–54 cap overflows a segment, every millimetre of it the
      // view-morphed surface and none of it skin — the guard was bridging
      // the frozen channel to a fiction every frame. The price is stated
      // and accepted per the stage-6 cap precedent: a genuine full-trust
      // penetration now reaches the guard through the window (~half a
      // second), and until then the frame tucks behind the drawn boundary —
      // the lesser visual evil, same as the cap's own ruling. A caller
      // without the reference (the harness's hand-built seatConfigs,
      // standalone sessions) keeps the raw baseline unchanged.
      const ref = seatCfg.needRef;
      if (Number.isFinite(ref)) needAdm = ref;
      // ...and it only answers at all from a square-on pose (2026-08-17, the
      // wearer's third report of the same push). Penetration is a statement
      // about where the pads meet the sidewalls; a head turned past the trust
      // ramps' start has one sidewall hallucinated, so every "penetration" it
      // reports is fiction — and the guard, being raw by design, put that
      // fiction straight on the screen. Off-square the eased channel carries
      // alone, which is the number the last square-on look measured. Callers
      // that publish no `squareOn` (the harness's hand-built seatConfigs) keep
      // the old always-on contract, so their pinned arithmetic is untouched.
      const squareOn = seatCfg.squareOn !== false;
      guard = squareOn && felt.touched > 0 && needAdm > zeta + GUARD_BAND
        ? needAdm - zeta : 0;
      // The guard's authority is capped (stage 6, found by the wearer live). It
      // exists to answer millimetre-scale true penetration; during a deep tilt
      // the view-deform's surface surge asked it for EIGHTEEN, and it launched
      // the frame forward off the face — the exact "pushed forward in a weird
      // way" complaint. Past the cap, tucking behind the drawn (feathered,
      // snapped) boundary is the lesser visual evil, and the trust-scaled
      // deform upstream keeps the honest ask inside the cap anyway. Overflow
      // is counted, not hidden.
      if (guard > GUARD_MAX) {
        if (stats !== null) stats.guardOverflow += 1;
        guard = GUARD_MAX;
      }
      applied = zeta + guard;
    } else {
      applied = needed;
    }

    const bounded = Math.min(Math.max(applied, -limit), limit);
    const sideL = felt.perSide.L;
    const sideR = felt.perSide.R;
    noseSeat = {
      /** The raw softened law at the applied placement, cm. */
      push: needed,
      /** The law the guard actually answered (2026-08-17): the carried
       * trust-admitted reference when one exists, the raw `push`
       * otherwise. */
      needAdmitted: needAdm,
      /** What was actually added to z — eased channel + raw guard, bounded. */
      easedPush: bounded,
      guard,
      /** This SESSION's cap overflows — zero for a caller that owns no stats. */
      guardOverflow: stats !== null ? stats.guardOverflow : 0,
      touched: felt.touched,
      dropped: felt.dropped,
      interference: felt.touched > 0 ? felt.soft : 0,
      interferenceMax: felt.max,
      restedAt: felt.restedAt,
      restedSide: felt.restedSide,
      perSide: {
        L: { soft: sideL.soft, count: sideL.count, dropped: sideL.dropped },
        R: { soft: sideR.soft, count: sideR.count, dropped: sideR.dropped },
        C: {
          soft: felt.perSide.C.soft,
          count: felt.perSide.C.count,
          dropped: felt.perSide.C.dropped,
        },
        /** How far the lighter pad trails the heavier one, cm. */
        gap: Number.isFinite(sideL.soft) && Number.isFinite(sideR.soft)
          ? Math.abs(sideL.soft - sideR.soft) : null,
      },
      clamped: bounded !== applied,
    };
    position.z += bounded;
  }

  // The wearer's own standoff, on top of wherever it came to rest.
  position.z += fit.offsetZ;

  return {
    position, quaternion, scale, verticalCorrection, noseSeat, seatBase,
    restHeight, restRoll: phi,
  };
}

/**
 * Where the pupils fall within the lens, 0 at the top edge and 1 at the bottom.
 *
 * The number an optician cares about. Pupils belong a little above the vertical
 * centre of the lens — around 0.4–0.5 — because a lens hung too low leaves the
 * wearer looking over the top of it, and too high crowds the brow. It is also the
 * cleanest single measure of whether adapting to the face actually helped: it
 * should land in range on *every* face, not just the average one.
 */
export function pupilHeightInLens({ model, anchors, placement }) {
  const box = model.frontBox.clone().applyMatrix4(
    new THREE.Matrix4().compose(
      placement.position,
      placement.quaternion,
      new THREE.Vector3(placement.scale, placement.scale, placement.scale),
    ),
  );

  const span = box.max.y - box.min.y;
  if (span < 1e-6) return 0.5;
  return (box.max.y - anchors.eyeLineY) / span;
}

/**
 * The pupil height as a fit VERDICT — stage 5's demotion, graft G17.
 *
 * Up to stage 5, pupil height was a *constraint*: the vertical solve put the
 * pupils at `pupilTarget` and the number above could only ever confirm it.
 * The seat equilibrium ends that — the wedge now decides where the frame
 * RESTS, the optical target only bounds the search — so this number becomes
 * an honest measurement again, and like `widthVerdict` it speaks in bands a
 * wearer would recognise. "This frame sits low on you" is information an
 * optician gives, not an error the solver hides.
 *
 * The bands: pupils belong a little above the lens's vertical centre. Below
 * 0.25 of the way down they are crowding the top edge — the frame has slid
 * DOWN the nose (the lens is low, so the eyes exit high). Above 0.65 the
 * frame is riding high and the wearer looks through the lens's lower half.
 * Between the two, an optician would not touch the pads.
 */
export const PUPIL_BANDS = { low: 0.25, high: 0.65 };

export function pupilVerdict(height) {
  let verdict = 'good';
  if (height < PUPIL_BANDS.low) verdict = 'sits low';
  else if (height > PUPIL_BANDS.high) verdict = 'sits high';
  return { height, verdict };
}

/**
 * How the frame's width compares with the wearer's — by where the arm lands.
 *
 * The two failure modes have been named correctly in this file since it was
 * written: "meaningfully wider and it slides down and the arms splay;
 * meaningfully narrower and it pinches". What was wrong for as long as they were
 * named was everything that turned them into a number.
 *
 * **What was wrong, measured.** The old test was `frameWidth / templeWidth` in
 * FACE-SPACE units, banded at 0.92 / 1.06. Three defects, and each of them fails
 * in the same direction:
 *
 *   1. *It could not see the wearer's size.* Both sides were in the canonical
 *      head's centimetres — face space is the canonical head, posed to cover
 *      whoever is in the chair — so the ratio described SHAPE and nothing else.
 *      Four of the fifteen generality subjects returned bit-identical verdicts to
 *      the mean face because their `widthRatio` was 1, whatever their actual head
 *      measured. A child with a proportionally normal face reads exactly what a
 *      large adult with a proportionally normal face reads. That is the one thing
 *      true-size mode exists to tell them apart on.
 *   2. *The bands were centred on the wrong place.* `templeWidth` is the span
 *      between landmarks 127 and 356, and those are the WIDEST vertex pair in the
 *      whole mesh, sitting at the ear plane (z = −2.0 cm, 154.9 mm apart on the
 *      canonical face — head breadth, not the optician's temple-to-temple, which
 *      industry sizing guides put at 120–140 mm for adults). Real frame fronts
 *      run 125–150 mm, so a correct frame lands at 0.81–0.97 of this ruler and
 *      the "good" band began at 0.92. Measured over the fifteen subjects and the
 *      eleven catalogue frames: **100 of 165 cells read `narrow`**, nine of the
 *      eleven frames on the mean face among them. A verdict that calls a standard
 *      adult frame narrow on an average adult sends that wearer to a larger one.
 *   3. *A width was compared without a depth.* See `findEndpiece`: the head
 *      narrows by 43 mm from the ear plane to the lens plane, so the same 140 mm
 *      means different things on a flat frame and a wrapped one, and one ratio
 *      cannot hold both.
 *
 * **What replaces it.** The arm runs from the endpiece back to the ear along the
 * side of the head, and the head widens the whole way. So there is exactly one
 * depth at which the two silhouettes cross, and that crossing IS the fit:
 *
 *   `contact` = 0   the head reaches the frame's width right at the endpiece —
 *                   the front itself is already fouling the face. NARROW.
 *   `contact` = 1   the head only reaches it at its widest point — the arm
 *                   barely grips, and past that it never touches at all. WIDE.
 *
 * The two band edges are the geometry's own and there is no third number: this
 * function has no tolerance constant in it, where the old one had two. And the
 * verdict now moves with the wearer's SIZE, because the comparison is run at the
 * frame's true manufactured width against the wearer's true head — `metricScale`
 * (the iris, this pipeline's only absolute ruler) divides the frame's face-space
 * size, since face space is the canonical head and `metricScale` is how much
 * bigger the real one is. Scaling is about `placement.position`, the point the
 * frame rests on, so a true-size frame swings about its own seat.
 *
 * `absolute` is false when no iris has been resolved. The verdict then describes
 * the frame AS DRAWN against the average head, which is a real answer to a
 * different question, and the caller is told which one it got rather than being
 * left to assume.
 *
 * `widthSource` rides out with it for the reason `models.js` gives: on nine of
 * the eleven catalogue assets `model.widthM` is a 140 mm assumption read back
 * off vertices that were scaled to it, and ±10 mm of that assumption moves the
 * ratio by ±7% — more than the whole of what this function is trying to resolve.
 */
export function widthVerdict({ model, anchors, placement, face = null, fit = null }) {
  const frameWidth = model.widthM * placement.scale;
  // The absolute ruler. 1 means "no iris yet": the comparison then runs in
  // canonical units, which is what this function did unconditionally before.
  const k = anchors.metricScale ?? 1;
  const absolute = anchors.metricScale != null;

  // How the drawn size relates to the size of the thing being described.
  //
  // In `physical` the drawn frame is the product's own metres pushed into
  // canonical centimetres, so the product occupies `1/k` of what is drawn on a
  // head `k` times the average — that division is the whole of the scale fix.
  // In `proportional` there is no product to describe: the frame has been
  // rescaled to span this face, so the drawn frame IS the frame being asked
  // about and the factor is 1. Getting this backwards would make the fit-to-face
  // mode report a size mismatch it removed by construction.
  const proportional = fit?.mode === 'proportional';
  const trueScale = proportional ? 1 : 1 / k;

  // Real millimetres on both sides, for the number a person reads.
  const faceWidthMm = anchors.templeWidth * k * 10;
  const frameWidthMm = frameWidth * trueScale * k * 10;
  const ratio = faceWidthMm > 0 ? frameWidthMm / faceWidthMm : 1;

  const contact = contactFraction({ model, anchors, placement, face, trueScale });

  let verdict = 'good';
  if (contact === null) verdict = 'wide';
  else if (contact <= 0) verdict = 'narrow';
  else if (contact >= 1) verdict = 'wide';

  return {
    ratio,
    verdict,
    contact,
    absolute,
    widthSource: model.widthSource ?? null,
    frameWidthMm,
    faceWidthMm,
    // The old face-space figures, unchanged in meaning, because two consumers
    // and a pinned harness read them.
    frameWidthCm: frameWidth,
    faceWidthCm: anchors.templeWidth,
  };
}

/**
 * Where along the arm's run the head first reaches the frame's half-width.
 *
 * 0 at the endpiece, 1 at the head's widest point, `null` when the head never
 * gets there — which is the "wide" failure and has to be distinguishable from a
 * contact at exactly 1.
 *
 * The wearer's silhouette is the canonical mesh's, stretched laterally by
 * `anchors.widthRatio` — the same model `estimateEars` uses to put the rest
 * points on the face's own outline, and the only per-wearer shape the pipeline
 * measures. Depths are the canonical head's, because nothing here measures a
 * wearer's fore-aft profile; that is stated rather than hidden, and it is why
 * this reports a contact POSITION rather than a millimetre of pressure.
 *
 * Restricted to the height band the frame front actually occupies, so a cheek or
 * a jaw cannot answer a question about a temple.
 */
function contactFraction({ model, anchors, placement, face, trueScale }) {
  if (!face) return null;

  const scale = placement.scale;
  const half = (model.widthM * scale * trueScale) / 2;
  if (!(half > 0)) return null;

  _placed.compose(placement.position, placement.quaternion, _scaleTriple.setScalar(scale));
  // The frame front's height band, and the endpiece's depth, both carried into
  // face space through the placement — then rescaled about the seat, which puts
  // the frame at the size being asked about on this wearer's own head.
  const band = model.frontBox.clone().applyMatrix4(_placed);
  const trueAbout = (v) => v.sub(placement.position).multiplyScalar(trueScale)
    .add(placement.position);
  trueAbout(band.min);
  trueAbout(band.max);
  const endpieceZ = trueAbout(
    _endpiece.set(0, model.endpiece.y, model.endpiece.z).applyMatrix4(_placed),
  ).z;

  // The head's own silhouette in that band, as the forward-monotone envelope
  // `h(z) = max{ |x_i| : z_i >= z }` — a head can only narrow going forward, so
  // the half-width at a depth is the widest thing at or in front of it. That
  // fills the gaps between the mesh's handful of silhouette vertices without
  // inventing a taper it does not have.
  //
  // Never materialised, because this runs on every frame of the live loop and a
  // sorted array of it would be an allocation per frame for nothing. The one
  // quantity wanted is the deepest-forward depth at which the envelope still
  // reaches the frame's half-width, and by the definition above that is simply
  // the largest `z` among vertices at least that wide. Four reductions over 468
  // vertices, no allocation, no sort.
  const wr = anchors.widthRatio ?? 1;
  let hMax = -Infinity;
  // Where the run ENDS: the frontmost depth at which the head reaches its widest.
  // Not the backmost vertex in the band, which is a different point and the
  // difference is not cosmetic — with the run measured to the back of the band, a
  // frame exactly as wide as the head reports a contact part-way along instead of
  // at the end, so the wide edge stops being reachable by equality. The synthetic
  // check that caught it is fit-to-face at `widthRatio` 1.0, which asks for
  // exactly that frame by construction.
  let zWidest = -Infinity;
  let zC0 = -Infinity;
  let inBand = 0;
  for (let i = 0; i < face.vertexCount; i++) {
    const y = face.positions[i * 3 + 1];
    if (y < band.min.y || y > band.max.y) continue;
    inBand++;
    const z = face.positions[i * 3 + 2];
    const w = Math.abs(face.positions[i * 3]) * wr;
    if (w > hMax) { hMax = w; zWidest = z; } else if (w === hMax && z > zWidest) zWidest = z;
    if (w >= half && z > zC0) zC0 = z;
  }
  if (inBand < 4) return null;
  // The frame is wider than the head ever gets: the arm cannot touch it.
  if (zC0 === -Infinity) return null;
  const span = endpieceZ - zWidest;
  // The frame carries its widest line behind the head's own widest point, so
  // there is no run to be part-way along: it can only ever touch at the back.
  if (!(span > 1e-9)) return 1;
  // The head has already caught up at the endpiece itself — the front is
  // fouling the face, which is the narrow edge.
  if (zC0 >= endpieceZ) return 0;

  // Where between this vertex and the next one forward the envelope crosses.
  // `hAt` is the envelope at the crossing vertex and `hF` the step in front of
  // it; `hF < half` holds by construction, since anything that wide sits at or
  // behind `zC0`.
  let hAt = 0;
  let zF = Infinity;
  for (let i = 0; i < face.vertexCount; i++) {
    const y = face.positions[i * 3 + 1];
    if (y < band.min.y || y > band.max.y) continue;
    const z = face.positions[i * 3 + 2];
    if (z >= zC0) {
      const w = Math.abs(face.positions[i * 3]) * wr;
      if (w > hAt) hAt = w;
      if (z > zC0 && z < zF) zF = z;
    }
  }
  let zC = zC0;
  if (zF < Infinity) {
    let hF = 0;
    for (let i = 0; i < face.vertexCount; i++) {
      const y = face.positions[i * 3 + 1];
      if (y < band.min.y || y > band.max.y) continue;
      if (face.positions[i * 3 + 2] < zF) continue;
      const w = Math.abs(face.positions[i * 3]) * wr;
      if (w > hF) hF = w;
    }
    if (hAt > hF) zC = zC0 + (zF - zC0) * ((hAt - half) / (hAt - hF));
  }
  return Math.min(Math.max((endpieceZ - zC) / span, 0), 1);
}

const _placed = new THREE.Matrix4();
const _scaleTriple = new THREE.Vector3();
const _endpiece = new THREE.Vector3();

const _projected = new THREE.Vector3();

/**
 * How wide this face is compared with the average one. 1.0 is average.
 *
 * Worth being precise about why it is a ratio and not millimetres. The facial
 * transformation matrix turns out to be *rigid* — its uniform scale is always
 * exactly 1. MediaPipe does not resize the canonical head to match the face; it
 * keeps it at nominal size and moves it nearer or further to explain how large the
 * face appears. That is the monocular scale/depth ambiguity, and it means absolute
 * head size genuinely is not in the data. A "head width in mm" read off this matrix
 * would be the canonical head's own width, dressed up as a measurement.
 *
 * What *is* recoverable is the residual: the canonical head is posed to fit this
 * face, so any leftover difference between where its temples reproject and where
 * the observed temples actually are is a real shape difference. Comparing those two
 * distances in image space gives an honest relative width — enough to drive frame
 * width selection, which is a comparison anyway.
 *
 * Only meaningful near head-on; foreshortening eats the temple span on a turn, so
 * callers should gate on yaw.
 */
export function measureFaceWidthRatio({ face, camera, head, landmarks, width, height }) {
  const project = (index) => {
    const p = face.point(index);
    _projected.set(p[0], p[1], p[2]).applyMatrix4(head.matrixWorld).project(camera);
    return [(_projected.x * 0.5 + 0.5) * width, (1 - (_projected.y * 0.5 + 0.5)) * height];
  };

  const [rx, ry] = project(LM.TEMPLE_R);
  const [lx, ly] = project(LM.TEMPLE_L);
  const canonicalPx = Math.hypot(lx - rx, ly - ry);
  if (canonicalPx < 1e-6) return 1;

  const right = landmarks[LM.TEMPLE_R];
  const left = landmarks[LM.TEMPLE_L];
  const observedPx = Math.hypot((left.x - right.x) * width, (left.y - right.y) * height);

  return observedPx / canonicalPx;
}
