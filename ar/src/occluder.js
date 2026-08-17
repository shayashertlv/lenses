/**
 * The head that hides the glasses — deformed onto the wearer, and shared with the seat.
 *
 * What was here before was the *average* head: `buildHeadShell` posed rigidly by
 * MediaPipe's transform, with a single sideways stretch applied outboard of x=3 cm.
 * Everything about that is defensible except one consequence, which is that the nose
 * — every part of it a frame can touch — was drawn at canonical width, canonical
 * height, canonical protrusion and canonical bridge position, on every face, forever.
 * Meanwhile the frame itself was placed on `anchors.bridge`, re-measured from the
 * observed landmarks every single frame. The glasses tracked the wearer. The nose that
 * occluded them did not.
 *
 * Worse, `seat()` was solving against a *third* surface: the canonical depth field
 * warped by `noseWidthRatio` and shifted onto the measured bridge. That warped surface
 * is a decent model of a real nose and nothing ever drew it. So the pipeline placed the
 * frame relative to a nose that did not exist on screen and then depth-tested it
 * against one that did. Measured over the ten frames in the catalogue, against the
 * shipped `solvePlacement`:
 *
 *   - on a *perfectly average* face, perfectly measured, every frame already lost a
 *     15-28 mm by 3-7 mm patch of its bridge, because `PAD_SINK` deliberately drives
 *     the rearmost geometry 0.5 mm into the surface the occluder then drew;
 *   - with the bridge landmark's own documented 1.5 mm bias, 16-30 by 9-16 mm;
 *   - on a nose 10% narrower, up to 42 by 23 mm;
 *   - with both, at 20 degrees of yaw, up to **50 by 27 mm and 6.1 mm deep** — the
 *     whole bridge and the inner third of both lenses.
 *
 * This module removes the ability to express that disagreement rather than correcting
 * it. There is one surface. `surfaceOf()` hands the seat the depth field rasterised
 * from the very triangles this module is about to write into the depth buffer, so the
 * two cannot differ by construction — not by convention, not by a constant kept in
 * step, but because there is only one array of vertices.
 *
 * Three things then follow, and each is worth stating on its own:
 *
 * **This is not a bust of the wearer's head, and must not try to be.** The deformation
 * follows the observed landmarks at *every* pose, which makes the shape slightly
 * view-dependent — it settles differently at 40 degrees of turn than head-on. That reads
 * like a flaw and is the opposite of one. The recovery borrows depth, so a head-on
 * measurement is the true shape plus an error almost entirely in z; head-on that error
 * projects to nothing, and at 40 degrees it projects into x as `sin(yaw)` — five
 * millimetres of invisible depth error becoming fourteen screen pixels of visible
 * sideways error. Re-measuring at the current pose absorbs it. What this mesh owes the
 * viewer is covering the face *in the image*, not being correct in a coordinate system
 * nobody looks at.
 *
 * What it must not do is believe a landmark it cannot see, so each vertex updates in
 * proportion to how squarely it faces the lens and holds its last good reading once it
 * turns away. See `FACING_TRUST`. Position is separate again: the mesh is translated
 * every frame so its bridge lands exactly on `anchors.bridge`, which is what keeps it
 * locked to the frame that is pinned there.
 *
 * **The drawn surface is not the skin surface.** `PAD_SINK` is a soft-tissue model
 * being fed to a binary depth test, and those two have no common language — a pad that
 * presses half a millimetre into skin is correct physics and an instant hole in the
 * frame. So the rendered mesh is relieved inward along its own normals, far enough that
 * a properly seated pad is in front of it. See `OCCLUDER_RELIEF`.
 *
 * **Nothing here is exact, so nothing here draws a hard edge.** The residue after all
 * of the above is about a millimetre, and a millimetre rendered as a binary depth test
 * is a hard edge in the wrong place that flickers as the measurement jitters. The mesh
 * is rendered a second time into a depth texture that `occlusion-mask.js` fades the
 * frame against. That is the one capability the old depth-only occluder had no way to
 * express: "probably behind".
 */

import * as THREE from 'three';

import {
  buildHeadShell, buildHeadProfile, buildPinnaGeometry, pinnaPlacement, reloftSkull,
} from './head.js';
import { buildFaceSurface, PAD_SINK, WINDOW } from './nose.js';
import { carryLandmarks } from './anchors.js';
import { LM } from './canonical-face.js';
import { planSubdivision } from './subdivide.js';

/**
 * How many times the face mesh is subdivided before it is used for anything.
 *
 * Two, and the number comes from a measurement rather than a preference. MediaPipe's
 * mesh puts 7.3 mm triangles over the nose — the strip where a bridge and the far lens's
 * inner rim cross skin — with the longest edges at 16.5 mm. On a phone at arm's length
 * that is nine screen pixels. On the close-up captures this was reported from, where a
 * face fills the frame, it is **37 to 53 pixels, worst edges past 120**, and the
 * occlusion boundary is visibly a polygon.
 *
 * Each level halves the edge: 7.3 mm becomes 3.6 and then 1.8, which is 13 px at that
 * range — under the feather's own width, which is the point at which the boundary stops
 * reading as straight segments. A third level would cost four times as much to buy
 * something already below the softening.
 */
const SUBDIVISION_LEVELS = 2;

/**
 * The layer the occluder is rendered on for its own depth pass.
 *
 * It is on layer 0 as well, so it still writes depth in the main pass — that is the
 * hard cutoff, and the reason a material that somehow misses the soft mask degrades
 * to the old behaviour instead of drawing a temple arm across a cheek.
 */
export const OCCLUDER_LAYER = 1;

/**
 * How wide the soft band at the occlusion boundary is, in cm.
 *
 * 1.2 mm, which is about the accuracy the whole stack can honestly claim: the anchor
 * recovery borrows canonical depth, MediaPipe's landmarks carry their own noise, and
 * the deformation is smoothed. A feather narrower than the error draws the error; one
 * much wider starts dissolving frame that is genuinely in front of the face.
 *
 * It is also the amount the occlusion boundary sits *behind* the true skin line, since
 * the relief below is measured from it — about 1.6 screen pixels at selfie range, which
 * is the price of never punching a hole in a frame again.
 */
export const OCCLUDER_FEATHER = 0.12;

/**
 * How far inside the skin the rendered occluder sits, in cm.
 *
 * Not a tuning constant — it is the smallest value that makes a correctly seated pad
 * fully visible, and it is derived rather than chosen so it cannot fall out of step
 * with either term:
 *
 *   `seat()` leaves the rearmost contact at `skin - PAD_SINK`.
 *   The mask fades to nothing at the rendered surface and is fully opaque
 *   `OCCLUDER_FEATHER` in front of it.
 *   So the pad is untouched exactly when `relief >= PAD_SINK + OCCLUDER_FEATHER`.
 *
 * The extra 0.1 mm is margin against the two surfaces landing on the same depth value,
 * which is a z-fight rather than an answer.
 */
export const OCCLUDER_RELIEF = PAD_SINK + OCCLUDER_FEATHER + 0.01;

/**
 * The most of itself the relief may ever show on screen, in pixels.
 *
 * Everything above is in centimetres of face space, which is the right unit for a
 * physical quantity and the wrong one for an artefact — a fixed millimetre offset
 * doubles in pixels every time the wearer halves their distance to the camera. Worked
 * out against a 63° camera and a 960 px buffer, the relief and the fade put the
 * occlusion boundary this far inside the real skin:
 *
 *     60 cm ->  1.6 px      30 cm -> 3.1 px      18 cm -> 5.2 px
 *     45 cm ->  2.1 px      23 cm -> 4.1 px      14 cm -> 6.7 px
 *
 * Every number in this rebuild was measured at 45 cm, where it is two pixels and
 * invisible. The captures it was reported from are close-ups with a face filling the
 * frame — five to seven pixels of frame drawn over skin that should have hidden it, and
 * a 4 px edge snap that could not reach far enough to pull it back.
 *
 * So the offsets stay physical until they would cost more than this, and then they stop
 * growing. The cost of capping them is that a pad pressed into skin at arm's length may
 * fade slightly when the wearer leans right in; the cost of not capping them is the
 * artefact this whole engine exists to remove, exactly where people look closest.
 */
export const MAX_RELIEF_PX = 4.0;

/**
 * How quickly the deformation follows a new measurement, as a time constant in seconds.
 *
 * Deliberately slow, and it costs nothing: these are *face-space* offsets with the
 * pose already divided out, so on a still head they are constant and on a turning head
 * they are still constant. Almost everything that moves frame to frame in them is
 * measurement noise, and noise in the occluder is a boundary that shimmers along the
 * side of the nose — the artefact most likely to be *introduced* by making a rigid
 * occluder track a face.
 *
 * The exception is expression, which is real and which this lags by about a third of a
 * second. A smile does not move the nasal sidewall enough to matter to a frame.
 */
const SHAPE_TAU = 0.10;

/**
 * How head-on a vertex has to be facing before its measurement is believed.
 *
 * The deformation updates at *every* pose now, and this is what makes that safe. A
 * landmark on the far side of a turned head is not observed — MediaPipe reports it, but
 * it is the model's guess at where a hidden thing is. A landmark on the near side is
 * seen, and the ray-through-landmark recovery pins it exactly.
 *
 * So each vertex is updated in proportion to how squarely it faces the camera, and one
 * that has turned away holds the last reading taken while it could still be seen.
 * Trust the observation where there is one, hold where there is not.
 *
 * The ramp sits almost entirely *behind* the silhouette, and that placement is the whole
 * of whether this helps or hurts. A vertex exactly on the silhouette has a facing of
 * zero — and it is perfectly well observed, being the one the detector can see most
 * clearly against the background. It is also, precisely, the vertex the occlusion
 * boundary is made of. A ramp that starts at zero and reaches trust at 0.35 throttles it
 * to 13% and its neighbours to 40-70%, which freezes the boundary while updating the
 * middle of the face: measured, that turned a 1.7 px mismatch at 40 degrees into 5.4.
 * So everything from the silhouette forwards is believed outright, and only what has
 * genuinely turned away is held.
 */
