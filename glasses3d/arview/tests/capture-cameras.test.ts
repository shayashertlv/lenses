/** `arview capture <glb> --cameras <cameras.json> --no-face` (qa/capture/cameras.ts, qa/capture/cli.ts and
 *  TryOnRenderer.poseCamera): the explicit-camera dev capture without a face.
 *
 *  - the cameras file is validated before anything starts, every problem named (no silent default);
 *  - the projection and placement handed to the renderer put every asset point exactly where the pinhole camera of the
 *    file says (pixel-centre convention), through three's own matrices;
 *  - the options: --cameras needs --no-face, replaces --poses, refuses what a camera file sets, implies the authored
 *    size and no portrait; the pose-set options are unchanged;
 *  - the renderer (on a recording backend that draws nothing): the eyewear is drawn with no face tracked, at exactly the
 *    requested placement and projection, with the authored arms (no spread, no terminal return, no clip, no overlays) and
 *    no face occluder; part-id works; it needs the authored size; the next face pose restores the virtual camera and the
 *    try-on's arm shape. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Color, Euler, Group, Matrix4, Mesh, PerspectiveCamera, Quaternion, Texture, Vector2, Vector3, Vector4} from 'three';
import type {Object3D, Scene, WebGLRenderTarget} from 'three';
import type {GLTFLoaderPlugin} from 'three/addons/loaders/GLTFLoader.js';
import {TryOnRenderer} from '../src/render/renderer.ts';
import type {RendererOptions} from '../src/render/renderer.ts';
import {resolveModelDefinition} from '../src/eyewear/catalog.ts';
import type {ModelDefinition} from '../src/eyewear/catalog.ts';
import type {GltfSource} from '../src/eyewear/optical-material.ts';
import {loadModel} from '../src/model/load.ts';
import {ModelReportBuilder} from '../src/model/report.ts';
import type {Detection} from '../src/face/protocol.ts';
import {VIRTUAL_CAMERA} from '../src/render/projection.ts';
import {buildGlb} from './glb-fab.ts';
import {publicFile} from './shipped-models.ts';
import {assetToCameraScene, cameraFile, CAMERA_SIZE, CameraSetError, CAMERAS_SCHEMA, METRES_TO_SCENE, parseCameraSet, projectionMatrix,
  projectPixel} from '../qa/capture/cameras.ts';
import {parseCaptureArgs, UsageError, USAGE} from '../qa/capture/cli.ts';

/** A row-major view (asset metres -> camera metres) of a camera at `eye` looking at `target`, +Y up. */
function lookAt(eye: readonly [number, number, number], target: readonly [number, number, number] = [0, 0, 0]): number[] {
  const z = new Vector3(...eye).sub(new Vector3(...target)).normalize();
  const x = new Vector3(0, 1, 0).cross(z).normalize(), y = z.clone().cross(x);
  const e = new Vector3(...eye);
  return [x.x, x.y, x.z, -x.dot(e), y.x, y.y, y.z, -y.dot(e), z.x, z.y, z.z, -z.dot(e), 0, 0, 0, 1];
}
function camera(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {id: 'front', width: 640, height: 480, background: '#F4F3F1', view: lookAt([0.04, 0.09, 0.5]), fx: 900, fy: 880, cx: 330.25, cy: 221.5,
    near: 0.1, far: 2, ...overrides};
}
const set = (...cameras: Record<string, unknown>[]) => ({schema: CAMERAS_SCHEMA, cameras});

