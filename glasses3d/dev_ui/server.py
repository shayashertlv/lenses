"""Loopback-only ASGI routes; no request starts long model or apply work inline."""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .api import Service
from .contracts import APIError, Config, SHA, confined, json_object
from .launcher import Launcher


def error_response(code, message, status=400):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class LocalBoundary:
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.lower(), []).append(value.decode("latin1"))
        hosts = headers.get(b"host", [])
        if len(hosts) != 1 or hosts[0] not in (f"127.0.0.1:{self.config.port}", f"localhost:{self.config.port}"):
            await error_response("invalid_host", "Open this UI on its local address.", 403)(scope, receive, send)
            return
        if scope["method"] in {"POST", "PATCH", "DELETE"}:
            origins = headers.get(b"origin") or headers.get(b"referer", [])
            valid = False
            if len(origins) == 1:
                try:
                    parsed = urlsplit(origins[0])
                    valid = parsed.scheme == "http" and parsed.netloc == hosts[0] and not parsed.username and not parsed.password
                    if b"origin" in headers:
                        valid = valid and not parsed.path and not parsed.query and not parsed.fragment
                except ValueError:
                    pass
            if not valid:
                await error_response("invalid_origin", "Submit this action from the same local UI page.", 403)(scope, receive, send)
                return
        lengths = headers.get(b"content-length", [])
        try:
            if len(lengths) > 1 or lengths and (int(lengths[0]) < 0 or int(lengths[0]) > self.config.max_request_bytes):
                raise ValueError
        except ValueError:
            await error_response("file_too_large", "The upload exceeds the request size limit.", 413)(scope, receive, send)
            return
        size = 0
        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.config.max_request_bytes:
                raise APIError("file_too_large", "The upload exceeds the request size limit.", 413)
            return message
        async def safe_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [(b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"),
                                       (b"referrer-policy", b"same-origin")]
            await send(message)
        await self.app(scope, bounded_receive, safe_send)


async def _json(request):
    return json_object(await request.body())


async def _multipart(request, json_field, required_files=None):
    async with request.form(max_files=64, max_fields=4, max_part_size=4 * 1024 * 1024) as form:
        pairs = list(form.multi_items())
        if len({name for name, _ in pairs}) != len(pairs):
            raise APIError("invalid_form", "Upload fields must be unique.")
        raw = form.get(json_field)
        if not isinstance(raw, str):
            raise APIError("invalid_form", f"The upload must include {json_field} JSON.")
        payload = json_object(raw)
        files = {}
        for name, value in pairs:
            if name == json_field:
                continue
            if not isinstance(value, UploadFile):
                raise APIError("invalid_form", "Unexpected upload field.")
            limit = request.app.state.service.config.max_photo_bytes if json_field == "manifest" else request.app.state.service.config.max_png_bytes
            data = await value.read(limit + 1)
            if len(data) > limit:
                raise APIError("file_too_large", "An uploaded file exceeds the size limit.", 413)
            files[name] = (value.filename, data)
        if required_files is not None and set(files) != set(required_files):
            raise APIError("invalid_form", "The capture needs both viewer and annotated PNG files.")
        return payload, files


def create_app(config=None, *, launcher=None, core_adapter=None, task_launcher=None):
    config = config or Config()
    service = Service(config, launcher or Launcher(config), core_adapter, task_launcher)

    async def call(function, *args):
        return JSONResponse(await run_in_threadpool(function, *args))

    async def configuration(request):
        return await call(service.config_dto)

    async def jobs(request):
        return await call(service.jobs, request.query_params.get("show_test_jobs", "").lower() in {"1", "true"})

    async def start(request):
        payload, files = await _multipart(request, "manifest")
        return await call(service.start_job, payload, files)

    async def run(request):
        return await call(service.run, request.path_params["key"])

    async def stop(request):
        await _json(request)
        return await call(service.stop, request.path_params["key"])

    async def delivery(request):
        return await call(service.delivery, request.path_params["key"])

    async def model(request):
        data, mime = await run_in_threadpool(service.model, request.path_params["key"])
        return StreamingResponse((data[offset:offset + 65536] for offset in range(0, len(data), 65536)),
                                 media_type=mime, headers={"Content-Length": str(len(data))})

    async def ar(request):
        await _json(request)
        return await call(service.ar, request.path_params["key"])

    async def lens_colour(request):
        return await call(service.lens_colour, request.path_params["key"])

    async def lens_preview(request):
        return await call(service.lens_preview, request.path_params["key"], await _json(request))

    async def lens_save(request):
        return await call(service.lens_save, request.path_params["key"], await _json(request))

    async def appearance(request):
        return await call(service.appearance, request.path_params["key"],
                          request.query_params.get("show_test_jobs", "").lower() in {"1", "true"})

    async def appearance_preview(request):
        return await call(service.appearance_preview, request.path_params["key"], await _json(request))

    async def appearance_save(request):
        return await call(service.appearance_save, request.path_params["key"], await _json(request))

    async def appearance_copy(request):
        return await call(service.appearance_copy, request.path_params["key"], await _json(request))

    async def appearance_model(request):
        data = await run_in_threadpool(service.appearance_model, request.path_params["key"], await _json(request))
        return Response(data, media_type="model/gltf-binary")

    async def new_draft(request):
        body = await _json(request)
        return await call(service.new_draft, body.get("delivery_key"))

    async def draft(request):
        return await call(service.draft, request.path_params["key"])

    async def item(request):
        key = request.path_params["key"]
        if request.method == "POST":
            payload, files = await _multipart(request, "capture", ("viewer", "annotated"))
            return await call(service.add_item, key, payload, files["viewer"][1], files["annotated"][1])
        if request.method == "PATCH":
            return await call(service.patch_item, key, request.path_params["item"], await _json(request))
        return await call(service.delete_item, key, request.path_params["item"])

    async def send(request):
        return await call(service.send, request.path_params["key"], await _json(request))

    async def understanding(request):
        return await call(service.understanding, request.path_params["key"])

    async def apply(request):
        return await call(service.apply, request.path_params["key"], await _json(request))

    async def task(request):
        return await call(service.task, request.path_params["key"])

    async def full_edit(request):
        return await call(service.full_edit, request.path_params["key"], request.path_params["item"], await _json(request))

    async def artifact(request):
        sha = request.path_params["sha"]
        if not SHA.fullmatch(sha):
            raise KeyError(sha)
        data, mime = await run_in_threadpool(service.artifacts.read, sha)
        return Response(data, media_type=mime)

    async def static(request):
        path = await run_in_threadpool(confined, config.static_root, request.path_params["path"])
        if path.suffix.lower() not in {".js", ".css", ".html", ".svg", ".png", ".ico"}:
            raise KeyError(path)
        return FileResponse(path)

    async def vendor(request):
        name = request.path_params["path"]
        if not name.endswith(".js") or not (name.startswith("build/") or name.startswith("examples/jsm/")):
            raise KeyError(name)
        return FileResponse(await run_in_threadpool(confined, config.vendor_root, name), media_type="text/javascript")

    async def lens_renderer(request):
        from .lens_renderer import renderer_asset
        try:
            path = await run_in_threadpool(renderer_asset, request.path_params["path"])
        except FileNotFoundError:
            raise KeyError(request.path_params["path"]) from None
        except RuntimeError:
            return error_response("renderer_unavailable", "The lens preview could not load. Check the dev UI log and retry.", 503)
        return FileResponse(path)

    def page(name):
        async def endpoint(request):
            return FileResponse(await run_in_threadpool(confined, config.static_root, name + ".html"))
        return endpoint

    async def handle_error(request, error):
        if isinstance(error, APIError):
            return error_response(error.code, error.message, error.status)
        if isinstance(error, KeyError):
            return error_response("not_found", "That item or file was not found.", 404)
        if isinstance(error, HTTPException):
            return error_response("http_error", str(error.detail), error.status_code)
        if isinstance(error, OSError):
            return error_response("file_unavailable", "A required local file is missing or could not be read.", 409)
        if not isinstance(error, ValueError):
            return error_response("internal_error", "The action could not complete. Check the dev UI server log.", 500)
        return error_response(getattr(error, "code", "invalid_input"), str(error), getattr(error, "status_code", 400))

    routes = [Route("/api/config", configuration), Route("/api/jobs", jobs),
              Route("/api/jobs/start", start, methods=["POST"]), Route("/api/runs/{key}", run),
              Route("/api/runs/{key}/stop", stop, methods=["POST"]), Route("/api/deliveries/{key}", delivery),
              Route("/api/deliveries/{key}/model.glb", model), Route("/api/deliveries/{key}/ar", ar, methods=["POST"]),
              Route("/api/deliveries/{key}/lens-colour", lens_colour),
              Route("/api/deliveries/{key}/lens-colour/preview", lens_preview, methods=["POST"]),
              Route("/api/deliveries/{key}/lens-colour/save", lens_save, methods=["POST"]),
              Route("/api/deliveries/{key}/appearance", appearance),
              Route("/api/deliveries/{key}/appearance-preview", appearance_preview, methods=["POST"]),
              Route("/api/deliveries/{key}/appearance-save", appearance_save, methods=["POST"]),
              Route("/api/deliveries/{key}/appearance-copy", appearance_copy, methods=["POST"]),
              Route("/api/deliveries/{key}/appearance-model", appearance_model, methods=["POST"]),
              Route("/api/edit-drafts", new_draft, methods=["POST"]), Route("/api/edit-drafts/{key}", draft),
              Route("/api/edit-drafts/{key}/items", item, methods=["POST"]),
              Route("/api/edit-drafts/{key}/items/{item}", item, methods=["PATCH", "DELETE"]),
              Route("/api/edit-drafts/{key}/send", send, methods=["POST"]),
              Route("/api/edits/{key}/understanding", understanding), Route("/api/edits/{key}/apply", apply, methods=["POST"]),
              Route("/api/edits/{key}/items/{item}/full-edit", full_edit, methods=["POST"]),
              Route("/api/tasks/{key}", task), Route("/api/artifacts/{sha}", artifact),
              Route("/static/{path:path}", static), Route("/vendor/three/{path:path}", vendor),
              Route("/lens-renderer/{path:path}", lens_renderer)]
    routes += [Route("/", page("index")), Route("/new", page("new"))]
    routes += [Route("/edit-version/{key}", page("general-edit"))]
    routes += [Route(f"/{name}/{{key}}", page(name)) for name in ("run", "results", "edit", "understand", "task", "lens-colour")]
    app = Starlette(routes=routes, exception_handlers={APIError: handle_error, ValueError: handle_error,
        KeyError: handle_error, HTTPException: handle_error, OSError: handle_error, Exception: handle_error})
    app.state.service = service
    app.add_middleware(LocalBoundary, config=config)
    return app
