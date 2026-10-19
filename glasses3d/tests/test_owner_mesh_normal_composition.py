"""Document HEAD's normal-write behaviour and the future exact-local goals.

The Blender instrument uses dyadic UV labels for native-loop correspondence;
the exported-normal assertions do not infer scope from floating point distance.
Strict xfails retain the unsupported zero-drift and exact-composition goals.
Single-pick byte equality and truthful overlap reporting remain passing contracts.
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


_FUTURE_NORMAL_REASON = (
    'Exact local normal writes and ordered composition remain a future task; '
    'evidence: data/m12/codex/c2f1-normal-final-summary.json and '
    'data/m12/proofs/d1b/diagnosis/normal-composition.json')


def _future_exact_local_normal_goal(test):
    if __name__ == '__main__':
        return test
    return pytest.mark.xfail(strict=True, reason=_FUTURE_NORMAL_REASON)(test)


def _future_automatic_normal_goal(test):
    if __name__ == '__main__':
        return test
    return pytest.mark.parametrize('normal_composition', (
        'authored',
        pytest.param('automatic', marks=pytest.mark.xfail(strict=True, reason=_FUTURE_NORMAL_REASON)),
    ), indirect=True, scope='module')(test)


@_future_exact_local_normal_goal
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


@_future_exact_local_normal_goal
def test_local_shading_preserves_exported_normal_rows_outside_native_influence(normal_composition):
    root, manifest = normal_composition
    source = _snapshot(manifest, 'source')
    loops, normals = _exported(root, 'source')
    for operation in manifest['operations']:
        actual_loops, actual = _exported(root, operation['id'])
        assert np.array_equal(loops, actual_loops)
        untouched = ~np.isin(loops, _influence(source, operation))
        assert np.array_equal(actual[untouched], normals[untouched])


@_future_automatic_normal_goal
def test_two_disjoint_shading_previews_have_zero_native_and_exported_overlap(normal_composition):
    root, manifest = normal_composition
    source = np.asarray(_snapshot(manifest, 'source')['objects'][0]['corner_normals'])
    previews = [np.asarray(_snapshot(manifest, item)['objects'][0]['corner_normals']) for item in ('I1', 'I2')]
    assert not np.any(np.any(previews[0] != source, axis=1) & np.any(previews[1] != source, axis=1))
    _, source = _exported(root, 'source')
    previews = [_exported(root, item)[1] for item in ('I1', 'I2')]
    assert not np.any(np.any(previews[0] != source, axis=1) & np.any(previews[1] != source, axis=1))


@_future_exact_local_normal_goal
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


@_future_exact_local_normal_goal
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


@_future_exact_local_normal_goal
def test_sequential_native_shading_preserves_authored_intent_exactly(normal_composition):
    from glasses3d.g3d import owner_surface
    root, manifest = normal_composition
    source, sequential = _snapshot(manifest, 'source'), _snapshot(manifest, 'sequential')
    assert owner_surface.verify(source, sequential, manifest['operations'])['ok']
    combined = _snapshot(manifest, 'both')
    for key in ('corner_normals', 'normal_attributes', 'attributes', 'normal_intent'):
        assert sequential['objects'][0][key] == combined['objects'][0][key]
    assert (root/'sequential.glb').read_bytes() == (root/'both.glb').read_bytes()


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    arguments = sys.argv[sys.argv.index('--')+1:]
    _native_instrument(Path(arguments[0]), len(arguments) > 1 and arguments[1] == 'automatic')
