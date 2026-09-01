/**
 * The renderer's half of the two-reader placement invariant.
 *
 * `fit/frame-from-mesh.ts` measures a glTF asset and writes `meshToFrame`;
 * `render/frame-mesh.ts` applies it. The tree already pins the writing half —
 * `asset.test.ts`'s "meshToFrame maps the file's own coordinates onto the
 * pad-centroid origin" — and pinned nothing at all about the reading half.
 * **No test in this tree referenced `frame-mesh.ts` before this file.**
 *
 * That is the gap this closes, and the two things it closes it around are the
 * two the file's own comments call out as silent when wrong:
 *
 *  1. `Matrix4.set` takes ROW-major arguments and stores column-major, so the
 *     row-major `meshToFrame` goes in element by element with no transpose.
 *     "Getting this backwards is a transform that looks almost right" — and a
 *     frame drawn a few millimetres from where it was fitted looks exactly like
 *     a tracking bug, which is the failure `frame-mesh.ts`'s header exists to
 *     prevent and which no gate could see.
 *  2. A transmissive material must NOT be `transparent`, and must keep
 *     `depthWrite`. Setting both sorts it into the transparent pass where it
 *     writes no depth, "and a lens that writes no depth cannot be occluded by
 *     the nose — which is the one thing this tree's whole occluder exists to
 *     do".
 *
 * **The transpose is not asserted as an argument order.** Checking that `set`
 * received the sixteen numbers in source order would pass on a matrix that is
 * symmetric by accident and, worse, would restate the implementation instead of
 * testing it. What is asserted is the MEANING: a probe point pushed through the
 * matrix three ends up holding must land where the row-major source says it
 * lands. `Matrix4` here is three's own `set` — the real element assignment,
 * copied and named as such — so a transpose in `frame-mesh.ts` moves the point
 * and the test goes red.
 *
 * Instantiated out of the compiled build against a stub three, for the reason
 * `scene.test.ts` and `layout.test.ts` give: `src/render/` pulls in three.js,
 * which is a vendored browser file here and the whole reason the isolation
 * boundary exists. This slices the WHOLE module body rather than one function,
 * so `isLens`, `isTransmissive`, `compositeTransmissive` and `makeLens` are the
 * shipped ones rather than a second copy — and a new import in `frame-mesh.ts`
 * surfaces as a loud ReferenceError here rather than as a silent gap.
 *
 * Every assertion below names how to make it red — and that was measured rather
 * than asserted. Twelve mutations were applied to the compiled module this file
 * reads and the suite was re-run against each: transposing the sixteen matrix
 * arguments, making a transmissive material `transparent`, taking `depthWrite`
 * away, letting `matrixAutoUpdate` back on, dropping the tone-mapping opt-out,
 * overwriting the asset's authored `ior` and `transmission`, widening `isLens`
 * to anything transmissive, dropping the `isMesh` guard, silencing the lens
 * counter, skipping `makeLens` on a named lens, and never calling
 * `updateMatrixWorld`. **All twelve go red. None survived.** A test that cannot
 * go red is a check that cannot fail, which is the defect this tree is named
 * after, so the survivor count is the number worth quoting here.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { readFileSync } from 'node:fs';

const DOUBLE_SIDE = 2; // THREE.DoubleSide

/** three's own `Matrix4.set`: row-major in, column-major in `elements`. */
class Matrix4 {
  elements: number[] = new Array(16).fill(0);

  set(
    n11: number, n12: number, n13: number, n14: number,
    n21: number, n22: number, n23: number, n24: number,
    n31: number, n32: number, n33: number, n34: number,
    n41: number, n42: number, n43: number, n44: number,
  ) {
    const te = this.elements;
    te[0] = n11; te[4] = n12; te[8] = n13; te[12] = n14;
    te[1] = n21; te[5] = n22; te[9] = n23; te[13] = n24;
    te[2] = n31; te[6] = n32; te[10] = n33; te[14] = n34;
    te[3] = n41; te[7] = n42; te[11] = n43; te[15] = n44;
    return this;
  }
}

