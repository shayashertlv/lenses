/**
 * Putting the temple arms on the ears.
 *
 * A pair of glasses is carried at three points: the nose and the two ears. The
 * solver in `fit.js` handles the nose and then rigidly transforms everything else,
 * which leaves the arms wherever the modeller happened to put them. Measured
 * against the average head, `base.obj` came out running **1.0–1.6 cm inside the
 * skull** and passing 3 cm below the ear. The occluder then does its job and hides
 * them, so all that survives is a stub poking out by the temple — which is exactly
 * what it looked like.
 *
 * Rotating the whole model cannot fix that. Tilting the front for pantoscopic angle
 * swings the arms through the same angle, which is backwards: on real glasses the
 * arms stay level and run to the ears, and the *front* tilts relative to them.
 *
 * So the arms get their own hinge, like the real thing. Each is split into its own
 * node pivoted where it meets the front, and aimed at that face's ear. That is the
 * same adjustment an optician makes by hand, and it decouples the arms from the
 * front's tilt.
 *
 * Aiming at the ear is necessary and not sufficient, because the line from the hinge
 * to the ear does not have to stay outside the head — and on a frame narrower than
 * the face it emphatically does not. A 140 mm front on a 165 mm head puts each hinge
 * 12 mm inboard of the temple, so the straight run passes through the skull; the
 * occluder then does its job and eats the arm, which is how a temple ends up
 * vanishing into a cheek. No amount of work on the occluder fixes that, because the
 * arm is in the wrong place. `splayClearOfHead` opens the temples at the hinge until
 * the run clears the head — the second adjustment an optician makes by hand.
 */

import * as THREE from 'three';

/**
 * Splits the temple arms out of a loaded model into separately hinged nodes.
 *
 * The cut is geometric, so it works on an asset with no named parts: the frame
 * front occupies the frontmost slice of the model, and anything behind it out at
 * the sides is an arm. Returns `null` when there is nothing arm-like — a
 * lens-only asset is left exactly as it was.
 */
