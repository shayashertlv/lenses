/**
 * Loop subdivision, precomputed as a fixed linear map.
 *
 * The occluder's silhouette is made of triangle edges, and MediaPipe's mesh has 898 of
 * them for a whole face. Measured over the nose specifically — the strip where a frame's
 * bridge and the far lens's inner rim cross the skin — the triangles are **7.3 mm on a
 * side, up to 16.5 mm**. On a phone held at arm's length that is nine screen pixels and
 * nobody notices. On the close-up captures this work was reported from, where a face
 * fills the frame, it is **37 to 53 pixels, with the longest edges reaching 121**.
 *
 * That is the artefact that survived the first rebuild. Getting the occluder onto the
 * right face fixed *where* the boundary is; it could not fix what the boundary is made
 * of, which is a polyline of straight segments tens of pixels long. Feathering does not
 * help either — a soft edge in the shape of a facet is a soft facet. The boundary has to
 * stop being polygonal.
 *
 * Loop is the right scheme here rather than Catmull-Clark because the mesh is triangles,
 * and it is applied as a *precomputed* map rather than as an algorithm: subdivision is
 * linear in the vertex positions, so the topology and the weights are solved once
 * against the canonical mesh and every rebuild afterwards is a weighted sum. The face
 * deforms every frame; its connectivity never does.
 *
 * The scheme is approximating rather than interpolating, so the limit surface pulls
 * slightly inside the original vertices at high curvature — a few tenths of a millimetre
 * at the nose tip. For an occluder that is the safe direction (a fractionally smaller
 * head under-occludes rather than eating the frame), and it is measured in the harness
 * rather than assumed.
 */

/**
 * Solves the subdivision of one topology into a reusable plan.
 *
 * Returns the new topology plus a CSR-style sparse matrix mapping old vertex positions
 * to new ones. Original vertices keep their indices, so anything addressing this mesh by
 * landmark number still can — `LM.NOSE_BRIDGE` is vertex 6 before and after.
 */
