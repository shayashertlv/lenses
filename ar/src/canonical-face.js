/**
 * The canonical face model — MediaPipe's average human head.
 *
 * MediaPipe's face landmarker returns a "facial transformation matrix" that maps
 * THIS mesh onto the face it just saw in the camera frame. So the mesh is the
 * bridge between the two worlds: anything we position relative to its vertices is
 * positioned relative to the real face, at any pose, for free.
 *
 * Units are centimetres. Axes are +X to the subject's left, +Y up, +Z out of the
 * face — the same handedness three.js uses, so no conversion is needed.
 */

import { buildFaceSurface } from './nose.js';

/** Vertex indices whose meaning we rely on. Stable across MediaPipe versions. */
export const LM = {
  NOSE_TIP: 4,
  SUBNASALE: 2,
  /** Between the pupils, on the bridge of the nose. Where a frame's pads land. */
  NOSE_BRIDGE: 6,
  /** Slightly higher up the bridge — the dip between the brows. */
  NASION: 168,
  GLABELLA: 9,
  /** Lower down the bridge, toward the tip. */
  BRIDGE_LOW: 197,
  /**
   * The sides of the nose where a pad bears, upper and lower, per side.
   *
   * Chosen off the mesh rather than from a diagram: 245/465 sit 10.3 mm out at the
   * height of the bridge landmark and 114/343 sit 13.0 mm out a centimetre lower,
   * which brackets the strip of nasal sidewall every pad on every frame lands in.
   * Both pairs are medial to the inner eye corners and well forward of them, so they
   * are on the nose rather than in the orbit.
   */
  NOSE_WALL_HIGH_R: 245,
  NOSE_WALL_HIGH_L: 465,
  NOSE_WALL_LOW_R: 114,
  NOSE_WALL_LOW_L: 343,
  EYE_OUTER_R: 33,
  EYE_OUTER_L: 263,
  EYE_INNER_R: 133,
  EYE_INNER_L: 362,
  /**
   * The iris, per eye: a centre and its four contour points.
   *
   * These exist only in the *detector's* output, which refines to 478 points — the
   * canonical .obj stops at 468 and has no iris geometry at all. So they are only
   * ever read off `detection.landmarks`, never off `face.positions`, and anything
   * indexing the mesh with them is a bug.
   *
   * They are here because the iris is the one absolute ruler on a face. Everything
   * else this pipeline measures is a *ratio* against the canonical head, which is
   * all a single camera can honestly recover — see `measureFaceWidthRatio`. The
   * iris breaks that tie: its diameter is 11.7 mm on essentially every adult, so a
   * span measured against it in the same image comes out in real centimetres.
   */
  IRIS_R_CENTRE: 468,
  IRIS_R_CONTOUR: [469, 470, 471, 472],
  IRIS_L_CENTRE: 473,
  IRIS_L_CONTOUR: [474, 475, 476, 477],
  /** Widest points of the face silhouette, at brow height. Frame width matches this. */
  TEMPLE_R: 127,
  TEMPLE_L: 356,
  /** Top of the ear, where a temple arm bends over. */
  EAR_TOP_R: 162,
  EAR_TOP_L: 389,
  CHEEK_R: 234,
  CHEEK_L: 454,
  CHIN: 152,
  FOREHEAD: 10,
};

/**
 * The rigid subset — the vertices the per-frame pose refit stands on
 * (anchoring-v3 candidate B, `pose-fit.js`; spec: ar/docs/nose-v3/v3-rethink.txt
 * §B.1). The refit re-derives the head pose each frame from THESE landmarks
 * only, so membership is the whole defence: a landmark in this table must move
 * with the SKULL — not with the eyes, not with expression — or the refit
 * inherits exactly the coupling it exists to remove.
 *
 * Every inclusion and exclusion is decided by measurement on the wearer's own
 * telemetry (the 2026-08-17 attribution run, spec.md Stage-6/R0 notes), not by
 * anatomy-book intuition:
 *
 *  - **Eye corners 33/263/133/362 — INCLUDED.** The community's generic worry
 *    is that everything near the eye follows gaze; measured live on a held
 *    still head under deliberate eye circles, this detector's corners moved
 *    0.00 mm while the nose BRIDGE swung 2.3 mm mean / 4 mm peaks. The corners
 *    are bony-landmark stable and sit at the exact image region the fit most
 *    needs constrained (the glasses hang between them).
 *  - **Nose core 4/6/129/358 — EXCLUDED.** MediaPipe's bridge/tip landmarks
 *    follow the eyes (upstream: google/mediapipe issue #1786; the live
 *    measurement above). MediaPipe's own Procrustes weighting trusts these
 *    vertices HIGHEST — which is precisely why its matrix carries a gaze
 *    residual (frozen-pass eye-circles RMS 3.49 px vs the 2 px bar) and why
 *    this table must not.
 *  - **All eyelids, irises, brows, lips — EXCLUDED.** Eyelids/irises are the
 *    gaze instrument itself; brows and lips are expression's fastest movers.
 *  - **The rest — temples, cheeks, jaw, forehead, chin, under-nose,
 *    philtrum — INCLUDED** per the v3 research: skull-following regions with
 *    wide geometric spread (the fit needs leverage in every axis), each far
 *    from the measured gaze field (the injection instrument displaces nose
 *    landmarks only; these held still). The under-nose pair (2/164) rides the
 *    maxilla, not the lip vermilion — talking moves it fractionally, and the
 *    Huber loss (δ = 2 px) bounds what a moving mouth can charge the fit.
 */
