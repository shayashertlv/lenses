/**
 * One frame, one description — checked from both ends.
 *
 * `render/frame-geometry.ts` draws a parametric frame and
 * `testkit/report-occlusion.ts` samples it to measure what the face hides. They
 * used to compute the same geometry independently, with each file header naming
 * the other as a twin to be kept in step by hand. **The bridge had drifted
 * 4.000000 mm**, pure +Y, at every one of its 16 samples: the instrument dropped
 * the rims by `LENS_DROP_MM` and forgot the bridge. Its samples missed the drawn
 * bridge tube — radius 1.6 mm — by 2.4 mm of clear air, and the error flattered,
 * under-reporting that part's occlusion by 9 to 14 percentage points.
 *
 * **There was no test to catch it, and there could not have been one.** No test
 * in this tree references `frame-geometry.ts`, because it imports three.js and
 * the suite runs under Node where three is a vendored browser file rather than a
 * dependency. The twin was maintained by two comments asking a reader to keep it
 * in step.
 *
 * Both sides now read `fit/frame-layout.ts`. That makes "do they agree?"
 * vacuous, so it is not what is asserted here. What is asserted is what the
 * shared buffer does NOT make free:
 *
 *  1. every part the renderer actually draws has samples, by name;
 *  2. the renderer applies no offset of its own on top of the layout — which is
 *     precisely how the 4 mm appeared, and the only way it can come back;
 *  3. the instrument refuses a mesh-backed asset instead of measuring a
 *     parametric stand-in for geometry nobody drew.
 *
 * The renderer is instantiated against a recording stub for the same reason
 * `scene.test.ts` instantiates `createScene`: reading the shipped function is a
 * test of the renderer, and re-implementing its arithmetic here would be a test
 * of a copy — which is exactly the failure mode being retired.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { readFileSync } from 'node:fs';

import { TEST_FRAMES, type FrameAsset } from '../src/fit/frame-asset.js';
import {
  BRIDGE_TUBE_MM, LENS_DROP_MM, RIM_TUBE_MM, TEMPLE_HEIGHT_MM, TEMPLE_THICKNESS_MM,
  frameLayout,
} from '../src/fit/frame-layout.js';
import { framePartNames, frameSampleParts, frameSampleSet } from '../src/testkit/report-occlusion.js';

const standard = TEST_FRAMES[1];

/**
 * `createFrameObject` out of the compiled build, against a stub that records
 * every mesh's name and where it was put.
 *
 * `orientAlong` sets a segment mesh's `position` to the midpoint of its run, and
 * a rim/lens disc is placed by translating its geometry — so both are readable
 * without three.js doing any real work.
 */