export function prepareTemples(root, { frontDepthFraction = 0.18, sideFraction = 0.25 } = {}) {
  root.updateMatrixWorld(true);

  const meshes = [];
  root.traverse((node) => { if (node.isMesh && node.geometry) meshes.push(node); });
  if (meshes.length === 0) return null;

  // Rebuild in world space — which is metres, because the loader has already
  // normalised the root — and then neutralise the root's transform so that its
  // local space *is* metres. Without that reset the baked scale would be applied a
  // second time on render and the model would vanish; without the baking, the arm
  // hinges would come out in whatever arbitrary units the asset was authored in and
  // would not line up with the placement, which works in metres.
  const v = new THREE.Vector3();
  const normalMatrix = new THREE.Matrix3();

  // Materials are tracked per triangle rather than collapsed to one. An eyewear glTF
  // routinely gives the lenses their own transmissive material — the one part of the
  // model whose material cannot be substituted for another — so rebuilding the whole
  // frame under a single material turns clear lenses into opaque plastic.
  const materials = [];
  const materialSlot = (material) => {
    const found = materials.indexOf(material);
    if (found >= 0) return found;
    materials.push(material);
    return materials.length - 1;
  };

  const triangles = [];
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];

  let partIndex = -1;
  for (const mesh of meshes) {
    partIndex += 1;
    const geometry = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry;
    const position = geometry.attributes.position;
    const normal = geometry.attributes.normal;
    const uv = geometry.attributes.uv;
    const local = mesh.matrixWorld;
    // Normals do not travel under the matrix that carries positions — they need the
    // inverse transpose. glTF nodes routinely carry a -90° X rotation to convert
    // Z-up to Y-up, and baking positions through it while copying normals verbatim
    // leaves every normal square to the surface it belongs to, so the whole model
    // lights as though it were facing the ceiling.
    normalMatrix.getNormalMatrix(local);

    for (let t = 0; t < position.count; t += 3) {
      const p = [0, 1, 2].map((k) => {
        v.fromBufferAttribute(position, t + k).applyMatrix4(local);
        for (let a = 0; a < 3; a++) {
          min[a] = Math.min(min[a], v.getComponent(a));
          max[a] = Math.max(max[a], v.getComponent(a));
        }
        return [v.x, v.y, v.z];
      });
      const n = normal
        ? [0, 1, 2].map((k) => {
          v.fromBufferAttribute(normal, t + k).applyMatrix3(normalMatrix).normalize();
          return [v.x, v.y, v.z];
        })
        : null;
      // UVs ride along untouched — they live in texture space, not model space.
      // Dropping them looks like nothing on an untextured frame and destroys a
      // textured one: every rebuilt vertex samples the map at (0,0).
      const tex = uv
        ? [0, 1, 2].map((k) => [uv.getX(t + k), uv.getY(t + k)])
        : null;
      triangles.push({
        p, n, tex, part: partIndex, slot: materialSlot(materialAt(mesh, geometry, t)),
      });
    }
  }

  if (triangles.length === 0) return null;

  const size = max.map((m, i) => m - min[i]);
  const centreX = (min[0] + max[0]) / 2;
  const zCut = max[2] - size[2] * frontDepthFraction;
  const xCut = size[0] * sideFraction;

  // side: -1 for the model's -X arm, +1 for +X. 0 is the front.
  const classify = ({ p: [a, b, c] }) => {
    const behind = (a[2] < zCut) && (b[2] < zCut) && (c[2] < zCut);
    if (!behind) return 0;
    const x = (a[0] + b[0] + c[0]) / 3 - centreX;
    if (Math.abs(x) < xCut) return 0;
    return Math.sign(x);
  };

  // Prefer the model's own parts, where it has them. A cut through a single welded
  // mesh can only guess where the arm ends and the front begins, and it guesses in
  // the middle of the geometry — so the arm pivots about a point part-way along
  // itself and *looks bent*, which is exactly the artefact this replaced. An asset
  // modelled in parts already knows: the boundary between the temple part and the
  // frame part IS the hinge, the same place a real pair bends.
  const partSides = classifyParts(triangles, meshes.length, centreX, xCut);

  const buckets = { 0: [], '-1': [], 1: [] };
  for (const tri of triangles) {
    buckets[partSides ? partSides[tri.part] : classify(tri)].push(tri);
  }

  const armR = buckets['-1'];
  const armL = buckets['1'];
  // Too little to be a temple arm — leave the model alone rather than dismember it.
  if (armR.length < 20 || armL.length < 20) return null;

  // Everything needed from the source meshes is now in plain arrays, so the
  // originals can go. Their geometry is dropped with them — nothing refers to it
  // once the rebuild replaces it — while the materials are deliberately kept, since
  // the rebuilt parts reuse them.
  const retiredGeometry = new Set();
  for (const mesh of meshes) {
    mesh.parent?.remove(mesh);
    retiredGeometry.add(mesh.geometry);
  }
  for (const geometry of retiredGeometry) geometry.dispose();

  // The geometry now carries the world transform, so the root must not apply it
  // again. From here on the root's local space is metres.
  root.position.set(0, 0, 0);
  root.quaternion.identity();
  root.scale.set(1, 1, 1);
  root.updateMatrixWorld(true);

  const front = buildPart(buckets[0], materials, new THREE.Vector3());
  if (front) root.add(front);

  const arms = [];
  for (const [side, tris] of [[-1, armR], [1, armL]]) {
    const hinge = findHinge(tris);
    const tip = findTip(tris);
    const axis = findAxisPoint(tris);

    // A group rather than a single mesh: an arm can span several materials — hooks,
    // temples and tips are frequently authored separately — and the hinge has to
    // swing all of them together.
    const node = buildPart(tris, materials, hinge);
    node.position.copy(hinge);
    root.add(node);

    // Each arm gets its own copies of whatever materials it landed on, so it can be
    // faded without taking the frame front — and the other arm — with it. The
    // material list is shared across every part by construction (it exists to keep
    // transmissive lenses transmissive), which is right for building the geometry and
    // exactly wrong for anything per-part afterwards.
    for (const mesh of node.children) {
      mesh.material = Array.isArray(mesh.material)
        ? mesh.material.map((m) => m.clone())
        : mesh.material.clone();
    }

    for (const mesh of node.children) installDepthFade(mesh);

    const rest = axis.clone().sub(hinge);
    arms.push({
      side,
      node,
      meshes: node.children.slice(),
      hinge,
      /** Whatever this arm's materials started at, so a fade can be undone exactly. */
      baseOpacity: node.children.map((mesh) => (
        Array.isArray(mesh.material)
          ? mesh.material.map((m) => ({ opacity: m.opacity, transparent: m.transparent }))
          : [{ opacity: mesh.material.opacity, transparent: mesh.material.transparent }]
      )),
      /** Current fade, 0..1 opaque. Tracked so the materials are only touched on change. */
      fade: 1,
      /**
       * Unit direction the arm travels along, in the model's own space. A degenerate
       * arm — every vertex in one plane — falls back to straight back, which is
       * where a temple runs.
       */
      rest: rest.lengthSq() < 1e-12 ? new THREE.Vector3(0, 0, -1) : rest.normalize(),
      length: tip.distanceTo(hinge),
    });
  }

  return { arms, front };
}

