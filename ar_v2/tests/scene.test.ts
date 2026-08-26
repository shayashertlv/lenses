/**
 * The renderer, asked what it actually did.
 *
 * `src/render/` cannot be imported under Node — it pulls in three.js, which is
 * the whole reason the isolation boundary exists. So this reads `createScene`
 * out of the compiled build and runs it against a recording stub, exactly as
 * `app.test.ts` does for `startLoop`.
 *
 * **Not `assert.match` on the source text, and the reason is measured.**
 * `tsconfig.json` sets no `removeComments`, so every docstring survives into
 * `dist/`. Before this stage `dist/src/render/scene.js` already contained the
 * word "environment" — inside a comment reading "Lighting: a fixed neutral
 * environment" — while nothing in the file assigned `scene.environment` at all.
 * A textual gate on an English word here is a check that cannot fail, which is
 * precisely the bug this tree's doctrine is named after. Instantiating the
 * function asks what it DID rather than what it says.
 *
 * Every assertion below names how to make it red.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { readFileSync } from 'node:fs';

import { buildHeadWithEars, reloftSkull } from '../src/core/head.js';
import {
  occluderBiasedMatrix, poseToGLMatrix, principalPointOffset, verticalFovDegFor,
} from '../src/render/convert.js';
import { loadTemplateMesh } from '../src/testkit/fixtures.js';

/**
 * Instantiates `createScene` out of the compiled build against a stub three.js.
 *
 * Module scope rather than inside one `describe`, because the occluder suite
 * below needs the same harness and a second copy of it would be a second thing
 * to keep in step with `scene.ts`.
 */
