/**
 * A head, to hide the temple arms behind.
 *
 * MediaPipe gives a *face*: 468 vertices that stop at the silhouette, whose
 * rearmost point on this template is **24.4 mm** behind the origin, and which
 * has no ears at all. navigator's temples run back to an ear rest at z = -96 mm,
 * so **72 mm of a 96 mm arm is past the last vertex the face mesh owns**. For
 * most of its length the arm is drawn against nothing.
 *
 * ## The trick, stated plainly
 *
 * **The face mesh's own boundary is a closed 36-vertex loop around the face
 * oval, and lofting it backwards to an occipital pole closes the head.**
 * Verified on this template: every boundary vertex has exactly two boundary
 * neighbours and the walk returns 36 of them, so it is one loop and not several
 * arcs.
 *
 * The loft starts at the mesh's own rim and SHARES its vertices, so the result
 * is watertight — there is no seam for an arm to appear through, and no
 * clearance constant deciding which artefact to prefer. From the rim forwards
 * the head IS the tracked face; from the rim backwards it is a skull that keeps
 * the face's own width and takes the head's real length.
 *
 * This is v1's design, ported. What is NOT ported is v1's `buildHeadProfile` /
 * `rasteriseWidth` — a half-width table for ROUTING temple arms around the head.
 * v2 draws straight hinge-to-ear temples (the documented Q6 approximation) and
 * routes nothing, so a table nothing reads would be code with no caller.
 *
 * ## Units
 *
 * **Millimetres.** v1's head.js is in centimetres, and every length here is its
 * value times ten. That conversion is the same trap the shadow frustum carried:
 * a copied constant is silently a tenth of the intended size and the symptom is
 * a proxy that does nothing rather than an error.
 */

/**
 * How far behind the face rim's own centre the occipital pole sits, mm.
 *
 * `measured`, on this template rather than chosen. The boundary ring's centre is
 * at z = **9.56 mm** and the nose tip (vertex 4) at z = **75.87 mm**, so a pole
 * 140 mm behind the ring centre lands at z = -130.4 and gives a head **206.3 mm
 * from nose to occiput**. Subtracting the nose's own protrusion puts glabella to
 * occiput at about 186 mm, which is an adult head — the population this mesh is
 * an average of.
 *
 * The sweep, on the same template:
 *
 *     depth mm   pole z    nose-to-occiput mm
 *       120      -110.4          186.3
 *       130      -120.4          196.3
 *       140      -130.4          206.3
 *       150      -140.4          216.3
 *
 * These reproduce v1's own readings (ring centre 9.6, nose tip 75.9) because it
 * is the same canonical mesh — which is the check that the port is on the same
 * geometry, not a coincidence.
 */
export const SKULL_DEPTH_MM = 140;

/**
 * How square the skull's sweep is, as the exponent of a superellipse.
 *
 * `measured`, and a circular sweep is wrong in a way that matters here. The
 * half-width retained at the ear's depth (z = -41.7 mm, where `earRestPoints`
 * puts the temple's rest), on this template:
 *
 *     exponent   width at the ear
 *       2.0           93.1%
 *       2.5           96.7%
 *       3.0           98.3%
 *       3.5           99.1%
 *       4.0           99.5%
 *
 * A real head is still at ~97% there, and the missing 5 mm at exponent 2.0 is
 * the whole clearance a temple arm has — an arm routed against a head that
 * narrows too early sits outside it and never gets hidden. 3.5 holds the width
 * to 99% at the ear and still closes cleanly at the occiput.
 */
export const SKULL_FULLNESS = 3.5;

/** Rings in the loft. Twelve is smooth enough that the silhouette has no facets. */
export const SKULL_RINGS = 12;

/**
 * The pinna, in millimetres: half-height, half-depth, and how far it stands off
 * the skull.
 *
 * `published`, generously. An adult ear is about 62 mm long and 32 mm across and
 * stands 17-20 mm off the head. Generous rather than tight, because an ear
 * slightly too large hides a little extra arm where the head would have hidden
 * it anyway, while one too small lets the arm cross the pinna — which is the
 * artefact the ear exists to prevent.
 *
 * **An ear is a dish, never a ball, and that is the whole point.** A pinna is
 * not a lump on the side of the head: it is a flap standing off it, and the
 * crevice behind the flap is precisely where a temple arm runs. Model it solid
 * and the crevice fills in, so the arm ends up inside the head and disappears —
 * which is exactly what v1's earlier 40 mm ear balls did.
 */