/** A point through a column-major 4x4, three's convention. */
function applyColumnMajor(te: readonly number[], p: readonly number[]): number[] {
  return [
    te[0] * p[0] + te[4] * p[1] + te[8] * p[2] + te[12],
    te[1] * p[0] + te[5] * p[1] + te[9] * p[2] + te[13],
    te[2] * p[0] + te[6] * p[1] + te[10] * p[2] + te[14],
  ];
}

/** The same point through the row-major source, which is what `mul4` builds. */
function applyRowMajor(m: ArrayLike<number>, p: readonly number[]): number[] {
  return [
    m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3],
    m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7],
    m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11],
  ];
}

interface StubMaterial {
  name?: string;
  transmission?: number;
  ior?: number;
  transparent?: boolean;
  depthWrite?: boolean;
  toneMapped?: boolean;
  shadowSide?: number;
  roughness?: number;
  metalness?: number;
  thickness?: number;
  needsUpdate?: boolean;
}

interface StubNode {
  name?: string;
  isMesh?: boolean;
  material?: StubMaterial | StubMaterial[];
  castShadow?: boolean;
  receiveShadow?: boolean;
}

/**
 * Instantiates the whole of `render/frame-mesh.js` against a stub three.
 *
 * The slice starts after the last import, so the module's own helpers come with
 * it. `assert.ok` on the anchors rather than a silent `indexOf`: if the imports
 * are reordered or a third one is added, this fails loudly instead of eval'ing
 * a truncated module.
 */
function instantiateFrameMesh(nodes: StubNode[]) {
  const text = readFileSync(new URL('../src/render/frame-mesh.js', import.meta.url), 'utf8');

  const lastImport = text.lastIndexOf('\nimport ');
  assert.ok(lastImport >= 0, 'render/frame-mesh no longer imports anything — the slice is wrong');
  const bodyAt = text.indexOf('\n', lastImport + 1);
  assert.ok(bodyAt > 0, 'could not find the end of the import block in render/frame-mesh');
  assert.ok(
    text.slice(0, bodyAt).includes("from 'three'"),
    'render/frame-mesh no longer imports three — this harness stubs the wrong thing',
  );
  const body = text.slice(bodyAt).replace(/^export\s+/gm, '');
  assert.ok(
    body.includes('function loadFrameMesh('),
    'loadFrameMesh has been renamed or moved out of render/frame-mesh',
  );

  const loaderCalls: string[] = [];
  class GLTFLoader {
    async loadAsync(url: string) {
      loaderCalls.push(url);
      return {
        scene: {
          traverse(fn: (n: StubNode) => void) {
            for (const n of nodes) fn(n);
          },
        },
      };
    }
  }

  const groups: any[] = [];
  const THREE: any = {
    DoubleSide: DOUBLE_SIDE,
    Group: class {
      name = '';
      matrixAutoUpdate = true;
      matrix = new Matrix4();
      children: unknown[] = [];
      worldUpdates = 0;
      constructor() { groups.push(this); }
      add(child: unknown) { this.children.push(child); }
      updateMatrixWorld() { this.worldUpdates++; }
    },
  };

  const loadFrameMesh = new Function(
    'THREE', 'GLTFLoader', `${body}\nreturn loadFrameMesh;`,
  )(THREE, GLTFLoader);

  return { loadFrameMesh, groups, loaderCalls };
}

/**
 * A deliberately ASYMMETRIC row-major affine matrix.
 *
 * Every off-diagonal differs from its transpose partner and the translation row
 * is non-zero, so a transpose is observable in the probe points rather than
 * cancelling. A symmetric matrix here would make the headline test vacuous.
 */
const MESH_TO_FRAME = Float64Array.from([
  1000, 7, -3, 11,
  -5, 900, 13, -17,
  2, -19, 1100, 23,
  0, 0, 0, 1,
]);

const PROBES = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0.3, -0.7, 0.11]];

const meshAsset = (over: Record<string, unknown> = {}) => ({
  id: 'stub-frame',
  source: { url: 'assets/glasses/stub.glb', meshToFrame: MESH_TO_FRAME },
  ...over,
});

