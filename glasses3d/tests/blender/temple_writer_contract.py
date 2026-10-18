"""Synthetic writer regressions, each runnable independently against old or new writer code."""
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np
from mathutils import Matrix

from glasses3d.g3d import render as R, temple_bpy as T
from glasses3d.g3d.temple import build as TB, fixtures as FX, spec as TS


def mesh_object(name, vertices, polygons):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(np.asarray(vertices).tolist(), [], polygons)
    mesh.update()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def pair(uv=False):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    spec = FX.straight()
    tb = TB.build_temple(spec)
    objects = []
    for side in (1, -1):
        world, polys, _ = tb.world(side)
        ob = mesh_object('Temple ' + str(side), world, polys)
        ob['partRole'] = 'temple'
        if uv:
            layer = ob.data.uv_layers.new(name='ChartUV')
            for loop in ob.data.loops:
                d, y, _ = tb.V[loop.vertex_index]
                layer.data[loop.index].uv = (d / 150., y / 40. + .5)
        objects.append(ob)
    bpy.context.view_layer.update()
    return spec, objects


def target_spec():
    spec = FX.straight()
    spec['base']['top'] = [[0., 5.], [120., 5.]]
    spec['base']['bottom'] = [[0., 5.], [120., 5.]]
    return spec


def decal(name='Marking', x=64.1):
    return mesh_object(name, [(x, -2., -24.), (x, 2., -24.), (x, 2., -30.), (x, -2., -30.)],
                       [[0, 1, 2, 3]])


def box(name, lo, hi):
    x, y, z = lo
    X, Y, Z = hi
    return mesh_object(name, [(x,y,z),(X,y,z),(X,Y,z),(x,Y,z),(x,y,Z),(X,y,Z),(X,Y,Z),(x,Y,Z)],
                       [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]])


def variant_modifiers():
    _, objects = pair()
    original = objects[0]
    # Targets may carry their valid role on the mesh; swapping the mesh must not lose that identity.
    original.data['partRole'] = original['partRole']
    del original['partRole']
    original.modifiers.new('Weighted authored head', 'WEIGHTED_NORMAL')
    second = original.modifiers.new('Disabled authored head', 'WEIGHTED_NORMAL')
    second.show_render = False
    second.show_viewport = False
    spec = FX.analytic_prism()
    variant = T.build_variant_meshes(spec, decals=None)
    assert variant['status'] == 'ok', variant
    before = T.scene_digest()
    with R.TempRender(bpy.context.scene, objects, {}, 1) as temp:
        copy = temp.copies[original.name]
        temp.configure({}, variant['overrides'], 'native')
        assert all(not m.show_render and not m.show_viewport for m in copy.modifiers)
        assert original.modifiers[0].show_render and original.modifiers[0].show_viewport
        with bpy.context.temp_override(scene=temp.scene, view_layer=temp.scene.view_layers[0]):
            bpy.context.view_layer.update()
            evaluated = copy.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                got = np.asarray([n.vector[:] for n in mesh.corner_normals])
                want = np.asarray([n.vector[:] for n in copy.data.corner_normals])
                assert np.max(np.abs(got-want)) < 2e-5
            finally:
                evaluated.to_mesh_clear()
        temp.configure({}, {}, 'native')
        assert copy.modifiers[0].show_render and copy.modifiers[0].show_viewport
        assert not copy.modifiers[1].show_render and not copy.modifiers[1].show_viewport
    assert T.scene_digest() == before
    return {'temporary_variant_normals_match': True, 'head_flags_restored': True}


def nearby_markings():
    pair()
    font = bpy.data.curves.new('Font marking', 'FONT')
    font.body, font.size = 'LOGO', 3.
    text = bpy.data.objects.new('Text logo', font)
    bpy.context.scene.collection.objects.link(text)
    text.location, text.rotation_euler = (64.2, -2., -35.), (0., math.pi/2, 0.)
    curve = bpy.data.curves.new('Curve marking', 'CURVE')
    curve.dimensions, curve.bevel_depth = '3D', .1
    spline = curve.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co, spline.points[1].co = (64.2, 0., -65., 1.), (64.2, 0., -70., 1.)
    line = bpy.data.objects.new('Curve logo', curve)
    bpy.context.scene.collection.objects.link(line)
    box('Thick logo', (64.1, -2., -45.), (64.7, 2., -40.))
    box('Embedded plate', (63.5, -2., -55.), (64.4, 2., -50.))
    decal('Raised logo', 65.2)
    hidden = decal('Hidden marking', 65.2)
    hidden.hide_render = True
    bpy.context.view_layer.update()
    before = T.scene_digest()
    rec = T.faceted_temple(target_spec(), side='+x', dry_run=True)
    assert rec['status'] == 'planned' and rec['scene_unchanged'], rec
    parts = {p['name']: p for p in rec['sides']['+x']['unmoved_nearby_parts']}
    assert set(parts) == {'Text logo', 'Curve logo', 'Thick logo', 'Embedded plate', 'Raised logo'}, parts
    assert parts['Text logo']['type'] == 'FONT' and parts['Curve logo']['type'] == 'CURVE'
    assert all(p['reason'] for p in parts.values())
    assert len([w for w in rec['sides']['+x']['warnings'] if w['code'] == 'W_DECAL_NOT_MOVED']) == len(parts)
    assert T.scene_digest() == before
    return {'reported': parts, 'hidden_omitted': True}


