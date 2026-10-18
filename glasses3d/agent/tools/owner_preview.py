"""Read-only owner amount choices, rendered from isolated copies of one verified head."""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import re
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace

import numpy as np
from agents.tool_context import ToolContext
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...g3d import owner_mesh as M, owner_markings
from ...g3d import owner_surface
from .. import edits, jobs, owner_edits, procs
from ..job_scene import JobScene
from ..tool_timeouts import OWNER_APPLY_DEADLINE_S, PREVIEW_OPTIONS_MAX
from . import look, model_map, look_composition, native_panels

SCHEMA = 'g3d.owner_amount_preview/1'


def preview_workers(tasks):
    from . import free_commit_bytes
    return min(tasks, max(1, min(16, math.floor(free_commit_bytes() / 2**30 / 1.3))))


def _option_process(root, folder, checkpoint, head, source, operations, refs, basis):
    """Each independent option owns a separate Blender process and local evidence."""
    from ..offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
    disable_sdk_tracing()
    procs.opt_out_self()
    with LoopbackOnly(), ProductionTransportTripwire(), procs.DescendantsOptOut():
        return _run_option(SimpleNamespace(root=root), folder, checkpoint, head, source, operations, refs, basis)


def revealing_refs(root, basis, operations, refs):
    """Depth movements need transverse photos; never infer the operation from prose."""
    bs = look.C.load_basis(root, basis)
    usable = bs.usable_refs()
    wanted = list(usable) if refs is None else [look.C.ref_id(name) for name in refs]
    transverse = any(op['kind'] in ('translate_region', 'taper_region') and
                     abs(op['params']['delta_mm'][0]) > 0 for op in operations)
    if transverse:
        wanted += [rid for rid in usable if bs.refs[rid].get('view_kind') in ('top', 'rear', 'side')]
    return list(dict.fromkeys(wanted))


def projection_effects(source, options, operations, before):
    """Native projected displacement per shown operation/view; side depth can be invisible."""
    rows = []
    for choice in options:
        for op in operations:
            oid = op['scope']['object_id']
            ids = [r['vertex_id'] for r in op['scope']['vertex_weights'] if r['weight'] > 0]
            a, b = M.objects(source)[oid], M.objects(choice['snapshot'])[oid]
            for view in before['views']:
                camera = view['camera']
                uv0, z0 = model_map.G.project(model_map._asset_points(a, camera)[ids], camera)
                uv1, z1 = model_map.G.project(model_map._asset_points(b, camera)[ids], camera)
                valid = (z0 > 0) & (z1 > 0) & np.isfinite(uv0).all(axis=1) & np.isfinite(uv1).all(axis=1)
                delta = np.linalg.norm(uv1[valid] - uv0[valid], axis=1)
                rows.append({'option': choice['id'], 'operation': op['id'], 'ref': view['ref'],
                             'view_kind': view['view_kind'], 'max_displacement_px': float(delta.max()) if len(delta) else None,
                             'note': 'Projection only; includes occluded support. Judge the photo-first rendered close-up.'})
    return rows


