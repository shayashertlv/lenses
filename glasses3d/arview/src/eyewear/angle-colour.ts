/** Owner "exact angle colours" (glasses3d dev UI, three-layer lens editor): the colour a lens reflects as a function
 *  of the viewing angle, drawn by the owner on a bar from "seen at an angle (lens sides)" to "seen straight on (lens
 *  centre)". It is a host effect, not a glTF standard: the GLB keeps its standard KHR_materials_iridescence (the
 *  strength and a fallback coating other viewers draw), and the material's `extras.g3dAngleColour` replaces only the
 *  thin-film colour term inside three's own iridescence path. Fresnel weighting, the strength (iridescenceFactor),
 *  transmission and every other lighting term stay three's, so the effect behaves like a coating whose colour is
 *  exactly what was drawn.
 *
 *  Bar position t = (cos(view) - 0.4) / 0.6, clamped: 0 at the sides end (66 degrees and beyond), 1 straight on. Stops
 *  carry linear-RGB reflectance colours at increasing positions; each transition is centred on the midpoint between
 *  its two stops with width softness * gap, smoothstep (the same rule as the owner gradient v2). */
import {MeshPhysicalMaterial, ShaderChunk, Vector3, type Material} from 'three';

export const ANGLE_COLOUR_KEY = 'g3dAngleColour';
export const ANGLE_COLOUR_SCHEMA = 'g3d.angle_colour/1';
export const SIDES_COS = 0.4;
const MAX_STOPS = 3;

export interface AngleColourStop {colour: [number, number, number]; position: number}
/** `reflection`: the colours replace the coating's reflected colour. `tint` (0..1): the same colours, at the lens's
 *  own brightness, also tint what is seen through the lens by angle (purple sides, green centre), which shows even
 *  where the fixed AR lighting gives few bright reflections. */
export interface AngleColourSpec {schema: string; stops: AngleColourStop[]; softness: number[]; reflection: boolean; tint: number}

const finite01 = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;

/** The validated spec, or null when absent or malformed (a malformed spec is ignored, never half-applied). */
export function angleColourOf(material: Material): AngleColourSpec | null {
  const value: unknown = (material.userData as Record<string, unknown> | undefined)?.[ANGLE_COLOUR_KEY];
  if (!value || typeof value !== 'object') return null;
  const spec = value as Partial<AngleColourSpec>;
  if (spec.schema !== ANGLE_COLOUR_SCHEMA || !Array.isArray(spec.stops) || !Array.isArray(spec.softness)) return null;
  const stops = spec.stops;
  if (stops.length < 2 || stops.length > MAX_STOPS || spec.softness.length !== stops.length - 1) return null;
  let previous = -1;
  for (const stop of stops) {
    if (!stop || !Array.isArray(stop.colour) || stop.colour.length !== 3 || !stop.colour.every(finite01) || !finite01(stop.position)) return null;
    if (previous >= 0 && stop.position - previous < 1e-3) return null;
    previous = stop.position;
  }
  if (!spec.softness.every(finite01) || typeof spec.reflection !== 'boolean' || !finite01(spec.tint)) return null;
  if (!spec.reflection && !(spec.tint > 0)) return null;
  return {schema: ANGLE_COLOUR_SCHEMA, stops: stops.map(stop => ({colour: [...stop.colour] as [number, number, number], position: stop.position})),
    softness: [...spec.softness], reflection: spec.reflection, tint: spec.tint};
}

/** The exact sampling the shader performs, for tests and UI bars. */
export function sampleAngleColour(spec: AngleColourSpec, cosTheta: number): [number, number, number] {
  const t = Math.min(1, Math.max(0, (cosTheta - SIDES_COS) / (1 - SIDES_COS)));
  const stops = spec.stops;
  let rgb: [number, number, number] = [...(stops[0]?.colour ?? [0, 0, 0])];
  for (let i = 0; i + 1 < stops.length; i++) {
    const from = stops[i]!, to = stops[i + 1]!;
    if (i && t < from.position) continue;
    const middle = (from.position + to.position) / 2, half = (spec.softness[i] ?? 0) * (to.position - from.position) / 2;
    let z = half > 0 ? Math.min(1, Math.max(0, (t - (middle - half)) / (2 * half))) : (t >= middle ? 1 : 0);
    z = z * z * (3 - 2 * z);
    rgb = [0, 1, 2].map(channel => from.colour[channel]! * (1 - z) + to.colour[channel]! * z) as [number, number, number];
  }
  return rgb;
}