function instantiateScene() {
    const text = readFileSync(new URL('../src/render/scene.js', import.meta.url), 'utf8');
    const start = text.indexOf('export async function createScene(');
    assert.ok(start >= 0, 'createScene has been renamed or moved out of render/scene');

    // NOT `indexOf('{', start)`. The signature is `createScene(canvas, options = {})`,
    // and an object default parameter puts a brace INSIDE the parameter list —
    // the brace counter `app.test.ts` uses for `startLoop` would start counting
    // there and slice out a syntax error. Match the parameter parens first. That
    // counter works today only because `startLoop(app)` has no default arguments.
    let paren = 0;
    let bodyAt = -1;
    for (let i = text.indexOf('(', start); i < text.length; i++) {
      if (text[i] === '(') paren++;
      else if (text[i] === ')' && --paren === 0) { bodyAt = text.indexOf('{', i); break; }
    }
    assert.ok(bodyAt > 0, 'could not find the body of createScene');

    let depth = 0;
    let end = start;
    for (let i = bodyAt; i < text.length; i++) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}' && --depth === 0) { end = i + 1; break; }
    }

    const made: string[] = [];
    const record = (name: string) => class {
      constructor(..._args: any[]) { made.push(name); }
    };
    class Vec3 {
      x = 0; y = 0; z = 0;
      constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
      set(x: number, y: number, z: number) { this.x = x; this.y = y; this.z = z; return this; }
      copy(v: Vec3) { return this.set(v.x, v.y, v.z); }
      add(v: Vec3) { return this.set(this.x + v.x, this.y + v.y, this.z + v.z); }
    }
    class Node {
      children: any[] = [];
      visible = true;
      matrixAutoUpdate = true;
      position = new Vec3();
      userData: Record<string, unknown> = {};
      // RECORDS what it was handed. The shipped stub threw the matrix away,
      // which made the occluder's camera-axis bias unobservable — the one thing
      // `setHeadPose` does to `occluderNode` that a test can see.
      matrix: { elements: number[]; fromArray(a: ArrayLike<number>): void } = {
        elements: [],
        fromArray(a: ArrayLike<number>) { this.elements = Array.from(a); },
      };
      constructor() { made.push('Object3D'); }
      add(child: any) { this.children.push(child); }
      remove(child: any) { this.children = this.children.filter((c) => c !== child); }
      updateMatrixWorld() { /* stub */ }
      getWorldPosition(v: Vec3) { return v; }
    }
    class DirectionalLight extends Node {
      castShadow = false;
      target = new Node();
      shadow = {
        mapSize: { set(_w: number, _h: number) { /* stub */ } },
        radius: 0,
        bias: 0,
        normalBias: 0,
        camera: { left: 0, right: 0, top: 0, bottom: 0, near: 0, far: 0 },
      };
      constructor(_colour?: number, _intensity?: number) {
        super();
        made.push('DirectionalLight');
      }
    }
    let nextId = 1;
    class Attribute {
      needsUpdate = false;
      constructor(public array: ArrayLike<number>, public itemSize: number) {}
    }
    class Geometry {
      id = nextId++;
      disposed = 0;
      normalsComputed = 0;
      boundingSpheres = 0;
      index: Attribute | null = null;
      attributes: Record<string, Attribute> = {};
      setAttribute(name: string, attr: Attribute) { this.attributes[name] = attr; }
      getAttribute(name: string) { return this.attributes[name]; }
      setIndex(attr: Attribute) { this.index = attr; }
      computeVertexNormals() { this.normalsComputed++; }
      computeBoundingSphere() { this.boundingSpheres++; }
      dispose() { this.disposed++; }
    }
    class MaterialStub {
      id = nextId++;
      disposed = 0;
      constructor(params: Record<string, unknown> = {}) { Object.assign(this, params); }
      dispose() { this.disposed++; }
    }
    class MeshStub extends Node {
      renderOrder = 0;
      castShadow = false;
      receiveShadow = false;
      constructor(public geometry: Geometry, public material: MaterialStub) {
        super();
      }
    }
    let pmremDisposals = 0;
    const THREE: any = {
      Scene: class {
        environment: any = null;
        background: any = null;
        children: any[] = [];
        constructor() { made.push('Scene'); }
        add(child: any) { this.children.push(child); }
      },
      PerspectiveCamera: class {
        fov = 0; aspect = 1;
        position = new Vec3();
        projectionMatrix = { elements: new Array(16).fill(0) };
        projectionMatrixInverse = { copy() { return this; }, invert() { return this; } };
        // Recorded, because the principal-point shear is the one thing
        // `applyIntrinsics` does that a stub can observe at all.
        viewOffset: number[] | null = null;
        viewOffsetClears = 0;
        constructor() { made.push('PerspectiveCamera'); }
        lookAt() { /* stub */ }
        updateProjectionMatrix() { /* stub */ }
        setViewOffset(...args: number[]) { this.viewOffset = args; }
        clearViewOffset() { this.viewOffset = null; this.viewOffsetClears++; }
      },
      WebGLRenderer: class {
        shadowMap: Record<string, unknown> = {};
        outputColorSpace: unknown;
        toneMapping: unknown;
        toneMappingExposure = 0;
        constructor() { made.push('WebGLRenderer'); }
        setPixelRatio() { /* stub */ }
        setSize() { /* stub */ }
        render() { /* stub */ }
        dispose() { /* stub */ }
      },
      PMREMGenerator: class {
        constructor() { made.push('PMREMGenerator'); }
        fromScene() { return { texture: { isTexture: true, dispose() { /* stub */ } } }; }
        dispose() { pmremDisposals++; }
      },
      Object3D: Node,
      DirectionalLight,
      AmbientLight: record('AmbientLight'),
      Vector3: Vec3,
      SRGBColorSpace: 'srgb',
      ACESFilmicToneMapping: 4,
      PCFShadowMap: 1,
      DoubleSide: 2,
      CanvasTexture: record('CanvasTexture'),
      // The four below are REAL enough to answer questions, because
      // `setOccluder` is the one method whose whole contract is which objects
      // it built, which it SHARED, and which it disposed. A `record()` stub
      // that only counts constructions cannot see any of that.
      BufferGeometry: Geometry,
      BufferAttribute: Attribute,
      Mesh: MeshStub,
      MeshBasicMaterial: class extends MaterialStub {
        constructor(params: Record<string, unknown> = {}) { super(params); made.push('MeshBasicMaterial'); }
      },
      ShadowMaterial: class extends MaterialStub {
        constructor(params: Record<string, unknown> = {}) { super(params); made.push('ShadowMaterial'); }
      },
    };
    const RoomEnvironment = record('RoomEnvironment');

    // **The slice is short seven names, and every test that existed before this
    // one passed only because all seven are referenced inside HANDLE METHODS
    // rather than in `createScene`'s straight-line body.** Instantiation
    // therefore succeeded and the ReferenceError was deferred until a method was
    // called — which nothing did. Measured on the previous stub: `setOccluder`
    // throws `buildHeadWithEars is not defined`, `setHeadPose` throws
    // `poseToGLMatrix is not defined`, `applyIntrinsics` throws
    // `verticalFovDegFor is not defined`, and `nudgeOccluder` silently no-ops.
    //
    // Six of the seven are REAL imports, and that is the point rather than a
    // convenience: `core/head.ts` has no imports at all and `render/convert.ts`
    // has only type imports, so both compile to modules Node can load. The
    // occluder tests below therefore run the actual loft and the actual
    // convention conversion against a stub three.js, not a second copy of either.
    //
    // `OCCLUDER_BIAS_MM` is the exception. It is exported from `scene.ts`, and
    // importing that module makes Node resolve `three` — a vendored browser
    // file. So it is read out of the same compiled text the slice came from,
    // which keeps it the SHIPPED value rather than a number restated here.
    const biasMatch = /OCCLUDER_BIAS_MM = (-?[\d.]+)/.exec(text);
    assert.ok(biasMatch, 'OCCLUDER_BIAS_MM was renamed — the bias test would pass vacuously');
    const OCCLUDER_BIAS_MM = Number(biasMatch[1]);

    const createScene = new Function(
      'THREE', 'RoomEnvironment', 'console', 'globalThis',
      'buildHeadWithEars', 'reloftSkull', 'poseToGLMatrix', 'occluderBiasedMatrix',
      'principalPointOffset', 'verticalFovDegFor', 'OCCLUDER_BIAS_MM',
      `${text.slice(start, end).replace(/^export\s+/, '')}\nreturn createScene;`,
    )(THREE, RoomEnvironment, { warn() { /* quiet */ }, info() { /* quiet */ } },
      { devicePixelRatio: 2 },
      buildHeadWithEars, reloftSkull, poseToGLMatrix, occluderBiasedMatrix,
      principalPointOffset, verticalFovDegFor, OCCLUDER_BIAS_MM);

    return {
      createScene, made, THREE, OCCLUDER_BIAS_MM,
      lights: () => made.filter((m) => m === 'DirectionalLight').length,
      pmremDisposals: () => pmremDisposals,
    };
}

