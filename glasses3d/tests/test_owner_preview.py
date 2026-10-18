"""Amount choice authority, source isolation and native evidence; no provider or Blender needed."""
import copy
import json
from pathlib import Path
import shutil
from concurrent.futures import Future
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from pydantic import ValidationError

from glasses3d.agent import edits, owner_edits
from glasses3d.agent.tools import owner_preview as P
from glasses3d.g3d import owner_markings
from glasses3d.g3d_host.camera import Camera
from glasses3d.tests.test_owner_markings import fixture as marked_fixture
from glasses3d.tests.agent.test_job_tools import scene as sdk_scene, invoke, record


CHOICES = [{'id': 'small', 'label': 'Less', 'scale': .5}, {'id': 'large', 'label': 'More', 'scale': 1.5}]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def capture(root, export, call_id, *, refs=None, basis=None):
    folder = root/'work'/call_id
    folder.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[:240, :320]
    photo = np.stack([x % 256, y % 256, (x+y) % 256], axis=2).astype('uint8')
    Image.fromarray(photo).save(folder/'photo.png')
    drawing = photo.copy(); drawing[80:110, 140:165] = [12, 34, 56]
    Image.fromarray(drawing).save(folder/'drawing.png')
    result = {'status': 'ok', 'basis': basis, 'views': [{'ref': 'ref01', 'view_kind': 'side',
              'camera': {'width': 320, 'height': 240}, 'photo_path': str(folder/'photo.png'),
              'drawing_path': str(folder/'drawing.png')}], 'capture_reports': []}
    write(folder/'report.json', result)
    return {**result, 'report': str(folder/'report.json')}


def export(root):
    path = root/'work/export.glb'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'strict local test export')
    return {'status': 'exported', 'path': str(path), 'sha256': edits.sha(path),
            'validate_spec': {'ok': True}, 'print_check': {'ok': True}, 'seat_check': {'glb': str(path)}}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    source, operation = marked_fixture()
    source['fingerprint'] = 'unchanged-source'
    root = tmp_path/'job'; root.mkdir()
    snapshot_path = write(root/'work/model_maps/source.json', source)
    map_path = write(root/'work/model_maps/map.json', {'map_id': 'test-map'})
    checkpoint = root/'checkpoints/source.blend'; checkpoint.parent.mkdir()
    checkpoint.write_bytes(b'exact source checkpoint')
    head = {'id': 'source-head', 'file': 'source.blend', 'sha256': edits.sha(checkpoint), 'fingerprint': source['fingerprint']}
    doc = {'phase': 'understand', 'source': {'fingerprint': source['fingerprint'], 'basis': 'basis-01'}}
    write(root/'job.json', {'source': 'local test'})
    write(root/'images/photo.json', {'original': True})
    write(root/'cameras/basis.json', {'frozen': True})
    write(root/'inputs/owner-edit.json', {'not_copied': True})
    evidence = {'operations': [operation], 'source': doc['source'], 'snapshot_path': str(snapshot_path),
                'map_path': map_path.relative_to(root).as_posix(), 'map_sha256': edits.sha(map_path),
                'evidence_files': [{'path': snapshot_path.relative_to(root).as_posix(), 'sha256': edits.sha(snapshot_path)}],
                'legend': [{'target_id': 'F1', 'label': 'T1', 'description': 'Outer face'}]}
    scene = SimpleNamespace(root=root, _unseen=set(),
                            s=SimpleNamespace(head=head, fingerprint=lambda: head['fingerprint']),
                            seeing=SimpleNamespace(export_head=lambda *args: export(root)))
    monkeypatch.setattr(edits, 'load', lambda folder: doc if Path(folder) == root else None)
    monkeypatch.setattr(P.jobs, 'load_job', lambda folder: {'local': True})
    monkeypatch.setattr(P.model_map, 'validate_understanding', lambda *args: copy.deepcopy(evidence))
    monkeypatch.setattr(P.model_map.G, 'project', lambda xyz, camera:
                        (np.column_stack([xyz[:, 0]*10+100, -xyz[:, 2]*10+60]), np.ones(len(xyz))))
    monkeypatch.setattr(P.look, 'capture', capture)
    monkeypatch.setattr(P.look.C, 'load_basis', lambda *args: SimpleNamespace(
        usable_refs=lambda: ['ref01'], refs={'ref01': {'view_kind': 'side'}},
        camera=lambda rid: Camera(90, 0, 0, 0, 1000, 160, 120), pose=lambda rid: None))
    monkeypatch.setattr(P.look.C, 'ref_id', lambda value: value)
    class InlinePool:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, fn, *args):
            future = Future()
            try: future.set_result(fn(*args))
            except Exception as error: future.set_exception(error)
            return future
    monkeypatch.setattr(P, 'ProcessPoolExecutor', InlinePool)
    calls = []
    real_run_option = P._run_option

    def fake_run(scene, folder, checkpoint, head, source, operations, refs, basis):
        calls.append(copy.deepcopy(operations))
        seed = P._prepare(scene, folder, checkpoint, head)
        assert seed.read_bytes() == checkpoint.read_bytes()
        assert not (folder/'inputs').exists()
        after = owner_edits._advance(source, operations)
        checks = owner_edits.verify_snapshots(source, after, operations)
        rendered = export(folder)
        before_path = write(folder/'work/before.json', source)
        after_path = write(folder/'work/after.json', after)
        return {'checks': {**checks, 'confirmed_amounts_match': True, 'strict_spec': {'ok': True}},
                'after_evidence': capture(folder, rendered, 'option', refs=refs, basis=basis),
                'export': rendered, 'snapshot': after,
                'before_snapshot': {'path': str(before_path), 'sha256': edits.sha(before_path)},
                'after_snapshot': {'path': str(after_path), 'sha256': edits.sha(after_path)},
                'seed_sha256': edits.sha(seed), 'initial_fingerprint': source['fingerprint'],
                'private_has_no_owner_edit': True}

    monkeypatch.setattr(P, '_run_option', fake_run)
    return SimpleNamespace(root=root, scene=scene, source=source, op=operation, evidence=evidence, doc=doc,
                           checkpoint=checkpoint, head=head, calls=calls, real_run_option=real_run_option)


