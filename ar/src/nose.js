/**
 * The nose, treated as a surface the frame rests on rather than a point it hangs from.
 *
 * Everything else in the fit anchors the frame to a *landmark*: put the model's pad
 * contact on vertex 6, slide it up the bridge until the pupils sit right. That gets
 * the frame to the correct place on the face and leaves it at the wrong distance
 * from it, because a landmark is a point and a nose is a wedge.
 *
 * The numbers say how wrong. On the canonical head, at the height of the bridge
 * landmark, the skin 7 mm off the centreline sits **5.5 mm further back** than the
 * skin on the centreline — the nose is falling away to either side that fast. So a
 * frame with pads 14 mm apart, pinned by a centreline contact point to the bridge
 * landmark, is 5.5 mm wrong at both of them; and which *way* it is wrong depends on
 * how the asset happens to be modelled, so across the four frames here it ranges
 * from 4.2 mm clear of the face to 1.7 mm inside it. Nothing in the pipeline
 * noticed: the pose is right, the height is right, the width is right, and the
 * frame is either hovering or buried.
 *
 * So this module builds the face's front surface once, as a depth field, and
 * `seat()` finds the smallest push along the view axis that puts the frame's
 * rearmost bridge geometry *on* that surface rather than through it. That push is
 * vertex distance — the optician's term for how far the lenses stand off the eye —
 * and solving it against the surface is what makes the contact real.
 *
 * The depth field is the canonical head's, warped to this face: shifted so its
 * bridge lands on the measured bridge, and stretched sideways by the measured nose
 * width. That last one is the individual difference that matters most here. A nose
 * 15% wider moves the skin at the pad line 2 mm forward, and 2 mm is the difference
 * between a frame resting on a nose and a frame sunk into it.
 */

import * as THREE from 'three';

const placed = new THREE.Vector3();

/**
 * The patch of face the bridge of a frame can reach, in centimetres.
 *
 * Deliberately small. Everything outside it — cheeks, brow, the temples' run past
 * the eyes — either cannot touch the bridge region or is handled by the occluder, so
 * a grid over the whole head would be two and a half times the cells for no extra
 * contact. Its real value is not the memory: a lookup that falls outside the patch
 * returns NaN and drops that sample, which is what keeps the temple arms — which are
 * *meant* to be behind the face — out of a solve about the nose.
 */
export const WINDOW = { minX: -4.5, maxX: 4.5, minY: -5.0, maxY: 7.0 };
/** 1 mm cells. The nose's sidewall falls ~8 mm per cm of travel, so this resolves it. */
const CELL = 0.1;

const EMPTY = -Infinity;

/**
 * Rasterises the canonical mesh into a front-facing depth field.
 *
 * Depth by triangle rasterisation rather than by binning vertices: the mesh puts
 * only a few hundred vertices anywhere near the nose and they are spaced 3–7 mm
 * apart, so binning them samples the surface at exactly the resolution the contact
 * solve is trying to beat. Rasterising interpolates across each triangle instead, so
 * the field has the mesh's real shape at whatever cell size is asked for.
 *
 * Keeping the *maximum* z per cell picks the front surface, which is the only one a
 * frame can touch — the nostrils and the inner eyelids fold back behind it and are
 * correctly ignored.
 */
