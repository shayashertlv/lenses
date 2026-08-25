/**
 * Parametric eyewear geometry: the first thing this tree has ever drawn.
 *
 * Stage 1 of the occlusion plan — a frame on the face, no occlusion. Everything
 * here is built from a `FrameAsset`'s own spec fields and nothing else: no
 * meshes, no textures, no loaders. The asset pipeline (Q10) replaces this the
 * day real geometry is measured; until then the renderer draws exactly the
 * numbers the contact solve seated.
 *
 * ## There is no twin any more, and there was a reason to end it
 *
 * This file and `testkit/report-occlusion.ts` used to describe the same object
 * independently, each header naming the other as a twin to be kept in step by
 * hand. **The bridge had already drifted 4.000000 mm**, pure +Y, at every
 * sample — the instrument dropped the rims by `LENS_DROP_MM` and forgot the
 * bridge — so its samples missed the drawn bridge tube by 2.4 mm of clear air
 * and under-reported that part's occlusion by 9 to 14 percentage points.
 *
 * Both sides now read `fit/frame-layout.ts`, which owns the arithmetic and the
 * cosmetic constants. This file turns that description into three.js objects and
 * decides nothing about where anything is.
 *
 * ## Conventions
 *
 * Frame-local, millimetres (`MM_TO_SCENE` is 1), same handedness as face space
 * and therefore as GL: +X wearer's left, +Y up, +Z out of the face, origin at
 * the pad-centroid midpoint. Vertices go to three.js UNCHANGED — no CV->GL
 * flip, per `render/convert.ts`. The group hangs under `frameNode`, whose
 * parent already carries the one flip this scene owns; a second one here is
 * the 127 mm defect in that file's header.
 *
 * ## Dispose discipline
 *
 * v1 leaked `glassMaterials` on frame swap and the review remembers it.
 * `disposeFrameObject` disposes every geometry and material exactly once;
 * `attachFrame` (scene.ts) must call it on the outgoing group before adding
 * the next. Materials are shared across meshes within one group, which is why
 * disposal collects into Sets rather than disposing per-mesh.
 */

import * as THREE from 'three';
import type { FrameAsset } from '../fit/frame-asset.js';
import {
  BRIDGE_TUBE_MM, RIM_TUBE_MM, TEMPLE_HEIGHT_MM, TEMPLE_THICKNESS_MM, frameLayout,
} from '../fit/frame-layout.js';

// The cosmetic constants and the rim arithmetic moved to `fit/frame-layout.ts`
// so the occlusion instrument reads the same numbers. `LENS_DROP_MM` is imported
// rather than re-declared for exactly the reason the bridge drifted.

/** A closed ellipse in a Z-normal plane, as a 3D curve for TubeGeometry. */
class EllipseCurve3 extends (THREE as any).Curve {
  constructor(
    private readonly cx: number, private readonly cy: number,
    private readonly cz: number, private readonly a: number,
    private readonly b: number,
  ) { super(); }

  getPoint(t: number, target?: any): any {
    const out = target ?? new THREE.Vector3();
    const th = t * Math.PI * 2;
    return out.set(
      this.cx + this.a * Math.cos(th),
      this.cy + this.b * Math.sin(th),
      this.cz,
    );
  }
}

/**
 * Positions and orients a mesh whose geometry is long in +Z so it runs from
 * `from` to `to` with its Y axis kept as upright as the run allows. A plain
 * `setFromUnitVectors` would pick an arbitrary roll for the near-antiparallel
 * temple direction; building the basis keeps flat temples flat.
 */
function orientAlong(mesh: any, from: ArrayLike<number>, to: ArrayLike<number>): void {
  const f = new THREE.Vector3(from[0], from[1], from[2]);
  const d = new THREE.Vector3(to[0], to[1], to[2]).sub(f);
  const len = d.length();
  const z = d.clone().divideScalar(len);
  const x = new THREE.Vector3(0, 1, 0).cross(z).normalize();
  const y = z.clone().cross(x);
  const m = new THREE.Matrix4().makeBasis(x, y, z);
  mesh.quaternion.setFromRotationMatrix(m);
  mesh.position.copy(f).addScaledVector(z, len / 2);
}

/**
 * Builds the renderable frame: two rim tubes, a bridge, two temples, two
 * lightly transparent lens discs. Frame-local millimetres; add under
 * `frameNode` and let `applySeat`'s matrix place it.
 */
