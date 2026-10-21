/** The lens panel uses TryOnRenderer itself, including its loader, materials and fixed room.
 * OrbitControls moves an independent metre-space camera; poseCamera performs the same
 * conversion as arview capture --cameras --no-face. There is no duplicate lighting setup. */
import {Box3, Matrix4, MeshPhysicalMaterial, PerspectiveCamera, Vector3, REVISION} from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {TryOnRenderer, SCENE_ENVIRONMENT_INTENSITY} from '../../arview/src/render/renderer.ts';
import {applyTransmissionToneMapping, isOpticalMaterial} from '../../arview/src/eyewear/optical-material.ts';
import {assetToCameraScene, projectionMatrix, METRES_TO_SCENE} from '../../arview/qa/capture/cameras.ts';
export {coatingSwatchRGB} from './coating.js';

// agent/tools/arview.py LENS_ENV_INTENSITY: the one fixed capture/review policy.
export const LENS_ENV_INTENSITY = 1;
export const RENDERER_POLICY = Object.freeze({three_revision: REVISION, lens_environment: LENS_ENV_INTENSITY,
  scene_environment: SCENE_ENVIRONMENT_INTENSITY, renderer: 'TryOnRenderer', camera_path: 'poseCamera',
  hair: false, shadows: false, guard: false});
const identityKey = 'g3dOwnerLensMaterialIndex';
const physicalFields = Object.freeze({transmission: 'transmission', metallic: 'metalness', roughness: 'roughness',
  ior: 'ior', iridescence: 'iridescence', iridescence_ior: 'iridescenceIOR'});

function indexPlugin(indices) {
  return parser => ({name: 'G3D_owner_lens_identity',
    // Tagged opaque lenses can begin as MeshStandardMaterial. Physical with neutral
    // additional lobes is identical and enables adding transmission/coating live.
    getMaterialType: index => indices.has(index) ? MeshPhysicalMaterial : null,
    afterRoot(gltf) {
      gltf.scene.traverse(object => {
        if (!object.isMesh) return;
        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
          const index = parser.associations.get(material)?.materials;
          if (indices.has(index)) material.userData[identityKey] = index;
        }
      });
    },
  });
}

