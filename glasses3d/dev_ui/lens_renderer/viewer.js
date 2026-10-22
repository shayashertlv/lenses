/** The lens panel uses TryOnRenderer itself, including its loader, materials and fixed room.
 * OrbitControls moves an independent metre-space camera; poseCamera performs the same
 * conversion as arview capture --cameras --no-face. There is no duplicate lighting setup. */
import {Box3, Matrix4, Mesh, MeshPhysicalMaterial, PerspectiveCamera, Raycaster, Vector2, Vector3, REVISION} from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {TryOnRenderer, SCENE_ENVIRONMENT_INTENSITY} from '../../arview/src/render/renderer.ts';
import {applyTransmissionToneMapping} from '../../arview/src/eyewear/optical-material.ts';
import {assetToCameraScene, projectionMatrix, METRES_TO_SCENE} from '../../arview/qa/capture/cameras.ts';
export {coatingSwatchRGB} from './coating.js';

// agent/tools/arview.py LENS_ENV_INTENSITY: the one fixed capture/review policy.
export const LENS_ENV_INTENSITY = 1;
export const RENDERER_POLICY = Object.freeze({three_revision: REVISION, lens_environment: LENS_ENV_INTENSITY,
  scene_environment: SCENE_ENVIRONMENT_INTENSITY, renderer: 'TryOnRenderer', camera_path: 'poseCamera',
  hair: false, shadows: false, guard: false});