test('a valid cameras file parses: values kept, background lower-cased, caller meta kept, file names per variant', () => {
  const parsed = parseCameraSet(set(camera({meta: {photo: 'reference-05.png'}}), camera({id: 'side_p90', view: lookAt([0.5, 0.05, -0.06])})));
  assert.equal(parsed.cameras.length, 2);
  const [front] = parsed.cameras;
  assert.equal(front!.background, '#f4f3f1'); assert.deepEqual(front!.meta, {photo: 'reference-05.png'});
  assert.deepEqual([front!.fx, front!.fy, front!.cx, front!.cy, front!.near, front!.far], [900, 880, 330.25, 221.5, 0.1, 2]);
  assert.ok(Object.isFrozen(parsed.cameras) && Object.isFrozen(front));
  assert.equal(cameraFile('side_p90', 'beauty'), 'side_p90__beauty.png'); assert.equal(cameraFile('side_p90', 'part-id'), 'side_p90__part-id.png');
});

test('a bad cameras file is a CameraSetError naming the camera and the problem, never a silent default', () => {
  const mirrored = lookAt([0, 0, 0.5]); mirrored[0] = -mirrored[0]!; mirrored[1] = -mirrored[1]!; mirrored[2] = -mirrored[2]!;
  const sheared = lookAt([0, 0, 0.5]); sheared[1] = 0.2;
  const cases: [unknown, RegExp][] = [
    [[], /JSON object/],
    [{cameras: [camera()]}, /"schema": "arview.cameras\/1"/],
    [set(), /non-empty "cameras" list/],
    [set(camera({id: 'Front'})), /id must match/],
    [set(camera({id: '../x'})), /id must match/],
    [set(camera(), camera()), /"front" is given twice/],
    [set(camera({width: 1920})), /width must be an integer 64-1280/],
    [set(camera({height: 10.5})), /height must be an integer/],
    [set(camera({background: 'white'})), /background must be "#rrggbb"/],
    [set(camera({view: [1, 0, 0]})), /16 finite numbers/],
    [set(camera({view: [...lookAt([0, 0, 0.5]).slice(0, 15), 2]})), /last row must be \(0, 0, 0, 1\)/],
    [set(camera({view: sheared})), /not a rotation/],
    [set(camera({view: mirrored})), /mirrored .*left and right would swap/],
    [set(camera({fx: 0})), /fx and fy must be positive/],
    [set(camera({fy: 'big'})), /fy must be a finite number/],
    [set(camera({near: 0})), /0 < near < far/],
    [set(camera({near: 1, far: 0.5})), /0 < near < far/],
    [set(camera({near: 1e-7, far: 1})), /far \/ near/],
  ];
  for (const [value, pattern] of cases) {
    assert.throws(() => parseCameraSet(value), (error: unknown) => error instanceof CameraSetError && pattern.test(error.message), JSON.stringify(value).slice(0, 120));
  }
  assert.equal(CAMERA_SIZE.maxWidth, 1280, 'the renderer never draws wider (MAX_RENDER_WIDTH)');
});

test('the placement and projection handed to the renderer put asset points where the pinhole camera says, through three', () => {
  const cams = [camera(), camera({id: 'side', view: lookAt([0.45, 0.08, -0.07], [0, 0, -0.07]), width: 1280, height: 400, fx: 2400, fy: 2410, cx: 700.5, cy: 180}),
    camera({id: 'top', view: lookAt([0.001, 0.6, 0.05], [0, 0, -0.05]), width: 900, height: 900, fx: 1500, fy: 1500, cx: 449.5, cy: 449.5, near: 0.3, far: 1})];
  const points: [number, number, number][] = [[0, 0, 0], [0.07, 0.01, -0.002], [-0.066, 0.005, -0.14], [0.03, -0.025, 0.004], [-0.01, 0.02, -0.08]];
  for (const raw of cams) {
    const spec = parseCameraSet(set(raw)).cameras[0]!;
    const placement = new Matrix4().fromArray(assetToCameraScene(spec)), projection = new Matrix4().fromArray(projectionMatrix(spec));
    for (const point of points) {
      const expected = projectPixel(spec, point)!;
      const clip = new Vector4(...point, 1).applyMatrix4(placement).applyMatrix4(projection);
      // three draws pixel i over [i, i + 1] in NDC-derived (edge) coordinates; its centre is i + 0.5.
      const u = (clip.x / clip.w + 1) * spec.width / 2 - 0.5, v = (1 - clip.y / clip.w) * spec.height / 2 - 0.5;
      assert.ok(Math.abs(u - expected[0]) < 1e-6 && Math.abs(v - expected[1]) < 1e-6, `${spec.id} ${point}: (${u}, ${v}) vs ${expected}`);
      assert.ok(Math.abs(clip.z / clip.w) < 1, `${spec.id} ${point}: inside the clip planes`);
      // the placement is the view in centimetres: a point 1 m along the camera axis is 100 scene units away
      const cameraSpace = new Vector4(...point, 1).applyMatrix4(placement);
      const v4 = spec.view;
      assert.ok(Math.abs(cameraSpace.z - METRES_TO_SCENE * (v4[8]! * point[0] + v4[9]! * point[1] + v4[10]! * point[2] + v4[11]!)) < 1e-9);
    }
  }
  assert.equal(projectPixel(parseCameraSet(set(camera())).cameras[0]!, [0, 0, 0.6]), null, 'a point behind the camera has no pixel');
});

