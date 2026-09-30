import {ACESFilmicToneMapping, Box3, Color, DirectionalLight, LoadingManager, Material, Mesh, MeshPhysicalMaterial, MeshStandardMaterial,
  PerspectiveCamera, PMREMGenerator, Scene, Texture, Vector3, WebGLRenderer} from 'three';
import type {Object3D, WebGLRenderTarget} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {parseExternalModel} from '../eyewear/external.ts';
import {classifyAssetMaterials, isOpticalMaterial, isTranslucentFrameMaterial} from '../eyewear/optical-material.ts';
import {readMaterialLensAppearance} from '../eyewear/lens-appearance.ts';
import {createSeeThroughRoom} from '../render/eyewear-reflection.ts';
import {applyStudioMaterials, studioMaterials, tagStudioMaterials} from './materials.ts';
import {createStudioBridge} from './bridge.ts';

const bridge = createStudioBridge('3d'), abort = new AbortController();
const canvas = document.querySelector('canvas')!, status = document.querySelector<HTMLElement>('#status')!;
const errorBox = document.querySelector<HTMLElement>('#error')!;
let renderer: WebGLRenderer | null = null, controls: OrbitControls | null = null, model: Object3D | null = null;
let roomTarget: WebGLRenderTarget | null = null, crystalTarget: WebGLRenderTarget | null = null, disposed = false;
const scene = new Scene(), camera = new PerspectiveCamera(38, 1, .001, 100), center = new Vector3();
let fitDistance = .35;
scene.background = new Color('#e7e7e7');
const resize = () => {
  if (!renderer) return;
  const width = Math.max(1, canvas.clientWidth), height = Math.max(1, canvas.clientHeight);
  renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
};
const observer = new ResizeObserver(resize); observer.observe(canvas);
function fit(view = 'fit') {
  if (!model || !controls) return;
  const size = new Box3().setFromObject(model).getSize(new Vector3());
  fitDistance = Math.max(size.y, size.x / camera.aspect, size.z) / (2 * Math.tan(camera.fov * Math.PI / 360)) * 1.3;
  const direction = ({front: [0, 0, 1], left: [-1, 0, 0], right: [1, 0, 0], back: [0, 0, -1], top: [0, 1, .0001], fit: [.35, .2, 1]} as Record<string, number[]>)[view] ?? [0, 0, 1];
  camera.position.copy(center).add(new Vector3(...direction).normalize().multiplyScalar(fitDistance));
  camera.near = Math.max(.0001, fitDistance / 100); camera.far = fitDistance * 100;
  camera.updateProjectionMatrix(); controls.target.copy(center); controls.update();
}
document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button => button.addEventListener('click', () => fit(button.dataset.view)));
function disposeModel(root: Object3D) {
  const materials = new Set<Material>(), textures = new Set<Texture>();
  root.traverse(o => {if (o instanceof Mesh) {o.geometry.dispose(); for (const material of Array.isArray(o.material) ? o.material : [o.material]) materials.add(material);}});
  for (const material of materials) {for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value); material.dispose();}
  for (const texture of textures) {if (typeof ImageBitmap !== 'undefined' && texture.image instanceof ImageBitmap) texture.image.close(); texture.dispose();}
}
function dispose() {
  if (disposed) return; disposed = true; abort.abort(); bridge?.dispose(); observer.disconnect();
  controls?.dispose(); renderer?.setAnimationLoop(null); if (model) disposeModel(model);
  roomTarget?.dispose(); crystalTarget?.dispose(); scene.clear(); renderer?.dispose(); renderer?.forceContextLoss();
}
window.addEventListener('pagehide', dispose, {once: true});

async function start() {
  const external = parseExternalModel(location.search, location.origin);
  if (!external?.sha256) throw new Error('A local model and its SHA-256 revision are required.');
  document.querySelector('#name')!.textContent = external.name;
  const response = await fetch(external.url, {signal: abort.signal, credentials: 'omit'});
  if (!response.ok) throw new Error(`The model could not be loaded (${response.status}).`);
  const bytes = await response.arrayBuffer();
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(v => v.toString(16).padStart(2, '0')).join('');
  if (digest !== external.sha256) throw new Error('The model does not match its saved revision.');
  if (disposed) return;
  const manager = new LoadingManager();
  manager.setURLModifier(value => {
    if (value.startsWith('blob:') || value.startsWith('data:')) return value;
    const url = new URL(value, external.url);
    if (url.origin !== new URL(external.url).origin) throw new Error('External model texture addresses are not supported.');
    return url.href;
  });
  const gltf = await new GLTFLoader(manager).parseAsync(bytes, new URL('.', external.url).href);
  if (disposed) {disposeModel(gltf.scene); return;}
  tagStudioMaterials(gltf); classifyAssetMaterials(gltf.scene); model = gltf.scene;
  model.traverse(object => {if (object instanceof Mesh) for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
    if (readMaterialLensAppearance(material)) throw new Error('This studio viewer supports native glTF materials; canonical optical descriptors require their dedicated viewer.');
  }});
  renderer = new WebGLRenderer({canvas, antialias: true, alpha: false}); renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.toneMapping = ACESFilmicToneMapping;
  const environment = new RoomEnvironment(), seeThrough = createSeeThroughRoom(), generator = new PMREMGenerator(renderer);
  try {roomTarget = generator.fromScene(environment, .04); crystalTarget = generator.fromScene(seeThrough, 0);}
  finally {generator.dispose(); environment.dispose(); seeThrough.dispose();}
  scene.environment = roomTarget.texture; scene.environmentIntensity = .8;
  const key = new DirectionalLight(0xffffff, 2); key.position.set(-10, 15, 20); scene.add(key, model);
  const setReflection = (value: number) => model!.traverse(object => {
    if (object instanceof Mesh) for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      if (material instanceof MeshStandardMaterial && isOpticalMaterial(material)) {
        if (!material.envMap) {material.envMap = roomTarget!.texture; material.needsUpdate = true;}
        material.envMapIntensity = .8 * value;
      }
    }
  });
  model.traverse(object => {if (object instanceof Mesh) for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
    if (isOpticalMaterial(material) || isTranslucentFrameMaterial(material)) material.toneMapped = false;
    if (material instanceof MeshPhysicalMaterial && isTranslucentFrameMaterial(material)) {material.envMap = crystalTarget!.texture; material.envMapIntensity = .8;}
  }});
  setReflection(external.lensEnvIntensity ?? 1);
  controls = new OrbitControls(camera, canvas); controls.enableDamping = true; controls.minDistance = .01; controls.maxDistance = 3;
  new Box3().setFromObject(model).getCenter(center); resize(); fit();
  bridge?.attach({materials: () => studioMaterials(model!), apply(preview) {
    applyStudioMaterials(model!, preview.edits);
    if (preview.viewer.lens_reflection !== undefined) setReflection(preview.viewer.lens_reflection);
  }});
  status.textContent = 'Drag to rotate · scroll to zoom · right-drag to pan';
  renderer.setAnimationLoop(() => {controls?.update(); renderer?.render(scene, camera);});
}
void start().catch(error => {
  if (disposed) return;
  const message = error instanceof Error ? error.message : String(error);
  status.textContent = 'Preview unavailable'; errorBox.textContent = message; errorBox.hidden = false;
  bridge?.error(message); dispose();
});
