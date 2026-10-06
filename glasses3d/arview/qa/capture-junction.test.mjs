/** arview capture: lens-brow junction slicing on synthetic fixtures (qa/capture/junction.ts). Metres in, millimetres out. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {boxCorners, clipSegment, cropRect, CROP_MIN, CROP_PAD_PX, frontTriangles, junctionStation, lensDecals, lensTopVsBrow, nearSide, pointTriangleDistanceSq,
  sliceAtX, soupArea} from './capture/junction.ts';

const MM = 0.001;
/** A closed axis-aligned box as a triangle soup (12 triangles), millimetre bounds. */
function box([x0, x1], [y0, y1], [z0, z1]) {
  const p = (x, y, z) => [x * MM, y * MM, z * MM];
  const v = [p(x0, y0, z0), p(x1, y0, z0), p(x1, y1, z0), p(x0, y1, z0), p(x0, y0, z1), p(x1, y0, z1), p(x1, y1, z1), p(x0, y1, z1)];
  const faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]];
  return faces.flatMap(face => face.flatMap(index => v[index]));
}
const mirror = soup => soup.map((value, i) => i % 3 === 0 ? -value : value);
const both = soup => [...soup, ...mirror(soup)];
const close = (actual, expected, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} vs ${expected}`);

// A lens 10..60 mm wide, top at y = 8 mm, 2 mm thick; mirrored to the left.
const LENS = both(box([10, 60], [-15, 8], [-1, 1]));

test('sliceAtX: a plane through a box gives its outline; through a vertex, each crossing once', () => {
  const segments = sliceAtX(box([0, 10], [0, 4], [0, 2]), 5 * MM);
  assert.equal(segments.length % 4, 0);
  const ys = [], zs = [];
  for (let i = 0; i < segments.length; i += 2) {ys.push(segments[i]); zs.push(segments[i + 1]);}
  close(Math.min(...ys), 0); close(Math.max(...ys), 4 * MM); close(Math.min(...zs), 0); close(Math.max(...zs), 2 * MM);
  // A plane through one vertex that splits the opposite edge: one segment, from the vertex to the edge's midpoint.
  const straddling = [0, 0, 0, -1, 1, 0, 1, 1, 0];
  const cut = sliceAtX(straddling, 0);
  assert.equal(cut.length, 4);
  assert.deepEqual([cut[0], cut[2]].sort(), [0, 1]);
  // Touching the plane at one vertex from one side only is a point contact: no segment, never a degenerate one.
  assert.equal(sliceAtX([0, 0, 0, 1, 1, 0, 1, -1, 0], 0).length, 0);
  assert.equal(sliceAtX(straddling, 2).length, 0);
});

test('clipSegment: the window y >= floor, |z - zc| < half', () => {
  assert.deepEqual(clipSegment(0, 0, 2, 0, 1, -1, 1), [1, 0, 2, 0]);
  assert.equal(clipSegment(0, 0, 0.5, 0, 1, -1, 1), null);
  assert.equal(clipSegment(2, 5, 3, 6, 1, -1, 1), null);
  const clipped = clipSegment(2, -3, 2, 3, 1, -1, 1);
  assert.deepEqual(clipped.map(value => Math.round(value * 1e9) / 1e9), [2, -1, 2, 1]);
});

test('a seated lens: every station reads +0.5 mm under the brow, with the box, the sides and the per-part readings', () => {
  const brow = both(box([5, 65], [7.5, 14], [-3, 3]));
  const result = lensTopVsBrow(LENS, [{name: 'Brow', triangles: brow}]);
  assert.deepEqual(result.warnings, []);
  assert.deepEqual(result.sides.map(side => [side.side, side.x_sign]), [['left', -1], ['right', 1]]);
  for (const side of result.sides) {
    assert.equal(side.stations.length, 24);
    assert.deepEqual(side.lens_x_range_mm, side.side === 'left' ? [-60, -10] : [10, 60]);
    // 8 % trimmed from each end of the 50 mm range: stations from 14 to 56 mm.
    close(Math.abs(side.stations.at(side.side === 'left' ? -1 : 0).x_mm), 14, 1e-3);
    close(Math.abs(side.stations.at(side.side === 'left' ? 0 : -1).x_mm), 56, 1e-3);
    for (const station of side.stations) {
      close(station.lens_top_y_mm, 8, 1e-6); close(station.brow_bottom_y_mm, 7.5, 1e-6); close(station.overlap_mm, 0.5, 1e-6);
      assert.equal(station.frame_above, true); assert.equal(station.floor, false); assert.equal(station.bottom_part, 'Brow');
    }
    assert.equal(side.summary.seated, 24); assert.equal(side.summary.gap, 0); assert.equal(side.summary.overlap_median_mm, 0.5);
    assert.deepEqual(Object.keys(side.summary.by_part), ['Brow']); assert.equal(side.summary.by_part.Brow.lowest_at, 24);
    assert.equal(side.basis, 'frame_above');
    const [x0, x1] = side.side === 'left' ? [-56 - 1.5, -14 + 1.5] : [14 - 1.5, 56 + 1.5];
    close(side.box_m.min[0] / MM, x0, 1e-6); close(side.box_m.max[0] / MM, x1, 1e-6);
    close(side.box_m.min[1] / MM, 8 - 4, 1e-6); close(side.box_m.max[1] / MM, 8 + 5, 1e-6);
  }
});

test('a gap: the brow 0.6 mm above the lens top reads -0.6 mm; a single soup works as one "frame" part', () => {
  const result = lensTopVsBrow(LENS, both(box([5, 65], [8.6, 14], [-3, 3])));
  for (const side of result.sides) {
    assert.equal(side.summary.gap, 24); assert.equal(side.summary.seated, 0);
    close(side.summary.overlap_min_mm, -0.6, 1e-6); close(side.summary.overlap_max_mm, -0.6, 1e-6);
    close(side.box_m.max[1] / MM, 8.6 + 5, 1e-6);
    assert.deepEqual(Object.keys(side.summary.by_part), ['frame']);
  }
});

test('a browline: the eyewire groove is the lowest point, the acetate brow\'s own gap is read per part', () => {
  const eyewire = both(box([9, 61], [7.6, 8.6], [-1.2, 1.2]));   // holds the lens top 0.4 mm deep
  const brow = both(box([5, 65], [8.7, 14], [-3, 3]));          // 0.7 mm above the lens top
  const station = junctionStation(LENS, [{name: 'Brow', triangles: brow}, {name: 'Eyewire', triangles: eyewire}], 30 * MM);
  close(station.overlap_mm, 0.4, 1e-6); assert.equal(station.bottom_part, 'Eyewire');
  assert.deepEqual(station.parts.map(part => part.name), ['Eyewire', 'Brow']);
  close(station.parts[1].overlap_mm, -0.7, 1e-6);
  const right = lensTopVsBrow(LENS, [{name: 'Brow', triangles: brow}, {name: 'Eyewire', triangles: eyewire}]).sides[1];
  close(right.summary.by_part.Brow.overlap_median_mm, -0.7, 1e-6); close(right.summary.by_part.Eyewire.overlap_median_mm, 0.4, 1e-6);
  assert.equal(right.summary.by_part.Eyewire.lowest_at, 24); assert.equal(right.summary.by_part.Brow.lowest_at, 0);
});

test('pointTriangleDistanceSq: inside the face, past an edge, past a corner; soupArea', () => {
  const tri = [0, 0, 0, 1, 0, 0, 0, 1, 0];
  close(pointTriangleDistanceSq([0.2, 0.2, 0.5], tri, 0), 0.25);          // above the face
  close(pointTriangleDistanceSq([0.5, -1, 0], tri, 0), 1);                // past the edge y = 0
  close(pointTriangleDistanceSq([2, 0, 0], tri, 0), 1);                   // past the corner (1, 0, 0)
  close(pointTriangleDistanceSq([1, 1, 0], tri, 0), 0.5);                 // past the hypotenuse
  close(pointTriangleDistanceSq([-1, -1, 0], [9, 9, 9, ...tri], 3), 2);   // a triangle at an offset in the soup
  close(soupArea(box([0, 2], [0, 3], [0, 4])), 2 * (6 + 8 + 12) * MM * MM, 1e-12);
});

test('a decal riding on the lens (r9\'s "Lens inscription") is not brow: left out, listed and warned', () => {
  const brow = both(box([5, 65], [7.5, 14], [-3, 3]));                  // seated 0.5 mm everywhere
  const decal = box([40, 52], [6.5, 7.9], [1, 1.05]);                    // printed on the right lens's front face, 1.5 mm below its top
  const withDecal = lensTopVsBrow(LENS, [{name: 'Brow', triangles: brow}, {name: 'Lens inscription', triangles: decal}]);
  assert.deepEqual(withDecal.lens_decals.map(item => item.name), ['Lens inscription']);
  const [found] = withDecal.lens_decals;
  close(found.max_lens_distance_mm, 0.05, 1e-6); assert.equal(found.triangles, 12); assert.ok(found.area_fraction < 0.02, `${found.area_fraction}`);
  assert.ok(withDecal.warnings.some(text => /frame part "Lens inscription" rides on the lens .* a decal, not brow/.test(text)), withDecal.warnings.join(' | '));
  const right = withDecal.sides[1];
  assert.ok(right.stations.every(station => Math.abs(station.overlap_mm - 0.5) < 1e-6 && station.bottom_part === 'Brow'), 'the decal never reads as brow');
  assert.deepEqual(Object.keys(right.summary.by_part), ['Brow']); assert.equal(right.summary.overlap_max_mm, 0.5);
  // Without the rule the decal would have been the brow at its stations (what the WP1 proof saw on r9: 3.0 mm, saturated).
  const station = junctionStation(LENS, [{name: 'Brow', triangles: frontTriangles(brow)}, {name: 'Lens inscription', triangles: decal}], 46 * MM);
  close(station.overlap_mm, 1.5, 1e-6); assert.equal(station.bottom_part, 'Lens inscription');
});

test('frame parts near the lens that are not decals stay frame: an eyewire 0.4 mm off, a sheet covering the lens', () => {
  // An eyewire band in front of the lens top, 0.4 mm off its face (q001's eyewire is up to 0.43 mm off and 9 % of the lens area).
  const offFace = box([20, 30], [6.5, 7.9], [1.4, 1.5]);
  // A part on the lens face but larger than 2 % of the lens area: a coating sheet, not a print.
  const sheet = box([12, 58], [-14, 7], [1, 1.02]);
  assert.deepEqual(lensDecals(LENS, [{name: 'Off', triangles: offFace}, {name: 'Sheet', triangles: sheet}]), []);
  // The same thin band laid on the face is a decal; a part far from any lens is never tested past its box.
  assert.deepEqual(lensDecals(LENS, [{name: 'On', triangles: box([20, 30], [6.5, 7.9], [1, 1.1])}, {name: 'Far', triangles: box([80, 81], [0, 1], [0, 1])}])
    .map(item => item.name), ['On']);
  // No lens area at all: nothing is a decal.
  assert.deepEqual(lensDecals([], [{name: 'On', triangles: box([20, 30], [6.5, 7.9], [1, 1.1])}]), []);
});

test('rimless: no frame above any station is reported, and the box falls back to the lens top', () => {
  const temple = both(box([62, 66], [0, 6], [-120, -10]));
  const result = lensTopVsBrow(LENS, temple);
  for (const side of result.sides) {
    assert.equal(side.summary.frame_above, 0); assert.equal(side.basis, 'lens_top_only');
    assert.ok(side.stations.every(station => station.overlap_mm === null && station.brow_bottom_y_mm === null));
    close(side.box_m.max[1] / MM, 8 + 5, 1e-6);
  }
  assert.equal(result.warnings.filter(text => /no frame material above the lens top/.test(text)).length, 2);
});

test('a rim wall continuing below the 3 mm window reads the floor (saturated) and says so', () => {
  const wall = both(box([5, 65], [-20, 14], [1.5, 3]));   // a plate in front of the lens, inside the z window, from below the floor up
  const right = lensTopVsBrow(LENS, wall).sides[1];
  assert.ok(right.stations.every(station => station.floor && Math.abs(station.overlap_mm - 3) < 1e-6));
  assert.equal(right.summary.floor, 24);
});

test('frame triangles behind -30 mm or more than 6 mm in front/behind the lens top are not the brow', () => {
  const far = both(box([5, 65], [7, 14], [8, 12]));       // 7+ mm in front of the lens top
  const deep = both(box([5, 65], [7, 14], [-60, -35]));   // behind the front band
  assert.equal(frontTriangles(deep).length, 0);
  const right = lensTopVsBrow(LENS, [...far, ...deep]).sides[1];
  assert.equal(right.summary.frame_above, 0);
});

test('a shield (one lens across the bridge) is measured on both sides, split at x = 0; a missing side is reported', () => {
  const shield = box([-60, 60], [-15, 8], [-1, 1]);
  const result = lensTopVsBrow(shield, box([-65, 65], [7.2, 14], [-3, 3]));
  for (const side of result.sides) {
    assert.deepEqual(side.lens_x_range_mm.map(Math.abs).sort((a, b) => a - b), [0, 60]);
    assert.equal(side.summary.with_lens, 24); close(side.summary.overlap_median_mm, 0.8, 1e-6);
  }
  const oneSided = lensTopVsBrow(box([10, 60], [-15, 8], [-1, 1]), box([5, 65], [7.5, 14], [-3, 3]));
  assert.equal(oneSided.sides[0].basis, 'none'); assert.equal(oneSided.sides[0].box_m, null);
  assert.match(oneSided.warnings.join(' '), /left side \(x < 0\): no lens geometry/);
});

test('cropRect: padded bbox, minimum size around its centre, clamped; behind the camera or off-frame is null with a reason', () => {
  const rect = cropRect([100, 100, 0, 300, 150, 0], 1280, 854).rect;
  assert.deepEqual(rect, {x: 100 - CROP_PAD_PX, y: 100 - CROP_PAD_PX, width: 200 + 2 * CROP_PAD_PX, height: 50 + 2 * CROP_PAD_PX});
  const tiny = cropRect([640, 400, 0, 642, 401, 0], 1280, 854).rect;
  assert.ok(tiny.width >= CROP_MIN.width && tiny.height >= CROP_MIN.height);
  const corner = cropRect([2, 2, 0, 10, 10, 0], 1280, 854).rect;
  assert.equal(corner.x, 0); assert.equal(corner.y, 0); assert.ok(corner.width >= CROP_MIN.width);
  const edge = cropRect([1270, 840, 0, 1279, 853, 0], 1280, 854).rect;
  assert.ok(edge.x + edge.width <= 1280 && edge.y + edge.height <= 854 && edge.width >= CROP_MIN.width);
  assert.match(cropRect([NaN, NaN, NaN, 10, 10, 0], 1280, 854).reason, /behind the camera/);
  assert.match(cropRect([-500, -500, 0, -400, -400, 0], 1280, 854).reason, /outside the frame/);
});

test('boxCorners gives the 8 corners; nearSide picks the larger projected box', () => {
  const corners = boxCorners({min: [0, 1, 2], max: [3, 4, 5]});
  assert.equal(corners.length, 24);
  assert.deepEqual([...corners.slice(0, 3)], [0, 1, 2]); assert.deepEqual([...corners.slice(21)], [3, 4, 5]);
  assert.equal(nearSide({left: 10, right: 20}), 'right'); assert.equal(nearSide({left: 30}), 'left'); assert.equal(nearSide({}), null);
});