export class LensColourViewer {
  constructor(host, {materials = [], lensEnvIntensity = LENS_ENV_INTENSITY} = {}) {
    if (lensEnvIntensity !== LENS_ENV_INTENSITY) throw new Error('The lens preview must use the fixed AR lighting.');
    this.host = host;
    this.materialRows = structuredClone(materials);
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'lens-live-canvas';
    this.canvas.setAttribute('aria-label', 'Live glasses preview. Drag to rotate; scroll to zoom.');
    this.canvas.tabIndex = 0;
    Object.assign(this.canvas.style, {width: '100%', height: '100%', display: 'block', touchAction: 'none'});
    host.append(this.canvas);
    this.source = document.createElement('canvas');
    this.background = '#f3f2f0';
    this.camera = new PerspectiveCamera(35, 1, .001, 10);
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = false;
    this.controls.minDistance = .025;
    this.controls.maxDistance = 3;
    this.controls.addEventListener('change', () => {this.explicit = null; this.requestRender();});
    this.abortController = new AbortController();
    this.materials = new Map();
    this.frameRequest = null;
    this.disposed = false;
    this.onKey = event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '-', '='].includes(event.key)) return;
      event.preventDefault();
      const offset = this.camera.position.clone().sub(this.controls.target);
      if (event.key === '+' || event.key === '=') offset.multiplyScalar(.9);
      else if (event.key === '-') offset.multiplyScalar(1.1);
      else {
        const axis = event.key === 'ArrowLeft' || event.key === 'ArrowRight'
          ? new Vector3(0, 1, 0) : new Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
        offset.applyAxisAngle(axis, (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? 1 : -1) * Math.PI / 24);
      }
      this.camera.position.copy(this.controls.target).add(offset);
      this.camera.lookAt(this.controls.target); this.controls.update(); this.explicit = null; this.requestRender();
    };
    this.canvas.addEventListener('keydown', this.onKey);
    this.resizeObserver = new ResizeObserver(() => this.requestRender());
    this.resizeObserver.observe(host);
  }

  async load(modelUrl, materials = this.materialRows) {
    this.materialRows = structuredClone(materials);
    const indices = new Set(materials.map(row => row.material_index));
    this.instance?.dispose();
    this.instance = await TryOnRenderer.create(this.canvas, this.abortController.signal,
      {url: modelUrl, displayName: 'Lens colour preview', lensEnvIntensity: LENS_ENV_INTENSITY},
      {sync: false, steady: null, fixedScale: true, load: {plugins: [indexPlugin(indices)]}});
    this.lastLighting = null;
    this.materials.clear();
    const bounds = new Box3();
    for (const part of this.instance.parts()) {
      if (part.overlay) continue;
      part.mesh.geometry.computeBoundingBox();
      bounds.union(part.mesh.geometry.boundingBox);
      for (const material of Array.isArray(part.mesh.material) ? part.mesh.material : [part.mesh.material]) {
        const index = material.userData[identityKey];
        if (!indices.has(index)) continue;
        if (!isOpticalMaterial(material)) throw new Error('A selected material is not a lens in the AR renderer.');
        if (!(material instanceof MeshPhysicalMaterial)) throw new Error('The lens material cannot be edited live.');
        if (!this.materials.has(index)) this.materials.set(index, new Set());
        this.materials.get(index).add(material);
      }
    }
    if ([...indices].some(index => !this.materials.has(index))) throw new Error('A lens material could not be found in the preview.');
    this.bounds = bounds;
    this.target = bounds.getCenter(new Vector3());
    this.setView('front');
    // Initial load keeps the exact authored material. The UI applies values only on edits.
    this.render();
    return this;
  }

  applyMaterials(rows) {
    for (const {material_index: index, values} of rows) {
      const targets = this.materials.get(index);
      if (!targets) throw new Error('This preview contains no editable lens with that identity.');
      for (const material of targets) {
        const transmissionEnabled = material.transmission > 0, shimmerEnabled = material.iridescence > 0;
        material.color.setRGB(...values.tint);
        for (const [key, property] of Object.entries(physicalFields)) material[property] = values[key];
        material.iridescenceThicknessRange = [values.iridescence_thickness_min, values.iridescence_thickness_max];
        const previousToneMapped = material.toneMapped;
        applyTransmissionToneMapping(material);
        // Uniform-only drags avoid shader recompilation; crossing a feature boundary
        // recompiles exactly as a fresh AR GLB load does.
        if (transmissionEnabled !== (material.transmission > 0) || shimmerEnabled !== (material.iridescence > 0)
          || previousToneMapped !== material.toneMapped) material.needsUpdate = true;
      }
    }
    this.requestRender();
  }

  setView(kind = 'front', side = 'left') {
    if (!this.bounds) return;
    const sign = side === 'right' ? -1 : 1;
    const directions = {front: [0, .04, 1], angled: [.8 * sign, .25, 1], side: [sign, .06, .02],
      top: [0, 1, .001], rear: [0, .04, -1], top_oblique: [.7 * sign, .8, 1],
      rear_three_quarter: [.8 * sign, .25, -1]};
    const direction = new Vector3(...(directions[kind] ?? directions.angled)).normalize();
    this.controls.target.copy(this.target);
    const {aspect} = this.viewportSize();
    this.framingAspect = aspect;
    const distance = this.fitDistance(direction, this.target, aspect);
    this.camera.position.copy(this.target).addScaledVector(direction, distance);
    this.camera.up.set(0, 1, 0); this.camera.lookAt(this.target); this.controls.update();
    this.explicit = null; this.requestRender();
  }

  fitDistance(direction, target, aspect) {
    const fov = this.camera.fov * Math.PI / 180;
    const right = this.camera.up.clone().cross(direction).normalize();
    const up = direction.clone().cross(right).normalize();
    let distance = .025;
    // Fit the actual metre-space bounds in this view, including near-side depth.
    // A sphere around the long arms unnecessarily shrinks the frontal lenses.
    for (const x of [this.bounds.min.x, this.bounds.max.x]) for (const y of [this.bounds.min.y, this.bounds.max.y]) {
      for (const z of [this.bounds.min.z, this.bounds.max.z]) {
        const corner = new Vector3(x, y, z).sub(target), depth = corner.dot(direction);
        distance = Math.max(distance, depth + 1.16 * Math.abs(corner.dot(right)) / (Math.tan(fov / 2) * aspect),
          depth + 1.16 * Math.abs(corner.dot(up)) / Math.tan(fov / 2));
      }
    }
    return distance;
  }

  /** Deterministic proof/capture interface, exactly arview.cameras/1's camera object. */
  setExplicitCamera(camera) {this.explicit = structuredClone(camera); this.requestRender();}

  viewportSize() {
    const width = Math.max(64, Math.min(1280, Math.round(this.host.clientWidth || 800)));
    const aspect = (this.host.clientWidth || 800) / (this.host.clientHeight || 500);
    const height = Math.max(64, Math.round(width / aspect));
    return {width, height, aspect: width / height};
  }

  cameraSpec() {
    if (this.explicit) return structuredClone(this.explicit);
    const {width, height, aspect} = this.viewportSize();
    if (this.bounds && this.framingAspect !== undefined && aspect !== this.framingAspect) {
      // Keep the user's orbit, pan and zoom relative to the fitted model when a
      // responsive layout changes aspect. Re-selecting Front would discard them;
      // retaining only distance crops the model when the panel becomes narrower.
      const offset = this.camera.position.clone().sub(this.controls.target), distance = offset.length();
      const direction = offset.normalize();
      const previousFit = this.fitDistance(direction, this.controls.target, this.framingAspect);
      const nextFit = this.fitDistance(direction, this.controls.target, aspect);
      this.camera.position.copy(this.controls.target).addScaledVector(direction, distance * nextFit / previousFit);
      this.framingAspect = aspect;
      this.controls.update();
    }
    this.camera.aspect = width / height; this.camera.updateProjectionMatrix(); this.camera.updateMatrixWorld();
    const column = this.camera.matrixWorldInverse.elements;
    const view = new Matrix4().fromArray(column).transpose().toArray();
    const focal = height / (2 * Math.tan(this.camera.fov * Math.PI / 360));
    return {id: 'lens_panel', width, height, background: this.background, view, fx: focal, fy: focal,
      cx: width / 2 - .5, cy: height / 2 - .5, near: this.camera.near, far: this.camera.far};
  }

  requestRender() {
    if (this.disposed || this.frameRequest !== null) return;
    this.frameRequest = requestAnimationFrame(() => {this.frameRequest = null; this.render();});
  }

  render() {
    if (!this.instance || this.disposed) return;
    const camera = this.cameraSpec();
    const lighting = JSON.stringify(camera.lighting ?? null);
    if (lighting !== this.lastLighting) {this.instance.setLighting(camera.lighting ?? null); this.lastLighting = lighting;}
    if (this.source.width !== camera.width || this.source.height !== camera.height) {
      this.source.width = camera.width; this.source.height = camera.height;
    }
    const context = this.source.getContext('2d');
    context.fillStyle = camera.background; context.fillRect(0, 0, camera.width, camera.height);
    this.instance.poseCamera(this.source, {assetToCamera: assetToCameraScene(camera), projection: projectionMatrix(camera),
      nearCm: camera.near * METRES_TO_SCENE, farCm: camera.far * METRES_TO_SCENE,
      ...(camera.temple_pose ? {temple_pose: camera.temple_pose} : {})});
    this.instance.render(null, {hair: false, shadows: false, guard: false});
  }

  capture() {
    this.render();
    const {width, height, data} = this.instance.readback();
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
    canvas.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(data), width, height), 0, 0);
    return {data_url: canvas.toDataURL('image/png'), camera: this.cameraSpec(), policy: RENDERER_POLICY,
      report: this.instance.report(), width, height};
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frameRequest !== null) cancelAnimationFrame(this.frameRequest);
    this.resizeObserver.disconnect(); this.controls.dispose();
    this.canvas.removeEventListener('keydown', this.onKey);
    this.abortController.abort(); this.instance?.dispose(); this.canvas.remove();
  }
}

export async function createLensColourViewer(host, {modelUrl, materials = [], lensEnvIntensity = LENS_ENV_INTENSITY}) {
  const viewer = new LensColourViewer(host, {materials, lensEnvIntensity});
  try {return await viewer.load(modelUrl);} catch (error) {viewer.dispose(); throw error;}
}
