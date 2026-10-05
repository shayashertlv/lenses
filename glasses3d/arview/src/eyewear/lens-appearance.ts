/** Canonical LensAppearance v1 response, shared with the offline Python model.
 *
 * This is response math, NOT renderer integration or recovered optical data.
 * All RGB values are scene-linear sRGB/D65. Density uses the natural logarithm
 * of normal-incidence intrinsic transmission. v is TEXCOORD_0.y attached to the
 * lens (bottom 0, top 1), never screen height. Angles are front-side incidence
 * from air, in DEGREES. Roughness remains metadata: this module does not sample
 * or blur an environment, refract an image, or implement multiple interfaces.
 */

export const LENS_APPEARANCE_EXTENSION = 'LENSES_lens_appearance';
export const MAX_LENS_DENSITY_KNOTS = 16;
export const MAX_LENS_ANGULAR_KNOTS = 16;

/** Fixed effective-group incidence calculation. On the tested D3D11 backend,
 * builtin acos dominates otherwise accurate mirror/gradient color transport.
 * acos(c)=2*asin(sqrt((1-c)/2)); the 24-term series uses y<=1/2. Coefficients
 * and order are fixed by the independent QA probe, not tuned per material.
 * Exact-series truncation is <3.944e-10 radians; float shader arithmetic has
 * separate, device-qualified error measurements and no universal bound here.
 */
const incidenceCoefficients: string[] = [];
let incidenceCentralBinomial = 1n;
for (let n = 0; n < 24; n++) {
  if (n) incidenceCentralBinomial = incidenceCentralBinomial * BigInt(4 * n - 2) / BigInt(n);
  incidenceCoefficients.push((Number(incidenceCentralBinomial) / Number(4n ** BigInt(n) * BigInt(2 * n + 1))).toPrecision(17));
}
export const LENS_INCIDENCE_GLSL = `
highp float lensIncidenceAngleDegrees(highp float cosine) {
  highp float y = (1.0 - clamp(cosine, 0.0, 1.0)) * 0.5;
  highp float p = ${incidenceCoefficients.at(-1)!};
  ${incidenceCoefficients.slice(0, -1).reverse().map(value => `p = p * y + ${value};`).join('\n  ')}
  return degrees(2.0 * sqrt(y) * p);
}`;

export type LensRGB = readonly [number, number, number];
export interface LensDensityKeyframe {readonly v: number; readonly optical_density_rgb: LensRGB}
export interface LensReflectanceKeyframe {readonly angle_degrees: number; readonly reflectance_rgb: LensRGB}
export interface LensAppearanceDescriptor {
  readonly schema_version: 1;
  readonly color_space: 'scene_linear_srgb_D65';
  readonly density_interpolation: 'piecewise_smoothstep_optical_density';
  readonly vertical_coordinate: 'lens_local_bottom_0_top_1';
  readonly normal_reflectance_rgb: LensRGB;
  /** Optional rear R/(1-T); T remains the canonical front transmission. */
  readonly rear_reflection_fraction_rgb?: LensRGB;
  readonly refractive_index: number;
  readonly roughness: number;
  readonly optical_density_keyframes: readonly LensDensityKeyframe[];
  readonly angular_reflectance_keyframes: readonly LensReflectanceKeyframe[] | null;
}
export interface LensAppearanceExtension {
  readonly schema_version: 1;
  readonly texcoord: 0;
  readonly appearance: LensAppearanceDescriptor;
}
export interface LensResponseSample {
  readonly reflectance_rgb: LensRGB;
  readonly transmission_rgb: LensRGB;
  readonly absorption_rgb: LensRGB;
  readonly optical_density_rgb: LensRGB;
}

function object(value: unknown, name: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error(`${name} must be an object`);
  return value as Record<string, unknown>;
}

function fields(value: unknown, expected: readonly string[], name: string): Record<string, unknown> {
  const result = object(value, name);
  const keys = Object.keys(result);
  if (keys.length !== expected.length || keys.some(key => !expected.includes(key)))
    throw new Error(`${name} requires exactly its declared fields`);
  return result;
}

function scalar(value: unknown, name: string, low: number, high = Infinity): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < low || value > high)
    throw new Error(`${name} must be finite in [${low}, ${high}]`);
  return value;
}

function rgb(value: unknown, name: string, high = Infinity): LensRGB {
  if (!Array.isArray(value) || value.length !== 3) throw new Error(`${name} must contain three RGB values`);
  return Object.freeze([scalar(value[0], name, 0, high), scalar(value[1], name, 0, high), scalar(value[2], name, 0, high)]);
}