export function buildFaceSurface({ positions, indices, origin, triangleCount = indices.length / 3 }) {
  const columns = Math.round((WINDOW.maxX - WINDOW.minX) / CELL) + 1;
  const rows = Math.round((WINDOW.maxY - WINDOW.minY) / CELL) + 1;
  const depth = new Float32Array(columns * rows);

  const field = {
    columns,
    rows,
    cell: CELL,
    /** Cells the mesh never covered, after filling. Zero on the canonical head. */
    holes: 0,
    /** Where the field's own bridge sits, so a measured bridge can be lined up with it. */
    origin: [origin[0], origin[1], origin[2]],

    /**
     * Re-rasterises from moved vertices, into the same grid.
     *
     * This is what makes the occluder's surface and the seat's surface the *same*
     * surface rather than two models of one. `occluder.js` deforms the face mesh onto
     * the wearer and calls this with the result, so `seat()` is solving against the
     * triangles the depth buffer is about to be written from — not, as it used to be,
     * against a separately-warped copy of the average nose that nothing ever drew.
     *
     * In place: this runs several times a second while a face moves, and a fresh
     * 11k-cell allocation per rebuild is garbage collected during tracking.
     */
    rebuild(next, nextIndices, nextOrigin, count = nextIndices.length / 3) {
      depth.fill(EMPTY);
      for (let t = 0; t < count * 3; t += 3) {
        rasterise(depth, columns, rows, next, nextIndices[t], nextIndices[t + 1], nextIndices[t + 2]);
      }
      field.holes = fillHoles(depth, columns, rows);
      field.origin[0] = nextOrigin[0];
      field.origin[1] = nextOrigin[1];
      field.origin[2] = nextOrigin[2];
      return field.holes;
    },

    /**
     * The front surface at (x, y) in canonical face space, or NaN off the field.
     *
     * NaN rather than a clamped edge value on purpose: a contact point that has left
     * the modelled patch should be dropped from the solve, not seated against a
     * fabricated depth.
     */
    depthAt(x, y) {
      const u = (x - WINDOW.minX) / CELL;
      const v = (y - WINDOW.minY) / CELL;
      const i = Math.floor(u);
      const j = Math.floor(v);
      if (i < 0 || j < 0 || i >= columns - 1 || j >= rows - 1) return NaN;

      const a = depth[j * columns + i];
      const b = depth[j * columns + i + 1];
      const c = depth[(j + 1) * columns + i];
      const d = depth[(j + 1) * columns + i + 1];
      if (a === EMPTY || b === EMPTY || c === EMPTY || d === EMPTY) return NaN;

      const fx = u - i;
      const fy = v - j;
      return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy;
    },
  };

  field.rebuild(positions, indices, origin, triangleCount);
  return field;
}

function rasterise(depth, columns, rows, positions, ia, ib, ic) {
  const ax = positions[ia * 3]; const ay = positions[ia * 3 + 1]; const az = positions[ia * 3 + 2];
  const bx = positions[ib * 3]; const by = positions[ib * 3 + 1]; const bz = positions[ib * 3 + 2];
  const cx = positions[ic * 3]; const cy = positions[ic * 3 + 1]; const cz = positions[ic * 3 + 2];

  const det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
  // A triangle edge-on to the view covers no cells and would divide by zero.
  if (Math.abs(det) < 1e-12) return;

  const i0 = Math.max(0, Math.ceil((Math.min(ax, bx, cx) - WINDOW.minX) / CELL));
  const i1 = Math.min(columns - 1, Math.floor((Math.max(ax, bx, cx) - WINDOW.minX) / CELL));
  const j0 = Math.max(0, Math.ceil((Math.min(ay, by, cy) - WINDOW.minY) / CELL));
  const j1 = Math.min(rows - 1, Math.floor((Math.max(ay, by, cy) - WINDOW.minY) / CELL));

  for (let j = j0; j <= j1; j++) {
    const y = WINDOW.minY + j * CELL;
    for (let i = i0; i <= i1; i++) {
      const x = WINDOW.minX + i * CELL;
      const l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / det;
      if (l1 < 0) continue;
      const l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / det;
      if (l2 < 0) continue;
      const l3 = 1 - l1 - l2;
      if (l3 < 0) continue;

      const z = l1 * az + l2 * bz + l3 * cz;
      const at = j * columns + i;
      if (z > depth[at]) depth[at] = z;
    }
  }
}

/**
 * Fills cells no triangle covered, from their neighbours.
 *
 * The mesh's triangles are not a perfect tiling at this resolution — a cell centre
 * can fall in the sliver between two of them — and an unfilled cell in the middle of
 * the nose would put a NaN into the contact solve and silently drop a pad from it.
 * Returns how many were left, which should be zero and is asserted to be.
 */
