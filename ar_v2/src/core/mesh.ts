/**
 * The template mesh, its topology, and the regions the rest of the tree names.
 *
 * The topology is MediaPipe's canonical face model — 468 vertices — because
 * that is the topology the landmark detector speaks, and a reconstruction whose
 * vertices are not the detector's landmarks needs a correspondence step that can
 * itself be wrong. Identity shape rides on top of this topology (see
 * `shape/basis.ts`); it is not the shape model itself.
 *
 * **Everything here is millimetres.** The .obj on disk is centimetres, and the
 * conversion happens once, at parse, so no consumer ever has to ask.
 *
 * ## What a "region" is for
 *
 * Three things in this tree need to talk about *part* of the face: the
 * free-form displacement field (which is allowed to move the nose and nothing
 * else), the contact solver (which cares about the nasal sidewall strip a pad
 * bears on), and the reporting (which grades accuracy per region, because a
 * millimetre on the forehead and a millimetre on the bridge are not the same
 * millimetre). Regions are computed from the mesh's own geometry rather than
 * hand-listed, so they survive a template swap.
 */

import { type Vec3, v3, vcross, vnormalize, vsub } from './linalg.js';

/** Landmark indices we rely on. Stable across MediaPipe face-mesh versions. */
export const LM = {
  NOSE_TIP: 4,
  SUBNASALE: 2,
  /** Between the pupils, on the bridge. Where pads land on the average face. */
  NOSE_BRIDGE: 6,
  /** The dip between the brows, higher up the bridge. */
  NASION: 168,
  GLABELLA: 9,
  BRIDGE_LOW: 197,
  /** Nasal sidewall, upper and lower per side — the strip a pad bears on. */
  NOSE_WALL_HIGH_R: 245,
  NOSE_WALL_HIGH_L: 465,
  NOSE_WALL_LOW_R: 114,
  NOSE_WALL_LOW_L: 343,
  NOSE_ALA_R: 129,
  NOSE_ALA_L: 358,
  EYE_OUTER_R: 33,
  EYE_OUTER_L: 263,
  EYE_INNER_R: 133,
  EYE_INNER_L: 362,
  /** Iris: detector-only refinement to 478. There is no mesh vertex for these. */
  IRIS_R_CENTRE: 468,
  IRIS_R_CONTOUR: [469, 470, 471, 472] as const,
  IRIS_L_CENTRE: 473,
  IRIS_L_CONTOUR: [474, 475, 476, 477] as const,
  TEMPLE_R: 127,
  TEMPLE_L: 356,
  EAR_TOP_R: 162,
  EAR_TOP_L: 389,
  CHEEK_R: 234,
  CHEEK_L: 454,
  CHIN: 152,
  FOREHEAD: 10,
} as const;

/** The first index that exists only in the detector's output, not in the mesh. */
export const FIRST_IRIS_INDEX = 468;
/** Vertices in the template mesh. The detector reports 10 more (the irises). */
export const MESH_VERTEX_COUNT = 468;
export const DETECTOR_LANDMARK_COUNT = 478;

export interface FaceMesh {
  /** Vertex positions, mm, length 3 * vertexCount. */
  positions: Float64Array;
  indices: Uint32Array;
  vertexCount: number;
  triangleCount: number;
  /** Flattened adjacency: neighbours of vertex i are
   *  `adjacency[adjacencyStart[i] .. adjacencyStart[i+1]]`. */
  adjacency: Uint32Array;
  adjacencyStart: Uint32Array;
}

// ------------------------------------------------------------------ parsing

const CM_TO_MM = 10;

/**
 * Parses the canonical .obj.
 *
 * Only positions and triangle indices are taken. The file carries texture
 * coordinates on a separate index set; nothing here is textured from the
 * template, and the vertex/texture index mismatch is exactly the trap that makes
 * naive .obj readers produce a mesh with the right vertices and the wrong faces.
 */