describe('the scene is set up to render a real asset', () => {
  it('renders in sRGB with tone mapping, which a PBR asset needs', async () => {
    // RED: delete either assignment in scene.ts. Both read `undefined` on the
    // shipped file before this stage — measured, not assumed.
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    assert.equal(handle.renderer.outputColorSpace, s.THREE.SRGBColorSpace,
      'the renderer is not in sRGB — every texture in the catalogue renders washed out');
    assert.equal(handle.renderer.toneMapping, s.THREE.ACESFilmicToneMapping,
      'no tone mapping — the frame is composited over a camera image that has already '
      + 'been through its own tone curve, so a linear frame reads as a sticker');
  });

  it('builds an environment map and hands the generator back', async () => {
    // RED: delete the PMREM block, or forget `pmrem.dispose()`.
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    assert.ok(s.made.includes('PMREMGenerator'),
      'no environment map is built — a PBR frame renders as a flat silhouette, because a '
      + 'material with nothing to reflect has nothing to show. A metal ferrule and a black '
      + 'acetate temple come out the same grey.');
    assert.ok(s.made.includes('RoomEnvironment'), 'the environment map has no source scene');
    assert.ok(handle.scene.environment, 'scene.environment was never assigned');
    assert.equal(s.pmremDisposals(), 1,
      'the PMREM generator was not disposed — it holds a render target for the life of the page');
  });

  it('casts a shadow, and its frustum is in millimetres rather than v1\'s centimetres', async () => {
    // RED (shadows off): delete `renderer.shadowMap.enabled = true`, or `key.castShadow`.
    // RED (units): paste v1's numbers. v1's scene is in CENTIMETRES — its
    //   `shadow.camera.left` is -25, `far` 300, `normalBias` 0.6 — and this one is
    //   in millimetres (`MM_TO_SCENE` is 1). Copied across, the frustum is ten
    //   times too small: the shadow falls off the edge of its own map and simply
    //   is not there. This assertion exists for that specific copy, because the
    //   symptom is a missing shadow rather than an error.
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    assert.equal(handle.renderer.shadowMap.enabled, true, 'shadows are switched off');

    const key = handle.scene.children.find((c: any) => c.castShadow);
    assert.ok(key, 'no light casts a shadow — nothing grounds the frame on the face');
    const half = Math.min(key.shadow.camera.right, key.shadow.camera.top);
    assert.ok(half >= 150,
      `the shadow camera's half-extent is ${half}. A head is ~250 mm across, so ${half} is `
      + "centimetres — v1's unit, not this scene's");
    assert.ok(key.shadow.camera.far >= 1000,
      `shadow far plane ${key.shadow.camera.far} is centimetres too`);
    assert.ok(key.shadow.normalBias >= 1,
      `normalBias ${key.shadow.normalBias} is v1's centimetre value`);
  });

  it('puts a second light where the wearer\'s screen is', async () => {
    // RED: delete the `screen` light.
    //
    // v1 measured what that costs. A lens facing the camera reflects whatever is
    // BEHIND the camera; the procedural room has no bright feature back there and
    // the key sits ~30 degrees off the view axis, which is thirty times the width
    // of a polished lens's specular lobe. Scaling the lens's environment
    // reflection TWENTY-FOLD moved a single pixel. The frame then renders with
    // empty rims — which is exactly how a real wearer reported it.
    const s = instantiateScene();
    await s.createScene({} as any, { preferWebGPU: false });
    assert.ok(s.lights() >= 2,
      'only one directional light — a lens facing the camera has nothing to reflect, and '
      + 'a frame with modelled lenses renders as a frame with empty rims');
  });
});

