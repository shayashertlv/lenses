"""$0 capture UNDERSTAND and exact per-item apply, scripted by Codex, not Astra.

The plan is ignored local test data: it names an immutable delivery, two capture
descriptions and predeclared operations. The production SDK, transport receipts,
tools, Blender, hard checks and delivery publication execute normally. Only model
HTTP is replayed. This instrument demonstration is never product acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import shutil
import time
from unittest.mock import patch
from concurrent.futures import Future

for _key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'

import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial.transform import Rotation

from glasses3d.agent import budget, delivery, edits, job_run, owner_captures, procs
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.replay import COUNT_PATH, ScriptedOpenAI, tool_call
from glasses3d.agent.tools import free_commit_bytes, model_map, model_map_geometry, owner_preview
from glasses3d.g3d_host import glb
from glasses3d.tests.proofs.assembly_m6_owner_edit import event, protected, result
from glasses3d.tests.proofs.offline_job_approval import prepare_offline_request

LABEL = 'scripted by Codex, not Astra'


def require_unpaid_checkout():
    lock = Path(__file__).resolve().parents[2]/'data/.paid-run.lock'
    if lock.exists():
        raise RuntimeError('Checkout paid-run lock is present; do not start this offline native proof.')


class SerialPreviewPool:
    """Proof scheduler: release the verified read-only parent around each child.

    The native option implementation and all its checks are unchanged. Reopening
    the parent verifies its checkpoint bytes/fingerprint through JobScene.start.
    """
    def __init__(self, scene, max_workers):
        assert max_workers == 1
        self.scene = scene

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def submit(self, function, *args):
        require_unpaid_checkout()
        future = Future()
        self.scene.s.close()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        finally:
            require_unpaid_checkout()
            self.scene.start()
        return future


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    procs.write_json_atomic(path, value)


def camera(eye, target, width, height):
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    z = eye-target
    z /= np.linalg.norm(z)
    x = np.cross([0., 1., 0.], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    world = np.eye(4)
    world[:3, :3], world[:3, 3] = np.column_stack((x, y, z)), eye
    fov, near, far, aspect = 35., .001, 10., width/height
    f = 1/math.tan(math.radians(fov)/2)
    projection = np.array([[f/aspect, 0, 0, 0], [0, f, 0, 0],
        [0, 0, -(far+near)/(far-near), -2*far*near/(far-near)], [0, 0, -1, 0]])
    record = {'projection': 'perspective', 'fov_y_deg': fov, 'aspect': aspect,
        'near_m': near, 'far_m': far, 'position_m': eye.tolist(),
        'quaternion_xyzw': Rotation.from_matrix(world[:3, :3]).as_quat().tolist(),
        'target_m': target.tolist(), 'matrix_world': world.T.ravel().tolist(),
        'projection_matrix': projection.T.ravel().tolist()}
    return record, projection @ np.linalg.inv(world)


def raster(model, vp, width, height):
    """CPU model capture with perspective-correct anchors and nearest-hit IDs.

    These synthetic viewer captures demonstrate the anchor contract. They are
    explicitly separate from authoritative product photos and option AR renders.
    """
    pixels = np.full((height, width, 3), 242, np.uint8)
    depth = np.full((height, width), np.inf)
    ids = np.full((height, width), -1, np.int32)
    triangles = []
    for primitive in model.primitives:
        material = model.material(primitive.material_index) or {}
        color = np.array(material.get('pbrMetallicRoughness', {}).get('baseColorFactor', [.25, .25, .25, 1])[:3])
        for index, corners in enumerate(primitive.triangles):
            xyz = primitive.positions[corners]
            clip = np.column_stack((xyz, np.ones(3))) @ vp.T
            if np.any(clip[:, 3] <= 0):
                continue
            ndc = clip[:, :3]/clip[:, 3, None]
            screen = np.column_stack(((ndc[:, 0]+1)*width/2, (1-ndc[:, 1])*height/2))
            tid = len(triangles)
            triangles.append((primitive, index, xyz, clip, screen))
            lo = np.maximum(0, np.floor(screen.min(0)).astype(int))
            hi = np.minimum([width-1, height-1], np.ceil(screen.max(0)).astype(int))
            if np.any(hi < lo):
                continue
            x, y = np.meshgrid(np.arange(lo[0], hi[0]+1)+.5, np.arange(lo[1], hi[1]+1)+.5)
            a, b, c = screen
            den = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(den) < 1e-10:
                continue
            u = ((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/den
            v = ((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/den
            w = 1-u-v
            z = u*ndc[0, 2]+v*ndc[1, 2]+w*ndc[2, 2]
            area = np.s_[lo[1]:hi[1]+1, lo[0]:hi[0]+1]
            take = (u >= 0)&(v >= 0)&(w >= 0)&(z < depth[area])
            normal = np.cross(xyz[1]-xyz[0], xyz[2]-xyz[0])
            normal /= max(np.linalg.norm(normal), 1e-12)
            shade = .4+.6*abs(float(np.dot(normal, [.35, .25, .9027735])))
            depth[area][take], ids[area][take] = z[take], tid
            pixels[area][take] = np.clip(color*255*shade, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels), ids, triangles


def anchor(triangle, xy):
    primitive, index, xyz, clip, screen = triangle
    weights = np.linalg.solve(np.vstack((screen.T, np.ones(3))), [*xy, 1.])
    weights /= clip[:, 3]
    weights /= weights.sum()
    normal = np.cross(xyz[1]-xyz[0], xyz[2]-xyz[0])
    normal /= np.linalg.norm(normal)
    return {'xy_px': list(map(float, xy)), 'anchor': {'kind': 'surface',
        'node_index': primitive.node_index, 'node_name': primitive.node_name,
        'mesh_index': primitive.mesh_index, 'primitive_index': primitive.primitive_index,
        'triangle_index': int(index), 'barycentric': weights.tolist(),
        'position_mm': (weights @ xyz*1000).tolist(), 'normal': normal.tolist(),
        'is_lens': False, 'behind': None}}


def capture(model, source_glb, folder, item):
    width, height = 1600, 1000
    definition = item['capture']
    cam, vp = camera(definition['eye_m'], definition['target_m'], width, height)
    clean, ids, triangles = raster(model, vp, width, height)
    marks = []
    annotated = clean.copy()
    draw = ImageDraw.Draw(annotated)
    for definition_mark in definition['marks']:
        matching = [i for i, t in enumerate(triangles) if t[0].node_name == definition_mark['node_name']]
        yy, xx = np.nonzero(np.isin(ids, matching))
        assert len(xx), 'Capture camera cannot see '+definition_mark['node_name']
        coords = np.column_stack((xx+.5, yy+.5))
        points = []
        for desired in definition_mark['points_mm']:
            clip = vp @ np.r_[np.asarray(desired)/1000, 1.]
            ndc = clip[:3]/clip[3]
            projected = np.array([(ndc[0]+1)*width/2, (1-ndc[1])*height/2])
            index = int(np.argmin(((coords-projected)**2).sum(1)))
            points.append(coords[index])
        assert len({tuple(p) for p in points}) == len(points), 'Capture shape collapsed'
        mask = Image.new('1', clean.size)
        mask_draw = ImageDraw.Draw(mask)
        if definition_mark['kind'] == 'point':
            x, y = points[0]
            mask_draw.ellipse((x-9, y-9, x+9, y+9), fill=1)
            draw.ellipse((x-9, y-9, x+9, y+9), fill='#dc2626')
        else:
            mask_draw.polygon([tuple(p) for p in points], fill=1)
            draw.line([*(tuple(p) for p in points), tuple(points[0])], fill='#dc2626', width=4)
        covered = ids[np.asarray(mask, dtype=bool)]
        surfaces = {}
        for tid in np.unique(covered[covered >= 0]):
            primitive, index, *_ = triangles[int(tid)]
            key = (primitive.node_index, primitive.mesh_index, primitive.primitive_index)
            surfaces.setdefault(key, set()).add(int(index))
        footprint = [{'node_index': k[0], 'mesh_index': k[1], 'primitive_index': k[2],
            'triangles': [[i, i] for i in sorted(v)]} for k, v in surfaces.items()]
        marks.append({'label': definition_mark['label'], 'kind': definition_mark['kind'],
            'radius_px': 9, 'stroke_px': 4,
            'vertices': [anchor(triangles[int(ids[int(p[1]), int(p[0])])], p) for p in points],
            'footprint': {'pixels': int(np.count_nonzero(covered >= 0)), 'faces': footprint}})
        draw.text(tuple(points[0]+[12, -14]), definition_mark['label'], fill='#dc2626', stroke_width=1)
    payload = {'schema': owner_captures.SCHEMA, 'capture_id': definition['capture_id'],
        'source': {'delivery_key': 'offline-copy', 'glb_sha256': edits.sha(source_glb)},
        'image': {'width_px': width, 'height_px': height}, 'camera': cam,
        'marks': marks, 'request_text': item['request_text']}
    folder.mkdir(parents=True, exist_ok=False)
    clean.save(folder/'viewer.png')
    annotated.save(folder/'annotated.png')
    write(folder/'capture.json', payload)
    owner_captures.validate_capture(payload, folder/'viewer.png', folder/'annotated.png', source_glb)
    return {'item_id': item['item_id'], 'request_text': item['request_text'], 'capture_dir': str(folder)}


class Script:
    """The next reply uses only real previous tool outputs and predeclared data."""
    def __init__(self, root, plan, out):
        self.root, self.plan, self.out = root, plan, out
        self.transport = ScriptedOpenAI([], count_tokens=lambda _: 1000)
        self.prepared, self.actions = set(), []
        self.scopes, self.operations = {}, {}
        self.manifest = None
        self.remapped = None
        self.previous = None
        self.count_attempts = 0

    def handle(self, request):
        index = len(self.transport.creates)
        if request.url.path == COUNT_PATH:
            self.count_attempts += 1
        if request.url.path == COUNT_PATH and index not in self.prepared:
            try:
                if self.previous:
                    cid, expected = self.previous
                    actual = event(self.root, cid)
                    assert actual and actual['status'] in expected, f'Previous real tool failed: {cid}: {actual}'
                name, args, cid, expected = self.reply(index)
            except Exception as exc:
                write(self.out/'replay-handler-error.json', {'index': index, 'type': type(exc).__name__,
                    'message': str(exc), 'label': LABEL})
                raise
            self.transport.replies.append(tool_call(name, args, call_id=cid, text=LABEL,
                counted=1000, usage={'input_tokens': 1000, 'output_tokens': 50, 'total_tokens': 1050,
                    'input_tokens_details': {'cached_tokens': 0},
                    'output_tokens_details': {'reasoning_tokens': 10}}))
            self.actions.append({'index': index, 'tool': name, 'call_id': cid, 'label': LABEL})
            write(self.out/'understand-actions.json', self.actions)
            print(f'UNDERSTAND {index}: {name} ({cid})', flush=True)
            self.previous = cid, expected
            self.prepared.add(index)
        return self.transport.handle(request)

    def reply(self, index):
        if index == 0:
            operations = [op for item in self.plan['items'] for op in item['operations']]
            return 'labelled_model_map', {'photos': self.plan['map_refs'],
                'focus': list(dict.fromkeys(op['scope']['object_id'] for op in operations)),
                'kind': 'parts', 'map_id': None, 'supports_json': None,
                'page': 0, 'label_descriptions_json': None, 'target_page': 0, 'sample_page': 0,
                'owner_labels': None}, 'c2-discovery', {'ok'}
        discovered = result(self.root, 'c2-discovery')
        if self.manifest is None:
            manifest = model_map.load_map(self.root, discovered['map_id'], discovered['map_sha256'])
            self.manifest = manifest
            assert manifest['source']['fingerprint'] == self.plan['source_fingerprint']
            resolutions = discovered['owner_mark_resolutions']
            captures = owner_captures.load_round_captures(self.root)
            expected_marks = sum(len(row['payload']['marks']) for row in captures)
            assert len(resolutions) == expected_marks and all(r['status'] == 'resolved' for r in resolutions)
            assert all(r['resolved_polygons'] and not r['edit_authority'] for r in resolutions)
            write(self.out/'mark-resolutions.json', resolutions)
            remap = {}
            for old_id, binding in self.plan['target_bindings'].items():
                found = [key for key, value in manifest['targets'].items()
                         if all(value[field] == binding[field] for field in ('object_name', 'kind', 'vertex_ids'))]
                assert len(found) == 1, 'Source target has no unique current identity: '+old_id
                remap[old_id] = found[0]
            self.remapped = copy.deepcopy(self.plan['items'])
            for item in self.remapped:
                for op in item['operations']:
                    op['target_ids'] = [remap[key] for key in op['target_ids']]
                    op['scope']['target_ids'] = [remap[key] for key in op['scope']['target_ids']]
            write(self.out/'target-rebinding.json', {'old_to_current': remap,
                'method': 'Unique same object name, kind and exact vertex IDs in the actual unchanged source map.',
                'geometry_and_amounts_unchanged': True})
        if index == 1:
            operations = [op for item in self.remapped for op in item['operations']]
            scopes = [{**op['scope'], 'target_ids': op['target_ids']} for op in operations]
            return 'labelled_model_map', {'photos': self.plan['map_refs'],
                'focus': list(dict.fromkeys(t for op in operations for t in op['target_ids'])),
                'kind': 'parts', 'map_id': discovered['map_id'], 'supports_json': json.dumps(scopes),
                'page': 0, 'label_descriptions_json': None, 'target_page': 0, 'sample_page': 0,
                'owner_labels': None}, 'c2-map', {'ok'}
        shown = result(self.root, 'c2-map')
        if not self.operations:
            delivered = {row['sha256'] for row in shown['scope_refs']}
            scopes, operations = {}, {}
            for item in self.remapped:
                group = []
                for op in item['operations']:
                    scope = model_map._scope(self.root, self.manifest, {**op['scope'], 'target_ids': op['target_ids']})
                    digest = model_map_geometry.digest(scope)
                    assert digest in delivered, 'Exact operation support was not delivered: '+op['id']
                    scopes[op['id']] = scope
                    group.append({'id': op['id'], 'kind': op['kind'], 'target_ids': op['target_ids'],
                        'scope_ref_sha256': digest, 'params': copy.deepcopy(op['params'])})
                operations[item['item_id']] = group
            # COUNT callbacks can retry. Publish state only after every item is
            # authenticated, so a refusal cannot leave a usable partial group.
            self.scopes, self.operations = scopes, operations
        if 2 <= index <= len(self.plan['items'])+1:
            item = self.remapped[index-2]
            return 'preview_owner_edit', {'map_id': shown['map_id'], 'map_sha256': shown['map_sha256'],
                'capture_receipts': [shown['capture_receipt']], 'item_id': item['item_id'],
                'operations_json': json.dumps(self.operations[item['item_id']]),
                'options': item['options'], 'refs': item['refs']}, 'c2-preview-'+item['item_id'], {'ok'}
        assert index == len(self.plan['items'])+2, 'UNDERSTAND failed to stop after finish'
        records = []
        for number, item in enumerate(self.remapped, 1):
            preview = result(self.root, 'c2-preview-'+item['item_id'])
            assert preview['source_unchanged'] and len(preview['options']) == 2
            records.append({'number': number, 'item_id': item['item_id'], 'label': item['label'],
                'interpretation': LABEL+': '+item['interpretation'], 'disposition': 'previewed',
                'reason': item['reason'], 'direction': item['direction'], 'amount': item['amount'],
                'target_ids': list(dict.fromkeys(t for op in item['operations'] for t in op['target_ids'])),
                'operation_json': json.dumps(self.operations[item['item_id']]),
                'preview_receipt': preview['preview_receipt']})
        return 'understand_finish', {'map_id': shown['map_id'], 'map_sha256': shown['map_sha256'],
            'capture_receipts': [shown['capture_receipt']], 'items': records, 'preview_receipt': None,
            'limitations': [LABEL+'. Tool test on copies; no product delivery or owner visual acceptance.',
                'Capture images are synthetic model-view evidence; product photos remain authoritative.',
                'The amounts were predeclared from existing $0 tool-test plans before this proof.']}, 'c2-finish', {'accepted'}


def prepare(plan, out):
    source = Path(plan['source']).resolve()
    original = Path(plan.get('original_source', source)).resolve()
    before = protected(original)
    write(out/'source-before.json', before)
    write(out/'adapted-source-before.json', protected(source))
    saved = delivery.load_delivery(source, plan['delivery_id'])
    assert saved['glb_sha256'] == plan['source_glb_sha256']
    source_glb = Path(saved['model'])
    items = []
    model = None
    for item in plan['items']:
        folder = out/item['capture']['capture_id']
        if item.get('capture_dir'):
            shutil.copytree(item['capture_dir'], folder)
            payload = owner_captures.validate_capture(edits.read(folder/'capture.json'),
                folder/'viewer.png', folder/'annotated.png', source_glb)
            assert payload['request_text'] == item['request_text']
            items.append({'item_id': item['item_id'], 'request_text': item['request_text'], 'capture_dir': str(folder)})
        else:
            if model is None:
                model = glb.load(source_glb)
            items.append(capture(model, source_glb, folder, item))
    root = owner_captures.create_capture_understand(source, plan['delivery_id'], items, destination=out/'e')
    assert not (root/'inputs/edit-capabilities.json').exists(), 'Capture admission must remain unresolved'
    assert protected(original) == before
    write(out/'prepared.json', {'root': str(root), 'source_unchanged': True, 'label': LABEL,
        'source_checkpoint': edits.load(root)['source'], 'actual_spend_usd': 0})
    return root


async def understand(root, plan, out):
    require_unpaid_checkout()
    script = Script(root, plan, out)
    settings = replace(job_run.settings_default(), effort='high', settled_only=True,
        settled_stop_usd='20', max_turns=12, max_output_tokens=4096)
    spec, scene = prepare_offline_request(root, 'c2-proof', script.handle, settings=settings, cap='20')
    request = edits.read(root/'runs/c2-proof/approval-request.json')
    assert request['offline_only'], 'Proof approval must be unusable for paid transport'
    started = time.monotonic()
    # The proof runs exactly one native Blender process at a time. The parent's
    # source checkpoint is reopened and reverified after each private option.
    with patch.object(budget, 'PAID_RUN_LOCK_ROOT', out/'locks'), patch.object(
            owner_preview, 'preview_workers', lambda _: 1), patch.object(
            owner_preview, 'ProcessPoolExecutor', lambda max_workers: SerialPreviewPool(scene, max_workers)):
        outcome = await job_run.execute_run(root, 'c2-proof', offline_handler=script.handle, scene=scene)
    report = {'root': str(root), 'seconds': time.monotonic()-started, 'outcome': outcome,
        'mock_inference_requests': len(script.transport.creates), 'mock_count_requests': len(script.transport.counts),
        'mock_count_attempts': script.count_attempts,
        'actual_provider_requests': 0, 'actual_spend_usd': 0, 'label': LABEL,
        'mock_usage_note': 'Fixed synthetic 1000-token counts exercise procedure; not an estimate of paid usage.',
        'transport_violations': script.transport.violations, 'transport_unexpected': script.transport.unexpected,
        'actions': script.actions, 'approval_offline_only': request['offline_only']}
    write(out/'understand-result.json', report)
    assert not script.transport.violations and not script.transport.unexpected
    assert outcome['result']['status'] == 'completed', outcome['result'].get('error')
    assert outcome['delivery']['status'] == 'awaiting_owner_confirmation'
    assert len(script.transport.creates) == len(plan['items'])+3
    return report


def apply(root, plan, out):
    require_unpaid_checkout()
    from glasses3d.agent import owner_choices
    dto = owner_choices.read_understanding(root)
    write(out/'understanding-dto.json', dto)
    assert len(dto['items']) == 2
    for item, expected in zip(dto['items'], plan['items']):
        assert item['item_id'] == expected['item_id']
        assert item['request_text'] == expected['request_text']
        assert item['disposition'] == 'previewed' and not item['fallback_allowed']
        assert len(item['options']) == 2
    results = []
    picks = [{'item_id': item['item_id'], 'option_id': item['selected_option']} for item in plan['items']]
    for label, choices, skipped in (
            ('both', picks, []),
            ('one-none', picks[:1], [{'item_id': plan['items'][1]['item_id'], 'note': plan['none_note']}] )):
        started = time.monotonic()
        require_unpaid_checkout()
        destination = out/('applied-'+label)
        if label == 'both' and plan.get('same_part_shading_refusal_proof'):
            expected_message = (
                f"Items {choices[0]['item_id']} and {choices[1]['item_id']} both change the shading of the same part, "
                "which cannot be combined exactly yet. Pick an option for one of them now "
                "(choose None of these for the other) and send the other again as a new edit.")
            before = edits.tree_hashes(root)
            with patch.object(Path, 'mkdir', side_effect=AssertionError('Refusal created a directory')) as directories, \
                    patch.object(owner_choices, '_prepare_apply',
                                 side_effect=AssertionError('Refusal copied a private job')) as copies, \
                    patch.object(owner_choices.JobScene, 'start',
                                 side_effect=AssertionError('Refusal started a native worker')) as native, \
                    patch.object(delivery, 'deliver_owner_applied',
                                 side_effect=AssertionError('Refusal published a delivery')) as publication:
                try:
                    owner_choices.apply_choices(root, dto['understanding_sha256'], choices, skipped,
                        LABEL+': exercise the early same-part shading refusal.', destination=destination)
                except ValueError as exc:
                    message = str(exc)
                else:
                    raise AssertionError('Same-part shading picks were not refused')
            assert message == expected_message, message
            counts = {'directory_creations': directories.call_count, 'private_copies': copies.call_count,
                      'native_worker_starts': native.call_count, 'publications': publication.call_count}
            assert all(count == 0 for count in counts.values()), counts
            assert not destination.exists() and edits.tree_hashes(root) == before
            row = {'case': label, 'status': 'expected_same_part_shading_refusal', 'message': message,
                   'seconds': time.monotonic()-started, 'choices': choices, 'skipped': skipped,
                   **counts, 'destination_absent': True, 'source_round_unchanged': True,
                   'actual_provider_requests': 0, 'actual_spend_usd': 0}
            write(out/('apply-'+label+'.json'), row)
            results.append(row)
            continue
        archived = out/'prepublication'/(label+'-'+str(time.time_ns()))
        publish = delivery.deliver_owner_applied
        expected_applied = [{**pick, 'amount_text': next(option['amount_text']
            for item in dto['items'] if item['item_id'] == pick['item_id']
            for option in item['options'] if option['option_id'] == pick['option_id'])} for pick in choices]

        def check_application(application, export_sha):
            assert application['origin'] == 'applied_picks'
            assert application['api_requests'] == 0 and application['settled_usd'] == '0.000000'
            assert application['notes'] == application['skipped'] == skipped
            assert application['applied'] == expected_applied
            exactness = application['exactness']
            assert exactness['ok'] and exactness['overlaps'] == [] and application['warnings'] == []
            assert all(row['exact_preview_geometry'] for row in exactness['items'])
            if len(choices) == 1:
                assert exactness['mode'] == 'single_glb_bytes' and exactness['single_pick_glb_equal']
                preview = result(root, 'c2-preview-'+choices[0]['item_id'])
                option = next(row for row in preview['options'] if row['id'] == choices[0]['option_id'])
                assert export_sha == option['export']['sha256']
            else:
                assert exactness['mode'] == 'per_item_geometry'

        def record_publication(job, exported, head, ar, application):
            """Keep evidence if an external publisher refuses; never turn refusal into success."""
            shutil.copytree(job, archived)
            write(archived/'prepublication-evidence.json', {'application': application,
                'exit_checkpoint': head, 'ar': ar, 'export': exported,
                'status': 'checks_complete_publication_not_yet_attempted', 'label': LABEL})
            assert edits.sha(archived/Path(exported['path']).relative_to(job)) == exported['sha256']
            check_application(application, exported['sha256'])
            assert not (Path(job)/'runs').exists(), 'A local apply must not create a model run'
            return publish(job, exported, head, ar, application)

        try:
            with patch.object(delivery, 'deliver_owner_applied', record_publication):
                applied = owner_choices.apply_choices(root, dto['understanding_sha256'], choices, skipped,
                    LABEL+': apply these exact selected amounts on this disposable tool-test copy.', destination=destination)
        except delivery.DeliveryError as exc:
            assert not destination.exists(), 'Failed publication left a half-written apply destination'
            if 'owner application does not bind its exact preview and delivered model' not in str(exc):
                raise
            application = edits.read(archived/'prepublication-evidence.json')['application']
            assert application['origin'] == 'applied_picks' and application['exactness']['ok']
            row = {'case': label, 'status': 'blocked_by_delivery_contract', 'reason': str(exc),
                'seconds': time.monotonic()-started, 'choices': choices, 'skipped': skipped,
                'exactness': application['exactness'], 'export_sha256': application['export']['sha256'],
                'applied': application['applied'], 'notes': application['notes'],
                'evidence': str(archived), 'failed_destination_removed': True,
                'actual_provider_requests': 0, 'actual_spend_usd': 0}
            write(out/('apply-'+label+'.json'), row)
            results.append(row)
            # A real proof failure is terminal. Retain the evidence and do not
            # attempt another selection after failed immutable publication.
            raise
        saved = delivery.load_delivery(applied)
        report = saved['report_data']
        assert Path(saved['report']).resolve().is_relative_to(Path(applied).resolve()/'deliveries')
        assert report['delivery_kind'] == 'owner_applied'
        assert report['finished'] is True and report['accepted_finish'] is False and report['run_status'] == 'owner_applied'
        binding = saved['report_data']['owner_applied']
        application_path = Path(saved['report']).parent/binding['path']
        assert edits.sha(application_path) == binding['sha256']
        application = edits.read(application_path)
        check_application(application, saved['glb_sha256'])
        assert all(report[key] == application[key] for key in ('origin', 'applied', 'notes', 'warnings', 'exactness'))
        exactness = application['exactness']
        assert not (Path(applied)/'runs').exists(), 'A local apply must not create a model run'
        row = {'case': label, 'status': 'delivered', 'root': str(applied), 'seconds': time.monotonic()-started,
            'choices': choices, 'skipped': skipped, 'delivery': saved,
            'exactness': exactness, 'applied': application['applied'], 'notes': application['notes'],
            'application_path': str(application_path), 'application_sha256': edits.sha(application_path),
            'actual_provider_requests': 0, 'actual_spend_usd': 0}
        write(out/('apply-'+label+'.json'), row)
        results.append(row)
    write(out/'apply-results.json', results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--phase', choices=('prepare', 'understand', 'apply', 'all'), default='all')
    args = parser.parse_args()
    require_unpaid_checkout()
    os.environ['G3D_OFFLINE'] = '1'
    for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID', 'OPENAI_ADMIN_KEY',
                'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        os.environ.pop(key, None)
    disable_sdk_tracing()
    plan, out = edits.read(args.plan), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.phase in ('prepare', 'all'):
        assert not (out/'prepared.json').exists(), 'Use a fresh proof directory'
        write(out/'predeclared-plan.json', plan)
    started = time.monotonic()
    machine = {'pid': os.getpid(), 'started_utc': procs.utcnow(), 'phase': args.phase,
        'free_commit_gib': free_commit_bytes()/2**30, 'blas_threads': 1,
        'power_throttling_off': procs.opt_out_self(), 'option_workers': 1}
    write(out/(args.phase+'-started.json'), machine)
    source = Path(plan.get('original_source', plan['source'])).resolve()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients, procs.DescendantsOptOut():
        try:
            root = prepare(plan, out) if args.phase in ('prepare', 'all') else Path(edits.read(out/'prepared.json')['root'])
            if args.phase in ('understand', 'all'):
                asyncio.run(understand(root, plan, out))
            if args.phase in ('apply', 'all'):
                apply(root, plan, out)
        finally:
            if (out/'source-before.json').exists():
                before, after = edits.read(out/'source-before.json'), protected(source)
                write(out/'source-after.json', {'unchanged': before == after, 'files': len(after),
                    'changed': [name for name in before if after.get(name) != before[name]],
                    'added': sorted(set(after)-set(before))})
            if (out/'adapted-source-before.json').exists():
                before, after = edits.read(out/'adapted-source-before.json'), protected(Path(plan['source']))
                write(out/'adapted-source-after.json', {'unchanged': before == after, 'files': len(after),
                    'changed': [name for name in before if after.get(name) != before[name]],
                    'added': sorted(set(after)-set(before))})
            write(out/'offline-isolation.json', {'non_loopback_attempts': network.attempts,
                'production_transport_constructions': clients.constructions, 'actual_spend_usd': 0,
                'credentials_scrubbed': True, 'sdk_tracing_disabled': True, 'label': LABEL})
    assert not network.attempts and not clients.constructions
    assert edits.read(out/'source-after.json')['unchanged'], 'Original source changed'
    assert edits.read(out/'adapted-source-after.json')['unchanged'], 'Adapted source changed'
    complete = {**machine, 'completed_utc': procs.utcnow(), 'seconds': time.monotonic()-started,
        'status': 'passed', 'actual_provider_requests': 0, 'actual_spend_usd': 0}
    if (out/'apply-results.json').exists() and any(
            row['status'] not in {'delivered', 'expected_same_part_shading_refusal'}
            for row in edits.read(out/'apply-results.json')):
        complete['status'] = 'blocked_by_delivery_contract'
    write(out/(args.phase+'-completed.json'), complete)
    if (out/'apply-results.json').exists():
        write(out/'proof.json', {'status': complete['status'], 'label': LABEL, 'actual_spend_usd': 0,
            'understand': edits.read(out/'understand-result.json'), 'applies': edits.read(out/'apply-results.json'),
            'source': edits.read(out/'source-after.json'), 'phase_times': {
                path.stem: edits.read(path) for path in out.glob('*-completed.json')}})
    print(json.dumps(complete), flush=True)
    if complete['status'] != 'passed':
        raise SystemExit(3)


if __name__ == '__main__':
    main()
