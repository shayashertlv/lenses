/** Diagnostic render overrides (WP1_DESIGN.md §5: `render(mask, {override})`), applied by the renderer to every eyewear
 *  mesh at once: the originals, the near-arm overlays and the translucent twins (which are materials of the same
 *  meshes), and restored in `finally`.
 *
 *  - 'part-id': one flat colour per mesh (lens meshes all magenta, reserved), black background.
 *  - 'silhouette': all eyewear white on black, an unambiguous raster coverage diagnostic.
 *  - 'lens-mask': lens meshes white, every other eyewear mesh black (it still hides the lens behind it), black background.
 *  - 'normals': view-space normals of every eyewear mesh, black background.
 *  - 'lens-hidden' / 'lens-only': the beauty render (one unguarded pass) with the lens meshes hidden / with only the lens
 *    meshes shown.
 *  The flat overrides draw without the guard, hair, shadows or look-through image; the near-arm overlays are hidden and
 *  the face and head depth occluders stay (an occluded part is not drawn). Their temples are cut at the renderer's
 *  current clip planes (a hard cut where the beauty render fades over the last 5 mm). */
import {Color, DoubleSide, Material, MeshBasicMaterial, MeshNormalMaterial, SRGBColorSpace} from 'three';

export const RENDER_OVERRIDES = Object.freeze(['part-id', 'silhouette', 'lens-mask', 'lens-hidden', 'lens-only', 'normals'] as const);
export type RenderOverride = typeof RENDER_OVERRIDES[number];
export const FLAT_OVERRIDES: ReadonlySet<RenderOverride> = new Set(['part-id', 'silhouette', 'lens-mask', 'normals']);
export const LENS_ID_RGB = Object.freeze([255, 0, 255] as const);
export type PartSide = 'left' | 'right' | 'centre';
export interface PaletteEntry {
  readonly rgb: readonly [number, number, number];
  readonly name: string;
  readonly class: 'lens' | 'frame' | 'mixed';
  readonly side: PartSide;
}

export function isRenderOverride(value: unknown): value is RenderOverride {return RENDER_OVERRIDES.includes(value as RenderOverride);}

type Rgb = [number, number, number];
const chebyshev = (a: readonly number[], b: readonly number[]): number => Math.max(Math.abs(a[0]! - b[0]!), Math.abs(a[1]! - b[1]!), Math.abs(a[2]! - b[2]!));
/** Colours a part may never take: the black background and the lens magenta. */
const RESERVED_RGB: readonly (readonly number[])[] = [[0, 0, 0], [...LENS_ID_RGB]];
/** Palette tiers: sRGB grids of `step` levels per channel, each colour at least `reservedGap` (Chebyshev) from black and
 *  from the lens magenta. Tier A (step 51) gives 200 colours at least 51 levels apart (102 from black and magenta);
 *  tier B (step 17) adds the rest of the 16-level grid, 17 apart, only for models with more than 200 parts. */
export const PART_COLOUR_TIERS = Object.freeze([{step: 51, reservedGap: 102}, {step: 17, reservedGap: 34}] as const);
const tiers: Rgb[][] = [];

/** Farthest-point (maximin) order: each next colour is the one farthest from black, magenta and every colour before it
 *  (ties: the first in grid order), so the first k colours of the sequence are as far apart as the grid allows. */
function maximinOrder(candidates: readonly Rgb[], fixed: readonly (readonly number[])[]): Rgb[] {
  const distance = candidates.map(colour => Math.min(...fixed.map(other => chebyshev(colour, other))));
  const used = new Uint8Array(candidates.length), out: Rgb[] = [];
  for (let n = 0; n < candidates.length; n++) {
    let best = -1;
    for (let i = 0; i < candidates.length; i++) if (!used[i] && (best < 0 || distance[i]! > distance[best]!)) best = i;
    used[best] = 1; out.push(candidates[best]!);
    for (let i = 0; i < candidates.length; i++) if (!used[i]) distance[i] = Math.min(distance[i]!, chebyshev(candidates[i]!, candidates[best]!));
  }
  return out;
}

function tier(index: number): Rgb[] {
  while (tiers.length <= index) {
    const {step, reservedGap} = PART_COLOUR_TIERS[tiers.length]!;
    const before = tiers.flat(), seen = new Set(before.map(colour => colour.join(',')));
    const levels: number[] = [];
    for (let level = 0; level <= 255; level += step) levels.push(level);
    const candidates: Rgb[] = [];
    for (const r of levels) for (const g of levels) for (const b of levels) {
      const colour: Rgb = [r, g, b];
      if (seen.has(colour.join(',')) || RESERVED_RGB.some(reserved => chebyshev(colour, reserved) < reservedGap)) continue;
      candidates.push(colour);
    }
    tiers.push(maximinOrder(candidates, [...RESERVED_RGB, ...before]));
  }
  return tiers[index]!;
}

/** The part-id colour of the `index`-th non-lens mesh: deterministic, from a fixed farthest-point sequence (tier A then
 *  tier B), so up to 200 parts are at least 51 sRGB levels apart from each other and at least 102 from the black
 *  background and the lens magenta (decodable with qa/capture/id-palette.ts's full tolerance after antialiasing). A
 *  model with more parts than both tiers hold (over 3,900) repeats colours, which the decoder reports as shared. */
export function partColour(index: number): [number, number, number] {
  if (!Number.isInteger(index) || index < 0) throw new RangeError(`part colour index must be a non-negative integer, got ${index}`);
  let rest = index;
  for (let t = 0; t < PART_COLOUR_TIERS.length; t++) {
    const colours = tier(t);
    if (rest < colours.length) return [...colours[rest]!] as Rgb;
    rest -= colours.length;
  }
  const all = PART_COLOUR_TIERS.map((_, t) => tier(t)).flat();
  return [...all[index % all.length]!] as Rgb;
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

/** The flat override material for one mesh slot. `rgb` is used by all flat colour overrides. */
export function flatOverrideMaterial(override: RenderOverride, rgb: readonly [number, number, number], uniforms: ClipUniforms,
  clip: boolean, side: 0 | 1 | null): Material {
  // Packed view-space normals, not tone-mapped (the canvas still encodes them to sRGB like every other pixel).
  if (override === 'normals') return withClip(new MeshNormalMaterial({side: DoubleSide, toneMapped: false}), uniforms, clip, side);
  const material = new MeshBasicMaterial({color: new Color().setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, SRGBColorSpace),
    side: DoubleSide, toneMapped: false});
  return withClip(material, uniforms, clip, side);
}