const identityKey = 'g3dOwnerLensMaterialIndex';
const primitiveKey = 'g3dOwnerPrimitiveIdentity';
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
        const association = parser.associations.get(object);
        if (association?.meshes !== undefined) object.userData[primitiveKey] = {
          mesh_index: association.meshes, primitive_index: association.primitives ?? 0,
        };
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

  async load(modelUrl, materials = this.materialRows, {preserveCamera = false} = {}) {
    const previous = preserveCamera && this.instance ? {camera: this.camera.clone(), target: this.controls.target.clone(), explicit: this.explicit, framingAspect: this.framingAspect} : null;
    this.materialRows = structuredClone(materials);
    const indices = new Set(materials.map(row => row.material_index));
    // The production renderer deliberately loses its WebGL context on dispose.
    // A reload therefore needs a fresh canvas; keep the current frame visible
    // until the replacement is fully loaded, then release the old context.
    const previousInstance = this.instance, previousCanvas = this.canvas;
    const nextCanvas = previousInstance ? previousCanvas.cloneNode(false) : previousCanvas;
    const nextInstance = await TryOnRenderer.create(nextCanvas, this.abortController.signal,
      {url: modelUrl, displayName: 'Lens colour preview', lensEnvIntensity: LENS_ENV_INTENSITY},
      {sync: false, steady: null, fixedScale: true, load: {plugins: [indexPlugin(indices)]}});
    if (previousInstance) {
      this.controls.disconnect(); previousCanvas.removeEventListener('keydown', this.onKey);
      previousCanvas.replaceWith(nextCanvas); this.canvas = nextCanvas;
      this.controls.connect(nextCanvas); nextCanvas.addEventListener('keydown', this.onKey);
      previousInstance.dispose();
    }
    this.instance = nextInstance;
    this.lastLighting = null;
    this.materials.clear();
    this.authoredPositions = new Map();
    this.radialSurfaceData = new Map();
    const bounds = new Box3();
    for (const part of this.instance.parts()) {
      if (part.overlay) continue;
      // Photo fitting opens the temples by changing their position attributes.
      // Keep the immutable authored points so a hit can return to the native
      // scene using its same triangle and barycentric coordinates.
      this.authoredPositions.set(part.mesh, part.mesh.geometry.getAttribute('position').clone());
      part.mesh.geometry.computeBoundingBox();
      bounds.union(part.mesh.geometry.boundingBox);
      for (const material of Array.isArray(part.mesh.material) ? part.mesh.material : [part.mesh.material]) {
        const index = material.userData[identityKey];
        if (!indices.has(index)) continue;
        if (!(material instanceof MeshPhysicalMaterial)) throw new Error('The lens material cannot be edited live.');
        if (!this.materials.has(index)) this.materials.set(index, new Set());
        this.materials.get(index).add(material);
      }
    }
    if ([...indices].some(index => !this.materials.has(index))) throw new Error('A lens material could not be found in the preview.');
    this.bounds = bounds;
    this.target = bounds.getCenter(new Vector3());
    this.setView('front');
    if (previous) {
      this.camera.copy(previous.camera); this.controls.target.copy(previous.target); this.controls.update();
      this.explicit = previous.explicit; this.framingAspect = previous.framingAspect;
    }
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

  /** Pick canonical geometry through the exact camera used for the AR draw. */
  projectionCamera(spec = this.cameraSpec()) {
    const camera = this.camera.clone();
    camera.matrixWorldInverse.fromArray(spec.view).transpose();
    camera.matrixWorld.copy(camera.matrixWorldInverse).invert();
    camera.matrixWorld.decompose(camera.position, camera.quaternion, camera.scale);
    camera.projectionMatrix.fromArray(projectionMatrix(spec));
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    return camera;
  }

  pick(normalized) {
    if (!this.instance) return null;
    const spec = this.cameraSpec(), camera = this.projectionCamera(spec);
    const ray = new Raycaster(); ray.setFromCamera(new Vector2(normalized[0] * 2 - 1, 1 - normalized[1] * 2), camera);
    const sources = new Map();
    const meshes = this.instance.parts().filter(part => !part.overlay).map(part => {
      const mesh = new Mesh(part.mesh.geometry, part.mesh.material); mesh.updateMatrixWorld(); sources.set(mesh, part.mesh); return mesh;
    });
    const hit = ray.intersectObjects(meshes, false)[0];
    if (!hit) return null;
    const material = Array.isArray(hit.object.material) ? hit.object.material[hit.face.materialIndex] : hit.object.material;
    const positions = this.authoredPositions.get(sources.get(hit.object));
    const vertices = [hit.face.a, hit.face.b, hit.face.c].map(index => new Vector3().fromBufferAttribute(positions, index));
    const canonical = vertices.reduce((point, vertex, i) => point.addScaledVector(vertex, hit.barycoord.getComponent(i)), new Vector3());
    const normal = vertices[1].clone().sub(vertices[0]).cross(vertices[2].clone().sub(vertices[0])).normalize();
    if (hit.face.normal.dot(ray.ray.direction) > 0) normal.negate();
    return {material_index: material.userData[identityKey], ...sources.get(hit.object).userData[primitiveKey],
      vertex_indices: [hit.face.a, hit.face.b, hit.face.c], normalized, position_mm: canonical.multiplyScalar(1000).toArray(),
      surface_normal: normal.toArray(), rendered_position_mm: hit.point.clone().multiplyScalar(1000).toArray(), camera: spec};
  }

  /** Place the draggable centre on its actual component, using the same canonical
   * coordinates and isotropic projection metadata as the saved texture. */
  radialCentres(row, gradient) {
    if (!this.instance || !row?.radial_projection || gradient?.direction !== 'around_point') return [];
    const camera = this.projectionCamera(), centres = new Map();
    for (const surface of row.radial_projection.surfaces) {
      const key = surface.lens_id !== undefined ? String(surface.lens_id) : row.radial_projection.mode === 'single' ? 'shared'
        : JSON.stringify([surface.centre, surface.half_extent, surface.mirror_x]);
      const part = this.instance.parts().find(part => !part.overlay
        && part.mesh.userData[primitiveKey]?.mesh_index === surface.mesh_index
        && part.mesh.userData[primitiveKey]?.primitive_index === surface.primitive_index);
      if (!part) continue;
      const positions = this.authoredPositions.get(part.mesh);
      const cacheKey = `${surface.mesh_index}:${surface.primitive_index}:${surface.component}`;
      let data = this.radialSurfaceData.get(cacheKey);
      if (!data) {
        const allowed = index => surface.vertex_ranges.some(([low, high]) => index >= low && index <= high);
        let z = 0, count = 0;
        for (const [low, high] of surface.vertex_ranges) for (let index = low; index <= high; index++) {z += positions.getZ(index); count++;}
        const indices = part.mesh.geometry.index, triangles = [];
        for (let i = 0; i + 2 < (indices?.count ?? positions.count); i += 3) {
          const ids = [0, 1, 2].map(j => indices ? indices.getX(i + j) : i + j);
          if (allowed(ids[0])) triangles.push(ids.map(index => [positions.getX(index), positions.getY(index), positions.getZ(index)]));
        }
        data = {z: z / (count || 1), triangles}; this.radialSurfaceData.set(cacheKey, data);
      }
      const x = surface.centre[0] + gradient.centre[0] * surface.half_extent[0] * surface.mirror_x;
      const y = surface.centre[1] + gradient.centre[1] * surface.half_extent[1];
      let point = null;
      // At a projected triangle interior interpolate its authored depth. The
      // outside-extent centre remains movable by the sliders at mean depth.
      for (const [a, b, c] of data.triangles) {
        const area = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
        if (Math.abs(area) < 1e-15) continue;
        const u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / area;
        const v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / area;
        if (u >= -1e-6 && v >= -1e-6 && u + v <= 1 + 1e-6) {
          const candidate = new Vector3(x, y, u * a[2] + v * b[2] + (1 - u - v) * c[2]).project(camera);
          if (!point || candidate.z < point.z) point = candidate;
        }
      }
      point ??= new Vector3(x, y, data.z).project(camera);
      if (!centres.has(key) || point.z < centres.get(key).depth) centres.set(key, {key, surface, depth: point.z,
        normalized: [(point.x + 1) / 2, (1 - point.y) / 2], visible: point.z >= -1 && point.z <= 1});
    }
    return [...centres.values()];
  }

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
    this.host.dispatchEvent(new Event('lens-render'));
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
