/**
 * A head, to hide the temple arms behind.
 *
 * MediaPipe gives us a *face*: 468 vertices that stop at the silhouette, whose
 * rearmost point is 2.4 cm behind the cheek and which has no ears at all. A temple
 * arm runs 12 cm back from the frame and spends ten of them past the last vertex
 * that mesh owns. So for almost its whole length the arm is drawn against nothing,
 * and what a viewer sees is decided by whichever ad-hoc proxy happens to be nearby.
 *
 * That is what the previous occluder was: the face mesh, plus a braincase ellipsoid
 * held deliberately *inside* the arms so it would not eat them, plus one 4 cm ball
 * per ear. Traced through face space, the union of the three covered a single 2.8 cm
 * band of a 12 cm arm — and that band sat 2 cm in *front* of the ear, over the
 * cheekbone. The arm therefore vanished in the middle of the cheek and reappeared
 * nowhere: on screen, a stub of temple driven into the side of the face. Both of the
 * artefacts this module exists to remove are that one fact seen from two angles.
 *
 * The fix is not another proxy. It is to stop approximating the head and build one:
 *
 *   **the face mesh's own boundary is a closed 36-vertex loop around the face oval,
 *   and lofting it backwards to an occipital pole closes the head.**
 *
 * That is worth stating plainly because it is the whole trick. The loft starts at
 * the mesh's own rim, sharing its vertices, so the result is watertight — there is
 * no seam for an arm to appear through, and no clearance constant deciding which
 * artefact we would rather have. From the rim forwards the head *is* MediaPipe's
 * face, tracked exactly as before; from the rim backwards it is a skull that keeps
 * the face's own width and takes the head's real length.
 *
 * Ears are the one thing the loft cannot supply, and they cannot be blobs. A pinna
 * is not a lump on the side of the head: it is a flap standing off it, and the
 * crevice behind the flap is precisely where a temple arm runs. Model it as a solid
 * and the crevice fills in, so the arm is inside the head and disappears — which is
 * exactly what the ear balls did. So each ear is an open dish: it writes depth, it
 * hides the arm from the side the way a real pinna does, and there is still a gap
 * behind it for the arm to run through.
 */

import * as THREE from 'three';

/**
 * How far behind the face rim's own centre the back of the skull sits, in cm.
 *
 * Measured off the canonical head rather than chosen: its ring centre is at z=0.96
 * and the nose tip at z=7.59, so a pole at z=-13 gives a head 20.6 cm from nose to
 * occiput and about 18.5 cm from glabella — an adult head, which is what this mesh
 * is an average of.
 */
const SKULL_DEPTH = 14.0;

/**
 * How square the skull's sweep is, as the exponent of a superellipse.
 *
 * A circular sweep (2.0) is wrong in a way that matters here: it would put the head
 * at 93% of its width by the depth of the ear, where a real head is still at ~97%,
 * and the missing 5 mm is the whole clearance a temple arm has. 3.5 holds the width
 * to 99% at the ear and still closes cleanly at the occiput.
 */
const SKULL_FULLNESS = 3.5;

/** Rings in the loft. Twelve is smooth enough that the silhouette has no facets. */
const SKULL_RINGS = 12;

/**
 * The pinna, in cm: half-height, half-depth, and how far it stands off the skull.
 *
 * An adult ear is about 6.2 cm long, 3.2 cm across and stands 1.7-2 cm off the head.
 * Generous rather than tight, because an ear slightly too large hides a little extra
 * arm where the head would have hidden it anyway, while one too small lets the arm
 * cross the pinna — the artefact the ear exists to prevent.
 */
const PINNA = { height: 3.2, depth: 1.7, standoff: 1.9, rings: 5, segments: 20 };

/** Where the pinna's centre sits relative to the arm's rest point, in cm. */
const PINNA_DOWN = 2.0;
const PINNA_BACK = 0.3;

/** The half-width table's grid, in cm. */
const PROFILE = { minY: -12, maxY: 12, minZ: -15, maxZ: 9, cell: 0.25 };