class PreviewOption(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str
    label: str
    scale: float = Field(strict=True, gt=0)


def _options(options):
    if not isinstance(options, (list, tuple)):
        raise ValueError('preview options must be a list')
    rows = [o.model_dump() if isinstance(o, PreviewOption) else copy.deepcopy(o) for o in options]
    if not 2 <= len(rows) <= PREVIEW_OPTIONS_MAX:
        raise ValueError('preview needs two or three amount options')
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'label', 'scale'}:
            raise ValueError('each option takes only id, label and scale')
        if not isinstance(row['id'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', row['id']):
            raise ValueError('option IDs must be safe stable letters, numbers, underscores or hyphens')
        if not isinstance(row['label'], str) or not row['label'].strip() or len(row['label']) > 160:
            raise ValueError('option labels must be nonempty and at most 160 characters')
        scale = row['scale']
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
            raise ValueError('option scales must be positive finite numbers')
    if len({r['id'].casefold() for r in rows}) != len(rows) or len({r['scale'] for r in rows}) != len(rows):
        raise ValueError('option IDs and scales must be distinct')
    return rows


def scaled_operations(operations, scale):
    """Change only the amount; scope, direction, axis, kind and attachment remain exact."""
    result = copy.deepcopy(operations)
    for op in result:
        kind, params = op['kind'], op['params']
        if kind in ('translate_region', 'taper_region'):
            values = np.asarray(params['delta_mm'], float)
            if values.shape != (3,) or not np.isfinite(values).all():
                raise ValueError('delta_mm must be three finite numbers')
            params['delta_mm'] = (values*scale).tolist()
        elif kind == 'rotate_region':
            if isinstance(params['delta_deg'], bool) or not isinstance(params['delta_deg'], (int, float)):
                raise ValueError('delta_deg must be finite')
            params['delta_deg'] *= scale
        elif kind in owner_surface.OPERATION_KINDS:
            params['strength'] *= scale
            if not 0 < params['strength'] <= 1:
                raise ValueError('scaled surface strength must be greater than zero through one')
        else:
            raise ValueError('unknown preview operation')
        if any(not math.isfinite(float(x)) for x in params.get('delta_mm', [params.get('delta_deg', 0)])):
            raise ValueError('scaled amount must remain finite')
    return result


def _positions_digest(positions):
    digest = hashlib.sha256()
    for oid, vertices in sorted(positions.items()):
        digest.update(oid.encode())
        digest.update(np.asarray(vertices, dtype='<f4').tobytes())
    return digest.hexdigest()


def _bound(root, relative, digest=None):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('preview evidence references must be relative to their job')
    path = edits.confined(root, relative)
    if not path.is_file() or (digest is not None and edits.sha(path) != digest):
        raise ValueError(f'preview evidence changed or is missing: {relative}')
    return path


def _relative(root, path):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def _archive_evidence(root, source, *, source_root=None):
    """Short canonical confirmation paths survive nested Windows owner-stage copies."""
    source_root = Path(source_root) if source_root is not None else root
    source = edits.confined(source_root, _relative(source_root, source))
    digest = edits.sha(source)
    if source.suffix.lower() not in ('.png', '.json', '.glb'):
        raise ValueError('unsupported preview evidence file type')
    relative = 'work/pv-assets/'+digest[:24]+source.suffix.lower()
    target = edits.confined(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or edits.sha(target) != digest:
            raise ValueError('preview evidence archive hash-prefix collision or changed bytes')
    else:
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        if edits.sha(target) != digest:
            raise ValueError('preview evidence archive copy differs from its source')
    return {'source_path': str(source), 'path': relative, 'sha256': digest}


def _capture_record(captured):
    """Only evidence used by the amount comparison; raw reports retain full render provenance."""
    keys = ('schema', 'status', 'basis', 'basis_sha256', 'cameras_sha256', 'export_sha256',
            'checkpoint', 'fixed_scale', 'report', 'report_sha256', 'capture_reports')
    fields = ('ref', 'view_kind', 'camera', 'photo_sha256', 'photo_path', 'drawing_path',
              'background', 'frozen_temple_pose')
    return {**{key: captured[key] for key in keys if key in captured},
            'views': [{key: view[key] for key in fields if key in view} for view in captured['views']]}


def _source(scene):
    doc = edits.load(scene.root)
    if not doc or doc['phase'] != 'understand':
        raise ValueError('amount previews are only available in the read-only understand phase')
    head = copy.deepcopy(scene.s.head)
    if not head or scene.s.fingerprint() != head['fingerprint'] or head['fingerprint'] != doc['source']['fingerprint']:
        raise ValueError('preview needs the unchanged selected delivery and verified current head')
    checkpoint = edits.confined(scene.root, 'checkpoints/'+head['file'])
    if edits.sha(checkpoint) != head['sha256']:
        raise ValueError('source checkpoint bytes changed')
    return doc, head, checkpoint


def _unchanged(scene, head, checkpoint):
    if scene.s.head != head or scene.s.fingerprint() != head['fingerprint'] or edits.sha(checkpoint) != head['sha256']:
        raise ValueError('preview source changed; no option can be accepted')


def _prepare(scene, folder, checkpoint, head):
    """No inherited owner authority, conversation, run settings or provider session."""
    jobs.load_job(scene.root)
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(scene.root/'job.json', folder/'job.json')
    for name in ('images', 'reference_images', 'cameras'):
        if (scene.root/name).exists():
            expected = edits.tree_hashes(scene.root/name)
            shutil.copytree(scene.root/name, folder/name)
            if edits.tree_hashes(folder/name) != expected:
                raise ValueError('private preview inputs differ from the source')
    seed = folder/'work/preview-source.blend'
    seed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(checkpoint, seed)
    if edits.sha(seed) != head['sha256']:
        raise ValueError('private preview seed differs from the verified checkpoint')
    return seed


def _run_option(scene, folder, checkpoint, head, source, operations, refs, basis):
    seed = _prepare(scene, folder, checkpoint, head)
    child = JobScene(folder, captured_seed=seed, captured_fingerprint=head['fingerprint'])
    try:
        child.start()
        if child.owner_edit is not None or child.s.fingerprint() != head['fingerprint']:
            raise ValueError('private preview must start from the exact source without edit authority')
        before, path = owner_edits.snapshot(child, 'preview-before')
        if before['fingerprint'] != source['fingerprint']:
            raise ValueError('private preview snapshot differs from the shown source')
        result = child.s.call_mutating('preview_owner_edit', 'owner_edit_apply', {
            'snapshot_path': str(path), 'snapshot_sha256': M.digest(before), 'operations': operations},
            deadline_s=OWNER_APPLY_DEADLINE_S, call_id='preview-apply', label='Read-only owner amount option')
        if result.get('status') != 'ok':
            raise ValueError('preview operation refused: '+str(result))
        after, after_path = owner_edits.snapshot(child, 'preview-after')
        checks = owner_edits.verify_snapshots(before, after, operations)
        expected = owner_edits.expected_positions(before, operations)
        if any(not np.array_equal(np.asarray(obj['vertices_local']), expected[obj['id']]) for obj in after['objects']):
            raise ValueError('private preview does not equal its exact proposed amounts')
        exported = child.seeing.export_head('preview-export')
        if exported.get('status') != 'exported' or not exported.get('validate_spec', {}).get('ok'):
            raise ValueError('preview option failed strict specification export')
        captured = look.capture(folder, exported, 'preview-option', refs=refs, basis=basis)
        if captured.get('status') != 'ok':
            raise ValueError('preview option evidence unavailable: '+str(captured.get('message', captured)))
        return {'checks': {**checks, 'confirmed_amounts_match': True, 'strict_spec': exported['validate_spec']},
                'after_evidence': _capture_record(captured), 'export': exported, 'snapshot': after,
                'before_snapshot': {'path': str(path), 'sha256': edits.sha(path)},
                'after_snapshot': {'path': str(after_path), 'sha256': edits.sha(after_path)},
                'seed_sha256': edits.sha(seed), 'initial_fingerprint': before['fingerprint'],
                'private_has_no_owner_edit': child.owner_edit is None}
    finally:
        child.close()


def _boxes(source, options, operations, before, legend):
    """One shared camera pixel box per operation's influence across all amount choices."""
    groups = {}
    for op in operations:
        row = groups.setdefault(op['id'], {'object_id': op['scope']['object_id'], 'vertices': set(), 'target_ids': set()})
        row['vertices'].update(v['vertex_id'] for v in op['scope']['vertex_weights'] if v['weight'] != 0)
        row['target_ids'].update(op['target_ids'])
    labels = {row['target_id']: row['label'] for row in legend}
    snapshots = [source, *[o['snapshot'] for o in options]]
    result = {}
    for view in before['views']:
        camera = view['camera']
        boxes = []
        for index, group in enumerate(groups.values()):
            oid = group['object_id']
            projected = []
            for snapshot in snapshots:
                obj = M.objects(snapshot)[oid]
                points = model_map._asset_points(obj, camera)[sorted(group['vertices'])]
                uv, depth = model_map.G.project(points, camera)
                valid = (depth > 0) & np.isfinite(uv).all(axis=1)
                projected.extend(uv[valid])
            if not projected:
                continue
            xy = np.asarray(projected)
            lo, hi = np.floor(xy.min(0)-24).astype(int), np.ceil(xy.max(0)+25).astype(int)
            box = [max(0, int(lo[0])), max(0, int(lo[1])), min(camera['width'], int(hi[0])), min(camera['height'], int(hi[1]))]
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            boxes.append({'id': f'detail-{index+1}', 'object_id': oid, 'target_ids': sorted(group['target_ids']),
                          'labels': [labels.get(t, t) for t in sorted(group['target_ids'])], 'box': box})
        result[view['ref']] = boxes
    if not any(result.values()):
        raise ValueError('selected photo cameras do not show any proposed target in frame')
    return result


def _triple(paths, destination, title, option_label, *, option_id=None, box=None):
    opened = []
    for path in paths:
        with Image.open(path) as image:
            opened.append(image.convert('RGB'))
    if len({im.size for im in opened}) != 1:
        raise ValueError('preview photo/before/option must share the exact camera pixel grid')
    crop = box if box is not None else [0, 0, *opened[0].size]
    headers = ['PHOTO', 'BEFORE', 'OPTION '+(option_id or option_label.split(':', 1)[0])]
    record, tiles = native_panels.comparison(opened, crop, destination, title, headers,
                                             gap=0, font=model_map._font(18))
    for item in [record, *tiles]:
        item.update(view=title+'; native photo / before / '+option_label,
                    kind='owner_amount_detail' if box else 'owner_amount_full', box=box)
    return record, tiles


def _pictures(root, directory, before, options, boxes):
    images, files = [], {}
    old = {r['ref']: r for r in before['views']}
    for option in options:
        after = {r['ref']: r for r in option['after_evidence']['views']}
        if old.keys() != after.keys():
            raise ValueError('preview options must render the same frozen photo references')
        pairs, details = [], []
        for rid, row in old.items():
            sources = []
            final = after[rid]
            if row['camera'] != final['camera'] or edits.sha(row['photo_path']) != edits.sha(final['photo_path']):
                raise ValueError('preview camera or source photo differs between options')
            paths = [row['photo_path'], row['drawing_path'], final['drawing_path']]
            label = f"{option['id']}: {option['label']}"
            full, delivered = _triple(paths, directory/f"{option['id']}-{rid}-full.png", rid, label, option_id=option['id'])
            pairs.append(full); sources.extend(delivered)
            for group in boxes[rid]:
                path = directory/f"{option['id']}-{rid}-{group['id']}.png"
                title = rid+'; '+', '.join(group['labels'])
                detail, delivered = _triple(paths, path, title, label, option_id=option['id'], box=group['box'])
                detail.update(object_id=group['object_id'], target_ids=group['target_ids'])
                details.append(detail); sources.extend(delivered)
            for item in sources:
                item.update(ref=rid, option_id=option['id'])
                files[_relative(root, item['path'])] = item['sha256']
            pages = look_composition.compose(sources, directory/f"{option['id']}-{rid}-combined", rid)
            for item in pages:
                item.update(option_id=option['id'], native_pixels=True)
            images.extend(pages)
            for item in [*pairs[-1:], *details]:
                files[_relative(root, item['path'])] = item['sha256']
        option['full_pairs'], option['target_details'] = pairs, details
    files.update({_relative(root, row['path']): row['sha256'] for row in images})
    return images, files


def preview(scene, call_id, map_id, map_sha256, capture_receipts, operations_json, options, refs=None):
    doc, head, checkpoint = _source(scene)
    try:
        choices = _options(options)
        operations = jobs._json(operations_json, 'preview operations')
        if not isinstance(operations, list) or not operations:
            raise ValueError('operations_json must contain a nonempty list of proposed operations')
        evidence = model_map.validate_understanding(scene.root, map_id, map_sha256, capture_receipts, operations)
        source = edits.read(evidence['snapshot_path'])
        if source['fingerprint'] != head['fingerprint'] or evidence['source']['fingerprint'] != head['fingerprint']:
            raise ValueError('preview map is not of the selected unchanged delivery')
        base_operations = owner_markings.seal(source, evidence['operations'])
        refs = revealing_refs(scene.root, doc['source']['basis'], base_operations, refs)
        if len({op['id'] for op in base_operations}) != len(base_operations):
            raise ValueError('preview operations must have distinct IDs')
        choices = [{**choice, 'operations': scaled_operations(base_operations, choice['scale'])} for choice in choices]
        special = owner_surface.active(base_operations)
        position_digests = {owner_surface.signature(source) if special else
                            _positions_digest({obj['id']: obj['vertices_local'] for obj in source['objects']})}
        for choice in choices:
            M.plan(source, choice['operations'])  # reject impossible amounts before starting any private worker
            digest = (owner_surface.signature(owner_surface.state(source, choice['operations'])[0]) if special else
                      _positions_digest(owner_edits.expected_positions(source, choice['operations'])))
            if digest in position_digests:
                raise ValueError('preview choices must change the source and produce distinct actual geometry or shading')
            position_digests.add(digest)
        directory = scene.root/'work/pv'/hashlib.sha256(call_id.encode()).hexdigest()[:20]
        directory.mkdir(parents=True, exist_ok=False)
        before_export = scene.seeing.export_head(call_id+'-preview-before')
        if before_export.get('status') != 'exported' or not before_export.get('validate_spec', {}).get('ok'):
            raise ValueError('source needs a strict valid export for amount comparison')
        # A short host-owned root also works when understand is itself a deeply
        # nested specialist. Every worker closes before this directory is removed.
        with tempfile.TemporaryDirectory(prefix='g3d-pv-', ignore_cleanup_errors=True) as temporary:
            scratch = Path(temporary).resolve()
            baseline = scratch/'before'
            _prepare(scene, baseline, checkpoint, head)
            baseline_export = baseline/'work/source.glb'
            shutil.copyfile(edits.confined(scene.root, _relative(scene.root, before_export['path'])), baseline_export)
            if edits.sha(baseline_export) != before_export['sha256']:
                raise ValueError('private source export differs from its verified bytes')
            before_export = {**before_export, 'path': str(baseline_export)}
            before = look.capture(baseline, before_export, 'before', refs=refs, basis=doc['source']['basis'])
            if before.get('status') != 'ok':
                raise ValueError('source preview evidence unavailable: '+str(before.get('message', before)))
            before = _capture_record(before)
            rendered = []
            from . import single_threaded_blas
            workers = preview_workers(len(choices))
            with single_threaded_blas(), procs.DescendantsOptOut(), ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_option_process, scene.root, scratch/f'option-{index}', checkpoint,
                                       head, source, choice['operations'], refs, doc['source']['basis'])
                           for index, choice in enumerate(choices)]
                for choice, future in zip(choices, futures):
                    rendered.append({**choice, **future.result()})
                    _unchanged(scene, head, checkpoint)
            effects = projection_effects(source, rendered, base_operations, before)
            boxes = _boxes(source, rendered, base_operations, before, evidence.get('legend', []))
            panels = scratch/'panels'; panels.mkdir()
            images, _ = _pictures(scratch, panels, before, rendered, boxes)
            for row in rendered:
                del row['snapshot']
            archives = {}

            def canonical(value, field=None):
                if isinstance(value, dict):
                    return {key: canonical(item, key) for key, item in value.items()}
                if isinstance(value, list):
                    return [canonical(item, field) for item in value]
                if (field not in {'path', 'full_comparison_path', 'photo_path', 'drawing_path', 'report', 'receipt', 'capture_reports', 'glb'} or
                        not isinstance(value, str) or not Path(value).is_absolute()):
                    return value
                if value not in archives:
                    source_root = scratch if Path(value).is_relative_to(scratch) else scene.root
                    archives[value] = _archive_evidence(scene.root, value, source_root=source_root)
                return str(scene.root/archives[value]['path'])

            # Keep every usable path canonical. Original temporary paths occur
            # only in archived_evidence provenance and unchanged historical reports.
            before = canonical(before)
            before_export = canonical(before_export)
            rendered = [{**row, **{key: canonical(row[key]) for key in
                         ('checks', 'after_evidence', 'export', 'before_snapshot', 'after_snapshot',
                          'full_pairs', 'target_details')}} for row in rendered]
            images = canonical(images)
        files = {r['path']: r['sha256'] for r in evidence['evidence_files']}
        files[evidence['map_path']] = evidence['map_sha256']
        files.update({row['path']: row['sha256'] for row in archives.values()})
        relative = _relative(scene.root, directory/'preview.json')
        receipt = {'schema': SCHEMA, 'status': 'ok', 'call_id': call_id,
                   'source_fingerprint': head['fingerprint'], 'source_head': head, 'source_checkpoint_sha256': head['sha256'],
                   'map_id': map_id, 'map_sha256': map_sha256, 'capture_receipts': capture_receipts,
                   'base_operations': base_operations, 'options': rendered,
                   'following_markings': owner_markings.summary(base_operations), 'before_evidence': before,
                   'before_export': before_export,
                   'shared_target_boxes': boxes, 'images': images, 'image_delivery': 'separate',
                   'projection_effects': effects, 'workers': workers, 'selected_refs': refs,
                   'archived_evidence': list(archives.values()),
                   'archive_note': 'Verified archive copies are canonical confirmation evidence. Original temporary paths and unchanged raw report paths are historical rendering provenance; the temporary jobs are removed after all workers close.',
                   'evidence_files': files, 'preview_receipt': relative, 'source_unchanged': True,
                   'interpretation': 'Each option starts from the same source; scales multiply amounts only. No source edit or owner selection has occurred.'}
        _unchanged(scene, head, checkpoint)
        procs.write_json_atomic(directory/'preview.json', receipt)
        return {**receipt, 'preview_sha256': edits.sha(directory/'preview.json')}
    finally:
        _unchanged(scene, head, checkpoint)


def mark_delivered(scene, result, delivered=True):
    if result.get('status') != 'ok' or result.get('schema') != SCHEMA:
        return
    path = _bound(scene.root, result['preview_receipt'], result['preview_sha256'])
    procs.write_json_atomic(path.with_name('preview-delivery.json'), {
        'schema': 'g3d.owner_amount_preview_delivery/1', 'preview_sha256': edits.sha(path),
        'call_id': result['call_id'], 'delivered': bool(delivered)})


def load_verified(scene, relative_receipt):
    path = _bound(scene.root, relative_receipt)
    expected_root = (scene.root/'work/pv').resolve()
    if not path.is_relative_to(expected_root) or path.name != 'preview.json':
        raise ValueError('preview receipt must be an owner preview in this job')
    receipt = edits.read(path)
    delivery = edits.read(path.with_name('preview-delivery.json'))
    state_path = scene.root/'work/job_scene/state.json'
    unseen = set(edits.read(state_path).get('unseen', [])) if state_path.is_file() else set()
    unseen.update(getattr(scene, '_unseen', ()))
    if (receipt.get('schema') != SCHEMA or receipt.get('status') != 'ok' or
            delivery.get('schema') != 'g3d.owner_amount_preview_delivery/1' or delivery.get('delivered') is not True or
            delivery.get('preview_sha256') != edits.sha(path) or delivery.get('call_id') != receipt.get('call_id') or
            receipt.get('call_id') in unseen):
        raise ValueError('amount choices require successfully delivered unchanged preview evidence')
    doc, head, checkpoint = _source(scene)
    if (receipt['source_fingerprint'] != head['fingerprint'] or receipt['source_checkpoint_sha256'] != head['sha256'] or
            receipt['source_head'] != head):
        raise ValueError('preview source does not match this understanding')
    if not receipt.get('base_operations') or any(not option.get('operations') for option in receipt['options']):
        raise ValueError('amount preview requires nonempty executable operations')
    evidence = model_map.validate_understanding(scene.root, receipt['map_id'], receipt['map_sha256'],
                                               receipt['capture_receipts'], receipt['base_operations'])
    base = owner_markings.seal(edits.read(evidence['snapshot_path']), evidence['operations'])
    if base != receipt['base_operations']:
        raise ValueError('preview operations differ from the shown source map')
    _options([{key: option[key] for key in ('id', 'label', 'scale')} for option in receipt['options']])
    if any(row['operations'] != scaled_operations(base, row['scale']) for row in receipt['options']):
        raise ValueError('preview options changed more than the proposed amounts')
    for reference, digest in receipt['evidence_files'].items():
        _bound(scene.root, reference, digest)
    _unchanged(scene, head, checkpoint)
    return {**receipt, 'preview_sha256': edits.sha(path), 'preview_delivery': _relative(scene.root, path.with_name('preview-delivery.json')),
            'preview_delivery_sha256': edits.sha(path.with_name('preview-delivery.json'))}


def tool_for(scene, tool, call):
    @tool('preview_owner_edit')
    async def preview_owner_edit(ctx: ToolContext, map_id: str, map_sha256: str, capture_receipts: list[str],
                                 operations_json: str, options: list[PreviewOption], refs: list[str] | None = None):
        """Show two or three proposed amount choices during understand without changing the source model.
        Supply a nonempty list of executable operations_json supported by delivered labelled_model_map receipts.
        Unsupported and needs_clarification items grant no preview authority; never use no-op placeholders.
        Each option has a safe stable id, a plain label and a distinct positive scale. Scale changes only
        delta_mm (translate/taper), delta_deg (rotation), or strength for every surface, material, edge,
        thickness, scale, bend, marking and mesh-health operation (scaled strength must stay greater than zero
        through one). Discrete cleanup amounts may coincide; choose two or three actually distinct options.
        Targets, supports,
        axes and marking attachments stay fixed. Surface operations always need two or three distinct previews.
        Every option starts from the same verified checkpoint in an isolated worker, passes exact-operation,
        physical and strict-export checks, and uses the frozen photo cameras. Returns native photo/before/option
        triples and target close-ups at shared boxes, composed per photo; overflow uses additional images without
        shrinking pixels. Also returns following_markings and a hash-bound preview_receipt.
        No run, approval, estimate or source edit is started. A refused amount produces no selectable preview.
        Reuse the option id and preview receipt when asking the owner to choose in the understand reply.
        refs null uses every usable frozen camera; a list selects photo ids or original filenames.
        Transverse X movements also include available top, rear and side photos, because a side view alone
        cannot show depth. Projection effects report displacement in each view. Options use independent
        workers sized to free commit memory / 1.3 GiB, capped at 16, with one BLAS thread.
        """
        return await call(ctx, 'preview_owner_edit', lambda cid: preview(scene, cid, map_id, map_sha256,
                           capture_receipts, operations_json, options, refs=refs))
    return preview_owner_edit