export function parseFaceObj(text: string, scale = CM_TO_MM): FaceMesh {
  const positions: number[] = [];
  const indices: number[] = [];

  for (const rawLine of text.split('\n')) {
    const line = rawLine.trimEnd();
    if (line.charCodeAt(0) === 118 /* v */ && line.charCodeAt(1) === 32) {
      const p = line.split(/\s+/);
      positions.push(+p[1] * scale, +p[2] * scale, +p[3] * scale);
    } else if (line.charCodeAt(0) === 102 /* f */ && line.charCodeAt(1) === 32) {
      const p = line.trim().split(/\s+/);
      // "f v/vt v/vt v/vt" — the vertex index is before the slash, 1-based.
      // Fans anything with more than three corners, though this file is
      // triangulated already.
      const corner = (s: string) => parseInt(s, 10) - 1;
      for (let i = 2; i + 1 < p.length; i++) {
        indices.push(corner(p[1]), corner(p[i]), corner(p[i + 1]));
      }
    }
  }

  const vertexCount = positions.length / 3;
  if (vertexCount !== 468) {
    throw new Error(`canonical face mesh: expected 468 vertices, parsed ${vertexCount}`);
  }

  const mesh: FaceMesh = {
    positions: Float64Array.from(positions),
    indices: Uint32Array.from(indices),
    vertexCount,
    triangleCount: indices.length / 3,
    adjacency: new Uint32Array(0),
    adjacencyStart: new Uint32Array(0),
  };
  buildAdjacency(mesh);
  return mesh;
}

export function buildAdjacency(mesh: FaceMesh): void {
  const sets: Set<number>[] = Array.from({ length: mesh.vertexCount }, () => new Set<number>());
  const idx = mesh.indices;
  for (let t = 0; t < idx.length; t += 3) {
    const a = idx[t], b = idx[t + 1], c = idx[t + 2];
    sets[a].add(b); sets[a].add(c);
    sets[b].add(a); sets[b].add(c);
    sets[c].add(a); sets[c].add(b);
  }
  const start = new Uint32Array(mesh.vertexCount + 1);
  let total = 0;
  for (let i = 0; i < mesh.vertexCount; i++) { start[i] = total; total += sets[i].size; }
  start[mesh.vertexCount] = total;
  const flat = new Uint32Array(total);
  let w = 0;
  for (let i = 0; i < mesh.vertexCount; i++) for (const j of sets[i]) flat[w++] = j;
  mesh.adjacency = flat;
  mesh.adjacencyStart = start;
}

export function cloneMesh(mesh: FaceMesh): FaceMesh {
  return {
    positions: new Float64Array(mesh.positions),
    indices: mesh.indices,
    vertexCount: mesh.vertexCount,
    triangleCount: mesh.triangleCount,
    adjacency: mesh.adjacency,
    adjacencyStart: mesh.adjacencyStart,
  };
}

// ------------------------------------------------------------------ normals

/** Area-weighted vertex normals. `out` is 3 * vertexCount. */
export function computeVertexNormals(
  positions: Float64Array, indices: Uint32Array, vertexCount: number,
  out = new Float64Array(vertexCount * 3),
): Float64Array {
  out.fill(0);
  const e1 = v3(), e2 = v3(), n = v3();
  for (let t = 0; t < indices.length; t += 3) {
    const a = indices[t] * 3, b = indices[t + 1] * 3, c = indices[t + 2] * 3;
    e1[0] = positions[b] - positions[a];
    e1[1] = positions[b + 1] - positions[a + 1];
    e1[2] = positions[b + 2] - positions[a + 2];
    e2[0] = positions[c] - positions[a];
    e2[1] = positions[c + 1] - positions[a + 1];
    e2[2] = positions[c + 2] - positions[a + 2];
    vcross(n, e1, e2); // length is twice the triangle area — the weighting we want
    out[a] += n[0]; out[a + 1] += n[1]; out[a + 2] += n[2];
    out[b] += n[0]; out[b + 1] += n[1]; out[b + 2] += n[2];
    out[c] += n[0]; out[c + 1] += n[1]; out[c + 2] += n[2];
  }
  const tmp = v3();
  for (let i = 0; i < vertexCount; i++) {
    tmp[0] = out[i * 3]; tmp[1] = out[i * 3 + 1]; tmp[2] = out[i * 3 + 2];
    vnormalize(tmp, tmp);
    out[i * 3] = tmp[0]; out[i * 3 + 1] = tmp[1]; out[i * 3 + 2] = tmp[2];
  }
  return out;
}