function domain(positions: readonly number[], end: number, allowSingle: boolean): void {
  if (positions.length === 0 || positions[0] !== 0 || positions.some((v, i) => i > 0 && v <= positions[i - 1]!))
    throw new Error('Keyframes must start at zero and be strictly increasing');
  if (!(allowSingle && positions.length === 1) && positions[positions.length - 1] !== end)
    throw new Error(`Keyframes must cover the full domain through ${end}`);
}

/** Parse the exact Python to_dict schema, returning an immutable detached copy.
 * No defaults, unknown properties, implicit color conversion or coercion.
 * CPU descriptors have no knot-count restriction; GPU packing rejects its own
 * explicit capacity/precision limits instead of altering an accepted descriptor.
 */
export function validateLensAppearance(value: unknown): LensAppearanceDescriptor {
  const optional = Object.hasOwn(object(value, 'LensAppearance v1'), 'rear_reflection_fraction_rgb')
    ? ['rear_reflection_fraction_rgb'] : [];
  const data = fields(value, ['schema_version', 'color_space', 'density_interpolation', 'vertical_coordinate',
    'normal_reflectance_rgb', 'refractive_index', 'roughness', 'optical_density_keyframes', 'angular_reflectance_keyframes', ...optional],
  'LensAppearance v1');
  if (data.schema_version !== 1) throw new Error('Unsupported LensAppearance schema_version');
  if (data.color_space !== 'scene_linear_srgb_D65') throw new Error('Unsupported color_space');
  if (data.density_interpolation !== 'piecewise_smoothstep_optical_density') throw new Error('Unsupported density_interpolation');
  if (data.vertical_coordinate !== 'lens_local_bottom_0_top_1') throw new Error('Unsupported vertical_coordinate');
  const normal = rgb(data.normal_reflectance_rgb, 'normal reflectance', 1);
  if (!Array.isArray(data.optical_density_keyframes)) throw new Error('Invalid optical density keyframes');
  const density = data.optical_density_keyframes.map(raw => {
    const key = fields(raw, ['v', 'optical_density_rgb'], 'Density keyframe');
    return Object.freeze({v: scalar(key.v, 'v', 0, 1), optical_density_rgb: rgb(key.optical_density_rgb, 'density')});
  });
  domain(density.map(key => key.v), 1, true);
  let angular: readonly LensReflectanceKeyframe[] | null = null;
  if (data.angular_reflectance_keyframes !== null) {
    if (!Array.isArray(data.angular_reflectance_keyframes)) throw new Error('Invalid angular reflectance keyframes');
    angular = Object.freeze(data.angular_reflectance_keyframes.map(raw => {
      const key = fields(raw, ['angle_degrees', 'reflectance_rgb'], 'Reflectance keyframe');
      return Object.freeze({angle_degrees: scalar(key.angle_degrees, 'angle', 0, 90), reflectance_rgb: rgb(key.reflectance_rgb, 'reflectance', 1)});
    }));
    domain(angular.map(key => key.angle_degrees), 90, false);
    if (angular[0]!.reflectance_rgb.some((value, channel) => value !== normal[channel]))
      throw new Error('Angular table at zero must equal normal_reflectance_rgb');
  }
  return Object.freeze({schema_version: 1, color_space: data.color_space,
    density_interpolation: data.density_interpolation, vertical_coordinate: data.vertical_coordinate,
    normal_reflectance_rgb: normal, refractive_index: scalar(data.refractive_index, 'index', 1),
    roughness: scalar(data.roughness, 'roughness', 0, 1), optical_density_keyframes: Object.freeze(density),
    angular_reflectance_keyframes: angular,
    ...(optional.length ? {rear_reflection_fraction_rgb: rgb(data.rear_reflection_fraction_rgb, 'rear reflection fraction', 1)} : {})});
}

/** Read the preserved custom glTF material extension. Missing means legacy or
 * unknown, not a guessed lens. A present malformed/unsupported extension throws.
 * Semantic node identity (node.extras.partRole='lens') is retained separately by
 * the asset; this reader never infers identity from numeric transmission/alpha.
 */
