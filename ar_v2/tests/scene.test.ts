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

describe('the scene is set up to render a real asset', () => {
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
      matrix = { fromArray() { /* stub */ } };
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
        constructor() { made.push('PerspectiveCamera'); }
        lookAt() { /* stub */ }
        updateProjectionMatrix() { /* stub */ }
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
      BufferGeometry: record('BufferGeometry'),
      BufferAttribute: record('BufferAttribute'),
      Mesh: record('Mesh'),
      MeshBasicMaterial: record('MeshBasicMaterial'),
      ShadowMaterial: record('ShadowMaterial'),
    };
    const RoomEnvironment = record('RoomEnvironment');

    const createScene = new Function(
      'THREE', 'RoomEnvironment', 'console', 'globalThis',
      `${text.slice(start, end).replace(/^export\s+/, '')}\nreturn createScene;`,
    )(THREE, RoomEnvironment, { warn() { /* quiet */ }, info() { /* quiet */ } },
      { devicePixelRatio: 2 });

    return {
      createScene, made, THREE,
      lights: () => made.filter((m) => m === 'DirectionalLight').length,
      pmremDisposals: () => pmremDisposals,
    };
  }

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