const FACING_HOLD = -0.45;
const FACING_TRUST = -0.05;

/**
 * The self-occlusion test the facing ramp cannot do.
 *
 * The facing ramp detects a vertex whose own surface turns away. It cannot detect a
 * vertex hidden behind *other* geometry — and at 30–45 degrees of yaw the far nasal
 * sidewall is exactly that: it still faces the camera well enough to be fully
 * trusted, while the nose ridge stands between it and the lens. MediaPipe keeps
 * reporting a landmark for it, but that landmark is the model's guess at a hidden
 * thing, and ray-pinning the vertex to it drags the far occlusion boundary — the
 * edge that decides where the far lens disappears — onto a hallucination.
 *
 * So each measurement pass rasterises the carried head (the base 468-vertex mesh,
 * ~900 triangles) into a small camera-space depth grid and tests every vertex
 * against it: a vertex with other geometry more than `VIS_BIAS` in front of it along
 * its own view ray is not being observed, whatever its normal says, and holds.
 *
 * `VIS_BIAS` is slack for the grid's own resolution — a cell spans a couple of
 * millimetres of face, and on a grazing surface that is real depth variation which
 * must not read as cover. The silhouette is the case that matters: a vertex *on* the
 * silhouette is its own front surface, so the grid depth at its cell is its own
 * depth and it stays trusted — which is the property the facing ramp was shaped so
 * carefully to keep, preserved here by construction. `VIS_RAMP` fades trust out over
 * the next half centimetre of cover rather than cutting it, so the hold engages
 * without a seam as the head turns.
 */
const VIS_GRID = 96;
const VIS_BIAS = 0.35;
const VIS_RAMP = 0.5;

/**
 * Extra slack for grazing surfaces, in cm at facing zero.
 *
 * The grid's depth is quantised to its cells, and on a surface the camera sees
 * edge-on — the nasal sidewall at moderate yaw, the cheek silhouette — depth changes
 * by many millimetres across one cell, so the frontmost triangle in a vertex's cell
 * can sit a centimetre in front of the vertex while being the same smooth surface.
 * A flat bias reads that as cover and holds a vertex the camera plainly sees; and a
 * vertex near the silhouette is both the best-observed one and the one the boundary
 * is made of, so a false hold there is the one error this test must not make.
 *
 * So the bias grows as the vertex's own surface turns edge-on, from `VIS_BIAS` when
 * it faces the lens squarely to `VIS_BIAS + VIS_GRAZE` at grazing. Genuine cover
 * survives this: a vertex meaningfully behind the nose ridge sits centimetres deep
 * along its view ray, well past even the widened bias, while quantisation error
 * cannot reach it. The cost is that the hold engages a little *past* the moment of
 * true occlusion rather than exactly at it — which is the direction the whole
 * facing ramp is already shaped to err in: trust the silhouette, hold what is well
 * beyond it.
 */
const VIS_GRAZE = 1.0;

/**
 * How far a vertex may be carried from its canonical position, in cm.
 *
 * The same job as `clampAnchors`: a hand across the face or a bad frame must not be
 * able to turn the head inside out. Generous, because real faces genuinely differ from
 * the average one by more than a centimetre out at the jaw and temple, and because the
 * smoothing above already absorbs anything brief.
 */
const OFFSET_LIMIT = 2.0;

/**
 * How far the landmark-depth fit may move a landmark from its borrowed depth, in cm.
 *
 * Tighter than `OFFSET_LIMIT` because this is the one axis a single camera cannot
 * really see. The fit is over all 468 points at once so it is stable, but its input is
 * the noisiest thing MediaPipe reports and the clamp is what bounds the damage if a
 * frame's z comes back as nonsense.
 *
 * Wide enough to let a real nose through, which matters more now that the slope comes
 * from the camera rather than from a regression: the regression could never have
 * produced a face deeper than the average one, so this clamp was never the binding
 * constraint on protrusion. It is now.
 *
 * It is also an instrument: a fit that is working leaves nearly every vertex well
 * inside this bound, and one that is not pins them against it. That count is what
 * exposed the first version of `fitLandmarkDepth` solving against the wrong axis —
 * 246 of 468 vertices railed — so `window.__ar` reports it.
 */
const DEPTH_LIMIT = 1.6;

/**
 * The r2 gate, de-cliffed: full trust above one bound, none below the other, a
 * smooth blend between.
 *
 * There used to be a single 0.90 threshold (`DEPTH_FIT_MIN_R2`) beside this band,
 * and both halves of its career are instructive. As a binary per-frame decision it
 * was a metronome: exactly at yaw, where landmark quality degrades toward it, each
 * crossing swapped the nose between its fitted protrusion and the borrowed average
 * one, a ~0.3 s morph of the whole far boundary per flip. De-cliffed into this band
 * it stopped flipping — and was then measured to be *inert*: on every one of the
 * user's twelve complaint captures the global r2 sat above 0.96 with the weight
 * pinned at 1.0, sd 0, RISING with pose severity, because a hallucinated far
 * sidewall still correlates beautifully with camera depth in the global sum. r2
 * survives as one factor of the weight and as a reported statistic; the factor that
 * actually discriminates on real captures is the nose-window residual below.
 */
const DEPTH_FIT_ZERO_R2 = 0.88;
const DEPTH_FIT_FULL_R2 = 0.92;

/**
 * How long the depth fit's global quantities are remembered, in seconds (C7).
 *
 * `r2`, `b` and `a` are slow global properties of the whole landmark cloud — and
 * `a` is rebuilt per frame from the pose's raw distance (`|e[14]|`), so it jitters
 * with pose z. Applied memorylessly, their per-frame noise walked straight through
 * the 0.04-wide r2 band (37.5 of weight slope per unit of r2) and pumped the
 * shape/seat chain at ~0.3 s — so the EMA is matched to that measured timescale:
 * long enough to kill the dither, and costing only ≤0.3 s of staleness on a signal
 * the anchors already consume one frame stale by design.
 */
const DEPTH_EMA_TAU = 0.3;

/**
 * The nose-window residual gate: full trust below the first bound, none above the
 * second, in cm of RMS residual (NOSE_RESID_TRUST in the design).
 *
 * This is the discriminator the global r2 provably cannot be. A good fit's nose
 * residual is bounded by landmark z noise — a millimetre or so — while a
 * hallucinated far sidewall (MediaPipe inventing landmarks for skin the nose ridge
 * hides) produces multi-millimetre residual concentrated exactly in the nose
 * window, even as the global correlation *improves* with pose severity. The band
 * separates those two regimes. Residuals are de-meaned within the window first, so
 * a clean DC offset (which `b` absorbs) is not read as disagreement.
 */
const NOSE_RESID_ZERO = 0.15;
const NOSE_RESID_FULL = 0.30;

/**
 * The nose box the residual is measured over, in cm of canonical face space:
 * within `NOSE_BOX.x` of the centreline, within `NOSE_BOX.y` of the bridge's
 * height. The same box every nose-window measurement in the harness uses. On the
 * canonical mesh it holds ~98 vertices — small enough that the second sum set is
 * noise on the fit's cost, wide enough to span both sidewalls and the tip.
 */
const NOSE_BOX = { x: 2.0, y: 2.5 };

/** Per-face nose-box index mask, built once — membership never changes.
 * Exported since stage 4: the person model's G9 dual-baseline tripwire runs
 * over the identical window, and two masks that could drift apart would let
 * the tripwire and the depth gate disagree about what "the nose" is. */
const noseMaskCache = new WeakMap();

export function noseMaskFor(face) {
  let mask = noseMaskCache.get(face);
  if (mask) return mask;
  const flags = new Uint8Array(face.vertexCount);
  const bridgeY = face.point(LM.NOSE_BRIDGE)[1];
  let count = 0;
  for (let i = 0; i < face.vertexCount; i++) {
    if (Math.abs(face.positions[i * 3]) > NOSE_BOX.x) continue;
    if (Math.abs(face.positions[i * 3 + 1] - bridgeY) > NOSE_BOX.y) continue;
    flags[i] = 1;
    count++;
  }
  mask = { flags, count };
  noseMaskCache.set(face, mask);
  return mask;
}

/** Rebuild thresholds, in cm of accumulated vertex movement since the last one. */
const SURFACE_DEADBAND = 0.02;  // 0.2 mm — the seat's own precision
const PROFILE_DEADBAND = 0.20;  // 2 mm — all the temple routing can use

/**
 * The rebuild cadence backstop (C6): a drift past the deadband earns a rebuild
 * only every third frame, unless it is large enough that waiting would show.
 *
 * The deadband alone was the storm: on the diag stills, noise-driven micro-
 * drift crossed 0.2 mm nearly every frame and the surface rebuilt 39–62 times
 * per 60 — each rebuild a fresh compensation solve, a re-loft, a re-rasterise,
 * and a small step for the seat to ride. The shrinkage floor (G16, in
 * `measureShape`) removes the drift at its source on a still head; this
 * interval bounds the worst case on a moving one. The 1 mm bypass keeps real
 * expression and pose reshapes at ≤SHAPE_TAU latency — a genuine movement is
 * never made to wait three frames.
 */
const REBUILD_MIN_INTERVAL = 3;
const REBUILD_BYPASS = 0.1;

