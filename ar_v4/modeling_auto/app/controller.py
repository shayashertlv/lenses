"""An explicitly started per-job sequence, then human preview."""
import asyncio
import copy
import hashlib
import json
from io import BytesIO
import math
import re
from pathlib import Path
import uuid

from PIL import Image, ImageOps

from .config import ROOT, VERSION
from .storage import Store, ServiceLock, atomic_json, digest, now, sha
from .workflows import (CURRENT_STAGES, native_stage, pipeline_name, pipeline_stages,
                        repeat_stage, run_disclosure, validate_pipeline)

ANGLES = ('front', 'back', 'left', 'right', 'angled')
STAGES = CURRENT_STAGES
DIMENSIONS = {'frame_width', 'lens_width', 'lens_height', 'bridge_width', 'temple_length'}

def runtime_manifest():
    return {str(p.relative_to(ROOT)): sha(p) for folder in ('app', 'blender')
            for p in sorted((ROOT / folder).glob('*.py'))}

LOADED_RUNTIME = runtime_manifest()

class Conflict(ValueError):
    pass

class Controller:
    def __init__(self, settings, *, meshy=None, astra=None, blender=None):
        from .providers import MeshyClient, AstraClient
        from .blender_runner import BlenderRunner
        self.settings = settings
        self.store = Store(settings.data_dir)
        self.lock = ServiceLock(settings.data_dir)
        self.meshy = meshy or MeshyClient(settings.meshy_key)
        self.astra = astra or AstraClient(settings.openai_key)
        self.blender = blender or BlenderRunner(settings.blender_path, timeout=settings.timeout,
            resolution=settings.resolution, samples=settings.samples)
        self.mutation = asyncio.Lock()
        self.task = None
        self.active_job_id = None
        self.cancel_event = asyncio.Event()
        self.closing = False
        self.storage_error = None

    async def open(self):
        self.lock.acquire()
        try:
            for job in self.store.all():
                if job.get('active_operation'):
                    try:
                        self._bind_receipts(job)
                    except (ValueError, OSError, KeyError) as error:
                        self._operation(job)['receipt_error'] = type(error).__name__
                if job['status'] == 'running':
                    job.update(status='interrupted', message='The previous process stopped. Nothing was resumed automatically.',
                               version=job['version'] + 1)
                    if job.get('active_operation'):
                        self._operation(job)['status'] = 'interrupted'
                    self._save(job)
                elif job.get('active_operation'):
                    self._save(job)
        except BaseException:
            self.lock.close()
            raise

    async def close(self):
        self.closing = True
        self.cancel_event.set()
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=15)
            except asyncio.TimeoutError:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
        try:
            await self.meshy.close()
            await self.astra.close()
        finally:
            self.lock.close()

    def _save(self, job):
        try:
            self.store.save(job)
        except OSError:
            self.storage_error = 'Local storage failed. Restart after resolving storage; recorded requests remain counted.'
            raise

    def health(self):
        current = runtime_manifest() == LOADED_RUNTIME
        keys = {'openai': bool(self.settings.openai_key), 'meshy': bool(self.settings.meshy_key)}
        native = bool(self.settings.blender_path and Path(self.settings.blender_path).is_file())
        return {'ready': all(keys.values()) and native and current and not self.storage_error,
                'runtime_ready': current and not self.storage_error, 'runtime_version': VERSION,
                'keys_present': keys, 'blender_available': native, 'active_job_id': self.active_job_id,
                'message': self.storage_error or ('' if current else 'Restart Modeling Auto to load changed files.'),
                'provider_access_verified': False,
                'note': 'Configuration is local. Account access and real provider model quality are not established by offline tests.'}

    def _runtime_ok(self):
        if self.storage_error:
            raise Conflict(self.storage_error)
        if runtime_manifest() != LOADED_RUNTIME:
            raise Conflict('Application files changed. Restart Modeling Auto; saved results remain available.')

    def _operation(self, job):
        return next(o for o in job['operations'] if o['id'] == job['active_operation'])

    def _recoverable(self, job):
        if not job.get('active_operation'):
            return self._continuation(job) is not None
        op = self._operation(job)
        return bool(not op.get('dispatch_started') or op.get('task_id') or op.get('script')
                    or op.get('download') or op.get('native_result'))

    def _auth_rejection(self, job):
        """Recognize a definitive saved rejection without reissuing or rewriting it."""
        if not job.get('active_operation'):
            return None
        op = self._operation(job)
        if (op.get('provider') != 'astra' or not op.get('dispatch_started')
                or op.get('stage') not in set(pipeline_stages(job)) - {'generate', 'texture'}
                or any(op.get(key) for key in ('script', 'native_result', 'download', 'task_id'))):
            return None
        rejection = None
        try:
            for attempt in op.get('attempts', []):
                folder = self.store.path(job, attempt)
                provider = folder / 'provider'
                if any((provider / name).exists() for name in (
                        'astra_response.json', 'astra_script.py', 'recovered_script.py',
                        'response.py', 'astra_failure.json')) or (folder / 'revision' / 'controller-result.json').exists():
                    return None
                raw = provider / 'astra_response.stream'
                receipt = provider / 'astra_http_error.json'
                if not raw.exists():
                    if receipt.exists():
                        return None
                    continue
                raw = self.store.path(job, raw)
                if not 0 < raw.stat().st_size <= 8_000_000:
                    return None
                payload = json.loads(raw.read_text(encoding='utf-8'))
                if (not isinstance(payload, dict) or not isinstance(payload.get('error'), dict)
                        or payload['error'].get('code') != 'invalid_api_key'):
                    return None
                key_hash = None
                if receipt.exists():
                    receipt = self.store.path(job, receipt)
                    if not 0 < receipt.stat().st_size <= 32_000:
                        return None
                    value = json.loads(receipt.read_text(encoding='utf-8'))
                    if (not isinstance(value, dict) or type(value.get('status_code')) is not int
                            or value['status_code'] != 401
                            or value.get('error_code') not in (None, 'invalid_api_key')
                            or value.get('response_sha256') != sha(raw)
                            or not isinstance(value.get('key_sha256'), str)
                            or not re.fullmatch(r'[0-9a-f]{64}', value['key_sha256'])):
                        return None
                    key_hash = value['key_sha256']
                elif op.get('error') != 'Astra HTTP 401; response saved locally, no automatic retry':
                    return None
                if rejection is not None:
                    return None
                rejection = {'key_sha256': key_hash}
        except (ValueError, OSError, KeyError, TypeError):
            return None
        return rejection

    def _continuation(self, job):
        stages = pipeline_stages(job)
        authorization = job.get('authorization') or {}
        pending = authorization.get('stages', [])
        if (authorization.get('pending_reservation') and authorization.get('operation_count') == len(job['operations']) and pending
                and pending[0] == job['stage'] and job['stage'] in stages):
            return job['stage']
        if (not job['operations'] and not job['current'] and job['stage'] == 'generate'
                and job.get('authorization')):
            return 'generate'
        if not job['operations'] or not job['current']:
            return None
        last = job['operations'][-1]
        if last.get('status') != 'complete' or last.get('revision_id') != job['current']['id']:
            return None
        if last['stage'] not in stages:
            return None
        index = stages.index(last['stage']) + 1
        return stages[index] if index < len(stages) else 'review'

    def _bind_receipts(self, job):
        """Bridge a crash between a durable provider reply and job-state commit."""
        def receipt_object(path):
            if path.stat().st_size > 8_000_000:
                raise ValueError('Saved provider receipt exceeds its bounded size.')
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('Saved provider receipt must be a JSON object.')
            return value
        op = self._operation(job)
        for attempt in op.get('attempts', []):
            folder = self.store.path(job, attempt)
            provider = folder / 'provider'
            task = provider / 'task_receipt.json'
            if not op.get('task_id') and task.is_file():
                value = receipt_object(task)
                if value.get('stage') == op['stage'] and isinstance(value.get('task_id'), str):
                    op['task_id'] = value['task_id']
            raw_task = provider / 'submit_response.json'
            if not op.get('task_id') and op['provider'] == 'meshy' and raw_task.is_file():
                value = receipt_object(raw_task).get('result')
                if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value):
                    op['task_id'] = value
            script = provider / 'astra_script.py'
            response = provider / 'astra_response.json'
            if not op.get('script') and response.is_file():
                from .providers import ProviderError, _extract_script
                if (provider / 'astra_failure.json').exists():
                    raise ValueError('Saved Astra request ended with an explicit response failure; no script will execute.')
                try:
                    expected = _extract_script(receipt_object(response))
                except ProviderError as error:
                    raise ValueError('Saved Astra response is incomplete or violates the one-script contract.') from error
                if not script.exists():
                    script = provider / 'recovered_script.py'
                    if not script.exists():
                        script.write_text(expected, encoding='utf-8')
                if script.read_text(encoding='utf-8') == expected:
                    op['script'] = {'path': str(script), 'sha256': sha(script)}
            download = provider / 'download_receipt.json'
            source = provider / 'source.glb'
            if not op.get('download') and download.is_file() and source.is_file():
                value = receipt_object(download)
                if value.get('task_id') == op.get('task_id') and value.get('sha256') == sha(source):
                    op['download'] = {'path': str(source), 'sha256': sha(source)}
            native = folder / 'revision' / 'controller-result.json'
            if not op.get('native_result') and native.is_file():
                value = receipt_object(native)
                try:
                    self._validate_result(job, value, native.parent)
                except (TypeError, AttributeError) as error:
                    raise ValueError('Saved native result has malformed artifact records.') from error
                op['native_result'] = {'path': str(native), 'sha256': sha(native)}

    def public(self, job):
        result = {k: copy.deepcopy(job.get(k)) for k in ('id', 'name', 'version', 'status', 'stage', 'message', 'error',
            'notes', 'edit_instructions', 'dimensions', 'references', 'calls', 'accepted', 'created_at')}
        result['pipeline'] = pipeline_name(job)
        result['pipeline_stages'] = list(pipeline_stages(job))
        def revision(value):
            return {k: copy.deepcopy(value.get(k)) for k in ('id', 'stage', 'blend_url', 'model_url', 'proofs',
                'closeup_url', 'inspection')} if value else None
        result['current'] = revision(job['current'])
        result['revisions'] = [revision(r) for r in job['revisions']]
        result['proposal'] = None
        result['steps'] = [{k: op.get(k) for k in ('id', 'stage', 'status', 'started_at', 'finished_at', 'error', 'task_id')}
                           for op in job['operations']]
        auth_failure = (job['status'] in {'failed', 'cancelled', 'interrupted'}
                        and self._auth_rejection(job) is not None)
        result['auth_failure'] = auth_failure
        if auth_failure:
            result['error'] = result['message'] = (
                'OpenAI rejected the API key. Save a valid OpenAI API key in API setup, '
                'then explicitly retry this Astra stage. Saved revisions and prior request receipts are retained.')
        if job['status'] == 'draft':
            actions = ['start']
        elif job['status'] == 'running':
            actions = [] if self.cancel_event.is_set() else ['cancel']
        elif job['status'] == 'waiting' and job['stage'] == 'review':
            actions = ['edit', 'accept']
        elif job['status'] in {'failed', 'cancelled', 'interrupted'}:
            actions = ['recover'] if self._recoverable(job) else []
            if auth_failure:
                actions.append('retry_auth')
            elif job['current'] and job['stage'] == repeat_stage(job):
                actions.append('edit')
        else:
            actions = []
        result['allowed_actions'] = actions if not self.storage_error else []
        op = self._operation(job) if job.get('active_operation') else None
        result['recovery_kind'] = ('astra_script' if 'recover' in result['allowed_actions']
            and op and op.get('provider') == 'astra' and op.get('script') and not op.get('native_result') else None)
        from .providers import meshy_settings
        result['settings'] = {'generate': meshy_settings('generate'), 'texture': meshy_settings('texture'),
                              'astra': {'model': 'gpt-6-astra', 'reasoning': 'high', 'one_script': True}}
        result['run_disclosure'] = run_disclosure(job)
        return result

    async def create(self, name, notes, dimensions, images, *, pipeline='current'):
        pipeline = validate_pipeline(pipeline)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise ValueError('Enter a product name (1–160 characters).')
        if not isinstance(notes, str) or len(notes) > 6000:
            raise ValueError('Notes must be at most 6,000 characters.')
        if not isinstance(dimensions, dict) or not 3 <= len(dimensions) <= len(DIMENSIONS) or not dimensions.keys() <= DIMENSIONS:
            raise ValueError('Supply at least three named dimensions in millimeters.')
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 1000 for v in dimensions.values()):
            raise ValueError('Dimensions must be finite, positive millimeter values up to 1000.')
        if set(images) != set(ANGLES):
            raise ValueError('Supply all five labeled reference photos.')
        prepared = {}
        for angle, raw in images.items():
            if not 0 < len(raw) <= 24 * 1024 * 1024:
                raise ValueError(f'{angle}: photo must be between 1 byte and 24 MB.')
            try:
                with Image.open(BytesIO(raw)) as source:
                    if source.width * source.height > 40_000_000 or min(source.size) < 64:
                        raise ValueError('Photo dimensions are unsupported.')
                    source.load()
                    picture = ImageOps.exif_transpose(source).convert('RGB')
                    picture.thumbnail((2048, 2048))
                    stream = BytesIO(); picture.save(stream, format='JPEG', quality=95)
                    prepared[angle] = stream.getvalue()
            except (OSError, Image.DecompressionBombError) as error:
                raise ValueError(f'{angle}: invalid image.') from error
        job = {'id': str(uuid.uuid4()), 'name': name.strip(), 'notes': notes, 'dimensions': dimensions,
               'pipeline': pipeline,
               'version': 1, 'status': 'draft', 'stage': 'generate', 'message': 'Ready to start the complete run.',
               'error': None, 'created_at': now(), 'references': [], 'reference_paths': {}, 'artifacts': {},
               'current': None, 'revisions': [], 'calls': {'astra': 0, 'meshy': 0}, 'operations': [],
               'active_operation': None, 'authorization': None, 'accepted': None}
        folder = self.store.directory(job['id']) / 'references'
        folder.mkdir(parents=True)
        for angle in ANGLES:
            (folder / (angle + '.original')).write_bytes(images[angle])
            path = folder / (angle + '.jpg'); path.write_bytes(prepared[angle])
            job['reference_paths'][angle] = str(path)
            job['references'].append({'angle': angle, 'url': self.store.artifact(job, path)})
        self._save(job)
        return self.public(job)

    def _version(self, job, expected):
        if type(expected) is not int or job['version'] != expected:
            raise Conflict('This view is stale. Refresh before making another decision.')

    def _verify_inputs(self, job):
        for reference in job['references']:
            self.store.verified_artifact(job, reference['url'].rsplit('/', 1)[1])
        if job['current']:
            for key in ('blend_url', 'model_url', 'closeup_url'):
                self.store.verified_artifact(job, job['current'][key].rsplit('/', 1)[1])
            for proof in job['current']['proofs']:
                self.store.verified_artifact(job, proof['url'].rsplit('/', 1)[1])

    async def start(self, identifier, version, *, action='start', notes=None):
        async with self.mutation:
            self._runtime_ok()
            job = self.store.load(identifier); self._version(job, version)
            if self.active_job_id is not None or self.closing:
                raise Conflict('Another run is active or the service is stopping.')
            if action not in self.public(job)['allowed_actions']:
                raise Conflict('That action is not available for this saved state.')
            if not self.health()['ready']:
                raise Conflict('Configure both API keys and Blender before starting.')
            rejection = self._auth_rejection(job) if action == 'retry_auth' else None
            rejected_key = None
            if action == 'retry_auth':
                if rejection is None:
                    raise Conflict('A verified authentication rejection is required before retrying this stage.')
                rejected_key = rejection['key_sha256']
            elif action == 'recover':
                rejected_key = (self._operation(job).get('rejected_key_sha256') if job.get('active_operation')
                                else (job.get('authorization') or {}).get('rejected_key_sha256'))
            if rejected_key == hashlib.sha256(self.settings.openai_key.encode()).hexdigest():
                raise Conflict('Save a different OpenAI API key in API setup before retrying this Astra stage.')
            self._verify_inputs(job)
            is_test = pipeline_name(job) == 'test'
            if is_test and action == 'edit' and (not isinstance(notes, str) or not notes.strip()):
                raise ValueError('Write specific instructions for the next finish edit.')
            if is_test and notes is not None and action != 'edit':
                raise ValueError('New instructions are accepted only with another finish edit.')
            if notes is not None:
                if not isinstance(notes, str) or len(notes) > 6000:
                    raise ValueError('Feedback must be at most 6,000 characters.')
                job['edit_instructions' if is_test else 'notes'] = notes.strip() if is_test else notes
            resume = action == 'recover'
            if action == 'retry_auth':
                stage = self._operation(job)['stage']
            else:
                stage = (self._operation(job)['stage'] if job.get('active_operation') else self._continuation(job)) if resume else (repeat_stage(job) if action == 'edit' else 'generate')
            stages = pipeline_stages(job)
            if stage != 'review' and stage not in stages:
                raise Conflict('The saved stage does not belong to this job pipeline.')
            pending_reservation = not (resume and job.get('active_operation'))
            if not resume:
                job['active_operation'] = None
            job['authorization'] = {'id': uuid.uuid4().hex, 'action': action, 'at': now(),
                'operation_count': len(job['operations']), 'pending_reservation': pending_reservation,
                'rejected_key_sha256': rejected_key,
                'stages': list(stages[stages.index(stage):]) if stage != 'review' else [], 'input_sha256': digest({
                    'references': [sha(p) for p in job['reference_paths'].values()],
                    'current': job['current']['master_sha256'] if job['current'] else None,
                    'dimensions': job['dimensions'], 'notes': job['notes'],
                    'pipeline': pipeline_name(job), 'edit_instructions': job.get('edit_instructions')})}
            job.update(status='running', stage=stage, error=None, accepted=None,
                       message='Running the requested sequence. The next review is the final preview.', version=job['version'] + 1)
            self._save(job)
            self.active_job_id = identifier
            self.cancel_event = asyncio.Event()
            self.task = asyncio.create_task(self._run(identifier, stage, resume))
            return self.public(job)

    async def cancel(self, identifier, version):
        async with self.mutation:
            job = self.store.load(identifier); self._version(job, version)
            if self.active_job_id != identifier or job['status'] != 'running':
                raise Conflict('No active run to cancel.')
            self.cancel_event.set()
            job.update(message='Stopping local work. A dispatched remote request may still be charged.', version=job['version'] + 1)
            self._save(job)
            return self.public(job)

    async def accept(self, identifier, version):
        async with self.mutation:
            self._runtime_ok()
            job = self.store.load(identifier); self._version(job, version)
            if 'accept' not in self.public(job)['allowed_actions'] or self.active_job_id is not None:
                raise Conflict('Only the final paused preview can be accepted.')
            self._verify_inputs(job)
            job.update(status='complete', stage='complete', version=job['version'] + 1,
                message='Accepted. Download the exact packed Blender master.',
                accepted={'sha256': job['current']['master_sha256'], 'url': job['current']['blend_url'],
                          'at': now(), 'by': 'owner', 'revision_id': job['current']['id']})
            self._save(job)
            return self.public(job)

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise asyncio.CancelledError()

    async def _update_operation(self, identifier, operation_id, **changes):
        async with self.mutation:
            job = self.store.load(identifier)
            op = next(o for o in job['operations'] if o['id'] == operation_id)
            op.update(changes); self._save(job)

    async def _new_operation(self, identifier, stage):
        async with self.mutation:
            self._check_cancel(); self._runtime_ok()
            job = self.store.load(identifier); self._verify_inputs(job)
            if stage not in pipeline_stages(job):
                raise Conflict('The requested stage does not belong to this job pipeline.')
            provider = 'meshy' if stage in ('generate', 'texture') else 'astra'
            op = {'id': uuid.uuid4().hex, 'stage': stage, 'provider': provider,
                'status': 'reserved', 'started_at': now(), 'attempts': [],
                'input_path': job['current']['blend_path'] if job['current'] else None,
                'input_sha256': job['current']['master_sha256'] if job['current'] else None,
                'model_path': job['current']['model_path'] if job['current'] else None,
                'reference_sha256': {k: sha(p) for k, p in job['reference_paths'].items()}}
            if provider == 'astra':
                op.update(self._astra_input(job, stage))
            authorization = job.get('authorization') or {}
            if (authorization.get('stages') or [None])[0] == stage and authorization.get('rejected_key_sha256'):
                op['rejected_key_sha256'] = authorization['rejected_key_sha256']
            job['operations'].append(op); job['active_operation'] = op['id']
            job['calls'][provider] += 1
            job.update(stage=stage, version=job['version'] + 1, message=f'{stage.title()}: one {provider.title()} request reserved.')
            self._save(job)
            return op['id']

    def _astra_input(self, job, stage):
        """Bind the images and task to this exact revision before any dispatch."""
        current = job['current']
        references = [Path(job['reference_paths'][a]) for a in ANGLES]
        rendered = ([Path(current['proof_paths'][a]) for a in ANGLES]
                    + [Path(current['closeup_path'])])
        if stage == 'connections':
            images = rendered
        elif pipeline_name(job) == 'test' and stage in {'finish', 'finish_refine'}:
            images = references + rendered
        else:
            images = references
        notes = job['notes']
        if pipeline_name(job) == 'test' and job.get('edit_instructions'):
            notes = f'Original product notes:\n{notes}\n\nSpecific instructions for this edit:\n{job["edit_instructions"]}'
        context = {'inspection': copy.deepcopy(current['inspection']), 'dimensions': copy.deepcopy(job['dimensions']),
                   'notes': notes, 'name': job['name'], 'model_sha256': current['master_sha256'],
                   'pipeline': pipeline_name(job)}
        return {'context': context, 'context_sha256': digest(context),
                'images': [{'path': str(p), 'sha256': sha(p)} for p in images]}

    async def _execute(self, identifier, operation_id):
        from .script_validation import validate_script
        self._check_cancel(); self._runtime_ok()
        job = self.store.load(identifier)
        op = next(o for o in job['operations'] if o['id'] == operation_id)
        self._verify_inputs(job)
        if op['input_path'] and sha(self.store.path(job, op['input_path'])) != op['input_sha256']:
            raise ValueError('Operation source changed; it cannot be recovered safely.')
        if op['reference_sha256'] != {k: sha(p) for k, p in job['reference_paths'].items()}:
            raise ValueError('Operation references changed.')
        if op.get('context') and digest(op['context']) != op.get('context_sha256'):
            raise ValueError('Saved Astra task context changed.')
        folder = self.store.directory(identifier) / 'operations' / operation_id / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True)
        await self._update_operation(identifier, operation_id, status='running', attempts=op['attempts'] + [str(folder)])
        if op.get('native_result'):
            record = op['native_result']
            path = self.store.path(job, record['path'])
            if sha(path) != record['sha256']:
                raise ValueError('Saved native result changed.')
            import json
            result = json.loads(path.read_text(encoding='utf-8'))
            result_folder = path.parent
        else:
            references = {k: Path(v) for k, v in job['reference_paths'].items()}
            stage = op['stage']
            provider_dir = folder / 'provider'; provider_dir.mkdir()
            if op['provider'] == 'meshy':
                if op.get('download'):
                    record = op['download']; source = self.store.path(job, record['path'])
                    if sha(source) != record['sha256']:
                        raise ValueError('Saved Meshy download changed; no new download was requested.')
                else:
                    task_id = op.get('task_id')
                    if not task_id:
                        if op.get('dispatch_started'):
                            raise Conflict('The previous Meshy submission has an uncertain outcome. A second paid submission is blocked.')
                        await self._update_operation(identifier, operation_id, dispatch_started=True)
                        task_id = await self.meshy.submit(stage, references, Path(op['model_path']) if op['model_path'] else None,
                            provider_dir, self.cancel_event)
                        await self._update_operation(identifier, operation_id, task_id=task_id)
                    self._check_cancel()
                    remote = await self.meshy.poll(stage, task_id, provider_dir, self.cancel_event)
                    self._check_cancel()
                    source = Path(await self.meshy.download(remote, provider_dir, self.cancel_event)).resolve(strict=True)
                    if not source.is_relative_to(provider_dir.resolve()):
                        raise ValueError('Provider download escaped its output folder.')
                    await self._update_operation(identifier, operation_id, download={'path': str(source), 'sha256': sha(source)})
                self._check_cancel(); self._runtime_ok()
                result = await self.blender.run('import', source, folder / 'revision', stage=stage,
                    dimensions=job['dimensions'], cancel=self.cancel_event)
            else:
                if op.get('script'):
                    path = self.store.path(job, op['script']['path'])
                    if sha(path) != op['script']['sha256']:
                        raise ValueError('Saved Astra script changed.')
                    script = path.read_text(encoding='utf-8')
                else:
                    if op.get('dispatch_started'):
                        raise Conflict('The previous Astra request has an uncertain outcome. A second paid request is blocked.')
                    if op.get('rejected_key_sha256') == hashlib.sha256(self.settings.openai_key.encode()).hexdigest():
                        raise Conflict('Save a different OpenAI API key in API setup before retrying this Astra stage.')
                    snapshot = ({key: op[key] for key in ('context', 'context_sha256', 'images')}
                                if op.get('context') else self._astra_input(job, stage))
                    images = [self.store.path(job, entry['path']) for entry in snapshot['images']]
                    if any(sha(p) != entry['sha256'] for p, entry in zip(images, snapshot['images'])):
                        raise ValueError('Saved Astra task images changed.')
                    context = snapshot['context']
                    await self._update_operation(identifier, operation_id, dispatch_started=True,
                        image_sha256=[sha(p) for p in images], image_count=len(images), **snapshot)
                    script = await self.astra.edit(stage, images, context, provider_dir, self.cancel_event)
                    if not isinstance(script, str):
                        raise ValueError('Astra did not return a Python script.')
                    path = provider_dir / 'response.py'; path.write_text(script, encoding='utf-8')
                    await self._update_operation(identifier, operation_id, script={'path': str(path), 'sha256': sha(path)})
                self._check_cancel(); self._runtime_ok(); validate_script(script)
                result = await self.blender.run('edit', Path(op['input_path']), folder / 'revision',
                    script=script, stage=native_stage(stage), dimensions=job['dimensions'], cancel=self.cancel_event)
            result_folder = folder / 'revision'
            self._validate_result(job, result, result_folder)
            record_path = result_folder / 'controller-result.json'; atomic_json(record_path, result)
            await self._update_operation(identifier, operation_id,
                native_result={'path': str(record_path), 'sha256': sha(record_path)})
        self._check_cancel(); self._runtime_ok()
        self._validate_result(job, result, result_folder)
        if op['input_path'] and sha(Path(op['input_path'])) != op['input_sha256']:
            raise ValueError('The original Blender source changed during execution.')
        async with self.mutation:
            self._check_cancel()
            job = self.store.load(identifier)
            op = next(o for o in job['operations'] if o['id'] == operation_id)
            bundle = copy.deepcopy(result)
            revision_id = f'r{len(job["revisions"]):03d}'
            revision = {'id': revision_id, 'stage': op['stage'], 'blend_path': bundle['blend_path'],
                'model_path': bundle['model_path'], 'proof_paths': bundle['proofs'], 'closeup_path': bundle['closeup_path'],
                'inspection': bundle['inspection'], 'master_sha256': sha(bundle['blend_path']),
                'blend_url': self.store.artifact(job, bundle['blend_path']),
                'model_url': self.store.artifact(job, bundle['model_path']),
                'closeup_url': self.store.artifact(job, bundle['closeup_path']),
                'proofs': [{'angle': a, 'url': self.store.artifact(job, bundle['proofs'][a])} for a in ANGLES]}
            job['current'] = revision; job['revisions'].append(revision)
            op.update(status='complete', finished_at=now(), revision_id=revision_id)
            job.update(active_operation=None, version=job['version'] + 1,
                       message=f'{op["stage"].title()} saved. Continuing the requested run.')
            self._save(job)

    def _validate_result(self, job, result, folder):
        folder = Path(folder).resolve(strict=True)
        paths = [result['blend_path'], result['model_path'], result['closeup_path'],
                 *[result['proofs'][a] for a in ANGLES]]
        if not result.get('inspection', {}).get('geometry_sha256'):
            raise ValueError('Blender returned no geometry inspection.')
        for value in paths:
            path = self.store.path(job, value)
            if not path.is_relative_to(folder) or not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Blender returned an incomplete or foreign revision.')
            recorded = result.get('hashes', {}).get(path.relative_to(folder).as_posix())
            if not recorded or recorded != sha(path):
                raise ValueError('Blender revision failed its saved artifact integrity check.')
        if len({str(Path(p).resolve()) for p in paths}) != len(paths):
            raise ValueError('Blender reused a required output path.')

    async def _run(self, identifier, first, resume):
        try:
            stages = pipeline_stages(self.store.load(identifier))
            for i, stage in enumerate(stages[stages.index(first):] if first != 'review' else ()):
                self._check_cancel(); self._runtime_ok()
                saved_op = self.store.load(identifier)['active_operation'] if resume and i == 0 else None
                op_id = saved_op or await self._new_operation(identifier, stage)
                await self._execute(identifier, op_id)
            async with self.mutation:
                self._check_cancel()
                job = self.store.load(identifier)
                job.update(stage='review', status='waiting', error=None, version=job['version'] + 1,
                           message='Final preview ready. Accept the Blender master or request another material/finish edit.')
                self._save(job)
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            message = ('Process stopped; no automatic resume.' if self.closing else
                       'Cancelled. Dispatched requests remain counted.' if cancelled else str(error))
            for key in (self.settings.openai_key, self.settings.meshy_key):
                if key:
                    message = message.replace(key, '[redacted]')
            async with self.mutation:
                job = self.store.load(identifier)
                status = 'interrupted' if self.closing else 'cancelled' if cancelled else 'failed'
                job.update(status=status, error=None if cancelled else message[:3000],
                           message=message[:3000], version=job['version'] + 1)
                if job.get('active_operation'):
                    self._operation(job).update(status=status, error=message[:3000], finished_at=now())
                    try:
                        self._bind_receipts(job)
                    except (ValueError, OSError, KeyError) as receipt_error:
                        self._operation(job)['receipt_error'] = type(receipt_error).__name__
                self._save(job)
        finally:
            self.active_job_id = None
