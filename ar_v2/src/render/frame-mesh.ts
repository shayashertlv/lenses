/**
 * Drawing a real pair of glasses: the glTF half of the asset pipeline.
 *
 * `fit/mesh-io.ts` reads the same file headlessly, for triangles and nothing
 * else. This one reads it through three.js, for the things a headless reader
 * deliberately refuses to decode — materials, textures, and the glTF extensions
 * that carry glass. The two must not disagree about where the frame is, and the
 * way they cannot is that **this file computes no placement at all**. It applies
 * `FrameAsset.source.meshToFrame`, the matrix `frameFromMesh` built while it was
 * measuring the pads, and that is the whole of its geometry.
 *
 * If that seems like an over-strong rule, it is the one v1's audit asks for. A
 * renderer that re-derives its own scale and centring agrees with the solver
 * until an asset arrives that they read differently, and the symptom then is a
 * frame drawn a few millimetres from where it was fitted — which looks exactly
 * like a tracking bug and is not one.
 *
 * ## What the catalogue's assets actually declare
 *
 * Measured across the ten GLBs: nine declare `KHR_materials_transmission`, and
 * seven carry lens identity in a material name (`Lens_Prescription_Glass`,
 * `Lens_Gradient_Rx`, `Lens_Gradient`, `Lens_Gold_Mirror`, `LensGlass`,
 * `lens_interior`, `lens_exterior`). A `/lens/i` match over the node name OR the
 * material name finds 15 lens parts across the catalogue with **zero false
 * positives**, and misses two assets entirely — `meshy-glasses` and
 * `crystal-parts` name nothing at all.
 *
 * The tempting alternative, "anything that declares transmission is a lens", is
 * worse and the counter-examples are in the catalogue: `Frame_Acetate_Translucent`
 * on horizon-sage is a translucent FRAME, and `nose_pads` on khronos are the
 * nose pads. Both are genuinely transmissive; neither is a lens.
 *
 * So there are two tiers, and they do different jobs:
 *
 *  - **Every transmissive material** gets the compositing fix — the thing glTF
 *    cannot express, which is how transmitted light behaves when what is behind
 *    it is a photograph of a face rather than a rendered room.
 *  - **Only named lens parts** get the ophthalmic treatment on top.
 *
 * ## The miss is silent, so it is declared rather than detected
 *
 * A matcher that returns nothing looks exactly like a frame with no lenses, and
 * v1 shipped that: "a frame with modelled lenses rendered as a frame with empty
 * rims". `lensPartCount` is exported so the caller can compare against what the
 * catalogue says the asset has, instead of trusting the matcher's own silence.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { FrameAsset } from '../fit/frame-asset.js';

/**
 * The refractive index of an ophthalmic lens, used only when the file declares
 * none.
 *
 * 1.586 is polycarbonate, the standard high-index lens material, and it is what
 * Meshy authored into `Lens_Prescription_Glass` — not a round number anybody
 * guesses, which is why it is worth preferring the file's own value wherever
 * there is one.
 */
export const LENS_IOR = 1.586;

/**
 * How rough a lens surface is.
 *
 * On a transmissive material this is not a finish: three samples the refracted
 * image at mip level `log2(bufferWidth) * roughness`, so it blurs what you see
 * THROUGH the lens as much as what you see in it. v1 measured the difference —
 * at 0.15 a wearer's eyebrow arrived behind the rim as a smudge; at 0.05 the
 * individual hairs come through. Below 0.05 is the same picture with a number
 * that lies about it, because the shader floors it at 0.0525.
 */
export const LENS_ROUGHNESS = 0.05;

/** Node or material names that identify a lens. See the file header. */
const LENS_NAME = /lens/i;

const isLens = (node: any): boolean => {
  if (LENS_NAME.test(node.name ?? '')) return true;
  const materials = Array.isArray(node.material) ? node.material : [node.material];
  return materials.some((m: any) => m && LENS_NAME.test(m.name ?? ''));
};

