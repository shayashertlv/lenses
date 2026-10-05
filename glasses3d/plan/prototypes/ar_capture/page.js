// Served by run.mjs as ar/qa/__junction_virtual.js (virtual; nothing is written into the repo).
import * as THREE from 'three';
import {TryOnRenderer} from '../src/render/renderer.ts';
import {registerModelingAutoEyewear} from '../src/eyewear/catalog.ts';
import {registerPinnedGeometry} from '../src/render/continuity.ts';

function detection(canonical, width, height, yaw = 0, pitch = 0, roll = 0, depth = 23) {
  const matrix = new THREE.Matrix4().compose(new THREE.Vector3(0, 0, -depth), new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch * Math.PI / 180, yaw * Math.PI / 180, roll * Math.PI / 180, 'YXZ')), new THREE.Vector3(1, 1, 1));
  const positions = [], point = new THREE.Vector3(); let mean = 0;
  for (let i = 0; i < 468; i++) {point.fromArray(canonical.positions, i * 3).applyMatrix4(matrix); positions.push(point.clone()); mean -= point.z / 468;}
  const vertical = 2 * Math.tan(63 * Math.PI / 360), horizontal = vertical * width / height;
  const landmarks = positions.map(p => ({x: .5 + p.x / (-p.z * horizontal), y: .5 - p.y / (-p.z * vertical), z: (-p.z / mean - 1) / horizontal}));
  for (const [a, b] of [[33, 133], [362, 263]]) for (let i = 0; i < 5; i++) landmarks.push({x: (landmarks[a].x + landmarks[b].x) / 2, y: (landmarks[a].y + landmarks[b].y) / 2, z: (landmarks[a].z + landmarks[b].z) / 2});
  return {matrix: matrix.toArray(), landmarks, inferenceMs: 0};
}
function paint(canvas, kind) {
  const c = canvas.getContext('2d'), {width, height} = canvas;
  if (kind === 'checker') {for (let y = 0; y < height; y += 32) for (let x = 0; x < width; x += 32) {c.fillStyle = (x / 32 + y / 32) % 2 ? '#b4a190' : '#ebe0d2'; c.fillRect(x, y, 32, 32);}}
  else if (kind === 'dark') {c.fillStyle = '#2b2522'; c.fillRect(0, 0, width, height);}
  else {c.fillStyle = '#cba68d'; c.fillRect(0, 0, width, height);}
}
const MAGENTA = new THREE.Color(1, 0, 1), CYAN = new THREE.Color(0, 1, 1);