const TARGET = 'material.iridescenceFresnel = mix( material.iridescenceFresnelDielectric, material.iridescenceFresnelMetallic, material.metalness );';
// Tint is applied where the lighting template has defined its view direction, before any light is evaluated, so the
// diffuse term and three's transmission (material.diffuseContribution) both see it.
const TINT_ANCHOR = 'vec3 geometryClearcoatNormal = vec3( 0.0 );';
const TINT_BLOCK = `
{
  vec3 g3dRgb = g3dAngleColour( saturate( dot( normal, geometryViewDir ) ) );
  vec3 g3dHue = g3dRgb / max( max( g3dRgb.r, g3dRgb.g ), max( g3dRgb.b, 1e-4 ) );
  vec3 g3dTint = mix( vec3( 1.0 ), g3dHue, g3dAngleTint );
  material.diffuseColor *= g3dTint;
  material.diffuseContribution *= g3dTint;
}
`;
const FUNCTIONS = `
uniform float g3dAngleTint;
uniform vec3 g3dAngleColours[${MAX_STOPS}];
uniform float g3dAnglePositions[${MAX_STOPS}];
uniform float g3dAngleSoftness[${MAX_STOPS - 1}];
uniform int g3dAngleCount;
vec3 g3dAngleColour( float cosTheta ) {
  float t = clamp( ( cosTheta - ${SIDES_COS.toFixed(1)} ) / ${(1 - SIDES_COS).toFixed(1)}, 0.0, 1.0 );
  vec3 rgb = g3dAngleColours[ 0 ];
  for ( int i = 0; i < ${MAX_STOPS - 1}; i ++ ) {
    if ( i >= g3dAngleCount - 1 ) break;
    float low = g3dAnglePositions[ i ];
    float high = g3dAnglePositions[ i + 1 ];
    if ( i > 0 && t < low ) continue;
    float middle = 0.5 * ( low + high );
    float halfWidth = g3dAngleSoftness[ i ] * ( high - low ) * 0.5;
    float z = halfWidth > 0.0 ? clamp( ( t - ( middle - halfWidth ) ) / ( 2.0 * halfWidth ), 0.0, 1.0 ) : step( middle, t );
    z = z * z * ( 3.0 - 2.0 * z );
    rgb = mix( g3dAngleColours[ i ], g3dAngleColours[ i + 1 ], z );
  }
  return rgb;
}
`;

/** Install the owner's angle colours on a lens material that carries a valid spec and an active iridescence term.
 *  Returns true when installed. Idempotent; materials without a spec are untouched. */
export function applyAngleColour(material: Material): boolean {
  if (!(material instanceof MeshPhysicalMaterial) || material.userData.g3dAngleColourInstalled) return false;
  const spec = angleColourOf(material);
  const chunk = ShaderChunk.lights_fragment_begin;
  if (!spec || !chunk.includes(TARGET) || !chunk.includes(TINT_ANCHOR)) return false;
  const reflection = spec.reflection && material.iridescence > 0;
  if (!reflection && !(spec.tint > 0)) return false;
  const padded = Array.from({length: MAX_STOPS}, (_, i) => spec.stops[Math.min(i, spec.stops.length - 1)]!);
  const colours = padded.map(stop => stop.colour), positions = padded.map(stop => stop.position);
  const softness = Array.from({length: MAX_STOPS - 1}, (_, i) => spec.softness[i] ?? 0);
  const key = JSON.stringify(spec);
  material.onBeforeCompile = shader => {
    shader.uniforms.g3dAngleColours = {value: colours.map(rgb => new Vector3(...rgb))};
    shader.uniforms.g3dAnglePositions = {value: positions};
    shader.uniforms.g3dAngleSoftness = {value: softness};
    shader.uniforms.g3dAngleCount = {value: spec.stops.length};
    shader.uniforms.g3dAngleTint = {value: spec.tint};
    let lighting = chunk;
    if (reflection) lighting = lighting.replace(TARGET, 'material.iridescenceFresnel = g3dAngleColour( dotNVi );');
    if (spec.tint > 0) lighting = lighting.replace(TINT_ANCHOR, `${TINT_ANCHOR}\n${TINT_BLOCK}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('void main() {', `${FUNCTIONS}\nvoid main() {`)
      .replace('#include <lights_fragment_begin>', lighting);
  };
  material.customProgramCacheKey = () => `g3d-angle-colour:${key}`;
  material.userData.g3dAngleColourInstalled = true;
  material.needsUpdate = true;
  return true;
}