/**
 * `setOccluder`, which had zero coverage repo-wide and is the whole illusion.
 *
 * The occluder is the only geometry standing between a temple and the camera:
 * without it an arm draws over the cheek it should be behind, and the frame
 * stops being on the face and starts being a sticker on the video. Nothing
 * tested it. This file's four other tests are sRGB, the environment map, the
 * shadow frustum's units and the screen light — every one about `createScene`'s
 * straight-line body, none about anything the handle DOES.
 *
 * These run the real `buildHeadWithEars` against the stub three.js, so the
 * vertex ordering and the count are the shipped loft's rather than a fixture's.
 */
describe('the occluder is built, shared, and disposed exactly once', () => {
  const mesh = loadTemplateMesh();
  const earRests: [Float64Array, Float64Array] = [
    Float64Array.of(-70, 6, -42), Float64Array.of(70, 6, -42),
  ];

  async function withOccluder() {
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    handle.setOccluder(mesh.positions, mesh.indices, earRests);
    const node = handle.occluderNode;
    return { s, handle, node, occluder: node.children[0], catcher: node.children[1] };
  }

  it('gives the occluder and the shadow catcher ONE geometry, not two equal ones', async () => {
    // The strongest assertion here because it is an identity check rather than a
    // value check. A clone is value-identical the moment it is made and only
    // diverges once the head takes a measured shape — which is precisely when
    // v1's shadow disappeared, and why `scene.ts` records that the catcher is
    // depth-tested against the occluder, so an occluder even a tenth of a
    // millimetre in front of it culls the shadow entirely.
    //
    // RED: `new THREE.Mesh(geometry.clone(), catcherMaterial)`. Every value
    // check would still pass; `===` fails instantly.
    const { node, occluder, catcher } = await withOccluder();
    assert.equal(node.children.length, 2, 'expected an occluder and a shadow catcher');
    assert.equal(occluder.geometry, catcher.geometry,
      'the catcher has a geometry of its own — it stays identical until the first '
      + 'measured face, and then the shadow vanishes');
    const shared = occluder.geometry.getAttribute('position').array;
    assert.equal(catcher.geometry.getAttribute('position').array, shared,
      'the two stopped sharing their position buffer');
  });

  it('puts the face vertices FIRST and applies no transform of its own', async () => {
    // The seat-and-occluder invariant, asserted exactly — no tolerance.
    //
    // `scene.ts` says the face part of this surface is "bit-identical to
    // `model.positions`". That is true of `head.positions` (Float64) and FALSE
    // of what reaches the GPU: after the Float32Array narrowing, 1376 of the
    // 1404 face components differ, by up to 3.8e-6 mm. What IS exact, for all
    // 1404, is `array[i] === Math.fround(positions[i])` — and that is the
    // invariant worth pinning, because it says `setOccluder` applies NO flip, NO
    // scale and NO offset between the surface the contact solve seated against
    // and the buffer the GPU draws.
    //
    // RED: insert a Y/Z negation (the CV->GL convention, which `convert.ts`
    // records was shipped once and cost 127 mm), a `MM_TO_SCENE` multiply, or
    // reorder the buffer so the lofted skull comes first. All three fail at
    // component 1 or 2.
    const { occluder } = await withOccluder();
    const position = occluder.geometry.getAttribute('position');
    assert.equal(position.itemSize, 3);

    const head = buildHeadWithEars(mesh.positions, mesh.indices, earRests);
    assert.equal(position.array.length, head.positions.length,
      'the buffer is not the head the loft built');
    assert.ok(position.array.length > mesh.positions.length,
      'the occluder is the same size as the face — the skull was never lofted, and a '
      + 'temple at yaw has nothing to hide behind');

    for (let i = 0; i < mesh.positions.length; i++) {
      assert.equal(position.array[i], Math.fround(mesh.positions[i]),
        'face component ' + i + ' was transformed between the seat and the GPU');
    }
    assert.ok(occluder.geometry.index, 'the occluder has no index buffer');
    assert.equal(occluder.geometry.index.itemSize, 1,
      'an index buffer with itemSize 3 draws garbage');
  });

  it('is depth-only, drawn first, and its catcher writes no depth', async () => {
    // Four separate visual failures, one assertion each:
    //   colorWrite    -> a grey head painted over the camera feed
    //   renderOrder   -> the occluder drawn AFTER the frame, hiding nothing
    //   catcher depth -> the catcher culls the frame it exists to receive
    //   normals       -> `key.shadow.normalBias` offsets along them; shadow acne
    // RED: delete any one of them.
    const { s, occluder, catcher } = await withOccluder();
    assert.equal(occluder.material.colorWrite, false,
      'the occluder writes colour — a grey head over the video');
    assert.equal(occluder.renderOrder, -1,
      'the occluder is not drawn first, so it hides nothing');
    assert.equal(occluder.receiveShadow, false,
      'a depth-only mesh that receives shadow is a contradiction');
    assert.equal(catcher.material.depthWrite, false,
      'the shadow catcher writes depth and will cull the frame');
    assert.equal(catcher.receiveShadow, true, 'the catcher receives no shadow');
    // Swapping the two materials would pass every value check above.
    assert.ok(occluder.material instanceof s.THREE.MeshBasicMaterial);
    assert.ok(catcher.material instanceof s.THREE.ShadowMaterial);
    assert.equal(occluder.geometry.normalsComputed, 1,
      'normals were never computed — the key light biases along them');
  });

  it('disposes the previous surface exactly once, and does not accumulate', async () => {
    // THREE calls, not two: two cannot tell "disposes the previous set" from
    // "disposes whatever it built last".
    //
    // The shape of the evidence is the point — ONE geometry dispose per swap
    // even though TWO children referenced it. That is the `Set` in the removal
    // loop doing its job.
    //
    // RED (double free): replace the two Sets with a per-child
    //   `child.geometry?.dispose()` — the first geometry's count becomes 2.
    // RED (leak): delete the geometry dispose loop — it stays 0.
    // RED (accumulate): delete the removal loop — children grow 2, 4, 6.
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    const node = handle.occluderNode;

    handle.setOccluder(mesh.positions, mesh.indices, earRests);
    const first = node.children[0].geometry;
    const firstMaterial = node.children[0].material;
    const firstCatcher = node.children[1].material;
    assert.equal(first.disposed, 0);

    handle.setOccluder(mesh.positions, mesh.indices, earRests);
    handle.setOccluder(mesh.positions, mesh.indices, earRests);

    assert.equal(first.disposed, 1,
      'the shared geometry was disposed ' + first.disposed + ' times — in real three that '
      + 'is a double free of a GPU buffer, or a leak');
    assert.equal(firstMaterial.disposed, 1, 'the occluder material leaked');
    assert.equal(firstCatcher.disposed, 1, 'the catcher material leaked');
    assert.equal(node.children.length, 2,
      'after three calls the node holds ' + node.children.length + ' children — the old '
      + 'surfaces were never removed');
    assert.ok(!node.children.includes(first), 'a disposed geometry is still in the graph');
  });

  it('pushes the occluder toward the camera, by the shipped bias and by nothing else', async () => {
    // `setOccluder` never touches the node's matrix — `setHeadPose` does, and it
    // is the only place `OCCLUDER_BIAS_MM` is applied. So this drives the pose
    // and reads the matrix the recording stub captured, which the shipped stub
    // threw away.
    //
    // RED: drop the bias (the occluder and the skin coincide and z-fight at
    // every silhouette), or apply it with the wrong sign (the occluder sits
    // BEHIND the skin and hides nothing).
    const { s, handle, node } = await withOccluder();
    handle.setHeadPose({
      R: Float64Array.of(1, 0, 0, 0, 1, 0, 0, 0, 1),
      t: Float64Array.of(0, 0, 500),
    } as any);

    const head = handle.headNode.matrix.elements;
    const occ = node.matrix.elements;
    assert.equal(head.length, 16, 'the head matrix was never written');
    assert.equal(occ.length, 16, 'the occluder matrix was never written');
    assert.notEqual(s.OCCLUDER_BIAS_MM, 0, 'the bias is zero — nothing separates them');

    // Column-major: 12..14 is the translation. The occluder must differ from the
    // head by the bias along the camera axis and by nothing else at all.
    for (let i = 0; i < 12; i++) {
      assert.equal(occ[i], head[i], 'the bias rotated the occluder at element ' + i);
    }
    const moved = Math.hypot(occ[12] - head[12], occ[13] - head[13], occ[14] - head[14]);
    assert.ok(Math.abs(moved - Math.abs(s.OCCLUDER_BIAS_MM)) < 1e-6,
      'the occluder moved ' + moved + ' against a bias of ' + s.OCCLUDER_BIAS_MM);
  });
});