function fillHoles(depth, columns, rows) {
  for (let pass = 0; pass < 4; pass++) {
    let filled = 0;
    for (let j = 1; j < rows - 1; j++) {
      for (let i = 1; i < columns - 1; i++) {
        const at = j * columns + i;
        if (depth[at] !== EMPTY) continue;
        let sum = 0;
        let n = 0;
        for (const neighbour of [at - 1, at + 1, at - columns, at + columns]) {
          if (depth[neighbour] !== EMPTY) { sum += depth[neighbour]; n++; }
        }
        if (n >= 2) { depth[at] = sum / n; filled++; }
      }
    }
    if (filled === 0) break;
  }

  let holes = 0;
  for (let j = 1; j < rows - 1; j++) {
    for (let i = 1; i < columns - 1; i++) if (depth[j * columns + i] === EMPTY) holes++;
  }
  return holes;
}

/**
 * How far a pad may press into the skin before it counts as through it, in cm.
 *
 * A nose pad is a plate on soft tissue, not a point on a rigid surface — it sinks in
 * a little, and the skin around it lifts. Seated at exact tangency the frame reads as
 * hovering, because every contact is a single-point one and no two surfaces ever
 * quite meet on screen. Half a millimetre of interference is what a real pad does and
 * what makes the contact look like one.
 */
export const PAD_SINK = 0.05;

/**
 * The soft-max temperature for `sideInterference`, in cm.
 *
 * The old seat reduced its contacts with a hard argmax, and an argmax is a step
 * generator by construction: two contacts within noise of each other trade the
 * win frame to frame, and every trade moves the push by their full separation —
 * measured 0.5–2 mm steps on the diag captures (seat jitter cause). A log-sum-exp
 * at temperature τ makes the reduction differentiable: each contact's influence
 * on the answer is its softmax share (< 1 always), so a bin flip is a continuous
 * slide, not a step.
 *
 * 0.05 cm — set equal to PAD_SINK, so contacts within one sink-depth of the
 * winner share the load, which is what a pad on compliant skin does anyway.
 * (Spec amendment: physics-first's 0.05 wins over stability-first's 0.03.)
 */
export const SOFTMAX_TAU = 0.05;

/**
 * FIELD AMENDMENT, stage 5 (the stage-1/3/4 precedent: constants must survive
 * their own arithmetic): the spec's G6 — a soft-max STANDOFF with
 * PAD_SINK′ = PAD_SINK − τ/2 compensating its bias — is retired by
 * measurement, and the standoff level stays on the argmax law.
 *
 * The τ/2 compensation priced the log-sum-exp's over-topping for ~1.6
 * contacts tied at the winner (τ·ln n = τ/2 ⇒ n ≈ 1.6). The catalogue's real
 * contact sets are 800–1150 bins whose near-winner mass is measured in the
 * HUNDREDS — the pad of a glasses bridge is a smooth patch, not a point — and
 * the measured bias `soft − max` on the seated catalogue is 1.10–1.69 mm per
 * asset (probe, canonical face), four to seven times the compensation. A
 * soft-anchored standoff would therefore stand the deepest contact
 * ~0.9–1.4 mm CLEAR of the drawn skin: the frame visibly hovering, the exact
 * defect PAD_SINK exists to prevent, dressed up as a smoothness win.
 *
 * So the division of labour is: the LEVEL (the applied standoff, the raw
 * guard) keeps `max − PAD_SINK` — today's law, bit for bit, which is also
 * what makes the zero-confidence fallback literally today's placement — and
 * the soft reduction owns every COMPARISON: per-side load, bearing deficit,
 * asymmetry, the roll lever. Comparisons are where the argmax's flip
 * discontinuities actually generated steps; the level's own kinks are
 * absorbed by the stage-5 plumbing (event-cadence solves, SEAT_TAU ease,
 * effect deadband) that the design already relies on. The harness pins the
 * bit-parity of the softened kernel's max with `seat()`'s.
 */

/**
 * How much penetration past the eased standoff the per-frame guard tolerates
 * before acting, in cm (graft G2).
 *
 * Just above the depth field's own cell-interpolation error (1 mm cells on an
 * ~8 mm/cm sidewall interpolate to well under 0.3 mm), so the raw guard fires on
 * real penetration — a turn that swings the pads into the nose faster than the
 * eased channel follows — and never on the field's own texture. Inward motion is
 * always eased; outward correction past this band is applied raw, because
 * penetration must not wait for a time constant.
 */
export const GUARD_BAND = 0.03;