export function createFrameObject(asset: FrameAsset): any {
  const group = new THREE.Group();
  group.name = `frame:${asset.id}`;

  const acetate = new THREE.MeshStandardMaterial({
    color: 0x241f1c, // dark acetate
    roughness: 0.32,
    metalness: 0.06,
  });
  const lensMaterial = new THREE.MeshPhysicalMaterial({
    color: 0xf4f6f8,
    roughness: 0.08,
    metalness: 0,
    transparent: true,
    opacity: 0.13,
    depthWrite: false, // stage 1: nothing behind a lens should be depth-culled by it
    side: THREE.DoubleSide,
  });

  // Every position below comes from `frameLayout`. Nothing here recomputes a
  // coordinate — that is what let the bridge drift 4 mm from the instrument.
  const layout = frameLayout(asset);

  for (let s = 0; s < 2; s++) {
    const rim = layout.rims[s];
    const curve = new EllipseCurve3(rim.centre[0], rim.centre[1], rim.centre[2], rim.a, rim.b);
    const tube = new THREE.Mesh(
      new THREE.TubeGeometry(curve, 64, RIM_TUBE_MM, 10, true), acetate,
    );
    tube.name = s === 0 ? 'rimR' : 'rimL';
    group.add(tube);

    // Lens: a flat elliptical disc, already inset by the layout.
    const disc = layout.lenses[s];
    const lensGeo = new THREE.CircleGeometry(1, 48);
    lensGeo.scale(disc.a, disc.b, 1);
    lensGeo.translate(disc.centre[0], disc.centre[1], disc.centre[2]);
    const lens = new THREE.Mesh(lensGeo, lensMaterial);
    lens.name = s === 0 ? 'lensR' : 'lensL';
    group.add(lens);
  }

  const tubeBetween = (from: ArrayLike<number>, to: ArrayLike<number>, name: string) => {
    const len = Math.hypot(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
    if (len < 0.5) return;
    const mesh = new THREE.Mesh(
      new THREE.CylinderGeometry(BRIDGE_TUBE_MM, BRIDGE_TUBE_MM, len, 12)
        .rotateX(Math.PI / 2), // cylinder axis Y -> Z, so orientAlong's basis applies
      acetate,
    );
    mesh.name = name;
    orientAlong(mesh, from, to);
    group.add(mesh);
  };

  tubeBetween(layout.bridge.from, layout.bridge.to, 'bridge');

  // Endpieces: rim outer edge to hinge. Without these the first real wearer saw
  // a bare gap between rim and temple — the hinge sits at the front's half-width
  // while the rim ends at its own outer edge, and nothing spanned the difference.
  for (let s = 0; s < 2; s++) {
    tubeBetween(layout.endpieces[s].from, layout.endpieces[s].to,
      s === 0 ? 'endpieceR' : 'endpieceL');
  }

  // Temples: straight hinge-to-ear boxes (Q6: no curl around the skull), with
  // the slight inward, slightly rising run the asset's own endpoints encode.
  for (let s = 0; s < 2; s++) {
    const { from, to } = layout.temples[s];
    const len = Math.hypot(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
    const temple = new THREE.Mesh(
      new THREE.BoxGeometry(TEMPLE_THICKNESS_MM, TEMPLE_HEIGHT_MM, len),
      acetate,
    );
    temple.name = s === 0 ? 'templeR' : 'templeL';
    orientAlong(temple, from, to);
    group.add(temple);
  }

  return group;
}

/**
 * Disposes every geometry and material under the group, each exactly once.
 *
 * Call after removing the group from its parent. Shared materials appear on
 * several meshes, hence the Sets: disposing per-mesh would double-dispose
 * (harmless but noisy), and forgetting one is v1's leak.
 */
export function disposeFrameObject(group: any): void {
  const geometries = new Set<any>();
  const materials = new Set<any>();
  group.traverse((node: any) => {
    if (node.geometry) geometries.add(node.geometry);
    if (Array.isArray(node.material)) for (const m of node.material) materials.add(m);
    else if (node.material) materials.add(node.material);
  });
  const textures = new Set<any>();
  for (const m of materials) {
    for (const slot of TEXTURE_SLOTS) {
      const t = m[slot];
      // `scene.environment` is assigned to `material.envMap` by three itself and
      // is SHARED by every material in the scene, including the next frame's.
      // Disposing it here kills the environment for everything that follows —
      // exactly the class of leak-fix that becomes a worse bug. It is released
      // in `createScene`'s `dispose`, which is the only place that owns it.
      if (t?.isTexture) textures.add(t);
    }
  }
  for (const g of geometries) g.dispose();
  for (const m of materials) m.dispose();
  for (const t of textures) t.dispose();
}

/**
 * Texture slots a loaded glTF material can hold.
 *
 * The parametric frame has none — it is untextured tubes — so this did nothing
 * until real assets arrived. It matters now: the catalogue carries **36 MB of
 * embedded texture**, and a wearer flicking through frames uploads all of it to
 * the GPU and, without this, never gets any of it back.
 *
 * `envMap` is deliberately absent. See the note in `disposeFrameObject`.
 */
const TEXTURE_SLOTS = [
  'map', 'normalMap', 'roughnessMap', 'metalnessMap', 'aoMap', 'emissiveMap',
  'bumpMap', 'displacementMap', 'alphaMap', 'lightMap',
  'specularMap', 'specularColorMap', 'specularIntensityMap',
  'transmissionMap', 'thicknessMap',
  'clearcoatMap', 'clearcoatRoughnessMap', 'clearcoatNormalMap',
  'sheenColorMap', 'sheenRoughnessMap', 'iridescenceMap', 'iridescenceThicknessMap',
  'anisotropyMap',
] as const;
