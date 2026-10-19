"""Read sealed per-item choices and apply owner-selected IDs without model requests.

Browser values never contain operations. All executable authority comes from the
delivered, hash-bound understanding and authenticated preview receipts.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

from . import delivery, edits, owner_apply, owner_edits, procs
from .job_scene import JobScene
from .journal import CheckpointIndex
from .tool_timeouts import OWNER_APPLY_DEADLINE_S
from .tools import owner_preview
from glasses3d.g3d import owner_mesh as M


def _load_sealed(round_root):
    root = Path(round_root).resolve()
    doc = edits.load(root)
    if not doc or doc['phase'] != 'understand':
        raise ValueError('Choose a completed UNDERSTAND round.')
    artifact = edits.confined(root, 'work/owner_edit/understanding.json')
    understanding = edits.read(artifact)
    shown = edits.read(artifact.with_name('understanding-delivery.json'))
    state = root / 'work/job_scene/state.json'
    unseen = edits.read(state).get('unseen', []) if state.exists() else []
    if (understanding.get('schema') != edits.UNDERSTANDING_SCHEMA
            or shown.get('schema') != 'g3d.owner_understanding_delivery/1'
            or shown.get('delivered') is not True or shown.get('sha256') != edits.sha(artifact)
            or shown.get('call_id') != understanding.get('call_id')
            or shown.get('call_id') in unseen):
        raise ValueError('The understanding must have been delivered unchanged.')
    if (understanding.get('source_fingerprint') != doc['source']['fingerprint']
            or understanding.get('request_text') != doc['request_text']):
        raise ValueError('The understanding does not describe this exact source and request.')
    expected_status = 'awaiting_owner_confirmation' if understanding.get('operations') else 'diagnostic_only'
    if understanding.get('status') != expected_status:
        raise ValueError('The understanding is not a completed sealed result.')
    for name, digest in understanding['evidence_files'].items():
        if edits.sha(edits.confined(root, name)) != digest:
            raise ValueError('The understanding evidence changed: ' + name)
    from .tools import model_map
    evidence = model_map.validate_understanding(root, understanding['map_id'], understanding['map_sha256'],
                                               understanding['capture_receipts'], [])
    if (evidence['source']['fingerprint'] != doc['source']['fingerprint']
            or str(Path(evidence['snapshot_path']).resolve()) !=
               str(edits.confined(root, understanding['snapshot_path']))):
        raise ValueError('The understanding map does not describe the exact source.')
    head = CheckpointIndex(root / 'checkpoints').head
    if not head or head['fingerprint'] != doc['source']['fingerprint']:
        raise ValueError('The understanding no longer has its unchanged source checkpoint.')
    if edits.sha(edits.confined(root, 'checkpoints/' + head['file'])) != head['sha256']:
        raise ValueError('The source checkpoint bytes changed.')
    scene = SimpleNamespace(root=root, _unseen=unseen,
                            s=SimpleNamespace(head=head, fingerprint=lambda: head['fingerprint']))
    # CLI text requests retain their complete operation group as one selectable item.
    if not doc.get('owner_capture_items'):
        understanding = copy.deepcopy(understanding)
        binding = understanding.get('amount_preview')
        legacy_amount = '; '.join(row.get('amount', '') for row in understanding['items'])
        understanding['items'] = [{
            'item_id': 'I1', 'number': 1, 'label': 'Text edit', 'labels': [],
            'request_text': doc['request_text'],
            'interpretation': '\n'.join(row['interpretation'] for row in understanding['items']),
            'disposition': 'previewed' if binding else 'cannot_preview',
            'reason': '' if binding else 'This text edit has no sealed amount choices.',
            'amount_preview': binding}]
    else:
        originals = doc['owner_capture_items']
        if ([row.get('item_id') for row in understanding['items']] != [row['item_id'] for row in originals]
                or [row.get('number') for row in understanding['items']] != list(range(1, len(originals) + 1))):
            raise ValueError('The understanding must retain every original item in order.')
    loaded, all_ids = [], set()
    for item in understanding['items']:
        disposition = item.get('disposition')
        if disposition not in {'previewed', 'cannot_preview', 'needs_clarification', 'unsupported'}:
            raise ValueError('Every item needs a previewed or blocked disposition.')
        if disposition != 'previewed':
            if not isinstance(item.get('reason'), str) or not item['reason'].strip() or item.get('amount_preview'):
                raise ValueError('A blocked item needs a reason and cannot grant option authority.')
            if doc.get('owner_capture_items') and json.loads(item.get('operation_json', 'null')) != []:
                raise ValueError('Blocked items cannot contain operations.')
            loaded.append((item, None))
            continue
        binding = item.get('amount_preview') or {}
        relative = binding.get('preview_receipt')
        if (not isinstance(relative, str)
                or understanding['evidence_files'].get(relative) != binding.get('preview_sha256')
                or edits.sha(edits.confined(root, relative)) != binding.get('preview_sha256')):
            raise ValueError('An item preview is missing or changed.')
        preview = owner_preview.load_verified(scene, relative)
        binding_keys = ['call_id', 'preview_sha256']
        if doc.get('owner_capture_items'):
            binding_keys += ['preview_delivery', 'preview_delivery_sha256']
        if any(preview.get(key) != binding.get(key) for key in binding_keys):
            raise ValueError('The delivered preview binding changed.')
        if doc.get('owner_capture_items') and (preview.get('item_id') != item['item_id']
                or binding.get('item_id') != item['item_id']):
            raise ValueError('A preview option belongs to a different owner item.')
        if doc.get('owner_capture_items'):
            proposed = json.loads(item['operation_json'])
            proposed = proposed if isinstance(proposed, list) else [proposed]
            concise = lambda op: {key: op[key] for key in ('id', 'kind', 'target_ids', 'params')}
            if [concise(op) for op in proposed] != [concise(op) for op in preview['base_operations']]:
                raise ValueError('The preview operations differ from this item interpretation.')
        if (preview['map_id'] != understanding['map_id']
                or preview['map_sha256'] != understanding['map_sha256']):
            raise ValueError('Item previews must use the sealed source map.')
        for name, digest in preview['evidence_files'].items():
            if understanding['evidence_files'].get(name) != digest:
                raise ValueError('Item preview evidence was not sealed in the understanding.')
        options = preview['options']
        if len(options) not in (2, 3) or len({row['id'] for row in options}) != len(options):
            raise ValueError('Every previewed item needs two or three distinct options.')
        for option in options:
            if doc.get('owner_capture_items'):
                if option.get('item_id') != item['item_id'] or not str(option.get('amount_text', '')).strip():
                    raise ValueError('Each sealed option needs its own item and readable amount.')
            else:
                # Legacy records did not have amount_text: the sealed label/scale
                # and sealed interpretation amount are the sole display source.
                option['amount_text'] = (option.get('amount_text') or
                    (f"{option['label']}: {option['scale']:g} × {legacy_amount}" if legacy_amount else option['label']))
            summaries = binding.get('options', [])
            summary = next((row for row in summaries if row.get('id') == option['id']), None)
            if summary is None or any(option.get(key) != value for key, value in summary.items()):
                raise ValueError('The item option differs from its sealed understanding.')
        ids = [op['id'] for op in preview['base_operations']]
        if len(set(ids)) != len(ids) or all_ids.intersection(ids):
            raise ValueError('Each item must have its own distinct operation identities.')
        all_ids.update(ids)
        loaded.append((item, preview))
    if [op for _, preview in loaded if preview for op in preview['base_operations']] != understanding['operations']:
        raise ValueError('The sealed item operations differ from the complete understanding.')
    return doc, understanding, loaded


def _image(root, evidence, record):
    digest = record.get('sha256')
    path = record.get('path') or record.get('full_pair')
    candidate = edits.confined(root, path) if path else None
    if digest is None and candidate is not None:
        digest = evidence.get(candidate.relative_to(root).as_posix())
    matches = [edits.confined(root, name) for name, value in evidence.items()
               if value == digest and name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
    if not matches:
        raise ValueError('A displayed image is absent from the sealed evidence.')
    chosen = candidate if candidate in matches else matches[0]
    if edits.sha(chosen) != digest:
        raise ValueError('A displayed image changed.')
    return {'path': str(chosen), 'sha256': digest}


def read_understanding(round_root) -> dict:
    """Return UnderstandingDTO with confined file paths/hashes in place of URLs."""
    root = Path(round_root).resolve()
    doc, understanding, loaded = _load_sealed(root)
    from .owner_captures import load_round_captures
    captures = {row['item_id']: row for row in load_round_captures(root, doc)}
    rows = []
    for item, preview in loaded:
        capture = captures.get(item['item_id'])
        row = {'item_id': item['item_id'], 'number': item['number'],
               'labels': capture['labels'] if capture else [],
               'request_text': capture['request_text'] if capture else doc['request_text'],
               'annotated_path': str(capture['annotated_path']) if capture else None,
               'annotated_sha256': capture['annotated_sha256'] if capture else None,
               'interpretation': item['interpretation'], 'disposition': item['disposition'],
               'reason': item.get('reason', ''), 'fallback_allowed': item['disposition'] != 'previewed',
               'options': []}
        for option in preview['options'] if preview else []:
            images = [{**_image(root, understanding['evidence_files'], image),
                       'view': image.get('view', image.get('ref', '')),
                       'panels': ['photo', 'before', 'option']}
                      for image in [*option.get('full_pairs', []), *option.get('target_details', [])]]
            if not images:
                raise ValueError('The selected amount has no delivered native photo/before/option images.')
            row['options'].append({'option_id': option['id'], 'label': option['label'],
                                   'amount_text': option['amount_text'], 'images': images})
        rows.append(row)
    maps, seen = [], set()
    for relative in understanding['capture_receipts']:
        receipt = edits.read(edits.confined(root, relative))
        for view in receipt['views']:
            image = _image(root, understanding['evidence_files'], {'path': view['full_pair']})
            if image['sha256'] not in seen:
                maps.append({**image, 'label': view.get('ref', 'Labelled model map')})
                seen.add(image['sha256'])
    usage_path = root / 'work/owner_edit/understanding-usage.json'
    usage = edits.read(usage_path) if usage_path.is_file() else {}
    source_key = next(iter(captures.values()))['payload']['source']['delivery_key'] if captures else doc['source']['delivery_id']
    return {'schema': 'g3d.dev_understanding/1', 'edit_key': doc['edit_id'],
            'understanding_sha256': edits.sha(root / 'work/owner_edit/understanding.json'),
            'source': {'delivery_key': source_key, 'glb_sha256': doc['source']['export_sha256']},
            'run_settled_usd': usage.get('settled_usd'), 'items': rows,
            'map_images': maps, 'limitations': understanding.get('limitations', [])}


def _selection(understanding, choices, skipped):
    if not isinstance(choices, list) or not isinstance(skipped, list):
        raise ValueError('Choices and skipped items must be lists of item IDs.')
    by_id = {item['item_id']: item for item in understanding['items']}
    selected_by_id, skipped_by_id = {}, {}
    for row in choices:
        if (not isinstance(row, dict) or set(row) != {'item_id', 'option_id'}
                or not all(isinstance(v, str) and v for v in row.values())):
            raise ValueError('A choice may contain only item_id and option_id; operations cannot come from the browser.')
        item_id = row['item_id']
        if item_id not in by_id or item_id in selected_by_id:
            raise ValueError('Choose each original item at most once.')
        item = by_id[item_id]
        if item['disposition'] != 'previewed':
            raise ValueError('This item has no previewed option to apply.')
        matches = [option for option in (item.get('amount_preview') or {}).get('options', [])
                   if option['id'] == row['option_id']]
        if len(matches) != 1 or matches[0].get('item_id', item_id) != item_id:
            raise ValueError('Choose an exact sealed option of that item.')
        selected_by_id[item_id] = {**row, 'amount_text': matches[0].get('amount_text', matches[0]['label']),
                                   'item': item, 'option': matches[0]}
    for row in skipped:
        if (not isinstance(row, dict) or set(row) != {'item_id', 'note'}
                or not isinstance(row.get('item_id'), str) or not isinstance(row.get('note'), str)
                or not row['note'].strip()):
            raise ValueError('Each skipped item needs its item_id and a nonempty note.')
        item_id = row['item_id']
        if item_id not in by_id or item_id in selected_by_id or item_id in skipped_by_id:
            raise ValueError('Each item must be picked or skipped exactly once.')
        skipped_by_id[item_id] = copy.deepcopy(row)
    if set(selected_by_id) | set(skipped_by_id) != set(by_id):
        raise ValueError('Pick one option or provide a skip note for every item.')
    if not selected_by_id:
        raise ValueError('Pick at least one previewed item to create a new delivery.')
    return ([selected_by_id[i] for i in by_id if i in selected_by_id],
            [skipped_by_id[i] for i in by_id if i in skipped_by_id])


def validate_delivery_application(application, report):
    """C3 delivery seam: validate truthful composed-pick provenance after byte checks.