def test_depth_preview_adds_top_rear_and_sides_without_keyword_mapping(monkeypatch):
    refs = {name: {'view_kind': kind} for name, kind in
            [('f','front'), ('s1','side'), ('s2','side'), ('t','top'), ('r','rear')]}
    cameras = {key: Camera(yaw, pitch, 0, 0, 1000, 160, 120) for key, yaw, pitch in
               [('f', 0, 0), ('s1', 90, 0), ('s2', -90, 0), ('t', 0, 90), ('r', 180, 0)]}
    monkeypatch.setattr(P.look.C, 'load_basis', lambda *args: SimpleNamespace(
        usable_refs=lambda: list(refs), refs=refs, camera=lambda rid: cameras[rid]))
    monkeypatch.setattr(P.look.C, 'ref_id', lambda value: value)
    op = {'kind':'translate_region','params':{'delta_mm':[3,0,0]}}
    assert P.revealing_refs(None, None, [op], ['s1','s2']) == ['s1','s2','f','t','r']
    op['params']['delta_mm'] = [0,0,3]
    assert P.revealing_refs(None, None, [op], ['f']) == ['f','s1','s2','t']


def test_preview_pool_scales_to_available_memory_and_task_count(monkeypatch):
    from glasses3d.agent import tools
    monkeypatch.setattr(tools, 'free_commit_bytes', lambda: 4*2**30)
    assert P.preview_workers(20) == 3
    monkeypatch.setattr(tools, 'free_commit_bytes', lambda: 100*2**30)
    assert P.preview_workers(20) == 14
    assert P.preview_workers(3) == 3