function drawFrame(asset: FrameAsset) {
  const text = readFileSync(new URL('../src/render/frame-geometry.js', import.meta.url), 'utf8');
  const start = text.indexOf('export function createFrameObject(');
  assert.ok(start >= 0, 'createFrameObject has been renamed or moved');

  // The whole module is evaluated, not one sliced function: `EllipseCurve3` and
  // `orientAlong` are module-scope and the function needs both.
  const source = text
    .replace(/^import[^;]+;$/gm, '')
    .replace(/^export /gm, '');

  const drawn: { name: string; position: number[]; translate: number[] | null; scale: number[] | null }[] = [];

  class Vec3 {
    constructor(public x = 0, public y = 0, public z = 0) {}
    set(x: number, y: number, z: number) { this.x = x; this.y = y; this.z = z; return this; }
    copy(v: Vec3) { return this.set(v.x, v.y, v.z); }
    clone() { return new Vec3(this.x, this.y, this.z); }
    sub(v: Vec3) { return this.set(this.x - v.x, this.y - v.y, this.z - v.z); }
    cross(v: Vec3) {
      return this.set(
        this.y * v.z - this.z * v.y, this.z * v.x - this.x * v.z, this.x * v.y - this.y * v.x,
      );
    }
    length() { return Math.hypot(this.x, this.y, this.z); }
    divideScalar(s: number) { return this.set(this.x / s, this.y / s, this.z / s); }
    normalize() { const l = this.length() || 1; return this.divideScalar(l); }
    addScaledVector(v: Vec3, s: number) {
      return this.set(this.x + v.x * s, this.y + v.y * s, this.z + v.z * s);
    }
    toArray() { return [this.x, this.y, this.z]; }
  }
  class Geometry {
    translate_: number[] | null = null;
    scale_: number[] | null = null;
    scale(x: number, y: number, z: number) { this.scale_ = [x, y, z]; return this; }
    translate(x: number, y: number, z: number) { this.translate_ = [x, y, z]; return this; }
    rotateX() { return this; }
    dispose() { /* stub */ }
  }
  class Mesh {
    name = '';
    position = new Vec3();
    quaternion = { setFromRotationMatrix() { /* stub */ } };
    constructor(public geometry: any, public material: any) {}
  }
  const THREE: any = {
    Group: class {
      name = ''; children: any[] = [];
      add(c: any) { this.children.push(c); drawn.push({
        name: c.name, position: c.position.toArray(),
        translate: c.geometry?.translate_ ?? null, scale: c.geometry?.scale_ ?? null,
      }); }
      traverse() { /* stub */ }
    },
    Mesh,
    Vector3: Vec3,
    Matrix4: class { makeBasis() { return this; } },
    Curve: class {},
    TubeGeometry: class extends Geometry { constructor(public curve: any) { super(); } },
    CircleGeometry: class extends Geometry {},
    CylinderGeometry: class extends Geometry {},
    BoxGeometry: class extends Geometry {},
    MeshStandardMaterial: class { dispose() { /* stub */ } },
    MeshPhysicalMaterial: class { dispose() { /* stub */ } },
    DoubleSide: 2,
  };

  // The layout module goes in for REAL, not stubbed: this test is about the
  // renderer reading it, so a fake would defeat the point entirely.
  const make = new Function(
    'THREE', 'frameLayout', 'RIM_TUBE_MM', 'BRIDGE_TUBE_MM',
    'TEMPLE_THICKNESS_MM', 'TEMPLE_HEIGHT_MM',
    `${source}\nreturn createFrameObject;`,
  )(THREE, frameLayout, RIM_TUBE_MM, BRIDGE_TUBE_MM, TEMPLE_THICKNESS_MM, TEMPLE_HEIGHT_MM);
  make(asset);
  return drawn;
}

