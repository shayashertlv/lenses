/**
 * The render layer: one WebGL/WebGPU canvas holding the video and the glasses.
 *
 * Keeping the camera feed *inside* the scene rather than behind a transparent
 * canvas is v1's decision and it was right for two reasons that still hold: the
 * lenses can refract what is behind them, and there is no second element that
 * can drift out of alignment with the first.
 *
 * ## The camera is solved, not assumed
 *
 * v1's own note: *"if our virtual camera disagrees, the frame will sit correctly
 * at one distance and slide off the face at every other."* It then hardcoded 63
 * degrees, because that is what MediaPipe assumes when it solves its own
 * transformation matrix — a reasonable choice when you are consuming that
 * matrix. v2 does not consume it, so the field of view comes from the scan's own
 * bundle (`FaceModel.intrinsics`) and `intrinsicsSolved` says whether it was
 * really solved or merely assumed.
 *
 * ## Renderer choice
 *
 * WebGPU where available, WebGL2 otherwise. As of 2026 WebGPU ships by default
 * in Chrome, Edge, Firefox and Safari 26 (including iOS), so the fallback is a
 * courtesy rather than the main path — but it is a real one, and the fallback
 * decision is logged rather than silent.
 */

import * as THREE from 'three';
import type { Intrinsics } from '../core/camera.js';
import type { Pose } from '../core/linalg.js';
import {
  poseToGLMatrix, principalPointOffset, verticalFovDegFor,
} from './convert.js';

export interface SceneHandle {
  renderer: any;
  scene: any;
  camera: any;
  /** The node the wearer's head geometry hangs off. */
  headNode: any;
  /** The node the glasses hang off — a child of `headNode`, so the seat
   *  transform is applied once and never recomputed per frame. */
  frameNode: any;
  background: any;
  backendName: string;
  setSize(width: number, height: number): void;
  setBackgroundSource(canvas: HTMLCanvasElement): void;
  markBackgroundDirty(): void;
  applyIntrinsics(intrinsics: Intrinsics): void;
  setHeadPose(pose: Pose | null): void;
  render(): void;
  dispose(): void;
}

export async function createScene(
  canvas: HTMLCanvasElement, options: { preferWebGPU?: boolean } = {},
): Promise<SceneHandle> {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(63, 1, 10, 5000);
  camera.position.set(0, 0, 0);
  camera.lookAt(0, 0, -1);

  let renderer: any;
  let backendName = 'webgl2';
  const wantGPU = options.preferWebGPU !== false
    && typeof (globalThis as any).navigator?.gpu !== 'undefined'
    && typeof (THREE as any).WebGPURenderer === 'function';

  if (wantGPU) {
    try {
      renderer = new (THREE as any).WebGPURenderer({ canvas, antialias: true, alpha: false });
      await renderer.init();
      backendName = 'webgpu';
    } catch (error) {
      console.warn('WebGPU unavailable, falling back to WebGL2:', error);
      renderer = null;
    }
  }
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  }
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));

  // The head node carries the solved pose; everything about the wearer hangs off
  // it. `matrixAutoUpdate` off because the matrix is written directly from a
  // solved pose and letting three.js recompose it from position/quaternion/scale
  // would round-trip through Float32 for no reason.
  const headNode = new THREE.Object3D();
  headNode.matrixAutoUpdate = false;
  scene.add(headNode);

  const frameNode = new THREE.Object3D();
  frameNode.matrixAutoUpdate = false;
  headNode.add(frameNode);

  // Lighting: a neutral environment until `lighting.ts` estimates a real one
  // from the frame. Two lights rather than one, because a single key on a
  // specular frame reads as a sticker.
  const key = new THREE.DirectionalLight(0xffffff, 2.0);
  key.position.set(-0.4, 0.6, 1);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.6);
  fill.position.set(0.6, -0.2, 0.8);
  scene.add(fill);
  scene.add(new THREE.AmbientLight(0xffffff, 0.35));

  let background: any = null;

  const handle: SceneHandle = {
    renderer, scene, camera, headNode, frameNode,
    get background() { return background; },
    backendName,

    setSize(width, height) {
      renderer.setSize(width, height, false);
    },

    /**
     * The background is a plain `Texture` over a canvas, never a `VideoTexture`.
     *
     * v1's hardest-won architectural rule, carried over verbatim: a
     * `VideoTexture` pulls frames from the element itself, which silently
     * reintroduces a second clock — the pixels on screen would come from
     * whenever the GPU last sampled the video, not from the frame the pose was
     * solved on. Everything in `app/framelock.ts` depends on this staying a
     * canvas.
     */
    setBackgroundSource(source) {
      background = new THREE.CanvasTexture(source);
      background.colorSpace = THREE.SRGBColorSpace;
      scene.background = background;
    },

    markBackgroundDirty() {
      if (background) background.needsUpdate = true;
    },

    applyIntrinsics(intrinsics) {
      camera.fov = verticalFovDegFor(intrinsics);
      camera.aspect = intrinsics.width / intrinsics.height;
      const offset = principalPointOffset(intrinsics);
      if (offset) {
        // three.js has no direct principal-point control, so the projection is
        // built and then sheared. Only reached when the bundle was asked to
        // solve the principal point, which it is not by default.
        camera.updateProjectionMatrix();
        camera.projectionMatrix.elements[8] += offset.x;
        camera.projectionMatrix.elements[9] += offset.y;
        camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
      } else {
        camera.updateProjectionMatrix();
      }
    },

    setHeadPose(pose) {
      if (!pose) { headNode.visible = false; return; }
      headNode.visible = true;
      const m = new Float32Array(16);
      poseToGLMatrix(m, pose);
      headNode.matrix.fromArray(m);
      headNode.updateMatrixWorld(true);
    },

    render() {
      renderer.render(scene, camera);
    },

    dispose() {
      renderer.dispose?.();
      background?.dispose?.();
    },
  };

  return handle;
}

/** Applies a solved seat transform to the frame node. Called ONCE per fit. */
export function applySeat(handle: SceneHandle, seat: Pose): void {
  const m = new Float32Array(16);
  poseToGLMatrix(m, seat);
  handle.frameNode.matrix.fromArray(m);
  handle.frameNode.updateMatrixWorld(true);
}
