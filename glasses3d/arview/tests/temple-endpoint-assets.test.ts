import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Matrix4, Mesh, Texture} from 'three';
import type {BufferGeometry, Material} from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {buildTempleContinuityModel, projectTempleContinuity} from '../src/render/continuity.ts';
import type {TempleContinuityModel, ProjectedTemplePath} from '../src/render/continuity.ts';
import {TempleEndpointTracker, TEMPLE_ENDPOINT} from '../src/render/temple-endpoint.ts';
import {GLASSES_OFFSET_CM} from '../src/eyewear/catalog.ts';

/** A strip bounded along the actual projected path; unlike round brush strokes it cannot inflate
 * a physically narrow hair crossing by painting beyond its front/rear limits. */
function narrowBand(model: TempleContinuityModel, paths: ProjectedTemplePath[], frontZM: number, rearZM: number, width: number) {
  const height = width * 9 / 16, ratio = width / 1280, category = new Uint8Array(width * height);
  const point = (zM: number) => {
    const stations = model.sides[0]!, path = paths[0]!, index = stations.findIndex((station, i) => i > 0 && station.zM <= zM),
      a = stations[index - 1]!, b = stations[index]!, t = (a.zM - zM) / (a.zM - b.zM), pa = path.points[index - 1]!, pb = path.points[index]!;
    return {x: (pa.x + (pb.x - pa.x) * t) * ratio, y: (pa.y + (pb.y - pa.y) * t) * ratio,
      radius: (pa.radiusPx + (pb.radiusPx - pa.radiusPx) * t) * ratio};
  };
  const a = point(frontZM), b = point(rearZM), dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy),
    nx = -dy / length, ny = dx / length, halfWidth = Math.max(a.radius, b.radius) + 1;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const ox = x + .5 - a.x, oy = y + .5 - a.y, along = (ox * dx + oy * dy) / length, across = ox * nx + oy * ny;
    if (along >= 0 && along <= length && Math.abs(across) <= halfWidth) category[y * width + x] = 1;
  }
  return {width, height, hairIndex: 1, category};
}

for (const [id, cutoffZM] of [['amber-horizon', -.14], ['tom-ford-clear', -.148]] as const) {
  test(`${id}: an early sideburn patch cuts the actual projected shaft at two distances and mask resolutions`, async () => {
    const bytes = await readFile(new URL(`../public/models/${id}.glb`, import.meta.url));
    const gltf = await new GLTFLoader().register(() => ({name: 'EndpointGeometryOnlyTextures', loadTexture: async () => new Texture()}))
      .parseAsync(new Uint8Array(bytes).buffer, '/models/');
    const geometries = new Set<BufferGeometry>(), materials = new Set<Material>(), textures = new Set<Texture>();
    gltf.scene.traverse(object => {
      if (!(object instanceof Mesh)) return; geometries.add(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        materials.add(material); for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
      }
    });
    try {
      const model = buildTempleContinuityModel(gltf.scene, cutoffZM), maximumZM = cutoffZM + .025;
      for (const depthCm of [35, 60]) for (const maskWidth of [320, 640]) {
        const render = {width: 1280, height: 720}, maskHeight = maskWidth * 9 / 16,
          matrix = new Matrix4().makeRotationY(Math.PI / 3).setPosition(0, 0, -depthCm).toArray();
        const paths = projectTempleContinuity(model, {eyewearMatrix: matrix, offsetCm: GLASSES_OFFSET_CM,
          sourceAspect: 16 / 9, ...render, dropM: 0, spreadM: .018});
        assert.ok(paths);
        const category = new Uint8Array(maskWidth * maskHeight), path = paths[0]!;
        // A continuous camera-space occluder over z=-55..-75 mm. This was entirely excluded by the
        // previous rear-only search. Painting uses real asset stations and real perspective projection.
        for (let segment = 1; segment < model.sides[0]!.length; segment++) {
          const a = model.sides[0]![segment - 1]!, b = model.sides[0]![segment]!, pa = path.points[segment - 1]!, pb = path.points[segment]!;
          for (let step = 0; step <= 40; step++) {
            const t = step / 40, z = a.zM + (b.zM - a.zM) * t;
            if (z > -.055 || z < -.075) continue;
            const x = (pa.x + (pb.x - pa.x) * t) * maskWidth / render.width,
              y = (pa.y + (pb.y - pa.y) * t) * maskHeight / render.height,
              radius = (pa.radiusPx + (pb.radiusPx - pa.radiusPx) * t) * maskWidth / render.width + 1;
            for (let iy = Math.floor(y - radius); iy <= Math.ceil(y + radius); iy++)
              for (let ix = Math.floor(x - radius); ix <= Math.ceil(x + radius); ix++)
                if (ix >= 0 && iy >= 0 && ix < maskWidth && iy < maskHeight && Math.hypot(ix + .5 - x, iy + .5 - y) <= radius)
                  category[iy * maskWidth + ix] = 1;
          }
        }
        const report = new TempleEndpointTracker(model, maximumZM).update({paths, mask: {width: maskWidth,
          height: maskHeight, category, hairIndex: 1}, render, timestampMs: 0, yawDegrees: 60, pitchDegrees: 0});
        assert.equal(report.negativeState, 'tracking', `${depthCm} cm / ${maskWidth}px mask`);
        assert.ok(report.negativeZM > -.082, 'the rear island beyond the sideburn is discarded');
        assert.ok(report.negativeZM + TEMPLE_ENDPOINT.fadeM < -.049, 'the front shaft survives');
      }
      // Regression: these 3/4/8 mm crossings are visible at mask resolution, but the former 8 mm
      // metric-run test rejected them and left the ear-side fragment fully rendered.
      for (const [depthCm, maskWidth, lengthM] of [[35, 640, .003], [35, 640, .004], [35, 320, .008], [60, 640, .008]] as const) {
        const render = {width: 1280, height: 720}, paths = projectTempleContinuity(model, {
          eyewearMatrix: new Matrix4().makeRotationY(Math.PI / 3).setPosition(0, 0, -depthCm).toArray(),
          offsetCm: GLASSES_OFFSET_CM, sourceAspect: 16 / 9, ...render, dropM: 0, spreadM: .018});
        assert.ok(paths);
        const mask = narrowBand(model, paths, -.065, -.065 - lengthM, maskWidth);
        const report = new TempleEndpointTracker(model, maximumZM).update({paths, mask, render,
          timestampMs: 0, yawDegrees: 60, pitchDegrees: 0});
        assert.equal(report.negativeState, 'tracking', `${lengthM * 1000} mm / ${depthCm} cm / ${maskWidth}px`);
        assert.ok(report.negativeZM > -.065 - lengthM - .001 && report.negativeZM < -.064);
        assert.ok(report.negativeFadeM > 0 && report.negativeFadeM < .005);
        assert.ok(report.negativeZM + report.negativeFadeM < -.064, 'the adaptive fade fits under the narrow crossing');
      }
    } finally {for (const resource of [...textures, ...materials, ...geometries]) resource.dispose();}
  });
}
