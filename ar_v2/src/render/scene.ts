/**
 * The render layer: one WebGL/WebGPU canvas holding the video, a camera solved
 * from the scan, and the two empty nodes the wearer's head and the frame will
 * hang off.
 *
 * ## What is actually in the scene today
 *
 * The video, three lights, `headNode`, `frameNode` — and, since stages 1 and 2
 * of the occlusion plan, the parametric frame from `render/frame-geometry.ts`
 * (swapped under `frameNode` by `attachFrame`) and the scanned face as a
 * depth-only occluder (`setOccluder` + `OCCLUDER_BIAS_MM`). There is still no
 * glTF loader, and the temple arms past the face oval have nothing to hide
 * behind until stage 3's head proxy. `attachFrame` obeys the three rules that
 * used to be listed here as debts owed by a future implementation:
 *   - **Swap, do not accumulate.** `fitFrame` runs on every frame change, so the
 *     previous child of `frameNode` is removed and disposed
 *     (`disposeFrameObject` — every geometry and material, exactly once; v1
 *     leaked materials on swap) before the next is added.
 *   - **Call `frameNode.updateMatrixWorld(true)` after adding a child**, because
 *     `matrixAutoUpdate` is off on these nodes: a child added after `applySeat`
 *     would inherit a stale world matrix until something forced the update.
 *   - **Obey the convention rule on `frameNode` below.** Geometry authored in
 *     frame-local or face space goes in unchanged. Passing it through a CV->GL
 *     flip puts it 127 mm from where the solve put it.
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
import type { FrameAsset } from '../fit/frame-asset.js';
import {
  occluderBiasedMatrix, poseToGLMatrix, poseToUnflippedMatrix, principalPointOffset,
  verticalFovDegFor,
} from './convert.js';
import { createFrameObject, disposeFrameObject } from './frame-geometry.js';

/**
 * How far the occluder sits off the head pose along the camera axis, mm.
 * NEGATIVE is toward the camera — the stage-0 instrument's sign convention,
 * pinned by a test.
 *
 * Read off the instrument's bias sweep (reports/occlusion.txt, pooled over
 * yaw >= 30, five seeds), not asserted: at 0 mm the scanned occluder leaves
 * 7.71% of contested frame samples X-rayed (median; per-seed 2.99-11.14);
 * -0.5 mm buys that down to 6.99% for +0.24 pp of forgiven hides (0.31 ->
 * 0.55%); -1.0 buys 6.17% for 0.84%; past -1.0 the returns diminish while the
 * forgiven cost accelerates. -0.5 is the knee. The sweep also showed the
 * larger truth: most residual X-ray is EDGE-COVERAGE error — samples on pixels
 * the face mask does not cover at all — which no depth bias can touch. That is
 * stage 3's problem (the head proxy) and stage 5's (the mask edge), not this
 * constant's.
 */
export const OCCLUDER_BIAS_MM = -0.5;

export interface SceneHandle {
  renderer: any;
  scene: any;
  camera: any;
  /**
   * The node the wearer's head geometry would hang off. Its parent is the scene
   * root, and the scene root *is* GL camera space here — the camera sits at the
   * origin looking down -Z and is never moved. So this is the one node whose
   * matrix carries the CV-camera -> GL flip, and the ONLY one written with
   * `poseToGLMatrix`.
   */
  headNode: any;
  /**
   * The node the frame geometry hangs off — a child of `headNode`, so the
   * seat transform is applied once and never recomputed per frame. Its one
   * child is the parametric frame `attachFrame` swaps in.
   *
   * **Convention rule, and it is the whole reason this comment is long.**
   * `headNode.matrix` already carries the flip. `frameNode` sits underneath it,
   * and the seat is a rigid transform *from frame-local into face space* — both
   * ends in the same handedness, which is also GL's. So its matrix is the plain
   * column-major `[R | t]` (`poseToUnflippedMatrix`), and so is anything under it.
   * Writing `poseToGLMatrix(seat)` here double-counts the flip: measured with a
   * real `solveSeat`, the right lens centre lands at (-31.74, -20.13, -460.34)
   * instead of (-31.74, 20.13, -339.66) — 127 mm off, mirrored in Y and Z, below
   * and behind the head. `F.S.F` is not the fix either; it reproduces the same
   * number. There is no flip to apply.
   */
  frameNode: any;
  /**
   * The scanned face, rendered depth-only — stage 2 of the occlusion plan.
   *
   * A SIBLING of `headNode`, not a child, because its matrix is the head's GL
   * matrix composed with the camera-axis bias (`OCCLUDER_BIAS_MM`), and a
   * camera-space offset cannot be expressed as a constant child transform.
   * `setHeadPose` writes both matrices from the same pose so they can never
   * drift apart. Empty until `setOccluder` runs.
   */
  occluderNode: any;
  background: any;
  backendName: string;
  setSize(width: number, height: number): void;
  setBackgroundSource(canvas: HTMLCanvasElement): void;
  markBackgroundDirty(): void;
  applyIntrinsics(intrinsics: Intrinsics): void;
  setHeadPose(pose: Pose | null): void;
  /**
   * Builds (or rebuilds) the depth-only occluder from a face model's solved
   * vertices. `positions` is the SAME buffer the seat solved against —
   * `model.positions`, face-space millimetres, handed over without any
   * intermediate transform (the Float32 narrowing for the GPU is the only
   * copy). Seat and occluder being one surface is the invariant this whole
   * design rests on; the caller passing anything else here re-opens v1's bug.
   */
  setOccluder(positions: Float64Array, indices: Uint32Array): void;
  /**
   * Applies per-vertex face-space deltas (mm, dense 3*vertexCount) on top of
   * the occluder's base positions — the edge-snap's output. Pass the zero
   * array to return to the pure scan. The base is never mutated: the snap is
   * a per-frame opinion about the image, not a change to the measurement.
   */
  nudgeOccluder(deltaMm: Float64Array): void;
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

