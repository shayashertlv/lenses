"""Reconstruct and compare appearance on an existing prepared glasses model.

This stage consumes the same source-bound geometry/observations as the joint
fitter, exports numerical material alternatives, renders actual AR cards and
records a reversible model-assisted choice. It does not regenerate geometry.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .ar_material_search import propose_material_candidates, compare_rendered_candidates, select_ar_material
from .appearance_selection import select_appearance
from .photo_lens_stage import load_optical_fit_inputs


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def collect_semantic_photo_inputs(fit_photos, initializer_report):
    """Include provider-only side/back views for interpretation, with pinned bytes."""
    result = [dict(p) for p in fit_photos]
    identifiers = {p['id'] for p in result}
    for row in [*initializer_report.get('selected', []), *initializer_report.get('unused', [])]:
        if row.get('source_availability') == 'missing_legacy_unsnapshotted_input':
            continue  # Explicitly retained as missing in the initializer report.
        if row['id'] not in identifiers:
            result.append({'id': row['id'], 'path': row['path'], 'sha256': row['sha256'], 'view': row.get('view','unknown')})
            identifiers.add(row['id'])
    if len(result) > 12:
        raise ValueError('Semantic interpretation supports at most twelve distinct photos')
    return result


def load_job_semantic_photos(job):
    """Replay pinned intake/initializer records, never raw request-relative paths."""
    from .job import _completed, _inside
    job = Path(job).resolve()
    journal = json.loads((job/'job.json').read_bytes())
    completed = _completed(job,journal,'input')
    if completed is None:
        raise ValueError('Semantic replay requires a completed pinned photo intake')
    folder = completed[1]
    bundle = json.loads((folder/'manifest.json').read_bytes())
    photos = [{'id':p['id'],'view':p['view'],'path':str(_inside(folder,p['normalized']['path'])),
               'sha256':p['normalized']['sha256']} for p in bundle['photos']]
    initial = _completed(job,journal,'initializer')
    return collect_semantic_photo_inputs(photos,json.loads((initial[1]/'initializer-report.json').read_bytes()) if initial else {})


def _evidence_view_label(label):
    """Only an explicitly axial back view supplies the rear-transmission branch."""
    label = str(label or 'unknown').lower().replace('-','_').replace(' ','_')
    if label in ('front','back','left','right','angled','unknown'):
        return label
    if label in ('front_left','front_right','back_left','back_right','three_quarter'):
        return 'angled'
    return 'unknown'


def _with_normal_control(candidates, control, maximum):
    retained = list(candidates)
    if len(retained) >= maximum:
        mirrored = lambda row: any('mirror' in f for f in row['family_assignment'].values())
        contrary = next((c for c in retained[1:] if mirrored(c) != mirrored(retained[0])),None)
        preserved = [retained[0]]+([contrary] if contrary else [])
        retained = (preserved+[c for c in retained[1:] if c not in preserved])[:maximum-1]
    return retained+[control]


def _snapshot_semantic_photos(photos, output):
    from .input_bundle import _normalize
    folder = Path(output) / 'photos'
    folder.mkdir(parents=True, exist_ok=True)
    normalized = []
    receipts = []
    pixels_seen = set()
    for number, photo in enumerate(photos):
        source = Path(photo['path']).resolve()
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if photo.get('sha256', digest) != digest:
            raise ValueError('Semantic source photo differs from its pinned input')
        record, png = _normalize(raw, f'image-{number}', 'unknown', source)
        if record['normalized']['pixel_sha256'] in pixels_seen:
            receipts.append({'photo_id':photo['id'],'original_sha256':digest,'status':'duplicate_normalized_pixels_omitted'})
            continue
        pixels_seen.add(record['normalized']['pixel_sha256'])
        path = folder / f'image-{number}.png'
        path.write_bytes(png)
        normalized.append({'id': photo['id'], 'path': str(path), 'sha256': _sha(path)})
        receipts.append({'photo_id': photo['id'], 'original_sha256': digest, 'normalization': record})
    _write(folder / 'normalization.json', receipts)
    return normalized


def prepare_semantic_appearance_inputs(preparation_report, region_report, output, *,
                                      semantic_report=None, client=None, photos=None,
                                      declared_facts=None, maximum_samples=512,
                                      group_photo_exclusions=None, aperture_engine=None, view_labels=None):
    """Produce source-bound hypotheses, sampled anchors and optional fit priors."""
    from .photo_semantics import (build_image_manifest, manifest_from_probe, validate_product_hypotheses,
                                 rebind_product_hypotheses, infer_product_hypotheses)
    from .appearance_anchors import sample_appearance_anchors, compile_material_priors
    output = Path(output).resolve()
    regions = json.loads(Path(region_report).read_bytes())
    fit_photos = [{'id': p['id'], 'path': p['source'], 'sha256': p['source_sha256']} for p in regions['photos']]
    if semantic_report is not None:
        raw = json.loads(Path(semantic_report).read_bytes())
        manifest = raw.get('image_manifest') or manifest_from_probe(raw)
        semantic = validate_product_hypotheses(raw, manifest)
        semantic = rebind_product_hypotheses(semantic, build_image_manifest(fit_photos))
    else:
        if client is None:
            raise ValueError('Supply a bound semantic report or an explicit semantic client')
        semantic = infer_product_hypotheses(_snapshot_semantic_photos(photos or fit_photos, output), client=client)
        semantic = rebind_product_hypotheses(semantic, build_image_manifest(fit_photos))
    inputs = load_optical_fit_inputs(Path(preparation_report), Path(region_report),
                                    maximum_samples_per_hypothesis=maximum_samples,
                                    group_photo_exclusions=group_photo_exclusions)
    if inputs.get('profile') != 'effective_optical_group_v1_experiment':
        raise ValueError('Semantic AR search currently requires prepared effective optical groups')
    groups = list(inputs['observations']['groups'].values())
    grounding = image_evidence = None
    if aperture_engine is not None:
        from .image_appearance_evidence import ground_semantic_regions, sample_image_appearance_evidence
        grounding = ground_semantic_regions(semantic, aperture_engine, output/'grounding')
        labels = dict(view_labels or {})
        photo_by_hash = {p.get('sha256') or _sha(p['path']): p for p in (photos or [])}
        photo_by_id = {p['id']: p for p in (photos or [])}
        for p in semantic['image_manifest']:
            original = photo_by_hash.get(p['sha256']) or photo_by_id.get(p['photo_id'])
            if original and original.get('view'):
                labels[p['photo_id']] = original['view']
        labels = {p['photo_id']: labels[p['photo_id']] for p in semantic['image_manifest'] if p['photo_id'] in labels}
        image_evidence = sample_image_appearance_evidence(semantic, grounding,
            view_labels={pid:_evidence_view_label(label) for pid,label in labels.items()})
        image_evidence['declared_view_labels'] = labels
        _write(output/'image-evidence.json',image_evidence)
    anchors = sample_appearance_anchors(groups, semantic, grounding=grounding, image_evidence=image_evidence)
    priors = compile_material_priors(semantic, anchors, declared_facts=declared_facts or {})
    if grounding is not None:
        from .grounded_fit_support import freeze_grounded_fit_support
        priors['frozen_image_fit_support']=freeze_grounded_fit_support(semantic,grounding)
        from .rear_correspondence import collect_rear_transmission_anchors
        contrast=collect_rear_transmission_anchors(semantic,grounding,groups)
        _write(output/'rear-correspondences.json',contrast)
        for gid,rows in contrast['anchors_by_group'].items():
            priors['groups'][gid]['transmission_anchors']=rows[:16]
    from .material_relations import infer_material_relations
    priors['material_relations'] = infer_material_relations(semantic, priors, [g['surface_binding'] for g in groups])
    for name, value in [('semantics', semantic), ('anchors', anchors), ('priors', priors)]:
        _write(output / (name + '.json'), value)
    return {'inputs': inputs, 'semantics': semantic, 'anchors': anchors, 'priors': priors,
            'priors_path': output / 'priors.json'}


def render_material_cards(manifest_path, output, *, node='node'):
    """Run the local AR harness with no camera, face data or live-site changes."""
    ar = Path(__file__).resolve().parents[2] / 'ar'
    command = [node, str(ar / 'qa/semantic-material-cards.mjs'),
               '--manifest=' + str(Path(manifest_path).resolve()), '--output=' + str(Path(output).resolve())]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'harness.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=ar, stdout=log, stderr=subprocess.STDOUT, timeout=900,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('AR material-card harness failed; see ' + str(output / 'harness.log'))
    report = json.loads((output / 'report.json').read_bytes())
    if report.get('status') != 'passed' or report.get('manifest_sha256') != _sha(manifest_path):
        raise ValueError('AR card report does not verify the supplied material manifest')
    return report


def _existing_candidates(joint_report, *, prepared_glb_sha256=None):
    folder = Path(joint_report).resolve().parent
    stage = json.loads(Path(joint_report).read_bytes())
    fit_path = folder / stage['fit']['path']
    if _sha(fit_path) != stage['fit']['sha256']:
        raise ValueError('Existing fitted descriptors changed')
    fit = json.loads(fit_path.read_bytes())
    baseline = select_appearance(fit, stage)
    selected = baseline.get('selected')
    if selected is None:
        raise ValueError('Joint stage has no baseline appearance')
    fitted = {r['candidate_id']: r for r in fit['candidates']}
    rows = []
    for row in [selected, *baseline['alternatives']]:
        if any(r['source_candidate_id'] == row['candidate_id'] for r in rows):
            continue
        candidate = fitted[row['candidate_id']]
        if prepared_glb_sha256 is not None and any(g['surface_binding']['prepared_glb_sha256'] != prepared_glb_sha256
                                                   for g in candidate['groups'].values()):
            raise ValueError('Existing material fit belongs to different prepared geometry')
        rows.append({'candidate_id': row['candidate_id'], 'source_candidate_id': row['candidate_id'],
                     'family_assignment': row['family_assignment'], 'score_codes': row['score_codes'],
                     'material_relation': candidate.get('assumptions', {}).get('material_relation', {'mode':'independent'}),
                     'rear_response_hypothesis':candidate.get('assumptions',{}).get('rear_response_hypothesis'),
                     'prepared_glb_sha256': next(iter(candidate['groups'].values()))['surface_binding']['prepared_glb_sha256'],
                     'appearances': {gid: g['appearance'] for gid, g in candidate['groups'].items()}})
    return rows, baseline


def run_semantic_appearance_stage(preparation_report, region_report, joint_report, output, *,
                                  semantic_report=None, client=None, photos=None, declared_facts=None,
                                  maximum_samples=512, maximum_candidates=8, width_mm=145.,
                                  group_photo_exclusions=None, renderer=render_material_cards,
                                  prepared_evidence=None, aperture_engine=None, view_labels=None):
    """Full cached-geometry appearance path; comparison requires an explicit client.