async function run() {
  const config = await fetch('/__junction/config.json').then(r => r.json());
  const canonical = await fetch('/models/canonical-face.json').then(r => r.json());
  const report = {cases: []}; window.junctionImages = [];
  const width = config.width ?? 1280, height = config.height ?? 854;
  const source = document.createElement('canvas'); source.width = width; source.height = height;
  for (const entry of config.cases) {
    const row = {id: entry.id, status: 'running', renders: []}; report.cases.push(row);
    let instance;
    try {
      let eyewearId = entry.eyewearId;
      if (!eyewearId) {
        const url = `/__junction/models/${entry.id}.glb`;
        registerPinnedGeometry(url, entry.sha256);
        registerModelingAutoEyewear({name: entry.id, assetUrl: url, widthMm: entry.width_mm ?? 145, templeClipLocalZM: entry.templeClipLocalZM ?? -.14});
        eyewearId = 'modeling-auto';
      }
      const canvas = document.createElement('canvas');
      instance = await TryOnRenderer.create(canvas, new AbortController().signal, eyewearId, {sync: false, steady: null});
      window.junctionGpu = instance.gpuRenderer;
      const scene = instance.scene, camera = instance.camera;
      const lensMeshes = instance.lensMeshes;
      row.lensMeshes = lensMeshes.map(m => m.name + ' :: ' + (m.material.name ?? '') + ' transmission=' + m.material.transmission);
      const lensMats = new Set(lensMeshes.map(m => m.material));
      const allEyewear = [];
      instance.eyewearPose.traverse(o => {if (o instanceof THREE.Mesh) allEyewear.push(o);});
      paint(source, config.backgrounds?.[0] ?? 'skin');
      let t = 0; const depth0 = entry.depth ?? config.depth ?? 23;
      for (; t <= 5000; t += 100) if (!instance.pose(source, detection(canonical, width, height, 0, 0, 0, depth0), t)) throw Error('pose rejected');
      row.fit = instance.fitting; if (!instance.fitting.ready) throw Error('fit not ready');
      const variants = config.variants;
      for (const view of config.views) {
        const depth = view.depth ?? depth0;
        const det = detection(canonical, width, height, view.yaw ?? 0, view.pitch ?? 0, view.roll ?? 0, depth);
        for (const bg of config.backgrounds ?? ['skin']) {
          paint(source, bg);
          for (let s = 0; s < 30; s++) if (!instance.pose(source, det, t += 100)) throw Error('view pose rejected');
          for (const variant of variants) {
            const restore = [];
            const swap = (mesh, material) => {restore.push(() => {mesh.material = mesh.userData.__orig; delete mesh.userData.__orig;}); mesh.userData.__orig = mesh.material; mesh.material = material;};
            const setp = (obj, key, value) => {const old = obj[key]; restore.push(() => {obj[key] = old; if (obj.isMaterial) obj.needsUpdate = true;}); obj[key] = value; if (obj.isMaterial) obj.needsUpdate = true;};
            let opts = {hair: false, shadows: true, guard: true};
            if (variant === 'plain') opts = {hair: false, shadows: false, guard: false};
            if (variant === 'lens_hidden') for (const m of lensMats) setp(m, 'visible', false);
            if (variant === 'lens_only') for (const mesh of allEyewear) for (const m of [mesh.material].flat()) if (!lensMats.has(m)) setp(m, 'visible', false);
            if (variant === 'lens_nospec') for (const m of lensMats) {setp(m, 'specularIntensity', 0); setp(m, 'clearcoat', 0); setp(m, 'sheen', 0);}
            if (variant === 'env_off') {setp(scene, 'environment', null); for (const m of lensMats) setp(m, 'envMap', null);}
            if (variant === 'lens_alpha') for (const mesh of lensMeshes) {const o = mesh.material; swap(mesh, new THREE.MeshPhysicalMaterial({color: o.color, roughness: o.roughness, metalness: 0, transmission: 0, transparent: true, opacity: .18, depthWrite: false, side: o.side, toneMapped: false}));}
            if (variant === 'lens_alpha_nospec') for (const mesh of lensMeshes) {const o = mesh.material; swap(mesh, new THREE.MeshBasicMaterial({color: o.color, transparent: true, opacity: .18, depthWrite: false, side: o.side, toneMapped: false}));}
            if (variant === 'lens_mask') for (const mesh of lensMeshes) swap(mesh, new THREE.MeshBasicMaterial({color: MAGENTA, side: mesh.material.side, toneMapped: false}));
            if (variant === 'lens_mask_double') for (const mesh of lensMeshes) swap(mesh, new THREE.MeshBasicMaterial({color: MAGENTA, side: THREE.DoubleSide, toneMapped: false}));
            if (variant === 'lens_xray') for (const mesh of lensMeshes) {swap(mesh, new THREE.MeshBasicMaterial({color: CYAN, side: THREE.DoubleSide, depthTest: false, transparent: true, opacity: .55, toneMapped: false})); setp(mesh, 'renderOrder', 999);}
            if (variant === 'normals') for (const mesh of allEyewear) swap(mesh, new THREE.MeshNormalMaterial({side: [mesh.material].flat()[0].side}));
            if (variant === 'lens_normals') for (const mesh of lensMeshes) swap(mesh, new THREE.MeshNormalMaterial({side: mesh.material.side}));
            if (variant === 'lens_tonemapped') for (const m of lensMats) setp(m, 'toneMapped', true);
            if (variant === 'frame_flat') for (const mesh of allEyewear) for (const m of [mesh.material].flat()) if (!lensMats.has(m) && m.map) {setp(m, 'map', null); setp(m, 'color', new THREE.Color(0x2a1a10));}
            if (variant === 'wire_mask') for (const mesh of allEyewear) if (/eyewire/i.test(mesh.name)) swap(mesh, new THREE.MeshBasicMaterial({color: new THREE.Color(0, 1, 0), side: THREE.DoubleSide, toneMapped: false}));
            if (variant === 'lens_front_only') for (const mesh of lensMeshes) setp(mesh.material, 'side', THREE.FrontSide);
            let pixels, timing;
            try {timing = instance.render(null, opts); pixels = instance.readback();}
            finally {for (const r of restore.reverse()) r();}
            // Screen bboxes of each lens mesh and of named frame meshes (for cropping).
            const boxes = {};
            const project = (mesh) => {
              const pos = mesh.geometry.getAttribute('position'), v = new THREE.Vector3();
              let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
              const step = Math.max(1, Math.floor(pos.count / 4000));
              for (let i = 0; i < pos.count; i += step) {v.fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld).project(camera); const x = (v.x + 1) * pixels.width / 2, y = (1 - v.y) * pixels.height / 2; x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);}
              return [x0, y0, x1, y1].map(Math.round);
            };
            for (const mesh of lensMeshes) boxes[mesh.name] = project(mesh);
            const image = document.createElement('canvas'); image.width = pixels.width; image.height = pixels.height; image.getContext('2d').putImageData(pixels, 0, 0);
            const filename = `${entry.id}__${view.id}__${bg}__${variant}.png`;
            row.renders.push({filename, view, bg, variant, boxes, timing: {guarded: timing.guarded, passes: timing.passes, shadowsApplied: timing.shadowsApplied}});
            window.junctionImages.push({filename, data: image.toDataURL('image/png')});
          }
        }
      }
      row.status = 'ok';
    } catch (error) {row.status = 'failed'; row.error = String(error.stack ?? error);}
    finally {instance?.dispose();}
  }
  return report;
}
window.junction = {run};
