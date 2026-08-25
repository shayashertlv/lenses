/**
 * Parametric eyewear geometry: the first thing this tree has ever drawn.
 *
 * Stage 1 of the occlusion plan — a frame on the face, no occlusion. Everything
 * here is built from a `FrameAsset`'s own spec fields and nothing else: no
 * meshes, no textures, no loaders. The asset pipeline (Q10) replaces this the
 * day real geometry is measured; until then the renderer draws exactly the
 * numbers the contact solve seated.
 *
 * ## Twinship with the stage-0 instrument — keep them in agreement
 *
 * `src/testkit/report-occlusion.ts`'s `buildFrameSamples` samples the same
 * layout this file renders: rim ellipses at the lens plane sized by
 * `rimHalfAxes` below (inner edge at the pad gap, 0.8 box aspect — the first
 * 0.11-of-front-width convention rendered a 30 mm lens and a real wearer saw
 * it immediately), a bridge segment between the two inner rim edges, and straight
 * hinge-to-ear temples out to `z = -templeReachMm` (contact.ts's documented Q6
 * approximation — a real arm curves around the skull; this one does not). The
 * instrument measures where THIS geometry's visible structure sits, so a change
 * to the layout on either side that is not made on both desynchronises the
 * occlusion numbers from the picture. testkit cannot be imported here — the
 * isolation boundary runs the other way — so the layout is re-derived, and both
 * file headers name the twin.
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

// Cosmetic constants — render-only, carrying no fit meaning. The rim/bridge
// tube radius and temple cross-section are typical acetate dimensions; nothing
// in `FrameSpec` measures them (the spec describes contact and layout, not
// wall thickness), so they are honest placeholders in the same sense as the
// catalogue's `dimensionSource: 'assumed'`.
const RIM_TUBE_MM = 1.7;
const BRIDGE_TUBE_MM = 1.6;
const TEMPLE_THICKNESS_MM = 2.6; // across (x-ish)
const TEMPLE_HEIGHT_MM = 4.4;    // vertical
/** Lens disc inset inside the rim ellipse, so the rim tube overlaps its edge. */
const LENS_INSET_MM = 0.6;

/**
 * How far the rim/lens centres sit BELOW the asset's lens-centre line, mm.
 *
 * A worn frame's lens centre is a few millimetres below the pupil (the trade
 * fits the pupil at the upper third of the box). The asset's `lensCentres`
 * carry the FIT meaning — vertex distance, height verdicts — and stay
 * untouched; this is a render offset only, twinned into the occlusion
 * instrument. It also matters for occlusion: the first real wearer's far rim
 * crossed the nose at the ROOT, its shallowest millimetres, because round
 * rims centred on the eye line never reached the dorsum where occlusion is
 * strong. Dropping the box moves the crossing down into it.
 */
const LENS_DROP_MM = 4;

/**
 * The rim half-axes, derived from the asset's own layout rather than a fixed
 * fraction of the front width.
 *
 * The first version used `0.11 * frontWidthMm` — `clearanceSamples`' coarse
 * instrument convention — which renders a 30 mm lens on a 138 mm front. A real
 * lens box is ~50 mm, and the first real wearer saw the gap immediately: tiny
 * rims floating mid-face that the nose could never occlude, and a bare hinge
 * gap between rim and temple. The honest derivation from what the spec
 * actually measures: the rim's inner edge belongs at the bridge, just clear of
 * the pad gap, so the half-width is the lens centre's offset minus half the
 * pad separation minus a margin — capped so the outer edge leaves room for the
 * endpiece before the hinge. Height is 0.8 of width, the trade's usual box
 * aspect. On the 'standard' asset (front 138, centres ±31.7, pads 17) this
 * gives a ≈ 21.7 mm — a 43 mm lens whose inner edge sits ~10 mm from the
 * centreline, where a nose can finally shadow it.
 *
 * TWIN: `report-occlusion.ts`'s `frameSampleSet` must use this same formula —
 * the instrument measures where THIS geometry's structure sits.
 */