/**
 * The shrinkage floor on the view-residual innovation, cm (G16).
 *
 * 0.3 mm — the seat's own precision (SURFACE_DEADBAND heritage) and ≈1.5x the
 * measured frontal landmark noise. Per vertex the floor widens with the person
 * model's online noise estimate (1.5·resNoise_i), so the tip and the hard-pose
 * regime, measured 2–6x noisier, rest under a correspondingly wider floor. An
 * innovation inside the floor is indistinguishable from noise and moves
 * nothing; one past it passes minus the floor's radius, so real motion tracks
 * with ≤SHAPE_TAU lag exactly as before, less up to r0 of bounded residual.
 */
const SHRINK_FLOOR = 0.03;

/**
 * How quickly a HELD vertex's view residual decays onto the person estimate.
 *
 * A vertex the camera cannot see holds its last reading — that rule predates
 * the person model and stays. But once the model underneath is confident, the
 * held residual is a stale view-dependent correction measured at some previous
 * pose, sitting on top of a converged estimate that no longer needs it. So a
 * held vertex's residual decays toward zero at TAU_RESID_DECAY, scaled by how
 * much the model has actually accumulated there (W/CONF_HOLD, saturating at
 * 30 unit frames ≈ one second of good viewing) — a cold model decays nothing,
 * a converged one quietly takes ownership of the far side of the head.
 */
const TAU_RESID_DECAY = 2.0;
const CONF_HOLD = 30;

/**
 * The relief-cap effect deadband, in screen pixels (C6's deferred input).
 *
 * The relief trigger used to fire on any 0.005 cm change of the capped relief
 * — and inside ~35 cm, where the cap binds, the cap's own slope is ~0.0051 cm
 * per cm of approach (MAX_RELIEF_PX/pixelsPerCm differentiated), so a slow
 * lean re-relieved the mesh nearly every detection and a close-up session
 * never rested. The deadband is therefore stated in the artefact's own unit:
 * a relief error only earns a rebuild once it would SHOW — half a pixel on
 * screen, far under the feather's own 2–4 px width — with the 0.005 cm
 * absolute floor kept so the far regime (cap slack, reliefBase constant)
 * never fires at all. Self-hysteretic: each fire snaps the applied relief to
 * the current cap, so the error re-arms from zero and cannot chatter.
 */
const RELIEF_DEADBAND_PX = 0.5;

const shift = new THREE.Vector3();
const cameraInFace = new THREE.Vector3();
const toFace = new THREE.Matrix4();

/**
 * Builds the occluder: a closed head that writes depth, and the surface the frame is
 * seated against, as one object.
 */
export function createOccluder(face) {
  const group = new THREE.Group();
  group.name = 'occluder';

  // The face is subdivided *before* the skull is lofted from it, so the loft starts at
  // the dense rim and the two stay watertight — 36 rim vertices become 144, which also
  // takes the facets out of the skull's own silhouette for free.
  const subdivision = planSubdivision(face.indices, face.vertexCount, SUBDIVISION_LEVELS);
  const control = new Float32Array(face.vertexCount * 3);
  const fineFace = new Float32Array(subdivision.vertexCount * 3);
  subdivision.compensate(face.positions, control);
  subdivision.apply(control, fineFace);

  const shell = buildHeadShell({
    positions: fineFace,
    indices: subdivision.indices,
    vertexCount: subdivision.vertexCount,
  });
  const vertexCount = shell.positions.length / 3;

  // Four arrays, and every one is load-bearing:
  //   `restBase` — the canonical *measured* mesh, 468 vertices. What offsets are from.
  //   `control`  — those 468, pre-compensated so the limit surface interpolates them.
  //   `skin`     — the subdivided, lofted, deformed true surface. Field and profile.
  //   the geometry's own array — `skin` relieved inward. Only this one is drawn.
  const restBase = face.positions.slice();
  const base = new Float32Array(face.vertexCount * 3);
  const skin = shell.positions.slice();

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(shell.positions, 3));
  geometry.setIndex(new THREE.BufferAttribute(shell.indices, 1));
  geometry.computeVertexNormals();

  const material = new THREE.MeshBasicMaterial({
    colorWrite: false, depthWrite: true, side: THREE.DoubleSide,
  });

  const head = new THREE.Mesh(geometry, material);
  // Ahead of the glasses, so the depth buffer is already populated when the
  // frames are drawn.
  head.renderOrder = -1;
  head.layers.enable(OCCLUDER_LAYER);
  group.add(head);

  const pinna = buildPinnaGeometry();
  const pinnaGeometry = new THREE.BufferGeometry();
  pinnaGeometry.setAttribute('position', new THREE.BufferAttribute(pinna.positions, 3));
  pinnaGeometry.setIndex(new THREE.BufferAttribute(pinna.indices, 1));
  pinnaGeometry.computeVertexNormals();

  const ears = {};
  for (const side of [-1, 1]) {
    const ear = new THREE.Mesh(pinnaGeometry, material);
    // The dish is built bulging along +x, so the right ear is the same geometry
    // mirrored. Winding flips with it, which costs nothing on a double-sided
    // depth-only material.
    ear.scale.x = side;
    ear.renderOrder = -1;
    ear.layers.enable(OCCLUDER_LAYER);
    group.add(ear);
    ears[side < 0 ? 'right' : 'left'] = ear;
  }

  // Only the triangles that can ever reach the depth field's window, and only the face's
  // own — the loft behind them is a *back* surface, and rasterising it into a field that
  // keeps the maximum z would put the back of the skull in front of the nose wherever the
  // two overlap in x and y.
  //
  // Subdivision multiplied the face by sixteen and the window covers less than half of
  // it, so this is most of that cost back. The margin is `OFFSET_LIMIT`, which is the
  // furthest the deformation is ever allowed to carry a vertex, so a triangle excluded
  // here cannot reach the window on any face.
  const windowIndices = selectWindowTriangles(fineFace, subdivision.indices);

  const surface = buildFaceSurface({
    positions: skin,
    indices: windowIndices,
    origin: [skin[LM.NOSE_BRIDGE * 3], skin[LM.NOSE_BRIDGE * 3 + 1], skin[LM.NOSE_BRIDGE * 3 + 2]],
  });

  group.userData = {
    head,
    ears,
    /** The shadow catcher, once one exists. It has to be moved with the head. */
    shadowCatcher: null,
    /** The wireframe overlay, once one exists. Follows the deformation and the shift. */
    debugMesh: null,
    face,
    subdivision,
    ring: shell.ring,
    faceVertexCount: shell.faceVertexCount,
    faceTriangleCount: subdivision.triangleCount,
    windowIndices,
    indices: shell.indices,
    restBase,
    /** The canonical mesh's own normals, for the per-vertex visibility test. */
    baseNormals: (() => {
      const normals = new Float32Array(face.positions.length);
      computeNormals(face.positions, face.indices, normals);
      return normals;
    })(),
    base,
    control,
    skin,
    /**
     * This face's deformation, in cm from canonical — the COMPOSITE the whole
     * downstream reads: `person.offsets + viewResidual`, written every frame
     * by `measureShape`. Everything that consumes the shape (`rebuildSurface`,
     * the field, the relief, `surfaceOf`) reads this one array, exactly as it
     * always did — the single-surface invariant does not know the person model
     * exists. Without a person model the composite IS the view residual.
     */
    offsets: new Float32Array(face.vertexCount * 3),
    /**
     * The fast layer: what THIS view says on top of the person estimate,
     * eased at SHAPE_TAU through the shrinkage floor. On a converged model it
     * hovers near zero on a still head; on a cold one it carries the whole
     * deformation, which is what makes frame one bit-identical to a pipeline
     * with no person model at all.
     */
    viewResidual: new Float32Array(face.vertexCount * 3),
    /** Scratch for one frame's raw recovery, before it is clamped and smoothed in. */
    observed: new Float32Array(face.vertexCount * 3),
    /** The self-occlusion grid, and per vertex how deep behind cover it sits (cm;
     * -Infinity when nothing covers it). See `VIS_GRID`. */
    visibility: {
      depth: new Float32Array(VIS_GRID * VIS_GRID),
      proj: new Float32Array(face.vertexCount * 2),
      vertexDepth: new Float32Array(face.vertexCount),
      behind: new Float32Array(face.vertexCount).fill(-Infinity),
    },
    /**
     * What the relief is derived from: `PAD_SINK + feather + margin`. The app
     * re-derives it when the feather control moves, so the two cannot drift apart —
     * a fade wider than the relief reopens the hole in the bridge the relief exists
     * to prevent.
     */
    reliefBase: OCCLUDER_RELIEF,
    /** Nothing measured yet, so the first sample is adopted whole rather than eased into. */
    hasShape: false,
    /** How far the shape has moved since each of the two rebuilds, in cm. */
    driftSurface: Infinity,
    driftProfile: Infinity,
    surface,
    profile: buildHeadProfile({ positions: skin, indices: shell.indices }),
    shift: new THREE.Vector3(),
    /** False until the compensation solve has a previous answer worth warm-starting from. */
    compensated: false,
    /** How far the drawn surface sits inside the skin, in cm. Capped on screen — see above. */
    relief: OCCLUDER_RELIEF,
    /**
     * The landmark-depth fit's persistent conditioned state (C7): smoothed a/b/r2/
     * rmsNose and the applied weight, folded per frame by `conditionDepthFit`.
     * Null until the first fit, and cleared whole whenever nothing is measuring
     * one any more — landmark depth toggled off, the deform toggled off, or a
     * re-measure (new face, new source; see `remeasure` in `frame.js`). This
     * object — never the raw per-frame fit — is what `carryLandmarks` and, one
     * frame stale, the anchor recovery consume.
     */
    depthFit: null,
    /** Landmarks whose fitted depth hit `DEPTH_LIMIT` last frame. Should be near zero. */
    depthClamped: 0,
    vertexCount,
    /**
     * Rebuild counters, so the harness and `window.__ar` can see the real
     * cost — extended at stage 4 with the cause of the last surface rebuild
     * ('drift' | 'relief' | 'reset') and a ring of recent rebuild frames so
     * `__ar.rebuilds.surfacePerMin` can be computed live.
     */
    rebuilds: { surface: 0, profile: 0, lastCause: null, marks: [] },
    /** Frames seen since creation — the clock the rebuild rate is read against. */
    frameCount: 0,
    /** Frames since the last surface rebuild — the C6 interval's own clock. */
    framesSinceRebuild: REBUILD_MIN_INTERVAL,
  };

  applyRelief(group);

  return group;
}

