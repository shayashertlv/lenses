"""Local-only HTTP application; all work is explicitly started by its owner."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings
from .controller import ANGLES, Conflict, Controller
from .workflows import DEFAULT_PIPELINE, canonical_pipeline

ORIGINS = {f'http://{host}:{port}' for host in ('127.0.0.1', 'localhost') for port in (8060, 8061)}
HOSTS = {f'{host}:{port}' for host in ('127.0.0.1', 'localhost') for port in (8060, 8061)}

def create_app(controller=None, settings=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.controller = controller or Controller(settings or Settings.load())
        await app.state.controller.open()
        try:
            ready = getattr(app.state, 'on_ready', None)
            if ready is not None:
                ready()
            yield
        finally:
            await app.state.controller.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if controller is not None:
        app.state.controller = controller

    @app.middleware('http')
    async def local_only(request, call_next):
        if request.headers.get('host', '').lower() not in HOSTS:
            return JSONResponse({'detail': 'This service is available only on its local address.'}, status_code=403)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin is not None and origin not in ORIGINS:
                return JSONResponse({'detail': 'Foreign request origin rejected.'}, status_code=403)
            media = request.headers.get('content-type', '').split(';')[0]
            if media not in {'application/json', 'multipart/form-data'}:
                return JSONResponse({'detail': 'Use a JSON or image upload request.'}, status_code=415)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'detail': str(error)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({'detail': 'Saved item not found.'}, status_code=404)

    async def body(request):
        raw = await request.body()
        if len(raw) > 32_000:
            raise ValueError('Request is too large.')
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise ValueError('Invalid JSON request.') from error
        if not isinstance(value, dict):
            raise ValueError('Request must be an object.')
        return value

    @app.get('/api/health')
    async def health(request: Request):
        return request.app.state.controller.health()

    @app.post('/api/shutdown')
    async def shutdown(request: Request):
        await body(request)
        c = request.app.state.controller
        async with c.mutation:
            if c.active_job_id is not None:
                raise Conflict('Cancel the active run and wait until it stops before stopping the service.')
            stop = getattr(request.app.state, 'shutdown', None)
            if stop is None:
                raise Conflict('This process is not running through the Modeling Auto launcher.')
            c.closing = True
            stop()
        return {'stopping': True}

    @app.get('/api/jobs')
    async def jobs(request: Request):
        c = request.app.state.controller
        return {'jobs': [c.public(j) for j in reversed(c.store.all())]}

    @app.get('/api/jobs/{identifier}')
    async def job(identifier: str, request: Request):
        c = request.app.state.controller
        return c.public(c.store.load(identifier))

    @app.post('/api/jobs')
    async def create(request: Request):
        async with request.form(max_files=5, max_fields=4, max_part_size=24 * 1024 * 1024) as form:
            required = {'name', 'notes', 'dimensions', *ANGLES}
            fields = set(form.keys())
            if fields not in (required, required | {'pipeline'}) or len(form.multi_items()) != len(fields):
                raise ValueError('Supply name, notes, dimensions and exactly five labeled photos.')
            try:
                pipeline = canonical_pipeline(form.get('pipeline', DEFAULT_PIPELINE))
            except ValueError:
                raise ValueError('Choose the standard or legacy pipeline.') from None
            try:
                dimensions = json.loads(form['dimensions'])
            except (TypeError, ValueError) as error:
                raise ValueError('Invalid dimensions.') from error
            images = {}
            for angle in ANGLES:
                upload = form[angle]
                if not hasattr(upload, 'read'):
                    raise ValueError('Each reference must be an uploaded image.')
                images[angle] = await upload.read(24 * 1024 * 1024 + 1)
            return await request.app.state.controller.create(form['name'], form['notes'], dimensions, images, pipeline=pipeline)

    @app.post('/api/jobs/{identifier}/{action}')
    async def action(identifier: str, action: str, request: Request):
        c = request.app.state.controller
        value = await body(request)
        if action in {'start', 'edit', 'recover', 'retry_auth'}:
            return await c.start(identifier, value.get('version'), action=action,
                                 notes=value.get('notes') if action == 'edit' else None)
        if action in {'cancel', 'accept'}:
            return await getattr(c, action)(identifier, value.get('version'))
        raise KeyError(action)

    @app.get('/api/jobs/{identifier}/files/{artifact_id}')
    async def artifact(identifier: str, artifact_id: str, request: Request):
        c = request.app.state.controller
        job = c.store.load(identifier)
        path = c.store.verified_artifact(job, artifact_id)
        types = {'.blend': 'application/octet-stream', '.glb': 'model/gltf-binary',
                 '.jpg': 'image/jpeg', '.png': 'image/png'}
        if path.suffix.lower() not in types:
            raise KeyError(artifact_id)
        filename = None
        if path.suffix == '.blend':
            filename = re.sub(r'[^A-Za-z0-9._-]+', '_', job['name']).strip('._')[:100] or 'model'
            filename += '.blend'
        return FileResponse(path, media_type=types[path.suffix.lower()], filename=filename)

    @app.post('/api/settings')
    async def configure(request: Request):
        from .providers import AstraClient, MeshyClient
        c = request.app.state.controller
        value = await body(request)
        if not value or not value.keys() <= {'openai_key', 'meshy_key'}:
            raise ValueError('Supply the provider keys to configure.')
        async with c.mutation:
            c._runtime_ok()
            if c.active_job_id is not None or c.closing:
                raise Conflict('Wait until this run stops before changing provider settings.')
            keys = {'openai_key': c.settings.openai_key, 'meshy_key': c.settings.meshy_key}
            for key, text in value.items():
                if not isinstance(text, str) or not 8 <= len(text.strip()) <= 500 or any(ch.isspace() for ch in text.strip()):
                    raise ValueError('API keys must be 8–500 characters without whitespace.')
                if key == 'openai_key' and (not text.strip().startswith('sk-') or not text.strip().isascii()):
                    raise ValueError('Enter the complete OpenAI API key beginning with sk-. Copy it again from OpenAI; do not omit the first character.')
                keys[key] = text.strip()
            path = ROOT / '.env'
            temporary = ROOT / ('.env.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write('OPENAI_API_KEY=' + keys['openai_key'] + '\nMESHY_API_KEY=' + keys['meshy_key'] + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            await c.meshy.close()
            await c.astra.close()
            c.settings.openai_key = keys['openai_key']
            c.settings.meshy_key = keys['meshy_key']
            c.meshy = MeshyClient(keys['meshy_key'])
            c.astra = AstraClient(keys['openai_key'])
            return c.health()

    dist = ROOT / 'dist'
    if (dist / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    @app.get('/')
    async def index():
        if not (dist / 'index.html').is_file():
            return JSONResponse({'detail': 'Build the local frontend with npm run build.'}, status_code=503)
        return FileResponse(dist / 'index.html')

    return app

app = create_app()