export const PINNA = {
  halfHeightMm: 32,
  halfDepthMm: 17,
  standoffMm: 19,
  rings: 5,
  segments: 20,
} as const;

/** Where the pinna's centre sits relative to the arm's rest point, mm. */
export const PINNA_DOWN_MM = 20;
export const PINNA_BACK_MM = 3;

export interface HeadShell {
  /** Face vertices first, keeping their own indices, then the loft. */
  readonly positions: Float64Array;
  readonly indices: Uint32Array;
  /** The face's own boundary loop, so the loft can be re-run when the face moves. */
  readonly ring: readonly number[];
  readonly faceVertexCount: number;
  /** The ring's own centre, which the loft shrinks toward. */
  readonly ringCentre: Float64Array;
}

/**
 * The head's boundary, as one ordered ring.
 *
 * Found rather than tabulated: an edge belonging to exactly one triangle is on
 * the boundary, and on this mesh those 36 edges form a single closed loop.
 * Deriving it means the loft follows the mesh, rather than a list of indices
 * that would quietly stop matching it after a template change.
 */
export function boundaryLoop(indices: Uint32Array | ArrayLike<number>): number[] {
  const counts = new Map<string, number>();
  const key = (a: number, b: number) => (a < b ? `${a},${b}` : `${b},${a}`);
  for (let t = 0; t + 2 < indices.length; t += 3) {
    const tri = [indices[t], indices[t + 1], indices[t + 2]];
    for (let e = 0; e < 3; e++) {
      const k = key(tri[e], tri[(e + 1) % 3]);
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
  }

  const neighbours = new Map<number, number[]>();
  for (const [k, n] of counts) {
    if (n !== 1) continue;
    const [a, b] = k.split(',').map(Number);
    if (!neighbours.has(a)) neighbours.set(a, []);
    if (!neighbours.has(b)) neighbours.set(b, []);
    neighbours.get(a)!.push(b);
    neighbours.get(b)!.push(a);
  }
  if (neighbours.size === 0) return [];

  const start = Math.min(...neighbours.keys());
  const loop = [start];
  const seen = new Set([start]);
  let current = start;
  for (;;) {
    const next = (neighbours.get(current) ?? []).find((n) => !seen.has(n));
    if (next === undefined) break;
    seen.add(next);
    loop.push(next);
    current = next;
  }
  return loop;
}

/**
 * Builds the closed head shell: the face, plus a skull lofted from its rim.
 *
 * The face's vertices come first and keep their own indices, so anything that
 * addresses this mesh by landmark index still can — and so the same solved
 * `model.positions` can be written straight into the front of the buffer.
 */
export function buildHeadShell(
  positions: Float64Array, faceIndices: Uint32Array,
): HeadShell {
  const ring = boundaryLoop(faceIndices);
  if (ring.length < 8) {
    throw new Error(
      `buildHeadShell: the face mesh's boundary is ${ring.length} vertices, which is not `
      + 'a face oval. The loft has nothing to start from.',
    );
  }
  const R = ring.length;
  const faceVertexCount = positions.length / 3;

  // Room for the loft: SKULL_RINGS-1 full rings, then the single occipital pole.
  const out = new Float64Array((faceVertexCount + (SKULL_RINGS - 1) * R + 1) * 3);
  out.set(positions);

  const indices: number[] = Array.from(faceIndices);
  const poleIndex = faceVertexCount + (SKULL_RINGS - 1) * R;
  const at = (k: number, i: number) => {
    if (k === 0) return ring[i];
    if (k === SKULL_RINGS) return poleIndex;
    return faceVertexCount + (k - 1) * R + i;
  };

  for (let k = 1; k <= SKULL_RINGS; k++) {
    for (let i = 0; i < R; i++) {
      const j = (i + 1) % R;
      const a = at(k - 1, i);
      const b = at(k - 1, j);
      // The last ring collapses to the pole, so it is one vertex rather than R
      // in the same place — a fan of degenerate triangles is a hole waiting to
      // be found by a grazing ray.
      if (k === SKULL_RINGS) indices.push(a, b, poleIndex);
      else indices.push(a, b, at(k, j), a, at(k, j), at(k, i));
    }
  }

  const ringCentre = reloftSkull(out, ring, faceVertexCount);
  return { positions: out, indices: Uint32Array.from(indices), ring, faceVertexCount, ringCentre };
}

/**
 * Re-derives the skull from wherever the face's rim currently is.
 *
 * The occluder is deformed to the wearer, which moves the 36 rim vertices the
 * loft was built from. Re-running the loft is what keeps the shell watertight
 * through that — the skull SHARES the rim's vertices, so it has to be rebuilt
 * from them or the seam the loft exists to avoid opens up behind the ear.
 *
 * Writes in place, over the layout `buildHeadShell` allocated, and is the ONLY
 * place the loft's shape is defined — `buildHeadShell` calls it too, so the two
 * can never drift apart. (That is the same rule `fit/frame-layout.ts` exists to
 * enforce between the renderer and the occlusion instrument, and for the same
 * reason: two descriptions of one surface drift, and the drift is silent.)
 *
 * `depthScale` lengthens the skull with the head: a face measured 8% broader is
 * a bigger head front to back as well, and leaving the occiput at the average
 * head's depth would tuck the back of a large head inside the arms routed
 * around it.
 */
export function reloftSkull(
  positions: Float64Array, ring: readonly number[], faceVertexCount: number,
  depthScale = 1,
): Float64Array {
  const R = ring.length;

  const centre = new Float64Array(3);
  for (const index of ring) {
    for (let a = 0; a < 3; a++) centre[a] += positions[index * 3 + a];
  }
  for (let a = 0; a < 3; a++) centre[a] /= R;
  // x is pinned to the centreline rather than averaged: the ring is symmetric by
  // construction and a stray asymmetry would lean the whole skull.
  centre[0] = 0;

  const depth = SKULL_DEPTH_MM * depthScale;

  for (let k = 1; k <= SKULL_RINGS; k++) {
    const s = k / SKULL_RINGS;
    const width = (1 - s ** SKULL_FULLNESS) ** (1 / SKULL_FULLNESS);
    const back = depth * s;

    if (k === SKULL_RINGS) {
      const pole = (faceVertexCount + (SKULL_RINGS - 1) * R) * 3;
      positions[pole] = centre[0];
      positions[pole + 1] = centre[1];
      positions[pole + 2] = centre[2] - depth;
      break;
    }

    for (let i = 0; i < R; i++) {
      const from = ring[i] * 3;
      const to = (faceVertexCount + (k - 1) * R + i) * 3;
      for (let a = 0; a < 3; a++) {
        const v = positions[from + a];
        positions[to + a] = centre[a] + (v - centre[a]) * width - (a === 2 ? back : 0);
      }
    }
  }

  return centre;
}

/**
 * One ear, as an open dish standing off the skull.
 *
 * Built in its own space: the rim lies in the y-z plane and the apex bulges
 * along +x, so placing it is a translation and, on the wearer's right, a sign
 * flip. **Open on purpose** — see `PINNA` for the crevice a solid ear fills in.
 */
export function buildPinna(): { positions: Float64Array; indices: Uint32Array } {
  const positions: number[] = [];
  const indices: number[] = [];

  // The apex, then one ring per step out to the rim.
  positions.push(PINNA.standoffMm, 0, 0);
  for (let r = 1; r <= PINNA.rings; r++) {
    const t = r / PINNA.rings;
    // Parabolic in the radius, so the dish leaves the skull tangentially at its
    // rim rather than as a cylinder wall standing proud of the head.
    const x = PINNA.standoffMm * (1 - t * t);
    for (let s = 0; s < PINNA.segments; s++) {
      const angle = (s / PINNA.segments) * Math.PI * 2;
      positions.push(
        x,
        PINNA.halfHeightMm * t * Math.cos(angle),
        PINNA.halfDepthMm * t * Math.sin(angle),
      );
    }
  }

  const at = (r: number, s: number) => 1 + (r - 1) * PINNA.segments + (s % PINNA.segments);
  for (let s = 0; s < PINNA.segments; s++) indices.push(0, at(1, s), at(1, s + 1));
  for (let r = 1; r < PINNA.rings; r++) {
    for (let s = 0; s < PINNA.segments; s++) {
      indices.push(at(r, s), at(r + 1, s), at(r + 1, s + 1));
      indices.push(at(r, s), at(r + 1, s + 1), at(r, s + 1));
    }
  }

  return { positions: Float64Array.from(positions), indices: Uint32Array.from(indices) };
}

/**
 * Where one ear's dish belongs, given that side's arm rest point and the head's
 * half-width there.
 *
 * The rest point is near the TOP of the ear — that is what a temple arm rests on
 * — so the pinna itself hangs below it. Its rim seats on the skull, which is
 * what leaves the crevice behind it at the depth a real one has.
 *
 * `side` is -1 for the wearer's right, +1 for their left, matching
 * `FrameAsset.padSide`.
 */
export function pinnaPlacement(
  rest: ArrayLike<number>, halfWidthMm: number, side: -1 | 1,
): Float64Array {
  return Float64Array.of(
    side * Math.max(halfWidthMm, Math.abs(rest[0]) - PINNA.standoffMm * 0.5),
    rest[1] - PINNA_DOWN_MM,
    rest[2] - PINNA_BACK_MM,
  );
}

/**
 * The head shell with both ears welded on, as one buffer the rasteriser can take.
 *
 * The ears are separate surfaces rather than part of the loft — a dish standing
 * off the skull cannot be a ring in a loft of the face's boundary — so they are
 * appended with their own triangles.
 */
export function buildHeadWithEars(
  positions: Float64Array, faceIndices: Uint32Array,
  earRests: readonly [ArrayLike<number>, ArrayLike<number>],
): { positions: Float64Array; indices: Uint32Array; shell: HeadShell } {
  const shell = buildHeadShell(positions, faceIndices);
  const pinna = buildPinna();
  const pinnaVerts = pinna.positions.length / 3;

  const base = shell.positions.length / 3;
  const out = new Float64Array(shell.positions.length + pinnaVerts * 3 * 2);
  out.set(shell.positions);
  const indices: number[] = Array.from(shell.indices);

  // The skull's half-width at the ear's depth, so the dish seats on it rather
  // than floating off it or sinking in.
  const halfWidth = skullHalfWidthAt(shell, earRests[0][2]);

  for (const [s, side] of [[0, -1], [1, 1]] as const) {
    const at = base + s * pinnaVerts;
    const centre = pinnaPlacement(earRests[s], halfWidth, side);
    for (let i = 0; i < pinnaVerts; i++) {
      // The dish bulges along +x in its own space; mirror it onto the right.
      out[(at + i) * 3] = centre[0] + side * pinna.positions[i * 3];
      out[(at + i) * 3 + 1] = centre[1] + pinna.positions[i * 3 + 1];
      out[(at + i) * 3 + 2] = centre[2] + pinna.positions[i * 3 + 2];
    }
    for (let t = 0; t + 2 < pinna.indices.length; t += 3) {
      // Mirroring reverses winding; swap two corners back on the right side so
      // the dish faces outward on both. It is drawn double-sided anyway, but a
      // consistent winding keeps that a safety net rather than load-bearing.
      if (side < 0) {
        indices.push(at + pinna.indices[t], at + pinna.indices[t + 2], at + pinna.indices[t + 1]);
      } else {
        indices.push(at + pinna.indices[t], at + pinna.indices[t + 1], at + pinna.indices[t + 2]);
      }
    }
  }

  return { positions: out, indices: Uint32Array.from(indices), shell };
}

/** The skull's half-width at a given depth, from the loft's own arithmetic. */
export function skullHalfWidthAt(shell: HeadShell, z: number): number {
  let ringHalfWidth = 0;
  for (const i of shell.ring) {
    const x = Math.abs(shell.positions[i * 3]);
    if (x > ringHalfWidth) ringHalfWidth = x;
  }
  const s = Math.min(1, Math.max(0, (shell.ringCentre[2] - z) / SKULL_DEPTH_MM));
  return ringHalfWidth * (1 - s ** SKULL_FULLNESS) ** (1 / SKULL_FULLNESS);
}