/**
 * Assigns whole parts to the front or to one of the arms, or returns null.
 *
 * The test is how far *inboard* a part reaches, not where its centre of mass is. A
 * temple and a lens rim both extend to the full width of the frame, so their outer
 * edges say nothing; what separates them is that the rim carries on to the bridge
 * while the arm never leaves the side of the head. A part whose every vertex stays
 * outboard of the side cut is an arm — and that catches the pieces a centroid test
 * misses, like a gold hinge segment sitting forward of the cut alongside the rim.
 *
 * Returns null when the asset has nothing to work with — one welded mesh, or parts
 * that each straddle both sides — leaving the per-triangle cut to do its best.
 */
function classifyParts(triangles, partCount, centreX, xCut) {
  if (partCount < 2) return null;

  const inboard = new Array(partCount).fill(Infinity);
  const sum = new Array(partCount).fill(0);

  for (const tri of triangles) {
    for (const [x] of tri.p) {
      const offset = x - centreX;
      inboard[tri.part] = Math.min(inboard[tri.part], Math.abs(offset));
      sum[tri.part] += offset;
    }
  }

  const sides = inboard.map((reach, part) => (reach > xCut ? Math.sign(sum[part]) : 0));
  const left = sides.filter((s) => s > 0).length;
  const right = sides.filter((s) => s < 0).length;
  return left > 0 && right > 0 ? sides : null;
}

/**
 * The material governing one triangle.
 *
 * A mesh with an array material splits its draw calls by geometry group, so the
 * material depends on where the triangle falls. `toNonIndexed` preserves groups and
 * expands indices in order, so group ranges still address vertices directly.
 */
function materialAt(mesh, geometry, vertexIndex) {
  const material = mesh.material;
  if (!Array.isArray(material)) return material;
  for (const group of geometry.groups ?? []) {
    if (vertexIndex >= group.start && vertexIndex < group.start + group.count) {
      return material[group.materialIndex ?? 0] ?? material[0];
    }
  }
  return material[0];
}

/**
 * Builds one part of the frame as a group of meshes, one per material it uses.
 *
 * Grouping by material rather than merging into a single mesh is what keeps a
 * transmissive lens transmissive through the rebuild.
 */
function buildPart(tris, materials, origin) {
  if (tris.length === 0) return null;

  const group = new THREE.Group();
  materials.forEach((material, slot) => {
    const forSlot = tris.filter((t) => t.slot === slot);
    if (forSlot.length === 0) return;
    group.add(buildMesh(forSlot, material, origin));
  });
  return group;
}

/**
 * Where the arm meets the front: the centroid of its contact face.
 *
 * The band has to be tight. Averaging the frontmost tenth of the arm sounds
 * harmless and is not: on a 130mm temple that puts the pivot 5mm *behind* the
 * joint, and every millimetre behind the joint is a millimetre the joint itself
 * swings when the arm is aimed. The frame stays put, the end of the arm steps
 * sideways away from it, and the bend appears at that step instead of at the
 * hinge — which is exactly what a real pair does not do. A few millimetres of
 * face, or 3% of the arm for a coarse one, is enough to average out a ragged edge
 * without dragging the pivot backwards.
 */
/**
 * The z extent of a set of triangles.
 *
 * A loop, and it has to be. This was `Math.max(...tris.flatMap(...))`, which spreads
 * **three arguments per triangle** into a call — and a function call has an argument
 * limit. Every frame in the catalogue stayed under it until a 196k-triangle shield
 * arrived with 90k of that in its temples, and the whole app died on
 * `Maximum call stack size exceeded` at the moment the arms were split. A size limit
 * nobody wrote down, expressed as a crash in geometry code.
 */