export function readLensAppearanceExtension(material: unknown): LensAppearanceExtension | null {
  const data = object(material, 'material');
  if (data.userData === undefined) return null;
  const userData = object(data.userData, 'material.userData');
  if (userData.gltfExtensions === undefined) return null;
  const extensions = object(userData.gltfExtensions, 'material.userData.gltfExtensions');
  if (!Object.hasOwn(extensions, LENS_APPEARANCE_EXTENSION)) return null;
  const extension = fields(extensions[LENS_APPEARANCE_EXTENSION], ['schema_version', 'texcoord', 'appearance'], LENS_APPEARANCE_EXTENSION);
  if (extension.schema_version !== 1) throw new Error('Unsupported lens extension schema_version');
  if (extension.texcoord !== 0) throw new Error('LensAppearance v1 response supports only TEXCOORD_0');
  return Object.freeze({schema_version: 1, texcoord: 0, appearance: validateLensAppearance(extension.appearance)});
}

export function readMaterialLensAppearance(material: unknown): LensAppearanceDescriptor | null {
  return readLensAppearanceExtension(material)?.appearance ?? null;
}

/** A perfectly reflecting, zero-transmission described optical surface is a lens. */
export function isLensAppearanceMaterial(material: unknown): boolean {
  return readLensAppearanceExtension(material) !== null;
}

function interpolate(x: number, positions: readonly number[], colors: readonly LensRGB[]): LensRGB {
  if (positions.length === 1) return colors[0]!;
  let index = 0;
  while (index + 1 < positions.length - 1 && x >= positions[index + 1]!) index++;
  const t = (x - positions[index]!) / (positions[index + 1]! - positions[index]!);
  const weight = t * t * (3 - 2 * t);
  const first = colors[index]!, second = colors[index + 1]!;
  return [first[0] * (1 - weight) + second[0] * weight, first[1] * (1 - weight) + second[1] * weight,
    first[2] * (1 - weight) + second[2] * weight];
}

/** Scalar CPU reference port. Inputs use intrinsic UV.y and degrees, respectively.
 * Invalid coordinates throw; nothing is clamped, inferred, or tone mapped.
 */
export function evaluateLensAppearance(value: LensAppearanceDescriptor, v: number, angleDegrees = 0,
  side: 'front' | 'rear' = 'front'): LensResponseSample {
  const appearance = validateLensAppearance(value);
  if (side !== 'front' && side !== 'rear') throw new Error('Lens side must be front or rear');
  scalar(v, 'v', 0, 1);
  scalar(angleDegrees, 'angle', 0, 90);
  const density = interpolate(v, appearance.optical_density_keyframes.map(key => key.v),
    appearance.optical_density_keyframes.map(key => key.optical_density_rgb));
  const angle = angleDegrees * Math.PI / 180;
  const angular = appearance.angular_reflectance_keyframes;
  const reflection: LensRGB = angular !== null
    ? interpolate(angleDegrees, angular.map(key => key.angle_degrees), angular.map(key => key.reflectance_rgb))
    : appearance.normal_reflectance_rgb.map(normal => normal + (1 - normal) * (1 - Math.cos(angle)) ** 5) as unknown as LensRGB;
  const cosInside = Math.sqrt(1 - (Math.sin(angle) / appearance.refractive_index) ** 2);
  const path = density.map(value => value === 0 ? 0 : cosInside > 0 ? value / cosInside : Infinity);
  const transmission = path.map((value, channel) => (1 - reflection[channel]!) * Math.exp(-value)) as unknown as LensRGB;
  const absorption = path.map((value, channel) => (1 - reflection[channel]!) * -Math.expm1(-value)) as unknown as LensRGB;
  if (side === 'rear' && appearance.rear_reflection_fraction_rgb) {
    const fraction = appearance.rear_reflection_fraction_rgb;
    return {reflectance_rgb: transmission.map((t, c) => (1 - t) * fraction[c]!) as unknown as LensRGB,
      transmission_rgb: transmission,
      absorption_rgb: transmission.map((t, c) => (1 - t) * (1 - fraction[c]!)) as unknown as LensRGB,
      optical_density_rgb: density};
  }
  return {reflectance_rgb: reflection, transmission_rgb: transmission, absorption_rgb: absorption, optical_density_rgb: density};
}

export interface LensAppearanceUniforms {
  [name: string]: {value: number | Float32Array};
  uLensDensityCount: {value: number};
  uLensDensityPositions: {value: Float32Array};
  uLensDensities: {value: Float32Array};
  uLensAngularCount: {value: number};
  uLensAnglePositions: {value: Float32Array};
  uLensReflectances: {value: Float32Array};
  uLensNormalReflectance: {value: Float32Array};
  uLensRefractiveIndex: {value: number};
  uLensHasRearResponse: {value: number};
  uLensRearReflectionFraction: {value: Float32Array};
}