  // Starts empty; `attachFrame` swaps the parametric frame under it. `applySeat`
  // writes its matrix; see `SceneHandle.frameNode` for the convention any child
  // of it has to respect.
  const frameNode = new THREE.Object3D();
  frameNode.matrixAutoUpdate = false;
  headNode.add(frameNode);

  // The occluder: scene child, matrix written by `setHeadPose` (head pose +
  // camera-axis bias). Hidden until a scan exists and a pose arrives.
  const occluderNode = new THREE.Object3D();
  occluderNode.matrixAutoUpdate = false;
  occluderNode.visible = false;
  scene.add(occluderNode);
  let occluderBase: Float32Array | null = null;
  let occluderMesh: any = null;

  // Lighting: a fixed neutral environment. Estimating a real one from the video
  // frame is the intended upgrade and nothing implements it — there is no
  // lighting module in this tree, and this comment used to name one
  // (`lighting.ts`) that has never existed. Two lights rather than one, because
  // a single key on a specular frame reads as a sticker.
  const key = new THREE.DirectionalLight(0xffffff, 2.0);
  key.position.set(-0.4, 0.6, 1);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.6);
  fill.position.set(0.6, -0.2, 0.8);
  scene.add(fill);
  scene.add(new THREE.AmbientLight(0xffffff, 0.35));

  let background: any = null;

  const handle: SceneHandle = {
    renderer, scene, camera, headNode, frameNode, occluderNode,
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
      if (!pose) { headNode.visible = false; occluderNode.visible = false; return; }
      headNode.visible = true;
      const m = new Float32Array(16);
      poseToGLMatrix(m, pose);
      headNode.matrix.fromArray(m);
      headNode.updateMatrixWorld(true);
      // The occluder rides the same pose, pushed OCCLUDER_BIAS_MM along the
      // camera axis. Visible only once it has geometry — an empty Object3D is
      // harmless either way, but the flag keeps the scene graph honest.
      if (occluderNode.children.length > 0) {
        occluderNode.visible = true;
        const mb = new Float32Array(16);
        occluderBiasedMatrix(mb, m, OCCLUDER_BIAS_MM);
        occluderNode.matrix.fromArray(mb);
        occluderNode.updateMatrixWorld(true);
      }
    },

    setOccluder(positions, indices) {
      for (const child of [...occluderNode.children]) {
        occluderNode.remove(child);
        child.geometry?.dispose?.();
        child.material?.dispose?.();
      }
      occluderBase = new Float32Array(positions);
      const geometry = new THREE.BufferGeometry();
      // Float32 narrowing is the only copy between the seat's surface and the
      // GPU's. Face-space millimetres, unchanged — no flip; the node's matrix
      // carries it (see `SceneHandle.occluderNode`).
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(occluderBase), 3));
      geometry.setIndex(new THREE.BufferAttribute(indices.slice(), 1));
      geometry.computeVertexNormals();
      // Depth-only: the universal recipe. Colour writes off, depth writes on,
      // rendered before everything that must be hidden by skin.
      const material = new THREE.MeshBasicMaterial({ colorWrite: false });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = -1;
      occluderNode.add(mesh);
      occluderNode.updateMatrixWorld(true);
      occluderMesh = mesh;
    },

    nudgeOccluder(deltaMm) {
      if (!occluderMesh || !occluderBase) return;
      const attr = occluderMesh.geometry.getAttribute('position');
      const a = attr.array as Float32Array;
      const n = Math.min(a.length, occluderBase.length, deltaMm.length);
      for (let i = 0; i < n; i++) a[i] = occluderBase[i] + deltaMm[i];
      attr.needsUpdate = true;
      occluderMesh.geometry.computeBoundingSphere?.();
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

/**
 * Applies a solved seat transform to the frame node. Called ONCE per fit.
 *
 * No flip. `seat` is frame-local -> face space, `frameNode`'s parent already
 * changed basis, and face space agrees with GL — see the convention rule on
 * `SceneHandle.frameNode`.
 */
export function applySeat(handle: SceneHandle, seat: Pose): void {
  const m = new Float32Array(16);
  poseToUnflippedMatrix(m, seat);
  handle.frameNode.matrix.fromArray(m);
  handle.frameNode.updateMatrixWorld(true);
}

/**
 * Swaps the renderable frame under `frameNode`: dispose the old, add the new,
 * refresh the world matrices. Called by `fitFrame` BEFORE `applySeat`, so the
 * one place that writes the seat matrix also covers the fresh child — but the
 * `updateMatrixWorld(true)` here makes the attach correct on its own, whatever
 * order a future caller picks (`matrixAutoUpdate` is off on these nodes and a
 * child added without it inherits a stale world matrix).
 *
 * The geometry is frame-local millimetres and goes in UNCHANGED — no flip; see
 * the convention rule on `SceneHandle.frameNode`.
 */
export function attachFrame(handle: SceneHandle, asset: FrameAsset): void {
  const node = handle.frameNode;
  for (const child of [...node.children]) {
    node.remove(child);
    disposeFrameObject(child);
  }
  node.add(createFrameObject(asset));
  node.updateMatrixWorld(true);
}
