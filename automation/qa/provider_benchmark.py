"""Six explicitly authorized provider generations with immutable resumable receipts.

Credentials are read only from the caller-selected dotenv file; never persisted.
Commands: prepare, submit, poll. A submission reservation is never auto-retried.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import socket
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / 'data/provider-comparison-v1'
FAL_ENDPOINTS = {'rodin': 'fal-ai/hyper3d/rodin/v2.5', 'trellis': 'fal-ai/trellis-2'}
SETTINGS = {
    'rodin': dict(tier='Gen-2.5-High', geometry_file_format='glb', material='PBR',
                  quality_mesh_option='500K Triangle', texture_mode='high',
                  enable_creative_mode=False, texture_delight=True, hd_texture=False,
                  use_original_alpha=False, seed=42, preview_render=True),
    'trellis': dict(seed=20260923, resolution=1536, decimation_target=500000,
                    texture_size=2048, remesh=True),
    'tripo': dict(model='v3.1-20260211', geometry_quality='detailed', texture=True,
                  pbr=True, texture_version='v3.5-20260815', texture_quality='detailed',
                  texture_alignment='original_image', delight=True, model_seed=230923,
                  texture_seed=230923, auto_size=False, quad=False, smart_low_poly=False,
                  generate_parts=False),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def write_json(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2)
    else:
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
        tmp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_glb(path):
    raw = Path(path).read_bytes()
    if len(raw) < 20 or struct.unpack_from('<4sII', raw) != (b'glTF', 2, len(raw)):
        raise ValueError('Invalid GLB header')
    offset, kinds, document = 12, [], None
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError('Truncated GLB chunk')
        length, kind = struct.unpack_from('<II', raw, offset)
        offset += 8
        if length % 4 or offset + length > len(raw) or kind in kinds:
            raise ValueError('Invalid GLB chunk framing')
        if not kinds and kind != 0x4E4F534A:
            raise ValueError('First GLB chunk must be JSON')
        if kind == 0x4E4F534A:
            document = json.loads(raw[offset:offset + length])
        kinds.append(kind)
        offset += length
    if not isinstance(document, dict) or document.get('asset', {}).get('version') != '2.0':
        raise ValueError('Invalid glTF document')
    for item in document.get('buffers', []) + document.get('images', []):
        uri = item.get('uri')
        if uri and not uri.startswith('data:'):
            raise ValueError('External glTF resource needs explicit capture')
    return document


def captured_photo(photo):
    raw = Path(photo['path']).read_bytes()
    if digest(raw) != photo['sha256']:
        raise ValueError('Photo changed after preparation')
    if not 0 < len(raw) <= 20 * 1024**2:
        raise ValueError('Photo exceeds 20 MiB')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return raw, 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return raw, 'image/jpeg'
    raise ValueError('Only source PNG/JPEG accepted')


def prepare(root):
    manifest = read_json(root / 'inputs.json')
    if set(manifest['products']) != {'oakley', 'miu'}:
        raise ValueError('This experiment is limited to Oakley and Miu')
    results = []
    for product, item in manifest['products'].items():
        for provider in SETTINGS:
            views = item['providers'][provider]['views']
            if len(views) != len(set(views)):
                raise ValueError('Duplicate photo')
            if provider == 'tripo' and (not 2 <= len(views) <= 4 or 'front' not in views or
                                         set(views) - {'front', 'left', 'back', 'right'}):
                raise ValueError('Tripo requires 2-4 named views including front')
            if provider == 'rodin' and not 1 <= len(views) <= 5:
                raise ValueError('Rodin photo count')
            if provider == 'trellis' and len(views) != 1:
                raise ValueError('TRELLIS.2 takes one photograph')
            photos = []
            for view in views:
                source = item['photos'][view]
                raw, mime = captured_photo(source)
                photos.append(dict(view=view, path=str(Path(source['path']).resolve()),
                                   sha256=digest(raw), bytes=len(raw), mime=mime))
            request = dict(schema_version=1, product=product, provider=provider,
                           endpoint=FAL_ENDPOINTS.get(provider, 'https://openapi.tripo3d.ai/v3/generation/multiview-to-model'),
                           settings=SETTINGS[provider], photos=photos,
                           estimated_usd={'rodin': .4, 'trellis': .35, 'tripo': .6}[provider])
            directory = root / 'runs' / f'{product}-{provider}'
            destination = directory / 'request.json'
            if destination.exists():
                if read_json(destination) != request:
                    raise ValueError('Immutable experiment request changed')
            else:
                write_json(destination, request, exclusive=True)
            results.append(dict(case=directory.name, request_sha256=digest(canonical(request))))
    write_json(root / 'prepared.json', dict(prepared_at=now(), cases=results, max_generations=6, estimated_usd=2.70))
    return results


class Client:
    def __init__(self, env_path):
        self.keys = dotenv_values(env_path)
        if any(not self.keys.get(name) for name in ('FAL_KEY', 'TRIPO_API_KEY')):
            raise ValueError('Both provider credentials must be set')
        self.session = requests.Session()
        self.session.trust_env = False

    def scrub(self, value):
        raw = json.dumps(value)
        for name in ('FAL_KEY', 'TRIPO_API_KEY'):
            raw = raw.replace(self.keys[name], '[REDACTED]')
        return json.loads(raw)

    def api(self, method, url, provider, **kwargs):
        parsed = urlsplit(url)
        allowed = {'openapi.tripo3d.ai'} if provider == 'tripo' else {'queue.fal.run', 'api.fal.ai'}
        if parsed.scheme != 'https' or parsed.hostname not in allowed or parsed.port not in (None, 443):
            raise ValueError('Unapproved API host')
        headers = dict(kwargs.pop('headers', {}))
        headers['Authorization'] = ('Bearer ' + self.keys['TRIPO_API_KEY'] if provider == 'tripo'
                                     else 'Key ' + self.keys['FAL_KEY'])
        response = self.session.request(method, url, headers=headers, timeout=(20, 120),
                                        allow_redirects=False, **kwargs)
        try:
            value = self.scrub(response.json())
        except ValueError:
            value = {'non_json': True, 'body_sha256': digest(response.content)}
        return response.status_code, value

    def download(self, url, destination, max_bytes=512 * 1024**2):
        # Never attach provider credentials to signed artifact URLs.
        for _ in range(5):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError('Expected public HTTPS artifact')
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
                raise ValueError('Nonpublic artifact host')
            with self.session.get(url, stream=True, timeout=(20, 120), allow_redirects=False) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers['Location'])
                    continue
                if response.status_code != 200:
                    raise RuntimeError(f'Artifact download HTTP {response.status_code}')
                if int(response.headers.get('Content-Length', '0')) > max_bytes:
                    raise ValueError('Artifact size limit')
                tmp = destination.with_suffix(destination.suffix + '.partial')
                size = 0
                hasher = hashlib.sha256()
                with tmp.open('wb') as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError('Artifact size limit')
                        handle.write(chunk)
                        hasher.update(chunk)
                tmp.replace(destination)
                return dict(path=str(destination.resolve()), bytes=size, sha256=hasher.hexdigest())
        raise RuntimeError('Artifact redirect limit')


def submit(directory, client):
    request = read_json(directory / 'request.json')
    receipt = directory / 'submission.json'
    if receipt.exists():
        return {'case': directory.name, 'state': 'already_submitted', 'receipt': str(receipt)}
    if (directory / 'submission-reserved.json').exists():
        return {'case': directory.name, 'state': 'submission_uncertain_or_rejected_no_retry'}
    provider = request['provider']
    payload = dict(request['settings'])
    if provider == 'tripo':
        tokens_path = directory / 'uploads.json'
        tokens = read_json(tokens_path) if tokens_path.exists() else {}
        for photo in request['photos']:
            raw, mime = captured_photo(photo)
            if photo['sha256'] not in tokens:
                status, value = client.api('POST', 'https://openapi.tripo3d.ai/v3/files', 'tripo',
                                          files={'file': (f"{photo['view']}.png" if mime == 'image/png' else f"{photo['view']}.jpg", raw, mime)})
                write_json(directory / f"upload-{photo['view']}.json", {'http': status, 'response': value})
                if status != 200 or value.get('code') != 0 or not value.get('data', {}).get('file_token'):
                    return {'case': directory.name, 'state': 'upload_failed', 'http': status, 'response': value}
                tokens[photo['sha256']] = value['data']['file_token']
                write_json(tokens_path, tokens)
        payload['inputs'] = [{photo['view']: {'file_token': tokens[photo['sha256']]}} for photo in request['photos']]
        url = request['endpoint']
    else:
        urls = []
        for photo in request['photos']:
            raw, mime = captured_photo(photo)
            urls.append('data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii'))
        payload['image_urls' if provider == 'rodin' else 'image_url'] = urls if provider == 'rodin' else urls[0]
        url = 'https://queue.fal.run/' + request['endpoint']
    write_json(directory / 'submission-reserved.json', dict(at=now(), request_sha256=digest(canonical(request)),
                payload_sha256=digest(canonical(payload)), estimated_usd=request['estimated_usd']), exclusive=True)
    try:
        status, value = client.api('POST', url, provider, json=payload, headers={'X-Fal-No-Retry': '1'} if provider != 'tripo' else {})
    except requests.RequestException as exc:
        write_json(directory / 'submission-error.json', dict(at=now(), error=type(exc).__name__))
        return {'case': directory.name, 'state': 'submission_uncertain'}
    write_json(receipt, dict(at=now(), http=status, response=value), exclusive=True)
    task_id = value.get('data', {}).get('task_id') if provider == 'tripo' else value.get('request_id')
    return {'case': directory.name, 'http': status, 'task_id': task_id, 'state': 'submitted' if task_id else 'rejected',
            **({} if task_id else {'response': value})}


def collect_urls(provider, result):
    if provider == 'tripo':
        output = result.get('data', {}).get('output', {})
        return [('model', output['model_url'])] if output.get('model_url') else []
    if provider == 'trellis':
        return [('model', result['model_glb']['url'])] if result.get('model_glb') else []
    entries = [('model', result.get('model_mesh', {}).get('url'))]
    entries += [(f'model-{i}', entry.get('url')) for i, entry in enumerate(result.get('model_meshes') or [])]
    entries += [(f'texture-{i}', entry.get('url')) for i, entry in enumerate(result.get('textures') or [])]
    seen, unique = set(), []
    for name, url in entries:
        if url and url not in seen:
            seen.add(url)
            unique.append((name, url))
    return unique


def poll(directory, client):
    if (directory / 'artifacts.json').exists():
        return {'case': directory.name, 'state': 'downloaded'}
    if not (directory / 'submission.json').exists():
        return {'case': directory.name, 'state': 'not_submitted'}
    request, receipt = read_json(directory / 'request.json'), read_json(directory / 'submission.json')
    provider, submitted = request['provider'], receipt['response']
    if provider == 'tripo':
        task_id = submitted.get('data', {}).get('task_id')
        if not task_id:
            return {'case': directory.name, 'state': 'rejected'}
        code, result = client.api('GET', 'https://openapi.tripo3d.ai/v3/tasks/' + task_id, provider)
        write_json(directory / 'status.json', dict(at=now(), http=code, response=result))
        state = result.get('data', {}).get('status', 'unknown')
        if code != 200 or state != 'success':
            return {'case': directory.name, 'state': state, 'http': code, 'progress': result.get('data', {}).get('progress')}
    else:
        if not submitted.get('request_id'):
            return {'case': directory.name, 'state': 'rejected'}
        code, status = client.api('GET', submitted['status_url'], provider)
        write_json(directory / 'status.json', dict(at=now(), http=code, response=status))
        state = status.get('status', 'unknown')
        if code != 200 or state != 'COMPLETED':
            return {'case': directory.name, 'state': state, 'http': code}
        code, result = client.api('GET', submitted['response_url'], provider)
    write_json(directory / 'result.json', dict(at=now(), http=code, response=result))
    if code != 200:
        return {'case': directory.name, 'state': 'provider_failed', 'http': code, 'response': result}
    urls = collect_urls(provider, result)
    if not urls:
        return {'case': directory.name, 'state': 'no_model', 'response': result}
    downloads = directory / 'artifacts'
    downloads.mkdir(exist_ok=True)
    artifacts = []
    for name, url in urls:
        suffix = Path(urlsplit(url).path).suffix.lower()
        if suffix not in {'.glb', '.png', '.jpg', '.jpeg', '.webp', '.zip', '.obj', '.mtl'}:
            suffix = '.bin'
        destination = downloads / (name + suffix)
        item = client.download(url, destination)
        with destination.open('rb') as handle:
            header = handle.read(20)
        if header[:4] == b'glTF':
            if suffix != '.glb':
                renamed = destination.with_suffix('.glb')
                destination.replace(renamed)
                destination = renamed
                item['path'] = str(renamed.resolve())
            validate_glb(destination)
            item['kind'] = 'glb'
        else:
            item['kind'] = 'other'
        item['name'] = name
        artifacts.append(item)
    if not any(item['kind'] == 'glb' for item in artifacts):
        return {'case': directory.name, 'state': 'no_glb', 'artifacts': artifacts}
    write_json(directory / 'artifacts.json', dict(at=now(), product=request['product'], provider=provider,
                                                artifacts=artifacts, credits_consumed=result.get('data', {}).get('credits_consumed')))
    return {'case': directory.name, 'state': 'downloaded', 'artifacts': len(artifacts)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'submit', 'poll'])
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(args.root)))
        return
    client = Client(args.env)
    prepared = read_json(args.root / 'prepared.json')
    for case in prepared['cases']:
        if args.case and case['case'] not in args.case:
            continue
        directory = args.root / 'runs' / case['case']
        if digest(canonical(read_json(directory / 'request.json'))) != case['request_sha256']:
            raise ValueError('Prepared request changed')
        try:
            result = submit(directory, client) if args.command == 'submit' else poll(directory, client)
        except Exception as exc:
            result = {'case': directory.name, 'state': 'client_error', 'error_type': type(exc).__name__}
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