export const RIGID_SUBSET = [
  // eye corners — measured 0.00 mm under gaze (outer R/L, inner R/L)
  33, 263, 133, 362,
  // temples — the silhouette anchors the width rides
  127, 356,
  // cheeks — malar surface, upper and mid
  234, 454, 205, 425,
  // jaw — the mandible's silhouette at mid-height
  136, 365,
  // forehead — centre and both brows' bony shelf above the expression zone
  10, 54, 284, 67, 297,
  // chin — pogonion and the point above it
  199, 152,
  // under-nose / philtrum — maxilla-following, below the gaze field
  2, 164,
];

/**
 * The horizontal diameter of the human iris, in centimetres.
 *
 * 11.7 mm, and remarkably invariant — it is the constant MediaPipe's own iris work
 * uses to recover depth from a single camera, where it measured 4.3% mean relative
 * error against ground truth over 200+ participants. It varies by about ±0.5 mm
 * across adults and essentially not at all with age past childhood, which is why it
 * beats every other facial dimension as a scale reference.
 */
export const IRIS_DIAMETER_CM = 1.17;

/**
 * Plausible pupil separation, in centimetres.
 *
 * Adult PD runs about 5.4–7.4 cm and a child's from roughly 4.8. The bounds are
 * wider than that on both sides because their job is to refuse a *measurement
 * failure* — a half-closed eye, a specular highlight, an iris the detector put on
 * the eyelid — not to police anybody's face.
 */
export const PD_RANGE_CM = [4.6, 8.0];

const OBJ_URL = new URL('../assets/face/canonical_face_model.obj', import.meta.url);

let cached = null;

/**
 * Parses the canonical model .obj.
 *
 * We only take positions and triangle indices. The .obj carries texture
 * coordinates on a separate index set, but nothing here is textured — the mesh is
 * only ever an invisible depth-writing occluder or a debug wireframe.
 */
export async function loadCanonicalFace() {
  if (cached) return cached;

  const text = await fetch(OBJ_URL).then((r) => {
    if (!r.ok) throw new Error(`canonical_face_model.obj: HTTP ${r.status}`);
    return r.text();
  });

  const positions = [];
  const indices = [];

  for (const line of text.split('\n')) {
    if (line[0] === 'v' && line[1] === ' ') {
      const p = line.split(/\s+/);
      positions.push(+p[1], +p[2], +p[3]);
    } else if (line[0] === 'f' && line[1] === ' ') {
      const p = line.trim().split(/\s+/);
      // "f v/vt v/vt v/vt" — the vertex index is the part before the slash, 1-based.
      indices.push(
        parseInt(p[1], 10) - 1,
        parseInt(p[2], 10) - 1,
        parseInt(p[3], 10) - 1,
      );
    }
  }

  const vertexCount = positions.length / 3;
  if (vertexCount !== 468) {
    throw new Error(`canonical face model: expected 468 vertices, parsed ${vertexCount}`);
  }

  cached = {
    positions: new Float32Array(positions),
    indices: new Uint16Array(indices),
    vertexCount,
    /** Copies vertex `i` into `[x, y, z]`. */
    point(i, out = [0, 0, 0]) {
      out[0] = this.positions[i * 3];
      out[1] = this.positions[i * 3 + 1];
      out[2] = this.positions[i * 3 + 2];
      return out;
    },
    /** Straight-line distance between two vertices, in cm. */
    distance(a, b) {
      const p = this.point(a);
      const q = this.point(b);
      return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
    },
  };

  // Reference measurements of the average head, used to size frames onto it.
  cached.templeWidth = cached.distance(LM.TEMPLE_R, LM.TEMPLE_L); // 15.49 cm
  cached.eyeLineY = (cached.point(LM.EYE_OUTER_R)[1] + cached.point(LM.EYE_OUTER_L)[1]) / 2;
  cached.noseWidth = noseSpan(
    cached.point(LM.NOSE_WALL_HIGH_R)[0], cached.point(LM.NOSE_WALL_HIGH_L)[0],
    cached.point(LM.NOSE_WALL_LOW_R)[0], cached.point(LM.NOSE_WALL_LOW_L)[0],
  ); // 2.33 cm


  // The front surface, as a depth field, for seating a frame on the nose rather
  // than hanging it off a landmark. Built once — 898 triangles into an 11k-cell
  // grid — because it is the same average head every session.
  cached.surface = buildFaceSurface({
    positions: cached.positions,
    indices: cached.indices,
    origin: cached.point(LM.NOSE_BRIDGE),
  });

  return cached;
}

/**
 * How wide the nose is across the strip a pad bears on, from the four sidewall x's.
 *
 * The mean of two spans rather than one, because a single pair puts the whole
 * measurement on one landmark's worth of noise, and the two pairs sit a centimetre
 * apart up the sidewall — which is the same centimetre a pad occupies. Shared by the
 * canonical head and the measured face so the ratio between them means something.
 */
export const noseSpan = (highR, highL, lowR, lowL) => (
  (Math.abs(highL - highR) + Math.abs(lowL - lowR)) / 2
);