The output directory is fresh. API receipts are separately cached by the
explicit client so interrupted or repeated requests need not spend again.
"""
    from .optical_group_asset import write_optical_group_candidate, read_optical_group_candidate
    from .group_photo_lens_inputs import verify_group_preview_geometry
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh semantic appearance output directory')
    output.mkdir(parents=True, exist_ok=True)
    if client is None:
        raise ValueError('An explicit comparison client is required')
    pins = {str(Path(p).resolve()): _sha(p) for p in (preparation_report, region_report, joint_report)}
    if semantic_report:
        pins[str(Path(semantic_report).resolve())] = _sha(semantic_report)
    _write(output / 'request.json', {'schema_version': 1, 'method': 'semantic_ar_v1', 'input_sha256': pins,
           'maximum_samples': maximum_samples, 'maximum_candidates': maximum_candidates,
           'declared_facts': declared_facts or {}, 'width_mm': width_mm})
    evidence = prepared_evidence or prepare_semantic_appearance_inputs(
        preparation_report, region_report, output / 'evidence', semantic_report=semantic_report,
        client=client, photos=photos, declared_facts=declared_facts, maximum_samples=maximum_samples,
        group_photo_exclusions=group_photo_exclusions, aperture_engine=aperture_engine, view_labels=view_labels)
    if prepared_evidence:
        for key in ('semantics', 'anchors', 'priors'):
            _write(output / 'evidence' / (key + '.json'), evidence[key])
    inputs = evidence['inputs']
    existing, baseline = _existing_candidates(joint_report, prepared_glb_sha256=_sha(inputs['model']))
    source_normal_inputs = None
    smoothing = inputs['preparation'].get('smooth_optical_geometry', {})
    if smoothing.get('changed_groups', 0):
        baseline_preparation = Path(smoothing['source_preparation'])
        if _sha(baseline_preparation) != smoothing['source_preparation_sha256']:
            raise ValueError('Original-normal preparation changed')
        source_normal_inputs = load_optical_fit_inputs(baseline_preparation, Path(region_report),
            maximum_samples_per_hypothesis=maximum_samples, group_photo_exclusions=group_photo_exclusions)
        # Preserve an actual rendered control for the surface approximation.
        # These parameters are transferred for comparison, not claimed refitted.
    candidates = propose_material_candidates(evidence['anchors'], evidence['priors'],
                    existing_candidates=existing, policy={'maximum_candidates': max(3,maximum_candidates-(1 if source_normal_inputs else 0))},
                    fit_groups=list(inputs['observations']['groups'].values()))
    if source_normal_inputs:
        control = deepcopy(candidates[0])
        control.update(candidate_id='material-'+hashlib.sha256((control['candidate_id']+_sha(source_normal_inputs['model'])).encode()).hexdigest()[:20],
                       origin='original_normals_control', geometry_variant='original_normals',
                       prepared_glb_sha256=_sha(source_normal_inputs['model']),
                       assumption='Material parameters transferred from the smooth-normal fit; original normals are a rendered contrary hypothesis.')
        # With the minimum three slots, preserve the fitted baseline and the
        # strongest contrary material before the geometry control. The original
        # proposal set is source-bound and can be expanded by a larger budget.
        candidates = _with_normal_control(candidates,control,maximum_candidates)
    cases = []
    for candidate in candidates:
        geometry_inputs = source_normal_inputs if candidate.get('geometry_variant') == 'original_normals' else inputs
        path = output / 'candidates' / (candidate['candidate_id'] + '.glb')
        path.parent.mkdir(exist_ok=True)
        receipt = write_optical_group_candidate(geometry_inputs['source'], path,
            [{'prepared': group, 'appearance': candidate['appearances'][gid]}
             for gid, group in sorted(geometry_inputs['prepared_groups'].items())],
            source_sha256=geometry_inputs['preparation']['source_sha256'],
            provenance={'method': 'semantic_material_candidate_v1', 'origin': candidate['origin'],
                        'priors_sha256': hashlib.sha256(json.dumps(evidence['priors'], sort_keys=True).encode()).hexdigest()})
        verify_group_preview_geometry(geometry_inputs['export_receipt'], receipt)
        read_optical_group_candidate(path, receipt, expected_sha256=receipt['output_sha256'])
        receipt_path = path.with_suffix('.export.json')
        _write(receipt_path, receipt)
        candidate.update(path=str(path), sha256=receipt['output_sha256'],
                         export={'path': str(receipt_path), 'sha256': _sha(receipt_path)})
        cases.append({'id': candidate['candidate_id'], 'path': str(path), 'model_sha256': candidate['sha256'],
                      'groups': receipt['groups'], 'width_mm': width_mm})
    _write(output / 'candidates.json', candidates)
    _write(output / 'runtime-manifest.json', {'schema_version': 1, 'cases': cases})
    rendered = renderer(output / 'runtime-manifest.json', output / 'renders')
    cards = []
    for row in rendered['cases']:
        path = output / 'renders' / row['card']['path']
        cards.append({'candidate_id': row['id'], 'path': str(path), 'sha256': row['card']['sha256'],
                      'model_sha256': row['model_sha256']})
    source_photos = [{'id': p['photo_id'], 'path': p['local_path'], 'sha256': p['sha256']}
                     for p in evidence['semantics']['image_manifest']]
    comparison = compare_rendered_candidates(source_photos, cards, client=client)
    _write(output / 'comparisons.json', comparison)
    selection = select_ar_material(candidates, evidence['anchors'], comparison,
                                   policy={'declared_facts': declared_facts or {}})
    _write(output / 'selection.json', selection)
    chosen = selection['selected']
    shutil.copyfile(chosen['path'], output / 'candidate-appearance.glb')
    if _sha(output / 'candidate-appearance.glb') != chosen['sha256']:
        raise ValueError('Selected appearance copy changed')
    if any(_sha(p) != digest for p, digest in {**pins, **inputs['pins'], **(source_normal_inputs['pins'] if source_normal_inputs else {})}.items()):
        raise ValueError('Source evidence changed during material search')
    report = {'schema_version': 1, 'method': 'semantic_ar_v1', 'status': selection['status'],
              'accepted': False, 'quality_verdict': 'unmeasured', 'input_sha256': pins,
              'baseline': baseline['selected'], 'selection': selection,
              'candidate': {'path': 'candidate-appearance.glb', 'sha256': chosen['sha256']},
              'runtime_report': 'renders/report.json', 'comparisons': 'comparisons.json'}
    _write(output / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True, type=Path, help='Completed inferred-group job to replay')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--semantic-report', type=Path, help='Bound production report or saved probe response')
    parser.add_argument('--api-key-env', required=True)
    parser.add_argument('--model', default='gemini-3.8-flash')
    parser.add_argument('--maximum-api-calls', type=int, default=3)
    parser.add_argument('--maximum-candidates', type=int, default=8)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--aperture-weights', type=Path, help='Optional explicit detector weights for grounded color evidence')
    args = parser.parse_args(argv)
    from .semantic_transport import GeminiSemanticClient
    client = GeminiSemanticClient(api_key=os.environ[args.api_key_env], model=args.model,
                                         maximum_calls=args.maximum_api_calls, cache_dir=args.cache)
    job = args.job.resolve()
    report = json.loads((job / 'report.json').read_bytes())
    optical = report['optical_candidates']
    joint = job / optical['fit_report']
    request = json.loads((joint.parent / 'request.json').read_bytes())
    original_request = json.loads((job / 'request.json').read_bytes())
    aperture_engine = None
    if args.aperture_weights:
        from .photo_apertures import OfflineLensApertureEngine
        aperture_engine = OfflineLensApertureEngine(args.aperture_weights)
    source_photos = load_job_semantic_photos(job)
    result = run_semantic_appearance_stage(Path(request['preparation_report']), Path(request['region_report']), joint,
        args.output, semantic_report=args.semantic_report, client=client,
        aperture_engine=aperture_engine, photos=source_photos,
        declared_facts=original_request.get('lens_facts'), maximum_samples=request['maximum_samples'],
        maximum_candidates=args.maximum_candidates, group_photo_exclusions=request.get('group_photo_exclusions'),
        width_mm=(report.get('ar_load_parameters') or {}).get('width_mm', 145.))
    print(json.dumps({'status': result['status'], 'candidate': result['candidate'],
                      'selection_basis': result['selection']['selection_basis']}))


if __name__ == '__main__':
    main()
