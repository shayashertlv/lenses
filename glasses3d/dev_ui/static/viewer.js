import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';

const png = canvas => new Promise((resolve, reject) => canvas.toBlob(
  blob => blob ? resolve(blob) : reject(new Error('The viewer image could not be captured.')), 'image/png'));

// Resolve the authored identity, including primitive children of multi-material nodes.
export function meshIdentity(mesh, parser) {
  const primitive = parser.associations.get(mesh);
  let node = mesh, index;
  while (node && index === undefined) {
    index = parser.associations.get(node)?.nodes;
    node = node.parent;
  }
  if (index === undefined || primitive?.meshes === undefined || primitive?.primitives === undefined) {
    throw new Error('A model surface has no glTF node or primitive identity.');
  }
  return {node_index: index, node_name: parser.json.nodes[index].name ?? '',
    mesh_index: primitive.meshes, primitive_index: primitive.primitives};
}

function materialAt(hit) {
  return Array.isArray(hit.object.material) ? hit.object.material[hit.face.materialIndex] : hit.object.material;
}
function isLens(hit) { const m = materialAt(hit); return m.transmission > 0 || m.opacity < 1; }

function anchorRecord(hit, parser) {
  const normal = (hit.normal ?? hit.face.normal).clone()
    .applyMatrix3(new THREE.Matrix3().getNormalMatrix(hit.object.matrixWorld)).normalize();
  return {kind: 'surface', ...meshIdentity(hit.object, parser), triangle_index: hit.faceIndex,
    barycentric: hit.barycoord.toArray(), position_mm: hit.point.clone().multiplyScalar(1000).toArray(),
    normal: normal.toArray(), is_lens: isLens(hit), behind: null};
}

function cameraRecord(camera, target) {
  return {projection: 'perspective', fov_y_deg: camera.fov, aspect: camera.aspect,
    near_m: camera.near, far_m: camera.far, position_m: camera.position.toArray(),
    quaternion_xyzw: camera.quaternion.toArray(), target_m: target.toArray(),
    matrix_world: camera.matrixWorld.toArray(), projection_matrix: camera.projectionMatrix.toArray()};
}

function distanceToSegment(x, y, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(x - a[0] - t * dx, y - a[1] - t * dy);
}

