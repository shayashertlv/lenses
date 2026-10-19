"""Exact local normal writes and ordered composition on generic geometry.

The Blender instrument uses dyadic UV labels for native-loop correspondence;
the exported-normal assertions do not infer scope from floating point distance.
Native and exported values, single-pick bytes and truthful overlap are exact.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys


def _native_instrument(root, automatic=False):
    import bpy
    import numpy as np
    from glasses3d.g3d import export_glb, owner_mesh as M, owner_mesh_bpy
    from glasses3d.tests.blender.owner_surface import capture

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = .001
    scene['mdl_bridge_underside'] = [0., 0., 0.]
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=10.)
    obj = bpy.context.object
    obj.name = 'Normal composition sphere'
    obj['partRole'] = 'frame'
    mesh = obj.data
    mesh.shade_smooth()
    material = bpy.data.materials.new('Normal regression material')
    material.use_nodes = True
    mesh.materials.append(material)
    # Slight, deterministic authored normals everywhere expose re-encoding of
    # untouched data. This is a generic instrument, not product geometry.
    initial = np.asarray([normal.vector[:] for normal in mesh.corner_normals], float)
    initial += np.asarray([.021, -.013, .017])
    initial /= np.linalg.norm(initial, axis=1)[:, None]
    if not automatic:
        mesh.normals_split_custom_set(initial.tolist())
    uv = mesh.uv_layers.active or mesh.uv_layers.new(name='UVMap')
    uv.data.foreach_set('uv', np.column_stack(((np.arange(len(mesh.loops))+1)/4096.,
                                             np.full(len(mesh.loops), .25))).ravel())
    mesh.update()
    source, source_path = capture(root, 'source')
    row = source['objects'][0]
    operations = []
    coordinates = np.asarray(row['vertices_local'])
    for item, side in (('I1', 1), ('I2', -1)):
        vertices = np.flatnonzero(coordinates[:, 0]*side > 8.).tolist()
        faces = [index for index, polygon in enumerate(row['polygons'])
                 if set(vertices).intersection(polygon)]
        assert faces
        operations.append({'id': item, 'kind': 'repair_shading', 'target_ids': [row['id']],
            'scope': {'object_id': row['id'],
                      'vertex_weights': [{'vertex_id': vertex, 'weight': 1.} for vertex in vertices],
                      'fixed_vertex_ids': [], 'affected_polygon_ids': faces,
                      'normal_affected_polygon_ids': sorted(M.normal_faces(row, faces))},
            'params': {'strength': .6, 'mode': 'smooth'}, 'marking_attachments': []})
    assert not set(operations[0]['scope']['normal_affected_polygon_ids']) & set(
        operations[1]['scope']['normal_affected_polygon_ids'])
    seed = root/'source.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(seed))
    export_glb.export_glb(root/'source.glb')
    overlap = copy.deepcopy(operations[0])
    overlap['id'] = 'I3'
    overlap['params']['strength'] = .25
    cases = {'I1': [operations[0]], 'I2': [operations[1]], 'both': operations,
             'overlap-preview': [overlap], 'overlap': [operations[0], overlap],
             'single-repeat': [operations[0]]}
    manifest = {'source': str(source_path), 'operations': operations, 'overlap': overlap,
                'cases': {}}
    for name, selected in cases.items():
        bpy.ops.wm.open_mainfile(filepath=str(seed))
        result = owner_mesh_bpy.apply({'snapshot_path': str(source_path),
            'snapshot_sha256': M.digest(source), 'operations': selected}, root)
        manifest['cases'][name] = result
        if result['status'] == 'ok':
            after, path = capture(root, name)
            manifest['cases'][name].update(snapshot=str(path), fingerprint=after['fingerprint'])
            export_glb.export_glb(root/(name+'.glb'))
    for name, ordered in (('sequential', operations), ('sequential-overlap', [operations[0], overlap])):
        bpy.ops.wm.open_mainfile(filepath=str(seed))
        previous, previous_path = source, source_path
        for operation in ordered:
            applied = owner_mesh_bpy.apply({'snapshot_path': str(previous_path),
                'snapshot_sha256': M.digest(previous), 'operations': [operation]}, root)
            manifest['cases'][name] = applied
            if applied['status'] != 'ok':
                break
            previous, previous_path = capture(root, name+'-'+operation['id'])
        else:
            manifest['cases'][name].update(snapshot=str(previous_path), fingerprint=previous['fingerprint'])
            export_glb.export_glb(root/(name+'.glb'))
    _n1_native_extra_cases(root, seed, source, source_path, operations, manifest)
    (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


if __name__ != '__main__':
    import numpy as np
    import pytest

    @pytest.fixture(scope='module', params=('authored', 'automatic'))
    def normal_composition(tmp_path_factory, request):
        from glasses3d.g3d_host import blender
        if blender.blender_exe() is None:
            pytest.skip('Blender is not installed')
        root = tmp_path_factory.mktemp('normal-composition-'+request.param)
        result = blender.run(None, Path(__file__), [str(root), request.param], timeout_s=180)
        (root/'blender.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
        assert result.returncode == 0, result.stdout+result.stderr
        assert (root/'manifest.json').is_file(), result.stdout+result.stderr
        return root, json.loads((root/'manifest.json').read_text(encoding='utf-8'))


def _snapshot(manifest, name):
    if name != 'source':
        assert manifest['cases'][name]['status'] == 'ok', manifest['cases'][name]
    path = manifest['source'] if name == 'source' else manifest['cases'][name]['snapshot']
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _influence(source, operation):
    row = source['objects'][0]
    start = np.cumsum([0]+[len(poly) for poly in row['polygons']])
    return np.concatenate([np.arange(start[face], start[face+1])
                           for face in operation['scope']['normal_affected_polygon_ids']])


def _exported(root, name):
    from glasses3d.g3d_host import glb
    asset = glb.load(root/(name+'.glb'))
    assert len(asset.primitives) == 1
    primitive = asset.primitives[0]
    indices = primitive.triangles.reshape(-1)
    labels = primitive.uv0[indices, 0]*4096-1
    assert np.array_equal(labels, labels.astype(int))
    return labels.astype(int), primitive.normals[indices]


def _pick(root, manifest, name, item_id=None):
    operation = (manifest['overlap'] if name == 'overlap-preview' else
                 next(row for row in manifest['operations'] if row['id'] == name))
    return {'item_id': item_id or name, 'option_id': 'B', 'snapshot': _snapshot(manifest, name),
            'operations': [operation], 'export': {'path': str(root/(name+'.glb')),
                'sha256': hashlib.sha256((root/(name+'.glb')).read_bytes()).hexdigest(),
                'fingerprint': manifest['cases'][name]['fingerprint']}}


def test_local_shading_preserves_native_encoded_and_decoded_normals_outside_influence(normal_composition):
    _, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    before = source['objects'][0]
    prior_encoding = next((row for row in before['normal_attributes'] if row['name'] == 'custom_normal'), None)
    for operation in manifest['operations']:
        after = _snapshot(manifest, operation['id'])['objects'][0]
        untouched = np.ones(len(before['corner_normals']), bool)
        untouched[_influence(source, operation)] = False
        actual_encoding = next(row for row in after['normal_attributes'] if row['name'] == 'custom_normal')
        if prior_encoding is not None:
            assert actual_encoding['data_type'] == prior_encoding['data_type']
            encoded = prior_encoding['values']
            actual = actual_encoding['values']
            assert np.array_equal(np.asarray(actual)[untouched], np.asarray(encoded)[untouched])
        # Automatic sources have no raw layer to compare. Their captured
        # decoded vectors still must remain exact outside the declared influence.
        assert np.array_equal(np.asarray(after['corner_normals'])[untouched],
                              np.asarray(before['corner_normals'])[untouched])
        assert before['vertices_local'] == after['vertices_local']


def test_local_shading_preserves_exported_normal_rows_outside_native_influence(normal_composition):
    root, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    loops, normals = _exported(root, 'source')
    for operation in manifest['operations']:
        actual_loops, actual = _exported(root, operation['id'])
        assert np.array_equal(loops, actual_loops)
        untouched = ~np.isin(loops, _influence(source, operation))
        assert np.array_equal(actual[untouched], normals[untouched])


def test_two_disjoint_shading_previews_have_zero_native_and_exported_overlap(normal_composition):
    root, manifest = normal_composition
    source = np.asarray(_snapshot(manifest, 'source')['objects'][0]['corner_normals'])
    previews = [np.asarray(_snapshot(manifest, item)['objects'][0]['corner_normals']) for item in ('I1', 'I2')]
    assert not np.any(np.any(previews[0] != source, axis=1) & np.any(previews[1] != source, axis=1))
    _, source = _exported(root, 'source')
    previews = [_exported(root, item)[1] for item in ('I1', 'I2')]
    assert not np.any(np.any(previews[0] != source, axis=1) & np.any(previews[1] != source, axis=1))


def test_disjoint_shading_on_one_object_composes_to_each_preview_exactly(normal_composition):
    from glasses3d.agent.owner_apply import verify_choice_exactness, verify_choice_exports
    root, manifest = normal_composition
    selected = [_pick(root, manifest, name) for name in ('I1', 'I2')]
    native = verify_choice_exactness(_snapshot(manifest, 'source'), selected, _snapshot(manifest, 'both'),
                                    hashlib.sha256((root/'both.glb').read_bytes()).hexdigest())
    exported = verify_choice_exports(root/'source.glb', selected, root/'both.glb')
    assert native['ok'] and not native['overlaps']
    assert all(row['exact_preview_geometry'] for row in native['items'])
    assert exported['ok'] and not exported['overlaps']


def test_genuinely_overlapping_shading_picks_still_report_overlap(normal_composition):
    from glasses3d.agent.owner_apply import verify_choice_exactness, verify_choice_exports
    root, manifest = normal_composition
    selected = [_pick(root, manifest, 'I1'), _pick(root, manifest, 'overlap-preview', 'I3')]
    native = verify_choice_exactness(_snapshot(manifest, 'source'), selected, _snapshot(manifest, 'overlap'),
                                    hashlib.sha256((root/'overlap.glb').read_bytes()).hexdigest())
    exported = verify_choice_exports(root/'source.glb', selected, root/'overlap.glb')
    assert native['ok'] and native['overlaps'][0]['item_ids'] == ['I1', 'I3']
    assert all(row['overlap_reported'] and not row['exact_preview_geometry'] for row in native['items'])
    assert exported['ok'] and exported['overlaps'][0]['item_ids'] == ['I1', 'I3']


def test_sequential_overlapping_shading_matches_combined_plan_exactly(normal_composition):
    from glasses3d.g3d import owner_surface
    root, manifest = normal_composition
    sequential = _snapshot(manifest, 'sequential-overlap')
    assert owner_surface.verify(_snapshot(manifest, 'source'), sequential,
                                [manifest['operations'][0], manifest['overlap']])['ok']
    assert (root/'sequential-overlap.glb').read_bytes() == (root/'overlap.glb').read_bytes()


def test_single_shading_pick_retains_exact_preview_glb_bytes(normal_composition):
    from glasses3d.agent.owner_apply import verify_choice_exactness
    root, manifest = normal_composition
    assert (root/'I1.glb').read_bytes() == (root/'single-repeat.glb').read_bytes()
    result = verify_choice_exactness(_snapshot(manifest, 'source'), [_pick(root, manifest, 'I1')],
        _snapshot(manifest, 'single-repeat'), hashlib.sha256((root/'single-repeat.glb').read_bytes()).hexdigest())
    assert result['ok'] and result['mode'] == 'single_glb_bytes' and result['single_pick_glb_equal']


def test_sequential_native_shading_preserves_authored_intent_exactly(normal_composition):
    from glasses3d.g3d import owner_surface
    root, manifest = normal_composition
    source, sequential = _snapshot(manifest, 'source'), _snapshot(manifest, 'sequential')
    assert owner_surface.verify(source, sequential, manifest['operations'])['ok']
    combined = _snapshot(manifest, 'both')
    for key in ('corner_normals', 'normal_attributes', 'attributes', 'normal_intent'):
        assert sequential['objects'][0][key] == combined['objects'][0][key]
    assert (root/'sequential.glb').read_bytes() == (root/'both.glb').read_bytes()


def _n1_native_extra_cases(root, seed, source, source_path, operations, manifest):
    import bpy
    import numpy as np
    from glasses3d.g3d import owner_mesh as M, owner_mesh_bpy, owner_normals_bpy
    from glasses3d.tests.blender.owner_surface import capture

    bpy.ops.wm.open_mainfile(filepath=str(seed))
    result = owner_mesh_bpy.apply({'snapshot_path': str(source_path),
        'snapshot_sha256': M.digest(source), 'operations': [operations[0]]}, root)
    assert result['status'] == 'ok', result
    edited, _ = capture(root, 'n1-continuation-source')
    prior = edited['objects'][0]
    source_storage = next(row for row in prior['normal_attributes'] if row['name'] == 'custom_normal')
    automatic_name = 'g3d_owner_normal_automatic'
    managed_name = 'g3d_owner_normal_managed'
    source_automatic = any(row['name'] == automatic_name for row in prior['attributes'])

    # A new mesh emulates the attribute loss at a topology rebuild. Fan-triangulate
    # the generated sphere; no product or owner-delivery geometry is involved.
    polygons = [[poly[0], poly[i], poly[i + 1]] for poly in prior['polygons']
                for i in range(1, len(poly) - 1)]
    rebuilt = bpy.data.meshes.new('N1 private rebuilt normal storage')
    try:
        rebuilt.from_pydata(prior['vertices_local'], [], polygons)
        rebuilt.update()
        assert rebuilt.attributes.get('custom_normal') is None
        assert rebuilt.attributes.get(automatic_name) is None
        assert rebuilt.attributes.get(managed_name) is None
        intended = np.asarray([normal.vector[:] for normal in rebuilt.corner_normals], dtype=np.float32)
        owner_normals_bpy.write(rebuilt, intended, source=prior)
        witness = owner_normals_bpy.capture(rebuilt)
        mask = np.empty(len(rebuilt.loops), dtype=bool)
        rebuilt.attributes[managed_name].data.foreach_get('value', mask)
        marker = rebuilt.attributes.get(automatic_name)
        marker_values = np.ones(len(rebuilt.loops), dtype=bool)
        if marker is not None:
            marker.data.foreach_get('value', marker_values)
        manifest['full_rebuild_writer'] = {
            'topology_changed': polygons != prior['polygons'],
            'source_storage': source_storage['data_type'],
            'result_storage': rebuilt.attributes['custom_normal'].data_type,
            'corner_count': len(rebuilt.loops), 'managed_count': int(mask.sum()),
            'encoded_matches': witness['encoded_matches'],
            'normal_space_matches': witness['normal_space_matches'],
            'decoded_within_quantization': witness['decoded_within_quantization'],
            'source_automatic_marker_present': source_automatic,
            'automatic_marker_present': marker is not None,
            'automatic_marker_matches_managed': bool(np.array_equal(marker_values, mask)),
        }

        # Full-write authority remains complete even if a self-consistent witness
        # can be forged by clearing a row. The host test independently requires
        # every corner above; this records the actual writer-produced mask only.
    finally:
        bpy.data.meshes.remove(rebuilt)


def _n1_parametrize(*args, **kwargs):
    if __name__ == '__main__':
        return lambda function: function
    return pytest.mark.parametrize(*args, **kwargs)


def _n1_attribute(obj, name):
    rows = [row for row in obj['attributes'] if row['name'] == name]
    assert len(rows) == 1, name
    return rows[0]


def _n1_outside_loop(source, operation):
    # Derive the one-ring from original polygon connectivity, independently of
    # the planner's encoding mask or a witness's claim about its authority.
    obj = source['objects'][0]
    selected_vertices = {vertex for face in operation['scope']['affected_polygon_ids']
                         for vertex in obj['polygons'][face]}
    outside, cursor = [], 0
    for polygon in obj['polygons']:
        if not selected_vertices.intersection(polygon):
            outside.extend(range(cursor, cursor + len(polygon)))
        cursor += len(polygon)
    assert outside
    return outside[0]


def _n1_set_managed(obj, values):
    _n1_attribute(obj, 'g3d_owner_normal_managed')['values'] = [[bool(value)] for value in values]
    obj['normal_intent']['managed_loop_ids'] = [i for i, value in enumerate(values) if value]
    automatic = next((row for row in obj['attributes']
                      if row['name'] == 'g3d_owner_normal_automatic'), None)
    if automatic is not None:
        automatic['values'] = [[bool(value)] for value in values]


@_n1_parametrize('damage', ('mask', 'intent', 'stored_code', 'decoded_vector'))
def test_partial_normal_witness_cannot_authorize_outside_data(normal_composition, damage):
    from glasses3d.g3d import owner_mesh as M, owner_surface as S
    _, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    actual = _snapshot(manifest, 'I1')
    operation = manifest['operations'][0]
    assert S.verify(source, actual, [operation])['ok']
    outside = _n1_outside_loop(source, operation)
    hostile = copy.deepcopy(actual)
    obj = hostile['objects'][0]
    if damage == 'mask':
        values = [row[0] for row in _n1_attribute(obj, 'g3d_owner_normal_managed')['values']]
        assert values[outside] is False
        values[outside] = True
        _n1_set_managed(obj, values)
    elif damage == 'intent':
        obj['normal_intent']['intent_normals'][outside][0] += .125
        _n1_attribute(obj, 'g3d_owner_normal_intent')['values'][outside][0] += .125
    elif damage == 'stored_code':
        storage = next(row for row in obj['normal_attributes'] if row['name'] == 'custom_normal')
        storage['values'][outside][0] += 1 if storage['data_type'] == 'INT16_2D' else .125
    else:
        obj['corner_normals'][outside][0] += .125
    # The forged witness keeps its positive booleans. The host must derive scope
    # itself rather than trusting these claims.
    assert obj['normal_intent']['encoded_matches'] is True
    with pytest.raises(M.OwnerMeshError):
        S.verify(source, hostile, [operation])


@_n1_parametrize('damage', ('all_false', 'all_true', 'wrong_domain', 'missing_intent'))
def test_automatic_marker_never_grants_its_own_authority(normal_composition, damage):
    from glasses3d.g3d import owner_mesh as M, owner_surface as S
    _, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    actual = _snapshot(manifest, 'I1')
    operation = manifest['operations'][0]
    assert S.verify(source, actual, [operation])['ok']
    hostile = copy.deepcopy(actual)
    obj = hostile['objects'][0]
    marker = next((row for row in obj['attributes']
                   if row['name'] == 'g3d_owner_normal_automatic'), None)
    if marker is None:
        marker = {'name': 'g3d_owner_normal_automatic', 'domain': 'CORNER',
                  'data_type': 'BOOLEAN', 'values': [[False] for _ in obj['corner_normals']]}
        obj['attributes'].append(marker)
    if damage == 'all_false':
        marker['values'] = [[False] for _ in obj['corner_normals']]
    elif damage == 'all_true':
        marker['values'] = [[True] for _ in obj['corner_normals']]
    elif damage == 'wrong_domain':
        marker['domain'] = 'POINT'
    else:
        obj['normal_intent'] = None
        obj['attributes'] = [row for row in obj['attributes']
                             if row['name'] not in ('g3d_owner_normal_intent', 'g3d_owner_normal_managed')]
    with pytest.raises(M.OwnerMeshError):
        S.verify(source, hostile, [operation])


def _n1_material_round(source):
    from glasses3d.g3d import owner_surface as S
    source = copy.deepcopy(source)
    # This test concerns the pure host authority check, not a simulated native
    # material datablock allocation. Actual native shading data are retained.
    source.pop('scene_state', None)
    obj = source['objects'][0]
    operation = {'id': 'material-no-normal-write', 'kind': 'material_adjust',
        'target_ids': [obj['id']], 'scope': {'object_id': obj['id'],
        'vertex_weights': [{'vertex_id': i, 'weight': 1.} for i in range(len(obj['vertices_local']))],
        'fixed_vertex_ids': [], 'affected_polygon_ids': list(range(len(obj['polygons'])))},
        'params': {'strength': 1., 'material_index': 0, 'values': {'roughness': .25}},
        'marking_attachments': []}
    target, _ = S.state(source, [operation])
    assert S.verify(source, target, [operation])['ok']
    return source, target, operation


@_n1_parametrize('damage', ('all_false', 'all_true', 'missing_mask', 'missing_witness'))
def test_material_only_edit_cannot_change_prior_normal_authority(normal_composition, damage):
    from glasses3d.g3d import owner_mesh as M, owner_surface as S
    _, manifest = normal_composition
    source, actual, operation = _n1_material_round(_snapshot(manifest, 'I1'))
    before, after = source['objects'][0], actual['objects'][0]
    assert before['corner_normals'] == after['corner_normals']
    assert before['normal_attributes'] == after['normal_attributes']
    assert before['normal_intent'] == after['normal_intent']
    hostile = copy.deepcopy(actual)
    obj = hostile['objects'][0]
    if damage in ('all_false', 'all_true'):
        _n1_set_managed(obj, [damage == 'all_true'] * len(obj['corner_normals']))
    elif damage == 'missing_mask':
        obj['attributes'] = [row for row in obj['attributes'] if row['name'] != 'g3d_owner_normal_managed']
    else:
        obj['normal_intent'] = None
    with pytest.raises(M.OwnerMeshError):
        S.verify(source, hostile, [operation])


def test_material_only_edit_rejects_orphan_all_false_automatic_marker(normal_composition):
    from glasses3d.g3d import owner_mesh as M, owner_surface as S
    _, manifest = normal_composition
    source, actual, operation = _n1_material_round(_snapshot(manifest, 'source'))
    hostile = copy.deepcopy(actual)
    obj = hostile['objects'][0]
    assert obj.get('normal_intent') is None
    obj['attributes'].append({'name': 'g3d_owner_normal_automatic', 'domain': 'CORNER',
        'data_type': 'BOOLEAN', 'values': [[False] for _ in obj['corner_normals']]})
    with pytest.raises(M.OwnerMeshError):
        S.verify(source, hostile, [operation])


def test_full_rebuilt_write_retains_source_storage_and_complete_authority(normal_composition):
    _, manifest = normal_composition
    record = manifest['full_rebuild_writer']
    assert record['topology_changed'] is True
    assert record['source_storage'] == record['result_storage']
    assert record['managed_count'] == record['corner_count']
    assert record['encoded_matches'] is True
    assert record['normal_space_matches'] is True
    assert record['decoded_within_quantization'] is True
    assert record['automatic_marker_present'] == record['source_automatic_marker_present']
    assert record['automatic_marker_matches_managed'] is True


@_n1_parametrize('storage_type', ('INT16_2D', 'FLOAT_VECTOR', None))
def test_noop_shading_does_not_split_existing_coplanar_fans(storage_type):
    from glasses3d.g3d import owner_normal_plan as N, owner_surface as S
    from glasses3d.tests.test_owner_surface import patch
    source, operation = patch(False)
    obj = source['objects'][0]
    obj['edges'] = sorted({tuple(sorted((a, b))) for polygon in obj['polygons']
                           for a, b in zip(polygon, polygon[1:] + polygon[:1])})
    adjacent = {edge: [] for edge in obj['edges']}
    for face, polygon in enumerate(obj['polygons']):
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            adjacent[tuple(sorted((a, b)))].append(face)
    vectors = S.face_vectors(np.asarray(obj['vertices_local']), obj['polygons'])
    # Only genuine geometric face boundaries are sharp. Coplanar top/bottom and
    # side subdivisions intentionally retain connected normal fans.
    sharp = {edge for edge, faces in adjacent.items()
             if len(faces) == 2 and not np.array_equal(vectors[faces[0]], vectors[faces[1]])}
    assert any(edge not in sharp for edge in obj['edges'])
    obj['attributes'].append({'name': 'sharp_edge', 'domain': 'EDGE', 'data_type': 'BOOLEAN',
                              'values': [[edge in sharp] for edge in obj['edges']]})
    obj['normal_attributes'] = [] if storage_type is None else [{
        'name': 'custom_normal', 'domain': 'CORNER', 'data_type': storage_type,
        'field': 'value' if storage_type == 'INT16_2D' else 'vector',
        'values': ([[0, 0] for _ in obj['corner_normals']] if storage_type == 'INT16_2D'
                   else copy.deepcopy(obj['corner_normals']))}]
    obj['has_custom_normals'] = storage_type is not None
    operation.update(kind='repair_shading', params={'strength': 1., 'mode': 'flat'})
    before = copy.deepcopy(source)
    target, _ = S.state(source, [operation])
    after = target['objects'][0]
    assert source == before
    assert after['corner_normals'] == obj['corner_normals']
    assert after['polygon_smooth'] == obj['polygon_smooth']
    assert N.sharp_edges(after) == sharp
    assert N.encoding_loops(obj, [operation], target=after) == set()
    assert S.verify(source, target, [operation])['ok']


def _n1_guard_context(root, manifest):
    source = _snapshot(manifest, 'source')
    path = Path(manifest['source'])
    relative = path.relative_to(root).as_posix()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ({'source': {'fingerprint': source['fingerprint']}},
            {'snapshot_path': relative, 'evidence_files': {relative: digest}})


def _n1_guard_refusal(first='I1', second='I2'):
    return (f'Items {first} and {second} both change the shading of the same part, '
        'which cannot be combined exactly yet. Pick an option for one of them now '
        '(choose None of these for the other) and send the other again as a new edit.')


def test_early_guard_allows_verified_native_disjoint_shading(normal_composition):
    from glasses3d.agent import owner_choices
    from glasses3d.g3d import owner_surface as S
    root, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    selected = copy.deepcopy([_pick(root, manifest, name) for name in ('I1', 'I2')])
    doc, understanding = _n1_guard_context(root, manifest)
    for pick in selected:
        assert S.verify(source, pick['snapshot'], pick['operations'])['ok']
    source_bytes = Path(manifest['source']).read_bytes()
    original = copy.deepcopy(selected)
    assert owner_choices._require_composable_picks(root, doc, understanding, selected) is None
    assert selected == original
    assert Path(manifest['source']).read_bytes() == source_bytes


def test_early_guard_keeps_native_overlapping_shading_refusal(normal_composition):
    from glasses3d.agent import owner_choices
    from glasses3d.g3d import owner_surface as S
    root, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    selected = copy.deepcopy([_pick(root, manifest, 'I1'), _pick(root, manifest, 'overlap-preview', 'I3')])
    doc, understanding = _n1_guard_context(root, manifest)
    for pick in selected:
        assert S.verify(source, pick['snapshot'], pick['operations'])['ok']
    with pytest.raises(ValueError) as error:
        owner_choices._require_composable_picks(root, doc, understanding, selected)
    assert str(error.value) == _n1_guard_refusal('I1', 'I3')


@_n1_parametrize('damage', ('legacy_writer_record', 'outside_decoded', 'outside_storage',
                           'geometry_change', 'narrowed_declared_influence'))
def test_early_guard_rejects_unproved_or_changed_native_shading(normal_composition, damage):
    from glasses3d.agent import owner_choices
    root, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    selected = copy.deepcopy([_pick(root, manifest, name) for name in ('I1', 'I2')])
    doc, understanding = _n1_guard_context(root, manifest)
    hostile = selected[0]
    obj = hostile['snapshot']['objects'][0]
    outside = _n1_outside_loop(source, hostile['operations'][0])
    if damage == 'legacy_writer_record':
        # The former complete writer's record shape claims full canonical
        # authority. Merely deleting the new marker must not opt out of the proof.
        obj['attributes'] = [row for row in obj['attributes'] if row['name'] not in
            ('g3d_owner_normal_managed', 'g3d_owner_normal_automatic')]
        obj['normal_intent'].pop('managed_loop_ids', None)
        obj['normal_intent'].pop('unmanaged_matches', None)
    elif damage == 'outside_decoded':
        obj['corner_normals'][outside][0] += .125
    elif damage == 'outside_storage':
        storage = next(row for row in obj['normal_attributes'] if row['name'] == 'custom_normal')
        storage['values'][outside][0] += 1 if storage['data_type'] == 'INT16_2D' else .125
    elif damage == 'geometry_change':
        obj['vertices_local'][0][0] += .125
        obj['vertices_world_mm'][0][0] += .125
    else:
        hostile['operations'][0]['scope']['normal_affected_polygon_ids'] = []
    with pytest.raises(ValueError) as error:
        owner_choices._require_composable_picks(root, doc, understanding, selected)
    assert str(error.value) == _n1_guard_refusal()


def test_early_guard_cannot_hide_real_overlap_with_declared_empty_influence(normal_composition):
    from glasses3d.agent import owner_choices
    from glasses3d.g3d import owner_surface as S
    root, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    selected = copy.deepcopy([_pick(root, manifest, 'I1'), _pick(root, manifest, 'overlap-preview', 'I3')])
    doc, understanding = _n1_guard_context(root, manifest)
    selected[1]['operations'][0]['scope']['normal_affected_polygon_ids'] = []
    # The native operation verifier derives actual influence itself. This proves
    # the guard must derive it as well instead of trusting the declared shortcut.
    assert S.verify(source, selected[1]['snapshot'], selected[1]['operations'])['ok']
    with pytest.raises(ValueError) as error:
        owner_choices._require_composable_picks(root, doc, understanding, selected)
    assert str(error.value) == _n1_guard_refusal('I1', 'I3')


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    arguments = sys.argv[sys.argv.index('--')+1:]
    _native_instrument(Path(arguments[0]), len(arguments) > 1 and arguments[1] == 'automatic')