/**
 * Writes `skin` into the drawn geometry, relieved inward along its own normals.
 *
 * Along the normal rather than along z, because the point is to shrink the occluding
 * *solid* uniformly. At the nasal sidewall — where the far lens's inner rim crosses,
 * and where every one of the reported artefacts lives — the surface normal points
 * sideways, so a relief along z would barely move the boundary that actually matters
 * while over-relieving the bridge.
 */
/**
 * Area-weighted vertex normals, straight over the typed arrays.
 *
 * The same algorithm `BufferGeometry.computeVertexNormals` runs, and it is here rather
 * than called because subdivision took this mesh from 1,726 triangles to 17,680 and
 * three's version costs 1.7 ms on it — a third of the whole rebuild, spent inside
 * `Vector3` allocations for a quantity only used to push vertices along their own
 * normal. This does it in a quarter of the time and produces the same numbers.
 */
function computeNormals(positions, indices, normals) {
  normals.fill(0);
  for (let t = 0; t < indices.length; t += 3) {
    const a = indices[t] * 3; const b = indices[t + 1] * 3; const c = indices[t + 2] * 3;
    const ux = positions[b] - positions[a];
    const uy = positions[b + 1] - positions[a + 1];
    const uz = positions[b + 2] - positions[a + 2];
    const vx = positions[c] - positions[a];
    const vy = positions[c + 1] - positions[a + 1];
    const vz = positions[c + 2] - positions[a + 2];
    // Left unnormalised, so the accumulation is area-weighted — a big triangle should
    // have more say in a vertex's normal than a sliver does.
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    normals[a] += nx; normals[a + 1] += ny; normals[a + 2] += nz;
    normals[b] += nx; normals[b + 1] += ny; normals[b + 2] += nz;
    normals[c] += nx; normals[c + 1] += ny; normals[c + 2] += nz;
  }
  for (let i = 0; i < normals.length; i += 3) {
    const len = Math.hypot(normals[i], normals[i + 1], normals[i + 2]);
    if (len < 1e-12) continue;
    normals[i] /= len; normals[i + 1] /= len; normals[i + 2] /= len;
  }
}

function applyRelief(occluder) {
  const { head, skin, indices, relief } = occluder.userData;
  const geometry = head.geometry;
  const position = geometry.attributes.position;
  const array = position.array;
  const normals = geometry.attributes.normal.array;

  // Normals first, from the true surface: they are the same to first order on the
  // relieved one, and computing them from an already-relieved mesh would compound the
  // offset a little further every rebuild.
  computeNormals(skin, indices, normals);
  for (let i = 0; i < array.length; i += 3) {
    // A degenerate normal means a degenerate triangle fan, which has no surface to
    // stand off from. Leaving the vertex where it is only ever costs relief.
    const nx = normals[i]; const ny = normals[i + 1]; const nz = normals[i + 2];
    array[i] = skin[i] - nx * relief;
    array[i + 1] = skin[i + 1] - ny * relief;
    array[i + 2] = skin[i + 2] - nz * relief;
  }

  position.needsUpdate = true;
  geometry.attributes.normal.needsUpdate = true;
  geometry.computeBoundingSphere();
}

/**
 * Fits MediaPipe's relative landmark depths onto real camera depth, as one affine.
 *
 * This is the only way a single camera recovers how far a nose actually sticks out,
 * and the signal has been flowing through the worker unused since it was written —
 * `tracker.worker.js` packs `landmarks[i].z` and `tracker.js` unpacks it, and nothing
 * reads it. The pipeline's distrust of it is well founded and is about the wrong
 * property: the value is *relative*, with an arbitrary origin and a scale that depends
 * on how much of the frame the face fills, so it says nothing absolute and cannot be
 * used for placement.
 *
 * An occluder does not need it to be absolute. Fitting one `a*z + b` across all 468
 * points throws away exactly the two things that are arbitrary — the scale and the
 * origin — and keeps the only thing that is not, which is the *shape*. What survives
 * the fit is this face's relief against the average face's, which is precisely what
 * borrowing a canonical depth cannot supply.
 *
 * **Against camera depth, not against face-space z**, and that distinction is the
 * whole of whether this works. MediaPipe's z is a depth along the *view* axis. Face
 * space is the head's own frame, and the two coincide only when the head is square to
 * the camera — so a fit against face-space z is correct at zero yaw and quietly
 * degrades from there, which is the worst possible failure shape because a test rig is
 * usually head-on. It was written that way first. Measured on a real capture at 10.6
 * degrees of turn: MediaPipe's z explains **97.1%** of the variance in camera depth
 * and **63.5%** of the variance in face-space z, and the face-space version pinned 246
 * of 468 vertices against their clamp — a fit that was not describing a head at all.
 *
 * So the answer is a camera depth, and `carryLandmarks` walks each ray out to it. The
 * inverse pose then distributes it across face-space x, y and z the way the geometry
 * demands, at any angle, with nothing here needing to know the angle.
 *
 * `headMatrixWorld` must be the *raw* pose the landmarks were solved with, for the
 * same reason everything else measured in this pipeline is.
 *
 * `exclude` is the per-vertex hold verdict of the self-occlusion grid (graft G7):
 * a marked vertex is standing behind other geometry — the far nasal sidewall at
 * yaw — so its landmark is MediaPipe's guess at a hidden thing, and its z has no
 * business voting on the offset every *visible* vertex's depth is walked to. The
 * deform loop already refuses to chase those landmarks; this makes the fit refuse
 * them too, with the identical criterion, so the hallucinated far side stops
 * polluting the affine while remaining fully visible to the nose residual below.
 *
 * Returns `used: false` rather than a weak answer: a `weight` of zero means the
 * depths do not describe a head — the global correlation is poor, or the nose
 * window disagrees with the affine by more than any honest fit can — and reshaping
 * a head from them would be worse than the average nose this replaces.
 */
export function fitLandmarkDepth(landmarks, face, headMatrixWorld, camera = null,
  exclude = null) {
  const count = Math.min(face.vertexCount, landmarks.length);
  const e = headMatrixWorld?.elements;
  if (!e) return null;

  const nose = noseMaskFor(face).flags;

  let sz = 0; let sc = 0; let szz = 0; let szc = 0; let scc = 0; let n = 0;
  // The same loop's nose-restricted sums (C7). Accumulated over EVERY nose-box
  // vertex, held-behind-cover ones included — deliberately, and the asymmetry with
  // the global exclusion is the mechanism: the residual's job is to *see* the
  // hallucinated far sidewall the global sums are being protected from.
  let szN = 0; let scN = 0; let szzN = 0; let szcN = 0; let sccN = 0; let nN = 0;
  let nExcluded = 0;

  for (let i = 0; i < count; i++) {
    const z = landmarks[i]?.z;
    if (!Number.isFinite(z)) continue;
    // The canonical vertex's depth in camera space — the same quantity
    // `carryLandmarks` borrows when there is no fit, so the fit is a correction to a
    // known baseline rather than an independent guess at an absolute.
    const c = e[2] * face.positions[i * 3]
      + e[6] * face.positions[i * 3 + 1]
      + e[10] * face.positions[i * 3 + 2]
      + e[14];
    if (nose[i]) { szN += z; scN += c; szzN += z * z; szcN += z * c; sccN += c * c; nN++; }
    if (exclude && exclude[i]) { nExcluded++; continue; }
    sz += z; sc += c; szz += z * z; szc += z * c; scc += c * c; n++;
  }

  if (n < 100) return null;

  const varZ = szz - (sz * sz) / n;
  const varC = scc - (sc * sc) / n;
  const cov = szc - (sz * sc) / n;
  if (!(varZ > 1e-12) || !(varC > 1e-12)) return null;

  // The correlation, which is what decides whether these depths describe a head at all.
  // Scale-free on purpose — it is a gate, not a measurement.
  const r2 = (cov * cov) / (varZ * varC);

  // **The slope comes from the camera, not from the regression.** This is the part that
  // was wrong, and it was wrong in a way `r2` cannot see.
  //
  // Regressing MediaPipe's z onto the *canonical* head's depths gives the best linear
  // predictor of the average face — so it inherits the average face's depth range and
  // shrinks every individual towards it. Measured live on a real capture: the regression
  // returned a slope of 22.52 where the geometry demands 27.65, reconstructing that face
  // **18.5% flatter than it is**, while reporting r2 = 0.971. A correlation of 0.97 with
  // the wrong gain is exactly what regression to the mean looks like.
  //
  // The right slope is not a free parameter. MediaPipe documents z as "scaled as the X
  // coordinate under the weak perspective projection camera model" — X being normalised
  // across the image width — so one unit of z is one image width of metric distance at
  // the face's own depth, and that is a number the camera and the pose already know:
  //
  //     slope = -2 * distance * tan(fov / 2) * aspect
  //
  // **`distance` is the face's own mean camera depth, not the head origin's.**
  // The first camera-slope implementation took `|e[14]|` — the pose
  // translation — and that is the wrong plane by about five centimetres:
  // MediaPipe scales z "as the X coordinate", i.e. at the depth where the FACE
  // is, and the face's centroid rides ~5 cm in front of the head origin.
  // Measured on the stage-3 harness synthetic: true mean depth −50.05 cm
  // against −55.15 used, a ~10% slope bias that reconstructed every nose ~10%
  // too deep (the "overshoots the shallow direction" note in the stage-3
  // landing). The mean carried depth of exactly the vertices the fit includes
  // is already in the accumulator (`sc/n`), from the same loop, under the same
  // G7 exclusions — so the slope now reads the plane the detector actually
  // scaled at.
  //
  // Only the offset is solved, and it is solved by putting the cloud's mean depth where
  // the pose says the head is. Position from the pose, shape from z, and neither one
  // borrowing the average face's proportions.
  let a = cov / varZ;
  let fromCamera = false;
  if (camera?.isPerspectiveCamera) {
    const distance = Math.abs(sc / n);
    if (distance > 1) {
      a = -2 * distance * Math.tan((camera.fov * Math.PI) / 360) * camera.aspect;
      fromCamera = true;
    }
  }
  const b = (sc - a * sz) / n;

  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;

  // The nose-window residual against the affine just solved, de-meaned within the
  // window (C7). With e_i = c_i − (a·z_i + b), the mean and mean-square over the
  // window expand into the sums already accumulated above — no second pass:
  //   Σe  = Sc − a·Sz − n·b
  //   Σe² = Scc − 2a·Szc − 2b·Sc + a²·Szz + 2ab·Sz + n·b²
  // and rmsNose is the standard deviation of e over the window. De-meaned because a
  // clean DC offset in the window is absorbed by `b` downstream and is not shape
  // disagreement; what is left after the mean IS: a far sidewall hallucinated flat,
  // a tip pulled toward the average head, any nose the affine cannot describe.
  let rmsNose = 0;
  if (nN > 3) {
    const sumE = scN - a * szN - nN * b;
    const sumEE = sccN - 2 * a * szcN - 2 * b * scN
      + a * a * szzN + 2 * a * b * szN + nN * b * b;
    rmsNose = Math.sqrt(Math.max(sumEE / nN - (sumE / nN) ** 2, 0));
  }

  // The pose depth the fit was solved against, carried with the answer so the
  // conditioning can separate what the pose already knows from what actually
  // needs smoothing — see `conditionDepthFit`.
  const poseDepth = e[14];

  // Not a threshold any more — a blend, and a product of two of them. The r2 factor
  // keeps the original refusal (depths that do not correlate with camera depth at
  // all describe nothing); the nose-residual factor is the one that bites on real
  // captures, where the global r2 is measured inert (see the band's note). Each is
  // a smoothstep, so the transition between "trust the fit" and "trust the average
  // head" is continuous in both inputs rather than a cliff the nose falls off.
  const t = clamp((r2 - DEPTH_FIT_ZERO_R2) / (DEPTH_FIT_FULL_R2 - DEPTH_FIT_ZERO_R2), 0, 1);
  const tn = clamp((rmsNose - NOSE_RESID_ZERO) / (NOSE_RESID_FULL - NOSE_RESID_ZERO), 0, 1);
  const weight = (t * t * (3 - 2 * t)) * (1 - tn * tn * (3 - 2 * tn));
  return { a, b, r2, rmsNose, weight, used: weight > 0, fromCamera, n, nExcluded, poseDepth };
}