def test_projection_reports_hidden_depth_in_side_view():
    # A lateral change is optically absent when viewed along X, but visible along Z.
    source = {'schema':'g3d.mesh_snapshot/1','objects':[{'id':'O','vertices_world_mm':[[0,0,0]], 'side':0, 'role':'frame'}]}
    after = copy.deepcopy(source); after['objects'][0]['vertices_world_mm']=[[3,0,0]]
    cameras = [{'ref':'front','view_kind':'front','camera':{'view':[1,0,0,0, 0,1,0,0, 0,0,1,-1, 0,0,0,1], 'fx':1000,'fy':1000,'cx':0,'cy':0}},
               {'ref':'side','view_kind':'side','camera':{'view':[0,0,1,0, 0,1,0,0, -1,0,0,-1, 0,0,0,1], 'fx':1000,'fy':1000,'cx':0,'cy':0}}]
    op={'id':'move','scope':{'object_id':'O','vertex_weights':[{'vertex_id':0,'weight':1}]}}
    rows=P.projection_effects(source,[{'id':'B','snapshot':after}],[op],{'views':cameras})
    assert rows[0]['max_displacement_px'] == pytest.approx(3)
    assert rows[1]['max_displacement_px'] == pytest.approx(0)


def preview(h, *, call_id='preview', operations=None, choices=None):
    return P.preview(h.scene, call_id, 'test-map', h.evidence['map_sha256'], ['shown-map.json'],
                     json.dumps(operations or [h.op]), choices or CHOICES, refs=['ref01'])


def test_diagnostic_empty_operations_cannot_start_a_preview_or_private_worker(harness):
    h = harness
    before = edits.tree_hashes(h.root)
    with pytest.raises(ValueError, match='nonempty list'):
        P.preview(h.scene, 'diagnostic', 'test-map', h.evidence['map_sha256'], ['shown-map.json'],
                  '[]', CHOICES)
    assert not h.calls and edits.tree_hashes(h.root) == before


def test_scale_changes_only_amount_and_keeps_input_exact():
    source, op = marked_fixture()
    operations = owner_markings.seal(source, [op])
    taper = copy.deepcopy(operations[0]); taper.update(id='taper', kind='taper_region')
    taper['params']['axis_points_mm'] = [[5, 0, 0], [5, 0, -6]]
    rotate = copy.deepcopy(taper); rotate.update(id='rotate', kind='rotate_region')
    rotate['params'] = {'axis_points_mm': [[5, 0, 0], [5, 0, -6]], 'delta_deg': 2.}
    operations += [taper, rotate]
    original = copy.deepcopy(operations)
    actual = P.scaled_operations(operations, .5)
    assert operations == original
    for old, new in zip(original, actual):
        assert {k: v for k, v in old.items() if k != 'params'} == {k: v for k, v in new.items() if k != 'params'}
        if old['kind'] == 'rotate_region':
            assert new['params'] == {**old['params'], 'delta_deg': 1.}
        else:
            assert new['params'] == {**old['params'], 'delta_mm': [.25, 0., 0.]}


@pytest.mark.parametrize('invalid', [[], CHOICES[:1], CHOICES*2, None, [None, CHOICES[1]],
    [{**CHOICES[0], 'id': '../bad'}, CHOICES[1]], [{**CHOICES[0], 'scale': True}, CHOICES[1]],
    [{**CHOICES[0], 'scale': 0}, CHOICES[1]], [{**CHOICES[0], 'scale': float('inf')}, CHOICES[1]],
    [{**CHOICES[0], 'label': ' '}, CHOICES[1]], [{**CHOICES[0], 'extra': 1}, CHOICES[1]],
    [CHOICES[0], {**CHOICES[1], 'id': 'SMALL'}], [CHOICES[0], {**CHOICES[1], 'scale': .5}]])
def test_invalid_option_sets_refuse_without_rendering(invalid):
    with pytest.raises(ValueError):
        P._options(invalid)


@pytest.mark.parametrize('scale', [True, float('nan'), float('inf'), 0, -1])
def test_tool_option_schema_rejects_invalid_scale(scale):
    with pytest.raises(ValidationError):
        P.PreviewOption(id='small', label='Less', scale=scale)