export function planLoopSubdivision(indices, vertexCount, pinned = 0) {
  const triangleCount = indices.length / 3;

  // --- edges, and the vertices opposite them ---
  const edgeKey = (a, b) => (a < b ? a * vertexCount + b : b * vertexCount + a);
  const edges = new Map();
  const edgeOf = new Uint32Array(triangleCount * 3);

  for (let t = 0; t < triangleCount; t++) {
    const v = [indices[t * 3], indices[t * 3 + 1], indices[t * 3 + 2]];
    for (let e = 0; e < 3; e++) {
      const a = v[e];
      const b = v[(e + 1) % 3];
      const opposite = v[(e + 2) % 3];
      const key = edgeKey(a, b);
      let edge = edges.get(key);
      if (!edge) {
        edge = { a: Math.min(a, b), b: Math.max(a, b), opposites: [], index: edges.size };
        edges.set(key, edge);
      }
      edge.opposites.push(opposite);
      edgeOf[t * 3 + e] = edge.index;
    }
  }

  const edgeList = new Array(edges.size);
  for (const edge of edges.values()) edgeList[edge.index] = edge;

  // --- neighbours, and which vertices sit on a boundary ---
  const neighbours = Array.from({ length: vertexCount }, () => []);
  const onBoundary = new Uint8Array(vertexCount);
  const boundaryNeighbours = Array.from({ length: vertexCount }, () => []);

  for (const edge of edgeList) {
    neighbours[edge.a].push(edge.b);
    neighbours[edge.b].push(edge.a);
    // An edge with one adjacent triangle is on the mesh's rim. The closed shell has
    // none; the bare face mesh has 36, and the loft is built from exactly those.
    if (edge.opposites.length === 1) {
      onBoundary[edge.a] = 1;
      onBoundary[edge.b] = 1;
      boundaryNeighbours[edge.a].push(edge.b);
      boundaryNeighbours[edge.b].push(edge.a);
    }
  }

  // --- the weights ---
  const rows = new Array(vertexCount + edgeList.length);

  for (let v = 0; v < vertexCount; v++) {
    // A pinned vertex is a *measurement*, and Loop's vertex rule would smooth it away.
    // Measured on the canonical mesh, full Loop pulls the nose bridge 1.6 mm back and
    // the whole head about 1 mm in — the ridge flattens, because that rule drags every
    // vertex towards the average of its ring and a nose bridge is nothing but curvature.
    // A millimetre of shrink is not a rounding error here: it sits on top of the relief
    // and is 20 screen pixels of under-occlusion at the range these captures were taken.
    //
    // So the 468 landmarks stay exactly where the detector put them, at every level, and
    // only the vertices subdivision itself introduced are free to move. What comes out
    // interpolates the measurement and is smooth between it, which is the same division
    // of labour the rest of this pipeline runs on.
    if (v < pinned) { rows[v] = [[v, 1]]; continue; }

    if (onBoundary[v]) {
      // A rim vertex is a cubic B-spline of the rim curve, and must depend only on the
      // rim. Letting the interior pull on it would drag the face's boundary inward and
      // open a seam against the skull lofted from it.
      const ring = boundaryNeighbours[v];
      if (ring.length === 2) {
        rows[v] = [[v, 3 / 4], [ring[0], 1 / 8], [ring[1], 1 / 8]];
      } else {
        rows[v] = [[v, 1]];
      }
      continue;
    }

    const ring = neighbours[v];
    const n = ring.length;
    if (n === 0) { rows[v] = [[v, 1]]; continue; }
    // Loop's weight. At n=3 this is exactly Warren's 3/16, so there is no special case.
    const centre = (3 / 8) + (1 / 4) * Math.cos((2 * Math.PI) / n);
    const beta = (1 / n) * ((5 / 8) - centre * centre);
    const row = [[v, 1 - n * beta]];
    for (const u of ring) row.push([u, beta]);
    rows[v] = row;
  }

  for (const edge of edgeList) {
    const at = vertexCount + edge.index;
    if (edge.opposites.length === 2) {
      rows[at] = [
        [edge.a, 3 / 8], [edge.b, 3 / 8],
        [edge.opposites[0], 1 / 8], [edge.opposites[1], 1 / 8],
      ];
    } else {
      rows[at] = [[edge.a, 1 / 2], [edge.b, 1 / 2]];
    }
  }

  // --- flatten to CSR, so applying it is one linear pass with no allocation ---
  let nnz = 0;
  for (const row of rows) nnz += row.length;
  const offsets = new Uint32Array(rows.length + 1);
  const parents = new Uint32Array(nnz);
  const weights = new Float32Array(nnz);
  let at = 0;
  for (let i = 0; i < rows.length; i++) {
    offsets[i] = at;
    for (const [parent, weight] of rows[i]) {
      parents[at] = parent;
      weights[at] = weight;
      at++;
    }
  }
  offsets[rows.length] = at;

  // --- the new triangles ---
  //
  // Four per input triangle, emitted in input order, so a prefix of the source's
  // triangles is still a prefix of the result's. `occluder.js` relies on that to
  // rasterise the face's own triangles into the depth field without the skull behind
  // them, and the relationship survives any number of levels.
  const out = new Uint32Array(triangleCount * 12);
  for (let t = 0; t < triangleCount; t++) {
    const a = indices[t * 3];
    const b = indices[t * 3 + 1];
    const c = indices[t * 3 + 2];
    const ab = vertexCount + edgeOf[t * 3];
    const bc = vertexCount + edgeOf[t * 3 + 1];
    const ca = vertexCount + edgeOf[t * 3 + 2];
    out.set([a, ab, ca, b, bc, ab, c, ca, bc, ab, bc, ca], t * 12);
  }

  return {
    vertexCount: vertexCount + edgeList.length,
    sourceVertexCount: vertexCount,
    indices: out,
    triangleCount: triangleCount * 4,
    offsets,
    parents,
    weights,
  };
}

/** Applies a plan: `into` becomes the subdivided positions of `from`. */
export function applyLoopSubdivision(plan, from, into) {
  const { offsets, parents, weights, vertexCount } = plan;
  for (let v = 0; v < vertexCount; v++) {
    let x = 0; let y = 0; let z = 0;
    for (let k = offsets[v]; k < offsets[v + 1]; k++) {
      const at = parents[k] * 3;
      const w = weights[k];
      x += from[at] * w;
      y += from[at + 1] * w;
      z += from[at + 2] * w;
    }
    into[v * 3] = x;
    into[v * 3 + 1] = y;
    into[v * 3 + 2] = z;
  }
  return into;
}