function zRange(tris) {
  let min = Infinity;
  let max = -Infinity;
  for (const t of tris) {
    for (const p of t.p) {
      if (p[2] < min) min = p[2];
      if (p[2] > max) max = p[2];
    }
  }
  return { min, max };
}

function findHinge(tris) {
  const { min: zMin, max: zMax } = zRange(tris);
  const cut = zMax - Math.max((zMax - zMin) * 0.03, 0.002);
  return averageWhere(tris, (p) => p[2] >= cut);
}

/** The far end of the arm: the average of its rearmost vertices. */
function findTip(tris) {
  const { min: zMin, max: zMax } = zRange(tris);
  const cut = zMin + (zMax - zMin) * 0.1;
  return averageWhere(tris, (p) => p[2] <= cut);
}

/**
 * A point on the straight run of the temple, before the hook.
 *
 * Aiming by the tip is wrong: most arms curl downwards at the end to grip behind
 * the ear, so the tip sits below the arm's own axis. Point *that* at the ear and
 * the whole arm levers upwards — it ended up passing about a centimetre over the
 * top of the ear. Sampling short of the hook gives the axis the arm actually
 * travels along.
 */
function findAxisPoint(tris, fraction = 0.7) {
  const { min: zMin, max: zMax } = zRange(tris);
  const span = zMax - zMin;
  if (span < 1e-9) return findTip(tris);

  const target = zMax - span * fraction;
  const band = span * 0.08;
  const point = averageWhere(tris, (p) => Math.abs(p[2] - target) <= band);
  return point.lengthSq() > 0 ? point : findTip(tris);
}

function averageWhere(tris, predicate) {
  const out = new THREE.Vector3();
  let n = 0;
  for (const t of tris) {
    for (const p of t.p) {
      if (predicate(p)) { out.x += p[0]; out.y += p[1]; out.z += p[2]; n++; }
    }
  }
  return n ? out.multiplyScalar(1 / n) : out;
}

function buildMesh(tris, material, origin) {
  if (tris.length === 0) return null;

  const positions = new Float32Array(tris.length * 9);
  const normals = tris[0].n ? new Float32Array(tris.length * 9) : null;
  const uvs = tris[0].tex ? new Float32Array(tris.length * 6) : null;

  tris.forEach((t, i) => {
    for (let k = 0; k < 3; k++) {
      const o = i * 9 + k * 3;
      positions[o] = t.p[k][0] - origin.x;
      positions[o + 1] = t.p[k][1] - origin.y;
      positions[o + 2] = t.p[k][2] - origin.z;
      if (normals) {
        normals[o] = t.n[k][0];
        normals[o + 1] = t.n[k][1];
        normals[o + 2] = t.n[k][2];
      }
      if (uvs) {
        const u = i * 6 + k * 2;
        uvs[u] = t.tex[k][0];
        uvs[u + 1] = t.tex[k][1];
      }
    }
  });

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  if (normals) geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  else geometry.computeVertexNormals();
  if (uvs) geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));

  return new THREE.Mesh(geometry, material);
}

/**
 * Where the arm starts and finishes dissolving into the distance, in centimetres of
 * depth behind its own hinge.
 *
 * This is the piece that makes the arm robust to the occluder being approximate, and
 * every production try-on ships some version of it — WebAR.rocks calls the constant
 * `branchFadingZ`. The reason is worth stating, because a hard edge is not a neutral
 * choice: an arm that stops dead has to stop *somewhere*, and wherever that is, a
 * viewer reads it as the end of a solid object. On a face that reads as a spike
 * driven into the cheek — which is exactly how it was reported, twice. Nothing about
 * the geometry has to be wrong for it to look wrong; the head proxy only has to be a
 * few millimetres narrow, or the hair a centimetre thick, and the cut lands on skin.
 *
 * Fading by *depth behind the hinge* rather than by distance along the arm is what
 * makes it cost nothing at the poses where the arm matters. Head-on, the arm recedes
 * about a centimetre of depth per centimetre of run, so it is gone within four — and
 * head-on is precisely where a real temple is hidden by the head anyway. In profile
 * the arm runs across the view at nearly constant depth, never reaches the threshold,
 * and stays fully drawn, which is where a temple is the whole point.
 *
 * The range is set by what it must NOT do. A shorter, harder fade softens the head-on
 * edge more, and it also dissolves the temple at 45° of turn — where the arm runs
 * visibly along the side of the head to the ear and belongs there, and where the ear
 * has to be able to tuck it away. Measured: at 45° the arm reaches the ear at about
 * 6.6 cm of depth, so anything ending below that deletes it. This range leaves it
 * drawn and two-fifths dissolved at that point, is inert at profile (the arm crosses
 * the view at ~1 cm of depth and never reaches the threshold), and takes the edge off
 * the deep tail head-on, where the head is doing the real work anyway.
 *
 * Measured in camera space, which the placement's scale puts in centimetres, and
 * relative to the arm node's own origin — the hinge, because `buildPart` rebuilds each
 * arm about it. So the shader needs no uniforms and nothing to keep up to date.
 */
