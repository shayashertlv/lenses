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
    from reconstruction.photo_semantics import build_image_manifest, manifest_from_probe
    raw = json.loads(Path(path).read_bytes())
    manifest = raw.get('image_manifest')
    if manifest is None:
        manifest = manifest_from_probe(raw)
    else:
        manifest = build_image_manifest([{'id': row['photo_id'], 'path': row['local_path'],
                                         'sha256': row['sha256']} for row in manifest])
    return [{'id': row['photo_id'], 'path': row['local_path'], 'sha256': row['sha256']}
            for row in manifest]


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
    from reconstruction.evaluation_reservation import verify_photo_usage
    reservation = read_pin(context['reservation'])
    usages = [read_pin(ref) for ref in context['usage_receipts']]
    result = verify_photo_usage(reservation, usages, expected_stages=context['expected_stages'],
                                sealed_candidate=pin(model))
    return reservation, result


def measure_reserved_views(context, model, output, *, history_receipts=()):
    """Seal the final asset/usage before opening evaluation pixels for scoring."""
    from reconstruction.production_validation import measure_heldout_views
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Reserved evaluation output must be fresh')
    output.mkdir(parents=True, exist_ok=True)
    reservation, usage = replay_evaluation_context(context, model)
    (output / 'candidate-seal.json').write_text(json.dumps(usage, indent=2) + '\n', encoding='utf-8')
    photos = [{'id': row['id'], 'path': row['normalized_snapshot']['path'],
               'sha256': row['normalized_snapshot']['sha256'], 'view': row['view']}
              for row in reservation['photos'] if row['purpose'] == 'evaluation' and 'duplicate_of' not in row]
    result = measure_heldout_views(model, photos, source_history_receipts=history_receipts,
                                  evaluation_context=context)
    (output / 'report.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return pin(output / 'report.json')