test('--cameras needs --no-face, replaces --poses, refuses what the camera file sets, implies the authored size and no portrait', () => {
  const options = parseCaptureArgs(['m.glb', '--cameras', 'cams.json', '--no-face', '--out', 'o', '--lensenv', '1.5', '--step-timeout-s=30']);
  assert.deepEqual({...options}, {glb: 'm.glb', poses: null, out: 'o', backgrounds: null, width: 1280, height: 854, depthCm: 23, fixedScale: true,
    wornSides: false, clipZM: null, lensEnvIntensity: 1.5, cropUpscale: 1, portrait: false, liveProbe: false, stepTimeoutS: 30, cameras: 'cams.json', noFace: true});
  assert.equal(parseCaptureArgs(['m.glb', '--cameras=c.json', '--no-face', '--out=o', '--fixed-scale', '--no-portrait', '--live-probe']).liveProbe, true);
  const face = parseCaptureArgs(['m.glb', '--poses', 'all', '--out', 'o']);
  assert.equal(face.cameras, null); assert.equal(face.noFace, false); assert.equal(face.poses, 'all'); assert.equal(face.portrait, true);
  const cases: [string[], RegExp][] = [
    [['m.glb', '--cameras', 'c.json', '--out', 'o'], /say so with --no-face/],
    [['m.glb', '--poses', 'default', '--no-face', '--out', 'o'], /--no-face needs --cameras/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--poses', 'all', '--out', 'o'], /--poses cannot be combined with --cameras/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--worn-sides', '--out', 'o'], /--worn-sides cannot be combined with --cameras/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--size', '800x600', '--out', 'o'], /--size cannot be combined/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--backgrounds', 'dark', '--out', 'o'], /--backgrounds cannot be combined/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--clip', '-0.1', '--out', 'o'], /--clip cannot be combined/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--depth-cm', '30', '--out', 'o'], /--depth-cm cannot be combined/],
    [['m.glb', '--cameras', 'c.json', '--no-face'], /--out <dir> is required/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--out', 'o', '--lensenv', '9'], /0.3-4/],
    [['m.glb', '--cameras', 'c.json', '--no-face', '--out', 'o', '--step-timeout-s', '200'], /5-90/],
    [['m.glb', '--cameras', '--no-face', '--out', 'o'], /--cameras needs a value/],
    [['m.glb', '--cameras', 'c.json', '--no-face=yes', '--out', 'o'], /takes no value/],
  ];
  for (const [argv, pattern] of cases) assert.throws(() => parseCaptureArgs(argv), (error: unknown) => error instanceof UsageError && pattern.test(error.message), argv.join(' '));
  for (const option of ['--cameras', '--no-face']) assert.match(USAGE, new RegExp(option));
});