/**
 * The most standoff the raw guard may add in one frame, cm.
 *
 * The guard answers true millimetre-scale penetration; a surging surface once
 * asked it for 1.8 cm during a deep tilt (stage 6) and it obliged, launching
 * the frame forward — worse than the tuck it was preventing. 0.4 cm covers
 * every honest ask measured (the harness's noise runs peak under 0.1) with
 * margin, and past it the feathered occlusion boundary absorbs the overlap.
 */
export const GUARD_MAX = 0.4;

/**
 * The push along +Z that puts the frame's bridge on the nose instead of through it.
 *
 * `contacts` are the model's rearmost bridge samples already carried into face space.
 * Returns centimetres: positive moves the frame away from the face.
 *
 * Along +Z specifically, and not along the surface normal, because +Z is the axis the
 * optics do not care about. Vertex distance is the one placement variable that
 * changes nothing about where the pupils fall in the lens, so seating along it cannot
 * fight the height solve that ran before it — the two corrections compose exactly
 * once rather than chasing each other.
 *
 * The push is the *worst* interference over every contact, so the frame comes to rest
 * on whichever part of its bridge reaches the face first — a saddle on the ridge, a
 * pad on the sidewall, the inner edge of a rim. That is the same answer a real frame
 * settles into and it needs no knowledge of which part of the model is which.
 *
 * SINCE STAGE 5 this argmax law is the REFERENCE, not the production path. The
 * placement seats through `sideInterference` — the identical bridge-relative
 * kernel with the argmax softened to a log-sum-exp — and the equilibrium solver
 * in `seat-equilibrium.js` decides the height it happens at. This function
 * survives for exactly one consumer: the harness's G6 recalibration check,
 * which holds the soft law within 0.3 mm of this one on the canonical face, so
 * the softening can never quietly drift into a re-seat. Nothing in `src/`
 * calls it any more.
 */
export function seat({ surface, contacts, toFace, anchors, limit = 1.2 }) {
  const result = { push: 0, touched: 0, interference: 0, restedAt: null, clamped: false };
  if (!surface || !contacts?.length) return result;

  // Bridge to bridge, and nothing else.
  //
  // There used to be a sideways division by `noseWidthRatio` here, standing in for a
  // nose this field did not have the shape of. It is gone, and its removal is the
  // point of the whole occluder rewrite rather than a tidy-up: the field is now
  // rasterised from the *deformed* face mesh, which has this wearer's nose width
  // already in its vertices, and that same mesh is what writes the depth buffer.
  //
  // The warp was not merely redundant, it was the defect. It moved the surface the
  // frame was seated against without moving the surface that was drawn, so on a nose
  // 15% narrower than average the two disagreed by up to 3.7 mm and the frame was
  // seated that far inside the head the depth test was about to write. Measured over
  // the catalogue, that swallowed a patch of frame up to 50 x 27 mm.
  //
  // This shift is the one remaining transform and it is shared exactly: `occluder.js`
  // translates the drawn mesh by the same `bridge - origin`, so the field and the
  // triangles are the same surface at the same place, every frame, by construction.
  const shiftZ = anchors.bridge.z - surface.origin[2];

  let worst = -Infinity;

  for (const point of contacts) {
    placed.copy(point).applyMatrix4(toFace);

    const x = surface.origin[0] + (placed.x - anchors.bridge.x);
    const y = surface.origin[1] + (placed.y - anchors.bridge.y);

    const skin = surface.depthAt(x, y);
    if (Number.isNaN(skin)) continue;

    result.touched++;
    const interference = (skin + shiftZ) - placed.z;
    if (interference > worst) {
      worst = interference;
      // Where on the frame it came to rest — the one sample the whole thing hangs
      // on. Reported because a seat solved off the wrong bit of geometry is
      // otherwise invisible: the number is plausible and the frame is wrong.
      result.restedAt = { x: placed.x, y: placed.y, z: placed.z };
    }
  }

  if (result.touched === 0) return result;

  result.interference = worst;
  const push = worst - PAD_SINK;
  result.push = Math.min(Math.max(push, -limit), limit);
  result.clamped = result.push !== push;
  return result;
}