def edge_decals():
    checks = []
    for slope in (0., .01, -.01):
        _, objects = pair()
        for vertex in objects[0].data.vertices:
            vertex.co.y += slope*(vertex.co.x-60.)
        objects[0].data.update()
        mesh_object('Top marking', [(x, 10.+slope*(x-60.)+.1, z)
                                    for x, z in ((61., -30.), (63., -30.), (63., -35.), (61., -35.))],
                    [[0, 1, 2, 3]])
        bpy.context.view_layer.update()
        before = T.scene_digest()
        rec = T.faceted_temple(target_spec(), side='+x')
        assert rec['status'] == 'refused' and rec['code'] == 'E_TARGET', rec
        assert rec['fields']['decal'] == 'Top marking' and '45 degrees' in rec['message'], rec
        assert rec['scene_unchanged'] and before == T.scene_digest()
        checks.append(slope)
    return {'refused_top_slopes': checks}


def shared_undo():
    results = []
    for order in ((0, 1), (1, 0)):
        _, objects = pair()
        first = decal()
        second = first.copy()
        second.name = 'Mirrored shared marking'
        second.matrix_world = Matrix.Diagonal((-1., 1., 1., 1.))
        bpy.context.scene.collection.objects.link(second)
        bpy.context.view_layer.update()
        original = first.data
        original_name, digest = original.name, T.mesh_digest(original)
        before = T.scene_digest()
        result = T.faceted_temple(target_spec())
        assert result['status'] == 'ok', result
        backups = [json.loads(ob[T.PROP_BACKUP]) for ob in objects]
        assert all(bpy.data.meshes[b['decals'][mark.name]['mesh']] == original
                   for b, mark in zip(backups, (first, second)))
        assert original.name == original_name
        assert first.data != second.data and all(T.BACKUP_SUFFIX not in ob.data.name for ob in (first, second))
        assert T.faceted_temple(target_spec())['status'] == 'ok'
        for index in order:
            result = T.unfaceted_temple(objects[index])
            assert result['status'] == 'ok', result
        assert first.data == second.data == original
        assert original.name == original_name and T.mesh_digest(original) == digest
        assert T.scene_digest() == before
        results.append(order)
    return {'undo_orders': results, 'original_shared_identity_and_bytes': True}


def uv_decal_opening():
    original_spec, objects = pair(uv=True)
    mark = decal()
    source = np.asarray([v.co[:] for v in mark.data.vertices])
    d0, y0 = original_spec['hinge']['z_mm']-source[:, 2], source[:, 1]
    errors = []
    for opening in (10., 16.):
        spec = target_spec()
        spec['hinge']['opening_deg'] = opening
        result = T.faceted_temple(spec)
        assert result['status'] == 'ok', result
        h = spec['hinge']
        moved = np.asarray([v.co[:] for v in mark.data.vertices])
        local = TB.to_local(moved, h['z_mm'], h['axis_xz_mm'], opening)
        # Constant destination outer face: signed .1 mm standoff is along +local X.
        expected = np.column_stack((d0, y0, np.full(len(d0), 65.1)))
        error = float(np.max(np.abs(local-expected)))
        assert error < 3e-5, (opening, error, local, expected)
        # The same d/y must still address the original analytic per-loop UV chart.
        ob = objects[0]
        coords, _, _ = T.mesh_arrays(ob, T.asset_origin())
        chart = TB.to_local(coords, h['z_mm'], h['axis_xz_mm'], opening)
        uv = ob.data.uv_layers['ChartUV']
        uv_error = max(float(np.max(np.abs(np.asarray(uv.data[loop.index].uv)-
                                          [chart[loop.vertex_index, 0]/150., chart[loop.vertex_index, 1]/40.+.5])))
                       for loop in ob.data.loops)
        assert uv_error < 2e-6, uv_error
        errors.append({'opening': opening, 'decal_error_mm': error, 'uv_error': uv_error})
    return {'charted_at_old_opening_then_new_opening': errors}