/**
 * There is no width ramp any more, and its absence is the fix.
 *
 * What stood here scaled the occluder sideways by the measured width ratio, but only
 * outboard of x=3 cm, because a uniform stretch walked the head *forwards* as well as
 * outwards and drove it through the frame's own pads. The ramp was the right answer to
 * that problem and the wrong problem to be solving: it left the entire nose — every
 * part of it a frame can touch — drawn at the average head's width, height, protrusion
 * and bridge position, on every face.
 *
 * `occluder.js` now carries all 468 vertices onto the observed face instead, which is
 * both more correct and cheaper than a ramp: there is nothing left to compromise
 * between, because nothing is being stretched.
 */

/**
 * Builds the closed head shell: the canonical face, plus a skull lofted from its rim.
 *
 * Returns plain arrays. The vertices of the face mesh come first and keep their own
 * indices, so anything that already addresses this mesh by landmark index still can.
 */
export function buildHeadShell(face) {
  const ring = boundaryLoop(face.indices);
  const R = ring.length;
  const faceVertexCount = face.positions.length / 3;

  // Room for the loft: SKULL_RINGS-1 full rings, then the single occipital pole.
  const positions = new Float32Array((faceVertexCount + (SKULL_RINGS - 1) * R + 1) * 3);
  positions.set(face.positions);

  const indices = [...face.indices];
  const poleIndex = faceVertexCount + (SKULL_RINGS - 1) * R;
  const at = (k, i) => {
    if (k === 0) return ring[i];
    if (k === SKULL_RINGS) return poleIndex;
    return faceVertexCount + (k - 1) * R + i;
  };

  for (let k = 1; k <= SKULL_RINGS; k++) {
    for (let i = 0; i < R; i++) {
      const j = (i + 1) % R;
      const a = at(k - 1, i);
      const b = at(k - 1, j);
      // Winding follows the face mesh's, so the shell is consistently outward-facing.
      // It is drawn double-sided anyway — a shell seen from inside on a hard turn
      // must still write depth — but a consistent one keeps that a safety net rather
      // than a load-bearing setting.
      //
      // The last ring collapses to the pole, so it is one vertex rather than 36 in the
      // same place — a fan of degenerate triangles is a hole waiting to be found by a
      // grazing ray.
      if (k === SKULL_RINGS) indices.push(a, b, poleIndex);
      else indices.push(a, b, at(k, j), a, at(k, j), at(k, i));
    }
  }

  const ringCentre = reloftSkull(positions, ring, faceVertexCount);

  return {
    positions,
    indices: new Uint32Array(indices),
    ringCentre,
    /** The face's own boundary loop, so the loft can be re-run when the face moves. */
    ring,
    faceVertexCount,
  };
}

/**
 * Re-derives the skull from wherever the face's rim currently is.
 *
 * The occluder is deformed to the wearer every frame (see `occluder.js`), which moves
 * the 36 rim vertices the loft was built from. Re-running the loft is what keeps the
 * shell watertight through that — the skull shares the rim's vertices, so it has to
 * be rebuilt from them or the seam the loft exists to avoid opens up behind the ear.
 *
 * Writes in place, over the same layout `buildHeadShell` allocated, and is the *only*
 * place the loft's shape is defined — `buildHeadShell` calls it too, so the two can
 * never drift apart.
 *
 * `depthScale` lengthens the skull with the head. A face measured 8% broader is a
 * bigger head front to back as well, and leaving the occiput at the average head's
 * 14 cm would tuck the back of a large head inside the arms routed around it.
 */