// ----------------------------------------------------------------------------- the renderer on a recording backend
const canonical = JSON.parse(await readFile(publicFile('models/canonical-face.json'), 'utf8')) as {positions: number[]; indices: number[]};
const stubTextures = (): GLTFLoaderPlugin => ({name: 'ArviewTestTextures', loadTexture: async () => new Texture()});
const frame = {width: 640, height: 480} as HTMLCanvasElement;

function detection(distanceCm = 45): Detection {
  const matrix = new Matrix4().compose(new Vector3(0, 0, -distanceCm), new Quaternion().setFromEuler(new Euler(0, 0, 0)), new Vector3(1, 1, 1));
  const view = new PerspectiveCamera(63, 4 / 3, 1, 10_000); view.updateMatrixWorld();
  return {matrix: matrix.toArray(), inferenceMs: 0, landmarks: Array.from({length: 478}, (_, i) => {
    if (i >= 468) return {x: .5, y: .5, z: 0};
    const projected = new Vector3().fromArray(canonical.positions, i * 3).applyMatrix4(matrix).project(view);
    return {x: (projected.x + 1) / 2, y: (1 - projected.y) / 2, z: 0};
  })};
}

interface Draw {background: unknown; meshes: {name: string; visible: boolean; overlay: boolean}[]}
interface Internal {
  scene: Scene; eyewearPose: Group; facePose: Group; camera: PerspectiveCamera;
  surfaceMesh: Mesh | null; templeHeadShell: Mesh | null;
  rearDrop: {spreadM: number; terminalFit: unknown} | null;
  templeClip: {configuration: unknown} | null;
  templeTerminalFit: unknown;
  configure(face: typeof canonical, root: Group, source?: GltfSource | null): void;
}

async function configured(options: RendererOptions) {
  const definition: ModelDefinition = {url: 'http://127.0.0.1:8250/models/model.glb', displayName: 'Test'};
  const resolved = resolveModelDefinition(definition);
  const report = new ModelReportBuilder(resolved.url, resolved.displayName, resolved.warnings);
  const loaded = await loadModel(buildGlb().glb, report, {url: resolved.url, renderer: null, plugins: [stubTextures]});
  const draws: Draw[] = [];
  const state = {target: null as WebGLRenderTarget | null, color: new Color(), alpha: 1};
  const noop = () => {};
  let instance: TryOnRenderer;
  const backend = {
    capabilities: {samples: 0, isWebGL2: true}, extensions: {has: () => true}, autoClear: true, toneMapping: 0, toneMappingExposure: 1, outputColorSpace: '',
    xr: {enabled: false}, state: {buffers: {stencil: {setMask: noop, setClear: noop}, depth: {setMask: noop, getReversed: () => false}}},
    setPixelRatio: noop, setSize: noop, setScissorTest: noop, clear: noop, clearDepth: noop, compile: noop, dispose: noop,
    render(scene: Scene) {
      const internal = instance as unknown as Internal;
      if (scene !== internal.scene || state.target !== null) return;
      const meshes: Draw['meshes'] = [];
      internal.eyewearPose.traverse(object => {
        if (!(object instanceof Mesh)) return;
        let visible = object.visible;
        for (let parent: Object3D | null = object.parent; parent; parent = parent.parent) visible &&= parent.visible;
        meshes.push({name: object.name, visible, overlay: object.userData.templeVisibilityOverlay === true});
      });
      draws.push({background: scene.background, meshes});
    },
    forceContextLoss: noop, getDrawingBufferSize: (out: Vector2) => out.set(640, 480),
    getRenderTarget: () => state.target, setRenderTarget: (value: WebGLRenderTarget | null) => {state.target = value;},
    getActiveCubeFace: () => 0, getActiveMipmapLevel: () => 0,
    getViewport: (out: Vector4) => out, setViewport: noop, getScissor: (out: Vector4) => out, setScissor: noop, getScissorTest: () => false,
    getClearColor: (out: Color) => out.copy(state.color), getClearAlpha: () => state.alpha,
    setClearColor: (value: Color | number, alpha = 1) => {state.color.set(value); state.alpha = alpha;},
  };
  const gl = {SCISSOR_TEST: 1, COLOR_BUFFER_BIT: 2, DEPTH_BUFFER_BIT: 4, STENCIL_BUFFER_BIT: 8, disable: noop, enable: noop, clear: noop, scissor: noop};
  instance = new (TryOnRenderer as unknown as new (backend: unknown, gl: unknown, model: unknown, options: RendererOptions, report?: ModelReportBuilder) => TryOnRenderer)(
    backend, gl, resolved, {sync: false, guard: true, steady: null, ...options}, report);
  const internal = instance as unknown as Internal;
  internal.configure(canonical, loaded.gltf.scene, loaded.source);
  instance.setShadows({enabled: false});
  return {instance, internal, draws};
}

