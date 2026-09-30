/** What the see-through eyewear reflects, and how a reflection is added to the camera seen through it. It applies to the
 *  canonical lenses and to translucent (crystal) frame materials only: opaque acetate, metal and everything else keep the
 *  scene's room (renderer.ts: three's RoomEnvironment through PMREM blur 0.04 at SCENE_ENVIRONMENT_INTENSITY), untouched.
 *
 *  Why (Tom Ford FT1123-D review, 2026-09-30). RoomEnvironment has one emitter on the +Z side, `light4` (emissive 43, a
 *  4.38 x 5.44 panel about 17 degrees wide and 10-29 degrees above the view axis). In a selfie mirror +Z is the camera's
 *  side, so every lens or crystal face turned toward the camera mirrors that panel at near-normal incidence: F0 0.04 x
 *  scene intensity 0.8 x 43 = 1.38 linear, on materials drawn without tone mapping because they carry the camera image
 *  (renderer.ts configure). It clipped to flat white.
 *  - Lenses: the two hard white rectangles on every delivery. r0009 had 10.2 % of the visible lens clipped at the front
 *    pose and 13.5 % at pitch -4; the largest clipped blob was 0.126 of vb's lens, 0.274 of miu's, 0.115 of rayban's; the
 *    owner's live screenshot about 11 %.
 *    98-99 % of the clipped lens pixels reflect light4 and no other emitter. No asset change removes it: base curve 8 still
 *    left 5 % white, roughness 0.4 hazed 40 % of the lens, reflectance 0.02 left pale hard rectangles. A base-4 lens
 *    mirrors any camera-side panel as one fixed rectangle about R x theta / 2 = 20 mm wide on a 47 mm lens, and the
 *    product photos show none.
 *  - Crystal: the white bridge slab and the grey top-to-bottom wash (12 % of r0009's crystal clipped, the top of the rim
 *    reflecting 2.4x the bottom while the look-through was flat), which the owner read as milky plastic. The owner's paid
 *    "glossy" round 1 changed 12 of 345,600 front-render pixels by more than 10 levels: the panel was already clipped
 *    (three also floors roughness at 0.0525, and the coat was never exported).
 *  So the see-through room is the scene's room without that panel. Dimming it instead was measured and rejected for both:
 *  on the lenses 0.1 / 0.25 of the panel left faint / grey hard-edged rectangles (p99 luma 173 / 208); on the crystal's
 *  nearly flat bridge 0.15 / 0.05 left one pale hard-edged block (r0009 and test-pilot-002 on the owner-like dim skin; the
 *  whole bridge at pitch -4 at 0.15; the clearcoat copy's crystal p99 luma 189 against 157 without the panel), the milky
 *  patch again. The crystal's gloss is the room's side emitters and ceiling on its curved bevels, reflected unblurred.
 *  The scene room keeps the panel: without it the glossy opaque acetate loses its frontal sheen (vb's navy bridge,
 *  rayban's tortoise went flatter), and the gold hardware is accepted as it is. The see-through room is three's own
 *  RoomEnvironment with the panel found and removed, so everything else a lens or crystal reflects is the scene's room,
 *  and a three upgrade that moves or drops the panel fails here (and in tests/eyewear-reflection.test.ts) instead of
 *  silently bringing it back. */
import {Mesh, MeshLambertMaterial} from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';

/** three 0.185.1 RoomEnvironment.js `light4` ("+z"): the only emitter within 45 degrees of a selfie camera's axis (20). */
export const FRONT_PANEL = Object.freeze({emissiveIntensity: 43, position: Object.freeze([-0.462, 8.89, 14.52] as const),
  scale: Object.freeze([4.38, 5.441, 0.088] as const)});