def float32_ears():
    # A long straight boundary stored at 4 degrees contains nonzero-area float32 ears.
    chart = np.asarray([(i*10/8, 0., 70.) for i in range(9)] + [(10., 10., 70.), (0., 10., 70.)])
    world = TB.to_world(chart, 0., [60., 0.], 4.)
    ob = mesh_object('Opened collinear boundary', world, [list(range(len(world)))])
    canonical = [[10, i, i+1] for i in range(8)] + [[10, 8, 9]]
    ob.data.calc_loop_triangles()
    points = np.asarray([v.co[:] for v in ob.data.vertices])
    tris = np.asarray([t.vertices[:] for t in ob.data.loop_triangles])
    flags = TS.triangle_degenerate_float32(points, tris)
    q = points[tris]
    area2 = np.linalg.norm(np.cross(q[:,1]-q[:,0], q[:,2]-q[:,0]), axis=1)
    assert np.any(flags & (area2 > 2e-12)), (flags.tolist(), area2.tolist())
    fixed, count = T._repair_degenerate_tessellation(ob.data, chart, canonical, [0]*len(canonical))
    fixed.calc_loop_triangles()
    after = np.asarray([t.vertices[:] for t in fixed.loop_triangles])
    assert count == 1 and not TS.triangle_degenerate_float32(points, after).any()
    # Isolate the noisy ear: no exactly-zero ear remains to accidentally trigger the old absolute cutoff.
    # An already triangular sliver has no boundary-preserving repair and must refuse, never pass silently.
    index = int(np.flatnonzero(flags & (area2 > 2e-12))[0])
    tiny = mesh_object('Isolated float32 ear', points[tris[index]], [[0, 1, 2]])
    try:
        T._repair_degenerate_tessellation(tiny.data, chart[tris[index]], [[0, 1, 2]], [0])
    except TS.TempleError as exc:
        assert exc.code == 'E_TARGET' and 'float32' in exc.message
    else:
        raise AssertionError('nonzero float32-degenerate triangle was silently accepted')
    return {'nonzero_float32_bad_ears': int(np.count_nonzero(flags & (area2 > 2e-12))),
            'after_bad_ears': 0, 'repaired_polygons': count, 'isolated_nonzero_ear_refused': True}


def normal_pass():
    _, objects = pair()
    with R.TempRender(bpy.context.scene, objects, {}, 1) as temp:
        temp.set_pass('normal')
        assert temp.scene.render.dither_intensity == 0.
        temp.set_pass('native')
        assert temp.scene.render.dither_intensity == temp.dither0
    return {'normal_dither_zero': True, 'native_dither_unchanged': True}


def exported_surfaces():
    warp = FX.straight(nodes={'A': [10., -6., 5.], 'B': [40., -6., 5.],
                              'C': [40., 6., 6.5], 'D': [10., 6., 5.]},
                        facets={'Q': {'poly': ['A', 'B', 'C', 'D'], 'plane': 'warp'}})
    soft = FX.straight(nodes={'A': [20., -5., 4.5], 'B': [50., -5., 4.5],
                              'C': [50., 5., 4.2], 'D': [20., 5., 4.2]},
                        facets={'S': {'poly': ['A', 'B', 'C', 'D'], 'soft': True}})
    outputs = []
    for label, spec in (('rolled', FX.rolled()), ('warp', warp), ('soft', soft)):
        pair()
        spec['hinge']['opening_deg'] = 4.
        result = T.faceted_temple(spec, decals=None)
        assert result['status'] == 'ok', result
        scene = bpy.context.scene
        scene.unit_settings.system = 'METRIC'
        scene.unit_settings.scale_length = .001
        scene[T.EX.ORIGIN_KEY] = [0., 0., 0.]
        path = Path(sys.argv[-1]).parent / (label + '.glb')
        receipt = T.EX.export_glb(path)
        assert receipt['status'] == 'exported' and receipt['scene_unchanged'], receipt
        outputs.append(str(path))
    return {'exported': outputs}


CASES = {function.__name__: function for function in (variant_modifiers, nearby_markings, edge_decals,
          shared_undo, uv_decal_opening, float32_ears, normal_pass, exported_surfaces)}

if __name__ == '__main__':
    case, out = sys.argv[sys.argv.index('--')+1:]
    result = CASES[case]()
    Path(out).write_text(json.dumps({'status': 'ok', 'case': case, 'checks': result}, indent=2), encoding='utf-8')
