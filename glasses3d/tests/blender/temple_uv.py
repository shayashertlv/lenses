"""Actual Blender regression for original per-loop UV transfer. No file opened by Blender is saved."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np
from mathutils import Matrix
from glasses3d.g3d import temple_bpy as T
from glasses3d.g3d.temple import build as TB, fixtures as FX


def values(kind, d, y, fraction):
    if kind in TB.OUTER_KINDS or kind == 'inner':
        return [d / 150 + max(d - 60, 0) / 10000 + (2 if kind == 'inner' else 0), .2 + y / 40]
    if kind in ('top', 'bottom'):
        return [d / 150 + (4 if kind == 'top' else 6), fraction]
    return [y / 40 + (8 if kind == 'cap_hinge' else 10), fraction]


def pair(interior_seam=False, split_vertices=False):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    original = FX.straight()
    tb = TB.build_temple(original)
    objects = []
    for side in (1, -1):
        W, polys, _ = tb.world(side)
        matrix = Matrix.Translation((3., -2., 4.)) @ Matrix.Rotation(.17, 4, 'Y') @ Matrix.Diagonal((-1.2, .8, 1.1, 1.))
        me = bpy.data.meshes.new('Original')
        local = (W - np.array(matrix)[:3, 3]) @ np.linalg.inv(np.array(matrix)[:3, :3]).T
        me.from_pydata(local.tolist(), [], [p[::-1] for p in polys])
        me.update()
        ob = bpy.data.objects.new('Right' if side > 0 else 'Left', me)
        bpy.context.scene.collection.objects.link(ob)
        ob.matrix_world = matrix
        ob['partRole'] = 'temple'
        for index, name in enumerate(('SurfaceUV', 'DetailUV')):
            layer = me.uv_layers.new(name=name)
            uv = []
            for pi, polygon in enumerate(me.polygons):
                kind = tb.poly_kind[pi]
                across = interior_seam and kind in TB.OUTER_KINDS and tb.V[list(polygon.vertices), 0].mean() > 60
                for vi in polygon.vertices:
                    d, y, x = tb.V[vi]
                    a, b = values(kind, d, y, (x - 60) / 4)
                    if across:
                        a += 3
                    uv.append([a, b] if index == 0 else [1 + 2 * a, 3 - b])
            layer.data.foreach_set('uv', np.asarray(uv, np.float32).ravel())
            layer.active_render = index == 1
        me.uv_layers.active_index = 1
        if split_vertices:
            duplicate = bpy.data.meshes.new('Imported split UV vertices')
            coords = [me.vertices[loop.vertex_index].co[:] for loop in me.loops]
            duplicate.from_pydata(coords, [], [list(poly.loop_indices) for poly in me.polygons])
            duplicate.update()
            for layer in me.uv_layers:
                copied = duplicate.uv_layers.new(name=layer.name)
                copied.data.foreach_set('uv', np.array([loop.uv[:] for loop in layer.data], np.float32).ravel())
                copied.active_render = layer.active_render
            duplicate.uv_layers.active_index = me.uv_layers.active_index
            ob.data = duplicate
            bpy.data.meshes.remove(me)
        objects.append(ob)
    bpy.context.view_layer.update()
    return objects, original


def design():
    s = FX.straight()
    s['nodes'] = {'A': [20., 10., 4.], 'B': [100., 10., 4.], 'C': [50., -10., 6.]}
    s['facets'] = {'F': ['A', 'B', 'C']}
    return s


def uv_record(me):
    return {'names': [layer.name for layer in me.uv_layers], 'active': me.uv_layers.active_index,
            'render': [layer.active_render for layer in me.uv_layers],
            'values': [np.array([loop.uv[:] for loop in layer.data]) for layer in me.uv_layers]}


def check_lineage(ob):
    """The actual writer retains names through mirrored frames, seams and ear repairs."""
    from glasses3d.g3d import model_map_bpy as MAP
    metadata = json.loads(ob['g3d_temple'])
    assert metadata['lineage']['schema'] == 'g3d.temple_lineage/1'
    raw = MAP._mesh(ob.data, ob.matrix_world, T.asset_origin())
    before = T.scene_digest()
    build = TB.build_temple

    def newer_builder(*args, **kwargs):
        raise RuntimeError('test: the current builder no longer supports this historical spec')

    TB.build_temple = newer_builder
    try:
        result = MAP._aliases(ob, raw)
    finally:
        TB.build_temple = build
    assert result['status'] == 'verified_topology', result
    assert result['facets'] == metadata['lineage']['facets']
    assert result['historical_coordinates_used'] is False
    assert T.scene_digest() == before


def check_values(ob, spec):
    check_lineage(ob)
    tb = TB.build_temple(spec)
    source = uv_record(ob.data)
    assert source['names'] == ['SurfaceUV', 'DetailUV'] and source['active'] == 1
    assert source['render'] == [False, True]
    surf = T.OuterSurface(tb)
    errors = []
    for pi, polygon in enumerate(ob.data.polygons):
        for li in polygon.loop_indices:
            vi = ob.data.loops[li].vertex_index
            d, y, x = tb.V[vi]
            fraction = (x - 60) / (surf.at(d, y)[0] - 60)
            expected = values(tb.poly_kind[pi], d, y, fraction)
            errors.append(np.max(np.abs(source['values'][0][li] - expected)))
    assert max(errors) < 3e-6, max(errors)
    assert np.max(np.abs(source['values'][1] - np.column_stack((1 + 2 * source['values'][0][:, 0],
                                                                3 - source['values'][0][:, 1])))) < 2e-6
    assert sum(edge.use_seam for edge in ob.data.edges) > 0
    return source


def closed(me):
    edges = {}
    for poly in me.polygons:
        vertices = list(poly.vertices)
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            edges[a, b] = edges.get((a, b), 0) + 1
    assert all(count == 1 and edges.get((b, a)) == 1 for (a, b), count in edges.items())
    assert len(me.vertices) - len(edges) // 2 + len(me.polygons) == 2


def reference_mesh(ob, tb, side):
    W, polys, _ = tb.world(side)
    pl = polys if np.linalg.det(np.array(ob.matrix_world)[:3, :3]) > 0 else [p[::-1] for p in polys]
    local = T._local(ob, W, T.asset_origin())
    normals = T._local_normals(ob, T._world_normals(tb, side))
    corners = T.corner_normals(tb, pl, normals)
    me, _ = T.build_mesh('Untessellated reference', local, pl, tb.sharp, tb.flat_polys, normals, [], corners=corners)
    return me


def verify_tessellation(ob, reference, tb, side, island=lambda d, y: d > 60):
    """Independent geometric, normal and analytic-texture checks against the pre-UV core mesh."""
    check_lineage(ob)
    closed(ob.data)
    reference.calc_loop_triangles()
    ref_v = np.array([v.co[:] for v in reference.vertices])
    ref_t = np.array([t.vertices[:] for t in reference.loop_triangles])
    ref_n = np.array([n.vector[:] for n in reference.corner_normals])
    bvh = T._bvh(ref_v, ref_t)
    ref_geometric = np.cross(ref_v[ref_t[:, 1]] - ref_v[ref_t[:, 0]], ref_v[ref_t[:, 2]] - ref_v[ref_t[:, 0]])
    ref_area = np.linalg.norm(ref_geometric, axis=1)
    ref_geometric /= np.maximum(ref_area[:, None], 1e-30)
    ref_basis = np.stack((ref_v[ref_t[:, 1]] - ref_v[ref_t[:, 0]],
                          ref_v[ref_t[:, 2]] - ref_v[ref_t[:, 0]]), axis=2)
    ref_coeff = np.linalg.pinv(ref_basis)
    ref_grad = np.concatenate((-ref_coeff.sum(axis=1)[:, None, :], ref_coeff), axis=1)
    ref_slack = 1e-4 * np.linalg.norm(ref_grad, axis=2)

    def reference_at(point, geometric_normal):
        # Near a crease/cap, float32 nearest-distance ties must not select the opposite shading fan. The new
        # face lies on an existing triangle plane, so its geometric normal identifies the correct old side.
        # Double-precision projection also avoids BVH nearest-point cancellation on very thin triangles.
        nearby = np.array(sorted({hit[2] for hit in bvh.find_nearest_range(tuple(point), .01)}), dtype=np.int64)
        if not len(nearby):
            nearby = np.arange(len(ref_t))
        delta = point - ref_v[ref_t[nearby, 0]]
        ab = np.einsum('nij,nj->ni', ref_coeff[nearby], delta)
        weights = np.column_stack((1 - ab.sum(axis=1), ab))
        distance = np.abs(np.einsum('ni,ni->n', delta, ref_geometric[nearby]))
        candidates = nearby[np.flatnonzero(np.all(weights >= -ref_slack[nearby], axis=1) &
                                          (distance < 1e-4) & (ref_area[nearby] > 1e-12))]
        if not len(candidates):
            delta = point - ref_v[ref_t[:, 0]]
            ab = np.einsum('nij,nj->ni', ref_coeff, delta)
            weights = np.column_stack((1 - ab.sum(axis=1), ab))
            distance = np.abs(np.einsum('ni,ni->n', delta, ref_geometric))
            candidates = np.flatnonzero(np.all(weights >= -ref_slack, axis=1) &
                                        (distance < 1e-4) & (ref_area > 1e-12))
        assert len(candidates), (point.tolist(), distance.min())
        return max(candidates, key=lambda ti: ref_geometric[ti] @ geometric_normal)
    actual_v = np.array([v.co[:] for v in ob.data.vertices])
    actual_n = np.array([n.vector[:] for n in ob.data.corner_normals])
    M = np.array(ob.matrix_world)
    world = actual_v @ M[:3, :3].T + M[:3, 3] - T.asset_origin()
    h = tb.spec.hinge
    chart = TB.to_local(world, h['z_mm'], h['axis_xz_mm'], h['opening_deg'], side)
    surf = T.OuterSurface(tb)
    maximum_distance, maximum_angle, maximum_interior, maximum_flat = 0., 0., 0., 0.
    angle_detail = None
    for p in actual_v:
        maximum_distance = max(maximum_distance, bvh.find_nearest(tuple(p))[3])
    for polygon in ob.data.polygons:
        ids = list(polygon.vertices)
        _, _, ti, _ = bvh.find_nearest(tuple(actual_v[ids].mean(axis=0)))
        triangle = reference.loop_triangles[ti]
        kind = tb.poly_kind[triangle.polygon_index]
        branches = []
        for li in polygon.loop_indices:
            vi = ob.data.loops[li].vertex_index
            # A quad's center can lie on its diagonal. Select the reference triangle just inside this corner
            # instead of extrapolating one diagonal's normal field to every corner of the whole polygon.
            center = actual_v[ids].mean(axis=0)
            # For a triangle the strict interior identifies the same original triangle for all three corners.
            # A near-corner probe on a very thin triangle can fall in both old fans' float32 edge-slack bands.
            probe = center if len(ids) == 3 else actual_v[vi] * (1 - 1e-4) + center * 1e-4
            normal_ti = reference_at(probe, np.array(polygon.normal))
            normal_triangle = reference.loop_triangles[normal_ti]
            na = ref_v[normal_triangle.vertices[0]]
            normal_coeff = ref_coeff[normal_ti]
            ab = normal_coeff @ (actual_v[vi] - na)
            w = np.array([1 - ab.sum(), *ab])
            expected_normal = w @ ref_n[list(normal_triangle.loops)]
            expected_normal /= np.linalg.norm(expected_normal)
            angle = np.degrees(np.arccos(np.clip(expected_normal @ actual_n[li], -1., 1.)))
            if angle > maximum_angle:
                angle_detail = (polygon.index, kind, normal_ti, actual_v[ids].tolist(),
                                expected_normal.tolist(), actual_n[li].tolist(), list(polygon.normal))
            maximum_angle = max(maximum_angle, angle)
            d, y, x = chart[vi]
            fraction = (x - 60) / (surf.at(d, y)[0] - 60)
            expected = np.array(values(kind, d, y, fraction))
            actual = np.array(ob.data.uv_layers[0].data[li].uv[:])
            if kind in TB.OUTER_KINDS:
                branch = round((actual[0] - expected[0]) / 3)
                assert branch in (0, 1)
                branches.append(branch)
                expected[0] += 3 * branch
            assert np.max(np.abs(actual - expected)) < 5e-6, (kind, actual, expected)
        assert len(set(branches)) <= 1, (polygon.index, branches, chart[ids].tolist())
        if branches:
            center = chart[ids].mean(axis=0)
            segments = getattr(island, 'boundary', np.array([[60., -10.], [60., 10.]]))
            starts, ends = segments[:-1], segments[1:]
            directions = ends - starts
            fraction = np.clip(np.einsum('ij,ij->i', center[:2] - starts, directions) /
                               np.einsum('ij,ij->i', directions, directions), 0, 1)
            distance = np.linalg.norm(starts + fraction[:, None] * directions - center[:2], axis=1).min()
            # The source object/mesh round-trips through float32. Within its existing geometric edge slack,
            # either adjacent island may own a microscopic cell, but all its loops must still agree above.
            if distance > T.EDGE_SLACK_MM:
                assert branches[0] == int(island(center[0], center[1])), (branches, center.tolist())
    assert maximum_distance < 1e-4, maximum_distance
    ob.data.calc_loop_triangles()
    for triangle in ob.data.loop_triangles:
        pos = actual_v[list(triangle.vertices)]
        cn = actual_n[list(triangle.loops)]
        geometric = np.cross(pos[1] - pos[0], pos[2] - pos[0])
        if np.linalg.norm(geometric) < 1e-12:
            continue  # Blender can emit zero-area loop triangles for collinear boundary vertices; no interior.
        geometric /= np.linalg.norm(geometric)
        for weights in ((1/3, 1/3, 1/3), (.6, .2, .2), (.2, .6, .2), (.2, .2, .6)):
            point = np.array(weights) @ pos
            try:
                ti = reference_at(point, geometric)
            except AssertionError as exc:
                raise AssertionError((str(exc), triangle.index, pos.tolist(),
                                      float(np.linalg.norm(np.cross(pos[1]-pos[0],pos[2]-pos[0]))))) from exc
            original = reference.loop_triangles[ti]
            a = ref_v[original.vertices[0]]
            coeff = ref_coeff[ti]
            ab = coeff @ (point - a)
            expected = np.array([1 - ab.sum(), *ab]) @ ref_n[list(original.loops)]
            actual = np.array(weights) @ cn
            cosine = expected @ actual / (np.linalg.norm(expected) * np.linalg.norm(actual))
            angle = np.degrees(np.arccos(np.clip(cosine, -1., 1.)))
            maximum_interior = max(maximum_interior, angle)
            if original.polygon_index in tb.flat_polys:
                maximum_flat = max(maximum_flat, angle)
    assert maximum_flat < .1, (maximum_flat, maximum_interior, maximum_angle, angle_detail)
    assert maximum_angle < .5, (maximum_angle, maximum_interior, maximum_flat, angle_detail)
    return {'surface_mm': maximum_distance, 'corner_normals_deg': maximum_angle,
            'interior_normals_deg': maximum_interior, 'flat_normals_deg': maximum_flat}


def concave_source():
    """A closed source with a C-shaped UV island, whose polygon mean lies outside that island."""
    objects, spec = pair()
    ds, ys = [0., 40., 50., 60., 80., 120.], [-10., -8., -4., 4., 8., 10.]
    points = np.array([[d, y, x] for x in (64., 60.) for y in ys for d in ds])
    n, width = len(ds) * len(ys), len(ds)
    faces, kinds = [], []
    for row in range(len(ys) - 1):
        for column in range(width - 1):
            a = row * width + column
            face = [a, a + 1, a + width + 1, a + width]
            faces.extend((face, [v + n for v in face[::-1]]))
            kinds.extend(('outer', 'inner'))
    boundary = list(range(width)) + [r * width + width - 1 for r in range(1, len(ys))]
    boundary += list(range(n - 2, n - width - 1, -1)) + [r * width for r in range(len(ys) - 2, 0, -1)]
    for a, b in zip(boundary, boundary[1:] + boundary[:1]):
        faces.append([a, a + n, b + n, b])
        center = (points[a] + points[b]) / 2
        kinds.append('bottom' if center[1] == -10 else 'top' if center[1] == 10 else
                     'cap_hinge' if center[0] == 0 else 'cap_tip')
    inside = lambda d, y: 40 < d < 80 and -8 < y < 8 and (d < 50 or y < -4 or y > 4)
    inside.boundary = np.array([[40., -8.], [80., -8.], [80., -4.], [50., -4.], [50., 4.],
                               [80., 4.], [80., 8.], [40., 8.], [40., -8.]])
    h = spec['hinge']
    for side, ob in zip((1, -1), objects):
        world = TB.to_world(points, h['z_mm'], h['axis_xz_mm'])
        world[:, 0] *= side
        polys = [face[::-1] for face in faces] if side < 0 else faces
        if np.linalg.det(np.array(ob.matrix_world)[:3, :3]) < 0:
            polys = [face[::-1] for face in polys]
        me = bpy.data.meshes.new('Concave UV island source')
        me.from_pydata(T._local(ob, world, T.asset_origin()).tolist(), [], polys)
        me.update()
        layer = me.uv_layers.new(name='SurfaceUV')
        for kind, poly in zip(kinds, me.polygons):
            center = points[list(poly.vertices)].mean(axis=0)
            offset = 3 if kind == 'outer' and inside(*center[:2]) else 0
            for li in poly.loop_indices:
                d, y, x = points[me.loops[li].vertex_index]
                uv = values(kind, d, y, (x - 60) / 4)
                uv[0] += offset
                layer.data[li].uv = uv
        previous, ob.data = ob.data, me
        bpy.data.meshes.remove(previous)
    return objects, inside


def run():
    meshes = set(bpy.data.meshes.keys())
    try:
        T.build_mesh('Forced incomplete mesh', [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]],
                     [(0, 3)], [], np.zeros((1, 3)), [])
    except KeyError:
        pass
    else:
        raise AssertionError('invalid sharp edge must fail mesh construction')
    assert set(bpy.data.meshes.keys()) == meshes, 'failed mesh construction leaked its untracked datablock'
    objects, _ = pair()
    original_data = [ob.data for ob in objects]
    original_digest = [T.mesh_digest(me) for me in original_data]
    spec = design()
    before = T.scene_digest()
    result = T.faceted_temple(spec, decals=None, dry_run=True)
    assert result['status'] == 'planned', result
    assert T.scene_digest() == before
    other = copy.deepcopy(spec)
    other['nodes']['C'][2] = 5.
    other['hinge']['opening_deg'] = 8.
    initial_variant = T.build_variant_meshes(other, decals=None)
    assert initial_variant['status'] == 'ok', initial_variant
    initial_uv = {side: uv_record(bpy.data.meshes[name]) for side, name in initial_variant['meshes'].items()}
    T.remove_variant_meshes()
    assert T.scene_digest() == before
    result = T.faceted_temple(spec, decals=None)
    assert result['status'] == 'ok', result
    first = [check_values(ob, spec) for ob in objects]
    before = T.scene_digest()
    variant = T.build_variant_meshes(other, decals=None)
    assert variant['status'] == 'ok', variant
    variant_uv = {side: uv_record(bpy.data.meshes[name]) for side, name in variant['meshes'].items()}
    T.remove_variant_meshes()
    assert T.scene_digest() == before
    assert T.faceted_temple(other, decals=None)['status'] == 'ok'
    for side, ob in zip(('+x', '-x'), objects):
        actual = uv_record(ob.data)
        for a, b in zip(actual['values'], variant_uv[side]['values']):
            assert np.array_equal(a, b)
        assert all(np.array_equal(a, b) for a, b in zip(actual['values'], initial_uv[side]['values']))
    assert T.faceted_temple(spec, decals=None)['status'] == 'ok'
    for ob, rec in zip(objects, first):
        actual = check_values(ob, spec)
        assert all(np.array_equal(a, b) for a, b in zip(rec['values'], actual['values']))
    for ob, me, digest in zip(objects, original_data, original_digest):
        assert T.unfaceted_temple(ob)['status'] == 'ok'
        assert ob.data == me and T.mesh_digest(me) == digest
    objects, _ = pair(interior_seam=True)
    result = T.faceted_temple(FX.straight(), decals=None)
    assert result['status'] == 'ok', result
    for ob in objects:
        tb = TB.build_temple(FX.straight())
        seam_u = []
        for pi, polygon in enumerate(ob.data.polygons):
            if tb.poly_kind[pi] not in TB.OUTER_KINDS:
                continue
            for li in polygon.loop_indices:
                vi = ob.data.loops[li].vertex_index
                if abs(tb.V[vi, 0] - 60) < 1e-7:
                    seam_u.append(ob.data.uv_layers[0].data[li].uv.x)
        assert min(seam_u) < .401 and max(seam_u) > 3.399, (min(seam_u), max(seam_u))
        assert T.unfaceted_temple(ob)['status'] == 'ok'
    objects, _ = pair(interior_seam=True, split_vertices=True)
    before = T.scene_digest()
    tb = TB.build_temple(spec)
    references = [reference_mesh(ob, tb, side) for side, ob in zip((1, -1), objects)]
    result = T.faceted_temple(spec, decals=None)
    assert result['status'] == 'ok', result
    evidence = []
    for side, ob, reference in zip((1, -1), objects, references):
        evidence.append(verify_tessellation(ob, reference, tb, side))
        assert len(ob.data.vertices) > len(TB.build_temple(spec).V)
        bpy.data.meshes.remove(reference)
        assert T.unfaceted_temple(ob)['status'] == 'ok'
    rolled = copy.deepcopy(spec)
    rolled['roll_mm'] = .6
    tb = TB.build_temple(rolled)
    references = [reference_mesh(ob, tb, side) for side, ob in zip((1, -1), objects)]
    result = T.faceted_temple(rolled, decals=None)
    assert result['status'] == 'ok', result
    smooth = []
    for side, ob, reference in zip((1, -1), objects, references):
        smooth.append(verify_tessellation(ob, reference, tb, side))
        bpy.data.meshes.remove(reference)
    objects, island = concave_source()
    tb = TB.build_temple(spec)
    references = [reference_mesh(ob, tb, side) for side, ob in zip((1, -1), objects)]
    result = T.faceted_temple(spec, decals=None)
    assert result['status'] == 'ok', result
    concave = []
    for side, ob, reference in zip((1, -1), objects, references):
        concave.append(verify_tessellation(ob, reference, tb, side, island))
        bpy.data.meshes.remove(reference)
    return {'status': 'ok', 'layers': 2, 'sides': 2, 'repeat_exact': True, 'variant_exact': True,
            'unfacet_exact': True, 'interior_seam_tessellated': True, 'tessellation': evidence,
            'split_source_vertices': True, 'smooth_tessellation': smooth, 'concave_island': concave}


if __name__ == '__main__':
    Path(sys.argv[-1]).write_text(json.dumps(run(), indent=2), encoding='utf-8')