describe('the frame is described once, and both sides read it', () => {
  it('every part the renderer draws has samples under a name the instrument knows', () => {
    // RED: add a part to `createFrameObject` — say a brow bar — and do not add a
    // `framePartNames` entry or samples for it. The renderer would draw
    // something the occlusion report silently never measures, which is the
    // three-of-five state this collapse ended: rims, bridge and temples were
    // sampled while the lens discs and endpieces were not.
    const drawn = drawFrame(standard);
    assert.ok(drawn.length >= 9, `the renderer emitted only ${drawn.length} meshes`);

    const classOf = (name: string) => name.replace(/[RL]$/, '');
    const drawnClasses = new Set(drawn.map((d) => classOf(d.name)));
    for (const cls of drawnClasses) {
      assert.ok((framePartNames as readonly string[]).includes(cls),
        `the renderer draws "${cls}" and the instrument has no part name for it — `
        + `it would be measured by nothing. framePartNames = [${framePartNames.join(', ')}]`);
    }

    // ...and the other direction: a named part with no samples is a row of
    // zeroes pretending to be a measurement.
    const parts = frameSampleParts(standard);
    const seen = new Set<number>(parts);
    for (let p = 0; p < framePartNames.length; p++) {
      assert.ok(seen.has(p),
        `"${framePartNames[p]}" is declared but no sample carries its index — the report `
        + 'would print a row about nothing');
    }
    assert.equal(seen.size, framePartNames.length,
      'a sample carries a part index with no name');
  });

  it('the renderer adds no offset of its own — the 4 mm bridge, made impossible', () => {
    // RED: put `- LENS_DROP_MM` back into `createFrameObject`'s bridge
    // endpoints, or any other private offset. This is the exact bug class:
    // both sides read one layout, and the drift returns the moment one of them
    // adjusts what it read.
    const drawn = drawFrame(standard);
    const layout = frameLayout(standard);
    const byName = new Map(drawn.map((d) => [d.name, d]));

    const midpoint = (s: { from: Float64Array; to: Float64Array }) =>
      [0, 1, 2].map((k) => (s.from[k] + s.to[k]) / 2);

    const segments: [string, { from: Float64Array; to: Float64Array }][] = [
      ['bridge', layout.bridge],
      ['endpieceR', layout.endpieces[0]], ['endpieceL', layout.endpieces[1]],
      ['templeR', layout.temples[0]], ['templeL', layout.temples[1]],
    ];
    for (const [name, seg] of segments) {
      const d = byName.get(name);
      assert.ok(d, `the renderer no longer draws "${name}"`);
      const want = midpoint(seg);
      for (let k = 0; k < 3; k++) {
        assert.ok(Math.abs(d.position[k] - want[k]) < 1e-9,
          `"${name}" is drawn at ${d.position.map((v) => v.toFixed(3))} but the layout puts `
          + `it at ${want.map((v) => v.toFixed(3))} — the renderer is applying its own offset`);
      }
    }

    // The lens discs are placed by translating their geometry.
    for (const [name, disc] of [['lensR', layout.lenses[0]], ['lensL', layout.lenses[1]]] as const) {
      const d = byName.get(name);
      assert.ok(d?.translate, `"${name}" is no longer placed by a geometry translate`);
      for (let k = 0; k < 3; k++) {
        assert.ok(Math.abs(d.translate![k] - disc.centre[k]) < 1e-9,
          `"${name}" drawn at ${d.translate} against the layout's ${Array.from(disc.centre)}`);
      }
    }
  });

  it('the bridge sits at the drop, on both sides — the regression itself', () => {
    // The specific number, pinned. The instrument's bridge used to sit at
    // `lensCentres[].y` while the renderer drew it at `y - LENS_DROP_MM`.
    const layout = frameLayout(standard);
    const wantY = standard.lensCentres[0][1] - LENS_DROP_MM;
    assert.ok(Math.abs(layout.bridge.from[1] - wantY) < 1e-12,
      `the layout's bridge is at y ${layout.bridge.from[1]}, not ${wantY}`);

    const points = frameSampleSet(standard);
    const parts = frameSampleParts(standard);
    const bridgeIndex = framePartNames.indexOf('bridge' as never);
    let n = 0;
    for (let i = 0; i < parts.length; i++) {
      if (parts[i] !== bridgeIndex) continue;
      n++;
      assert.ok(Math.abs(points[i * 3 + 1] - wantY) < 1e-9,
        `a bridge sample sits at y ${points[i * 3 + 1].toFixed(6)}, ${(points[i * 3 + 1] - wantY).toFixed(6)} `
        + 'mm from where the bridge is drawn. That gap was 4.000000 mm and it made every '
        + 'bridge occlusion number a statement about empty air.');
    }
    assert.ok(n >= 16, `only ${n} bridge samples`);
  });

  it('refuses to sample a mesh-backed asset rather than measuring a stand-in', () => {
    // RED: return a layout for a mesh asset instead of throwing. navigator draws
    // 68,638 of its own triangles; `rimHalfAxes` is a guess about where a rim
    // would be if the frame were parametric. Measuring that would be the 4 mm
    // bridge again, an order of magnitude larger.
    const meshBacked: FrameAsset = {
      ...standard,
      id: 'pretend-mesh',
      source: { url: 'assets/glasses/navigator.glb', meshToFrame: new Float64Array(16) },
    };
    assert.equal(frameLayout(meshBacked).describesDrawn, false);
    assert.throws(() => frameSampleSet(meshBacked), /mesh-backed/,
      'the instrument sampled a parametric stand-in for a frame that draws its own geometry');
    // ...and still measures a parametric one.
    assert.equal(frameLayout(standard).describesDrawn, true);
    assert.ok(frameSampleSet(standard).length > 0);
  });
});
