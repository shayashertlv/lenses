"""UV edits after faceting become the next frozen source; original unfacet backups stay exact."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np
from mathutils import Matrix, Vector
from glasses3d.g3d import temple_bpy as T
from glasses3d.tests.blender.temple_uv import pair, design, uv_record


def record(me):
    return {**uv_record(me), 'clone': [layer.active_clone for layer in me.uv_layers]}


def equal_uv(actual, expected, *, exact=True):
    for key in ('names', 'active', 'render', 'clone'):
        assert actual[key] == expected[key], (key, actual[key], expected[key])
    for actual_values, expected_values in zip(actual['values'], expected['values']):
        assert actual_values.shape == expected_values.shape
        if exact:
            assert np.array_equal(actual_values, expected_values)
        else:
            assert np.max(np.abs(actual_values - expected_values), initial=0) < 1e-5


def state():
    """Also bind unlinked datablocks and UV flags, which the exported scene hash may omit."""
    meshes = []
    for me in sorted(bpy.data.meshes, key=lambda mesh: mesh.name):
        rec = record(me)
        meshes.append((me.name, me.users, me.use_fake_user, T.mesh_digest(me),
                       rec['names'], rec['active'], rec['render'], rec['clone'],
                       [hashlib.sha256(a.tobytes()).hexdigest() for a in rec['values']],
                       [(key, str(me[key])) for key in sorted(me.keys())]))
    objects = [(ob.name, ob.data.name if ob.type == 'MESH' else None,
                [(key, str(ob[key])) for key in sorted(ob.keys())]) for ob in bpy.data.objects]
    return T.scene_digest(), meshes, objects


def build(spec):
    result = T.faceted_temple(spec, decals=None)
    assert result['status'] == 'ok', result


def shift_spec(spec, dy):
    shifted = copy.deepcopy(spec)
    for chain in ('top', 'bottom'):
        for point in shifted['outline'][chain]:
            point[1] += dy
    for point in shifted['nodes'].values():
        point[1] += dy
    return shifted


def scale_translate_spec(spec, scale, dz):
    changed = copy.deepcopy(spec)
    changed['hinge']['z_mm'] = scale * changed['hinge']['z_mm'] + dz
    axis = changed['hinge']['axis_xz_mm']
    changed['hinge']['axis_xz_mm'] = [scale * axis[0], scale * axis[1] + dz]
    for table in (changed['outline']['top'], changed['outline']['bottom'], changed['inner'],
                  changed['base']['top'], changed['base']['bottom']):
        for row in table:
            row[:] = [scale * value for value in row]
    for row in changed['nodes'].values():
        row[:] = [scale * value if isinstance(value, (int, float)) else value for value in row]
    changed['outline']['tip_round_mm'] *= scale
    changed['roll_mm'] *= scale
    changed['options']['station_step_mm'] = scale * changed['options'].get('station_step_mm', 2.)
    changed['options']['snap_mm'] = scale * changed['options'].get('snap_mm', .05)
    return changed


def check_known_uv(objects, *, dy=0., scale=1., dz=0.):
    """Independent inverse transform of the known nonconstant UV chart, on every current corner."""
    for ob in objects:
        layer = ob.data.uv_layers['g3d_print']
        for li, loop in enumerate(ob.data.loops):
            point = ob.matrix_world @ ob.data.vertices[loop.vertex_index].co
            expected = np.array([(point.z - dz) / scale / 150, (point.y / scale - dy) / 40])
            assert np.max(np.abs(np.array(layer.data[li].uv[:]) - expected)) < 5e-6, (ob.name, li, expected)


def cycle(objects, spec, *, input_uv_check=None):
    """Planning cannot advance the source; A->B->A must use one exact new generation."""
    expected = [record(ob.data) for ob in objects]
    if input_uv_check is not None:
        input_uv_check(objects)
    before = state()
    other = copy.deepcopy(spec)
    other['nodes']['C'][2] = spec['nodes']['C'][2] / 6 * 5
    dry = T.faceted_temple(other, decals=None, dry_run=True)
    if dry['status'] != 'planned' and os.environ.get('G3D_UV_DIAGNOSTIC'):
        target = Path(os.environ['G3D_UV_DIAGNOSTIC'])
        target.mkdir(parents=True, exist_ok=False)
        bpy.ops.wm.save_as_mainfile(filepath=str(target / 'before.blend'))
        (target / 'specs.json').write_text(json.dumps({'A': spec, 'B': other, 'result': dry}, indent=2),
                                         encoding='utf-8')
    assert dry['status'] == 'planned' and dry['scene_unchanged'], dry
    assert state() == before, 'dry run changed scene, UV source or mesh datablocks'
    variant = T.build_variant_meshes(other, decals=None)
    assert variant['status'] == 'ok', variant
    variant_uv = [record(bpy.data.meshes[variant['meshes'][side]]) for side in ('+x', '-x')]
    assert T.remove_variant_meshes()['status'] == 'ok'
    assert state() == before, 'variant planning/cleanup changed source or leaked a mesh'
    build(spec)
    first = [record(ob.data) for ob in objects]
    if input_uv_check is None:
        for actual, wanted in zip(first, expected):
            equal_uv(actual, wanted, exact=False)
    else:
        # A transformed source can require seam subdivisions, so the old and new loop counts differ.
        # Preserve layer metadata exactly and evaluate the authored UV function at every new corner.
        for actual, wanted in zip(first, expected):
            for key in ('names', 'active', 'render', 'clone'):
                assert actual[key] == wanted[key], (key, actual[key], wanted[key])
        input_uv_check(objects)
    build(other)
    for ob, wanted in zip(objects, variant_uv):
        equal_uv(record(ob.data), wanted)
    build(spec)
    for ob, wanted in zip(objects, first):
        equal_uv(record(ob.data), wanted)
    return spec


def injected_write_failure():
    """Local rollback restores original meshes and removes both newly captured UV sources."""
    from glasses3d.g3d import temple_uv_source as UVS
    objects, _ = pair()
    original = [(ob.data, T.mesh_digest(ob.data), record(ob.data)) for ob in objects]
    original_meshes = {me.name for me in bpy.data.meshes}
    spec = design()
    build(spec)
    for ob in objects:
        layer = ob.data.uv_layers.new(name='g3d_print')
        layer.data.foreach_set('uv', np.tile(np.array([.25, .75], np.float32), len(layer.data)))
    build(spec)  # retain an existing extra generation before the failing replacement capture
    for ob in objects:
        layer = ob.data.uv_layers['g3d_print']
        layer.data.foreach_set('uv', np.tile(np.array([.5, .625], np.float32), len(layer.data)))
    calls = []
    real_written = UVS.record_written

    def fail_second(ob, backup, origin):
        calls.append(ob.name)
        if len(calls) == 2:
            raise RuntimeError('injected failure after second UV source capture')
        return real_written(ob, backup, origin)

    UVS.record_written = fail_second
    try:
        result = T.faceted_temple(spec, decals=None)
    finally:
        UVS.record_written = real_written
    assert len(calls) == 2 and result['status'] == 'failed', result
    assert 'injected failure after second UV source capture' in result.get('error', ''), result
    for ob, (mesh, digest, uv) in zip(objects, original):
        assert ob.data == mesh and T.mesh_digest(mesh) == digest
        equal_uv(record(mesh), uv)
    assert {me.name for me in bpy.data.meshes} == original_meshes, 'failed write leaked a UV source or planned mesh'


def unsupported_chart_transform():
    objects, _ = pair()
    spec = design()
    build(spec)
    for ob in objects:
        ob.matrix_world = Matrix.Diagonal((1., 1.2, 1., 1.)) @ ob.matrix_world
    bpy.context.view_layer.update()
    before = state()
    result = T.faceted_temple(spec, decals=None)
    assert result['status'] == 'refused' and result['code'] == 'E_TARGET', result
    assert result['scene_unchanged'] and state() == before
    for ob in objects:
        for layer in list(ob.data.uv_layers):
            ob.data.uv_layers.remove(layer)
    build(spec)  # no UV chart remains to impose a transfer-frame restriction
    assert all(not ob.data.uv_layers for ob in objects)
    for ob in objects:
        assert T.unfaceted_temple(ob)['status'] == 'ok'


def shared_ownership_and_mesh_role():
    objects, _ = pair()
    spec = design()
    targets = {'+x': objects[0].name, '-x': objects[1].name}
    linked = bpy.data.objects.new('Unrelated linked mesh user', objects[0].data)
    bpy.context.scene.collection.objects.link(linked)
    linked.matrix_world = objects[0].matrix_world
    before = state()
    result = T.faceted_temple(spec, targets=targets, decals=None)
    assert result['status'] == 'refused' and result['code'] == 'E_TARGET', result
    assert result['scene_unchanged'] and state() == before
    bpy.data.objects.remove(linked, do_unlink=True)

    originals = [ob.data for ob in objects]
    for ob in objects:
        ob.data['partRole'] = ob['partRole']
        del ob['partRole']
    result = T.faceted_temple(spec, targets=targets, decals=None)
    assert result['status'] == 'ok', result
    duplicate = objects[0].copy()
    duplicate.data = objects[0].data.copy()
    duplicate.name = 'Duplicated backup owner'
    bpy.context.scene.collection.objects.link(duplicate)
    before = state()
    duplicate_targets = {**targets, '+x': duplicate.name}
    for selection in (targets, duplicate_targets):
        result = T.faceted_temple(spec, targets=selection, decals=None)
        assert result['status'] == 'refused' and result['code'] == 'E_TARGET', result
        assert result['scene_unchanged'] and state() == before
    for ob in (objects[0], duplicate):
        result = T.unfaceted_temple(ob)
        assert result['status'] == 'refused' and result['code'] == 'E_TARGET', result
        assert state() == before
    mesh = duplicate.data
    bpy.data.objects.remove(duplicate, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    for ob, original in zip(objects, originals):
        result = T.unfaceted_temple(ob)
        assert result['status'] == 'ok', result
        assert ob.data == original and 'partRole' not in ob and ob.data['partRole'] == 'temple'


def run():
    objects, _ = pair()
    original = [(ob.data, T.mesh_digest(ob.data), record(ob.data)) for ob in objects]
    original_meshes = {me.name for me in bpy.data.meshes}
    spec = design()
    build(spec)
    checks = {}

    for ob in objects:
        me = ob.data
        values = record(me)['values'][0]
        layer = me.uv_layers.new(name='g3d_print')
        layer.data.foreach_set('uv', np.asarray(values * .03125 + [.125, .25], np.float32).ravel())
        me.uv_layers.active_index = len(me.uv_layers) - 1
        layer.active_render = True
        layer.active_clone = True
    cycle(objects, spec)
    for ob in objects:
        assert record(ob.data)['names'] == ['SurfaceUV', 'DetailUV', 'g3d_print']
    checks['added_print_layer'] = True

    for ob in objects:
        layer = ob.data.uv_layers['SurfaceUV']
        values = np.array([item.uv[:] for item in layer.data])
        layer.data.foreach_set('uv', np.asarray(values + [16., -8.], np.float32).ravel())
    cycle(objects, spec)
    assert all(np.min(record(ob.data)['values'][0][:, 0]) > 15 for ob in objects)
    checks['edited_existing_layer'] = True

    for ob in objects:
        ob.data.uv_layers.remove(ob.data.uv_layers['DetailUV'])
    cycle(objects, spec)
    assert all(record(ob.data)['names'] == ['SurfaceUV', 'g3d_print'] for ob in objects)
    checks['deleted_one_layer'] = True

    for ob in objects:
        me = ob.data
        me.uv_layers.active_index = 0
        me.uv_layers[0].active_render = True
        me.uv_layers[0].active_clone = True
    cycle(objects, spec)
    assert all(record(ob.data)['active'] == 0 and record(ob.data)['render'] == [True, False]
               and record(ob.data)['clone'] == [True, False] for ob in objects)
    checks['active_flags'] = True

    for ob in objects:
        for layer in list(ob.data.uv_layers):
            ob.data.uv_layers.remove(layer)
    cycle(objects, spec)
    assert all(not ob.data.uv_layers for ob in objects)
    checks['deleted_all_layers'] = True

    for ob in objects:
        layer = ob.data.uv_layers.new(name='g3d_print')
        # Nonconstant coordinates make a stale pre-transform source observably wrong in the next two cases.
        world = [ob.matrix_world @ ob.data.vertices[loop.vertex_index].co for loop in ob.data.loops]
        layer.data.foreach_set('uv', np.asarray([[point.z / 150, point.y / 40] for point in world],
                                                np.float32).ravel())
    cycle(objects, spec)
    assert all(record(ob.data)['names'] == ['g3d_print'] for ob in objects)
    checks['recreated_after_deletion'] = True

    for ob in objects:
        ob.matrix_world = Matrix.Translation((0., .5, 0.)) @ ob.matrix_world
    bpy.context.view_layer.update()
    spec = shift_spec(spec, .5)
    cycle(objects, spec)
    check_known_uv(objects, dy=.5)
    checks['external_transform'] = True

    for ob in objects:
        local_delta = ob.matrix_world.to_3x3().inverted() @ Vector((0., .25, 0.))
        for vertex in ob.data.vertices:
            vertex.co += local_delta
        ob.data.update()
    spec = shift_spec(spec, .25)
    cycle(objects, spec)
    check_known_uv(objects, dy=.75)
    checks['external_geometry'] = True

    scale, dz = 1.1, 5.
    for ob in objects:
        ob.matrix_world = Matrix.Translation((0., 0., dz)) @ Matrix.Scale(scale, 4) @ ob.matrix_world
    bpy.context.view_layer.update()
    spec = scale_translate_spec(spec, scale, dz)
    cycle(objects, spec, input_uv_check=lambda current: check_known_uv(current, dy=.75, scale=scale, dz=dz))
    check_known_uv(objects, dy=.75, scale=scale, dz=dz)
    checks['external_scale_and_z_translation'] = True

    for ob, (mesh, digest, uv) in zip(objects, original):
        matrix = ob.matrix_world.copy()
        result = T.unfaceted_temple(ob)
        assert result['status'] == 'ok', result
        assert ob.data == mesh and T.mesh_digest(mesh) == digest and ob.matrix_world == matrix
        equal_uv(record(mesh), uv)
    assert {me.name for me in bpy.data.meshes} == original_meshes, 'generation datablocks leaked after unfacet'
    checks.update(original_unfacet_exact=True, generations_cleaned=True,
                  dry_run_and_variants_unchanged=True, new_generation_aba_exact=True, known_uv_after_transform=True)
    injected_write_failure()
    checks['failed_write_sources_cleaned'] = True
    unsupported_chart_transform()
    checks['unsupported_uv_frame_refused_without_mutation'] = True
    checks['empty_uv_generation_has_no_frame_restriction'] = True
    shared_ownership_and_mesh_role()
    checks['linked_mesh_and_duplicate_backup_refused_without_mutation'] = True
    checks['mesh_only_part_role_restored'] = True
    return {'status': 'ok', 'sides': 2, 'checks': checks}


if __name__ == '__main__':
    Path(sys.argv[-1]).write_text(json.dumps(run(), indent=2), encoding='utf-8')