def test_native_previews_are_same_source_exact_amounts_and_follow_marks(harness):
    h = harness
    original_head, original_bytes = copy.deepcopy(h.head), h.checkpoint.read_bytes()
    result = preview(h)
    assert result['schema'] == P.SCHEMA and result['source_unchanged']
    assert h.head == original_head and h.checkpoint.read_bytes() == original_bytes
    assert len(h.calls) == 2
    assert [ops[0]['params']['delta_mm'][0] for ops in h.calls] == [.25, .75]
    assert result['following_markings'][0]['object_name'] == 'Surface mark'
    assert all(row['checks']['confirmed_amounts_match'] for row in result['options'])
    first, second = result['options']
    assert first['target_details'][0]['box'] == second['target_details'][0]['box']
    assert 'T1' in first['target_details'][0]['view']
    full = Image.open(first['full_pairs'][0]['path'])
    assert full.width == 960  # exact 320 x 240 panels plus readable label band
    photo = Image.open(result['before_evidence']['views'][0]['photo_path'])
    assert full.crop(first['full_pairs'][0]['panel_boxes'][0]).tobytes() == photo.tobytes()
    detail = first['target_details'][0]
    x, y = detail['panel_content_origin_px']; w, _ = detail['source_panel_size_px']
    np.testing.assert_array_equal(np.asarray(Image.open(detail['path']))[y:, x:x+w],
                                  np.asarray(photo.crop(detail['box'])))
    assert not any('checkpoint' in path or path.endswith('.blend') for path in result['evidence_files'])
    assert all(not Path(path).is_absolute() for path in result['evidence_files'])
    P.mark_delivered(h.scene, result, True)
    verified = P.load_verified(h.scene, result['preview_receipt'])
    assert verified['preview_sha256'] == result['preview_sha256']
    assert verified['options'][1]['operations'] == h.calls[1]
    assert all(Path(row['path']).parent == h.root/'work/pv-assets' for row in result['images'])
    assert all(Path(option[key]['path']).is_file() for option in result['options']
               for key in ('before_snapshot', 'after_snapshot'))
    assert all(row['path'].startswith('work/pv-assets/') and len(row['path']) <= 44
               for row in result['archived_evidence'])
    assert all(not Path(row['source_path']).exists() for row in result['archived_evidence']
               if 'g3d-pv-' in row['source_path'])


@pytest.mark.parametrize('problem', ['duplicate_id', 'zero', 'float32_rounding', 'cancelled'])
def test_nondistinct_choices_refuse_before_export_or_worker(harness, problem):
    h = harness; operations = [copy.deepcopy(h.op)]
    if problem == 'duplicate_id':
        operations *= 2
    elif problem == 'zero':
        operations[0]['params']['delta_mm'] = [0, 0, 0]
    elif problem == 'float32_rounding':
        operations[0]['params']['delta_mm'] = [1e-15, 0, 0]
    else:
        other = copy.deepcopy(h.op); other['id'] = 'opposite'; other['params']['delta_mm'] = [-.5, 0, 0]
        operations.append(other)
    h.evidence['operations'] = operations
    with pytest.raises(ValueError, match='distinct'):
        preview(h, operations=operations)
    assert not h.calls and not (h.root/'work/export.glb').exists()


def test_failed_option_never_leaves_selectable_receipt_and_source_is_exact(harness, monkeypatch):
    h = harness; original = copy.deepcopy(h.head); payload = h.checkpoint.read_bytes()
    temporary = []; constructor = P.tempfile.TemporaryDirectory
    def traced(*args, **kwargs):
        directory = constructor(*args, **kwargs); temporary.append(Path(directory.name))
        return directory
    monkeypatch.setattr(P.tempfile, 'TemporaryDirectory', traced)
    def fail(*args):
        raise ValueError('private worker refusal')
    monkeypatch.setattr(P, '_run_option', fail)
    with pytest.raises(ValueError, match='private worker refusal'):
        preview(h)
    assert h.head == original and h.checkpoint.read_bytes() == payload
    assert not list((h.root/'work/pv').rglob('preview.json'))
    assert temporary and all(not path.exists() for path in temporary)


@pytest.mark.parametrize('problem', ['undelivered', 'receipt', 'image', 'raw_image', 'source_head', 'source_bytes',
                                     'unseen_memory', 'unseen_disk', 'changed_map'])