/**
 * Folds one frame's raw fit into the persistent conditioned state the pipeline
 * actually consumes (C7).
 *
 * `data.depthFit` used to be overwritten memorylessly every frame, which made the
 * one-frame-stale handoff to the anchors a per-frame noise conduit: `a` jitters
 * with pose z, `r2` dithers, and a 37.5 weight slope turned 0.005 of r2 wobble
 * into 0.19 of applied-weight step. Now the frame's raw fit is evidence folded
 * into an EMA at `DEPTH_EMA_TAU`, and the *smoothed* r2/rmsNose drive the applied
 * weight — so the gate is finally both alive (rmsNose moves it on hard poses) and
 * quiet (nothing it applies can step faster than the EMA).
 *
 * First sample adopted whole — a session's first fit is the fit, not something to
 * ease toward from nothing. A degenerate frame (`raw` null: too few finite z, a
 * collapsed variance) holds the previous state rather than dropping it: the fit is
 * a slow global quantity and one bad frame is not evidence the face changed.
 *
 * **`a` and `b` are smoothed in a distance-invariant parameterisation**, and the
 * reason is a bug this EMA shipped with. `b` is an absolute camera depth — where
 * the landmark cloud's mean sits — so almost all of it is the pose's own
 * translation, which is known *exactly, per frame*, from the same matrix the fit
 * was solved against. Smoothed directly, that known part was made to share the
 * residual's 0.3 s timescale: lean in at 10 cm/s and the carried `b` trails the
 * face by v·τ = 3 cm, past the ±1.6 cm clamp — every carried depth rails and the
 * recovered head flattens against the bound precisely while the wearer is moving
 * in to look. `a` has the same distance factor by the weak-perspective model
 * itself (`slope = −2·d·tan(fov/2)·aspect`). So what the EMA holds is only what
 * is genuinely uncertain — the offset's residual against the pose (`b − d`) and
 * the slope per centimetre of distance (`a / d`) — and the applied `a`/`b` are
 * reconstituted against THIS frame's pose depth. On a still head the two forms
 * are the same filter; on a moving one the rigid translation passes straight
 * through, which is the placement principle applied to a depth.
 */
function conditionDepthFit(data, raw, dt) {
  const prev = data.depthFit;
  if (!raw) return prev;

  // The same degeneracy guard the camera slope uses: a pose depth this small
  // describes nothing, so the parameterisation degrades to the identity rather
  // than dividing by it.
  const depth = Number.isFinite(raw.poseDepth) && Math.abs(raw.poseDepth) > 1
    ? raw.poseDepth : null;
  const aRel = depth ? raw.a / depth : raw.a;
  const bRel = depth ? raw.b - depth : raw.b;

  if (!prev || prev.conditioned !== true) {
    data.depthFit = {
      conditioned: true,
      a: raw.a,
      b: raw.b,
      aRel,
      bRel,
      r2: raw.r2,
      rmsNose: raw.rmsNose,
      r2Raw: raw.r2,
      rmsNoseRaw: raw.rmsNose,
      weight: raw.weight,
      weightDelta: 0,
      used: raw.used,
      fromCamera: raw.fromCamera,
      n: raw.n,
      nExcluded: raw.nExcluded,
    };
    return data.depthFit;
  }

  const alpha = 1 - Math.exp(-Math.max(dt, 0) / DEPTH_EMA_TAU);
  prev.aRel += (aRel - prev.aRel) * alpha;
  prev.bRel += (bRel - prev.bRel) * alpha;
  prev.a = depth ? prev.aRel * depth : prev.aRel;
  prev.b = depth ? prev.bRel + depth : prev.bRel;
  prev.r2 += (raw.r2 - prev.r2) * alpha;
  prev.rmsNose += (raw.rmsNose - prev.rmsNose) * alpha;
  prev.r2Raw = raw.r2;
  prev.rmsNoseRaw = raw.rmsNose;
  prev.fromCamera = raw.fromCamera;
  prev.n = raw.n;
  prev.nExcluded = raw.nExcluded;

  // The applied weight, from the SMOOTHED inputs — same law as the raw one.
  const t = clamp((prev.r2 - DEPTH_FIT_ZERO_R2) / (DEPTH_FIT_FULL_R2 - DEPTH_FIT_ZERO_R2), 0, 1);
  const tn = clamp((prev.rmsNose - NOSE_RESID_ZERO) / (NOSE_RESID_FULL - NOSE_RESID_ZERO), 0, 1);
  const weight = (t * t * (3 - 2 * t)) * (1 - tn * tn * (3 - 2 * tn));
  prev.weightDelta = weight - prev.weight;
  prev.weight = weight;
  prev.used = weight > 0;
  return prev;
}

/**
 * Puts the head onto this face, and moves everything that has to move with it.
 *
 * Safe to call with anything — it no-ops on whatever it was not given — so callers and
 * the harness's bare state objects do not have to care which parts are wired up.
 */
