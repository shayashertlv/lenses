/** Capture lighting (glasses3d/plan/archive/20261019/WP1_DESIGN.md §5 `setLighting`): the environment the eyewear reflects, as a public renderer setting
 *  instead of the QA harness reaching into private fields. Only the environment maps and the illumination multiplier
 *  change; no material coefficient and no model byte does.
 *
 *  - 'room': the runtime's own two rooms (renderer.ts): three's RoomEnvironment for the scene and native lenses, and the
 *    see-through room (no +Z front panel, eyewear-reflection.ts) for translucent (crystal) frame materials.
 *  - 'broad_studio' / 'side_studio': a neutral analytic studio (overhead or side key, a soft fill), for both.
 *  The scene map is pre-blurred 0.04 like the runtime room; the crystal map is unblurred like the see-through room. */
import {DataTexture, EquirectangularReflectionMapping, FloatType, LinearSRGBColorSpace, PMREMGenerator, RGBAFormat, Scene, Vector3} from 'three';
import type {WebGLRenderer, WebGLRenderTarget} from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {createSeeThroughRoom} from './eyewear-reflection.ts';

export const LIGHTING_PRESETS = Object.freeze(['room', 'broad_studio', 'side_studio'] as const);
export type LightingPreset = typeof LIGHTING_PRESETS[number];
export interface LightingConfig {
  readonly preset: LightingPreset;
  /** scene.environmentIntensity (the runtime uses 0.8); 0 < intensity <= 4. */
  readonly intensity: number;
}

/** Throws for an unusable configuration (a caller error, not model content). */
export function validateLighting(config: LightingConfig): LightingConfig {
  if (!config || !LIGHTING_PRESETS.includes(config.preset)) throw new Error(`Unknown lighting preset ${JSON.stringify(config?.preset)}.`);
  if (typeof config.intensity !== 'number' || !Number.isFinite(config.intensity) || config.intensity <= 0 || config.intensity > 4) {
    throw new Error('The lighting intensity must be > 0 and <= 4.');
  }
  return {preset: config.preset, intensity: config.intensity};
}

/** The analytic studio's equirectangular radiance: a broad key (overhead or side), a fill and a dim floor. */
export function studioTexture(preset: Exclude<LightingPreset, 'room'>): DataTexture {
  const width = 256, height = 128, data = new Float32Array(width * height * 4);
  const key = new Vector3(...(preset === 'broad_studio' ? [.2, .9, .45] as const : [-.85, .3, .45] as const)).normalize();
  const fill = new Vector3(.75, .2, -.6).normalize(), direction = new Vector3();
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const phi = (x + .5) / width * Math.PI * 2, theta = (y + .5) / height * Math.PI;
    direction.set(-Math.sin(theta) * Math.cos(phi), Math.cos(theta), Math.sin(theta) * Math.sin(phi));
    const radiance = .10 + 1.8 * Math.exp((direction.dot(key) - 1) / .22) + .55 * Math.exp((direction.dot(fill) - 1) / .38);
    const offset = (y * width + x) * 4; data[offset] = data[offset + 1] = data[offset + 2] = radiance; data[offset + 3] = 1;
  }
  const texture = new DataTexture(data, width, height, RGBAFormat, FloatType);
  texture.mapping = EquirectangularReflectionMapping; texture.colorSpace = LinearSRGBColorSpace; texture.needsUpdate = true;
  return texture;
}

export interface LightingMaps {
  readonly config: LightingConfig;
  /** What scene.environment gets (and native lenses with an explicit map). */
  readonly scene: WebGLRenderTarget;
  /** What translucent frame materials (crystal) reflect. */
  readonly crystal: WebGLRenderTarget;
  dispose(): void;
}

export function createLightingMaps(renderer: WebGLRenderer, config: LightingConfig): LightingMaps {
  const valid = validateLighting(config);
  const generator = new PMREMGenerator(renderer);
  let room: RoomEnvironment | null = null, seeThrough: RoomEnvironment | null = null, studio: Scene | null = null, texture: DataTexture | null = null;
  let scene: WebGLRenderTarget | null = null, crystal: WebGLRenderTarget | null = null;
  try {
    if (valid.preset === 'room') {room = new RoomEnvironment(); seeThrough = createSeeThroughRoom();}
    else {studio = new Scene(); texture = studioTexture(valid.preset); studio.background = texture;}
    scene = generator.fromScene((room ?? studio)!, .04);
    crystal = generator.fromScene((seeThrough ?? studio)!, 0);
    const maps = {config: valid, scene, crystal, dispose() {maps.scene.dispose(); maps.crystal.dispose();}};
    return maps;
  } catch (error) {scene?.dispose(); crystal?.dispose(); throw error;}
  finally {generator.dispose(); room?.dispose(); seeThrough?.dispose(); texture?.dispose();}
}