describe('render/frame-mesh applies the solve\'s matrix, and nothing else', () => {
  it('writes meshToFrame un-transposed — a probe point lands where the solve says', async () => {
    // THE headline. `Matrix4.set` is row-major in, column-major out, and
    // `meshToFrame` is row-major (`fit/frame-from-mesh.ts`'s `mul4` says so in
    // its own docstring). Transposing the sixteen arguments in
    // `render/frame-mesh.ts` — the natural slip its comment warns about —
    // leaves the diagonal intact and moves every probe but the origin, which is
    // exactly why "a transform that looks almost right" is the danger.
    const { loadFrameMesh, groups } = instantiateFrameMesh([]);
    await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');

    assert.equal(groups.length, 1, 'loadFrameMesh built no group, or more than one');
    const te = groups[0].matrix.elements;

    for (const p of PROBES) {
      const got = applyColumnMajor(te, p);
      const want = applyRowMajor(MESH_TO_FRAME, p);
      for (let axis = 0; axis < 3; axis++) {
        assert.ok(
          Math.abs(got[axis] - want[axis]) < 1e-9,
          `probe [${p}] landed at ${got[axis]} on axis ${axis} where meshToFrame puts it at `
          + `${want[axis]}. The renderer and the solver now disagree about where the frame is, `
          + 'which draws as a tracking bug and is not one.',
        );
      }
    }
  });

  it('leaves matrixAutoUpdate off, so three cannot recompose the matrix', async () => {
    // Red if `matrixAutoUpdate = false` is dropped. three would then recompose
    // the matrix from Float32 position/quaternion/scale every frame — a
    // decompose/recompose round trip of a matrix carrying a 1000x scale, for no
    // reason. The docstring promises this; nothing checked it.
    const { loadFrameMesh, groups } = instantiateFrameMesh([]);
    await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');
    assert.equal(groups[0].matrixAutoUpdate, false,
      'matrixAutoUpdate is on, so three will recompose the solved matrix through Float32 PQS');
    assert.ok(groups[0].worldUpdates >= 1,
      'the group was never updateMatrixWorld()d, so the matrix it was handed is not live');
  });

  it('resolves the asset url against the base, and refuses a parametric frame', async () => {
    // Red if the `new URL(source.url, baseUrl)` resolution is dropped (the
    // loader would be handed a relative path), or if the no-source guard goes —
    // a parametric frame would then fail somewhere inside the loader instead of
    // at the boundary, with a message naming neither the frame nor the file
    // that does draw it.
    //
    // The base is `document.baseURI` in the app (main.ts), which ends in a
    // slash, so the asset path resolves INSIDE it. This expectation was written
    // the other way round first and the test caught it — worth keeping as the
    // reason the exact string is pinned rather than a suffix match.
    const { loadFrameMesh, loaderCalls } = instantiateFrameMesh([]);
    await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');
    assert.deepEqual(loaderCalls, ['https://example.invalid/app/assets/glasses/stub.glb']);

    await assert.rejects(
      () => loadFrameMesh({ id: 'parametric-frame' }, 'https://example.invalid/app/'),
      /parametric-frame.*no mesh source/s,
      'a parametric asset did not fail at the boundary with a message naming it',
    );
  });
});