The delivery loader must first verify its manifest, packaged application hash,
strict export, retained print, checkpoint and AR evidence as it does for the
legacy owner-applied schema. This validates the additional C2 record only.
"""
    if not isinstance(application, dict) or not isinstance(report, dict):
        raise ValueError('Applied-picks provenance must be a record.')
    app = application
    if not isinstance(app.get('export'), dict) or not isinstance(report.get('owner_applied'), dict):
        raise ValueError('Applied picks need their export and packaged report binding.')
    if (app.get('schema') != 'g3d.owner_applied_choices/1' or app.get('status') != 'applied'
            or app.get('origin') != 'applied_picks' or type(app.get('api_requests')) is not int
            or app.get('api_requests') != 0 or app.get('visual_acceptance') is not False
            or app.get('settled_usd') != '0.000000' or type(app.get('tool_test')) is not bool
            or app.get('tool_test') is not report.get('tool_test')
            or report.get('delivery_kind') != 'owner_applied' or report.get('finished') is not True
            or report.get('accepted_finish') is not False or report.get('run_status') != 'owner_applied'
            or app.get('export', {}).get('sha256') != report.get('glb_sha256')
            or app.get('export', {}).get('fingerprint') != report.get('fingerprint')
            or app.get('source_basis') != report.get('owner_applied', {}).get('source_basis')):
        raise ValueError('Applied picks do not bind this validated delivery.')
    for key in ('origin', 'applied', 'notes', 'warnings', 'exactness'):
        if report.get(key) != app.get(key):
            raise ValueError('Applied-picks delivery provenance changed: ' + key)
    applied, skipped = app.get('applied'), app.get('skipped')
    if not isinstance(applied, list) or not applied or not isinstance(skipped, list) or app.get('notes') != skipped:
        raise ValueError('Applied picks must retain every choice and skipped note.')
    ids = set()
    for rows, fields in ((applied, {'item_id', 'option_id', 'amount_text'}),
                         (skipped, {'item_id', 'note'})):
        for row in rows:
            if (not isinstance(row, dict) or set(row) != fields
                    or any(not isinstance(value, str) or not value.strip() for value in row.values())
                    or row['item_id'] in ids):
                raise ValueError('Applied and skipped items need distinct IDs and sealed amounts or notes.')
            ids.add(row['item_id'])
    exactness = app.get('exactness') or {}
    if not isinstance(exactness, dict) or not isinstance(exactness.get('items'), list) or any(
            not isinstance(row, dict) for row in exactness['items']):
        raise ValueError('The exactness proof needs one record per selected item.')
    exported_geometry = exactness.get('exported_geometry')
    if not isinstance(exported_geometry, dict) or exported_geometry.get('ok') is not True:
        raise ValueError('The delivery needs the actual exported geometry comparison.')
    one = len(applied) == 1
    if (exactness.get('ok') is not True
            or exactness.get('mode') != ('single_glb_bytes' if one else 'per_item_geometry')
            or exactness.get('single_pick_glb_equal') is not (True if one else None)
            or app.get('warnings') != exactness.get('overlaps')
            or [(row.get('item_id'), row.get('option_id')) for row in exactness.get('items', [])] !=
               [(row['item_id'], row['option_id']) for row in applied]):
        raise ValueError('The delivery has no complete exactness proof for these picks.')
    applied_ids = {row['item_id'] for row in applied}
    overlaps = exactness.get('overlaps')
    if not isinstance(overlaps, list):
        raise ValueError('Applied-picks overlaps must be reported explicitly.')
    overlapping = set()
    for row in overlaps:
        if not isinstance(row, dict):
            raise ValueError('Each overlap must be an item/object record.')
        pair = row.get('item_ids', [])
        objects = row.get('objects')
        if (not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(i, str) for i in pair)
                or len(set(pair)) != 2
                or set(pair) - applied_ids or not isinstance(objects, list) or not objects
                or any(not isinstance(obj, dict) or not isinstance(obj.get('object'), str)
                       or not obj['object'] for obj in objects)):
            raise ValueError('An overlap must identify the selected items and affected objects.')
        overlapping.update(pair)
    for row in exactness['items']:
        overlap = row['item_id'] in overlapping
        if row.get('overlap_reported') is not overlap or row.get('exact_preview_geometry') is not (not overlap):
            raise ValueError('Each item must reproduce its preview or report its overlapping composition.')
    if one and overlaps:
        raise ValueError('A single pick cannot claim a multi-item interaction.')
    return True


def _prepare_apply(root, source_root, checkpoint, head):
    """Populate the exclusively claimed destination with the same preview inputs."""
    from . import jobs, print_freshness, temple_lineage
    jobs.load_job(source_root)
    shutil.copyfile(source_root / 'job.json', root / 'job.json')
    for name in ('images', 'reference_images', 'cameras'):
        if (source_root / name).exists():
            expected = edits.tree_hashes(source_root / name)
            shutil.copytree(source_root / name, root / name)
            if edits.tree_hashes(root / name) != expected:
                raise ValueError('The private apply inputs differ from the source.')
    print_freshness.carry_evidence(source_root, root)
    temple_lineage.carry_evidence(source_root, root)
    seed = root / 'work/preview-source.blend'
    seed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(checkpoint, seed)
    if edits.sha(seed) != head['sha256']:
        raise ValueError('The private source checkpoint bytes changed.')
    return seed


def _run_apply(root, source_root, doc, understanding, selected, skipped, confirmation_text):
    authority_sha256 = edits.sha(source_root / 'work/owner_edit/understanding.json')
    head = CheckpointIndex(source_root / 'checkpoints').head
    checkpoint = edits.confined(source_root, 'checkpoints/' + head['file'])
    seed = _prepare_apply(root, source_root, checkpoint, head)
    scene = JobScene(root, captured_seed=seed, captured_fingerprint=doc['source']['fingerprint'])
    try:
        scene.start()
        if scene.owner_edit is not None or scene.s.fingerprint() != doc['source']['fingerprint']:
            raise ValueError('Local apply must start from the exact unchanged source checkpoint.')
        source, _ = owner_edits.snapshot(scene, 'picks-source')
        sealed_source = edits.read(edits.confined(source_root, understanding['snapshot_path']))
        if source['fingerprint'] != sealed_source['fingerprint']:
            raise ValueError('The apply source differs from the authenticated model map.')
        receipts, operations = [], []
        for index, pick in enumerate(selected):
            current, path = owner_edits.snapshot(scene, 'picks-before')
            group = pick['operations']
            M.plan(current, group)  # closed solid, thickness, crossing and marking checks before writing
            expected = owner_edits.expected_positions(current, group)
            result = scene.s.call_mutating('host_owner_choices', 'owner_edit_apply', {
                'snapshot_path': str(path), 'snapshot_sha256': M.digest(current), 'operations': group},
                deadline_s=OWNER_APPLY_DEADLINE_S, call_id='picks-apply-' + str(index),
                label='Owner-selected item ' + pick['item_id'])
            if result.get('status') != 'ok':
                raise ValueError('The selected item failed a hard check: ' + str(result))
            after, _ = owner_edits.snapshot(scene, 'picks-after')
            check = owner_edits.verify_snapshots(current, after, group)
            if not check.get('ok') or any(not np.array_equal(np.asarray(obj['vertices_local']), expected[obj['id']])
                                          for obj in after['objects']):
                raise ValueError('The item did not reproduce its sealed operation amounts.')
            operations.extend(group)
            receipts.append({'item_id': pick['item_id'], 'option_id': pick['option_id'],
                             'operations': group, 'checks': check, 'fingerprint': after['fingerprint']})
        current, _ = owner_edits.snapshot(scene, 'picks-final')
        scope = owner_edits.verify_snapshots(source, current, operations)
        if not scope.get('ok'):
            raise ValueError('The composed picks failed scope verification.')
        exported = scene.seeing.export_head('picks-export')
        if (exported.get('status') != 'exported' or not exported.get('validate_spec', {}).get('ok')
                or not exported.get('print_check', {}).get('ok')
                or exported.get('fingerprint') != scene.s.fingerprint()):
            raise ValueError('The composed picks failed strict export or retained-print verification.')
        exactness = owner_apply.verify_choice_exactness(source, selected, current, exported['sha256'])
        exactness['exported_geometry'] = owner_apply.verify_choice_exports(
            selected[0]['source_export']['path'], selected, exported['path'])
        for overlap in exactness['exported_geometry']['overlaps']:
            matching = next((row for row in exactness['overlaps'] if row['item_ids'] == overlap['item_ids']), None)
            if matching is None:
                exactness['overlaps'].append(copy.deepcopy(overlap))
            else:
                matching['exported_objects'] = overlap['objects']
                matching['exported_attributes'] = overlap['attributes']
        overlapped = {item for row in exactness['overlaps'] for item in row['item_ids']}
        for row in exactness['items']:
            row.update(exact_preview_geometry=row['item_id'] not in overlapped,
                       overlap_reported=row['item_id'] in overlapped)
        from .tools import arview
        ar = arview.capture(root, exported, 'picks-ar')
        exit_head = scene.exit_checkpoint()
        applied = [{key: pick[key] for key in ('item_id', 'option_id', 'amount_text')} for pick in selected]
        application = {'schema': 'g3d.owner_applied_choices/1', 'status': 'applied', 'origin': 'applied_picks',
            'tool_test': bool(understanding.get('scripted')), 'visual_acceptance': False,
            'api_requests': 0, 'settled_usd': '0.000000', 'applied': applied,
            'skipped': skipped, 'notes': skipped, 'warnings': exactness.get('overlaps', []),
            'confirmation_text': confirmation_text, 'request_text': doc['request_text'],
            'understanding_sha256': edits.sha(source_root / 'work/owner_edit/understanding.json'),
            'source': doc['source'], 'source_head': head, 'checkpoint': scene.s.head,
            'exactness': exactness, 'scope_check': scope, 'apply_receipts': receipts,
            'export': exported, 'images': [], 'source_basis': {'basis': doc['source']['basis'],
                'basis_sha256': edits.sha(root / 'cameras' / doc['source']['basis'] / 'basis.json')}}
        # Revalidate all authority/evidence after the potentially long Blender run.
        if (_load_sealed(source_root)[:2] != (doc, understanding)
                or edits.sha(source_root / 'work/owner_edit/understanding.json') != authority_sha256):
            raise ValueError('The sealed understanding changed during application.')
        procs.write_json_atomic(root / 'work/owner_edit/applied-picks.json', application)
        delivery.deliver_owner_applied(root, exported, exit_head, ar, application)
        return root
    finally:
        scene.close()


def _remove_private_tree(root):
    """Remove this transaction's private copy, including sealed read-only checkpoints."""
    root = Path(root).resolve()

    def writable_retry(function, name, error):
        path = Path(name)
        if not isinstance(error, PermissionError):
            raise error
        if (path.is_symlink() or path.resolve() != path.absolute()
                or not path.resolve().is_relative_to(root)):
            raise ValueError('Cleanup cannot change permissions outside its private copy.') from error
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
        function(path)

    shutil.rmtree(root, onexc=writable_retry)




