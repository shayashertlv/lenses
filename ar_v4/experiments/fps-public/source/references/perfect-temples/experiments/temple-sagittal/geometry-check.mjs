import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Matrix4, Mesh, MeshPhysicalMaterial, PerspectiveCamera, Texture, Vector3} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {EYEWEAR} from '../../src/render/eyewear.ts';
import {VIRTUAL_CAMERA} from '../../src/render/projection.ts';
import {createRearDrop, rearDropForPose, REAR_DROP_PARAMETERS} from './rear-drop.ts';

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const destination = path.join(app, '.recovery/temple-rethink-2026-09-08');
const checkpoint = '31df28eb8ca0c698467fd9bfb16f737f9a74e915';
const hash = value => createHash('sha256').update(value).digest('hex');
const camera = new PerspectiveCamera(VIRTUAL_CAMERA.verticalFovDegrees, 1280 / 720, VIRTUAL_CAMERA.nearCm, VIRTUAL_CAMERA.farCm);
camera.updateMatrixWorld();
const poses = [
  {id: 'down20', down: 20, yaw: 0}, {id: 'down40', down: 40, yaw: 0}, {id: 'down50', down: 50, yaw: 0},
  {id: 'up20', down: -20, yaw: 0}, {id: 'down20-yaw-negative30', down: 20, yaw: -30},
  {id: 'down20-yaw-positive30', down: 20, yaw: 30},
];
const poseFor = value => new Matrix4().makeRotationY(value.yaw * Math.PI / 180)
  .multiply(new Matrix4().makeRotationX(value.down * Math.PI / 180)).setPosition(0, 0, -40);
const project = (point, pose, eyewear) => {
  const p = new Vector3(...point.map((value, index) => value * 100 + eyewear.offsetCm[index])).applyMatrix4(pose).project(camera);
  return [(p.x + 1) * 640, (1 - p.y) * 360];
};
const extent = points => ({min: [0, 1].map(axis => Math.min(...points.map(p => p[axis]))),
  max: [0, 1].map(axis => Math.max(...points.map(p => p[axis])))});
const points = attribute => Array.from({length: attribute.count}, (_, i) => [attribute.getX(i), attribute.getY(i), attribute.getZ(i)]);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const modelReports = [], visual = [];