/**
 * The seat's contact reduction, softened and split by side — the production
 * kernel since stage 5.
 *
 * The *query* is `seat()`'s, verbatim, and that is a property to defend rather
 * than an implementation detail: every contact is looked up bridge-relative —
 * `origin + (placed − bridge)` in x/y, `bridge.z − origin[2]` shifting the
 * answer in z — so an error in the measured bridge moves the query point and
 * the compared depth by the same amount and cancels exactly. The diagnosis
 * verified that cancellation algebraically; every solver upstream of this
 * function inherits it or silently loses it, which is why there is ONE kernel
 * and the sweep, the guard and the readouts all call it.
 *
 * Two things are new. The reduction is a log-sum-exp at `SOFTMAX_TAU` instead
 * of a max (see the constant for why), computed per SIDE — the load-time L/C/R
 * split of the contact set — as well as overall. The per-side numbers are what
 * the equilibrium solve stands on: a wedge bears when BOTH sidewall sets carry
 * load, and a single scalar max cannot say whether one pad is floating. The
 * overall value is not a fourth measurement: a log-sum-exp over the union IS
 * the log-sum-exp of the per-side reductions, so `soft` and `perSide` can
 * never disagree.
 *
 * `offset` displaces every contact in face space before the lookup (the height
 * sweep rides this — one transformed pass per candidate height), and `rollZ`
 * rotates them about the face-space Z axis through `(pivotX, pivotY)` (the G5
 * pad-balance evaluation). Both default to identity.
 *
 * Returns interferences in cm; sides with no valid contact report -Infinity,
 * which the log-sum-exp treats as absent (exp → 0) — the honest value, since a
 * missing side genuinely contributes nothing to where the frame rests.
 */
export function sideInterference({
  surface, contacts, sides = null, toFace, anchors,
  offset = null, rollZ = 0, pivotX = 0, pivotY = 0, tau = SOFTMAX_TAU,
}) {
  const result = {
    soft: NaN,
    max: -Infinity,
    touched: 0,
    dropped: 0,
    restedAt: null,
    restedSide: null,
    perSide: {
      L: { soft: -Infinity, max: -Infinity, count: 0, dropped: 0 },
      R: { soft: -Infinity, max: -Infinity, count: 0, dropped: 0 },
      C: { soft: -Infinity, max: -Infinity, count: 0, dropped: 0 },
    },
  };
  if (!surface || !contacts?.length) return result;

  const shiftZ = anchors.bridge.z - surface.origin[2];
  const ox = offset ? offset.x : 0;
  const oy = offset ? offset.y : 0;
  const oz = offset ? offset.z : 0;
  const cos = rollZ !== 0 ? Math.cos(rollZ) : 1;
  const sin = rollZ !== 0 ? Math.sin(rollZ) : 0;

  // One running log-sum-exp per side, kept stable by rescaling against the
  // running max — the textbook trick, needed here because interferences sit
  // within a few τ of each other by design and the naive sum would still be
  // fine, but a NaN-free -Infinity start would not.
  const accumulate = (side, interference) => {
    if (interference > side.max) {
      side.sum = (side.sum ?? 0) * Math.exp((side.max - interference) / tau) + 1;
      side.max = interference;
    } else {
      side.sum = (side.sum ?? 0) + Math.exp((interference - side.max) / tau);
    }
    side.count++;
  };

  const measure = (point, side, sideName) => {
    placed.copy(point).applyMatrix4(toFace);
    if (rollZ !== 0) {
      const dx = placed.x - pivotX;
      const dy = placed.y - pivotY;
      placed.x = pivotX + cos * dx - sin * dy;
      placed.y = pivotY + sin * dx + cos * dy;
    }
    placed.x += ox; placed.y += oy; placed.z += oz;

    const x = surface.origin[0] + (placed.x - anchors.bridge.x);
    const y = surface.origin[1] + (placed.y - anchors.bridge.y);
    const skin = surface.depthAt(x, y);
    if (Number.isNaN(skin)) { side.dropped++; result.dropped++; return; }

    result.touched++;
    const interference = (skin + shiftZ) - placed.z;
    accumulate(side, interference);
    if (interference > result.max) {
      result.max = interference;
      result.restedAt = { x: placed.x, y: placed.y, z: placed.z };
      result.restedSide = sideName;
    }
  };

  if (sides) {
    for (const name of ['L', 'R', 'C']) {
      const side = result.perSide[name];
      const list = sides[name];
      if (!list) continue;
      for (const index of list) measure(contacts[index], side, name);
    }
  } else {
    // No split available — everything is the centre set, which reduces this
    // function to a softened `seat()` and keeps single-set callers honest.
    for (const point of contacts) measure(point, result.perSide.C, 'C');
  }

  // Close each side's log-sum-exp, then combine: LSE(union) = LSE(per-side
  // LSEs), so the overall reduction is exact, not an approximation of one.
  let allMax = -Infinity;
  let allSum = 0;
  for (const name of ['L', 'R', 'C']) {
    const side = result.perSide[name];
    if (side.count > 0) side.soft = side.max + tau * Math.log(side.sum);
    delete side.sum;
    if (side.count === 0) continue;
    if (side.soft > allMax) {
      allSum = allSum * Math.exp((allMax - side.soft) / tau) + 1;
      allMax = side.soft;
    } else {
      allSum += Math.exp((side.soft - allMax) / tau);
    }
  }
  // Combining per-side LSEs as plain values would double-soften; the identity
  // needs the union of the raw exponentials. Per-side soft = max + τ·ln(sum),
  // so exp(soft/τ) = exp(max/τ)·sum — the union's total mass is recovered
  // exactly by summing exp((soft − allMax)/τ), which is what the loop above
  // does. `soft` is therefore the LSE over every touched contact, bit-honest.
  result.soft = result.touched > 0 ? allMax + tau * Math.log(allSum) : NaN;
  return result;
}