export function updateOccluder(occluder, {
  face, camera, headMatrixWorld, landmarks, anchors,
  measuring = false, dt = 0,
  deform = true, useLandmarkDepth = true, pixelsPerCm = 0,
  person = null, wPose = 1,
  /**
   * Anchoring-v3 R0 'frozen' hold: the slow estimators under the deform —
   * the person model's accumulate/commit and the depth-fit EMA — do not
   * advance; the view-locked residual still eases so the mask keeps covering
   * THIS image (the single-surface invariant). Default false keeps every
   * existing caller bit-identical; the harness asserts the hold.
   */
  freezeEstimators = false,
} = {}) {
  const data = occluder?.userData;
  if (!data?.head) return;

  data.frameCount++;
  data.framesSinceRebuild++;

  // The relief in centimetres, but never more than `MAX_RELIEF_PX` of it on
  // screen. Behind an EFFECT deadband stated in the artefact's own unit (see
  // `RELIEF_DEADBAND_PX`): the applied relief moves only when its error
  // against the capped value would show on screen, so a close-up session —
  // where the cap binds and its slope used to cross the old 0.005 cm
  // threshold on every slow lean — genuinely rests.
  if (pixelsPerCm > 0) {
    const capped = Math.min(data.reliefBase ?? OCCLUDER_RELIEF, MAX_RELIEF_PX / pixelsPerCm);
    const err = Math.abs(capped - data.relief);
    if (err > 0.005 && err * pixelsPerCm > RELIEF_DEADBAND_PX) {
      data.relief = capped;
      data.driftSurface = Infinity;
      data.rebuilds.lastCause = 'relief';
    }
  }

  // No pose gate, and removing it is the fix for "the mesh does not match my nose at
  // difficult angles".
  //
  // It used to run only while `measuring` was true — under 25% of turn — on the same
  // reasoning the anchor window uses: a face's shape is static, so measure it from a
  // pose you can trust and carry it rigidly through the rest. That reasoning is right
  // for the anchors and wrong here, and the arithmetic says why.
  //
  // The recovery borrows depth, so a head-on measurement comes out as the true shape
  // plus an error that is almost entirely in **z**. Head-on, a z error projects to
  // nothing — the mesh sits on the face and everything looks right. Turn the head, and
  // that same error projects into x as `sin(yaw)`. **Five millimetres of depth error,
  // invisible head-on, is 3.2 mm sideways at 40° — fourteen screen pixels on a
  // close-up.** The mesh was never getting worse at angle; it was being *seen* at angle.
  //
  // Re-measuring at the current pose absorbs it: the ray-through-landmark recovery pins
  // every visible vertex onto the landmark it belongs to, for *this* view. The shape
  // becomes slightly view-dependent, which is impure for a head model and exactly right
  // for an occluder — what it owes the viewer is covering the face in the image, not
  // being a true bust of it. `measuring` still gates the anchors, which are a different
  // question and genuinely want the head-on answer.
  if (deform && landmarks && camera && headMatrixWorld && face) {
    measureShape(data, {
      face, camera, headMatrixWorld, landmarks, useLandmarkDepth, dt, person, wPose,
      freezeEstimators,
    });

    // The person commit, on the same event cadence as every other slow
    // quantity (≤ COMMIT_HZ). Invisible by construction: the solve moves
    // `person.offsets` and the view residual is re-based by exactly the same
    // delta, so the composite the surface is built from does not move — no
    // step, no drift, no rebuild. What a commit changes is only the SPLIT
    // between the slow layer and the fast one, plus everything derived from
    // the estimate (zConf, the pin base, the crossfade targets).
    if (!freezeEstimators && person && data.hasShape && person.commitDue(dt)) {
      const delta = person.commit();
      const viewResidual = data.viewResidual;
      for (let i = 0; i < viewResidual.length; i++) viewResidual[i] -= delta[i];
    }
  } else if (!deform && data.hasShape) {
    // The control was turned off. Fall back to the average head rather than freezing
    // this face's shape into it, so the toggle is the honest A/B it looks like.
    data.offsets.fill(0);
    data.viewResidual.fill(0);
    // The person model goes with the shape, for the same honesty: with the
    // deform off nothing accumulates, and a frozen estimate would spring the
    // measured face back the instant the toggle returned — a step, and a lie
    // about what the toggle shows in between.
    person?.reset();
    data.hasShape = false;
    data.driftSurface = Infinity;
    data.driftProfile = Infinity;
    data.rebuilds.lastCause = 'reset';
    // Cold-start the next compensation solve. The warm start is only valid when the
    // target moved less than the rebuild deadband; this jump is the whole
    // deformation at once, and three warm passes from the old control leave ~0.5 mm
    // of the previous face baked into the "average" head the toggle promises.
    data.compensated = false;
    // The depth fit goes with the shape. With the deform off nothing re-measures
    // it, and `frame.js` hands this object to the anchor recovery gated only on
    // ITS OWN toggle — left in place, the anchors would ride a frozen fit whose
    // offset describes wherever the head was when the control flipped, drifting
    // off by however far the wearer moves afterwards. Null is the honest value:
    // there is no fit while nothing measures one.
    data.depthFit = null;
  }

  // C6: the deadband decides WHETHER the drift is worth acting on, the
  // interval decides WHEN — a deadband on effect cadence, not on accumulated
  // micro-drift. Drift past the bypass (1 mm — a real reshape) never waits.
  if (data.driftSurface >= SURFACE_DEADBAND
    && (data.framesSinceRebuild >= REBUILD_MIN_INTERVAL
      || data.driftSurface >= REBUILD_BYPASS)) {
    if (data.driftSurface !== Infinity) data.rebuilds.lastCause = 'drift';
    rebuildSurface(occluder, anchors);
  }
  if (data.driftProfile >= PROFILE_DEADBAND) rebuildProfile(occluder);

  if (!anchors) return;

  // Position from the landmarks, every frame and with no deadband. The shape above is
  // smoothed and the bridge is not, and that is the right way round: the frame is
  // pinned to `anchors.bridge` by `solvePlacement`, so anything less than an exact
  // translation here reopens by a fraction of a millimetre the very gap this module
  // exists to close.
  shift.set(
    anchors.bridge.x - data.surface.origin[0],
    anchors.bridge.y - data.surface.origin[1],
    anchors.bridge.z - data.surface.origin[2],
  );
  data.shift.copy(shift);
  data.head.position.copy(shift);
  // The catcher is depth-tested against the occluder and must be the same surface to
  // within nothing at all — see `createShadowCatcher`. It shares the geometry; it also
  // has to share the translation.
  data.shadowCatcher?.position.copy(shift);
  data.debugMesh?.position.copy(shift);

  placeEars(occluder, anchors);
}

/**
 * One frame's recovery, clamped and eased into the carried shape.
 *
 * Since stage 4 the carried shape is TWO layers with one sum. The person
 * model's committed `offsets` are the slow face-space constant (T2 — seconds
 * to minutes, fused across poses); `viewResidual` is the fast layer this
 * function eases (T1 — SHAPE_TAU, view-locked, covering THIS image). The
 * composite `person.offsets + viewResidual` is written into `data.offsets`,
 * which is the ONLY array anything downstream reads — the field, the relief,
 * the profile and `surfaceOf` cannot tell the split exists, so the
 * single-surface invariant survives untouched. Empty person model → composite
 * ≡ view residual ≡ the pre-stage behaviour, bit for bit on frame one.
 */