/** The +Z emitter of a RoomEnvironment: exactly one emissive mesh in front of the room's centre, where three 0.185.1 has it. */
export function findFrontPanel(room: RoomEnvironment): Mesh {
  const panels = room.children.filter((child): child is Mesh => child instanceof Mesh && child.material instanceof MeshLambertMaterial
    && child.material.emissiveIntensity > 1 && child.position.z > 10);
  const [panel] = panels;
  if (panels.length !== 1 || !panel) throw new Error(`The see-through room expects exactly one front panel in three's RoomEnvironment (found ${panels.length}).`);
  const material = panel.material as MeshLambertMaterial;
  if (material.emissiveIntensity !== FRONT_PANEL.emissiveIntensity
    || FRONT_PANEL.position.some((value, axis) => Math.abs(panel.position.getComponent(axis) - value) > 1e-6)) {
    throw new Error('three\'s RoomEnvironment front panel moved or changed intensity: re-measure the see-through room (eyewear-reflection.ts).');
  }
  return panel;
}

/** The see-through room: three's RoomEnvironment without its front panel. The caller disposes it. */
export function createSeeThroughRoom(): RoomEnvironment {
  const room = new RoomEnvironment();
  try {
    const panel = findFrontPanel(room);
    room.remove(panel); (panel.material as MeshLambertMaterial).dispose();     // the shared box geometry stays the room's
  } catch (error) {room.dispose(); throw error;}
  return room;
}

/** The reflection limit: the linear level a reflection added to the look-through approaches and never exceeds. 0.9 is
 *  sRGB 243, below the observation's clipped threshold (min channel 250); a pixel the reflection brings to the limit keeps
 *  its gradient instead of becoming a flat 255 slab. So a reflection the limit compresses lands between the surface's
 *  knee and 0.9 (a lens: 0.8-0.9 linear, sRGB 231-243), which a count of pixels at 250 cannot see (review AR-R3). */
export const REFLECTION_WHITE = .9;

/** Where a surface's shoulder starts. The reflection is added unchanged while its peak channel is below `share` of the
 *  headroom or while the look-through's brightest channel plus it stays below `level` (linear), whichever reaches
 *  further, and never past the headroom: knee = min(max(share x headroom, level - through), headroom). */
export interface ReflectionKnee {readonly share: number; readonly level: number}

/** Canonical lenses: exact up to a total of 0.8 linear (sRGB 231), then a 0.1 shoulder into the limit. Absolute, not a
 *  share of the headroom (review AR-R2, 2026-09-30): a lens's reflection is scaled by the owner's lensenv before the limit
 *  (renderer.ts: envMapIntensity 0.8 x lensenv), and the pipeline recommends lensenv as a linear ratio (lens_colour.py:
 *  env = LUMA(photo - T bg) / LUMA(A)), so the shoulder must leave ordinary mirror reflections linear. At half the headroom
 *  (the first limit) it did not: invu-astra2's mirror reflects p50 0.29 / p90 0.57 / p99 0.80 linear at lensenv 1, over a
 *  0.42 knee. Harness renders (the see-through room drawn with the reflection added linearly as the reference; front view,
 *  dim-skin fixture): at its delivered lensenv 1.49, accepted "perfect" live on the old runtime, the first knee drew 35.7 %
 *  of its visible lens more than 2 sRGB levels darker (p90 24) and its mean lens luma 0.311 against 0.349; this knee 12.6 %
 *  (p90 8) and 0.328, of which 9.6 points are pixels the linear draw clips at 250+ in a channel (the old runtime drew them
 *  at 250-255; here they stay under the limit, hue kept) and 1.9 points 243-249: what is left is the limit itself. At
 *  lensenv 1: 13.2 % -> 2.0 %. oakley-astra2 at its delivered 1.23: 6.5 % -> 0.0 % (the linear draw to the level). The
 *  panel that clipped (1.38 linear) still ends below the limit, now between 0.89 and 0.9. */