function explicit(raw: Record<string, unknown> = camera()) {
  const spec = parseCameraSet(set(raw)).cameras[0]!;
  return {spec, view: {assetToCamera: assetToCameraScene(spec), projection: projectionMatrix(spec), nearCm: spec.near * METRES_TO_SCENE,
    farCm: spec.far * METRES_TO_SCENE}};
}
const close = (a: readonly number[], b: readonly number[], tolerance = 1e-9) => a.length === b.length && a.every((value, i) => Math.abs(value - b[i]!) <= tolerance * Math.max(1, Math.abs(b[i]!)));

test('poseCamera draws the eyewear with no face, at exactly the requested placement and projection, arms as authored', async t => {
  const {instance, internal, draws} = await configured({fixedScale: true});
  t.after(() => instance.dispose());
  const {spec, view} = explicit();
  instance.poseCamera(frame, view);
  assert.equal(instance.explicitCameraPosed, true);
  assert.ok(close(instance.assetMatrixWorld()!, view.assetToCamera, 1e-9), 'the asset is drawn exactly where the camera file puts it');
  const state = instance.cameraState()!;
  assert.ok(close(state.projection, view.projection, 1e-12)); assert.deepEqual([state.near, state.far, state.width, state.height], [10, 200, 640, 480]);
  assert.deepEqual(state.view, new Matrix4().toArray(), 'the camera stays at the origin (the key light and environment stay camera-relative)');
  // the reported field of view is the explicit projection's (fy 880 px over 480 px), not the virtual camera's 63 deg
  assert.ok(Math.abs(state.verticalFovDegrees - 2 * Math.atan(240 / 880) * 180 / Math.PI) < 1e-9, `${state.verticalFovDegrees}`);
  // projectAssetPoints (pixel edges) = the pinhole projection (pixel centres) + 0.5
  const points: [number, number, number][] = [[0, 0, 0], [0.068, 0.006, -0.12], [-0.05, -0.02, 0]];
  const projected = instance.projectAssetPoints(Float32Array.from(points.flat()))!;
  points.forEach((point, i) => {
    const [u, v] = projectPixel(spec, point)!;
    assert.ok(Math.abs(projected[i * 3]! - (u + 0.5)) < 1e-3 && Math.abs(projected[i * 3 + 1]! - (v + 0.5)) < 1e-3, `${point}`);
  });
  // no face: no occluder, no head shell; the authored arms: no spread, no terminal return, no clip, no overlays
  assert.equal(internal.facePose.visible, false); assert.equal(internal.surfaceMesh?.visible, false); assert.equal(internal.templeHeadShell?.visible, false);
  assert.equal(internal.rearDrop?.spreadM, 0); assert.equal(internal.rearDrop?.terminalFit, null); assert.equal(internal.templeClip?.configuration, null);
  const timing = instance.render(null, {hair: false, shadows: false, guard: false});
  assert.equal(timing.guarded, false); assert.equal(timing.shadowsApplied, false); assert.equal(timing.hairApplied, false);
  assert.match(timing.templeEndState, /^authored \(explicit camera/, 'the report says the arms are drawn as authored, not capped');
  const beauty = draws.at(-1)!;
  assert.ok(beauty.background instanceof Texture, 'the camera frame (the flat background) is the backdrop the lenses transmit');
  const authored = beauty.meshes.filter(mesh => !mesh.overlay);
  assert.equal(authored.length, 7); assert.ok(authored.every(mesh => mesh.visible), 'every part is drawn although no face is tracked');
  assert.ok(beauty.meshes.filter(mesh => mesh.overlay).every(mesh => !mesh.visible), 'no near-arm overlay');
  const partId = instance.render(null, {override: 'part-id'});
  assert.equal(partId.palette?.length, 7);
  const flat = draws.at(-1)!;
  assert.ok(flat.background instanceof Color && (flat.background as Color).getHex() === 0);
  assert.ok(flat.meshes.filter(mesh => !mesh.overlay).every(mesh => mesh.visible), 'part-id draws every part too');
});

test('the next face pose returns to the virtual camera and the try-on arm shape', async t => {
  const {instance, internal, draws} = await configured({fixedScale: true});
  t.after(() => instance.dispose());
  instance.poseCamera(frame, explicit().view);
  assert.equal(instance.pose(frame, detection(), 100), true);
  assert.equal(instance.explicitCameraPosed, false);
  const reference = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, 640 / 480, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
  assert.ok(close(instance.cameraState()!.projection, reference.projectionMatrix.toArray(), 1e-12));
  assert.equal(instance.cameraState()!.verticalFovDegrees, VIRTUAL_CAMERA.verticalFovDegrees);
  assert.deepEqual([internal.camera.near, internal.camera.far], [1, 10_000]);
  assert.equal(internal.rearDrop?.spreadM, 0.018, 'the fixed arm spread is back');
  assert.equal(internal.facePose.visible, true);
  instance.render(null, {guard: false});   // one pass (the guarded pass B skips the lenses)
  assert.ok(draws.at(-1)!.meshes.filter(mesh => !mesh.overlay).every(mesh => mesh.visible));
  // and a lost face draws nothing again (the explicit flag is gone)
  const lost = {...detection(), matrix: null} as unknown as Detection;
  instance.pose(frame, lost, 200); instance.render(null);
  assert.ok(draws.at(-1)!.meshes.every(mesh => !mesh.visible), 'no face, no explicit camera: nothing drawn');
});

test('poseCamera needs the authored size and usable input; nothing changes when it refuses', async t => {
  const auto = await configured({});
  t.after(() => auto.instance.dispose());
  assert.throws(() => auto.instance.poseCamera(frame, explicit().view), /authored size/);
  assert.equal(auto.instance.explicitCameraPosed, false);
  const {instance} = await configured({fixedScale: true});
  t.after(() => instance.dispose());
  const {view} = explicit();
  assert.throws(() => instance.poseCamera(frame, {...view, projection: view.projection.slice(0, 15)}), /two finite column-major 4x4/);
  assert.throws(() => instance.poseCamera(frame, {...view, assetToCamera: view.assetToCamera.map((v, i) => i === 3 ? Number.NaN : v)}), /two finite/);
  assert.throws(() => instance.poseCamera(frame, {...view, nearCm: 50, farCm: 10}), /0 < near < far/);
  assert.throws(() => instance.poseCamera({width: 0, height: 10} as HTMLCanvasElement, view), /frame is empty/);
  assert.throws(() => instance.poseCamera(frame, {...view, assetToCamera: new Array(16).fill(0)}), /singular/);
  assert.equal(instance.explicitCameraPosed, false); assert.equal(instance.assetMatrixWorld(), null);
});
