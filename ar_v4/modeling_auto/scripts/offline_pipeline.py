"""End-to-end HTTP + real providers on fake transport + real Blender, zero paid calls.

The default checks the retained current workflow; --pipeline test checks the
separate six-request workflow and its one-request user refinement loop.
"""
import argparse
import asyncio
import base64
from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from PIL import Image, ImageDraw
from app.blender_runner import BlenderRunner, DEFAULT_BLENDER
from app.config import Settings
from app.controller import Controller, ANGLES
from app.providers import AstraClient, MeshyClient, meshy_settings
from app.server import create_app
from app.storage import sha, atomic_json

async def fixture_native(script, arguments, log_path):
    env = dict(os.environ)
    local_home = log_path.parent / 'native-home'; local_home.mkdir(exist_ok=True)
    env.update(TEMP=str(local_home), TMP=str(local_home), HOME=str(local_home), USERPROFILE=str(local_home),
               BLENDER_USER_RESOURCES=str(local_home), PYTHONNOUSERSITE='1')
    for key in list(env):
        if any(word in key.upper() for word in ('API_KEY', 'OPENAI', 'MESHY', 'TOKEN', 'SECRET')): env.pop(key, None)
    with log_path.open('wb') as log:
        process = await asyncio.create_subprocess_exec(str(DEFAULT_BLENDER), '--background', '--factory-startup',
            '--disable-autoexec', '--python', str(ROOT / 'blender' / script), '--', *[str(v) for v in arguments],
            cwd=ROOT, env=env, stdout=log, stderr=asyncio.subprocess.STDOUT)
        try:
            await asyncio.wait_for(process.wait(), 180)
        finally:
            if process.returncode is None: process.kill(); await process.wait()
    if process.returncode: raise AssertionError(f'Fixture creation failed: {log_path}')