/**
 * The surface normal at (x, y) in field coordinates, by central differences
 * over ±1 cell. NaN-propagating: any tap off the patch voids the answer.
 *
 * A diagnostic query, not a solver input: the seat classifies its rested
 * contact as ridge (|n_x| small — the frame sits on top of the nose) or
 * sidewall (|n_x| > 0.5 — a pad bearing on the wedge's flank), which is the
 * difference between a saddle fit and a pad fit, visible in `__ar.seat`.
 */
export function normalAt(surface, x, y) {
  const h = CELL;
  const xp = surface.depthAt(x + h, y);
  const xm = surface.depthAt(x - h, y);
  const yp = surface.depthAt(x, y + h);
  const ym = surface.depthAt(x, y - h);
  if (Number.isNaN(xp) || Number.isNaN(xm) || Number.isNaN(yp) || Number.isNaN(ym)) return null;
  const nx = -(xp - xm) / (2 * h);
  const ny = -(yp - ym) / (2 * h);
  const length = Math.hypot(nx, ny, 1);
  return { x: nx / length, y: ny / length, z: 1 / length };
}

/**
 * The nose's width at height y, at depth zRef — the wedge query, in cm.
 *
 * Marches outward from the centreline in cell steps on each side until the
 * skin first drops below `zRef`, interpolates each crossing, and returns the
 * span between them. NaN when either side never crosses inside the window or
 * the march hits an unmodelled cell — a width the field cannot actually
 * testify to is not returned as one it can.
 *
 * DIAGNOSTIC-ONLY, by the spec's verdict: the production seat solves bearing
 * directly (`sideInterference` against the frame's own pad geometry) and never
 * DECIDES on a width. Two readers, neither load-bearing: the harness asserts
 * the resting-height LAW — wider wedge ⇒ the same frame rests higher and
 * further out — against the same field the solve used, from the outside; and
 * `solveRestConfiguration` reports `widthAtRestCm` through `restWidth` so the
 * live readout can say what wedge width the rest landed on. Nothing in the
 * solve's answer depends on either.
 */
export function widthAt(surface, y, zRef) {
  const crossing = (direction) => {
    let previous = surface.depthAt(0, y);
    if (Number.isNaN(previous)) return NaN;
    for (let x = CELL; x <= WINDOW.maxX; x += CELL) {
      const depth = surface.depthAt(direction * x, y);
      if (Number.isNaN(depth)) return NaN;
      if (depth < zRef) {
        // Linear interpolation between the bracketing cells.
        const t = previous === depth ? 0 : (previous - zRef) / (previous - depth);
        return x - CELL + t * CELL;
      }
      previous = depth;
    }
    return NaN;
  };

  const right = crossing(-1);
  const left = crossing(1);
  return right + left;
}