def test_confirmation_refuses_unseen_stale_or_tampered_evidence(harness, monkeypatch, problem):
    h = harness; result = preview(h); P.mark_delivered(h.scene, result, True)
    if problem == 'undelivered':
        P.mark_delivered(h.scene, result, False)
    elif problem == 'receipt':
        path = h.root/result['preview_receipt']; path.write_text(path.read_text()+' ')
    elif problem == 'image':
        Path(result['images'][0]['path']).write_bytes(b'changed')
    elif problem == 'raw_image':
        Path(result['options'][0]['after_evidence']['views'][0]['drawing_path']).write_bytes(b'changed')
    elif problem == 'source_head':
        h.head['id'] = 'another-head'
    elif problem == 'source_bytes':
        h.checkpoint.write_bytes(b'changed')
    elif problem == 'unseen_memory':
        h.scene._unseen.add(result['call_id'])
    elif problem == 'unseen_disk':
        write(h.root/'work/job_scene/state.json', {'unseen': [result['call_id']]})
    else:
        def changed(*args):
            raise ValueError('map is stale')
        monkeypatch.setattr(P.model_map, 'validate_understanding', changed)
    with pytest.raises(ValueError):
        P.load_verified(h.scene, result['preview_receipt'])


def test_forged_option_direction_is_not_accepted_even_with_rewritten_delivery_hash(harness):
    h = harness; result = preview(h)
    path = h.root/result['preview_receipt']; receipt = edits.read(path)
    receipt['options'][0]['operations'][0]['params']['delta_mm'][1] = 1
    write(path, receipt); result['preview_sha256'] = edits.sha(path)
    P.mark_delivered(h.scene, result, True)
    with pytest.raises(ValueError, match='more than'):
        P.load_verified(h.scene, result['preview_receipt'])


def test_source_edit_phase_or_changed_fingerprint_refuses(harness):
    h = harness; h.doc['phase'] = 'edit'
    with pytest.raises(ValueError, match='understand phase'):
        preview(h)
    h.doc['phase'] = 'understand'; h.scene.s.fingerprint = lambda: 'other'
    with pytest.raises(ValueError, match='unchanged selected'):
        preview(h)


def test_native_tiles_do_not_resize_pixels(harness, monkeypatch):
    h = harness; rendered = capture(h.root, {}, 'tile-test')
    row = rendered['views'][0]
    monkeypatch.setattr(P.native_panels, 'HIGH_PATCH_BUDGET', 100)
    full, tiles = P._triple([row['photo_path'], row['drawing_path'], row['drawing_path']],
                           h.root/'tiled.png', 'ref01', 'small: Less')
    restored = Image.new('RGB', Image.open(row['photo_path']).size)
    assert len(tiles) > 1
    for tile in tiles:
        part = Image.open(tile['path']); assert tile['patches_32px'] <= 100
        restored.paste(part.crop(tile['panel_boxes'][0]), tile['crop_box'][:2])
    assert restored.tobytes() == Image.open(row['photo_path']).tobytes()


@pytest.mark.parametrize('width', [12, 24, 80])
@pytest.mark.parametrize('option_id', ['B', 'A'*32])
def test_narrow_panels_keep_legible_headers_inside_columns_and_native_pixels(tmp_path, width, option_id):
    path = tmp_path/'source.png'; Image.new('RGB', (width, 90), '#123456').save(path)
    record, _ = P._triple([path]*3, tmp_path/'triple.png', 'Very long reference description',
                          option_id+': A very long amount label', option_id=option_id)
    assert record['headers'] == ['PHOTO', 'BEFORE', 'OPTION '+option_id]
    assert 'Very long reference' in record['view'] and 'very long amount' in record['view']
    with Image.open(record['path']) as im:
        column = record['panel_width_px']; x, y = record['panel_content_origin_px']
        assert column >= 120 and column >= width and im.size == (3*column, y+90)
        assert all(0 <= a < c <= im.width and 0 <= b < d <= y for a,b,c,d in record['label_boxes'])
        for box in record['panel_boxes']:
            assert im.crop(box).tobytes() == Image.open(path).tobytes()