class OfflineWire:
    def __init__(self, folder, blank, pipeline='current'):
        self.folder = folder; self.blank = blank; self.tasks = {}; self.posts = []; self.astra_requests = []
        self.pipeline = pipeline
        self.controller = None
        self.astra_stages = (('lenses', 'connections', 'finish', 'finish_refine', 'finish_refine')
                             if pipeline == 'test' else ('lenses', 'connections', 'finish', 'finish'))

    def current_job(self):
        assert self.controller is not None and self.controller.active_job_id
        return self.controller.store.load(self.controller.active_job_id)

    async def __call__(self, request):
        # Every URL uses this transport. An unexpected request fails locally.
        url = str(request.url)
        if request.method == 'POST' and url.startswith('https://api.meshy.ai/openapi/v1/'):
            payload = json.loads(request.content)
            stage = 'generate' if url.endswith('/multi-image-to-3d') else 'texture'
            assert url.endswith('/multi-image-to-3d' if stage == 'generate' else '/retexture')
            assert all(payload[k] == v for k, v in meshy_settings(stage).items())
            task_id = 'offline-' + stage
            assert task_id not in self.tasks, 'Duplicate Meshy submission'
            if stage == 'generate':
                assert len(payload['image_urls']) == 4 and payload['should_texture'] is False
                source = self.blank
            else:
                assert len(payload['multiview_image_urls']) == 4
                uploaded = self.folder / 'uploaded_for_texture.glb'
                uploaded.write_bytes(base64.b64decode(payload['model_url'].split(',', 1)[1]))
                assert sha(uploaded) == sha(self.current_job()['current']['model_path'])
                source = self.folder / 'returned_textured.glb'
                await fixture_native('synthetic_texture.py', [uploaded, source], self.folder / 'texture-fixture.log')
            self.tasks[task_id] = source; self.posts.append('meshy:' + stage)
            print(json.dumps({'mock_request': 'meshy:' + stage}), flush=True)
            return httpx.Response(200, json={'result': task_id})
        if request.method == 'GET' and url.startswith('https://api.meshy.ai/openapi/v1/'):
            task_id = url.rsplit('/', 1)[1]; assert task_id in self.tasks
            return httpx.Response(200, json={'id': task_id, 'status': 'SUCCEEDED',
                'model_urls': {'glb': 'https://offline-assets.example/' + task_id + '.glb'}})
        if request.method == 'GET' and request.url.host == 'offline-assets.example':
            task_id = request.url.path.split('/')[-1].removesuffix('.glb')
            return httpx.Response(200, content=self.tasks[task_id].read_bytes())
        if request.method == 'POST' and url == 'https://api.openai.com/v1/responses':
            stage = self.astra_stages[len(self.astra_requests)]
            payload = json.loads(request.content)
            images = [x for x in payload['input'][0]['content'] if x['type'] == 'input_image']
            job = self.current_job(); current = job['current']
            assert job['stage'] == stage
            originals = [Path(job['reference_paths'][angle]) for angle in ANGLES]
            renders = [Path(current['proof_paths'][angle]) for angle in ANGLES] + [Path(current['closeup_path'])]
            expected_images = (renders if stage == 'connections' else originals + renders
                if self.pipeline == 'test' and stage in {'finish', 'finish_refine'} else originals)
            image_hashes = [hashlib.sha256(base64.b64decode(x['image_url'].split(',', 1)[1])).hexdigest() for x in images]
            assert image_hashes == [sha(path) for path in expected_images], 'Astra received stale or reordered images'
            context = json.loads(payload['input'][0]['content'][0]['text'].split('\n', 1)[1])
            assert context['model_sha256'] == current['master_sha256'] == sha(current['blend_path'])
            assert context['inspection'] == current['inspection'], 'Astra received stale scene inspection'
            operation = job['operations'][-1]
            assert operation['input_path'] == current['blend_path']
            assert operation['input_sha256'] == current['master_sha256']
            assert operation['image_sha256'] == image_hashes
            assert payload['tool_choice'] == {'type': 'custom', 'name': 'run_blender_python'}
            assert 'previous_response_id' not in payload and len(payload['input']) == 1
            self.astra_requests.append({'stage': stage, 'image_hashes': image_hashes,
                'input_revision': current['id'], 'input_stage': current['stage'],
                'input_sha256': context['model_sha256'], 'notes': context['notes']})
            self.posts.append('astra:' + stage)
            print(json.dumps({'mock_request': 'astra:' + stage, 'input_revision': current['id'],
                              'image_count': len(image_hashes)}), flush=True)
            script = (ROOT / 'scripts' / 'fixtures' / (stage + '.py')).read_text(encoding='utf-8')
            if stage == 'lenses':
                script = (ROOT / 'scripts' / 'fixtures' / 'fused_optics.py').read_text(encoding='utf-8') + '\n' + script
            response = {'id': 'offline-astra-' + str(len(self.astra_requests)), 'status': 'completed',
                'output': [{'type': 'custom_tool_call', 'name': 'run_blender_python', 'input': script}],
                'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}
            stream = 'data: ' + json.dumps({'type': 'response.completed', 'response': response}) + '\n\ndata: [DONE]\n\n'
            return httpx.Response(200, content=stream.encode(), headers={'content-type': 'text/event-stream'})
        raise AssertionError(f'Unmocked request blocked: {request.method} {url}')

async def run(pipeline='current'):
    # Short, unique paths keep native Blender below Windows legacy path limits.
    output = ROOT / 'data' / (('e2t-' if pipeline == 'test' else 'e2e-') + datetime.now().strftime('%H%M%S'))
    output.mkdir(parents=True)
    fixtures = output / 'fixtures'; fixtures.mkdir()
    await fixture_native('synthetic_fixture.py', [fixtures, '--fused'], output / 'blank-fixture.log')
    wire = OfflineWire(output, fixtures / 'blank.glb', pipeline)
    transport_http = httpx.AsyncClient(transport=httpx.MockTransport(wire))
    meshy = MeshyClient('offline-fake-meshy-key', client=transport_http)
    astra = AstraClient('offline-fake-astra-key', client=transport_http)
    controller = Controller(Settings(data_dir=output, openai_key='offline-fake-astra-key',
        meshy_key='offline-fake-meshy-key', blender_path=str(DEFAULT_BLENDER)), meshy=meshy, astra=astra,
        blender=BlenderRunner(resolution=256, samples=8, timeout=600))
    wire.controller = controller
    app = create_app(controller)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8060') as api:
            files = {}
            for i, angle in enumerate(ANGLES):
                picture = Image.new('RGB', (384, 256), (30 + i * 25, 75, 100))
                ImageDraw.Draw(picture).text((20, 20), 'INDEPENDENT SYNTHETIC ' + angle, fill='white')
                stream = BytesIO(); picture.save(stream, format='JPEG')
                files[angle] = (angle + '.jpg', stream.getvalue(), 'image/jpeg')
            form = {'name': 'Offline native ' + pipeline + ' pipeline', 'notes': 'Synthetic fixture only',
                'dimensions': json.dumps({'frame_width': 140, 'lens_width': 50, 'lens_height': 40})}
            if pipeline == 'test':
                form['pipeline'] = pipeline
            response = await api.post('/api/jobs', data=form, files=files)
            assert response.status_code == 200, response.text
            job = response.json(); identifier = job['id']
            response = await api.post(f'/api/jobs/{identifier}/start', json={'version': job['version']})
            assert response.status_code == 200, response.text
            await asyncio.wait_for(controller.task, 1200)
            job = (await api.get(f'/api/jobs/{identifier}')).json()
            assert (job['status'], job['stage']) == ('waiting', 'review'), {
                key: job.get(key) for key in ('id', 'status', 'stage', 'error', 'calls')}
            expected_sequence = ['meshy:generate', 'astra:lenses', 'astra:connections', 'meshy:texture', 'astra:finish']
            if pipeline == 'test':
                expected_sequence.append('astra:finish_refine')
                assert job['pipeline'] == 'test'
            assert wire.posts == expected_sequence
            initial_astra = 4 if pipeline == 'test' else 3
            assert job['calls'] == {'meshy': 2, 'astra': initial_astra}
            original = controller.store.load(identifier)
            source_hashes = {artifact['path']: sha(artifact['path']) for artifact in original['artifacts'].values()}
            assert original['revisions'][2]['inspection']['closeup']['tagged_lens']
            textured = original['revisions'][3]['inspection']
            assert textured['images']
            for revision in original['revisions'][4:]:
                assert textured['geometry_sha256'] == revision['inspection']['geometry_sha256']
                assert textured['images'] == revision['inspection']['images']
            for item in job['current']['proofs'] + [{'url': job['current']['closeup_url']}]:
                response = await api.get(item['url']); assert response.status_code == 200
                with Image.open(BytesIO(response.content)) as image: assert min(image.size) == 256
            if pipeline == 'test':
                for notes in (None, '   '):
                    body = {'version': job['version']}
                    if notes is not None:
                        body['notes'] = notes
                    response = await api.post(f'/api/jobs/{identifier}/edit', json=body)
                    assert response.status_code in {400, 422}, response.text
                    unchanged = (await api.get(f'/api/jobs/{identifier}')).json()
                    assert unchanged['version'] == job['version'] and unchanged['calls'] == job['calls']
                    assert wire.posts == expected_sequence
            extra_notes = 'Refine the frame coat and optical lens roughness once more'
            response = await api.post(f'/api/jobs/{identifier}/edit', json={'version': job['version'], 'notes': extra_notes})
            assert response.status_code == 200, response.text
            await asyncio.wait_for(controller.task, 600)
            job = (await api.get(f'/api/jobs/{identifier}')).json()
            assert job['status'] == 'waiting' and job['calls'] == {'meshy': 2, 'astra': initial_astra + 1}, {
                key: job.get(key) for key in ('id', 'status', 'stage', 'error', 'calls')}
            expected_sequence.append('astra:finish_refine' if pipeline == 'test' else 'astra:finish')
            assert wire.posts == expected_sequence
            if pipeline == 'test':
                assert extra_notes in wire.astra_requests[-1]['notes']
                assert 'Synthetic fixture only' in wire.astra_requests[-1]['notes']
            else:
                assert wire.astra_requests[-1]['notes'] == extra_notes
            final = controller.store.load(identifier)
            assert final['current']['inspection']['geometry_sha256'] == textured['geometry_sha256']
            assert final['current']['inspection']['images'] == textured['images']
            if pipeline == 'test':
                assert [r['input_stage'] for r in wire.astra_requests] == ['generate', 'lenses', 'texture', 'finish', 'finish_refine']
                assert [r['input_revision'] for r in wire.astra_requests] == ['r000', 'r001', 'r003', 'r004', 'r005']
                assert len({r['input_sha256'] for r in wire.astra_requests[2:]}) == 3
                for before, after_request in zip(wire.astra_requests[2:], wire.astra_requests[3:]):
                    assert before['image_hashes'][:5] == after_request['image_hashes'][:5]
                    assert before['image_hashes'][5:] != after_request['image_hashes'][5:], 'Finish pass reused stale renders'
                for op in final['operations']:
                    if op['stage'] == 'finish_refine':
                        native_request = Path(op['native_result']['path']).parent / 'request.json'
                        assert json.loads(native_request.read_text(encoding='utf-8'))['stage'] == 'finish'
            else:
                assert wire.astra_requests[0]['image_hashes'] == wire.astra_requests[2]['image_hashes'] == wire.astra_requests[3]['image_hashes']
            assert all(sha(path) == checksum for path, checksum in source_hashes.items())
            response = await api.post(f'/api/jobs/{identifier}/accept', json={'version': job['version']})
            assert response.status_code == 200, response.text
            accepted = response.json()
            download = await api.get(accepted['accepted']['url'])
            assert download.status_code == 200
            saved = output / 'accepted-download.blend'; saved.write_bytes(download.content)
            assert sha(saved) == accepted['accepted']['sha256'] == final['current']['master_sha256']
            report = {'ok': True, 'pipeline': pipeline, 'paid_calls': 0, 'transport': 'All provider URLs intercepted by httpx.MockTransport',
                'native_blender': str(DEFAULT_BLENDER), 'http_api': True, 'sequence': wire.posts,
                'astra_image_counts': [len(r['image_hashes']) for r in wire.astra_requests], 'job_id': identifier,
                'astra_inputs': wire.astra_requests, 'initial_calls': {'meshy': 2, 'astra': initial_astra},
                'final_calls': job['calls'], 'native_finish_scopes': True,
                'accepted_sha256': sha(saved), 'all_original_revisions_intact': True,
                'packed_textures_preserved_through_finish': True, 'finish_geometry_locked': True,
                'each_request_uses_current_model_and_matching_images': True,
                'empty_additional_notes_rejected': pipeline == 'test',
                'quality_limit': 'Synthetic software validation; real Astra output and Meshy quality remain unmeasured.'}
    # Reopen exact same store: acceptance and every original revision must persist, no POST.
    controller.lock.close()
    after = Controller(controller.settings, meshy=meshy, astra=astra, blender=controller.blender)
    await after.open()
    try:
        assert after.store.load(identifier)['accepted']['sha256'] == report['accepted_sha256']
        assert len(wire.posts) == len(expected_sequence) and after.active_job_id is None
        assert all(sha(path) == checksum for path, checksum in source_hashes.items())
        report['store_reopen_preserves_acceptance_without_dispatch'] = True
        atomic_json(output / 'report.json', report)
        print(json.dumps({'ok': True, 'report': str(output / 'report.json')}), flush=True)
    finally:
        await after.close(); await transport_http.aclose()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipeline', choices=('current', 'test'), default='current')
    asyncio.run(run(parser.parse_args().pipeline))