describe('render/frame-mesh keeps transmissive materials in the depth pass', () => {
  const transmissive = (over: Partial<StubMaterial> = {}): StubMaterial =>
    ({ name: 'Frame_Acetate_Translucent', transmission: 0.4, ...over });

  it('a transmissive FRAME part is composited, not made transparent', async () => {
    // Red if `compositeTransmissive` sets `transparent = true` or drops
    // `depthWrite = true`. Either sorts the material into the transparent pass,
    // where it writes no depth and the nose occluder cannot hide it — the one
    // job the occluder exists for. `horizon-sage`'s translucent acetate frame
    // and `khronos`'s nose pads are the real materials in this class.
    const node: StubNode = { name: 'FrameFront', isMesh: true, material: transmissive() };
    const { loadFrameMesh } = instantiateFrameMesh([node]);
    const out = await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');

    const m = node.material as StubMaterial;
    assert.equal(m.transparent, false,
      'a transmissive material was marked transparent — it now sorts into the transparent '
      + 'pass and stops writing depth');
    assert.equal(m.depthWrite, true,
      'a transmissive material stopped writing depth, so the nose occluder cannot hide it');
    assert.equal(m.toneMapped, false,
      'the glass is tone-mapped a second time on top of the camera\'s own curve');
    assert.equal(m.shadowSide, DOUBLE_SIDE, 'a thin shell will drop half its shadow');
    assert.equal(m.needsUpdate, true, 'the material change will not reach the GPU');

    // Counted as transmissive, NOT as a lens: this is the counter-example the
    // file header names. Red if `isLens` widens to "anything transmissive".
    assert.equal(out.transmissiveCount, 1);
    assert.equal(out.lensPartCount, 0,
      'a translucent FRAME was counted as a lens — the header\'s own counter-example');
    assert.equal(m.roughness, undefined,
      'the ophthalmic treatment was applied to a frame part');
  });

  it('a named lens part gets the ophthalmic treatment ON TOP of the compositing', async () => {
    // Red if `makeLens` stops calling `compositeTransmissive` — the lens would
    // keep its optics and lose its depth, which is the subtler half of the same
    // occluder failure.
    const material: StubMaterial = { name: 'Lens_Prescription_Glass' };
    const node: StubNode = { name: 'LensLeft', isMesh: true, material };
    const { loadFrameMesh } = instantiateFrameMesh([node]);
    const out = await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');

    assert.equal(material.transparent, false, 'a lens was marked transparent');
    assert.equal(material.depthWrite, true, 'a lens stopped writing depth');
    assert.equal(material.transmission, 1, 'a lens with no declared transmission stayed opaque');
    assert.equal(material.metalness, 0, 'a lens is a dielectric, not a metal');
    assert.ok((material.thickness ?? 0) > 0, 'a lens with no thickness refracts by nothing');
    assert.equal(out.lensPartCount, 1);
  });

  it('the file\'s own ior and transmission win over the defaults', async () => {
    // Red if `makeLens` overwrites rather than fills in. Meshy authors 1.586
    // into `Lens_Prescription_Glass`; clobbering a real authored value with a
    // default is the same class of defect as a renderer re-deriving placement.
    const material: StubMaterial = { name: 'LensGlass', transmission: 0.8, ior: 1.74 };
    const { loadFrameMesh } = instantiateFrameMesh([
      { name: 'lens', isMesh: true, material },
    ]);
    await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');
    assert.equal(material.ior, 1.74, 'the asset\'s authored ior was overwritten by the default');
    assert.equal(material.transmission, 0.8, 'the asset\'s authored transmission was overwritten');
  });

  it('counts what it found, so a silent miss is visible to the caller', async () => {
    // The header's stated reason for exporting the counts: "a matcher that
    // returns nothing looks exactly like a frame with no lenses, and v1 shipped
    // that". Red if the counters stop counting, or if a node is visited twice.
    // Lens identity is matched on the NODE name here and the MATERIAL name in
    // the second — both paths, because `isLens` checks both.
    const { loadFrameMesh } = instantiateFrameMesh([
      { name: 'LensLeft', isMesh: true, material: { name: 'Glass' } },
      { name: 'part', isMesh: true, material: { name: 'Lens_Gradient', transmission: 0.9 } },
      { name: 'nose_pads', isMesh: true, material: transmissive({ name: 'nose_pads' }) },
      { name: 'TempleArm', isMesh: true, material: { name: 'Acetate' } },
      { name: 'AnEmptyGroup', isMesh: false, material: transmissive() },
    ]);
    const out = await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');

    assert.equal(out.lensPartCount, 2,
      'lens parts were miscounted — the caller can no longer compare against what the '
      + 'catalogue says the asset has');
    assert.equal(out.transmissiveCount, 1,
      'the transmissive count is not the non-lens transmissive parts');
  });

  it('a non-mesh node is left entirely alone', async () => {
    // Red if the `if (!node.isMesh) return` guard goes: three's scene graph
    // carries Object3D, Bone and Light nodes whose `material` is undefined, and
    // the traversal would start writing shadow flags onto them.
    const node: StubNode = { name: 'Armature', isMesh: false, material: transmissive() };
    const { loadFrameMesh } = instantiateFrameMesh([node]);
    await loadFrameMesh(meshAsset(), 'https://example.invalid/app/');
    const m = node.material as StubMaterial;
    assert.equal(m.transparent, undefined, 'a non-mesh node was given material flags');
    assert.equal(node.castShadow, undefined, 'a non-mesh node was given shadow flags');
  });
});