def _exact_disjoint_shading_picks(snapshot, object_id, picks, verified=None):
    """A read-only sufficient proof for separate same-part shading transactions."""
    from glasses3d.g3d import owner_surface as S
    source = M.objects(snapshot)
    verified = {} if verified is None else verified
    influence_sets = []
    try:
        before = source[object_id]
        for pick in picks:
            own = [operation for operation in pick['operations']
                   if operation['scope']['object_id'] == object_id]
            if not own or any(operation['kind'] != 'repair_shading' for operation in own):
                return False
            # An attached marking is another normal writer, even when a direct
            # shading operation also happens to name that object.
            if any(attachment['object_id'] == object_id for operation in pick['operations']
                   for attachment in operation.get('marking_attachments', [])):
                return False
            after = M.objects(pick['snapshot'])[object_id]
            geometry = ('vertices_local', 'vertices_world_mm', 'polygons', 'triangles',
                        'triangle_polygons', 'edges', 'loops')
            if any(before.get(key) != after.get(key) for key in geometry):
                return False
            influence = set()
            for operation in own:
                scope = operation['scope']
                actual = M.normal_faces(before, scope['affected_polygon_ids'])
                declared = scope.get('normal_affected_polygon_ids')
                if declared is not None and set(declared) != actual:
                    return False
                influence.update(actual)
            if not influence or any(influence.intersection(other) for other in influence_sets):
                return False
            influence_sets.append(influence)
            item_id = pick['item_id']
            if item_id not in verified:
                # Derive authority from source + operations, never marker presence.
                verified[item_id] = S.verify(snapshot, pick['snapshot'], pick['operations']).get('ok') is True
            if not verified[item_id]:
                return False
    except (M.OwnerMeshError, ValueError, TypeError, KeyError, IndexError):
        return False
    return True