function rimHalfAxes(asset: FrameAsset): { a: number; b: number } {
  const cx = Math.abs(asset.lensCentres[0][0]);
  const inner = cx - asset.padSeparationMm / 2 - 1.5; // nose side sets the size
  const outerCap = asset.frontWidthMm / 2 - cx - 1;   // never past the hinge
  const floor = asset.frontWidthMm * 0.11;            // never below the old size
  const a = Math.max(floor, Math.min(inner, outerCap));
  return { a, b: a * 0.8 };
}

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

  const { a, b } = rimHalfAxes(asset);

  // Rims: an elliptical tube per lens, centred on the asset's own lens
  // centres, dropped LENS_DROP_MM below the fit line (render-only; see the
  // constant).
  for (let s = 0; s < 2; s++) {
    const c0 = asset.lensCentres[s];
    const c = [c0[0], c0[1] - LENS_DROP_MM, c0[2]];
    const curve = new EllipseCurve3(c[0], c[1], c[2], a, b);
    const rim = new THREE.Mesh(new THREE.TubeGeometry(curve, 64, RIM_TUBE_MM, 10, true), acetate);
    rim.name = s === 0 ? 'rimR' : 'rimL';
    group.add(rim);

    // Lens: a flat elliptical disc inset inside the rim.
    const lensGeo = new THREE.CircleGeometry(1, 48);
    lensGeo.scale(a - LENS_INSET_MM, b - LENS_INSET_MM, 1);
    lensGeo.translate(c[0], c[1], c[2]);
    const lens = new THREE.Mesh(lensGeo, lensMaterial);
    lens.name = s === 0 ? 'lensR' : 'lensL';
    group.add(lens);
  }

  // Bridge: right lens's inner edge to left lens's inner edge — the same
  // segment the stage-0 instrument samples.
  const r = asset.lensCentres[0], l = asset.lensCentres[1];
  const bridgeFrom = [r[0] + a, r[1] - LENS_DROP_MM, r[2]];
  const bridgeTo = [l[0] - a, l[1] - LENS_DROP_MM, l[2]];
  const bridgeLen = Math.hypot(
    bridgeTo[0] - bridgeFrom[0], bridgeTo[1] - bridgeFrom[1], bridgeTo[2] - bridgeFrom[2],
  );
  const bridge = new THREE.Mesh(
    new THREE.CylinderGeometry(BRIDGE_TUBE_MM, BRIDGE_TUBE_MM, bridgeLen, 12)
      .rotateX(Math.PI / 2), // cylinder axis Y -> Z, so orientAlong's basis applies
    acetate,
  );
  bridge.name = 'bridge';
  orientAlong(bridge, bridgeFrom, bridgeTo);
  group.add(bridge);

  // Endpieces: rim outer edge to hinge. Without these the first real wearer
  // saw a bare gap between rim and temple — the hinge sits at the front's
  // half-width while the rim ends at its own outer edge, and nothing spanned
  // the difference. Render-only cosmetic, same class as the tube radii.
  for (let s = 0; s < 2; s++) {
    const c = asset.lensCentres[s], h = asset.hinges[s];
    const sign = s === 0 ? -1 : 1;
    const from = [c[0] + sign * a, c[1] - LENS_DROP_MM, c[2]];
    const len = Math.hypot(h[0] - from[0], h[1] - from[1], h[2] - from[2]);
    if (len < 0.5) continue;
    const endpiece = new THREE.Mesh(
      new THREE.CylinderGeometry(BRIDGE_TUBE_MM, BRIDGE_TUBE_MM, len, 10).rotateX(Math.PI / 2),
      acetate,
    );
    endpiece.name = s === 0 ? 'endpieceR' : 'endpieceL';
    orientAlong(endpiece, from, h);
    group.add(endpiece);
  }

  // Temples: straight hinge-to-ear boxes (Q6: no curl around the skull), with
  // the slight inward, slightly rising run the asset's own endpoints encode.
  for (let s = 0; s < 2; s++) {
    const h = asset.hinges[s], e = asset.earRests[s];
    const len = Math.hypot(e[0] - h[0], e[1] - h[1], e[2] - h[2]);
    const temple = new THREE.Mesh(
      new THREE.BoxGeometry(TEMPLE_THICKNESS_MM, TEMPLE_HEIGHT_MM, len),
      acetate,
    );
    temple.name = s === 0 ? 'templeR' : 'templeL';
    orientAlong(temple, h, e);
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
  for (const g of geometries) g.dispose();
  for (const m of materials) m.dispose();
}