function gpuNumber(value: number): number {
  const result = Math.fround(value);
  if (!Number.isFinite(result) || (value !== 0 && result === 0))
    throw new Error('Lens value cannot be represented as a nonzero finite GPU float');
  return result;
}

function gpuPositions(values: readonly number[], capacity: number): Float32Array {
  const result = new Float32Array(capacity);
  values.forEach((value, i) => {
    result[i] = gpuNumber(value);
    if (i > 0 && result[i]! <= result[i - 1]!) throw new Error('Lens keyframe positions collapse at GPU float precision');
  });
  return result;
}

function gpuColors(values: readonly LensRGB[], capacity: number): Float32Array {
  const result = new Float32Array(capacity * 3);
  values.forEach((value, i) => value.forEach((channel, c) => { result[i * 3 + c] = gpuNumber(channel); }));
  return result;
}

/** Three ShaderMaterial-ready uniforms. Flat Float32Array values upload as
 * float/vec3 arrays; no Three import or Vector3 conversion is necessary.
 * More knots, collapsed positions, overflow or underflow are rejected explicitly.
 * Roughness is not uploaded: reflection filtering belongs to the consuming renderer.
 */
export function createLensAppearanceUniforms(value: LensAppearanceDescriptor): LensAppearanceUniforms {
  const appearance = validateLensAppearance(value);
  const density = appearance.optical_density_keyframes, angular = appearance.angular_reflectance_keyframes ?? [];
  if (density.length > MAX_LENS_DENSITY_KNOTS || angular.length > MAX_LENS_ANGULAR_KNOTS)
    throw new Error(`GPU LensAppearance supports at most ${MAX_LENS_DENSITY_KNOTS} density and ${MAX_LENS_ANGULAR_KNOTS} angular knots`);
  const index = gpuNumber(appearance.refractive_index);
  if (appearance.refractive_index > 1 && index === 1)
    throw new Error('Refractive index collapses to the grazing singularity at GPU float precision');
  return {
    uLensDensityCount: {value: density.length},
    uLensDensityPositions: {value: gpuPositions(density.map(key => key.v), MAX_LENS_DENSITY_KNOTS)},
    uLensDensities: {value: gpuColors(density.map(key => key.optical_density_rgb), MAX_LENS_DENSITY_KNOTS)},
    uLensAngularCount: {value: angular.length},
    uLensAnglePositions: {value: gpuPositions(angular.map(key => key.angle_degrees), MAX_LENS_ANGULAR_KNOTS)},
    uLensReflectances: {value: gpuColors(angular.map(key => key.reflectance_rgb), MAX_LENS_ANGULAR_KNOTS)},
    uLensNormalReflectance: {value: gpuColors([appearance.normal_reflectance_rgb], 1)},
    uLensRefractiveIndex: {value: index},
    uLensHasRearResponse: {value: appearance.rear_reflection_fraction_rgb ? 1 : 0},
    uLensRearReflectionFraction: {value: gpuColors([appearance.rear_reflection_fraction_rgb ?? [0, 0, 0]], 1)},
  };
}

/** Shared GLSL response only, usable by lens composition AND face-tint passes.
 * Requires highp support and uniforms from createLensAppearanceUniforms.
 * Inputs must be intrinsic TEXCOORD_0.y in [0,1] and front incidence DEGREES in
 * [0,90]. Out-of-domain inputs return -1 channels to expose invalid wiring; they
 * are never clamped into a plausible material. No alpha/color-space conversion.
 */