export function reloftSkull(positions, ring, faceVertexCount, depthScale = 1) {
  const R = ring.length;

  // The ring's own centre, which the loft shrinks towards, and the pole it closes at.
  const centre = [0, 0, 0];
  for (const index of ring) {
    for (let a = 0; a < 3; a++) centre[a] += positions[index * 3 + a];
  }
  for (let a = 0; a < 3; a++) centre[a] /= R;
  // x is pinned to the centreline rather than averaged: the ring is symmetric by
  // construction and a stray asymmetry would lean the whole skull.
  centre[0] = 0;

  const depth = SKULL_DEPTH * depthScale;

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
 * The head's boundary, as one ordered ring.
 *
 * Found rather than tabulated: an edge belonging to exactly one triangle is on the
 * boundary, and on this mesh those 36 edges form a single closed loop around the
 * face oval. Deriving it means the loft follows the mesh rather than a list of
 * indices that would quietly stop matching it.
 */
export function boundaryLoop(indices) {
  const counts = new Map();
  const key = (a, b) => (a < b ? `${a},${b}` : `${b},${a}`);
  for (let t = 0; t < indices.length; t += 3) {
    const tri = [indices[t], indices[t + 1], indices[t + 2]];
    for (let e = 0; e < 3; e++) {
      const k = key(tri[e], tri[(e + 1) % 3]);
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
  }

  const neighbours = new Map();
  for (const [k, n] of counts) {
    if (n !== 1) continue;
    const [a, b] = k.split(',').map(Number);
    if (!neighbours.has(a)) neighbours.set(a, []);
    if (!neighbours.has(b)) neighbours.set(b, []);
    neighbours.get(a).push(b);
    neighbours.get(b).push(a);
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
 * How wide the head is at a given height and depth — the number the temple arms are
 * routed against.
 *
 * Rasterised off the shell rather than derived from the constants above, so the arms
 * clear the surface that will actually be drawn and not an idealisation of it. Keeps
 * the largest |x| per cell, which is the outer surface: the only one an arm can be
 * outside of.
 *
 * Returns 0 where the head has no geometry, which reads as "nothing to clear" and is
 * the right answer in front of the face and behind the skull alike.
 */
export function buildHeadProfile({ positions, indices }) {
  const columns = Math.round((PROFILE.maxZ - PROFILE.minZ) / PROFILE.cell) + 1;
  const rows = Math.round((PROFILE.maxY - PROFILE.minY) / PROFILE.cell) + 1;
  const width = new Float32Array(columns * rows);

  const fill = (p, ix) => {
    width.fill(0);
    for (let t = 0; t < ix.length; t += 3) {
      rasteriseWidth(width, columns, rows, p, ix[t], ix[t + 1], ix[t + 2]);
    }
  };
  fill(positions, indices);

  return {
    columns,
    rows,
    cell: PROFILE.cell,
    /**
     * Re-rasterises from moved vertices, into the same grid.
     *
     * In place because the deformed occluder rebuilds this several times a second and
     * a fresh 9.4k-cell allocation each time is garbage the collector then has to
     * find during tracking.
     */
    rebuild(next = positions, nextIndices = indices) { fill(next, nextIndices); },
    /** The head's half-width at (y, z) in canonical face space, in cm. */
    at(y, z) {
      const u = (z - PROFILE.minZ) / PROFILE.cell;
      const v = (y - PROFILE.minY) / PROFILE.cell;
      const i = Math.floor(u);
      const j = Math.floor(v);
      if (i < 0 || j < 0 || i >= columns - 1 || j >= rows - 1) return 0;
      const a = width[j * columns + i];
      const b = width[j * columns + i + 1];
      const c = width[(j + 1) * columns + i];
      const d = width[(j + 1) * columns + i + 1];
      const fx = u - i;
      const fy = v - j;
      return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy;
    },
  };
}

/** One triangle into the (z, y) grid, keeping the largest |x| it covers. */
function rasteriseWidth(grid, columns, rows, positions, ia, ib, ic) {
  const az = positions[ia * 3 + 2]; const ay = positions[ia * 3 + 1];
  const bz = positions[ib * 3 + 2]; const by = positions[ib * 3 + 1];
  const cz = positions[ic * 3 + 2]; const cy = positions[ic * 3 + 1];
  const ax = Math.abs(positions[ia * 3]);
  const bx = Math.abs(positions[ib * 3]);
  const cx = Math.abs(positions[ic * 3]);

  const det = (by - cy) * (az - cz) + (cz - bz) * (ay - cy);
  // Edge-on in this projection: a triangle lying in a plane through the x axis, so
  // one of the front, back, top or bottom. Never the widest, so nothing is lost.
  if (Math.abs(det) < 1e-12) return;

  const i0 = Math.max(0, Math.ceil((Math.min(az, bz, cz) - PROFILE.minZ) / PROFILE.cell));
  const i1 = Math.min(columns - 1, Math.floor((Math.max(az, bz, cz) - PROFILE.minZ) / PROFILE.cell));
  const j0 = Math.max(0, Math.ceil((Math.min(ay, by, cy) - PROFILE.minY) / PROFILE.cell));
  const j1 = Math.min(rows - 1, Math.floor((Math.max(ay, by, cy) - PROFILE.minY) / PROFILE.cell));

  for (let j = j0; j <= j1; j++) {
    const y = PROFILE.minY + j * PROFILE.cell;
    for (let i = i0; i <= i1; i++) {
      const z = PROFILE.minZ + i * PROFILE.cell;
      const l1 = ((by - cy) * (z - cz) + (cz - bz) * (y - cy)) / det;
      if (l1 < 0) continue;
      const l2 = ((cy - ay) * (z - cz) + (az - cz) * (y - cy)) / det;
      if (l2 < 0) continue;
      const l3 = 1 - l1 - l2;
      if (l3 < 0) continue;
      const x = l1 * ax + l2 * bx + l3 * cx;
      const at = j * columns + i;
      if (x > grid[at]) grid[at] = x;
    }
  }
}

/**
 * One ear, as an open dish standing off the skull.
 *
 * Built in its own space: the rim lies in the y-z plane and the apex bulges along
 * +x, so placing it is a translation and, on the right ear, a sign flip. Open on
 * purpose — see the note at the top of this file about the crevice.
 */
export function buildPinnaGeometry() {
  const positions = [];
  const indices = [];

  // The apex, then one ring per step out to the rim.
  positions.push(PINNA.standoff, 0, 0);
  for (let r = 1; r <= PINNA.rings; r++) {
    const t = r / PINNA.rings;
    // Parabolic in the radius, so the dish leaves the skull tangentially at its rim
    // rather than as a cylinder wall standing proud of the head.
    const x = PINNA.standoff * (1 - t * t);
    for (let s = 0; s < PINNA.segments; s++) {
      const angle = (s / PINNA.segments) * Math.PI * 2;
      positions.push(x, PINNA.height * t * Math.cos(angle), PINNA.depth * t * Math.sin(angle));
    }
  }

  const at = (r, s) => 1 + (r - 1) * PINNA.segments + (s % PINNA.segments);
  for (let s = 0; s < PINNA.segments; s++) indices.push(0, at(1, s), at(1, s + 1));
  for (let r = 1; r < PINNA.rings; r++) {
    for (let s = 0; s < PINNA.segments; s++) {
      indices.push(at(r, s), at(r + 1, s), at(r + 1, s + 1));
      indices.push(at(r, s), at(r + 1, s + 1), at(r, s + 1));
    }
  }

  return {
    positions: new Float32Array(positions),
    indices: new Uint16Array(indices),
  };
}

/**
 * Where one ear's dish belongs, given that side's arm rest point and the head's
 * width there.
 *
 * The rest point is near the *top* of the ear — that is what a temple arm rests on —
 * so the pinna itself hangs below it. Its rim is seated on the skull, which is what
 * leaves the crevice behind it at exactly the depth a real one has.
 */
export function pinnaPlacement(rest, halfWidth, side) {
  return new THREE.Vector3(
    side * Math.max(halfWidth, Math.abs(rest.x) - PINNA.standoff * 0.5),
    rest.y - PINNA_DOWN,
    rest.z - PINNA_BACK,
  );
}

export const HEAD_CONSTANTS = { SKULL_DEPTH, SKULL_FULLNESS, SKULL_RINGS, PINNA };
