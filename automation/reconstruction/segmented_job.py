"""Photo-to-AR segmented reconstruction with durable, verified stage recovery.

The explicit segmented_ar_v1 route keeps the textured generation, infers optical
parts from known-camera render evidence, reduces geometry before preparing
optics, then recovers appearance. It produces reviewable candidates, not an
unmeasured promise of product fidelity. No deployment occurs here.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import numpy as np

from .segmented_providers import (SubmissionBudget, advance_mask, advance_tripo, immutable,
    pin, tripo_request, verified, _verify_tripo_download)
from qa.provider_benchmark import write_json, read_json, canonical, Client


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = dict(yaw_degrees=-90., display_width_mm=145., render_width=640, render_height=480,
                render_views=['front', 'angled', 'angled-opposite', 'back'], lens_triangles=20000,
                frame_triangles=100000, maximum_triangles=150000, maximum_bytes=15000000,
                simplification_error=.002, maximum_appearance_candidates=6)
DIRECTIONS = dict(front=[0, 0, 1], back=[0, 0, -1], left=[1, 0, 0], right=[-1, 0, 0],
                  angled=[.68, .22, 1], **{'angled-opposite': [-.68, .22, 1]})
AR_POLICY = dict(background_fixture='checker', environments=[dict(id='room', preset='room', intensity=.8),
    dict(id='broad', preset='broad_studio', intensity=.8), dict(id='side', preset='side_studio', intensity=.8)],
    ar_views=[dict(id='front', yaw_degrees=0, pitch_degrees=0, roll_degrees=0),
              dict(id='angled', yaw_degrees=35, pitch_degrees=0, roll_degrees=0),
              dict(id='rolled', yaw_degrees=0, pitch_degrees=0, roll_degrees=15),
              dict(id='back', type='asset-back')])


def _sha_json(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _provider_wait_status(result, pending_status):
    if result.get('status') in ('provider_failed', 'rejected', 'failed', 'cancelled', 'canceled', 'submission_uncertain'):
        return 'needs_review'
    return pending_status


def _copy(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if pin(source)['sha256'] != pin(target)['sha256']:
            raise ValueError('Existing snapshot differs')
    else:
        shutil.copyfile(source, target)
    return pin(target)


def validate_request(request):
    if request.get('schema_version') != 1 or request.get('pipeline') != 'segmented_ar_v1':
        raise ValueError('Expected segmented_ar_v1 version-one request')
    # the keys reconstruction.job.EVALUATION_REQUEST_KEYS refuses at the shared entry point: this route has no
    # evaluation reservation, so a request naming reserved photos is refused before any photo is captured
    reserved = sorted({'evaluation_photos', 'reserved_photo_ids'} & set(request))
    if reserved:
        raise ValueError('The segmented route does not support evaluation reservation; remove ' + ', '.join(reserved))
    product = request.get('product_id')
    if not isinstance(product, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', product):
        raise ValueError('Safe product_id required')
    photos = request.get('photos')
    if not isinstance(photos, list) or not 2 <= len(photos) <= 12:
        raise ValueError('Two to twelve existing photographs required')
    ids = [p.get('id') for p in photos]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', i) for i in ids):
        raise ValueError('Unique safe photo IDs required')
    if request.get('source', {}).get('kind') not in ('tripo', 'cached_tripo'):
        raise ValueError('Expected new Tripo generation or pinned cached Tripo directories')
    settings = dict(DEFAULTS)
    supplied = request.get('settings', {})
    if set(supplied) - set(settings):
        raise ValueError('Unknown segmented pipeline setting')
    settings.update(supplied)
    if not all(math.isfinite(settings[k]) for k in ('yaw_degrees', 'display_width_mm', 'simplification_error')):
        raise ValueError('Finite settings required')
    if not 60 <= settings['display_width_mm'] <= 250 or not 0 < settings['simplification_error'] <= .01:
        raise ValueError('Unsupported width or simplification error')
    for k in ('render_width', 'render_height', 'lens_triangles', 'frame_triangles', 'maximum_triangles', 'maximum_bytes', 'maximum_appearance_candidates'):
        if type(settings[k]) is not int or settings[k] <= 0:
            raise ValueError('Positive integer budgets required')
    if not 128 <= settings['render_width'] <= 1600 or not 128 <= settings['render_height'] <= 1600:
        raise ValueError('Evidence render resolution out of bounds')
    if not 2 <= settings['maximum_appearance_candidates'] <= 12:
        raise ValueError('Appearance candidate capacity must be 2..12 before submitting providers')
    if not 2 <= len(settings['render_views']) <= 6 or len(set(settings['render_views'])) != len(settings['render_views']) or set(settings['render_views']) - set(DIRECTIONS):
        raise ValueError('Distinct known evidence cameras required')
    return settings


class Journal:
    def __init__(self, output, request, settings):
        self.output = Path(output).resolve()
        self.path = self.output / 'journal.json'
        self.request = request
        self.settings = settings
        binding = _sha_json(dict(request=request, settings=settings))
        if self.path.exists():
            self.value = read_json(self.path)
            if self.value['request_binding'] != binding:
                raise ValueError('Changed request/settings require a new job directory')
        else:
            self.value = dict(schema_version=1, pipeline='segmented_ar_v1', request_binding=binding,
                              settings=settings, stages={}, accepted=False, quality_verdict='unmeasured')
        self.save()

    def save(self):
        write_json(self.path, self.value)

    def stage(self, name, function, *, recipe=None):
        from .job import _inventory, _verify_stage
        old = self.value['stages'].get(name)
        if old and old['status'] == 'complete':
            _verify_stage(self.output, old)
            if old.get('recipe') == recipe:
                return old['result']
        if old:
            self.value.setdefault('stage_history', {}).setdefault(name, []).append(old)
        attempt = (old.get('attempt', 0) if old else 0) + 1
        folder = self.output / 'stages' / name / f'attempt-{attempt:04d}'
        folder.mkdir(parents=True, exist_ok=False)
        stage = dict(status='running', attempt=attempt, directory=folder.relative_to(self.output).as_posix(), recipe=recipe)
        self.value['stages'][name] = stage
        self.save()
        try:
            result = function(folder)
            write_json(folder / 'stage-result.json', result)
            stage.update(status='complete', result=result, artifacts=_inventory(self.output, folder))
            self.save()
            return result
        except Exception as error:
            stage.update(status='failed', error_type=type(error).__name__, error=str(error))
            self.report('failed', failed_stage=name, error_type=type(error).__name__, reason=str(error))
            raise

    def report(self, status, **fields):
        report = dict(schema_version=1, pipeline='segmented_ar_v1', product_id=self.request['product_id'],
                      status=status, accepted=False, quality_verdict='unmeasured', **fields)
        self.value['status'] = status
        self.save()
        write_json(self.output / 'report.json', report)
        return report


def capture_photos(photos, folder):
    captured = []
    for row in photos:
        source = Path(row['path']).resolve()
        if row.get('sha256') and pin(source)['sha256'] != row['sha256']:
            raise ValueError('Input photo hash changed')
        image = _copy(source, folder / (row['id'] + source.suffix.lower()))
        captured.append(dict(id=row['id'], view=row.get('view', 'unknown'),
                             provider_input=row.get('provider_input', row.get('view') in ('front', 'left', 'back', 'right')), **image))
    return dict(photos=captured)


def capture_cached(source, folder, photos):
    generated = Path(source['generation_directory']).resolve()
    segmented = Path(source['segmentation_directory']).resolve()
    a, b = _verify_tripo_download(generated), _verify_tripo_download(segmented)
    generation_request = read_json(generated / 'request.json')
    available = {p['sha256'] for p in photos}
    if not {p['sha256'] for p in generation_request['photos']} <= available:
        raise ValueError('Cached generation does not bind this photograph bundle')
    segment_request = read_json(segmented / 'request.json')
    if segment_request['settings'].get('input') != a['task_id']:
        raise ValueError('Cached segmentation belongs to a different generation')
    records = {}
    for label, directory in [('generation', generated), ('segmentation', segmented)]:
        for name in ('request.json', 'submission.json', 'result.json', 'artifacts.json'):
            records[f'{label}/{name}'] = _copy(directory / name, folder / label / name)
    return dict(generation={**a, 'model': _copy(verified(a['model']), folder / 'generated.glb')},
                segmentation={**b, 'model': _copy(verified(b['model']), folder / 'segmented.glb')},
                original_records=records)


def render_evidence(model, folder, settings):
    path = verified(model)
    manifest = dict(cases=[dict(id='source', product='source', provider='retained segmentation', path=str(path),
                               rotation_degrees=[0, settings['yaw_degrees'], 0])],
                    views=settings['render_views'], modes=['raw'], backgrounds=['light'],
                    width=settings['render_width'], height=settings['render_height'])
    write_json(folder / 'manifest.json', manifest)
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js is required for actual known-camera render evidence')
    command = [node, str(ROOT.parent / 'ar/qa/provider-comparison.mjs'),
               '--manifest=' + str(folder / 'manifest.json'), '--output=' + str(folder / 'renders'), '--stage=raw']
    result = subprocess.run(command, cwd=ROOT.parent / 'ar', capture_output=True, text=True, timeout=600)
    (folder / 'renderer.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Evidence renderer failed; inspect retained renderer.log')
    report = read_json(folder / 'renders/report.json')
    case = report['cases'][0]
    if case.get('status') != 'rendered' or case['model_sha256'] != model['sha256']:
        raise ValueError('Renderer source binding/status mismatch')
    return dict(report=pin(folder / 'renders/report.json'), directory=str(folder / 'renders'), case=case)


def view_evidence(rendered, masks, settings):
    from .camera import Camera
    case = rendered['case']
    normalization = case['normalization']
    if normalization['rotation_order'] != 'XYZ' or normalization['rotation_degrees'] != [0, settings['yaw_degrees'], 0]:
        raise ValueError('Unexpected render normalization')
    angle = math.radians(settings['yaw_degrees'])
    rotation = np.array([[math.cos(angle), 0, math.sin(angle)], [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]])
    transform = np.eye(4)
    transform[:3, :3] = rotation * normalization['uniform_scale']
    transform[:3, 3] = np.asarray(normalization['translation']) * normalization['uniform_scale']
    if 'world_to_render' in normalization:
        observed = np.asarray(normalization['world_to_render']).reshape(4, 4, order='F')
        if normalization.get('matrix_layout') != 'column-major' or not np.allclose(observed, transform, atol=1e-10, rtol=1e-10):
            raise ValueError('Exact renderer transform disagrees with evidence camera adapter')
    width, height = settings['render_width'], settings['render_height']
    rows = []
    for view in settings['render_views']:
        render = next(r for r in case['renders'] if r['mode'] == 'raw' and r['background'] == 'light' and r['view'] == view)
        direction = np.asarray(DIRECTIONS[view], float); direction /= np.linalg.norm(direction)
        if 'camera' in render:
            recorded = render['camera']
            if (recorded.get('matrix_layout') != 'column-major' or recorded['width'] != width or recorded['height'] != height
                    or not np.allclose(recorded['direction'], direction, atol=1e-12)):
                raise ValueError('Recorded evidence camera disagrees with requested view')
            projection = np.asarray(recorded['projection_matrix']).reshape(4, 4, order='F')
            span = case['orthographic_vertical_span']
            if not np.allclose([projection[0, 0], projection[1, 1], projection[3, 3]], [2*height/(span*width), 2/span, 1], atol=1e-12):
                raise ValueError('Recorded evidence projection disagrees with camera adapter')
        camera = Camera(yaw=math.degrees(math.atan2(direction[0], direction[2])), pitch=math.degrees(math.asin(direction[1])),
                        roll=0, perspective=0, scale=height / case['orthographic_vertical_span'],
                        center_x=(width - 1) / 2, center_y=(height - 1) / 2)
        image = pin(Path(rendered['directory']) / render['filename'])
        if image['sha256'] != render['sha256']:
            raise ValueError('Render changed before masking')
        rows.append(dict(id=view, model_sha256=case['model_sha256'], width=width, height=height,
                         camera=asdict(camera), world_to_render=transform.tolist(), cull_mode='front',
                         image=image, masks=masks[view]['masks'], mask_status=masks[view]['mask_status']))
    return rows


def render_appearance(appearance, folder, product, settings):
    manifest = read_json(appearance['runtime_manifest'])
    for case in manifest['cases']:
        case['product'] = product
        case['width_mm'] = settings['display_width_mm']
        case['input_provenance'] = dict(method='segmented_ar_v1', material='source-pixel-derived conditional optical hypothesis',
                                        width_basis='display convention, not a physical product measurement')
    manifest.update(input_description='Automatically inferred segmented optical groups, measured per-part reduction, canonical placement and conditional image-grounded appearance. Synthetic poses and checker backdrop test optical behavior and runtime compatibility; no real wearer or independent product accuracy claim.', **AR_POLICY)
    path = folder / 'manifest.json'; write_json(path, manifest)
    command = [shutil.which('node') or 'node', str(ROOT.parent / 'ar/qa/provider-comparison.mjs'),
               '--manifest=' + str(path), '--output=' + str(folder / 'renders'), '--stage=ar']
    result = subprocess.run(command, cwd=ROOT.parent / 'ar', capture_output=True, text=True, timeout=900)
    (folder / 'renderer.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Actual AR appearance render failed; inspect retained log')
    report = read_json(folder / 'renders/report.json')
    if any(c['status'] != 'runtime_compatible' for c in report['cases']):
        return dict(status='runtime_rejected', report=pin(folder / 'renders/report.json'))
    from qa.provider_comparison import candidate_cards, contact_sheets
    contact_sheets(path, folder / 'renders')
    cards = candidate_cards(path, folder / 'renders')
    cards_path = folder / 'cards.json'; write_json(cards_path, cards)
    return dict(status='runtime_compatible', report=pin(folder / 'renders/report.json'), cards=pin(cards_path))


def deliver_candidate(selected, render_result, folder, settings):
    from .mesh import load_glb_bytes
    from .optical_group_asset import read_optical_group_candidate, _seal
    path = verified(dict(path=selected['path'], sha256=selected['sha256']))
    rendered = read_json(verified(render_result['report']))
    matches = [c for c in rendered['cases'] if c['id'] == selected['candidate_id'] and c['model_sha256'] == selected['sha256']]
    if len(matches) != 1 or matches[0]['status'] != 'runtime_compatible':
        raise ValueError('Selected bytes lack a matching successful actual AR render')
    receipt = read_json(verified(selected['export']))
    read_optical_group_candidate(path, receipt, expected_sha256=selected['sha256'])
    raw = path.read_bytes(); mesh = load_glb_bytes(raw)
    violations = []
    if len(mesh.faces) > settings['maximum_triangles']:
        violations.append('triangle_budget_exceeded')
    if len(raw) > settings['maximum_bytes']:
        violations.append('file_size_budget_exceeded')
    target = folder / 'candidate.glb'; _copy(path, target)
    receipt.pop('receipt_sha256', None); receipt['output'] = str(target); receipt = _seal(receipt)
    write_json(folder / 'candidate.export.json', receipt)
    read_optical_group_candidate(target, receipt, expected_sha256=selected['sha256'])
    return dict(status='within_delivery_budgets' if not violations else 'delivery_requires_review',
                candidate=pin(target), export=pin(folder / 'candidate.export.json'), triangles=len(mesh.faces),
                vertices=len(mesh.vertices), violations=violations, runtime=render_result['status'], accepted=False)


def _run_base_segmented_job(request_path, output, *, client=None, maximum_new_calls=0, semantic_client=None,
                      semantic_report=None, sampling_report=None, aperture_engine=None, refresh_appearance=False):
    from .job import _job_lock
    output = Path(output).resolve()
    request = read_json(request_path) if not isinstance(request_path, dict) else request_path
    settings = validate_request(request)
    budget = SubmissionBudget(maximum_new_calls)
    with _job_lock(output):
        immutable(output / 'request.json', request)
        journal = Journal(output, request, settings)
        photos = journal.stage('input', lambda folder: capture_photos(request['photos'], folder))['photos']
        if request['source']['kind'] == 'cached_tripo':
            sources = journal.stage('cached_source', lambda folder: capture_cached(request['source'], folder, photos))
            generation, segmentation = sources['generation'], sources['segmentation']
        else:
            # Provider-only snapshots already retained by an earlier run may have
            # equivalent owned paths. Keep its immutable request instead of
            # rewriting paths after intake recovery.
            generation_dir = output / 'providers/generation'
            intended = tripo_request(request['product_id'], 'generation', photos)
            if (generation_dir / 'request.json').exists():
                saved = read_json(generation_dir / 'request.json')
                if [(p['view'], p['sha256']) for p in saved['photos']] != [(p['view'], p['sha256']) for p in intended['photos']] or saved['settings'] != intended['settings']:
                    raise ValueError('Retained provider request differs from current inputs/settings')
                intended = saved
            generation = advance_tripo(generation_dir, intended, client=client, budget=budget)
            if generation['status'] != 'complete':
                return journal.report(_provider_wait_status(generation, 'provider_pending'), operation='generation', provider=generation, new_calls=budget.used)
            segmentation = advance_tripo(output / 'providers/segmentation',
                tripo_request(request['product_id'], 'segment', input_task=generation['task_id']), client=client, budget=budget)
            if segmentation['status'] != 'complete':
                return journal.report(_provider_wait_status(segmentation, 'provider_pending'), operation='segmentation', provider=segmentation, new_calls=budget.used)
        def source_correspondence(folder):
            from qa.part_probe_audit import bounded_correspondence
            report = bounded_correspondence(verified(generation['model']), verified(segmentation['model']), folder)
            if not report['complete_bijection'] or report['reversed_winding_faces']:
                raise ValueError('Segmentation did not preserve the generated surface within the bounded correspondence contract')
            return report
        correspondence = journal.stage('segmentation_correspondence', source_correspondence,
            recipe=dict(original_sha256=generation['model']['sha256'], segmented_sha256=segmentation['model']['sha256'],
                implementation_sha256=pin(ROOT / 'qa/part_probe_audit.py')['sha256'], relative_tolerance=1e-6))
        rendered = journal.stage('evidence_renders', lambda folder: render_evidence(segmentation['model'], folder, settings))
        masks, pending = {}, {}
        for view in settings['render_views']:
            render = next(r for r in rendered['case']['renders'] if r['mode'] == 'raw' and r['background'] == 'light' and r['view'] == view)
            image = dict(path=str(Path(rendered['directory']) / render['filename']), sha256=render['sha256'])
            result = advance_mask(output / 'providers/masks' / view, image, client=client, budget=budget)
            if result['status'] == 'complete':
                masks[view] = result
            else:
                pending[view] = result
        if pending:
            status = 'needs_review' if any(_provider_wait_status(p, '') == 'needs_review' for p in pending.values()) else 'evidence_pending'
            return journal.report(status, views=pending, new_calls=budget.used)
        from .part_role_inference import infer_part_roles
        evidence = view_evidence(rendered, masks, settings)
        def identify(folder):
            write_json(folder / 'views.json', evidence)
            return infer_part_roles(verified(segmentation['model']), evidence, folder / 'inference')
        roles = journal.stage('part_roles', identify, recipe=dict(model_sha256=segmentation['model']['sha256'],
            evidence_sha256=_sha_json(evidence), implementation_sha256=pin(ROOT / 'reconstruction/part_role_inference.py')['sha256']))
        groups = roles['primary_groups']
        if not groups:
            return journal.report('needs_review', reason='no_supported_optical_parts', roles=roles)
        indices = sorted({index for group in groups for index in group})
        from qa.part_lod_probe import run as reduce_parts
        from .compact_glb import run_compact_asset
        from qa.part_optics_probe import run as prepare_optics
        def reduce(folder):
            result = reduce_parts(verified(segmentation['model']), folder / 'lod', indices,
                                  settings['lens_triangles'], settings['frame_triangles'], settings['simplification_error'])
            compact = run_compact_asset(folder / 'lod/candidate.glb', folder / 'compact')
            return dict(lod=result, compact=compact, model=pin(folder / 'compact/compact.glb'))
        reduced = journal.stage('reduction', reduce, recipe=dict(source_sha256=segmentation['model']['sha256'],
            optical_parts=indices, lens_triangles=settings['lens_triangles'], frame_triangles=settings['frame_triangles'],
            simplification_error=settings['simplification_error']))
        role_alternatives = None
        if len(roles['hypotheses']) > 1:
            from .segmented_role_alternatives import process_role_alternatives
            def alternatives(folder):
                return process_role_alternatives(verified(reduced['model']), roles, folder / 'alternatives',
                    reduction_proof=reduced, **{k:settings[k] for k in ('lens_triangles', 'frame_triangles',
                        'simplification_error', 'yaw_degrees', 'display_width_mm', 'maximum_triangles', 'maximum_bytes')})
            role_alternatives = journal.stage('role_alternatives', alternatives,
                recipe=dict(reduced_sha256=reduced['model']['sha256'], roles_sha256=_sha_json(roles),
                            implementation_sha256=pin(ROOT / 'reconstruction/segmented_role_alternatives.py')['sha256']))
        def prepare(folder):
            result = prepare_optics(verified(reduced['model']), folder / 'prepared', groups,
                                    yaw_degrees=settings['yaw_degrees'], width_m=settings['display_width_mm'] / 1000)
            preparation_path = folder / 'prepared/optics/report.json'
            if result['preparation_status'] != 'prepared_optical_group_candidate':
                from .segmented_optics import recover_smooth_failed_preparation
                repaired = recover_smooth_failed_preparation(preparation_path, folder / 'normal-recovery')
                result = {**result, 'preparation_status': repaired['status'],
                          'original_preparation_reasons': result.get('reasons', []), 'reasons': [],
                          'candidate': str(folder / 'normal-recovery' / repaired['model']['path']),
                          'normal_recovery': repaired['normal_recovery']}
                preparation_path = folder / 'normal-recovery/report.json'
            return dict(**result, preparation_report=pin(preparation_path))
        prepared = journal.stage('optical_preparation', prepare, recipe=dict(source_sha256=reduced['model']['sha256'],
            groups=groups, yaw_degrees=settings['yaw_degrees'], display_width_mm=settings['display_width_mm']))
        semantic_report = semantic_report or request.get('semantic_report')
        sampling_report = sampling_report or request.get('sampling_report')
        old_appearance = journal.value['stages'].get('appearance', {})
        previous_recipe = old_appearance.get('recipe') or {}
        appearance_recipe = dict(preparation_sha256=prepared['preparation_report']['sha256'],
            photos=[p['sha256'] for p in photos], maximum_candidates=settings['maximum_appearance_candidates'],
            implementation_sha256=pin(ROOT / 'reconstruction/segmented_appearance.py')['sha256'],
            semantic_report_sha256=pin(semantic_report)['sha256'] if semantic_report else previous_recipe.get('semantic_report_sha256'),
            sampling_report_sha256=pin(sampling_report)['sha256'] if sampling_report else previous_recipe.get('sampling_report_sha256'),
            aperture_engine=aperture_engine.describe() if aperture_engine else previous_recipe.get('aperture_engine'))
        # Resume is a replay of the completed, versioned experiment. A software
        # upgrade alone must not trigger new semantic calls. Explicit refresh
        # adopts changed local material code; changed evidence still invalidates.
        if (old_appearance.get('status') == 'complete' and not refresh_appearance
                and {k:v for k,v in previous_recipe.items() if k != 'implementation_sha256'} ==
                    {k:v for k,v in appearance_recipe.items() if k != 'implementation_sha256'}):
            appearance_recipe['implementation_sha256'] = previous_recipe['implementation_sha256']
        saved_appearance = old_appearance.get('status') == 'complete' and previous_recipe == appearance_recipe
        if not saved_appearance and (aperture_engine is None or (semantic_client is None and not (semantic_report and sampling_report))):
            return journal.report('appearance_pending', preparation=prepared, roles=roles,
                reason='explicit_aperture_engine_and_semantic_client_or_both_saved_reports_required', new_calls=budget.used)
        from .segmented_appearance import run_segmented_appearance
        def appearance(folder):
            options = dict(semantic_report=pin(semantic_report) if semantic_report else None,
                           sampling_report=pin(sampling_report) if sampling_report else None,
                           client=semantic_client.describe() if semantic_client else None)
            write_json(folder / 'options.json', options)
            return run_segmented_appearance(verified(prepared['preparation_report']), photos, folder / 'appearance',
                semantic_report=semantic_report, client=semantic_client,
                maximum_candidates=settings['maximum_appearance_candidates'], sampling_report=sampling_report,
                aperture_engine=aperture_engine)
        appearance_result = journal.stage('appearance', appearance, recipe=appearance_recipe)
        if len(appearance_result['candidates']) < 2:
            return journal.report('needs_review', reason='insufficient_appearance_evidence', appearance=appearance_result, roles=roles, new_calls=budget.used)
        renders = journal.stage('appearance_renders', lambda folder: render_appearance(appearance_result, folder, request['product_id'], settings),
            recipe=dict(candidates=[c['sha256'] for c in appearance_result['candidates']], policy=AR_POLICY,
                implementation={name:pin(ROOT.parent / 'ar/qa' / name)['sha256'] for name in
                    ('provider-comparison.mjs', 'provider-comparison-ar.html', 'provider-comparison-lighting.mjs')},
                cards_implementation=pin(ROOT / 'qa/provider_comparison.py')['sha256']))
        if renders['status'] != 'runtime_compatible':
            return journal.report('needs_review', reason='runtime_rejected', rendering=renders, new_calls=budget.used)
        review_recipe = dict(cards_sha256=renders['cards']['sha256'],
            appearance_sha256=pin(Path(appearance_result['runtime_manifest']).parent / 'report.json')['sha256'],
            implementation_sha256=pin(ROOT / 'reconstruction/segmented_appearance_review.py')['sha256'])
        saved_review = journal.value['stages'].get('appearance_review', {})
        previous_review_recipe = saved_review.get('recipe') or {}
        if (saved_review.get('status') == 'complete' and not refresh_appearance
                and {k:v for k,v in previous_review_recipe.items() if k != 'implementation_sha256'} ==
                    {k:v for k,v in review_recipe.items() if k != 'implementation_sha256'}):
            review_recipe['implementation_sha256'] = previous_review_recipe['implementation_sha256']
        if semantic_client is None and (saved_review.get('status') != 'complete' or saved_review.get('recipe') != review_recipe):
            return journal.report('review_pending', reason='explicit_semantic_review_client_required', appearance=appearance_result, rendering=renders, new_calls=budget.used)
        from .segmented_appearance_review import run_segmented_appearance_review
        def review(folder):
            return run_segmented_appearance_review(Path(appearance_result['runtime_manifest']).parent / 'report.json',
                verified(renders['cards']), folder / 'review', client=semantic_client, environment='broad')
        selection = journal.stage('appearance_review', review, recipe=review_recipe)
        delivery = journal.stage('delivery', lambda folder: deliver_candidate(selection['selected_candidate'], renders, folder, settings),
            recipe=dict(selected_sha256=selection['selected_candidate']['sha256'], render_report_sha256=renders['report']['sha256'],
                        maximum_triangles=settings['maximum_triangles'], maximum_bytes=settings['maximum_bytes']))
        # Published local pointer is replaceable; every original revision remains
        # immutable under its completed delivery attempt in the journal.
        target = output / 'candidate.glb'
        source = verified(delivery['candidate'])
        if target.exists() and pin(target)['sha256'] != delivery['candidate']['sha256']:
            previous_deliveries = journal.value.get('stage_history', {}).get('delivery', [])
            known_hashes = {s.get('result', {}).get('candidate', {}).get('sha256') for s in previous_deliveries}
            if pin(target)['sha256'] not in known_hashes:
                raise ValueError('Local candidate changed outside this job; preserve it before publishing a new revision')
            staging = output / 'candidate.glb.pending'
            shutil.copyfile(source, staging)
            os.replace(staging, target)
        else:
            _copy(source, target)
        candidate = pin(target)
        status = 'needs_review' if selection.get('all_candidates_rejected') else 'candidate_available'
        return journal.report(status, candidate=candidate, delivery=delivery, selection=selection,
            role_inference=roles, role_alternatives=role_alternatives, segmentation_correspondence=correspondence,
            appearance=appearance_result, rendering=renders, new_calls=budget.used,
            production_ready=False, role_selection_required=bool(role_alternatives and role_alternatives['role_selection_required']),
            requires_review=True, review_reasons=roles['review_reasons'] + delivery['violations'] +
                (role_alternatives['review_reasons'] if role_alternatives else []) +
                (['all_material_candidates_rejected'] if selection.get('all_candidates_rejected') else []) +
                (['material_review_unresolved_prior_retained'] if selection['fallback_used'] else []),
            limitations=['Conditional material preference uses the source photos; independent product accuracy is unmeasured.',
                         'Actual renderer handover uses synthetic poses; real-face and phone performance are unmeasured.'])


def run_segmented_job(request_path, output, *, astra_options=None, **base_options):
    """Complete the base stages, then optionally run the independent durable editor."""
    result = _run_base_segmented_job(request_path, output, **base_options)
    if astra_options is None or 'candidate' not in result:
        return result
    from .segmented_astra_job import run_astra_job
    from .job import _write
    options = dict(astra_options)
    destination = Path(options.pop('output', Path(output) / 'astra')).resolve()
    edited = run_astra_job(Path(output).resolve(), destination, **options)
    combined = {**result, 'base_candidate': result['candidate'], 'base_delivery': result['delivery'],
        'base_selection': result['selection'], 'base_rendering': result['rendering'],
        'candidate': edited['candidate'], 'delivery': edited, 'astra': edited,
        'selection': {'stage': 'astra_editing', 'current_revision': edited['current_revision'],
            'model_reviewed_current': edited['model_reviewed_current'], 'accepted': False},
        'rendering': edited['observation'], 'current_candidate_stage': 'astra_editing',
        'status': edited['status'], 'production_ready': False, 'requires_review': True}
    _write(Path(output).resolve() / 'report.json', combined)
    return combined


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--env', type=Path, help='Explicit dotenv path for Tripo and fal credentials')
    parser.add_argument('--maximum-new-calls', type=int, default=0)
    parser.add_argument('--wait-seconds', type=float, default=0)
    parser.add_argument('--semantic-api-key-env', help='Explicit environment variable for photo interpretation and review')
    parser.add_argument('--semantic-model', default='gemini-3.8-flash')
    parser.add_argument('--semantic-cache', type=Path)
    parser.add_argument('--semantic-report', type=Path, help='Reuse exact-pixel-bound semantic interpretation')
    parser.add_argument('--sampling-report', type=Path, help='Reuse exact-pixel-bound appearance sampling proposals')
    parser.add_argument('--aperture-weights', type=Path, help='Explicit local detector weights for independent photo-region grounding')
    parser.add_argument('--refresh-appearance', action='store_true', help='Adopt changed material/review code in new local stage attempts; preserve prior artifacts')
    parser.add_argument('--semantic-maximum-calls', type=int, default=4)
    from .segmented_astra_job import add_astra_arguments, client_from_args
    add_astra_arguments(parser)
    args = parser.parse_args(argv)
    if not 0 <= args.maximum_new_calls <= 100 or not 0 <= args.wait_seconds <= 3600:
        parser.error('Unsupported call or wait budget')
    client = Client(args.env) if args.env else None
    astra_options = None
    if args.with_astra:
        astra_options = dict(client=client_from_args(args), maximum_turns=args.astra_max_turns,
            output=args.astra_output or args.output / 'astra')
    elif args.authorize_paid_astra or args.astra_script:
        parser.error('Use --with-astra to enable the Astra stage')
    semantic_client = None
    aperture_engine = None
    if args.aperture_weights:
        from .photo_apertures import OfflineLensApertureEngine
        aperture_engine = OfflineLensApertureEngine(args.aperture_weights)
    if args.semantic_api_key_env or args.semantic_cache:
        if not args.semantic_api_key_env or not args.semantic_cache or not os.environ.get(args.semantic_api_key_env):
            parser.error('Semantic execution requires an explicit available credential variable and durable cache')
        from .semantic_transport import GeminiSemanticClient
        semantic_client = GeminiSemanticClient(api_key=os.environ[args.semantic_api_key_env], model=args.semantic_model,
            cache_dir=args.semantic_cache, maximum_calls=args.semantic_maximum_calls, maximum_output_tokens=8192)
    deadline = time.monotonic() + args.wait_seconds
    remaining = args.maximum_new_calls
    while True:
        result = run_segmented_job(args.request, args.output, client=client, maximum_new_calls=remaining, semantic_client=semantic_client,
            semantic_report=args.semantic_report, sampling_report=args.sampling_report, aperture_engine=aperture_engine,
            refresh_appearance=args.refresh_appearance, astra_options=astra_options)
        remaining -= result.get('new_calls', 0)
        print(json.dumps(dict(status=result['status'], product=result['product_id'], new_calls=result.get('new_calls', 0))), flush=True)
        if result['status'] not in ('provider_pending', 'evidence_pending') or time.monotonic() >= deadline:
            break
        time.sleep(min(10, max(0, deadline - time.monotonic())))


if __name__ == '__main__':
    main()