const ARM_FADE_DEPTH = { start: 4.0, end: 9.0 };

/**
 * Adds the depth fade to one arm mesh's materials.
 *
 * `customProgramCacheKey` is not optional: three caches compiled programs by material
 * type and defines, so without a distinct key an arm can be handed the program
 * compiled for an identical material that has no fade in it — silently, and only on
 * some frames.
 */
function installDepthFade(mesh) {
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  for (const material of materials) {
    // The fade is alpha, so the material has to be in the transparent pass whatever
    // its opacity says. Left opaque, the fade is computed and thrown away.
    material.transparent = true;
    material.onBeforeCompile = (shader) => {
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nvarying float vArmDepth;')
        .replace('#include <project_vertex>', `#include <project_vertex>
          vArmDepth = (modelViewMatrix * vec4(0.0, 0.0, 0.0, 1.0)).z - mvPosition.z;`);
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', `#include <common>
          varying float vArmDepth;`)
        .replace('#include <alphatest_fragment>', `
          diffuseColor.a *= 1.0 - smoothstep(
            ${ARM_FADE_DEPTH.start.toFixed(1)}, ${ARM_FADE_DEPTH.end.toFixed(1)}, vArmDepth);
          #include <alphatest_fragment>`);
    };
    material.customProgramCacheKey = () => 'temple-depth-fade';
    material.needsUpdate = true;
  }
}

const faceDirection = new THREE.Vector3();
const modelDirection = new THREE.Vector3();
const hingeInFace = new THREE.Vector3();
const inverseRotation = new THREE.Quaternion();

/**
 * Aims each arm at that side's ear.
 *
 * Works in face space and converts back, because that is where the ear is. The
 * placement's scale is uniform, so it does not affect directions and only the
 * rotation has to be undone.
 *
 * The arms are rotated about their hinges, which preserves their length — an arm
 * shorter than the distance to the ear will point correctly and stop short rather
 * than stretch, which is the honest outcome for a frame that is too small.
 */
/**
 * How far off level, in face space, the arm's straight run may pitch: tan(8°).
 *
 * Real temples run essentially level and the *tail* is bent to meet the ear — an
 * optician never tilts the whole arm off its line. Aiming the arm dead at the
 * measured ear rest point does exactly that whenever the measurement lands high
 * or low (the ear-top landmarks sit at the hairline and are routinely under
 * hair), and a whole arm pitched 15° off the front reads as a frame bent at the
 * hinges. So the aim takes the ear's *width* exactly — that is what keeps the arm
 * out of the cheek and inside the paddle — and takes its height only within the
 * range a real arm would run.
 */
const MAX_ARM_PITCH = Math.tan(THREE.MathUtils.degToRad(8));

/**
 * How far outside the head the arm is asked to run, in cm.
 *
 * A temple lies against the skin, so this wants to be as close to nothing as the
 * geometry allows: the arm has to be *outside* the occluder or the head swallows it,
 * and it has to be no further out than a real arm or it visibly stands off the head.
 * 2.5 mm is about the thickness of the hair a temple presses into, and it is above
 * the head proxy's own sampling error.
 */
const ARM_CLEARANCE = 0.25;