def _require_composable_picks(source_root, doc, understanding, selected):
    """Allow independently verified disjoint shading; keep other normal pairs out."""
    if len(selected) < 2:
        return
    from glasses3d.g3d import owner_surface

    # Surface operations plan custom normals; material-only edits short-circuit
    # that writer. Finite coordinate edits keep their existing exactness path
    # when their sealed intended normals, smoothing and topology stay unchanged.
    normal_kinds = owner_surface.OPERATION_KINDS - {'material_adjust'}
    finite_kinds = M.OPERATION_KINDS - owner_surface.OPERATION_KINDS
    path = edits.confined(source_root, understanding['snapshot_path'])
    payload = path.read_bytes()
    if understanding['evidence_files'].get(path.relative_to(source_root).as_posix()) != hashlib.sha256(payload).hexdigest():
        raise ValueError('The source snapshot for these previews is missing or changed.')
    snapshot = json.loads(payload)
    if snapshot.get('fingerprint') != doc['source']['fingerprint']:
        raise ValueError('The source snapshot differs from the authenticated model map.')
    source = M.objects(snapshot)

    def normal_state(obj):
        witness = obj.get('normal_intent')
        intended = (witness['intent_normals'] if isinstance(witness, dict)
                    and isinstance(witness.get('intent_normals'), list)
                    and all(witness.get(key) is True for key in
                            ('encoded_matches', 'normal_space_matches', 'decoded_within_quantization'))
                    else obj['corner_normals'])
        return (intended, obj.get('polygon_smooth'), obj.get('polygons'), obj.get('edges'),
                obj.get('loops'), len(obj['vertices_local']))

    owners = {}
    for pick in selected:
        preview = M.objects(pick['snapshot'])
        touched = set()
        for operation in pick['operations']:
            oid = operation['scope']['object_id']
            if operation['kind'] in normal_kinds or (operation['kind'] in finite_kinds
                    and normal_state(source[oid]) != normal_state(preview[oid])):
                touched.add(oid)
            # A support surface is not changed by transform_marking. Actual
            # transported marking normals are separately bound in attachments.
            for attachment in operation.get('marking_attachments', []):
                mid = attachment['object_id']
                if normal_state(source[mid]) != normal_state(preview[mid]):
                    touched.add(mid)
        for oid in sorted(touched):
            owners.setdefault(oid, []).append(pick['item_id'])
    picks_by_id = {pick['item_id']: pick for pick in selected}
    verified = {}
    for oid, item_ids in owners.items():
        if len(item_ids) > 1:
            if _exact_disjoint_shading_picks(snapshot, oid,
                    [picks_by_id[item_id] for item_id in item_ids], verified):
                continue
            raise ValueError(
                f'Items {item_ids[0]} and {item_ids[1]} both change the shading of the same part, '
                'which cannot be combined exactly yet. Pick an option for one of them now '
                '(choose None of these for the other) and send the other again as a new edit.')