/**
 * Chains `levels` of subdivision into one reusable pipeline.
 *
 * Each level quadruples the triangles and halves the edge length, so the nose's 7.3 mm
 * facets become 3.7 mm at one level and 1.8 mm at two. Two is where the facet drops
 * below the feather's own width at close range, which is the point at which the
 * boundary stops being a polygon and starts being a curve.
 */
export function planSubdivision(indices, vertexCount, levels, pinned = 0) {
  const plans = [];
  const buffers = [];
  let currentIndices = indices;
  let currentCount = vertexCount;

  // Zero levels is a real setting, not a degenerate one: it is the A/B against the
  // faceted occluder, and it has to cost nothing and change nothing.
  if (levels <= 0) {
    return {
      levels: 0, plans, buffers, indices, vertexCount,
      triangleCount: indices.length / 3, scale: 1,
      apply(from, into = null) { if (into && into !== from) into.set(from); return into ?? from; },
      compensate(target, control) { control.set(target); return control; },
    };
  }

  for (let level = 0; level < levels; level++) {
    // `pinned` does not grow with the mesh: it is the count of *measured* vertices, and
    // subdivision never adds a measurement. Everything a previous level introduced is
    // free to be smoothed by the next one, which is what stops the pinned points from
    // reading as 468 little creases.
    const plan = planLoopSubdivision(currentIndices, currentCount, pinned);
    plans.push(plan);
    buffers.push(new Float32Array(plan.vertexCount * 3));
    currentIndices = plan.indices;
    currentCount = plan.vertexCount;
  }

  return {
    levels,
    plans,
    buffers,
    /** The finest topology — what actually gets drawn and rasterised. */
    indices: currentIndices,
    vertexCount: currentCount,
    triangleCount: currentIndices.length / 3,
    /** How a source triangle count maps through, so prefixes stay prefixes. */
    scale: 4 ** levels,
    /**
     * Runs every level. `into` is optional and defaults to the last internal buffer, so
     * a caller that only wants to read the result allocates nothing.
     */
    apply(from, into = null) {
      let source = from;
      for (let level = 0; level < plans.length; level++) {
        const target = (level === plans.length - 1 && into) ? into : buffers[level];
        applyLoopSubdivision(plans[level], source, target);
        source = target;
      }
      return source;
    },

    /**
     * Solves for the control positions whose *limit surface* passes through `target`.
     *
     * Loop is approximating, so feeding it the measured landmarks gives a surface that
     * misses them — and misses them the same way every time, by flattening curvature.
     * Measured on the canonical head at two levels: the nose bridge pulled back 1.6 mm
     * and the worst landmark moved 3.3 mm. That is a systematic shrink of the occluder
     * on top of the relief it already carries, and at the range these captures were
     * taken it is twenty pixels of frame drawn over skin that should hide it.
     *
     * Pinning the landmarks instead is worse and it is worth saying why, because it is
     * the obvious fix: holding a vertex still while its whole neighbourhood smooths away
     * from it turns it into a spike. Measured, it took the worst crease in the nose from
     * 52° to **90°** at two levels and 117° at three — the opposite of the intent.
     *
     * So invert it instead. Subdivision is linear, the landmarks keep their indices, and
     * `I - S` is a contraction on this mesh, so a plain Richardson iteration converges
     * geometrically: eight passes take the worst landmark error to 0.11 mm, which is
     * under a tenth of the feather. What comes out interpolates every measurement and is
     * smooth in between — which is the whole point, and is the same division of labour
     * between measured and interpolated that the rest of the pipeline runs on.
     */
    compensate(target, control, iterations = 8, warm = false) {
      // Warm-started, the previous answer is the starting point. The shape only rebuilds
      // when it has moved past a 0.2 mm deadband, so the previous control mesh is already
      // within that of the new one and three passes take the residual to a hundredth of a
      // millimetre — where a cold start needs eight to get anywhere near. Same fixed
      // point either way; this only changes how far it has to travel to reach it.
      if (!warm) control.set(target);
      const scratch = buffers[buffers.length - 1];
      for (let pass = 0; pass < iterations; pass++) {
        this.apply(control, scratch);
        // Only the rows that carry a measurement are corrected; everything subdivision
        // introduced is free and is exactly what absorbs the correction.
        for (let i = 0; i < target.length; i++) control[i] += target[i] - scratch[i];
      }
      return control;
    },
  };
}
