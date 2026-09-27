"""Pre-reconstruction reservation and captured image-use provenance.

Staged outside reconstruction while a source-frozen production run is active.
Receipts prove local data flow, not photographic truth or unknown external use.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from reconstruction.input_bundle import _normalize, _canonical, _SLUG, _DEVICE


METHOD = 'pre_reconstruction_evaluation_reservation_v1'
USAGE_METHOD = 'captured_stage_photo_usage_v1'
PHASES = {'provider', 'semantic', 'geometry', 'material', 'frame', 'candidate_selection'}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _seal(value):
    value = deepcopy(value)
    value.pop('receipt_sha256', None)
    value['receipt_sha256'] = _sha(_canonical(value))
    return value


def _verify_seal(value):
    if not isinstance(value, dict) or _seal(value).get('receipt_sha256') != value.get('receipt_sha256'):
        raise ValueError('Evaluation receipt integrity differs')


def _load(value):
    return json.loads(Path(value).read_bytes()) if isinstance(value, (str, Path)) else deepcopy(value)


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n')


def _new_folder(path):
    path = Path(path).resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('Evaluation output must be new or empty')
    return path


def _photo_id(value):
    if not isinstance(value, str) or not _SLUG.fullmatch(value) or _DEVICE.fullmatch(value):
        raise ValueError('Evaluation photo/stage IDs must be safe 1-64 character slugs')
    return value


def _path(value, base):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError('Photo path must be a nonempty local file path')
    value = Path(value)
    return (value if value.is_absolute() else base/value).resolve(strict=True)


def _normalized(raw, identifier, view, source):
    label = view if view in {'front', 'back', 'left', 'right', 'angled', 'unknown'} else 'unknown'
    return _normalize(raw, identifier, label, source)


def _check_crop(parent_png, child_png, crop):
    if not isinstance(crop, list) or len(crop) != 4 or any(type(v) is not int for v in crop):
        raise ValueError('Crop requires integer [left, top, right, bottom] in normalized parent pixels')
    with Image.open(io.BytesIO(parent_png)) as parent, Image.open(io.BytesIO(child_png)) as child:
        left, top, right, bottom = crop
        if not (0 <= left < right <= parent.width and 0 <= top < bottom <= parent.height):
            raise ValueError('Crop exceeds its normalized parent image')
        expected = parent.convert('RGBA').crop(crop)
        if expected.size != child.size or expected.tobytes() != child.convert('RGBA').tobytes():
            raise ValueError('Declared crop does not reproduce the decoded child pixels')


def prepare_evaluation_reservation(request, base_dir, output):
    """Snapshot all owned photos and remove evaluation sources before any model call.

    Public request options: ``evaluation_photos`` (additional ordinary photo
    records), or ``reserved_photo_ids`` (IDs in photos/provider_views), exclusively.
    A photo may declare source_photo_id/crop_xyxy to bind an exact normalized crop.
    Without either option the request is returned untouched and no files are made.
    """
    if not isinstance(request, dict):
        raise ValueError('Expected a request dictionary')
    if not {'evaluation_photos', 'reserved_photo_ids'} & set(request):
        return {'status': 'not_requested', 'reconstruction_request': deepcopy(request),
                'evaluation_photos': [], 'receipt': None, 'receipt_path': None}
    if {'evaluation_photos', 'reserved_photo_ids'} <= set(request):
        raise ValueError('Use evaluation_photos or reserved_photo_ids, not both')
    base, output = Path(base_dir).resolve(strict=True), _new_folder(output)
    initializer = request.get('initializer', {})
    if not isinstance(initializer, dict):
        raise ValueError('initializer must be a dictionary')
    photos, extras = request.get('photos'), initializer.get('provider_views', [])
    if not isinstance(photos, list) or not isinstance(extras, list):
        raise ValueError('photos and provider_views must be lists')
    evaluation = request.get('evaluation_photos', [])
    if not isinstance(evaluation, list) or ('evaluation_photos' in request and not evaluation):
        raise ValueError('evaluation_photos must be a nonempty list')
    ids = request.get('reserved_photo_ids', [])
    if not isinstance(ids, list) or any(not isinstance(v, str) for v in ids) or len(set(ids)) != len(ids):
        raise ValueError('reserved_photo_ids must be distinct photo IDs')
    if 'reserved_photo_ids' in request and not ids:
        raise ValueError('reserved_photo_ids must not be empty')
    captured, by_id, requested = [], {}, set(ids)
    for role, rows, prefix in (('fit', photos, 'photo-'), ('provider', extras, 'provider-view-'),
                               ('evaluation', evaluation, 'evaluation-')):
        for i, photo in enumerate(rows):
            if not isinstance(photo, dict) or 'path' not in photo:
                raise ValueError('Every photo needs a path')
            default = f'{prefix}{i+1:03d}' if role in ('fit', 'evaluation') else f'{prefix}{i+1}'
            identifier = _photo_id(photo.get('id', default))
            if identifier.casefold() in {v.casefold() for v in by_id}:
                raise ValueError('Photo IDs must be unique across all reservation inputs')
            source = _path(photo['path'], base)
            raw = source.read_bytes()
            if photo.get('sha256', _sha(raw)) != _sha(raw):
                raise ValueError('Reserved source differs from its SHA-256 pin')
            record, png = _normalized(raw, identifier, photo.get('view', 'unknown'), source)
            row = {'id': identifier, 'source_role': role, 'view': photo.get('view', 'unknown'),
                   'normalization': record, 'parent_photo_id': photo.get('source_photo_id'),
                   'crop_xyxy': photo.get('crop_xyxy'), 'request_record': deepcopy(photo)}
            captured.append((row, raw, png)); by_id[identifier] = captured[-1]
            if role == 'evaluation':
                requested.add(identifier)
    if not requested <= by_id.keys():
        raise ValueError('A reserved photo ID is absent from photos/provider_views')
    roots, visiting = {}, set()

    def root(identifier):
        if identifier in roots:
            return roots[identifier]
        if identifier in visiting:
            raise ValueError('Photo derivation cycle')
        visiting.add(identifier)
        row, _, png = by_id[identifier]
        parent = row['parent_photo_id']
        if parent is not None:
            if parent not in by_id:
                raise ValueError('Crop parent must be present in the owned photo inventory')
            _check_crop(by_id[parent][2], png, row['crop_xyxy'])
            value = root(parent)
        else:
            if row['crop_xyxy'] is not None:
                raise ValueError('Crop coordinates require source_photo_id')
            value = identifier
        visiting.remove(identifier); roots[identifier] = value
        return value

    for identifier in by_id:
        root(identifier)
    evaluation_roots = {roots[identifier] for identifier in requested}
    seen_pixels, aliases = {}, []
    for row, _, _ in captured:
        row['root_photo_id'] = roots[row['id']]
        row['purpose'] = 'evaluation' if roots[row['id']] in evaluation_roots else 'reconstruction'
        digest = row['normalization']['normalized']['pixel_sha256']
        if digest in seen_pixels:
            previous = seen_pixels[digest]
            if previous['purpose'] != row['purpose']:
                raise ValueError('Duplicate decoded photos cannot cross reconstruction/evaluation sets')
            row['duplicate_of'] = previous['id']; aliases.append({'id': row['id'], 'duplicate_of': previous['id']})
        else:
            seen_pixels[digest] = row
    fit_rows = [row for row, _, _ in captured if row['source_role'] == 'fit' and
                row['purpose'] == 'reconstruction' and 'duplicate_of' not in row]
    if len(fit_rows) < 2 or len({row['root_photo_id'] for row in fit_rows}) < 2:
        raise ValueError('Evaluation reservation unavailable: at least two distinct reconstruction photos must remain')
    # Validate every input and lineage before writing the first snapshot.
    output.mkdir(parents=True, exist_ok=True)
    for row, raw, png in captured:
        for kind, data, suffix in (('original', raw, '.source'), ('normalized', png, '.png')):
            path = output/'photos'/row['id']/(kind+suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data)
            row[kind+'_snapshot'] = {'path': str(path), 'sha256': _sha(data)}
    clean = deepcopy(request)
    clean.pop('evaluation_photos', None); clean.pop('reserved_photo_ids', None)
    clean['photos'] = [{'id': row['id'], 'view': row['normalization']['view'],
                        'path': row['original_snapshot']['path']} for row in fit_rows]
    if extras:
        clean['initializer']['provider_views'] = [
            {**{key: value for key, value in row['request_record'].items()
                if key not in {'source_photo_id', 'crop_xyxy', 'path', 'sha256'}},
             'id': row['id'], 'path': row['normalized_snapshot']['path'],
             'sha256': row['normalized_snapshot']['sha256']}
            for row, _, _ in captured if row['source_role'] == 'provider' and
            row['purpose'] == 'reconstruction' and 'duplicate_of' not in row]
    rows = [{key: value for key, value in row.items() if key != 'request_record'} for row, _, _ in captured]
    receipt = _seal({'schema_version': 1, 'method': METHOD,
                     'request_sha256': _sha(_canonical(request)), 'photos': rows, 'aliases': aliases,
                     'reconstruction_photo_ids': [r['id'] for r in rows if r['purpose'] == 'reconstruction'],
                     'evaluation_photo_ids': [r['id'] for r in rows if r['purpose'] == 'evaluation'],
                     'requested_evaluation_photo_ids': sorted(requested),
                     'reconstruction_request_sha256': _sha(_canonical(clean)),
                     'scope': 'Reserved before this pipeline run; previous external model usage requires separate verified history.'})
    evaluation_rows = [{'id': r['id'], 'view': r['view'], 'path': r['normalized_snapshot']['path'],
                        'sha256': r['normalized_snapshot']['sha256'], 'root_photo_id': r['root_photo_id']}
                       for r in rows if r['purpose'] == 'evaluation' and 'duplicate_of' not in r]
    _write(output/'reconstruction-request.json', clean)
    _write(output/'reservation.json', receipt)
    return {'status': 'reserved', 'reconstruction_request': clean, 'evaluation_photos': evaluation_rows,
            'receipt': receipt, 'receipt_path': str(output/'reservation.json')}


def verify_reservation(value):
    receipt = _load(value); _verify_seal(receipt)
    if receipt.get('method') != METHOD:
        raise ValueError('Unsupported evaluation reservation')
    for row in receipt['photos']:
        original_source=Path(row['normalization']['source_path'])
        if original_source.is_file() and _sha(original_source.read_bytes())!=row['original_snapshot']['sha256']:
            raise ValueError('Original reserved input changed; a new request/reservation is required')
        for key in ('original_snapshot', 'normalized_snapshot'):
            record = row[key]
            if _sha(Path(record['path']).read_bytes()) != record['sha256']:
                raise ValueError('Reserved photo snapshot changed')
        normalized, _ = _normalized(Path(row['original_snapshot']['path']).read_bytes(), row['id'],
                                    row['view'], Path(row['original_snapshot']['path']))
        if normalized['normalized']['pixel_sha256'] != row['normalization']['normalized']['pixel_sha256']:
            raise ValueError('Reserved decoded pixel identity changed')
    return receipt


def _identify_photo(receipt, photo, raw, png, record):
    matches = [row for row in receipt['photos'] if
               record['normalized']['pixel_sha256'] == row['normalization']['normalized']['pixel_sha256']]
    if matches:
        return matches[0], 'exact_decoded_pixels'
    parent_id = photo.get('source_photo_id')
    parent = next((row for row in receipt['photos'] if row['id'] == parent_id), None)
    if parent is not None:
        _check_crop(Path(parent['normalized_snapshot']['path']).read_bytes(), png, photo.get('crop_xyxy'))
        return parent, 'verified_normalized_parent_crop'
    return None, 'source_history_unknown'


def record_photo_usage(reservation, stage_id, phase, actual_photos, output, *, external_history=None):
    """Capture actual stage arguments before execution; return immutable receipt.

    Unknown derivatives are retained as unverified, never silently relabeled.
    Explicit reserved-source leakage raises before the stage/backend can run.
    external_history is a verifier spec {initializer_folder, model_path}, not a
    claimed boolean. Provider phases require it for verified usage history.
    """
    receipt = verify_reservation(reservation)
    _photo_id(stage_id)
    if phase not in PHASES or not isinstance(actual_photos, list):
        raise ValueError('Usage requires a known phase and actual photo argument list')
    if external_history is not None:
        if not isinstance(external_history,dict) or set(external_history)!={'initializer_folder','model_path'}:
            raise ValueError('External history requires actual initializer_folder/model_path, never a verified boolean')
        external_history={key:str(Path(value).resolve()) for key,value in external_history.items()}
    output = _new_folder(output); captured=[]
    for i, photo in enumerate(actual_photos):
        path = Path(photo['path']).resolve(strict=True); raw = path.read_bytes()
        if photo.get('sha256', _sha(raw)) != _sha(raw):
            raise ValueError('Stage photo bytes differ from the supplied pin')
        record, png = _normalized(raw, f'use-{i:03d}', 'unknown', path)
        matched, relation = _identify_photo(receipt, photo, raw, png, record)
        if matched is not None and matched['purpose'] == 'evaluation':
            raise ValueError(f'Reserved evaluation photo leaked into {stage_id}: {matched["id"]}')
        row = {'argument_photo_id': photo.get('id'), 'source_path': str(path),
               'sha256': _sha(raw), 'pixel_sha256': record['normalized']['pixel_sha256'],
               'matched_photo_id': matched['id'] if matched else None,
               'root_photo_id': matched['root_photo_id'] if matched else None,
               'source_relation': relation,
               'source_photo_id': photo.get('source_photo_id'), 'crop_xyxy': photo.get('crop_xyxy')}
        captured.append((row,raw))
    history = (verified_external_history(**external_history) if external_history is not None else
               {'status': 'unverified', 'reason': 'No verified initializer provenance supplied'})
    if external_history is not None:
        if history['status'] == 'verified':
            for photo in history['photos']:
                raw = Path(photo['path']).read_bytes()
                record,png = _normalized(raw,'external','unknown',Path(photo['path']))
                matched,_ = _identify_photo(receipt,photo,raw,png,record)
                if matched is not None and matched['purpose']=='evaluation':
                    raise ValueError('Reserved evaluation photo was used in the cached provider request')
                if matched is None:
                    history={'status':'unverified','reason':'Verified provider used an image outside this owned inventory'}
                    break
    output.mkdir(parents=True,exist_ok=True)
    for i,(row,raw) in enumerate(captured):
        path=output/f'photo-{i:03d}.source'
        with path.open('xb') as stream:stream.write(raw)
        row['snapshot_path']=str(path)
    value=_seal({'schema_version':1,'method':USAGE_METHOD,'stage_id':stage_id,'phase':phase,
                 'reservation_sha256':receipt['receipt_sha256'],'photos':[r for r,_ in captured],
                 'external_history_spec':external_history,'external_history':history,
                 'capture_scope':'Actual photo arguments captured before stage execution; stage inventory is supplied by the job journal.'})
    _write(output/'usage.json',value)
    return value


def verify_photo_usage(reservation, stage_receipts, *, expected_stages, sealed_candidate=None):
    """Recompute local photo membership and require the complete job-stage ledger.

    expected_stages maps journal stage IDs to phases; it must come from trusted
    orchestration, never from a user scalar or the receipts being checked.
    """
    receipt=verify_reservation(reservation)
    if not isinstance(expected_stages,dict) or not expected_stages or any(v not in PHASES for v in expected_stages.values()):
        raise ValueError('Expected stage/phase inventory must come from the job journal')
    usages=[_load(value) for value in stage_receipts]
    if len({u.get('stage_id') for u in usages})!=len(usages):raise ValueError('Duplicate stage usage receipts')
    actual={u.get('stage_id') for u in usages};missing=sorted(set(expected_stages)-actual)
    unexpected=sorted(actual-set(expected_stages));issues=[];used=set()
    if missing:issues.append({'reason':'missing_stage_usage','stages':missing})
    if unexpected:issues.append({'reason':'unexpected_stage_usage','stages':unexpected})
    for usage in usages:
        _verify_seal(usage)
        if usage.get('method')!=USAGE_METHOD or usage.get('reservation_sha256')!=receipt['receipt_sha256']:
            raise ValueError('Usage receipt belongs to another reservation')
        if expected_stages.get(usage['stage_id'])!=usage['phase']:
            issues.append({'reason':'stage_phase_mismatch','stage_id':usage['stage_id']})
        for row in usage['photos']:
            raw=Path(row['snapshot_path']).read_bytes()
            if _sha(raw)!=row['sha256']:raise ValueError('Usage photo snapshot changed')
            record,png=_normalized(raw,'verify','unknown',Path(row['snapshot_path']))
            matched,relation=_identify_photo(receipt,row,raw,png,record)
            if matched is None:
                issues.append({'reason':'unknown_image_source','stage_id':usage['stage_id']})
            else:
                used.add(matched['root_photo_id'])
                if matched['purpose']=='evaluation':issues.append({'reason':'reserved_photo_used','photo_id':matched['id']})
            if relation!=row['source_relation'] or record['normalized']['pixel_sha256']!=row['pixel_sha256']:
                raise ValueError('Usage decoded provenance differs')
        if usage['phase']=='provider' or usage.get('external_history_spec') is not None:
            spec=usage.get('external_history_spec')
            history=verified_external_history(**spec) if spec is not None else {'status':'unverified'}
            prior=usage.get('external_history',{})
            if prior.get('status')=='verified' and history!=prior:
                raise ValueError('Previously verified external provider history changed')
            if history['status']!='verified':
                issues.append({'reason':'initializer_history_unverified','stage_id':usage['stage_id']})
            else:
                for photo in history['photos']:
                    raw=Path(photo['path']).read_bytes();record,png=_normalized(raw,'history','unknown',Path(photo['path']))
                    matched,_=_identify_photo(receipt,photo,raw,png,record)
                    if matched is None:issues.append({'reason':'external_image_source_unknown','stage_id':usage['stage_id']})
                    elif matched['purpose']=='evaluation':issues.append({'reason':'provider_used_reserved_photo','photo_id':matched['id']})
                    else:used.add(matched['root_photo_id'])
    if 'provider' not in expected_stages.values():issues.append({'reason':'initializer_usage_missing'})
    candidate=None
    if sealed_candidate is not None:
        candidate=deepcopy(sealed_candidate)
        if not isinstance(candidate,dict) or set(candidate)!={'path','sha256'} or _sha(Path(candidate['path']).read_bytes())!=candidate['sha256']:
            raise ValueError('Sealed candidate bytes changed')
    return _seal({'schema_version':1,'method':'verified_reserved_photo_usage_v1',
                  'reservation_sha256':receipt['receipt_sha256'],
                  'status':'verified' if not issues else 'unverified',
                  'independent_evaluation_eligible':not issues and candidate is not None and bool(receipt['evaluation_photo_ids']),
                  'issues':issues,'used_root_photo_ids':sorted(used),'expected_stages':expected_stages,
                  'usage_receipt_sha256s':{u['stage_id']:u['receipt_sha256'] for u in usages},
                  'sealed_candidate':candidate,
                  'scope':'Current pipeline photo isolation plus narrowly verified initializer history; no appearance or geometry accuracy claim.'})


def verified_external_history(initializer_folder, model_path):
    """Verify built-in Meshy transport bytes; imported/legacy histories stay unknown."""
    from reconstruction.initializer import _json_bytes, _split_payload, MESHY_ENDPOINT, MESHY_SPLIT_ENDPOINT
    from reconstruction.meshy_transport import _raw_json
    folder,model=Path(initializer_folder).resolve(),Path(model_path).resolve()
    pins={}

    def read(path, decoded=True):
        raw=Path(path).read_bytes();pins[str(Path(path).resolve())]=_sha(raw)
        return json.loads(raw) if decoded else raw

    def unknown(reason):
        return {'status':'unverified','method':'unverified_initializer_history_v1','reason':reason,
                'model_path':str(model),'source_pins':pins}

    def phase(root,request_sha,payload,endpoint,task_id,model_sha):
        submission=read(root/'transport/submission.json');body=_raw_json(payload)
        if (submission.get('request_sha256')!=request_sha or submission.get('payload_sha256')!=_sha(body)
                or submission.get('payload_bytes')!=len(body)):
            raise ValueError('Provider submission payload binding differs')
        exchanges=[]
        for path in sorted((root/'transport').glob('http-*/request.json')):
            request=read(path)
            if request.get('request_sha256')!=request_sha:raise ValueError('Transport request association differs')
            metadata=read(path.with_name('response.json'))
            response=read(path.with_name('response.bin'),False)
            if metadata.get('saved_sha256')!=_sha(response):raise ValueError('Transport response binding differs')
            if metadata.get('error_type') is None and isinstance(metadata.get('status'),int) and 200<=metadata['status']<300:
                exchanges.append((request,metadata,response))
        posts=[r for r,m,b in exchanges if r.get('method')=='POST']
        if (len(posts)!=1 or posts[0].get('url')!=endpoint or posts[0].get('authenticated_api') is not True
                or posts[0].get('body_sha256')!=_sha(body)):
            raise ValueError('Actual provider POST body is not verified')
        post_response=next(b for r,m,b in exchanges if r is posts[0])
        if json.loads(post_response).get('result')!=task_id:raise ValueError('Provider POST task association differs')
        task=read(root/'task_receipt.json')
        if task.get('request_sha256')!=request_sha or task.get('task_id')!=task_id or task.get('association')!='submit_response':
            raise ValueError('Provider task receipt differs')
        urls=[]
        for r,m,b in exchanges:
            if r.get('method')=='GET' and r.get('authenticated_api') is True and r.get('url')==endpoint+'/'+task_id:
                result=json.loads(b)
                if result.get('id')==task_id and result.get('status')=='SUCCEEDED':
                    url=(result.get('model_urls') or {}).get('glb')
                    if isinstance(url,str):urls.append(url)
        if not any(r.get('method')=='GET' and r.get('authenticated_api') is False and r.get('url') in urls
                   and _sha(b)==model_sha and m.get('secret_redacted') is False for r,m,b in exchanges):
            raise ValueError('Provider output bytes lack a verified successful task download')

    try:
        request=read(folder/'request.json');artifact=read(folder/'artifact_receipt.json')
        model_raw=read(model,False);model_sha=_sha(model_raw)
        request_sha=_sha(_json_bytes(request))
        if request.get('kind')!='meshy' or 'source' in request:
            return unknown('Existing/imported model has no verified original provider image history')
        if artifact.get('request_sha256')!=request_sha or artifact.get('sha256')!=model_sha:
            raise ValueError('Initializer artifact is not bound to the exact request/model')
        selected=request.get('selection',{}).get('selected',[])
        if not isinstance(selected,list) or not 1<=len(selected)<=4:raise ValueError('Provider photo selection missing')
        unused=request.get('selection',{}).get('unused',[])
        if not isinstance(unused,list):raise ValueError('Provider selection inventory is invalid')
        photos=[];images=[]
        for index,photo in enumerate([*selected,*unused]):
            candidates=[folder/'provider_photos'/(photo['sha256']+'.image'),Path(photo['path'])]
            owned=next((p for p in candidates if p.is_file() and _sha(p.read_bytes())==photo['sha256']),None)
            if owned is None:return unknown('Original submitted photo bytes are unavailable')
            raw=read(owned,False)
            mime='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else ('image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None)
            if mime is None:raise ValueError('Provider snapshot encoding unsupported')
            submitted=index<len(selected)
            if submitted:images.append(f'data:{mime};base64,'+base64.b64encode(raw).decode('ascii'))
            photos.append({'id':photo['id'],'path':str(owned),'sha256':_sha(raw),
                           'provider_submitted':submitted,'local_selection_input':True})
        payload={**request['settings'],'image_urls':images}
        task_id=artifact['task_id'];generation=artifact.get('generation')
        generation_sha=generation['sha256'] if generation else model_sha
        if generation is not None:
            saved=read(folder/'generation_receipt.json')
            if saved.get('request_sha256')!=request_sha or saved.get('task_id')!=task_id or saved.get('sha256')!=generation_sha:
                raise ValueError('Provider generation association differs')
            if _sha(read(folder/'generated.glb',False))!=generation_sha:raise ValueError('Provider generation bytes differ')
        phase(folder,request_sha,payload,MESHY_ENDPOINT,task_id,generation_sha)
        if generation is not None:
            split_request=read(folder/'split/request.json')
            if split_request.get('input_task_id')!=task_id:raise ValueError('Split refers to another generation')
            phase(folder/'split',_sha(_json_bytes(split_request)),_split_payload(split_request),MESHY_SPLIT_ENDPOINT,
                  artifact['split_task_id'],model_sha)
        return {'status':'verified','method':'exact_meshy_transport_history_v1','model_path':str(model),
                'model_sha256':model_sha,'request_sha256':request_sha,'photos':photos,'source_pins':pins,
                'scope':'Exact local HTTP input/task/output binding; provider internals and earlier unrelated models are not asserted.'}
    except (OSError,ValueError,KeyError,TypeError,IndexError) as error:
        return unknown('Provider history cannot be verified: '+type(error).__name__)