def apply_choices(round_root, understanding_sha256, choices, skipped, confirmation_text, destination=None) -> Path:
    """Apply sealed IDs in original item order to a fresh job; failure removes the private copy."""
    source_root = Path(round_root).resolve()
    artifact = edits.confined(source_root, 'work/owner_edit/understanding.json')
    if not isinstance(understanding_sha256, str) or edits.sha(artifact) != understanding_sha256:
        raise ValueError('The understanding changed. Reload its choices before applying.')
    if not isinstance(confirmation_text, str) or not confirmation_text.strip():
        raise ValueError('The owner confirmation text is required.')
    doc, understanding, loaded = _load_sealed(source_root)
    if edits.sha(artifact) != understanding_sha256:
        raise ValueError('The understanding changed while its choices were being loaded.')
    selected, notes = _selection(understanding, choices, skipped)
    previews = {item['item_id']: preview for item, preview in loaded}
    for pick in selected:
        option = next(row for row in previews[pick['item_id']]['options'] if row['id'] == pick['option_id'])
        snapshot_path = edits.confined(source_root, option['after_snapshot']['path'])
        snapshot_bytes = snapshot_path.read_bytes()
        snapshot_sha = option['after_snapshot']['sha256']
        if (hashlib.sha256(snapshot_bytes).hexdigest() != snapshot_sha
                or understanding['evidence_files'].get(snapshot_path.relative_to(source_root).as_posix()) != snapshot_sha):
            raise ValueError('The selected preview snapshot is missing or changed.')
        pick.update(operations=copy.deepcopy(option['operations']), export=copy.deepcopy(option['export']),
                    source_export=copy.deepcopy(previews[pick['item_id']]['before_export']),
                    amount_text=option['amount_text'],
                    snapshot=json.loads(snapshot_bytes))
        before = pick['source_export']
        before_path = edits.confined(source_root, before['path'])
        if (understanding['evidence_files'].get(before_path.relative_to(source_root).as_posix()) != before['sha256']
                or edits.sha(before_path) != before['sha256']):
            raise ValueError('The source export for this preview is missing or changed.')
    if len({pick['source_export']['sha256'] for pick in selected}) != 1:
        raise ValueError('All selected previews must share the exact same source export.')
    _require_composable_picks(source_root, doc, understanding, selected)
    root = Path(destination).resolve() if destination is not None else edits.RUNTIME_ROOT / ('p-' + uuid4().hex[:10])
    if root.exists() or root.is_relative_to(source_root) or source_root.is_relative_to(root):
        raise ValueError('The apply destination must be a new folder outside the source round.')
    original_job = doc['source'].get('job')
    if original_job and root.is_relative_to(Path(original_job).resolve()):
        raise ValueError('The apply destination must stay outside the unchanged source job.')
    # Only this non-existing, resolved exact destination belongs to this transaction.
    owned = False
    try:
        root.mkdir(parents=True, exist_ok=False)
        owned = True
        return _run_apply(root, source_root, doc, understanding, selected, notes, confirmation_text)
    except BaseException:
        if owned and root.exists() and root.resolve() == root and not root.is_symlink():
            _remove_private_tree(root)
        raise