function measureShape(data, {
  face, camera, headMatrixWorld, landmarks, useLandmarkDepth, dt,
  person = null, wPose = 1, freezeEstimators = false,
}) {
  const { observed, offsets, viewResidual, restBase: rest } = data;

  // Where the camera is, in face space. Everything in front of the head is on the
  // camera's side of the origin, so this is what tells a vertex whether it is looking at
  // the lens or away from it.
  toFace.copy(headMatrixWorld).invert();
  cameraInFace.set(0, 0, 0).applyMatrix4(toFace);

  // Which vertices the camera can actually see this frame, from the carried head's
  // own geometry. The facing ramp below answers "has this surface turned away"; this
  // answers "is something standing in front of it" — the far nasal sidewall at yaw
  // being the case the ramp cannot catch and the boundary cannot survive.
  //
  // Measured BEFORE the depth fit, not after it (graft G7). The reorder itself is a
  // no-op on the numbers — nothing between the old call site and this one writes the
  // carried offsets or the pose, so `behind` comes out bit-identical — and that
  // no-op-ness is asserted in the harness rather than assumed. What the order buys
  // is that the verdict exists in time for the fit to consume it below.
  measureVisibility(data, face, headMatrixWorld);
  const visBehind = data.visibility.behind;

  const { baseNormals } = data;

  // How squarely each vertex faces the lens, taken against its canonical normal and
  // its currently-carried position — accurate enough for a facing test, one
  // normalise per vertex rather than a rebuilt normal field. Computed here, once,
  // because two consumers now need it: the deform loop's trust ramp below, and the
  // fit-input hold verdict right after this loop. The verdict is the deform's own
  // criterion, applied identically: a vertex behind cover past its graze-widened
  // bias is not being observed, whatever its normal says.
  const count = offsets.length / 3;
  const facingDot = data.facingDot ?? (data.facingDot = new Float32Array(count));
  const fitExclude = data.fitExclude ?? (data.fitExclude = new Uint8Array(count));
  for (let i = 0; i < count; i++) {
    const at = i * 3;
    const px = rest[at] + offsets[at] - cameraInFace.x;
    const py = rest[at + 1] + offsets[at + 1] - cameraInFace.y;
    const pz = rest[at + 2] + offsets[at + 2] - cameraInFace.z;
    const len = Math.hypot(px, py, pz) || 1;
    const dot = -(baseNormals[at] * px + baseNormals[at + 1] * py
      + baseNormals[at + 2] * pz) / len;
    facingDot[i] = dot;
    const behind = visBehind[i];
    fitExclude[i] = behind > 0
      && behind > VIS_BIAS + VIS_GRAZE * (1 - clamp(dot, 0, 1)) ? 1 : 0;
  }

  // The depth fit goes *into* the recovery rather than being applied on top of it.
  // That is not a refactor: a z correction bolted on afterwards moves the vertex
  // straight back in face space, while walking the ray to a corrected depth moves it
  // along the line of sight — which is the only direction the observation actually
  // constrains, and which lands correctly in x and y as well at any yaw.
  //
  // What the recovery consumes is the CONDITIONED fit — the raw frame's answer
  // folded into the persistent EMA state (C7) — so the depth every ray is walked to
  // rides the smoothed a/b/weight, not this frame's jitter. `useLandmarkDepth` off
  // clears the state whole: the toggle is an honest A/B, not a fade.
  if (useLandmarkDepth) {
    // Frozen (R0): the EMA holds bit-for-bit — the raw fit is not even
    // solved, and every ray below walks to the frozen conditioned state.
    if (!freezeEstimators) {
      const raw = fitLandmarkDepth(landmarks, face, headMatrixWorld, camera, fitExclude);
      data.depthFit = conditionDepthFit(data, raw, dt);
    }
  } else {
    data.depthFit = null;
  }

  const recovery = carryLandmarks({
    face, camera, headMatrixWorld, landmarks, out: observed,
    depthFit: data.depthFit, depthLimit: DEPTH_LIMIT, person,
  });
  data.depthClamped = recovery.depthClamped;

  // First measurement is adopted whole. Easing in from the average head would drag a
  // visible reshape across the first second of every session, and the pose filter has
  // already established that frame one is a fit.
  //
  // Scaled by pose trust past the first sample (stage 6, found by the wearer live):
  // the view-locked residual exists to make the mesh cover THIS image, but at an
  // untrusted pose "this image's" landmarks are foreshortened and half-hallucinated
  // — chasing them surged the drawn surface by centimetres during a deep tilt, and
  // the seat's non-penetration guard then "protected" the frame 18 mm forward off
  // the face. Below trust the surface stands on what it has (person + the last
  // trusted residual); the fade band and snap absorb the boundary error, which is
  // millimetres, where the chase's error was centimetres.
  const trustScale = data.hasShape
    ? (wPose >= 0.3 ? 1 : Math.max(wPose / 0.3, 0.05))
    : 1;
  const alpha = data.hasShape
    ? (1 - Math.exp(-Math.max(dt, 0) / SHAPE_TAU)) * trustScale
    : 1;
  const alphaDecay = 1 - Math.exp(-Math.max(dt, 0) / TAU_RESID_DECAY);
  const personOffsets = person?.offsets ?? null;
  const resNoise = person?.resNoise ?? null;
  const personW = person?.W ?? null;
  const facingTrust = data.facingTrust ?? (data.facingTrust = new Float32Array(count));
  let drift = 0;

  for (let i = 0; i < offsets.length; i += 3) {
    let tx = observed[i] - rest[i];
    let ty = observed[i + 1] - rest[i + 1];
    let tz = observed[i + 2] - rest[i + 2];

    tx = clamp(tx, -OFFSET_LIMIT, OFFSET_LIMIT);
    ty = clamp(ty, -OFFSET_LIMIT, OFFSET_LIMIT);
    tz = clamp(tz, -OFFSET_LIMIT, OFFSET_LIMIT);

    // The facing trust, from the pre-pass above — the same dot, the same ramp.
    //
    // Weighted on the FIRST sample too, which used to adopt every vertex whole. A
    // session that begins with the head already turned would bake the hidden half in
    // from hallucinated landmarks at full trust and then hold it; weighted, the
    // unobserved half simply stays canonical until it is actually seen.
    const v = i / 3;
    const dot = facingDot[v];
    const t = clamp((dot - FACING_HOLD) / (FACING_TRUST - FACING_HOLD), 0, 1);
    let facing = t * t * (3 - 2 * t);

    // The self-occlusion hold, with the grazing slack scaled by this vertex's own
    // facing: a surface seen edge-on gets the full `VIS_GRAZE` of quantisation
    // allowance, one seen squarely almost none. See the constants above.
    const behind = visBehind[v];
    if (behind > 0) {
      const bias = VIS_BIAS + VIS_GRAZE * (1 - clamp(dot, 0, 1));
      if (behind > bias) facing *= Math.max(0, 1 - (behind - bias) / VIS_RAMP);
    }

    // Exported per vertex: the person model's accumulate reads the identical
    // facing×visibility trust the deform applies, so the two can never
    // disagree about what was observed.
    facingTrust[v] = facing;

    // The innovation, against the COMPOSITE — what is drawn is what the
    // observation is compared to, whichever layer owns it.
    const pox = personOffsets ? personOffsets[i] : 0;
    const poy = personOffsets ? personOffsets[i + 1] : 0;
    const poz = personOffsets ? personOffsets[i + 2] : 0;
    let ex = tx - pox - viewResidual[i];
    let ey = ty - poy - viewResidual[i + 1];
    let ez = tz - poz - viewResidual[i + 2];

    // The shrinkage floor (G16), on every sample after the first: an
    // innovation inside the noise floor is noise, and easing noise into a
    // face-space constant is the drift pump the rebuild storm was made of.
    // Vector shrinkage rather than a hard gate, so the passband has no cliff.
    // The FIRST sample stays adopted whole (via hasShape → alpha 1, shrink 1):
    // frame one is bit-identical to the pre-stage pipeline.
    if (data.hasShape) {
      const el = Math.hypot(ex, ey, ez);
      const r0 = Math.max(SHRINK_FLOOR, 1.5 * (resNoise ? resNoise[v] : 0));
      const shrink = el > r0 ? 1 - r0 / el : 0;
      ex *= shrink; ey *= shrink; ez *= shrink;
    }

    const rate = alpha * facing;
    let dx = ex * rate;
    let dy = ey * rate;
    let dz = ez * rate;

    // The held-vertex decay: in proportion to how much of the update this
    // vertex did NOT receive (a fully-observed vertex decays nothing), its
    // stale view residual relaxes onto the person estimate — but only as fast
    // as the model there has earned trust. See `TAU_RESID_DECAY`.
    if (personW && data.hasShape && facing < 1) {
      const k = alphaDecay * (1 - facing) * Math.min(personW[v] / CONF_HOLD, 1);
      if (k > 0) {
        dx -= viewResidual[i] * k;
        dy -= viewResidual[i + 1] * k;
        dz -= viewResidual[i + 2] * k;
      }
    }

    viewResidual[i] += dx;
    viewResidual[i + 1] += dy;
    viewResidual[i + 2] += dz;

    // The composite — the one shape the rest of the pipeline knows.
    offsets[i] = pox + viewResidual[i];
    offsets[i + 1] = poy + viewResidual[i + 1];
    offsets[i + 2] = poz + viewResidual[i + 2];

    const moved = Math.abs(dx) + Math.abs(dy) + Math.abs(dz);
    if (moved > drift) drift = moved;
  }

  // The estimator eats RAW: the observations, not the shrunk innovations —
  // shrinkage conditions the render path and must never starve the person
  // model (G16's second half, and the two-features-interacting risk retired).
  // Frozen (R0): the model holds whole — no accumulate, and with it no
  // tripwires, no decay, no zWeight ease.
  if (person && !freezeEstimators) {
    person.accumulate(observed, cameraInFace, facingTrust, wPose, dt);
  }

  data.hasShape = true;
  data.driftSurface += drift;
  data.driftProfile += drift;
}

/**
 * Rasterises the carried head into a small camera-space depth grid and scores each
 * vertex by whether anything stands between it and the lens.
 *
 * The grid is in direction space — x/depth, y/depth over the face's own bounds — so
 * a cell is a bundle of view rays, which is the geometry the question is asked in.
 * The base 468-vertex mesh is enough: the question is "is this vertex behind the
 * nose ridge", not "where exactly is the skin", and ~900 triangles into ~9k cells is
 * a fraction of what the seat field already pays per rebuild.
 *
 * Depth is interpolated barycentrically across each triangle (screen-linear rather
 * than perspective-correct, which at face scale against camera distance is an error
 * far inside `VIS_BIAS`), and the *minimum* per cell wins — the surface nearest the
 * camera, the one doing the occluding.
 */
function measureVisibility(data, face, headMatrixWorld) {
  const { visibility, restBase: rest, offsets } = data;
  const { depth, proj, vertexDepth, behind } = visibility;
  const e = headMatrixWorld.elements;
  const count = face.vertexCount;

  let minU = Infinity; let maxU = -Infinity; let minV = Infinity; let maxV = -Infinity;
  for (let i = 0; i < count; i++) {
    const x = rest[i * 3] + offsets[i * 3];
    const y = rest[i * 3 + 1] + offsets[i * 3 + 1];
    const z = rest[i * 3 + 2] + offsets[i * 3 + 2];
    const cx = e[0] * x + e[4] * y + e[8] * z + e[12];
    const cy = e[1] * x + e[5] * y + e[9] * z + e[13];
    const cz = e[2] * x + e[6] * y + e[10] * z + e[14];
    // The camera looks down -z, so depth is -cz. A vertex at or behind the camera
    // has no view ray to occlude; it scores visible and the clamps own the rest.
    const d = -cz;
    if (!(d > 1e-6)) {
      vertexDepth[i] = 0;
      continue;
    }
    const u = cx / d;
    const v = cy / d;
    proj[i * 2] = u;
    proj[i * 2 + 1] = v;
    vertexDepth[i] = d;
    if (u < minU) minU = u;
    if (u > maxU) maxU = u;
    if (v < minV) minV = v;
    if (v > maxV) maxV = v;
  }

  if (!(maxU - minU > 1e-6) || !(maxV - minV > 1e-6)) {
    behind.fill(-Infinity);
    return;
  }

  const scaleU = (VIS_GRID - 1) / (maxU - minU);
  const scaleV = (VIS_GRID - 1) / (maxV - minV);
  depth.fill(Infinity);

  const indices = face.indices;
  for (let t = 0; t < indices.length; t += 3) {
    const ia = indices[t]; const ib = indices[t + 1]; const ic = indices[t + 2];
    const da = vertexDepth[ia]; const db = vertexDepth[ib]; const dc = vertexDepth[ic];
    if (da === 0 || db === 0 || dc === 0) continue;
    const ax = (proj[ia * 2] - minU) * scaleU; const ay = (proj[ia * 2 + 1] - minV) * scaleV;
    const bx = (proj[ib * 2] - minU) * scaleU; const by = (proj[ib * 2 + 1] - minV) * scaleV;
    const cx = (proj[ic * 2] - minU) * scaleU; const cy = (proj[ic * 2 + 1] - minV) * scaleV;

    const det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if (Math.abs(det) < 1e-12) continue;

    const i0 = Math.max(0, Math.ceil(Math.min(ax, bx, cx)));
    const i1 = Math.min(VIS_GRID - 1, Math.floor(Math.max(ax, bx, cx)));
    const j0 = Math.max(0, Math.ceil(Math.min(ay, by, cy)));
    const j1 = Math.min(VIS_GRID - 1, Math.floor(Math.max(ay, by, cy)));

    for (let j = j0; j <= j1; j++) {
      for (let i = i0; i <= i1; i++) {
        const l1 = ((by - cy) * (i - cx) + (cx - bx) * (j - cy)) / det;
        if (l1 < 0) continue;
        const l2 = ((cy - ay) * (i - cx) + (ax - cx) * (j - cy)) / det;
        if (l2 < 0) continue;
        const l3 = 1 - l1 - l2;
        if (l3 < 0) continue;
        const d = l1 * da + l2 * db + l3 * dc;
        const at = j * VIS_GRID + i;
        if (d < depth[at]) depth[at] = d;
      }
    }
  }

  for (let i = 0; i < count; i++) {
    if (vertexDepth[i] === 0) {
      behind[i] = -Infinity;
      continue;
    }
    const gi = Math.round((proj[i * 2] - minU) * scaleU);
    const gj = Math.round((proj[i * 2 + 1] - minV) * scaleV);
    const front = depth[Math.min(Math.max(gj, 0), VIS_GRID - 1) * VIS_GRID
      + Math.min(Math.max(gi, 0), VIS_GRID - 1)];
    // Uncovered cell means the vertex is its own frontmost surface — silhouettes
    // land here when their cell rounds off the mesh — which is visible by definition.
    behind[i] = front === Infinity ? -Infinity : vertexDepth[i] - front;
  }
}

