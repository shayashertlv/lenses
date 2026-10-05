/** Diagnostic render overrides (WP1_DESIGN.md §5: `render(mask, {override})`), applied by the renderer to every eyewear
 *  mesh at once: the originals, the near-arm overlays and the translucent twins (which are materials of the same
 *  meshes), and restored in `finally`.
 *
 *  - 'part-id': one flat colour per mesh (lens meshes all magenta, reserved), black background.
 *  - 'lens-mask': lens meshes white, every other eyewear mesh black (it still hides the lens behind it), black background.
 *  - 'normals': view-space normals of every eyewear mesh, black background.
 *  - 'lens-hidden' / 'lens-only': the beauty render (one unguarded pass) with the lens meshes hidden / with only the lens
 *    meshes shown.
 *  The flat overrides draw without the guard, hair, shadows or look-through image; the near-arm overlays are hidden and
 *  the face and head depth occluders stay (an occluded part is not drawn). Their temples are cut at the renderer's
 *  current clip planes (a hard cut where the beauty render fades over the last 5 mm). */
import {Color, DoubleSide, Material, MeshBasicMaterial, MeshNormalMaterial, SRGBColorSpace} from 'three';
import type {Mesh} from 'three';

export const RENDER_OVERRIDES = Object.freeze(['part-id', 'lens-mask', 'lens-hidden', 'lens-only', 'normals'] as const);
export type RenderOverride = typeof RENDER_OVERRIDES[number];
export const FLAT_OVERRIDES: ReadonlySet<RenderOverride> = new Set(['part-id', 'lens-mask', 'normals']);
export const LENS_ID_RGB = Object.freeze([255, 0, 255] as const);
export type PartSide = 'left' | 'right' | 'centre';
export interface PaletteEntry {
  readonly rgb: readonly [number, number, number];
  readonly name: string;
  readonly class: 'lens' | 'frame' | 'mixed';
  readonly side: PartSide;
}

export function isRenderOverride(value: unknown): value is RenderOverride {return RENDER_OVERRIDES.includes(value as RenderOverride);}

/** The part-id colour of the `index`-th non-lens mesh: deterministic, never black and never near the reserved magenta
 *  (hues 270-330 degrees are skipped), alternating bright and dark so neighbours in the list differ in value too. */
export function partColour(index: number): [number, number, number] {
  let hueStep = 0, found = -1;
  for (;;) {
    const hue = (hueStep * 0.6180339887) % 1;
    if (!(hue * 360 > 270 && hue * 360 < 330)) found++;
    if (found === index) break;
    hueStep++;
  }
  const hue = (hueStep * 0.6180339887) % 1, value = hueStep % 2 ? .62 : .96, saturation = .85;
  const color = new Color().setHSL(hue, 1, .5);
  // HSL with full saturation, then scaled to the chosen value/saturation in sRGB bytes.
  const rgb = [color.r, color.g, color.b].map(channel => value * (1 - saturation + saturation * channel));
  return rgb.map(channel => Math.max(16, Math.round(channel * 255))) as [number, number, number];
}

export interface ClipUniforms {
  readonly enabled: {value: number};
  readonly negative: {value: number};
  readonly positive: {value: number};
}
export function createClipUniforms(): ClipUniforms {return {enabled: {value: 0}, negative: {value: -1}, positive: {value: -1}};}

/** A hard temple cut on a flat override material: fragments behind the side's cutoff (mesh-local z) are discarded.
 *  `side` null decides the side by the fragment's x, 0/1 are an authored side; `clip` false never cuts (lenses, fronts). */
function withClip<T extends Material>(material: T, uniforms: ClipUniforms, clip: boolean, side: 0 | 1 | null): T {
  if (!clip) return material;
  material.onBeforeCompile = shader => {
    shader.uniforms.overrideClipEnabled = uniforms.enabled;
    shader.uniforms.overrideClipNegative = uniforms.negative;
    shader.uniforms.overrideClipPositive = uniforms.positive;
    shader.vertexShader = 'varying vec2 overrideLocalXZ;\n' + shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\noverrideLocalXZ = position.xz;');
    const cutoff = side === null ? '(overrideLocalXZ.x < 0.0 ? overrideClipNegative : overrideClipPositive)' : side === 0 ? 'overrideClipNegative' : 'overrideClipPositive';
    shader.fragmentShader = 'varying vec2 overrideLocalXZ;\nuniform float overrideClipEnabled, overrideClipNegative, overrideClipPositive;\n'
      + shader.fragmentShader.replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>\nif (overrideClipEnabled > 0.5 && overrideLocalXZ.y < ${cutoff}) discard;`);
  };
  material.customProgramCacheKey = () => `override-clip|${side ?? 'x'}`;
  return material;
}

/** The flat override material for one mesh slot. `rgb` is used by part-id and lens-mask. */
export function flatOverrideMaterial(override: RenderOverride, rgb: readonly [number, number, number], uniforms: ClipUniforms,
  clip: boolean, side: 0 | 1 | null): Material {
  // Packed view-space normals, not tone-mapped (the canvas still encodes them to sRGB like every other pixel).
  if (override === 'normals') return withClip(new MeshNormalMaterial({side: DoubleSide, toneMapped: false}), uniforms, clip, side);
  const material = new MeshBasicMaterial({color: new Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, SRGBColorSpace),
    side: DoubleSide, toneMapped: false});
  return withClip(material, uniforms, clip, side);
}

/** The meshes' current materials, to restore after an override. */
export function saveMaterials(meshes: readonly Mesh[]): Map<Mesh, Mesh['material']> {return new Map(meshes.map(mesh => [mesh, mesh.material]));}