/**
 * Where along the run the clearance is asked for: the last quarter, at the ear.
 *
 * Asking for it everywhere is the obvious reading and it is wrong twice over. The
 * geometry first: the angle a clearance costs is the shortfall divided by how far
 * along the arm it occurs, so an obstacle 6 cm out demands half again the angle of
 * the same obstacle at 9 cm — and that angle carries on to the tip, standing it a
 * centimetre off the head. Then the anatomy: a frame narrower than the face *does*
 * press into the flesh at the temple, and being hidden there is correct — that stretch
 * of a real temple is behind the head's own outline. What must not happen is the arm
 * being swallowed for its whole length, and that is a question about the ear end.
 *
 * So this is a backstop rather than a shape. On a frame whose ear rest point already
 * sits outboard of the head — which is every frame here, because both are derived
 * from the same measured width — it does nothing at all.
 */
const CLEARANCE_FROM = 0.75;

/**
 * The most the arms may be splayed to clear the head, as |sin| of the angle off the
 * aim. 0.42 is 25°, well past anything a wearable frame needs and short of an arm
 * that has given up going backwards.
 */
const MAX_SPLAY = 0.42;

/** Points along the run the clearance is checked at. */
const CLEARANCE_SAMPLES = 14;

export function aimTemples(temples, anchors, placement, headProfile = null) {
  if (!temples || !anchors?.ears) return;

  inverseRotation.copy(placement.quaternion).invert();

  for (const arm of temples.arms) {
    const ear = arm.side < 0 ? anchors.ears.right : anchors.ears.left;

    // The hinge, carried into face space by the current placement.
    hingeInFace.copy(arm.hinge)
      .multiplyScalar(placement.scale)
      .applyQuaternion(placement.quaternion)
      .add(placement.position);

    faceDirection.copy(ear).sub(hingeInFace);
    if (faceDirection.lengthSq() < 1e-8) continue;

    // Keep the arm level, within the tolerance real arms have.
    const run = Math.hypot(faceDirection.x, faceDirection.z);
    const maxRise = run * MAX_ARM_PITCH;
    faceDirection.y = Math.min(Math.max(faceDirection.y, -maxRise), maxRise);
    faceDirection.normalize();

    splayClearOfHead(arm, headProfile, placement.scale);

    // Back into the model's own space, where the arm node lives.
    modelDirection.copy(faceDirection).applyQuaternion(inverseRotation);
    arm.node.quaternion.setFromUnitVectors(arm.rest, modelDirection);
  }
}

/**
 * Opens the arm outwards until its straight run clears the side of the head.
 *
 * The backstop for a frame narrow enough that even the ear end of its arms would sit
 * inside the skull — the arm is then swallowed for its whole length, hinge to tip,
 * and no work on the occluder can recover it because the arm is in the wrong place.
 * An optician fixes that by opening the temples at the hinge, and so does this: the
 * run is sampled against the head's own half-width over its last quarter and rotated
 * outwards by the smallest angle that clears it. See `CLEARANCE_FROM` for why the
 * front of the run is deliberately left alone, and why on the frames here this
 * usually does nothing at all.
 *
 * Two passes because the head's width is read at the samples' own height and depth,
 * which move when the arm does. The second pass changes the answer by well under a
 * millimetre; there is no third.
 */
function splayClearOfHead(arm, headProfile, scale) {
  if (!headProfile) return;

  const length = arm.length * scale;
  if (!(length > 0)) return;

  // Which way is outboard for this arm. Taken from the hinge rather than from
  // `arm.side` so it survives a placement that mirrors or rolls the frame.
  const outboard = Math.sign(hingeInFace.x) || arm.side;
  const hingeOut = outboard * hingeInFace.x;

  for (let pass = 0; pass < 2; pass++) {
    const current = outboard * faceDirection.x;
    let needed = current;

    for (let k = Math.ceil(CLEARANCE_FROM * CLEARANCE_SAMPLES); k <= CLEARANCE_SAMPLES; k++) {
      const s = (k / CLEARANCE_SAMPLES) * length;
      const y = hingeInFace.y + faceDirection.y * s;
      const z = hingeInFace.z + faceDirection.z * s;
      const want = headProfile.at(y, z);
      // No head at this height and depth — in front of the face, or past the
      // occiput. Nothing to clear.
      if (want <= 0) continue;
      const required = (want + ARM_CLEARANCE - hingeOut) / s;
      if (required > needed) needed = required;
    }

    if (needed <= current + 1e-6) return;

    const dx = Math.min(needed, MAX_SPLAY);
    const rest = Math.sqrt(Math.max(0, 1 - dx * dx));
    const lateral = Math.hypot(faceDirection.y, faceDirection.z);
    // A direction with no backwards component left to rescale would be an arm
    // pointing straight out of the side of the head; leave it alone rather than
    // divide by zero.
    if (lateral < 1e-6) return;
    faceDirection.set(
      outboard * dx,
      (faceDirection.y / lateral) * rest,
      (faceDirection.z / lateral) * rest,
    );
  }
}