/**
 * Writes the deformation into the vertices, re-lofts the skull behind them, and
 * re-rasterises the field the seat queries.
 *
 * The order matters and it is the whole guarantee: `skin` is written first, the field
 * is rasterised from `skin`, and only then is the relief applied into the drawn
 * geometry. There is no path through this function that produces a field describing
 * anything other than the triangles that are about to be rendered.
 */
function rebuildSurface(occluder, anchors) {
  const data = occluder.userData;
  const { skin, restBase, base, control, offsets, subdivision, ring, faceVertexCount } = data;

  // Measured mesh, then the control mesh that subdivides *onto* it, then the smooth
  // surface, then the skull lofted from its rim. Four steps and one array comes out —
  // which is still the whole guarantee: the field below and the geometry that writes
  // the depth buffer are rasterised from these same vertices.
  for (let i = 0; i < offsets.length; i++) base[i] = restBase[i] + offsets[i];
  subdivision.compensate(base, control, data.compensated ? 3 : 8, data.compensated);
  data.compensated = true;
  subdivision.apply(control, skin);

  // The overlay shows the measured mesh, not the control mesh and not the subdivided
  // one. `control` is a pre-compensated intermediate that is deliberately *not* the
  // face — drawing it would show a sharpened caricature and mean nothing.
  if (data.debugMesh) {
    data.debugMesh.geometry.attributes.position.array.set(base);
    data.debugMesh.geometry.attributes.position.needsUpdate = true;
    data.debugMesh.geometry.computeBoundingSphere();
  }
  reloftSkull(skin, ring, faceVertexCount, anchors?.widthRatio ?? 1);

  const bridge = LM.NOSE_BRIDGE * 3;
  data.surface.rebuild(
    skin, data.windowIndices,
    [skin[bridge], skin[bridge + 1], skin[bridge + 2]],
  );

  applyRelief(occluder);

  data.driftSurface = 0;
  data.framesSinceRebuild = 0;
  data.rebuilds.surface++;
  // The rate ring `__ar.rebuilds.surfacePerMin` reads: frame indices of the
  // most recent rebuilds, enough to cover a minute at the worst legal cadence.
  const marks = data.rebuilds.marks;
  marks.push(data.frameCount);
  if (marks.length > 640) marks.splice(0, marks.length - 640);
}

/** The half-width table the temple arms are routed against, from the moved vertices. */
function rebuildProfile(occluder) {
  const data = occluder.userData;
  data.profile.rebuild(data.skin, data.indices);
  data.driftProfile = 0;
  data.rebuilds.profile++;
}

/**
 * Seats each pinna against the skull it is standing off.
 *
 * The ears live in face space rather than inside the head's translation: their rest
 * points are measured per side from this face's own landmarks, so they are already
 * where they belong and moving them with the bridge would double-count it.
 */
function placeEars(occluder, anchors) {
  const { ears, profile, shift: applied } = occluder.userData;
  if (!ears || !anchors?.ears) return;

  for (const side of ['right', 'left']) {
    const rest = anchors.ears[side];
    // The profile is rasterised from the untranslated mesh, so the query is too.
    const halfWidth = profile
      ? profile.at(rest.y - applied.y, rest.z - applied.z)
      : Math.abs(rest.x);
    ears[side].position.copy(
      pinnaPlacement(rest, Math.max(halfWidth, 0), side === 'right' ? -1 : 1),
    );
  }
}

/**
 * The depth field the frame is seated against — the occluder's own, always.
 *
 * `fit.js` takes this in preference to `face.surface`, and that substitution is the
 * entire fix. `face.surface` is the canonical nose and is now only a fallback for
 * callers with no occluder at all.
 */
export function surfaceOf(occluder) {
  return occluder?.userData?.surface ?? null;
}

/**
 * How wide the head is at a point in face space — the surface the temple arms are
 * routed against. Null when there is no occluder.
 *
 * Reads the profile the occluder is *currently drawn at*, translation included, so the
 * arms and the thing that hides them can never be solved against two different heads.
 */
export function headProfileFor(occluder) {
  const data = occluder?.userData;
  if (!data?.profile) return null;
  const applied = data.shift;
  return { at: (y, z) => data.profile.at(y - applied.y, z - applied.z) };
}

/**
 * A head-shaped shadow receiver.
 *
 * Instead of writing depth it shows the key light's shadow — the glasses darken the
 * face they rest on, which is what visually attaches them to it. `ShadowMaterial` is
 * transparent everywhere the shadow is not, so the camera feed shows through
 * untouched. Drawn in the transparent pass with depth testing on, so it never paints
 * over the frame itself, and with no depth write, so it cannot occlude anything.
 *
 * It shares the occluder's geometry *instance* rather than taking a copy, and that is
 * load-bearing. The two must be the same surface to within nothing at all: the
 * catcher is depth-tested against the occluder, so an occluder even a tenth of a
 * millimetre in front of it culls the shadow entirely. A copy would be identical
 * until the first time the head took a measured shape — which is how the shadow
 * disappeared the first time this was tried.
 *
 * It registers itself on the occluder so `updateOccluder` can keep its translation in
 * step, for the same reason. It stays off `OCCLUDER_LAYER`: the depth pre-pass wants
 * the head's depth and nothing else.
 */
export function createShadowCatcher(occluder, { opacity = 0.18 } = {}) {
  const material = new THREE.ShadowMaterial({ opacity });
  material.depthWrite = false;

  const mesh = new THREE.Mesh(occluder.userData.head.geometry, material);
  mesh.receiveShadow = true;
  mesh.position.copy(occluder.userData.shift);
  occluder.userData.shadowCatcher = mesh;
  return mesh;
}

/** The face triangles whose canonical position puts them within reach of the field. */
function selectWindowTriangles(positions, indices) {
  const margin = OFFSET_LIMIT;
  const kept = [];
  for (let t = 0; t < indices.length; t += 3) {
    let inside = false;
    for (let v = 0; v < 3 && !inside; v++) {
      const at = indices[t + v] * 3;
      const x = positions[at];
      const y = positions[at + 1];
      inside = x > WINDOW.minX - margin && x < WINDOW.maxX + margin
        && y > WINDOW.minY - margin && y < WINDOW.maxY + margin;
    }
    if (inside) kept.push(indices[t], indices[t + 1], indices[t + 2]);
  }
  return new Uint32Array(kept);
}

/**
 * A wireframe of the head the occluder is *actually* using, over the video.
 *
 * This replaces a debug mesh that drew the canonical average face from a copy taken at
 * boot and never looked at the occluder again. That was fine when the occluder was the
 * canonical average face. It stopped being true the moment the head started being
 * deformed onto the wearer, and a debug overlay that lies is worse than none: it was
 * showing the average nose sitting off the real one and inviting exactly the wrong
 * conclusion.
 *
 * Drawn at the *base* 468-vertex topology rather than the subdivided one. The subdivided
 * mesh is 17,680 triangles and its wireframe at close range is a solid green wash — the
 * question this answers is "is the model on my face", and 898 readable triangles answer
 * it where 17,680 unreadable ones do not.
 *
 * `depthTest: false` on purpose: this has to be visible over the frame it is diagnosing.
 */
export function createOccluderDebugMesh(occluder) {
  const data = occluder.userData;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(data.base.length), 3));
  geometry.setIndex(new THREE.BufferAttribute(Uint32Array.from(data.face.indices), 1));

  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
    color: 0x4ade80,
    wireframe: true,
    transparent: true,
    opacity: 0.45,
    depthTest: false,
    toneMapped: false,
  }));
  mesh.renderOrder = 10;
  mesh.visible = false;
  mesh.position.copy(data.shift);
  data.debugMesh = mesh;
  // Whatever the current deformation is, not an empty buffer until the next rebuild.
  geometry.attributes.position.array.set(data.base);
  geometry.attributes.position.needsUpdate = true;
  return mesh;
}

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

export const OCCLUDER_CONSTANTS = {
  OCCLUDER_FEATHER, OCCLUDER_RELIEF, SHAPE_TAU, OFFSET_LIMIT, DEPTH_LIMIT,
  DEPTH_FIT_ZERO_R2, DEPTH_FIT_FULL_R2,
  DEPTH_EMA_TAU, NOSE_RESID_ZERO, NOSE_RESID_FULL, NOSE_BOX,
  SURFACE_DEADBAND, PROFILE_DEADBAND, SUBDIVISION_LEVELS,
  VIS_GRID, VIS_BIAS, VIS_RAMP, VIS_GRAZE,
  REBUILD_MIN_INTERVAL, REBUILD_BYPASS, SHRINK_FLOOR,
  TAU_RESID_DECAY, CONF_HOLD, RELIEF_DEADBAND_PX,
};