for (const eyewear of Object.values(EYEWEAR)) {
  const relative = 'public' + eyewear.assetUrl, bytes = fs.readFileSync(path.join(app, relative));
  const accepted = execFileSync('git', ['show', `${checkpoint}:ar_v4/${relative}`], {cwd: app, maxBuffer: 16 * 1024 * 1024});
  assert(bytes.equals(accepted), 'The source asset must remain the accepted GLB.');
  const sourceDocument = JSON.parse(bytes.toString('utf8', 20, 20 + bytes.readUInt32LE(12)));
  const sourceNodes = sourceDocument.nodes.map(node => ({name: node.name, mesh: node.mesh,
    translation: node.translation ?? [0, 0, 0], rotation: node.rotation ?? [0, 0, 0, 1], scale: node.scale ?? [1, 1, 1],
    matrix: node.matrix ?? null, identity: !node.matrix && !node.translation && !node.rotation && !node.scale}));
  assert(sourceNodes.every(node => node.identity), 'Current originals must have identity source nodes.');
  const gltf = await new GLTFLoader().register(() => ({name: 'MathOnlyImages', loadTexture: async () => new Texture()}))
    .parseAsync(new Uint8Array(bytes).buffer, '/models/');
  const originals = [], resources = new Set();
  gltf.scene.traverse(mesh => {
    if (!(mesh instanceof Mesh)) return;
    assert(!Array.isArray(mesh.material), 'This audit expects the actual separate single-material primitives.');
    const lens = mesh.material instanceof MeshPhysicalMaterial && mesh.material.transmission > 0;
    originals.push({mesh, geometry: mesh.geometry, positions: points(mesh.geometry.getAttribute('position')),
      normals: points(mesh.geometry.getAttribute('normal')), lens, opaque: !lens && !mesh.material.transparent});
    resources.add(mesh.geometry); resources.add(mesh.material);
    for (const value of Object.values(mesh.material)) if (value instanceof Texture) resources.add(value);
  });
  const controller = createRearDrop(gltf.scene, eyewear.templeClipLocalZM);
  try {
    const diagnostics = controller.diagnostics, start = diagnostics.startZM;
    const posterior = p => Math.abs(p[0]) > .045 && p[2] < start;
    const visiblePosterior = p => posterior(p) && p[2] >= eyewear.templeClipLocalZM;
    const sourceProtected = originals.flatMap(original => original.positions.filter(p => !original.opaque || !posterior(p)));
    const sourceArm = originals.filter(original => original.opaque).flatMap(original => original.positions.filter(visiblePosterior));
    const cases = [];
    for (const value of poses) {
      const pose = poseFor(value), dropM = rearDropForPose(pose.toArray());
      controller.setDrop(dropM);
      let originalBuffersExact = true, originalXAndZExact = true, protectedPositionsExact = true, protectedNormalsExact = true;
      const candidateArm = [], candidates = new Map();
      for (const original of originals) {
        const candidate = points(original.mesh.geometry.getAttribute('position'));
        const normal = points(original.mesh.geometry.getAttribute('normal'));
        candidates.set(original, candidate);
        originalBuffersExact &&= same(points(original.geometry.getAttribute('position')), original.positions)
          && same(points(original.geometry.getAttribute('normal')), original.normals);
        for (let i = 0; i < original.positions.length; i++) {
          const p = original.positions[i];
          originalXAndZExact &&= candidate[i][0] === p[0] && candidate[i][2] === p[2];
          if (!original.opaque || !posterior(p)) {
            protectedPositionsExact &&= same(candidate[i], p);
            protectedNormalsExact &&= same(normal[i], original.normals[i]);
          }
          if (original.opaque && visiblePosterior(p)) candidateArm.push(candidate[i]);
        }
      }
      assert(originalBuffersExact && originalXAndZExact && protectedPositionsExact && protectedNormalsExact);
      const before = extent(sourceArm.map(p => project(p, pose, eyewear)));
      const after = extent(candidateArm.map(p => project(p, pose, eyewear)));
      const curves = [-1, 1].map(side => {
        const samples = [];
        for (let z = eyewear.templeClipLocalZM + .001; z < start; z += .005) {
          const source = [], candidate = [];
          for (const original of originals) if (original.opaque) for (let i = 0; i < original.positions.length; i++) {
            const p = original.positions[i];
            if (visiblePosterior(p) && Math.sign(p[0]) === side && Math.abs(p[2] - z) < .0025) {
              source.push(p); candidate.push(candidates.get(original)[i]);
            }
          }
          if (!source.length) continue;
          const midpoint = values => [0, 1, 2].map(axis =>
            (Math.min(...values.map(p => p[axis])) + Math.max(...values.map(p => p[axis]))) / 2);
          samples.push({source: midpoint(source), candidate: midpoint(candidate)});
        }
        return {side, samples};
      });
      const tipSamples = curves.map(curve => {
        const sample = curve.samples[0], originalPx = project(sample.source, pose, eyewear), candidatePx = project(sample.candidate, pose, eyewear);
        return {side: curve.side, originalLocalM: sample.source, candidateLocalM: sample.candidate,
          originalPixel: originalPx, candidatePixel: candidatePx, deltaPx: candidatePx.map((v, i) => v - originalPx[i])};
      });
      const zeroDropFullGeometryExact = dropM === 0 ? originals.every(original => same(candidates.get(original), original.positions)) : null;
      cases.push({...value, rawMatrix: pose.toArray(), actualDropM: dropM, gateFractionOf20mm: dropM / .02,
        projectedShaftWidthPx: {before: before.max[0] - before.min[0], after: after.max[0] - after.min[0]},
        projectedShaftBounds: {before, after}, tipSamples,
        invariants: {originalBuffersExact, originalXAndZExact, protectedPositionsExact, protectedNormalsExact, zeroDropFullGeometryExact}});
      visual.push({eyewear, value, pose, dropM, curves, sourceProtected});
    }
    controller.setDrop(0);
    assert(originals.every(original => same(points(original.mesh.geometry.getAttribute('position')), original.positions)
      && same(points(original.mesh.geometry.getAttribute('normal')), original.normals)));
    modelReports.push({id: eyewear.id, name: eyewear.name, asset: {path: relative, sha256: hash(bytes), byteExactCheckpoint: true, sourceNodes},
      parameters: diagnostics, protectedBounds: {min: controller.opticalBounds.min.toArray(), max: controller.opticalBounds.max.toArray()},
      exactZeroRestore: true, cases});
  } finally {controller.dispose(); for (const resource of resources) resource.dispose();}
}

