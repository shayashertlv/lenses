"""Loopback-only agentic eyewear studio. Run with ``python -m blender_agent.studio_server``.

Development/startup never invokes a paid model. Start/resume and Generate specifics are explicit
CSRF-protected actions. Only this studio's allowlisted job artifacts are served.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
import uuid
import webbrowser

import httpx

from . import studio_description
from .studio_jobs import (ACTIVE, AUTOMATION, DEFAULT_BLENDER, DEFAULT_ROOT, JobError, JobStore,
                          atomic_json, bounded_path, clean_process_env, known_spec, now, process_identity, process_options, read_json, sha, validated_specs)


class Studio:
    def __init__(self, store: JobStore, *, port=8767, gemini_model=studio_description.DEFAULT_MODEL):
        self.store = store
        self.port = port
        self.csrf_token = secrets.token_urlsafe(32)
        self.gemini_model = gemini_model
        self.ar_process = None
        self.ar_lock = threading.Lock()
        self.web_root = Path(__file__).with_name("studio_web")
        self.ar_root = AUTOMATION.parent / "ar"
        self.recover_research()

    def recover_research(self):
        """A lost local request is not retried; users can review and explicitly ask again."""
        with self.store.lock:
            for path in (self.store.root / "jobs").glob("*/job.json"):
                job = self.store.metadata(path.parent.name)
                if job["status"] not in {"describing", "researching"}:
                    continue
                owner = job.get("specifics_request") or job.get("description_request") or {}
                identity = process_identity(owner["pid"]) if type(owner.get("pid")) is int else False
                if identity is False or (identity is not None and owner.get("process_created") is not None
                                          and identity != owner["process_created"]):
                    job.update(status="draft", error="Previous Gemini request was interrupted. Its Gemini charge may be unresolved; no automatic retry occurred after restart.")
                    job.pop("description_request", None)
                    job.pop("specifics_request", None)
                    self.store.save(job)

    @property
    def origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

    def config(self):
        keys = studio_description.credentials(self.store.env_file)
        return {"csrf_token": self.csrf_token, "defaults": {"budget_usd": 20, "effort": "max",
                                                           "gemini_model": self.gemini_model},
                "capabilities": {"openai_key": bool(keys.get("OPENAI_API_KEY")),
                                 "gemini_key": bool(keys.get("GEMINI_API_KEY") or keys.get("GOOGLE_API_KEY")),
                                 "blender": self.store.blender.is_file(),
                                 "ar": (self.ar_root / "node_modules/vite/bin/vite.js").is_file(),
                                 "stop": True}, "ar_origin": self.store.ar_origin,
                "specifics_billing": "Google Search research is separately billed: at most two Gemini Pro attempts, plus one syntax-only repair per malformed answer. The Astra cap covers building and resumes."}

    def specifics(self, job_id):
        with self.store.lock:
            job = self.store.metadata(job_id)
            if job["status"] != "draft" or job["started_once"]:
                raise JobError("Generate specifics is available only before the first start", 409)
            keys = studio_description.credentials(self.store.env_file)
            key = keys.get("GEMINI_API_KEY") or keys.get("GOOGLE_API_KEY")
            if not key:
                raise JobError("Configure GEMINI_API_KEY or GOOGLE_API_KEY on the server", 409)
            if not job["name"].strip() or job["name"] == "Untitled eyewear":
                raise JobError("Enter the exact brand, model and variant/SKU before generating specifics", 400)
            job["status"] = "researching"
            job["specifics_request"] = {"pid": os.getpid(), "process_created": process_identity(os.getpid()), "started_at": now()}
            job["error"] = None
            self.store.save(job)
        receipts = self.store.directory(job_id) / "specifics-requests"
        receipt_path = receipts / (uuid.uuid4().hex + ".json")
        journal = {"created_at": now(), "product_name": job["name"], "state": "started", "attempts": []}
        def record_attempts(value):
            journal.update(value)
            atomic_json(receipt_path, journal)
        try:
            receipts.mkdir(exist_ok=True)
            atomic_json(receipt_path, journal)
            provenance = job.get("spec_provenance", {})
            user_specs = {k: v for k, v in job["specs"].items() if known_spec(v) and provenance.get(k, {}).get("kind") != "web"}
            result = studio_description.generate_specifics(self.store.image_paths(job), job["name"], user_specs,
                key=key, model=self.gemini_model, on_attempt=record_attempts)
            result["specs"] = validated_specs(result["specs"])
            with self.store.lock:
                job = self.store.metadata(job_id)
                previous_auto = validated_specs((job.get("specifics") or {}).get("specs", {}))
                previous_provenance = job.get("spec_provenance", {})
                manual = {k: v for k, v in validated_specs(job["specs"]).items() if known_spec(v)
                    and not (previous_provenance.get(k, {}).get("kind") == "web" and v == previous_auto.get(k))}
                specs = {k: v for k, v in result["specs"].items() if known_spec(v)}
                provenance = {k: {"kind": "web", "evidence": result["evidence"].get(k, [])} for k in specs}
                for field, value in manual.items():
                    if specs.get(field) is not None and specs[field] != value:
                        result["uncertainties"].append(f"{field}: your supplied value was retained over the different researched value.")
                    specs[field] = value
                    provenance[field] = {"kind": "user", "evidence": []}
                canonical = studio_description.canonical_json(result)
                specifics_path = self.store.directory(job_id) / "specifics.json"
                temporary = specifics_path.with_name("specifics." + uuid.uuid4().hex + ".tmp")
                temporary.write_text(canonical + "\n", encoding="utf-8")
                os.replace(temporary, specifics_path)
                job.update(specs=specs, spec_provenance=provenance, specifics=result, specifics_json=canonical,
                           uncertainties=result["uncertainties"], specifics_model=result["model"], status="draft")
                self.store.save(job)
                journal.update(state="completed", result=result)
                atomic_json(receipt_path, journal)
                return {**result, "specs": specs}
        except studio_description.SpecificsError as error:
            journal.update(state="failed", error=str(error), attempts=error.attempts)
            atomic_json(receipt_path, journal)
            with self.store.lock:
                job = self.store.metadata(job_id)
                job["error"] = str(error)
                self.store.save(job)
            raise JobError(str(error), 502) from None
        finally:
            with self.store.lock:
                job = self.store.metadata(job_id)
                if job["status"] == "researching":
                    job["status"] = "draft"
                job.pop("specifics_request", None)
                self.store.save(job)

    def materials(self, job_id):
        from .studio_materials import describe_model
        job = self.store.metadata(job_id)
        result = job.get("result") or {}
        value = describe_model(self.store.artifact(job_id, "model.glb"), revision=result.get("revision", "original"),
                               viewer=result.get("viewer"))
        value["source_blend_matches_revision"] = result.get("scene_matches_revision", False)
        return value

    def change_materials(self, job_id, payload):
        from .studio_materials import describe_model, validate_viewer, write_material_revision
        with self.store.lock:
            job = self.store.metadata(job_id)
            if job["status"] in ACTIVE:
                raise JobError("Wait until the agent stops before editing exported materials", 409)
            current = job.get("result") or {}
            if payload.get("base_revision") != current.get("revision", "original"):
                raise JobError("The model revision changed; reload before saving", 409)
            if set(payload) - {"base_revision", "edits", "viewer"}:
                raise JobError("Unsupported material request field")
            edits = payload.get("edits", {})
            viewer = validate_viewer(payload.get("viewer", current.get("viewer")))
            source = self.store.artifact(job_id, "model.glb")
            revision = uuid.uuid4().hex[:16]
            directory = self.store.directory(job_id)
            target = directory / "revisions" / revision
            target.mkdir(parents=True)
            try:
                receipt = write_material_revision(source, target / "model.glb", edits,
                                                  expected_sha256=current["model_sha256"])
            except ValueError as exc:
                raise JobError(str(exc)) from None
            atomic_json(target / "settings.json", {"viewer": viewer, "receipt": receipt, "created_at": now()})
            changed = receipt.get("source_blend_matches_revision") is not True
            current.update(revision=revision, viewer=viewer, model_sha256=receipt["model_sha256"],
                           model_url=f"/api/jobs/{job_id}/model.glb?revision={revision}",
                           scene_matches_revision=False if changed else current.get("scene_matches_revision"),
                           glb_materials_edited=bool(current.get("glb_materials_edited") or changed),
                           source_match_status="material_changes" if changed else current.get("source_match_status", "unverified"))
            job["artifacts"]["model"] = str((target / "model.glb").relative_to(directory))
            job["result"] = current
            self.store.save(job)
            return {"job": self.public_job(job_id), "materials": self.materials(job_id),
                    "receipt": receipt}

    def public_job(self, job_id):
        job = self.store.get(job_id)
        if job.get("result"):
            result = job["result"]
            query = {"model": f"http://127.0.0.1:{self.port}" + result["model_url"], "name": job["name"],
                     "sha256": result["model_sha256"], "lensenv": result.get("viewer", {}).get("lens_reflection", 1)}
            # Native exports use metres. Inspect geometry for width, rather than a guessed product label.
            try:
                from bsa.archeck import front_width_mm
                query["width"] = round(front_width_mm(self.store.artifact(job_id, "model.glb")), 2)
            except (ValueError, OSError, KeyError):
                query["width"] = 145
            query["clip"] = -.14
            result["ar_url"] = self.store.ar_origin + "/?" + urlencode(query)
            result["viewer_url"] = self.store.ar_origin + "/studio-viewer.html?" + urlencode(query)
        return job

    def ensure_ar(self):
        """Start current source via Vite; never serve/publish the frozen production build."""
        with self.ar_lock:
            try:
                response = httpx.get(self.store.ar_origin + "/src/render/renderer.ts", timeout=3)
                if response.status_code == 200 and "TryOnRenderer" in response.text:
                    return {"ready": True, "origin": self.store.ar_origin, "started": False}
            except httpx.HTTPError:
                pass
            vite = self.ar_root / "node_modules/vite/bin/vite.js"
            node = shutil.which("node")
            if not vite.is_file() or not node:
                raise JobError("AR dependencies are missing; install dependencies in ar/ first", 409)
            if self.ar_process and self.ar_process.poll() is None:
                return {"ready": False, "origin": self.store.ar_origin, "started": True}
            port = urlsplit(self.store.ar_origin).port
            with (self.store.root / "ar-dev.log").open("ab") as log:
                self.ar_process = subprocess.Popen([node, str(vite), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
                                                  cwd=self.ar_root, stdout=log, stderr=log,
                                                  env=clean_process_env(), **process_options())
            return {"ready": False, "origin": self.store.ar_origin, "started": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalEyewearStudio/1"

    @property
    def studio(self):
        return self.server.studio

    def log_message(self, *_):
        # URLs/user inputs/exception bodies are intentionally not logged.
        pass

    def guards(self, mutation=False):
        allowed_hosts = {f"127.0.0.1:{self.studio.port}", f"localhost:{self.studio.port}"}
        if self.headers.get("Host") not in allowed_hosts:
            raise JobError("Unrecognized local Host", 403)
        origin = self.headers.get("Origin")
        path = urlsplit(self.path).path
        ar_asset = bool(re.fullmatch(r"/api/jobs/[0-9a-f]{32}/model\.glb", path))
        if origin and origin not in self.studio.origins and not (ar_asset and not mutation and origin == self.studio.store.ar_origin):
            raise JobError("Unrecognized request origin", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not (ar_asset and origin == self.studio.store.ar_origin):
            raise JobError("Cross-site requests are disabled", 403)
        if mutation and not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.studio.csrf_token):
            raise JobError("Invalid CSRF token", 403)

    def headers_common(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        if self.headers.get("Origin") == self.studio.store.ar_origin and self.path.split("?")[0].endswith("/model.glb"):
            self.send_header("Access-Control-Allow-Origin", self.studio.store.ar_origin)
            self.send_header("Vary", "Origin")

    def reply(self, value, status=200):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.headers_common()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def file(self, path, mime=None):
        if not path.is_file():
            raise JobError("File not available", 404)
        self.send_response(200)
        self.headers_common()
        self.send_header("Content-Type", mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        if path.suffix in {".blend", ".glb"}:
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

    def body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise JobError("Expected application/json", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise JobError("Invalid body length") from None
        if length < 0 or length > 60 * 1024 * 1024:
            raise JobError("Request body exceeds 60 MiB", 413)
        try:
            value = json.loads(self.rfile.read(length) or b"{}", parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise JobError("Invalid JSON") from None
        if not isinstance(value, dict):
            raise JobError("Expected a JSON object")
        return value

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def do_PATCH(self):
        self.dispatch(True)

    def do_OPTIONS(self):
        try:
            self.guards()
            if self.headers.get("Origin") != self.studio.store.ar_origin or not self.path.split("?")[0].endswith("/model.glb"):
                raise JobError("Cross-origin API access is disabled", 403)
            self.send_response(204)
            self.headers_common()
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Range")
            self.end_headers()
        except JobError as error:
            self.reply({"error": str(error)}, error.status)

    def dispatch(self, mutation):
        try:
            self.guards(mutation)
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            parts = path.strip("/").split("/")
            payload = self.body() if mutation else {}
            if path == "/api/config" and not mutation:
                return self.reply(self.studio.config())
            if path == "/api/ar/start" and self.command == "POST":
                return self.reply(self.studio.ensure_ar())
            if path == "/api/jobs":
                if self.command == "GET":
                    return self.reply({"jobs": [self.studio.public_job(j["id"]) for j in self.studio.store.list()]})
                if self.command == "POST":
                    return self.reply(self.studio.store.create(payload), 201)
            if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
                job_id = parts[2]
                self.studio.store.metadata(job_id)
                if len(parts) == 3:
                    if self.command == "GET":
                        return self.reply(self.studio.public_job(job_id))
                    if self.command == "PATCH":
                        return self.reply(self.studio.store.update(job_id, payload))
                action = parts[3] if len(parts) == 4 else None
                if self.command == "GET" and action in {"model.glb", "scene.blend"}:
                    revision = parse_qs(parsed.query).get("revision", [None])[0]
                    return self.file(self.studio.store.artifact(job_id, action, revision), "model/gltf-binary" if action.endswith("glb") else "application/octet-stream")
                if self.command == "GET" and len(parts) == 5 and parts[3] == "images":
                    job = self.studio.store.metadata(job_id)
                    if parts[4] not in {p["name"] for p in job["images"]}:
                        raise JobError("Unknown image", 404)
                    return self.file(bounded_path(self.studio.store.directory(job_id), "images/" + parts[4]))
                if action == "materials":
                    return self.reply(self.studio.materials(job_id) if self.command == "GET"
                                      else self.studio.change_materials(job_id, payload))
                if self.command == "POST":
                    if action in {"start", "resume"}:
                        return self.reply(self.studio.store.start(job_id, resume=action == "resume"), 202)
                    if action == "stop":
                        return self.reply(self.studio.store.stop(job_id), 202)
                    if action == "specifics":
                        return self.reply(self.studio.specifics(job_id))
            if not mutation and not path.startswith("/api/"):
                relative = "index.html" if path == "/" else path.lstrip("/")
                if Path(relative).suffix not in {".html", ".js", ".css", ".svg", ".png", ".ico"}:
                    raise JobError("Unknown web asset", 404)
                return self.file(bounded_path(self.studio.web_root, relative))
            raise JobError("Unknown endpoint", 404)
        except JobError as error:
            self.reply({"error": str(error)}, error.status)
        except (ValueError, RuntimeError) as error:
            # These modules raise bounded validation errors; provider responses never reach this path verbatim.
            self.reply({"error": str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply({"error": "Local operation failed; inspect host evidence. No automatic paid retry."}, 500)


def create_server(studio: Studio, port=None):
    server = ThreadingHTTPServer(("127.0.0.1", studio.port if port is None else port), Handler)
    studio.port = server.server_address[1]
    server.studio = studio
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--data", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--env", type=Path, default=AUTOMATION / ".env")
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--gemini-model", default=studio_description.DEFAULT_MODEL)
    parser.add_argument("--import-trial", type=Path, help="CLI-only copy of completed delivery; cannot resume its paid history")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--start-ar", action="store_true", help="Start current AR source server, with no paid calls")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a loopback port from 1024 through 65535")
    store = JobStore(args.data, env_file=args.env, blender=args.blender)
    if args.import_trial:
        job = store.import_completed(args.import_trial)
        print("Imported job " + job["id"], flush=True)
    studio = Studio(store, port=args.port, gemini_model=args.gemini_model)
    server = create_server(studio)
    if args.start_ar:
        try:
            studio.ensure_ar()
        except JobError as error:
            print(str(error), flush=True)
    url = f"http://127.0.0.1:{studio.port}/"
    print("Local studio: " + url + " (no paid model requests until an explicit action)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for job in store.list():
            if job["status"] in {"starting", "running"}:
                store.stop(job["id"])
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
