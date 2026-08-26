/**
 * The render layer: one WebGL/WebGPU canvas holding the video, a camera solved
 * from the scan, and the two empty nodes the wearer's head and the frame will
 * hang off.
 *
 * ## What is actually in the scene today
 *
 * The video, three lights, `headNode`, `frameNode` — and, since stages 1 and 2
 * of the occlusion plan, the parametric frame from `render/frame-geometry.ts`
 * (swapped under `frameNode` by `attachFrame`), a real glTF asset when one is
 * loaded (`render/frame-mesh.ts`), and the scanned face lofted into a whole
 * HEAD as a depth-only occluder (`setOccluder` + `OCCLUDER_BIAS_MM`).
 *
 * The head matters because the face does not reach: MediaPipe's mesh stops
 * 24.4 mm back and a temple runs to an ear rest 96 mm back, so 72 mm of the arm
 * used to be drawn against nothing. Measured, a face-only occluder lets 8.9% of
 * temple samples X-ray through the head at yaw 45 and 12.5% at yaw 60; the loft
 * takes both to 0.0%. See `core/head.ts`. `attachFrame` obeys the three rules that
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
 * ## Renderer choice — and this section was false
 *
 * It used to say: "WebGPU where available, WebGL2 otherwise. As of 2026 WebGPU
 * ships by default in Chrome, Edge, Firefox and Safari 26, so the fallback is a
 * courtesy rather than the main path."
 *
 * **Every machine takes the fallback, and always has.** three.js ships
 * `WebGPURenderer` in a separate build (`three.webgpu.js`) which this tree does
 * not vendor: `scripts/fetch-vendor.mjs` pins `three.module.js`, and the only
 * occurrence of the string `WebGPURenderer` in that 1.2 MB bundle is inside a
 * doc comment. Verified by importing it in Node — `typeof THREE.WebGPURenderer`
 * is `undefined`. So `wantGPU` below is a constant false, the branch it guards
 * is unreachable, and `backendName` is `'webgl2'` unconditionally.
 *
 * That mattered beyond tidiness: `backendName` goes into the diagnostics a
 * wearer pastes, so anyone reading one concluded the fallback had fired on that
 * particular machine. It fires on all of them.
 *
 * The branch is kept rather than deleted because vendoring the WebGPU build is a
 * one-line change to `fetch-vendor.mjs` the day it is wanted — and because WebGL2
 * is where this tree needs to be anyway: three's transmission pass, `PMREMGenerator`
 * and the transmission render target are all WebGL-path features, and transmission
 * is what makes a lens look like glass.
 */