/** Geometric normal of one triangle, not normalised by area. */
export function triangleNormal(
  out: Vec3, positions: ArrayLike<number>, a: number, b: number, c: number,
): Vec3 {
  const e1 = v3(), e2 = v3(), pa = v3(), pb = v3(), pc = v3();
  pa[0] = positions[a * 3]; pa[1] = positions[a * 3 + 1]; pa[2] = positions[a * 3 + 2];
  pb[0] = positions[b * 3]; pb[1] = positions[b * 3 + 1]; pb[2] = positions[b * 3 + 2];
  pc[0] = positions[c * 3]; pc[1] = positions[c * 3 + 1]; pc[2] = positions[c * 3 + 2];
  vsub(e1, pb, pa);
  vsub(e2, pc, pa);
  vcross(out, e1, e2);
  return vnormalize(out, out);
}

// ------------------------------------------------------------------ regions

export interface Region {
  readonly name: string;
  /** 1 inside, 0 outside, fractional in the feather band. */
  readonly weight: Float64Array;
  /** Indices with weight > 0, for iteration without scanning the whole mesh. */
  readonly members: Uint32Array;
}

/**
 * A soft region grown from seed vertices by graph distance, feathered at the rim.
 *
 * Feathered rather than binary, and that is load-bearing for the displacement
 * field: a hard region boundary lets the free-form solve create a step in the
 * surface exactly at the edge of what it is allowed to move, and a step on the
 * side of the nose is a visible crease under a nose pad. The feather makes the
 * displacement fall to zero smoothly, so the patch blends into the shape basis
 * instead of butting against it.
 */
export function growRegion(
  mesh: FaceMesh, name: string, seeds: number[], radiusMm: number, featherMm: number,
): Region {
  const dist = new Float64Array(mesh.vertexCount).fill(Infinity);
  const queue: number[] = [];
  for (const s of seeds) { dist[s] = 0; queue.push(s); }

  // Dijkstra on edge lengths. The mesh is small enough that a simple
  // linear-scan frontier beats a heap in practice and is easier to trust.
  const visited = new Uint8Array(mesh.vertexCount);
  const limit = radiusMm + featherMm;
  for (;;) {
    let best = -1, bestD = Infinity;
    for (const q of queue) if (!visited[q] && dist[q] < bestD) { bestD = dist[q]; best = q; }
    if (best < 0 || bestD > limit) break;
    visited[best] = 1;
    const s0 = mesh.adjacencyStart[best], s1 = mesh.adjacencyStart[best + 1];
    for (let a = s0; a < s1; a++) {
      const nb = mesh.adjacency[a];
      const d = bestD + edgeLength(mesh.positions, best, nb);
      if (d < dist[nb]) { dist[nb] = d; if (!visited[nb]) queue.push(nb); }
    }
  }

  const weight = new Float64Array(mesh.vertexCount);
  const members: number[] = [];
  for (let i = 0; i < mesh.vertexCount; i++) {
    const d = dist[i];
    if (!(d <= limit)) continue;
    const w = d <= radiusMm ? 1 : 1 - (d - radiusMm) / Math.max(featherMm, 1e-6);
    if (w > 1e-4) { weight[i] = w * w * (3 - 2 * w); members.push(i); }
  }
  return { name, weight, members: Uint32Array.from(members) };
}

