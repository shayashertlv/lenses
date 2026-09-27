"""Job-owned evaluation reservation, stage image ledgers and final measurement."""
import hashlib
import json
from pathlib import Path


STAGES = {'initializer': 'provider', 'geometry': 'geometry', 'material': 'material',
          'semantics': 'semantic', 'selection': 'candidate_selection', 'frame': 'frame'}


def pin(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def read_pin(reference):
    raw = Path(reference['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('Evaluation context artifact changed')
    return json.loads(raw)


def reserve_job_evaluation(request, base_dir, output, journal):
    from reconstruction.job import _attempt, _completed, _finish
    from reconstruction.evaluation_reservation import prepare_evaluation_reservation, verify_reservation
    if not {'evaluation_photos', 'reserved_photo_ids'} & set(request):
        return request, None
    completed = _completed(output, journal, 'evaluation_reservation')
    if completed:
        folder = completed[1]
        receipt = verify_reservation(folder / 'reservation.json')
        clean = json.loads((folder / 'reconstruction-request.json').read_bytes())
        from reconstruction.input_bundle import _canonical
        if receipt['request_sha256'] != hashlib.sha256(_canonical(request)).hexdigest():
            raise ValueError('Evaluation reservation belongs to a different request')
        if receipt['reconstruction_request_sha256'] != hashlib.sha256(_canonical(clean)).hexdigest():
            raise ValueError('Reserved reconstruction request changed')
    else:
        stage, folder = _attempt(output, journal, 'evaluation_reservation')
        result = prepare_evaluation_reservation(request, base_dir, folder)
        clean = result['reconstruction_request']
        _finish(output, journal, stage, folder)
    return clean, {'reservation': pin(folder / 'reservation.json'), 'usage_receipts': [],
                   'expected_stages': dict(STAGES)}


def capture_job_usage(output, journal, context, stage_id, photos, *, external_history=None):
    if context is None:
        return
    from reconstruction.job import _attempt, _completed, _finish
    from reconstruction.evaluation_reservation import record_photo_usage, verify_reservation
    phase = context['expected_stages'][stage_id]
    name = 'photo_usage_' + stage_id
    completed = _completed(output, journal, name)
    if completed:
        folder = completed[1]
        saved = json.loads((folder / 'usage.json').read_bytes())
        actual = [(p.get('id'), hashlib.sha256(Path(p['path']).read_bytes()).hexdigest()) for p in photos]
        captured = [(p['argument_photo_id'], p['sha256']) for p in saved['photos']]
        if actual != captured or saved.get('external_history_spec') != external_history:
            raise ValueError('Resumed stage photo arguments differ from their captured usage')
        verify_reservation(read_pin(context['reservation']))
    else:
        stage, folder = _attempt(output, journal, name)
        record_photo_usage(read_pin(context['reservation']), stage_id, phase, photos, folder,
                           external_history=external_history)
        _finish(output, journal, stage, folder)
    reference = pin(folder / 'usage.json')
    if reference not in context['usage_receipts']:
        context['usage_receipts'].append(reference)


def semantic_cache_photos(path):
    """Read original interpretation inputs, before any rebinding drops views."""
    from reconstruction.photo_semantics import build_image_manifest
    raw = json.loads(Path(path).read_bytes())
    rows = raw.get('image_manifest') or raw['source_images']
    manifest = build_image_manifest([{'id': row.get('photo_id', row.get('label')),
                                     'path': row['local_path'], 'sha256': row['sha256']} for row in rows])
    return [{'id': row['photo_id'], 'path': row['local_path'], 'sha256': row['sha256']}
            for row in manifest]


def capture_cached_semantics(output, journal, context, report_path):
    """Keep every cached interpretation input, including views absent from fitting.

    The usage stage owns exact source bytes before any backend runs. Rebinding
    the interpretation to those copies also lets an interrupted job resume when
    the external image files disappear before appearance evidence is prepared.
    """
    from reconstruction.job import _attempt, _completed, _finish, _write
    from reconstruction.photo_semantics import (build_image_manifest, validate_product_hypotheses,
                                                 rebind_product_hypotheses)
    raw = json.loads(Path(report_path).read_bytes())
    source_rows = raw.get('image_manifest') or raw['source_images']
    expected = [(row.get('photo_id', row.get('label')), row['sha256']) for row in source_rows]
    context['expected_stages']['cached-semantics'] = 'semantic'
    completed = _completed(output, journal, 'photo_usage_cached-semantics')
    if completed:
        saved = json.loads((completed[1] / 'usage.json').read_bytes())
        if [(row['argument_photo_id'], row['sha256']) for row in saved['photos']] != expected:
            raise ValueError('Cached semantic inputs differ from their captured usage')
        photos = [{'id': row['argument_photo_id'], 'path': row['snapshot_path'], 'sha256': row['sha256']}
                  for row in saved['photos']]
        for row in source_rows:
            original = Path(row['local_path'])
            if original.is_file() and pin(original)['sha256'] != row['sha256']:
                raise ValueError('Cached semantic source photo changed')
    else:
        photos = semantic_cache_photos(report_path)
    # This must precede interpreting the response: even a malformed cached
    # response cannot bypass the reserved-photo leakage check.
    capture_job_usage(output, journal, context, 'cached-semantics', photos)
    usage_folder = _completed(output, journal, 'photo_usage_cached-semantics')[1]
    saved = json.loads((usage_folder / 'usage.json').read_bytes())
    owned_manifest = build_image_manifest([
        {'id': row['argument_photo_id'], 'path': row['snapshot_path'], 'sha256': row['sha256']}
        for row in saved['photos']])
    if raw.get('image_manifest') is not None:
        semantic = validate_product_hypotheses(raw, raw['image_manifest'])
        semantic = rebind_product_hypotheses(semantic, owned_manifest)
    else:
        semantic = validate_product_hypotheses(raw, owned_manifest)
    completed = _completed(output, journal, 'cached_semantic_inputs')
    if completed:
        folder = completed[1]
        if (json.loads((folder / 'origin.json').read_bytes()) != pin(report_path)
                or json.loads((folder / 'semantics.json').read_bytes()) != semantic):
            raise ValueError('Cached semantic snapshot differs from its bound interpretation')
    else:
        stage, folder = _attempt(output, journal, 'cached_semantic_inputs')
        folder.mkdir(parents=True)
        _write(folder / 'origin.json', pin(report_path))
        _write(folder / 'semantics.json', semantic)
        _finish(output, journal, stage, folder)
    return folder / 'semantics.json'


def capture_delivery_usage(context, region_report, output):
    if context is None:
        return None
    from copy import deepcopy
    from reconstruction.evaluation_reservation import record_photo_usage
    context = deepcopy(context)
    regions = json.loads(Path(region_report).read_bytes())
    photos = [{'id': row['id'], 'path': row['source'], 'sha256': row['source_sha256']}
              for row in regions['photos']]
    record_photo_usage(read_pin(context['reservation']), 'frame', 'frame', photos, output)
    context['usage_receipts'].append(pin(Path(output) / 'usage.json'))
    return context


def replay_evaluation_context(context, model):
    from reconstruction.evaluation_reservation import verify_photo_usage, _seal
    reservation = read_pin(context['reservation'])
    usages = [read_pin(ref) for ref in context['usage_receipts']]
    result = verify_photo_usage(reservation, usages, expected_stages=context['expected_stages'],
                                sealed_candidate=pin(model))
    commitment = context.get('candidate_commitment')
    if commitment is None:
        result['independent_evaluation_eligible'] = False
        result['status'] = 'unverified'
        result['issues'].append({'reason':'final_candidate_not_committed_before_evaluation'})
    else:
        saved = read_pin(commitment)
        expected_path = Path(context['reservation']['path']).parent.parent/'evaluation-consumption.json'
        if (Path(commitment['path']).resolve() != expected_path.resolve()
                or saved.get('reservation_sha256') != context['reservation']['sha256']
                or saved.get('candidate_sha256') != pin(model)['sha256']):
            raise ValueError('Evaluation was already consumed by another candidate')
    return reservation, _seal(result)


def measure_reserved_views(context, model, output, *, history_receipts=()):
    """Seal the final asset/usage before opening evaluation pixels for scoring."""
    from reconstruction.production_validation import measure_heldout_views
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Reserved evaluation output must be fresh')
    output.mkdir(parents=True, exist_ok=True)
    # One immutable commitment per reserved evidence set. A second evaluation of
    # the same bytes is reproducibility; choosing new bytes consumes the holdout.
    reservation, usage = replay_evaluation_context(context, model)
    commitment_path = Path(context['reservation']['path']).parent.parent/'evaluation-consumption.json'
    commitment = {'method':'single_candidate_reserved_evaluation_v1',
                  'reservation_sha256':context['reservation']['sha256'],
                  'candidate_sha256':pin(model)['sha256']}
    encoded = (json.dumps(commitment,sort_keys=True,indent=2)+'\n').encode('utf-8')
    try:
        with commitment_path.open('xb') as stream:
            stream.write(encoded)
    except FileExistsError:
        if commitment_path.read_bytes() != encoded:
            raise ValueError('Reserved evaluation was already consumed by another candidate')
    context['candidate_commitment'] = pin(commitment_path)
    reservation, usage = replay_evaluation_context(context, model)
    (output / 'candidate-seal.json').write_text(json.dumps(usage, indent=2) + '\n', encoding='utf-8')
    photos = [{'id': row['id'], 'path': row['normalized_snapshot']['path'],
               'sha256': row['normalized_snapshot']['sha256'], 'view': row['view']}
              for row in reservation['photos'] if row['purpose'] == 'evaluation' and 'duplicate_of' not in row]
    result = measure_heldout_views(model, photos, source_history_receipts=history_receipts,
                                  evaluation_context=context)
    (output / 'report.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return pin(output / 'report.json')