import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { Intrinsics } from '../core/camera.js';
import { buildHeadWithEars, reloftSkull, type HeadShell } from '../core/head.js';
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
  setOccluder(
    positions: Float64Array, indices: Uint32Array,
    earRests: readonly [ArrayLike<number>, ArrayLike<number>],
  ): void;
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

  // The four settings without which a PBR asset renders as a flat silhouette.
  // None of them was here while the only thing drawn was a parametric tube.
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  // ACES rather than linear: the frame is composited over a camera frame that
  // has already been through its own tone curve, so a linear frame reads as a
  // sticker with blown highlights on the metal.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  // Shadows are what ground the frame on the face — v1's finding, and its own
  // note is the argument: without one the glasses read as floating in front of
  // the wearer rather than resting on them.
  renderer.shadowMap.enabled = true;
  // PCF rather than PCFSoft: the softness comes from `shadow.radius` below, and
  // r185 deprecated the soft variant.
  renderer.shadowMap.type = THREE.PCFShadowMap;

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
  /** The loft, kept so the snap can re-run it. See `nudgeOccluder`. */
  let occluderShell: HeadShell | null = null;
  let reloftScratch: Float64Array | null = null;
  /** Scratch, so `setHeadPose` allocates nothing on the per-frame path. */
  const headWorld = new THREE.Vector3();

  // ---------------------------------------------------------------- lighting
  //
  // Estimating the room's real light from the video is still the intended
  // upgrade and still unimplemented. What is here now is v1's rig, ported with
  // its reasoning and with **every length converted from centimetres to
  // millimetres** — v1's scene is in cm and this one is in mm (`MM_TO_SCENE`
  // is 1), so a copied shadow frustum would be ten times too small and the
  // shadow would vanish off the side of its own map.
  //
  // A small room bounced into an environment map. Procedural, so there is
  // nothing to fetch and the app stays offline. Without it a metal ferrule and
  // a black acetate temple render as the same flat grey, because a PBR material
  // with no environment has nothing to reflect.
  const pmrem = new THREE.PMREMGenerator(renderer);
  const environmentTexture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environment = environmentTexture;
  pmrem.dispose();

  /**
   * The key, which also casts the contact shadow.
   *
   * Upper-front-right of the head, the classic portrait key. It TRACKS the head
   * (see `setHeadPose`) rather than sitting still, and v1's reason is the one
   * that matters: a fixed light would need a shadow frustum covering everywhere
   * the head might go, and the shadow would pop in and out of resolution as the
   * wearer leaned.
   */
  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  // Softness is what makes a contact shadow read as a shadow rather than as a
  // second, darker pair of glasses drawn on the face. A frame sits about 10 mm
  // off the skin, and at that distance a real shadow's edge is already diffuse;
  // a pin-sharp one looks painted on. The larger map pays for the blur.
  key.shadow.radius = 5;
  // MILLIMETRES. A head is ~250 mm, so a +-250 mm box catches frame and face.
  key.shadow.camera.left = -250;
  key.shadow.camera.right = 250;
  key.shadow.camera.top = 250;
  key.shadow.camera.bottom = -250;
  key.shadow.camera.near = 50;
  key.shadow.camera.far = 3000;
  key.shadow.bias = -0.0002;   // unitless, in depth-buffer terms
  key.shadow.normalBias = 6;   // mm — v1's 0.6 cm
  scene.add(key);
  scene.add(key.target);

  /**
   * The screen the wearer is looking at, and it is what puts glass in the frames.
   *
   * v1's finding, and it is worth carrying whole because it is not obvious. A
   * lens facing the camera reflects whatever is BEHIND the camera. The
   * procedural room has no bright feature back there and the key sits ~30
   * degrees off the view axis, which is thirty times the width of a polished
   * lens's specular lobe. Measured on a real capture, scaling the lens's
   * environment reflection **twenty-fold moved a single pixel** — so a frame
   * with modelled lenses rendered as a frame with empty rims, which is exactly
   * how it was reported. (v1's `GLASS_ENVIRONMENT_BOOST` ended at 1.0 for the
   * same reason: the boost was never the answer, this light was.)
   *
   * Putting a light at the camera is not a cheat. It is the one light in the
   * room whose position this app actually knows: somebody at a webcam is lit by,
   * and looking at, a screen roughly where the lens is, and a screen reflection
   * is the single most characteristic thing on a pair of glasses in a webcam
   * photograph.
   *
   * Weak on purpose, and that is what makes it usable. With light and view
   * coincident the half-vector IS the surface normal, so a polished surface
   * concentrates the whole lobe into a highlight while a matte one barely
   * registers it. A light strong enough to read as FILL would be four times this
   * and would flatten the face with it.
   */
  const screen = new THREE.DirectionalLight(0xffffff, 0.35);
  scene.add(screen);
  scene.add(screen.target);

  scene.add(new THREE.AmbientLight(0xffffff, 0.35));

  /** Where the key sits relative to the head, millimetres. v1's cm x10. */
  const KEY_OFFSET_MM = new THREE.Vector3(300, 600, 1000);

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
        /**
         * An off-centre frustum, three.js's own way.
         *
         * **What was here sheared the projection in the WRONG DIRECTION, by
         * twice the offset — strictly worse than not shearing at all.**
         * `projectionMatrix.elements[8] += offset.x` looks right and is not:
         * `Matrix4.makePerspective` sets `te[8] = (right+left)/(right-left)`
         * and `te[11] = -1`, so a camera-space point `(0,0,-d)` lands at
         * `clip.x = -te[8]·d, clip.w = d` — **the optical axis sits at NDC
         * `-te[8]`, not `+te[8]`**. `principalPointOffset` returns the desired
         * NDC position (that function is correct), so adding it puts the axis
         * at its negation. Measured against the real three.js at 1280x720,
         * 63 degrees, a subject at 450 mm:
         *
         *     principal point off centre   as shipped    no shear    fixed
         *     12.8, 7.2 px  (1% of W/H)   -19.6,-11.0   -9.8,-5.5    0, 0  mm
         *     25.6, 14.4 px (2%)          -39.2,-22.1  -19.6,-11.0   0, 0
         *     64,   36 px   (5%)          -98.0,-55.1  -49.0,-27.6   0, 0
         *
         * **It has never fired.** `principalPointOffset` returns null on every
         * shipped path: `cx`/`cy` are set only by `intrinsicsFromFov` (exactly
         * the image centre) and moved only by `applyIntrinsicsDelta` under
         * `mask.pp`, and every `pp` in this tree is false — the one `pp: true`
         * is a Jacobian unit test that never reaches a renderer. So no
         * published number moves. It is worth fixing precisely BECAUSE it is
         * dormant: the day somebody solves the principal point, the frame will
         * be drawn tens of millimetres off along the right axis in the wrong
         * direction, which reads as a solver fault rather than a renderer one.
         *
         * `setViewOffset` rather than the corrected `-=`, because patching
         * `projectionMatrix.elements` is silently wiped by any later
         * `updateProjectionMatrix()` — measured, `te[8]` goes -0.04 to exactly
         * 0. Nothing in the current render path calls it, but `setFocalLength`,
         * the XR paths and the shadow paths all do, and a trap that needs a
         * comment to survive is a trap.
         */
        const dx = intrinsics.cx - intrinsics.width / 2;
        const dy = intrinsics.cy - intrinsics.height / 2;
        camera.setViewOffset(
          intrinsics.width, intrinsics.height, -dx, -dy,
          intrinsics.width, intrinsics.height,
        );
      } else {
        // A previous non-central solve must not stick. `applyIntrinsics` runs
        // more than once per session, and without this a central intrinsics
        // arriving after an off-centre one keeps the old shear.
        camera.clearViewOffset();
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
      // The key rides the head so its shadow frustum only ever has to cover one
      // head-sized box; the screen light aims at the head from the camera, which
      // sits at the origin looking down -Z and never moves. Both targets are
      // scene children, so their world matrices need the refresh.
      headNode.getWorldPosition(headWorld);
      key.target.position.copy(headWorld);
      key.position.copy(headWorld).add(KEY_OFFSET_MM);
      key.target.updateMatrixWorld(true);
      screen.target.position.copy(headWorld);
      screen.position.set(0, 0, 0);
      screen.target.updateMatrixWorld(true);
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

    setOccluder(positions, indices, earRests) {
      // Sets, because the depth occluder and the shadow catcher SHARE one
      // geometry instance on purpose (see below) and disposing per-child would
      // dispose it twice.
      const geometries = new Set<any>();
      const materials = new Set<any>();
      for (const child of [...occluderNode.children]) {
        occluderNode.remove(child);
        if (child.geometry) geometries.add(child.geometry);
        if (child.material) materials.add(child.material);
      }
      for (const g of geometries) g.dispose?.();
      for (const m of materials) m.dispose?.();
      // **The occluder is a HEAD, not a face**, and the difference is 72 mm of
      // temple. MediaPipe's mesh stops at the silhouette — its rearmost vertex
      // on this template is 24.4 mm back — while a temple runs to an ear rest
      // 96 mm back, so most of the arm used to be drawn against nothing.
      // `buildHeadWithEars` lofts the mesh's own 36-vertex boundary loop to an
      // occipital pole and hangs an open dish on each side.
      //
      // **The seat-and-occluder invariant is intact, and this is where to check
      // it.** The loft SHARES the face's vertices and keeps them first, at their
      // own indices, so the face part of this surface is bit-identical to
      // `model.positions` — the same buffer the contact solve seated against.
      // Nothing in front of the rim has changed; the addition is all behind it.
      const head = buildHeadWithEars(positions, indices, earRests);
      occluderShell = head.shell;
      occluderBase = new Float32Array(head.positions);
      const geometry = new THREE.BufferGeometry();
      // Float32 narrowing is the only copy between the seat's surface and the
      // GPU's. Face-space millimetres, unchanged — no flip; the node's matrix
      // carries it (see `SceneHandle.occluderNode`).
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(occluderBase), 3));
      geometry.setIndex(new THREE.BufferAttribute(head.indices.slice(), 1));
      geometry.computeVertexNormals();
      // Depth-only: the universal recipe. Colour writes off, depth writes on,
      // rendered before everything that must be hidden by skin.
      const material = new THREE.MeshBasicMaterial({ colorWrite: false });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = -1;
      occluderNode.add(mesh);

      /**
       * A head-shaped shadow receiver, and the reason a shadow is visible at all.
       *
       * The face on screen is VIDEO, not geometry — the only thing rendered
       * there is the depth-only occluder above, which writes no colour. So a
       * shadow cast onto the face would land on nothing and the whole shadow
       * map would be invisible. `ShadowMaterial` is the answer: transparent
       * everywhere the shadow is not, so the camera feed shows through
       * untouched, and darkening only where the frame blocks the key.
       *
       * It shares the occluder's geometry INSTANCE rather than a copy, and v1
       * records why that is load-bearing: the catcher is depth-tested against
       * the occluder, so an occluder even a tenth of a millimetre in front of it
       * culls the shadow entirely. A copy stays identical until the first time
       * the head takes a measured shape — which is how v1's shadow disappeared
       * the first time it was tried.
       *
       * `depthWrite` off so it cannot occlude anything itself, and it is a
       * child of the same node so the camera-axis bias moves both together.
       */
      const catcherMaterial = new THREE.ShadowMaterial({ opacity: 0.18 });
      catcherMaterial.depthWrite = false;
      const catcher = new THREE.Mesh(geometry, catcherMaterial);
      catcher.receiveShadow = true;
      occluderNode.add(catcher);

      occluderNode.updateMatrixWorld(true);
      occluderMesh = mesh;
    },

    nudgeOccluder(deltaMm) {
      if (!occluderMesh || !occluderBase) return;
      const attr = occluderMesh.geometry.getAttribute('position');
      const a = attr.array as Float32Array;
      // `deltaMm` is dense over the FACE's vertices; the loft's are past its
      // end, which `Math.min` already handled and which is now load-bearing
      // rather than incidental.
      const n = Math.min(a.length, occluderBase.length, deltaMm.length);
      for (let i = 0; i < n; i++) a[i] = occluderBase[i] + deltaMm[i];
      // Past the snap's reach, restore the loft's own values before re-lofting,
      // or a shrinking rim would ratchet the skull inward frame by frame.
      for (let i = n; i < a.length; i++) a[i] = occluderBase[i];

      // **Re-loft, or the seam opens.** The snap moves the 36 rim vertices the
      // skull was lofted from, and the skull SHARES them — so leaving it where
      // it was tears a gap behind the ear at exactly the millimetre scale the
      // snap works at. This is the one thing v1's head.js says the loft cannot
      // survive without.
      if (occluderShell) {
        // `reloftSkull` wants Float64; the buffer is Float32 for the GPU. One
        // scratch array, reused, rather than an allocation per frame — the snap
        // runs several times a second.
        if (!reloftScratch || reloftScratch.length !== a.length) {
          reloftScratch = new Float64Array(a.length);
        }
        reloftScratch.set(a);
        reloftSkull(reloftScratch, occluderShell.ring, occluderShell.faceVertexCount);
        for (let i = occluderShell.faceVertexCount * 3; i < a.length; i++) a[i] = reloftScratch[i];
      }
      attr.needsUpdate = true;
      occluderMesh.geometry.computeBoundingSphere?.();
    },

    render() {
      renderer.render(scene, camera);
    },

    dispose() {
      renderer.dispose?.();
      background?.dispose?.();
      // The environment map is a render target's texture and nothing else owns
      // it. It is the one thing here that survives a frame swap by design — see
      // `disposeFrameObject`, which must NOT touch it — so this is the only
      // place it can be released.
      environmentTexture?.dispose?.();
      scene.environment = null;
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
export function attachFrame(handle: SceneHandle, asset: FrameAsset, object?: any): void {
  attachFrameObject(handle, object ?? createFrameObject(asset));
}

/**
 * Takes the frame off the face, disposing whatever was ours to dispose.
 *
 * `frameNode` is a child of `headNode`, so a frame outlives everything the app
 * clears on a rescan: the model, the tracker, the seat and the calibration
 * field all go, and the previous wearer's glasses stay drawn — now over a face
 * that is being re-measured, at a seat solved for somebody else's nose.
 *
 * Shares `attachFrameObject`'s cache rule rather than repeating it: a loaded
 * glTF group belongs to the app's cache and is only unparented, a parametric
 * group is ours and is destroyed.
 */
export function detachFrame(handle: SceneHandle): void {
  const node = handle.frameNode;
  for (const child of [...node.children]) {
    node.remove(child);
    if (!child.userData?.[CACHED_BY_CALLER]) disposeFrameObject(child);
  }
}

/**
 * The swap-and-dispose half, for geometry that was loaded rather than built.
 *
 * `loadFrameMesh` returns a group already placed by the asset's own
 * `meshToFrame`; this puts it under `frameNode` with the same discipline
 * `attachFrame` has always had. Split out so the dispose rule lives in exactly
 * one place — v1 leaked materials on frame swap and the review remembers it.
 */
export function attachFrameObject(handle: SceneHandle, object: any): void {
  const node = handle.frameNode;
  for (const child of [...node.children]) {
    node.remove(child);
    // **Not everything under this node is ours to destroy.** A parametric frame
    // is built fresh on every fit and must be disposed or it leaks — that is
    // v1's bug. A loaded mesh is CACHED by the app so a wearer flicking between
    // frames does not re-download 4 MB per click, and disposing it would leave
    // the cache holding a group whose geometry and textures are gone: the next
    // attach draws nothing, silently, and only for the frames already visited.
    if (!child.userData?.[CACHED_BY_CALLER]) disposeFrameObject(child);
  }
  node.add(object);
  node.updateMatrixWorld(true);
}

/**
 * Marks a frame object as owned by its loader rather than by the scene.
 *
 * Set it on anything handed to `attachFrameObject` that will be attached again
 * later. See the disposal rule above for what forgetting it costs.
 */
export const CACHED_BY_CALLER = 'arCachedByCaller';