/** Puts the arms back where the modeller left them. */
export function resetTemples(temples) {
  if (!temples) return;
  for (const arm of temples.arms) arm.node.quaternion.identity();
}

/**
 * Where the far arm starts and finishes dissolving, as |sin(yaw)|.
 *
 * 0.42 is about 25° of turn and 0.78 about 51°. The skull occluder takes the arm
 * over somewhere in that same range, and the overlap is deliberate: the ellipsoid is
 * a good approximation of a head and not a head, so its silhouette does not land
 * exactly where the real one does, and the arm crossing that seam is precisely the
 * artefact people read as the frame swimming. Fading across the same interval means
 * whatever is left over when the occluder takes charge is already close to invisible.
 */
const TEMPLE_FADE = { start: 0.42, end: 0.78 };

/**
 * Dissolves the arm on the far side of a turned head.
 *
 * The cheapest large win available for *perceived* stability, and it buys something
 * no amount of pose accuracy can. On a turned head the far temple is the longest,
 * thinnest, furthest-from-anchor piece of geometry on screen, so it is where every
 * millimetre of error is most legible — and it is simultaneously the piece a viewer
 * has the least information about, because on a real face it is mostly hidden. Every
 * production try-on hides it somehow; WebAR.rocks ships explicit constants for
 * exactly this (`branchFadingZ`, `branchBendingAngle`).
 *
 * Which arm is far is read off the pose rather than off a yaw estimate. The first
 * column of the head's world matrix is face-space +X expressed in camera space, so
 * its z component is `sin(yaw)` with the sign already in it: an arm at face-space
 * `side * halfWidth` sits at camera-space depth `side * xAxisZ`, and the camera looks
 * down -Z, so the arm with the more negative value is the one further away. No angle
 * conventions, no separate estimator, and it stays correct under roll and pitch too.
 */
export function fadeTemples(temples, headMatrixWorld) {
  if (!temples) return;

  // elements[2] is m[2][0] in column-major: the z of the transformed +X axis. The
  // head node carries a uniform scale, which cancels in the comparison below and in
  // the normalised magnitude, so it is not divided out.
  const xAxisZ = headMatrixWorld.elements[2];
  const turn = Math.min(Math.abs(xAxisZ), 1);

  for (const arm of temples.arms) {
    const far = arm.side * xAxisZ < 0;
    const fade = far ? 1 - smoothFade(TEMPLE_FADE.start, TEMPLE_FADE.end, turn) : 1;
    applyFade(arm, fade);
  }
}

function applyFade(arm, fade) {
  // Materials are only touched when the number actually moves. Writing `transparent`
  // recompiles the shader program, so setting it every frame would cost a recompile
  // per frame per material — an easy way to turn a fade into a stutter.
  if (Math.abs(fade - arm.fade) < 0.002) return;
  arm.fade = fade;

  arm.meshes.forEach((mesh, meshIndex) => {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach((material, slot) => {
      const base = arm.baseOpacity[meshIndex]?.[slot]
        ?? arm.baseOpacity[meshIndex]?.[0]
        ?? { opacity: 1, transparent: false };
      material.opacity = base.opacity * fade;
      // Restored rather than left on: a fully opaque material marked transparent is
      // sorted rather than depth-tested, and an arm that renders in the wrong order
      // against the lens behind it is a worse artefact than the one being fixed.
      const wantsTransparent = base.transparent || fade < 0.999;
      if (material.transparent !== wantsTransparent) {
        material.transparent = wantsTransparent;
        material.needsUpdate = true;
      }
    });
  });
}

/** Same curve as `smoothing.js`, kept local so this module stands alone. */
function smoothFade(edge0, edge1, x) {
  const t = Math.min(Math.max((x - edge0) / (edge1 - edge0), 0), 1);
  return t * t * (3 - 2 * t);
}