describe('an off-centre principal point shifts the frustum the right way', () => {
  // **Nothing asserted on this, which is why it was wrong.** The shipped code
  // did `projectionMatrix.elements[8] += offset.x`, and three.js puts the
  // optical axis at NDC `-te[8]` — so the shear went the wrong way by twice the
  // offset, which is strictly worse than not shearing at all. It never fired,
  // because `principalPointOffset` returns null on every shipped path; it would
  // have fired the day somebody solved the principal point, and it would have
  // looked like a solver fault.

  const central = { f: 600, cx: 640, cy: 360, k1: 0, width: 1280, height: 720 };

  it('does nothing at all when the principal point IS the image centre', async () => {
    // RED: shear unconditionally. Every shipped intrinsics is central, so a
    // shear applied here would move the frame on every frame of every session.
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    handle.applyIntrinsics(central);
    assert.equal(handle.camera.viewOffset, null,
      'a centred principal point produced a frustum offset');
  });

  it('offsets the frustum by MINUS the principal point, not plus', async () => {
    // The sign is the whole test. `setViewOffset(fullW, fullH, x, y, w, h)`
    // shifts `left` by x·width/fullWidth, which lands the optical axis at NDC
    // -2x/W — so reaching a principal point at +dx needs x = -dx.
    //
    // RED: pass `+dx, +dy`, or go back to `elements[8] += offset.x`. Either
    // way the frame is drawn on the correct axis in the wrong direction.
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    const dx = 25.6, dy = 14.4;                       // 2% of width and height
    handle.applyIntrinsics({ ...central, cx: central.cx + dx, cy: central.cy + dy });

    const off = handle.camera.viewOffset;
    assert.ok(off, 'an off-centre principal point produced no frustum offset');
    assert.equal(off[0], central.width, 'fullWidth is not the image width');
    assert.equal(off[1], central.height, 'fullHeight is not the image height');
    assert.ok(Math.abs(off[2] + dx) < 1e-9,
      `offsetX is ${off[2]}, expected ${-dx} — the shear is inverted, which draws `
      + 'the frame the correct axis and the wrong way');
    assert.ok(Math.abs(off[3] + dy) < 1e-9, `offsetY is ${off[3]}, expected ${-dy}`);
  });

  it('clears a previous off-centre solve when a central one arrives', async () => {
    // `applyIntrinsics` runs more than once per session. Without the clear, a
    // central intrinsics following an off-centre one keeps the old shear —
    // which is the shape of bug that survives because it only appears second.
    // RED: delete `camera.clearViewOffset()` from the else branch.
    const s = instantiateScene();
    const handle = await s.createScene({} as any, { preferWebGPU: false });
    handle.applyIntrinsics({ ...central, cx: central.cx + 30 });
    assert.ok(handle.camera.viewOffset, 'the setup never offset anything');
    handle.applyIntrinsics(central);
    assert.equal(handle.camera.viewOffset, null, 'a stale shear survived a central solve');
  });
});
