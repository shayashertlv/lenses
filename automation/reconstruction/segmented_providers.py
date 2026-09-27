"""Resumable Tripo generation/segmentation and SAM3 evidence requests.

Transport is explicitly injected. Durable reservations prevent uncertain paid
submissions from being repeated; completed artifacts are verified on every use.
The existing provider benchmark transport supplies allowlisted HTTPS and secret
scrubbing. This module adds product-independent task contracts and budgets.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re

import requests
import numpy as np
from PIL import Image

from qa import provider_benchmark as transport


TRIPO = 'https://openapi.tripo3d.ai/v3'
SAM_ENDPOINT = 'fal-ai/sam-3/image'
SAM_SETTINGS = dict(prompt='eyeglass lenses', apply_mask=False, sync_mode=False,
                    output_format='png', return_multiple_masks=True, max_masks=4,
                    include_scores=True, include_boxes=True)
GENERATION_SETTINGS = dict(transport.SETTINGS['tripo'])
SEGMENTATION_SETTINGS = dict(model='v2.0-20260430', segmentation_granularity='detailed',
                             split_by_connectivity=False)
HEADERS = {'X-Fal-No-Retry': '1', 'x-app-fal-disable-fallback': 'true'}


def pin(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=transport.digest(path.read_bytes()), bytes=path.stat().st_size)


def verified(item):
    path = Path(item['path']).resolve()
    if transport.digest(path.read_bytes()) != item['sha256']:
        raise ValueError('Pinned provider input/artifact changed')
    return path


def immutable(path, value):
    path = Path(path)
    if path.exists():
        if transport.read_json(path) != value:
            raise ValueError(f'Immutable provider record changed: {path.name}')
    else:
        transport.write_json(path, value, exclusive=True)


@dataclass
class SubmissionBudget:
    """Allowance for this invocation; existing submissions do not spend it."""
    maximum_new_calls: int = 0
    used: int = 0

    def reserve(self):
        if (type(self.maximum_new_calls) is not int or self.maximum_new_calls < 0 or
                type(self.used) is not int or self.used < 0):
            raise ValueError('Nonnegative integer submission allowance required')
        if self.used >= self.maximum_new_calls:
            return False
        self.used += 1
        return True


def tripo_request(product, operation, photos=(), input_task=None):
    if operation == 'generation':
        chosen = [dict(p) for p in photos if p.get('provider_input', p.get('view') in ('front', 'left', 'back', 'right'))]
        views = [p['view'] for p in chosen]
        if not 2 <= len(chosen) <= 4 or 'front' not in views or len(views) != len(set(views)) or set(views) - {'front', 'left', 'back', 'right'}:
            raise ValueError('Tripo requires a front plus 1-3 distinct axial views; oblique photos are not relabeled')
        for photo in chosen:
            raw, mime = transport.captured_photo(photo)
            photo.update(mime=mime, bytes=len(raw))
        settings = dict(GENERATION_SETTINGS)
        endpoint, estimate = TRIPO + '/generation/multiview-to-model', .60
    elif operation == 'segment':
        if not isinstance(input_task, str) or not input_task or '/' in input_task:
            raise ValueError('Retained successful Tripo generation task ID required')
        chosen, settings = [], dict(SEGMENTATION_SETTINGS, input=input_task)
        endpoint, estimate = TRIPO + '/mesh/segment', .40
    else:
        raise ValueError('Unknown Tripo operation')
    return dict(schema_version=1, product=product, provider='tripo', operation=operation,
                endpoint=endpoint, photos=chosen, settings=settings, estimated_usd=estimate)


def _check_reservation(directory, request):
    reserved = transport.read_json(directory/'submission-reserved.json')
    if reserved['request_sha256'] != transport.digest(transport.canonical(request)):
        raise ValueError('Provider reservation request changed')
    payload = dict(request['settings'])
    if request.get('endpoint') == SAM_ENDPOINT:
        image_path = verified(request['image'])
        payload['image_url'] = 'data:image/png;base64,'+base64.b64encode(image_path.read_bytes()).decode()
    elif request.get('provider') == 'tripo' and 'input' not in payload:
        tokens = transport.read_json(directory/'uploads.json')
        payload['inputs'] = [{photo['view']:{'file_token':tokens[photo['sha256']]}} for photo in request['photos']]
    if reserved['payload_sha256'] != transport.digest(transport.canonical(payload)):
        raise ValueError('Provider reservation payload changed')
    return reserved


def _check_completion(directory):
    path = directory/'completion.json'
    if path.exists():
        records = transport.read_json(path)['records']
        expected = {str((directory/name).resolve()) for name in
                    ('request.json', 'submission-reserved.json', 'submission.json', 'result.json', 'artifacts.json')}
        if len(records) != len(expected) or {str(Path(item['path']).resolve()) for item in records} != expected:
            raise ValueError('Provider completion seal has missing or substituted records')
        for item in records:
            verified(item)


def _seal_completion(directory):
    immutable(directory/'completion.json', dict(records=[pin(directory/name) for name in
        ('request.json', 'submission-reserved.json', 'submission.json', 'result.json', 'artifacts.json')]))


def _valid_submission(directory, provider):
    receipt = transport.read_json(directory/'submission.json')
    response = receipt.get('response', {})
    task_id = response.get('data', {}).get('task_id') if provider == 'tripo' else response.get('request_id')
    valid = receipt.get('http') == 200 and bool(task_id) and (provider != 'tripo' or response.get('code') == 0)
    if task_id and (not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', task_id)):
        raise ValueError('Invalid retained provider task identifier')
    return receipt, task_id if valid else None


def _terminal(directory, request, provider_state, **extra):
    value = dict(status='provider_failed', provider_state=provider_state, retry_allowed=False, **extra)
    record = dict(request_sha256=transport.digest(transport.canonical(request)),
                  submission=pin(directory/'submission.json'), outcome=value)
    immutable(directory/'terminal.json', record)
    return value


def _saved_terminal(directory, request):
    path = directory/'terminal.json'
    if not path.exists():
        return None
    terminal = transport.read_json(path)
    if terminal['request_sha256'] != transport.digest(transport.canonical(request)):
        raise ValueError('Provider terminal receipt belongs to another request')
    verified(terminal['submission'])
    return terminal['outcome']


def _verify_tripo_download(directory):
    directory = Path(directory).resolve()
    _check_completion(directory)
    request = transport.read_json(directory/'request.json')
    _check_reservation(directory, request)
    artifacts = transport.read_json(directory / 'artifacts.json')
    models = [item for item in artifacts['artifacts'] if item.get('kind') == 'glb']
    if len(models) != 1:
        raise ValueError('Expected one retained GLB')
    path = verified(models[0])
    transport.validate_glb(path)
    receipt, task_id = _valid_submission(directory, 'tripo')
    if not task_id:
        raise ValueError('Invalid retained Tripo submission')
    result = transport.read_json(directory/'result.json')
    data = result.get('response', {}).get('data', {})
    if (result.get('http') != 200 or result['response'].get('code') != 0 or
            data.get('status') != 'success' or data.get('task_id') != task_id):
        raise ValueError('Retained Tripo completion does not match its successful task')
    if not data.get('output', {}).get('model_url'):
        raise ValueError('Tripo completion did not return a model')
    return dict(status='complete', model=pin(path), task_id=task_id,
                credits_consumed=artifacts.get('credits_consumed'))


def advance_tripo(directory, request, *, client=None, budget=None):
    directory = Path(directory).resolve()
    expected = {'generation': TRIPO+'/generation/multiview-to-model', 'segment': TRIPO+'/mesh/segment'}
    if request.get('provider') != 'tripo' or request.get('endpoint') != expected.get(request.get('operation')):
        raise ValueError('Unexpected Tripo request operation/endpoint')
    for photo in request.get('photos', []):
        transport.captured_photo(photo)
    immutable(directory / 'request.json', request)
    if (directory/'submission-reserved.json').exists():
        _check_reservation(directory, request)
    terminal = _saved_terminal(directory, request)
    if terminal:
        return terminal
    if (directory / 'artifacts.json').exists():
        return _verify_tripo_download(directory)
    if not (directory / 'submission.json').exists():
        if (directory / 'submission-reserved.json').exists():
            return dict(status='submission_uncertain', retry_allowed=False)
        if client is None or budget is None or not budget.reserve():
            return dict(status='awaiting_submission_allowance')
        if request['operation'] == 'generation':
            result = transport.submit(directory, client)
        else:
            payload = request['settings']
            transport.write_json(directory / 'submission-reserved.json',
                dict(at=transport.now(), request_sha256=transport.digest(transport.canonical(request)),
                     payload_sha256=transport.digest(transport.canonical(payload)), estimated_usd=request['estimated_usd']), exclusive=True)
            try:
                status, value = client.api('POST', request['endpoint'], 'tripo', json=payload)
            except requests.RequestException as error:
                transport.write_json(directory / 'submission-error.json', dict(error_type=type(error).__name__), exclusive=True)
                return dict(status='submission_uncertain', retry_allowed=False)
            transport.write_json(directory / 'submission.json', dict(at=transport.now(), http=status, response=value), exclusive=True)
            result = dict(state='submitted' if status == 200 and value.get('code') == 0 and value.get('data', {}).get('task_id') else 'rejected')
        if result['state'] != 'submitted':
            return dict(status=result['state'], retry_allowed=False)
    receipt, task_id = _valid_submission(directory, 'tripo')
    if not task_id:
        return dict(status='rejected', retry_allowed=False)
    if client is None:
        return dict(status='pending', polling_requires_client=True)
    result = transport.poll(directory, client)
    if result['state'] == 'downloaded':
        complete = _verify_tripo_download(directory)
        _seal_completion(directory)
        return complete
    if result['state'].lower() in ('failed', 'cancelled', 'canceled', 'error', 'expired', 'banned'):
        return _terminal(directory, request, result['state'])
    return dict(status='pending' if result['state'] in ('queued', 'running', 'processing') else result['state'], provider_state=result['state'])


def _mask_result(directory):
    result = transport.read_json(directory/'result.json')
    masks = result.get('response', {}).get('masks')
    if result.get('http') != 200 or not isinstance(masks, list) or len(masks) > SAM_SETTINGS['max_masks']:
        raise ValueError('Invalid successful SAM3 mask response')
    if any(not isinstance(m, dict) or not isinstance(m.get('url'), str) for m in masks):
        raise ValueError('Invalid SAM3 mask URL entry')
    return masks


def _verify_mask_download(directory, width, height):
    from qa.render_mask_probe import read_binary_mask
    _check_completion(directory)
    request = transport.read_json(directory/'request.json')
    _check_reservation(directory, request)
    _, task_id = _valid_submission(directory, 'fal')
    if not task_id:
        raise ValueError('Invalid retained SAM3 submission')
    masks = _mask_result(directory)
    artifacts = transport.read_json(directory/'artifacts.json')
    if len(artifacts['masks']) != len(masks):
        raise ValueError('Retained mask count differs from provider response')
    if Path(verified(artifacts['result'])) != directory/'result.json':
        raise ValueError('Mask completion references another result')
    for index, item in enumerate(artifacts['masks']):
        path = verified(item)
        read_binary_mask(path, (width, height))
        if item.get('id') != f'mask-{index:02d}':
            raise ValueError('Retained mask order changed')
        if item.get('source_url_sha256') is not None and item['source_url_sha256'] != transport.digest(masks[index]['url'].encode()):
            raise ValueError('Retained mask belongs to another provider output')
        # Earlier successful artifacts already bind the full result receipt and
        # ordered masks. Verify that lineage without rewriting completed records.
        provider_path = verified(item.get('provider_image', item))
        original = read_binary_mask(provider_path, (width, height))
        with Image.open(path) as normalized:
            pixels = np.asarray(normalized.convert('L'))
        if not np.array_equal(pixels, original.astype('uint8')*255):
            raise ValueError('Normalized mask differs from provider binary mask')
    return dict(status='complete', masks=artifacts['masks'], mask_status='success' if artifacts['masks'] else 'no_detection')


def advance_mask(directory, image, *, client=None, budget=None):
    directory = Path(directory).resolve()
    source = verified(image)
    with Image.open(source) as im:
        if im.format != 'PNG':
            raise ValueError('Saved PNG render required')
        width, height = im.size
    request = dict(schema_version=1, endpoint=SAM_ENDPOINT, image=pin(source), width=width, height=height,
                   settings=SAM_SETTINGS, headers=HEADERS)
    immutable(directory / 'request.json', request)
    if (directory/'submission-reserved.json').exists():
        _check_reservation(directory, request)
    terminal = _saved_terminal(directory, request)
    if terminal:
        return terminal
    if (directory / 'artifacts.json').exists():
        return _verify_mask_download(directory, width, height)
    submission = directory / 'submission.json'
    if not submission.exists():
        if (directory / 'submission-reserved.json').exists():
            return dict(status='submission_uncertain', retry_allowed=False)
        if client is None or budget is None or not budget.reserve():
            return dict(status='awaiting_submission_allowance')
        payload = dict(SAM_SETTINGS, image_url='data:image/png;base64,' + base64.b64encode(source.read_bytes()).decode())
        transport.write_json(directory / 'submission-reserved.json', dict(at=transport.now(),
            request_sha256=transport.digest(transport.canonical(request)), payload_sha256=transport.digest(transport.canonical(payload))), exclusive=True)
        try:
            status, response = client.api('POST', 'https://queue.fal.run/' + SAM_ENDPOINT, 'fal', json=payload, headers=HEADERS)
        except requests.RequestException as error:
            transport.write_json(directory / 'submission-error.json', dict(error_type=type(error).__name__), exclusive=True)
            return dict(status='submission_uncertain', retry_allowed=False)
        transport.write_json(submission, dict(at=transport.now(), http=status, response=response), exclusive=True)
    submission_receipt, request_id = _valid_submission(directory, 'fal')
    submitted = submission_receipt['response']
    if not request_id:
        return dict(status='rejected', retry_allowed=False)
    result_path = directory / 'result.json'
    if client is None and not result_path.exists():
        return dict(status='pending', polling_requires_client=True)
    if not result_path.exists():
        # Bind read requests to this accepted task, not arbitrary response URLs.
        queue_base = 'https://queue.fal.run/fal-ai/sam-3/requests/' + request_id
        code, state = client.api('GET', queue_base+'/status', 'fal')
        transport.write_json(directory / 'status.json', dict(at=transport.now(), http=code, response=state))
        if code == 200 and str(state.get('status', '')).upper() in ('FAILED', 'ERROR', 'CANCELLED', 'CANCELED'):
            return _terminal(directory, request, state['status'])
        if code != 200 or state.get('status') != 'COMPLETED':
            return dict(status='pending', provider_state=state.get('status', 'unknown'), http=code)
        code, result = client.api('GET', queue_base, 'fal')
        if code == 429 or code >= 500:
            transport.write_json(directory/'result-transient-error.json', dict(at=transport.now(), http=code, response=result))
            return dict(status='pending', provider_state='result_temporarily_unavailable', http=code)
        transport.write_json(result_path, dict(at=transport.now(), http=code, response=result), exclusive=True)
    result = transport.read_json(result_path)
    if result['http'] != 200:
        return _terminal(directory, request, 'result_failed', http=result['http'])
    masks = _mask_result(directory)
    from qa.render_mask_probe import read_binary_mask
    artifacts = []
    for index, item in enumerate(masks):
        source_hash = transport.digest(item['url'].encode())
        download_receipt = directory/f'mask-{index:02d}-download.json'
        provider_destination = directory/f'mask-{index:02d}-provider.png'
        destination = directory / f'mask-{index:02d}.png'
        if download_receipt.exists():
            saved = transport.read_json(download_receipt)
            if saved['source_url_sha256'] != source_hash:
                raise ValueError('Mask download receipt belongs to another result')
            provider_destination = verified(saved['image'])
        else:
            if client is None:
                return dict(status='pending', artifact_download_requires_client=True)
            # An unreceipted file can be an interrupted download or unrelated
            # data. Fetch the same paid task's output; never submit another task.
            client.download(item['url'], provider_destination, max_bytes=20 * 1024**2)
            read_binary_mask(provider_destination, (width, height))
            immutable(download_receipt, dict(source_url_sha256=source_hash, image=pin(provider_destination)))
        binary = read_binary_mask(provider_destination, (width, height))
        from io import BytesIO
        buffer = BytesIO()
        Image.fromarray(binary.astype('uint8')*255).save(buffer, format='PNG')
        normalized = buffer.getvalue()
        if destination.exists():
            if not np.array_equal(read_binary_mask(destination, (width, height)), binary):
                raise ValueError('Existing mask differs from receipted provider output')
            with Image.open(destination) as prior:
                normalized_values = np.asarray(prior.convert('L'))
            if not np.array_equal(normalized_values, binary.astype('uint8')*255):
                # Preserve an old interrupted 0/1 provider PNG; emit a distinct
                # normalized derivative for consumers that expect 0/255 masks.
                destination = directory/f'mask-{index:02d}-normalized.png'
                if destination.exists() and destination.read_bytes() != normalized:
                    raise ValueError('Normalized mask derivative changed')
                if not destination.exists():
                    with destination.open('xb') as stream:
                        stream.write(normalized)
        else:
            with destination.open('xb') as stream:
                stream.write(normalized)
        artifacts.append(dict(id=f'mask-{index:02d}', **pin(destination),
                              provider_image=pin(provider_destination), source_url_sha256=source_hash))
    transport.write_json(directory / 'artifacts.json', dict(masks=artifacts, result=pin(result_path)), exclusive=True)
    complete = _verify_mask_download(directory, width, height)
    _seal_completion(directory)
    return complete