// Pixel-center coverage, independent of browser canvas antialiasing or device scale.
export function coversPixel(mark, x, y) {
  const points = mark.vertices.map(v => v.xy_px);
  if (mark.kind === 'point') return Math.hypot(x - points[0][0], y - points[0][1]) <= mark.radius_px;
  if (mark.kind === 'polyline') return points.slice(1).some((p, i) => distanceToSegment(x, y, points[i], p) <= mark.stroke_px / 2);
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const a = points[i], b = points[j];
    if (distanceToSegment(x, y, a, b) < 1e-8) return true;
    if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

function ranges(values) {
  const sorted = [...values].sort((a, b) => a - b), result = [];
  for (const n of sorted) {
    const last = result.at(-1);
    if (last && last[1] + 1 === n) last[1] = n; else result.push([n, n]);
  }
  return result;
}

// Flat integer face IDs avoid color management, blending and MSAA edge ambiguity.
function faceMaterial(material, firstId) {
  for (const texture of [material.map, material.alphaMap]) if (texture?.matrixAutoUpdate) texture.updateMatrix();
  return new THREE.RawShaderMaterial({glslVersion: THREE.GLSL3, side: material.side,
    uniforms: {firstId: {value: firstId}, alphaCutoff: {value: material.alphaTest || 0}, opacity: {value: material.opacity},
      colorMap: {value: material.map}, alphaMap: {value: material.alphaMap},
      hasColorMap: {value: !!material.map}, hasAlphaMap: {value: !!material.alphaMap},
      colorTransform: {value: material.map?.matrix ?? new THREE.Matrix3()}, alphaTransform: {value: material.alphaMap?.matrix ?? new THREE.Matrix3()}},
    vertexShader: `precision highp float; precision highp int;
      uniform mat4 projectionMatrix, modelViewMatrix; uniform uint firstId;
      uniform mat3 colorTransform, alphaTransform;
      in vec3 position; in vec2 colorUv, alphaUv; out vec2 vColorUv, vAlphaUv; flat out uint faceId;
      void main() { faceId = firstId + uint(gl_VertexID) / 3u;
        vColorUv = (colorTransform * vec3(colorUv, 1.0)).xy; vAlphaUv = (alphaTransform * vec3(alphaUv, 1.0)).xy;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `precision highp float; precision highp int;
      flat in uint faceId; in vec2 vColorUv, vAlphaUv; out vec4 outColor;
      uniform sampler2D colorMap, alphaMap; uniform bool hasColorMap, hasAlphaMap; uniform float opacity, alphaCutoff;
      void main() { float alpha = opacity;
        if (hasColorMap) alpha *= texture(colorMap, vColorUv).a;
        if (hasAlphaMap) alpha *= texture(alphaMap, vAlphaUv).g;
        if (alpha < alphaCutoff) discard;
        outColor = vec4(float(faceId & 255u), float((faceId >> 8u) & 255u),
        float((faceId >> 16u) & 255u), 255.0) / 255.0; }`,
    blending: THREE.NoBlending, depthTest: true, depthWrite: true, toneMapped: false});
}

function faceBuffer(renderer, root, parser, camera, width, height) {
  const scene = new THREE.Scene(), records = [], resources = [];
  let nextId = 1;
  root.traverseVisible(mesh => {
    if (!mesh.isMesh) return;
    if (mesh.isInstancedMesh) throw new Error('This model uses instances that cannot yet be annotated.');
    const identity = meshIdentity(mesh, parser), src = mesh.geometry;
    const count = src.index?.count ?? src.attributes.position.count;
    if (count % 3 || nextId + count / 3 > 0xffffff) throw new Error('This model has too many triangles to annotate.');
    const positions = new Float32Array(count * 3), v = new THREE.Vector3();
    for (let i = 0; i < count; i++) {
      mesh.getVertexPosition(src.index ? src.index.getX(i) : i, v);
      v.toArray(positions, i * 3);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
    for (const [attribute, texture] of [['colorUv', material.map], ['alphaUv', material.alphaMap]]) {
      const uv = src.getAttribute(texture?.channel ? `uv${texture.channel}` : 'uv'), values = new Float32Array(count * 2);
      if (uv) for (let i = 0; i < count; i++) { const j = src.index ? src.index.getX(i) : i; values[i * 2] = uv.getX(j); values[i * 2 + 1] = uv.getY(j); }
      geometry.setAttribute(attribute, new THREE.BufferAttribute(values, 2));
    }
    geometry.setDrawRange(src.drawRange.start, src.drawRange.count);
    const materials = (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).map(m => faceMaterial(m, nextId));
    const copy = new THREE.Mesh(geometry, Array.isArray(mesh.material) ? materials : materials[0]);
    for (const group of src.groups) geometry.addGroup(group.start, group.count, group.materialIndex);
    copy.matrixAutoUpdate = false; copy.matrix.copy(mesh.matrixWorld); copy.frustumCulled = false;
    scene.add(copy); resources.push(geometry, ...materials);
    records.push({first: nextId, end: nextId + count / 3, identity}); nextId += count / 3;
  });
  const target = new THREE.WebGLRenderTarget(width, height, {samples: 0, depthBuffer: true});
  target.texture.colorSpace = THREE.NoColorSpace;
  const previous = renderer.getRenderTarget(), clear = renderer.getClearColor(new THREE.Color()), alpha = renderer.getClearAlpha();
  const pixels = new Uint8Array(width * height * 4);
  try {
    renderer.setRenderTarget(target); renderer.setClearColor(0x000000, 0); renderer.clear();
    renderer.render(scene, camera); renderer.readRenderTargetPixels(target, 0, 0, width, height, pixels);
  } finally {
    renderer.setRenderTarget(previous); renderer.setClearColor(clear, alpha);
    target.dispose(); resources.forEach(r => r.dispose());
  }
  return {pixels, records};
}

function footprint(buffer, width, height, mark) {
  const points = mark.vertices.map(v => v.xy_px), padding = mark.kind === 'point' ? mark.radius_px : mark.stroke_px / 2;
  const x0 = Math.max(0, Math.floor(Math.min(...points.map(p => p[0])) - padding));
  const x1 = Math.min(width - 1, Math.ceil(Math.max(...points.map(p => p[0])) + padding));
  const y0 = Math.max(0, Math.floor(Math.min(...points.map(p => p[1])) - padding));
  const y1 = Math.min(height - 1, Math.ceil(Math.max(...points.map(p => p[1])) + padding));
  const ids = new Set(); let covered = 0;
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
    if (!coversPixel(mark, x + .5, y + .5)) continue;
    const i = ((height - 1 - y) * width + x) * 4;
    const id = buffer.pixels[i] + buffer.pixels[i + 1] * 256 + buffer.pixels[i + 2] * 65536;
    if (id) { ids.add(id); covered++; }
  }
  const faces = [];
  for (const r of buffer.records) {
    const triangles = ranges([...ids].filter(id => id >= r.first && id < r.end).map(id => id - r.first));
    if (triangles.length) { const {node_name, ...identity} = r.identity; faces.push({...identity, triangles}); }
  }
  return {pixels: covered, faces};
}

// Fit each box corner in camera space; a bounding sphere wastes most of the
// width for a wide, shallow frame. Leave 4% of the canvas at each limiting edge.
export function fitCameraToBounds(camera, bounds) {
  const center = bounds.getCenter(new THREE.Vector3());
  const inverseRotation = camera.quaternion.clone().invert();
  const tanY = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) / camera.zoom;
  const tanX = tanY * camera.aspect, fill = .92;
  let distance = 0;
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        const corner = new THREE.Vector3(x, y, z).sub(center).applyQuaternion(inverseRotation);
        distance = Math.max(distance, corner.z + Math.abs(corner.x) / (tanX * fill),
          corner.z + Math.abs(corner.y) / (tanY * fill), corner.z + camera.near * 2);
      }
    }
  }
  camera.position.copy(center).add(new THREE.Vector3(0, 0, distance).applyQuaternion(camera.quaternion));
  camera.updateMatrixWorld(true);
  return {center, distance};
}

export async function createViewer(container, {modelUrl, source} = {}) {
  const renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0xeeeeec); renderer.toneMapping = THREE.ACESFilmicToneMapping;
  container.append(renderer.domElement); renderer.domElement.setAttribute('aria-label', '3D model');
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(35, 1, .001, 10);
  const controls = new OrbitControls(camera, renderer.domElement);
  const pmrem = new THREE.PMREMGenerator(renderer), room = new RoomEnvironment();
  const environment = pmrem.fromScene(room, .04); scene.environment = environment.texture;
  room.dispose(); pmrem.dispose();
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => {
    if (url.startsWith('blob:') || url.startsWith('data:')) return url;
    const parsed = new URL(url, location.href);
    if (parsed.origin !== location.origin) throw new Error('This model refers to an external resource.');
    return parsed.href;
  });
  let gltf;
  try { gltf = await new GLTFLoader(manager).loadAsync(modelUrl); }
  catch (error) { controls.dispose(); environment.dispose(); renderer.dispose(); renderer.domElement.remove(); throw error; }
  const root = gltf.scene; scene.add(root); root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(root), center = bounds.getCenter(new THREE.Vector3());
  if (bounds.isEmpty()) { controls.dispose(); environment.dispose(); renderer.dispose(); renderer.domElement.remove(); throw new Error('This model contains no visible geometry.'); }
  const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, .001);
  let capturing = false, disposed = false;
  function resize() {
    if (capturing || disposed) return;
    const rect = container.getBoundingClientRect(), width = Math.max(1, rect.width), height = Math.max(1, rect.height || 480);
    renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix();
  }
  resize();
  camera.near = Math.max(.00001, radius / 1000);
  const {distance} = fitCameraToBounds(camera, bounds);
  camera.far = Math.max(10, distance + radius * 100);
  controls.target.copy(center); controls.minDistance = radius * .05; controls.maxDistance = radius * 50;
  camera.updateProjectionMatrix(); controls.update();
  renderer.setAnimationLoop(() => { if (!capturing && !disposed) { controls.update(); renderer.render(scene, camera); } });
  const observer = new ResizeObserver(resize); observer.observe(container);

  async function capture() {
    if (capturing || disposed) throw new Error('The viewer is not ready for another capture.');
    capturing = true; const enabled = controls.enabled; controls.enabled = false;
    const frozen = camera.clone(); frozen.updateMatrixWorld(true);
    const target = controls.target.clone(), cssSize = renderer.getSize(new THREE.Vector2()), dpr = renderer.getPixelRatio();
    const width = frozen.aspect >= 1 ? 1600 : Math.max(1, Math.round(1600 * frozen.aspect));
    const height = frozen.aspect >= 1 ? Math.max(1, Math.round(1600 / frozen.aspect)) : 1600;
    frozen.aspect = width / height; frozen.updateProjectionMatrix();
    try {
      renderer.setPixelRatio(1); renderer.setSize(width, height, false);
      renderer.render(scene, frozen);
      const blob = await png(renderer.domElement);
      const buffer = faceBuffer(renderer, root, gltf.parser, frozen, width, height);
      const ray = new THREE.Raycaster(); ray.near = frozen.near; ray.far = frozen.far;
      return {blob, width, height, camera: cameraRecord(frozen, target),
        anchorAt(xy) {
          ray.setFromCamera(new THREE.Vector2(2 * xy[0] / width - 1, 1 - 2 * xy[1] / height), frozen);
          const hits = ray.intersectObject(root, true).filter(h => {
            if (!h.object.isMesh || !h.face) return false;
            for (let object = h.object; object; object = object.parent) if (!object.visible) return false;
            return true;
          });
          if (!hits.length) return {kind: 'background'};
          const anchor = anchorRecord(hits[0], gltf.parser);
          if (anchor.is_lens) { const behind = hits.slice(1).find(h => !isLens(h)); if (behind) anchor.behind = anchorRecord(behind, gltf.parser); }
          return anchor;
        },
        footprint: mark => footprint(buffer, width, height, mark),
        dispose() { buffer.pixels = new Uint8Array(); buffer.records = []; }};
    } finally {
      renderer.setPixelRatio(dpr); renderer.setSize(cssSize.x, cssSize.y, false);
      controls.enabled = enabled; capturing = false; resize(); renderer.render(scene, camera);
    }
  }
  return {renderer, camera, controls, scene, root, gltf, source, capture,
    dispose() {
      disposed = true; observer.disconnect(); controls.dispose(); renderer.setAnimationLoop(null);
      root.traverse(o => { o.geometry?.dispose(); for (const m of o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : []) {
        for (const value of Object.values(m)) if (value?.isTexture) value.dispose(); m.dispose();
      }});
      environment.dispose(); renderer.dispose(); renderer.domElement.remove();
    }};
}