export const LENS_REFLECTION_KNEE_LEVEL = .8;
export const LENS_REFLECTION_KNEE: ReflectionKnee = Object.freeze({share: 0, level: LENS_REFLECTION_KNEE_LEVEL});
/** The crystal's knee, a share of the headroom (the twin, translucent-twin.ts): exact below half of it, where the room's
 *  walls and side emitters on the bevels sit, and a wide shoulder above it, so a hot bevel highlight keeps a long
 *  gradient. The crystal has no lensenv, and the see-through measurements were taken with it (r0009's crystal clipped
 *  share 0.116 -> 0, frame_see_through 0.553 -> 0.701): unchanged by the lens knee. automation's appearance.py mirrors
 *  it for crystal bands (tests/test_appearance_reflection_limit.py reads this line); a LENS port (lens_colour's lensenv
 *  recommendation) must use LENS_REFLECTION_KNEE_LEVEL instead. */
export const REFLECTION_KNEE = .5;
export const CRYSTAL_REFLECTION_KNEE: ReflectionKnee = Object.freeze({share: REFLECTION_KNEE, level: 0});

/** GLSL of `limitReflection`, for the lens and crystal fragment shaders (declared before `main`); call it through
 *  `reflectionLimitCall` so the surface's knee is the one declared here. */
export const REFLECTION_LIMIT_GLSL = /* glsl */`
vec3 eyewearReflectionLimit( const in vec3 reflected, const in vec3 through, const in float kneeShare, const in float kneeLevel ) {
  // eyewear-reflection.ts: the headroom the look-through leaves below the limit, and a C1 exponential shoulder above the
  // surface's knee, applied to the reflection's peak channel so its hue is kept. Never exceeds REFLECTION_WHITE.
  float peak = max( max( reflected.r, reflected.g ), reflected.b );
  float brightest = max( max( through.r, through.g ), through.b );
  float headroom = max( ${REFLECTION_WHITE.toFixed(4)} - brightest, 0.0 );
  float knee = min( max( kneeShare * headroom, kneeLevel - brightest ), headroom ), span = headroom - knee;
  if ( peak <= knee ) return reflected;
  if ( span <= 1e-6 ) return vec3( 0.0 );
  return reflected * ( ( knee + span * ( 1.0 - exp( - ( peak - knee ) / span ) ) ) / peak );
}
`;

/** The GLSL call of the limit with a surface's knee, e.g. reflectionLimitCall('outgoingLight', 'twinThrough', CRYSTAL_REFLECTION_KNEE). */
export function reflectionLimitCall(reflected: string, through: string, knee: ReflectionKnee): string {
  return `eyewearReflectionLimit( ${reflected}, ${through}, ${knee.share.toFixed(4)}, ${knee.level.toFixed(4)} )`;
}

/** Add a surface reflection (linear RGB) to the light seen through the surface without running into white: the returned
 *  reflection is unchanged below the surface's knee (ReflectionKnee; the headroom is REFLECTION_WHITE minus the
 *  look-through's brightest channel) and above it approaches the headroom along an exponential shoulder (continuous
 *  slope), scaled on its peak channel so its hue is kept. Where the look-through is already at the limit (a white wall
 *  seen through clear crystal) nothing is added. The reference of REFLECTION_LIMIT_GLSL. Without a knee it is the
 *  crystal's (the first limit's), which automation's node cross-check of appearance.limit_reflection calls. */
export function limitReflection(reflected: readonly [number, number, number], through: readonly [number, number, number],
  {share, level}: ReflectionKnee = CRYSTAL_REFLECTION_KNEE): [number, number, number] {
  const brightest = Math.max(...through), peak = Math.max(...reflected), headroom = Math.max(REFLECTION_WHITE - brightest, 0);
  const knee = Math.min(Math.max(share * headroom, level - brightest), headroom), span = headroom - knee;
  if (peak <= knee) return [...reflected];
  if (span <= 1e-6) return [0, 0, 0];
  const scale = (knee + span * (1 - Math.exp(-(peak - knee) / span))) / peak;
  return [reflected[0] * scale, reflected[1] * scale, reflected[2] * scale];
}