const edgeLength = (p: Float64Array, a: number, b: number): number =>
  Math.hypot(p[a * 3] - p[b * 3], p[a * 3 + 1] - p[b * 3 + 1], p[a * 3 + 2] - p[b * 3 + 2]);

/**
 * The regions this project names, all derived from the template's own geometry.
 *
 * The radii are stated, not derived, and are recorded as such in
 * `docs/CONSTANTS.md`. They were chosen by rendering the masks and looking at
 * them, which is an honest provenance for a *selection* — a region is a
 * statement about which part of the face a solver may touch, and there is no
 * measurement that decides it.
 */
export function standardRegions(mesh: FaceMesh): Record<string, Region> {
  return {
    /** Everything the free-form displacement field is allowed to move. Covers
     *  the bridge, both sidewalls, the tip and the upper alae — the whole
     *  surface a frame can possibly touch, plus a margin. */
    nose: growRegion(mesh, 'nose', [
      LM.NOSE_BRIDGE, LM.NASION, LM.BRIDGE_LOW, LM.NOSE_TIP, LM.SUBNASALE,
      LM.NOSE_WALL_HIGH_R, LM.NOSE_WALL_HIGH_L, LM.NOSE_WALL_LOW_R, LM.NOSE_WALL_LOW_L,
    ], 15, 6),

    /** The strip a nose pad actually bears on. Used by the contact solver and
     *  reported separately, because this is the surface the wearer's complaint
     *  was about. */
    padStrip: growRegion(mesh, 'padStrip', [
      LM.NOSE_WALL_HIGH_R, LM.NOSE_WALL_HIGH_L, LM.NOSE_WALL_LOW_R, LM.NOSE_WALL_LOW_L,
    ], 7, 4),

    bridge: growRegion(mesh, 'bridge', [LM.NOSE_BRIDGE, LM.NASION, LM.BRIDGE_LOW], 9, 5),
    eyes: growRegion(mesh, 'eyes', [
      LM.EYE_OUTER_R, LM.EYE_OUTER_L, LM.EYE_INNER_R, LM.EYE_INNER_L,
    ], 18, 6),
    // The silhouette ring of this mesh is coarse — mean edge 9 mm, and far
    // coarser at the rim — so these radii are larger than the anatomical region
    // to reach a workable number of vertices. Sizes are asserted in
    // `tests/regions.test.ts` so a template swap cannot silently empty one.
    temples: growRegion(mesh, 'temples', [LM.TEMPLE_R, LM.TEMPLE_L], 30, 12),
    cheeks: growRegion(mesh, 'cheeks', [LM.CHEEK_R, LM.CHEEK_L], 32, 12),
  };
}

// --------------------------------------------------------------- measurement

export function vertex(out: Vec3, positions: ArrayLike<number>, i: number): Vec3 {
  out[0] = positions[i * 3]; out[1] = positions[i * 3 + 1]; out[2] = positions[i * 3 + 2];
  return out;
}

export function vertexDistance(positions: ArrayLike<number>, a: number, b: number): number {
  return Math.hypot(
    positions[a * 3] - positions[b * 3],
    positions[a * 3 + 1] - positions[b * 3 + 1],
    positions[a * 3 + 2] - positions[b * 3 + 2],
  );
}

/**
 * Nose width across the strip a pad bears on, mm.
 *
 * The mean of two spans rather than one: a single pair puts the whole
 * measurement on one landmark's noise, and the two pairs sit about a centimetre
 * apart up the sidewall — which is the same centimetre a pad occupies. Ported
 * from v1, where the reasoning was right even though what it fed was not.
 */
export const noseSpan = (highR: number, highL: number, lowR: number, lowL: number): number =>
  (Math.abs(highL - highR) + Math.abs(lowL - lowR)) / 2;

/**
 * A summary of the fit-relevant anthropometry of a mesh, in mm.
 *
 * Every one of these is something an optician or a frame designer would
 * recognise. That is deliberate: it makes the numbers checkable against
 * published anthropometry and against a caliper, which is the only way out of
 * the n=1 problem the v1 audit found.
 */