def test_archive_is_confined_flat_exact_and_refuses_prefix_collision(tmp_path):
    source = tmp_path/'work/nested/raw.png'; source.parent.mkdir(parents=True)
    source.write_bytes(b'raw image evidence')
    record = P._archive_evidence(tmp_path, source)
    archived = tmp_path/record['path']
    assert archived.read_bytes() == source.read_bytes() and len(record['path']) == 43
    assert P._archive_evidence(tmp_path, source) == record
    archived.write_bytes(b'collision or tamper')
    with pytest.raises(ValueError, match='collision'):
        P._archive_evidence(tmp_path, source)
    assert archived.read_bytes() == b'collision or tamper'
    outside = tmp_path.parent/'outside.json'; outside.write_text('{}')
    with pytest.raises(ValueError):
        P._archive_evidence(tmp_path, outside)


def test_long_ids_and_nested_confirmation_copy_use_only_short_canonical_paths(harness):
    h = harness
    choices = [{**row, 'id': ('A' if i == 0 else 'B')*32} for i, row in enumerate(CHOICES)]
    result = preview(h, call_id='call_'+'q'*150, choices=choices)
    stage = h.root/'work/temple_stage'/('s'*32)
    for name, digest in result['evidence_files'].items():
        destination = stage/name
        assert len(str(destination)) < 260
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(h.root/name, destination)
        assert edits.sha(destination) == digest
    for option in result['options']:
        assert Path(option['after_evidence']['views'][0]['photo_path']).parent == h.root/'work/pv-assets'
        assert Path(option['export']['path']).parent == h.root/'work/pv-assets'
        assert option['seed_sha256'] == h.head['sha256'] and option['initial_fingerprint'] == h.head['fingerprint']
    assert all('option-'+choices[0]['id'] not in row['source_path'] for row in result['archived_evidence'])


def test_absolute_looking_owner_label_is_plain_text_not_a_file(harness):
    choices = [{**CHOICES[0], 'label': 'C:\\shape'}, CHOICES[1]]
    result = preview(harness, choices=choices)
    assert result['options'][0]['label'] == 'C:\\shape'
    assert not any(row['source_path'] == 'C:\\shape' for row in result['archived_evidence'])


def test_nested_understand(harness, monkeypatch):
    h = harness; original = h.root
    nested = original/'work/temple_stage'/('n'*32)
    nested.mkdir(parents=True)
    shutil.copyfile(original/'job.json', nested/'job.json')
    for folder in ('images', 'cameras', 'checkpoints', 'work/model_maps'):
        shutil.copytree(original/folder, nested/folder)
    h.root = h.scene.root = nested
    h.checkpoint = nested/'checkpoints/source.blend'
    h.evidence['snapshot_path'] = str(nested/'work/model_maps/source.json')
    h.scene.seeing.export_head = lambda *args: export(nested)
    monkeypatch.setattr(edits, 'load', lambda root: h.doc if Path(root) == nested else None)
    result = preview(h)
    assert all(len(row['source_path']) < 260 for row in result['archived_evidence'])
    assert all(len(str(nested/path)) < 260 for path in result['evidence_files'])
    assert all(Path(row['path']).parent == nested/'work/pv-assets' for row in result['images'])
    assert result['preview_receipt'].startswith('work/pv/') and len(result['preview_receipt']) == 41
    assert all(Path(row['export']['seat_check']['glb']).is_file() for row in result['options'])
    P.mark_delivered(h.scene, result, True)
    assert P.load_verified(h.scene, result['preview_receipt'])['options'] == result['options']