const isTransmissive = (m: any): boolean =>
  typeof m?.transmission === 'number' && m.transmission > 0;

/**
 * How a transmissive material has to behave when it lands on a photograph.
 *
 * three's transmission path renders the opaque scene into a buffer and refracts
 * that buffer, which is a different mechanism from alpha blending and does not
 * want `transparent: true`. Setting both makes the material sort into the
 * transparent pass, where it no longer writes depth, and a lens that writes no
 * depth cannot be occluded by the nose — which is the one thing this tree's
 * whole occluder exists to do.
 */
function compositeTransmissive(material: any): void {
  material.transparent = false;
  material.depthWrite = true;
  // The frame is composited over a video frame that has already been through
  // the camera's own tone curve, so tone-mapping the glass a second time
  // double-darkens exactly the pixels the wearer is looking through.
  material.toneMapped = false;
  // A thin shell has no back face to shadow from; DoubleSide here keeps a lens
  // from dropping half its shadow.
  material.shadowSide = THREE.DoubleSide;
  material.needsUpdate = true;
}

/** The ophthalmic treatment, for parts a lens name identifies. */
function makeLens(material: any): void {
  compositeTransmissive(material);
  if (!(material.transmission > 0)) material.transmission = 1;
  // The file's own IOR wins: `Lens_Prescription_Glass` ships 1.586 and that is
  // a real material property, not a default anyone would land on by accident.
  if (!(material.ior > 1)) material.ior = LENS_IOR;
  material.roughness = LENS_ROUGHNESS;
  material.metalness = 0;
  // A lens is a dielectric shell, not a solid: thickness drives the refraction
  // offset, and a millimetre is what an ophthalmic lens is at its thinnest.
  if (!(material.thickness > 0)) material.thickness = 1.0;
  material.needsUpdate = true;
}

/**
 * Loads one asset and returns it placed in frame space, ready to hang under
 * `frameNode`.
 *
 * The returned group's matrix IS `asset.source.meshToFrame`, written directly
 * rather than decomposed, and `matrixAutoUpdate` is off so three cannot
 * recompose it through Float32 position/quaternion/scale for no reason.
 */
export async function loadFrameMesh(
  asset: FrameAsset, baseUrl: string,
): Promise<{ object: any; lensPartCount: number; transmissiveCount: number }> {
  const source = asset.source;
  if (!source) {
    throw new Error(
      `frame "${asset.id}" has no mesh source — it is parametric, and `
      + 'render/frame-geometry.ts draws it.',
    );
  }

  const url = new URL(source.url, baseUrl).href;
  const gltf = await new GLTFLoader().loadAsync(url);
  const loaded = gltf.scene;

  let lensPartCount = 0;
  let transmissiveCount = 0;
  loaded.traverse((node: any) => {
    if (!node.isMesh) return;
    node.castShadow = true;
    node.receiveShadow = true;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    const lens = isLens(node);
    if (lens) lensPartCount++;
    for (const material of materials) {
      if (!material) continue;
      if (lens) makeLens(material);
      else if (isTransmissive(material)) { compositeTransmissive(material); transmissiveCount++; }
    }
  });

  // One matrix, from the solve. See the file header for why nothing here
  // measures the asset for itself.
  const group = new THREE.Group();
  group.name = `frame:${asset.id}`;
  group.matrixAutoUpdate = false;
  // `Matrix4.set` takes its arguments in ROW-major order and stores column-major
  // internally, so a row-major source array goes in element by element with no
  // transpose. Getting this backwards is a transform that looks almost right.
  const m = source.meshToFrame;
  group.matrix.set(
    m[0], m[1], m[2], m[3],
    m[4], m[5], m[6], m[7],
    m[8], m[9], m[10], m[11],
    m[12], m[13], m[14], m[15],
  );
  group.add(loaded);
  group.updateMatrixWorld(true);

  return { object: group, lensPartCount, transmissiveCount };
}