export interface FaceMeasurements {
  templeWidth: number;
  noseWidth: number;
  /**
   * Nasal root depth: the nasion forward of the plane through the inner eye
   * corners, mm.
   *
   * The anthropometric quantity, and the one that matters here — it is how far
   * the bridge stands proud of the eye plane, which with `bridgeStandoff` is
   * what decides whether a frame's own bridge clears the face at all. On the
   * template it is 14.8 mm.
   *
   * An earlier draft of this file measured the bridge against the *temple*
   * landmarks and got 78 mm, which is a real span and a useless one: it is
   * mostly the depth of the whole head, so every depth mode scaled off it came
   * out with a standard deviation five times too large. Kept in the history
   * because it is the exact shape of error a "derived" constant can have while
   * looking derived.
   */
  nasalRootDepth: number;
  /** The pad-bearing bridge landmark forward of the inner eye corners, mm. */
  bridgeStandoff: number;
  /** Nose tip forward of the bridge. The single number a borrowed depth gets
   *  most wrong. */
  noseProtrusion: number;
  /** Height of the bridge landmark above the subnasale. */
  bridgeHeight: number;
  /** Interpupillary-ish: outer eye corner separation (the mesh has no pupil). */
  outerEyeSpan: number;
  /**
   * Half-angle of the nasal sidewall wedge, radians.
   *
   * The slope at which the nose widens as you descend it. This is the number
   * behind v1's hardest-won insight: a frame whose pads are too far apart does
   * not float, it *slides down* until the wall is wide enough to catch them.
   * `tan(sidewallAngle)` is millimetres of half-width per millimetre of descent.
   */
  sidewallAngle: number;
}

export function measure(positions: Float64Array): FaceMeasurements {
  const p = (i: number, c: number) => positions[i * 3 + c];

  const templeWidth = vertexDistance(positions, LM.TEMPLE_R, LM.TEMPLE_L);
  const noseWidth = noseSpan(
    p(LM.NOSE_WALL_HIGH_R, 0), p(LM.NOSE_WALL_HIGH_L, 0),
    p(LM.NOSE_WALL_LOW_R, 0), p(LM.NOSE_WALL_LOW_L, 0),
  );
  const innerCanthiZ = (p(LM.EYE_INNER_R, 2) + p(LM.EYE_INNER_L, 2)) / 2;

  // Half-width grows as you descend the wedge; the angle is that slope.
  const highHalf = Math.abs(p(LM.NOSE_WALL_HIGH_L, 0) - p(LM.NOSE_WALL_HIGH_R, 0)) / 2;
  const lowHalf = Math.abs(p(LM.NOSE_WALL_LOW_L, 0) - p(LM.NOSE_WALL_LOW_R, 0)) / 2;
  const dropY =
    (p(LM.NOSE_WALL_HIGH_R, 1) + p(LM.NOSE_WALL_HIGH_L, 1)) / 2 -
    (p(LM.NOSE_WALL_LOW_R, 1) + p(LM.NOSE_WALL_LOW_L, 1)) / 2;

  return {
    templeWidth,
    noseWidth,
    nasalRootDepth: p(LM.NASION, 2) - innerCanthiZ,
    bridgeStandoff: p(LM.NOSE_BRIDGE, 2) - innerCanthiZ,
    noseProtrusion: p(LM.NOSE_TIP, 2) - p(LM.NOSE_BRIDGE, 2),
    bridgeHeight: p(LM.NOSE_BRIDGE, 1) - p(LM.SUBNASALE, 1),
    outerEyeSpan: vertexDistance(positions, LM.EYE_OUTER_R, LM.EYE_OUTER_L),
    sidewallAngle: Math.atan2(lowHalf - highHalf, Math.max(Math.abs(dropY), 1e-6)),
  };
}