export const LENS_RESPONSE_GLSL = /* glsl */`
precision highp float;
precision highp int;
uniform int uLensDensityCount;
uniform float uLensDensityPositions[${MAX_LENS_DENSITY_KNOTS}];
uniform vec3 uLensDensities[${MAX_LENS_DENSITY_KNOTS}];
uniform int uLensAngularCount;
uniform float uLensAnglePositions[${MAX_LENS_ANGULAR_KNOTS}];
uniform vec3 uLensReflectances[${MAX_LENS_ANGULAR_KNOTS}];
uniform vec3 uLensNormalReflectance;
uniform float uLensRefractiveIndex;
uniform int uLensHasRearResponse;
uniform vec3 uLensRearReflectionFraction;

struct LensResponse {
  vec3 reflectance;
  vec3 transmission;
  vec3 absorption;
  vec3 opticalDensity;
};

vec3 lensDensityAt(float intrinsicV) {
  if (uLensDensityCount == 1) return uLensDensities[0];
  for (int i = 0; i < ${MAX_LENS_DENSITY_KNOTS - 1}; i++) {
    if (i + 1 < uLensDensityCount && intrinsicV <= uLensDensityPositions[i + 1]) {
      float weight = smoothstep(uLensDensityPositions[i], uLensDensityPositions[i + 1], intrinsicV);
      return mix(uLensDensities[i], uLensDensities[i + 1], weight);
    }
  }
  return vec3(-1.0);
}

vec3 lensReflectanceAt(float angleDegrees, float cosOutside) {
  if (uLensAngularCount == 0) {
    float m = 1.0 - cosOutside;
    return uLensNormalReflectance + (vec3(1.0) - uLensNormalReflectance) * m * m * m * m * m;
  }
  for (int i = 0; i < ${MAX_LENS_ANGULAR_KNOTS - 1}; i++) {
    if (i + 1 < uLensAngularCount && angleDegrees <= uLensAnglePositions[i + 1]) {
      float weight = smoothstep(uLensAnglePositions[i], uLensAnglePositions[i + 1], angleDegrees);
      return mix(uLensReflectances[i], uLensReflectances[i + 1], weight);
    }
  }
  return vec3(-1.0);
}

// Return exp(-pathDensity), 1-exp(-pathDensity). Taylor evaluation protects
// absorption near zero where subtraction would discard faint lens attenuation.
vec2 lensAttenuation(float density, float cosInside) {
  if (density == 0.0) return vec2(1.0, 0.0);
  if (cosInside == 0.0) return vec2(0.0, 1.0);
  float pathDensity = density / cosInside;
  if (pathDensity < 0.001) {
    float absorption = pathDensity * (1.0 - pathDensity * (0.5 - pathDensity / 6.0));
    return vec2(1.0 - absorption, absorption);
  }
  float transmission = exp(-pathDensity);
  return vec2(transmission, 1.0 - transmission);
}

LensResponse evaluateLensResponse(float intrinsicV, float angleDegrees) {
  // Initialize the entire return struct and keep one return site. ANGLE's D3D
  // compiler can otherwise flag its synthesized struct return as uninitialized
  // despite each field being assigned on both early-return branches.
  LensResponse response = LensResponse(vec3(-1.0), vec3(-1.0), vec3(-1.0), vec3(-1.0));
  if (intrinsicV >= 0.0 && intrinsicV <= 1.0 && angleDegrees >= 0.0 && angleDegrees <= 90.0) {
    float angle = radians(angleDegrees);
    float cosOutside = clamp(cos(angle), 0.0, 1.0);
    float sinInside = sin(angle) / uLensRefractiveIndex;
    float cosInside = sqrt(max(0.0, 1.0 - sinInside * sinInside));
    vec3 density = lensDensityAt(intrinsicV);
    vec3 reflection = lensReflectanceAt(angleDegrees, cosOutside);
    vec2 red = lensAttenuation(density.r, cosInside);
    vec2 green = lensAttenuation(density.g, cosInside);
    vec2 blue = lensAttenuation(density.b, cosInside);
    response.reflectance = reflection;
    response.transmission = (vec3(1.0) - reflection) * vec3(red.x, green.x, blue.x);
    response.absorption = (vec3(1.0) - reflection) * vec3(red.y, green.y, blue.y);
    response.opticalDensity = density;
  }
  return response;
}

// Source +Z, transformed into the incident camera/light frame, identifies the
// material side. Triangle winding and face-forwarded normals do not.
LensResponse evaluateLensResponse(float intrinsicV, float angleDegrees, bool rearSide) {
  LensResponse response = evaluateLensResponse(intrinsicV, angleDegrees);
  if (rearSide && uLensHasRearResponse != 0 && response.transmission.r >= 0.0) {
    vec3 remaining = vec3(1.0) - response.transmission;
    response.reflectance = remaining * uLensRearReflectionFraction;
    response.absorption = remaining * (vec3(1.0) - uLensRearReflectionFraction);
  }
  return response;
}

void evaluateLensResponse(float intrinsicV, float angleDegrees, out vec3 R, out vec3 T) {
  LensResponse response = evaluateLensResponse(intrinsicV, angleDegrees);
  R = response.reflectance;
  T = response.transmission;
}
`;
