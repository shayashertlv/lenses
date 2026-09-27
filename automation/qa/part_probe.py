"""Four authorized Tripo part experiments, with durable paid-call reservations.

This QA harness does not change production models or retry uncertain submissions.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from PIL import Image
import requests

from qa import provider_benchmark as base

ROOT = base.ROOT
DEFAULT_ROOT = ROOT / 'data/part-probes-v1'
SOURCE_ROOT = ROOT / 'data/provider-comparison-v1'
API = 'https://openapi.tripo3d.ai/v3'
CASE_NAMES = tuple(f'{product}-{operation}' for operation in ('segment-auto', 'native-parts')
                   for product in ('oakley', 'miu'))
GUIDED_CASE_NAMES = ('oakley-segment-guided', 'miu-segment-guided')


def prepare_guided(root: Path, source_root: Path = SOURCE_ROOT, evidence_root=None, mask_root=None):
    """Freeze two explicitly authorized follow-ups; keep the original four-call plan intact."""
    evidence_root = evidence_root or ROOT / 'data/part-mask-correspondence-v1'
    mask_root = mask_root or ROOT / 'data/render-mask-probes-v1'
    cases = []
    for product, view, expected_masks in (('oakley', 'front', 1), ('miu', 'angled', 2)):
        name = product+'-segment-guided'
        source = source_root / 'runs' / (product+'-tripo')
        submission = base.read_json(source/'submission.json')
        if submission['http'] != 200 or submission['response'].get('code') != 0:
            raise ValueError('Source task was not accepted')
        task_id = submission['response']['data']['task_id']
        evidence_path = evidence_root / (product+'-'+view) / 'report.json'
        evidence = base.read_json(evidence_path)
        face_ids = np.load(evidence['face_index_path'])
        if evidence['alignment']['iou'] < .97:
            raise ValueError('Reference camera alignment failed')
        artifacts = base.read_json(mask_root / (product+'-'+view) / 'artifacts.json')
        if len(artifacts['masks']) != expected_masks:
            raise ValueError('Unexpected semantic mask count')
        colors = np.zeros((*face_ids.shape, 3), np.uint8)
        colors[face_ids >= 0] = [0, 0, 255]
        rows = []
        assigned = np.zeros(face_ids.shape, bool)
        for item, color in zip(artifacts['masks'], ([255, 0, 0], [0, 255, 0])):
            raw = Path(item['path']).read_bytes()
            if base.digest(raw) != item['sha256']:
                raise ValueError('Semantic mask changed')
            mask = np.asarray(Image.open(BytesIO(raw)).convert('L')) > 127
            if mask.shape != face_ids.shape or np.any(mask & assigned):
                raise ValueError('Semantic mask size/overlap invalid')
            # The pixel-center silhouette excludes antialiased background edge samples.
            mask &= face_ids >= 0
            assigned |= mask
            colors[mask] = color
            rows.append(dict(index=item['index'], sha256=item['sha256'], color_rgb=color,
                             source_pixels=int((np.asarray(Image.open(BytesIO(raw)).convert('L')) > 127).sum()),
                             foreground_pixels=int(mask.sum())))
        buffer = BytesIO()
        Image.fromarray(colors).save(buffer, format='PNG')
        raw = buffer.getvalue()
        directory = root/'runs'/name
        directory.mkdir(parents=True, exist_ok=True)
        evidence_snapshot = directory/'evidence-report.json'
        evidence_raw = evidence_path.read_bytes()
        if evidence_snapshot.exists() and evidence_snapshot.read_bytes() != evidence_raw:
            raise ValueError('Immutable evidence report changed')
        if not evidence_snapshot.exists():
            with evidence_snapshot.open('xb') as handle:
                handle.write(evidence_raw)
        image_path = directory/'reference-mask.png'
        if image_path.exists() and image_path.read_bytes() != raw:
            raise ValueError('Immutable guide image changed')
        if not image_path.exists():
            with image_path.open('xb') as handle:
                handle.write(raw)
        request = dict(schema_version=1, product=product, provider='tripo', operation='segment-guided',
            endpoint=API+'/mesh/segment', estimated_usd=.40, photos=[], original_task_id=task_id,
            settings=dict(input=task_id, model='v2.0-20260430'),
            reference_image=dict(path=str(image_path.resolve()), sha256=base.digest(raw),
                view=view, width=face_ids.shape[1], height=face_ids.shape[0], masks=rows,
                background_rgb=[0, 0, 0], other_visible_geometry_rgb=[0, 0, 255],
                evidence_report_sha256=base.digest(evidence_path.read_bytes()),
                render_sha256=evidence['render_sha256'], model_sha256=evidence['model_sha256'],
                face_raster_sha256=base.digest(Path(evidence['face_index_path']).read_bytes()),
                caveat='Provider documents no camera or palette contract; one-view guidance is experimental'))
        destination = directory/'request.json'
        if destination.exists() and base.read_json(destination) != request:
            raise ValueError('Immutable guided request changed')
        if not destination.exists():
            base.write_json(destination, request, exclusive=True)
        cases.append(dict(case=name, request_sha256=base.digest(base.canonical(request))))
    plan = dict(schema_version=1, cases=cases, maximum_paid_calls=2, estimated_usd=.80)
    destination = root/'guided-prepared.json'
    if destination.exists() and base.read_json(destination) != plan:
        raise ValueError('Immutable guided plan changed')
    if not destination.exists():
        base.write_json(destination, plan, exclusive=True)
    return plan


def prepare(root: Path, source_root: Path = SOURCE_ROOT):
    cases = []
    for name in CASE_NAMES:
        product, operation = name.split('-', 1)
        source = source_root / 'runs' / f'{product}-tripo'
        original = base.read_json(source / 'request.json')
        submitted = base.read_json(source / 'submission.json')
        if submitted['http'] != 200 or submitted['response'].get('code') != 0:
            raise ValueError('Original Tripo task was not accepted')
        task_id = submitted['response']['data']['task_id']
        if not task_id:
            raise ValueError('Original Tripo task missing')
        if operation == 'segment-auto':
            settings = dict(input=task_id, model='v2.0-20260430',
                            segmentation_granularity='detailed', split_by_connectivity=False)
            endpoint, cost = API + '/mesh/segment', .40
        else:
            settings = {key: original['settings'][key] for key in
                        ('model', 'geometry_quality', 'model_seed', 'texture_seed', 'auto_size')}
            settings.update(texture=False, pbr=False, generate_parts=True)
            endpoint, cost = API + '/generation/multiview-to-model', .60
        request = dict(schema_version=1, product=product, provider='tripo', operation=operation,
                       settings=settings, endpoint=endpoint, estimated_usd=cost,
                       photos=original['photos'] if operation == 'native-parts' else [],
                       source_request_sha256=base.digest(base.canonical(original)),
                       original_task_id=task_id)
        directory = root / 'runs' / name
        destination = directory / 'request.json'
        if destination.exists():
            if base.read_json(destination) != request:
                raise ValueError('Immutable experiment request changed')
        else:
            base.write_json(destination, request, exclusive=True)
        if operation == 'native-parts' and not (directory / 'uploads.json').exists():
            # Tokens reference the exact same photo bytes, whose hashes are checked on submit.
            base.write_json(directory / 'uploads.json', base.read_json(source / 'uploads.json'), exclusive=True)
        cases.append(dict(case=name, request_sha256=base.digest(base.canonical(request))))
    plan = dict(schema_version=1, cases=cases, maximum_paid_calls=4, estimated_usd=2.00)
    destination = root / 'prepared.json'
    if destination.exists() and base.read_json(destination) != plan:
        raise ValueError('Immutable experiment plan changed')
    if not destination.exists():
        base.write_json(destination, plan, exclusive=True)
    return plan


def submit(directory: Path, client):
    request = base.read_json(directory / 'request.json')
    if request['operation'] == 'native-parts':
        return base.submit(directory, client)
    if request['operation'] not in ('segment-auto', 'segment-guided') or request['endpoint'] != API + '/mesh/segment':
        raise ValueError('Unexpected probe operation')
    receipt = directory / 'submission.json'
    if receipt.exists():
        return dict(case=directory.name, state='already_submitted')
    reservation = directory / 'submission-reserved.json'
    if reservation.exists():
        return dict(case=directory.name, state='submission_uncertain_or_rejected_no_retry')
    payload = dict(request['settings'])
    if request['operation'] == 'segment-guided':
        reference = request['reference_image']
        raw = Path(reference['path']).read_bytes()
        if base.digest(raw) != reference['sha256']:
            raise ValueError('Immutable reference image changed')
        upload_path = directory/'reference-upload.json'
        if upload_path.exists():
            uploaded = base.read_json(upload_path)
            if uploaded['sha256'] != reference['sha256']:
                raise ValueError('Uploaded reference differs from prepared image')
        else:
            status, response = client.api('POST', API+'/files', 'tripo',
                files={'file': ('reference-mask.png', raw, 'image/png')})
            uploaded = dict(http=status, response=response, sha256=reference['sha256'])
            base.write_json(upload_path, uploaded, exclusive=True)
        if uploaded['http'] != 200 or uploaded['response'].get('code') != 0:
            return dict(case=directory.name, state='reference_upload_failed')
        token = uploaded['response'].get('data', {}).get('file_token')
        if not token:
            raise ValueError('Reference upload did not return a token')
        payload['ref_image'] = token
    base.write_json(reservation, dict(at=base.now(), request_sha256=base.digest(base.canonical(request)),
                    payload_sha256=base.digest(base.canonical(payload)),
                    estimated_usd=request['estimated_usd']), exclusive=True)
    try:
        status, result = client.api('POST', request['endpoint'], 'tripo', json=payload)
    except requests.RequestException as exc:
        base.write_json(directory / 'submission-error.json', dict(at=base.now(), error=type(exc).__name__))
        return dict(case=directory.name, state='submission_uncertain')
    base.write_json(receipt, dict(at=base.now(), http=status, response=result), exclusive=True)
    task_id = result.get('data', {}).get('task_id')
    accepted = status == 200 and result.get('code') == 0 and bool(task_id)
    return dict(case=directory.name, state='submitted' if accepted else 'rejected',
                http=status, task_id=task_id, **({} if accepted else {'response': result}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'prepare-guided', 'submit', 'poll'])
    parser.add_argument('--guided', action='store_true')
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--source-root', type=Path, default=SOURCE_ROOT)
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--case', action='append', choices=CASE_NAMES+GUIDED_CASE_NAMES)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(args.root, args.source_root)))
        return
    if args.command == 'prepare-guided':
        print(json.dumps(prepare_guided(args.root, args.source_root)))
        return
    client = base.Client(args.env)
    prepared = base.read_json(args.root / ('guided-prepared.json' if args.guided else 'prepared.json'))
    if tuple(c['case'] for c in prepared['cases']) != (GUIDED_CASE_NAMES if args.guided else CASE_NAMES):
        raise ValueError('Unexpected probe cases')
    for case in prepared['cases']:
        if args.case and case['case'] not in args.case:
            continue
        directory = args.root / 'runs' / case['case']
        if base.digest(base.canonical(base.read_json(directory / 'request.json'))) != case['request_sha256']:
            raise ValueError('Prepared request changed')
        try:
            result = submit(directory, client) if args.command == 'submit' else base.poll(directory, client)
        except Exception as exc:
            result = dict(case=directory.name, state='client_error', error_type=type(exc).__name__)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