@pytest.mark.parametrize('failure', [None, 'start', 'apply', 'wrong_amount', 'strict_export', 'print_evidence', 'capture'])
def test_real_option_coordinator_applies_exact_worker_method_and_always_closes(harness, monkeypatch, failure):
    h = harness; children = []
    operations = owner_markings.seal(h.source, [h.op])
    class Child:
        def __init__(self, folder, *, captured_seed, captured_fingerprint):
            self.root, self.owner_edit, self.closed = folder, None, False
            assert captured_seed.read_bytes() == h.checkpoint.read_bytes()
            assert captured_fingerprint == h.head['fingerprint']
            assert not (folder/'inputs').exists()
            self.current = copy.deepcopy(h.source)
            self.s = SimpleNamespace(fingerprint=lambda: h.head['fingerprint'], call_mutating=self.apply)
            self.seeing = SimpleNamespace(export_head=self.export)
            children.append(self)
        def start(self):
            if failure == 'start':
                raise ValueError('start refused')
        def close(self):
            self.closed = True
        def apply(self, tool, method, params, **kwargs):
            assert tool == 'preview_owner_edit' and method == 'owner_edit_apply'
            assert params['operations'] == operations and kwargs['call_id'] == 'preview-apply'
            if failure == 'apply':
                return {'status': 'refused'}
            applied = copy.deepcopy(params['operations'])
            if failure == 'wrong_amount':
                applied[0]['params']['delta_mm'][0] += .1
            self.current = owner_edits._advance(self.current, applied)
            return {'status': 'ok'}
        def export(self, call_id):
            result = {'status': 'refused'} if failure == 'strict_export' else export(self.root)
            if failure == 'print_evidence':
                result['print_check'] = {'ok': False}
            return result
    monkeypatch.setattr(P, 'JobScene', Child)
    monkeypatch.setattr(P.owner_edits, 'snapshot', lambda child, label:
                        (copy.deepcopy(child.current), write(child.root/'work'/f'{label}.json', child.current)))
    if failure == 'capture':
        monkeypatch.setattr(P.look, 'capture', lambda *args, **kwargs: {'status': 'unavailable'})
    args = (h.scene, h.root/'private', h.checkpoint, h.head, h.source, operations, ['ref01'], 'basis-01')
    if failure:
        with pytest.raises(ValueError):
            h.real_run_option(*args)
    else:
        result = h.real_run_option(*args)
        assert result['checks']['confirmed_amounts_match'] and result['checks']['strict_spec']['ok']
    assert children and all(child.closed for child in children)
    assert h.checkpoint.read_bytes() == b'exact source checkpoint'


@pytest.mark.parametrize('delivery_failure', [False, True])
def test_real_sdk_records_preview_delivery_only_after_images_and_final_output(harness, sdk_scene, monkeypatch,
                                                                            delivery_failure):
    from agents.tool import ToolOutputImage
    from glasses3d.agent import tools as T
    h = harness; result = preview(h, call_id='sdk-preview')
    # The shared image adapter reserves refs for a mapping of per-photo sheets.
    assert 'selected_refs' in result and 'refs' not in result
    sdk_scene.root, sdk_scene.s = h.root, h.scene.s
    sdk_scene.owner_edit = h.doc
    monkeypatch.setattr(P, 'preview', lambda *args, **kwargs: copy.deepcopy(result))
    delivered = []; original_mark = P.mark_delivered
    def mark(scene, value, success=True):
        assert scene.images  # native image preparation and durable records precede this callback
        delivered.append(success)
        original_mark(scene, value, success)
    monkeypatch.setattr(P, 'mark_delivered', mark)
    if delivery_failure:
        def fail(*args, **kwargs):
            raise OSError('cannot persist complete amount preview output')
        monkeypatch.setattr(T, 'limit_output', fail)
    output = invoke(sdk_scene, 'preview_owner_edit', {'map_id': 'test-map', 'map_sha256': h.evidence['map_sha256'],
                    'capture_receipts': ['shown-map.json'], 'operations_json': json.dumps([h.op]),
                    'options': CHOICES, 'refs': ['ref01']}, cid='sdk-preview')
    receipt = edits.read((h.root/result['preview_receipt']).with_name('preview-delivery.json'))
    assert receipt['delivered'] is (not delivery_failure)
    if delivery_failure:
        assert isinstance(output, str) and 'cannot persist complete' in output
        assert delivered == [False] and 'sdk-preview' in sdk_scene.unseen
        with pytest.raises(ValueError, match='successfully delivered'):
            P.load_verified(h.scene, result['preview_receipt'])
    else:
        assert record(output)['status'] == 'ok' and delivered == [True]
        assert sum(isinstance(row, ToolOutputImage) for row in output) == len(result['images'])
        assert all(row['size_px'] == row['source_size_px'] for row in sdk_scene.images)
        assert P.load_verified(h.scene, result['preview_receipt'])['preview_sha256'] == result['preview_sha256']