const hull = points => {
  const sorted = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const half = array => {const result = []; for (const p of array) {while (result.length > 1 && cross(result.at(-2), result.at(-1), p) <= 0) result.pop(); result.push(p);} return result;};
  return half(sorted).slice(0, -1).concat(half(sorted.reverse()).slice(0, -1));
};
const polyline = (points, style) => `<polyline points="${points.map(p => p.map(v => v.toFixed(2)).join(',')).join(' ')}" ${style}/>`;
let svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1850" height="830" viewBox="0 0 1850 830"><rect width="1850" height="830" fill="#101925"/><style>text{font-family:Arial,sans-serif;fill:#eff4ff}.small{font-size:14px;fill:#b4c4d9}</style><text x="25" y="34" font-size="24">Synthetic authored posterior Y curve — source and candidate trajectories</text><text x="25" y="60" class="small">Blue = accepted source. Pink = authored curve. Gold = undeformed geometry convex outline. Projection 63° / 1280×720 / pose Z −40cm.</text><text x="25" y="82" class="small">No wearer image, detector, skull reconstruction or anatomical improvement claim. Source X/Z, optical front and accepted original-Z caps/fade remain fixed.</text>';
for (const [index, item] of visual.entries()) {
  const row = Math.floor(index / poses.length), column = index % poses.length, x = 20 + column * 305, y = 104 + row * 330;
  const title = item.value.yaw ? `down20° / yaw${item.value.yaw}°` : item.value.down < 0 ? 'up20°' : `down${item.value.down}°`;
  svg += `<rect x="${x}" y="${y}" width="294" height="314" rx="9" fill="#182638" stroke="#31465f"/><text x="${x + 12}" y="${y + 25}" font-size="17">${item.eyewear.name}</text><text x="${x + 12}" y="${y + 47}" class="small">${title} · actual drop ${(item.dropM * 1000).toFixed(1)}mm</text><svg x="${x + 6}" y="${y + 56}" width="282" height="236" viewBox="450 210 380 320">`;
  const outline = hull(item.sourceProtected.map(p => project(p, item.pose, item.eyewear)));
  svg += polyline(outline.concat([outline[0]]), 'fill="none" stroke="#d9bd72" stroke-width="1.5" opacity="0.8"');
  for (const curve of item.curves) {
    svg += polyline(curve.samples.map(p => project(p.source, item.pose, item.eyewear)), 'fill="none" stroke="#5ed3f3" stroke-width="3"');
    svg += polyline(curve.samples.map(p => project(p.candidate, item.pose, item.eyewear)), 'fill="none" stroke="#f191bf" stroke-width="2"');
    const tip = project(curve.samples[0].candidate, item.pose, item.eyewear);
    svg += `<circle cx="${tip[0]}" cy="${tip[1]}" r="3" fill="#f191bf"/>`;
  }
  svg += `</svg><text x="${x + 12}" y="${y + 303}" class="small">X/Z unchanged · cap ${item.eyewear.templeClipLocalZM * 1000}mm</text>`;
}
svg += '<text x="25" y="789" class="small">The projected width can change through perspective despite identical local X. Bounds and line samples simplify the mesh; no depth occlusion is rendered.</text><text x="25" y="812" class="small">The 20mm maximum is an authored preview parameter. Live matched steep poses must determine whether the path improves contact or merely relocates intersection.</text></svg>';
fs.mkdirSync(destination, {recursive: true});
fs.writeFileSync(path.join(destination, 'synthetic-geometry.svg'), svg);
const report = {generatedAt: new Date().toISOString(), acceptedCheckpoint: checkpoint,
  evidenceClass: 'Synthetic authored-curve mathematics and original-asset checks; no wearer evidence.',
  camera: {...VIRTUAL_CAMERA, width: 1280, height: 720, poseTranslationCm: [0, 0, -40], calibrated: false},
  sourceHashes: Object.fromEntries(['experiments/temple-sagittal/rear-drop.ts', 'experiments/temple-sagittal/geometry-check.mjs',
    'src/render/eyewear.ts', 'src/render/temple-clip.ts', 'src/render/projection.ts'].map(relative => [relative, hash(fs.readFileSync(path.join(app, relative)))])),
  curve: {...REAR_DROP_PARAMETERS, expression: 'y = originalY - D*(3u^2 - 2u^3), u=clamp((startZ-originalZ)/(startZ-capZ),0,1)',
    startDefinition: 'Lowest physical-lens original Z minus 15mm; all nondeformed referenced points protected.',
    normal: 'normalize(nx, ny, nz - k*ny)', tangent: 'normalize(tx, ty + k*tz, tz), original handedness',
    k: 'D*6*u*(1-u)/(startZ-capZ)'},
  models: modelReports, svgSha256: hash(svg),
  limitation: 'No true hinge, wearer fit, head collision or realistic motion is established. Original X/Z preservation does not imply identical projected width.'};
fs.writeFileSync(path.join(destination, 'synthetic-geometry.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({destination, models: modelReports.map(model => ({id: model.id, startZM: model.parameters.startZM,
  cases: model.cases.map(value => ({id: value.id, dropMm: value.actualDropM * 1000, width: value.projectedShaftWidthPx,
    tipDeltaPx: value.tipSamples.find(sample => sample.side === 1).deltaPx, invariants: value.invariants}))}))}, null, 2));
