"""A material-only wrapper around the real Astra tool registry.

No API client or approval is created. Every trial has its own copied inputs,
checkpoint journal, camera basis, tool images and receipts. Baseline tools and
their text/image transport are unchanged; variants substitute only the look implementation.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import types
import uuid

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "glasses3d/data/lens_lab"
SOURCE_JOB = REPO / "glasses3d/data/jobs/oakley-oo9208-v2-3e057bab"
TASK17_STATUS = REPO / "glasses3d/data/review_20261018/codex/task17_status.txt"
SOURCE_CHECKPOINT = "c0015"
SOURCE_BLEND_SHA = "1808105190ec0695750e501be41229abdc58b84f7bd7a4252b46ffb1f9ce8980"
SOURCE_GLB_SHA = "981314580a6b174546ebac0e42509b03a2e83044e4ae888aa729539277d198fb"
TOOLS = (
    "execute_blender_code", "compare_look_to_photos", "render_views", "read_image",
    "read_report", "save_checkpoint", "restore_checkpoint", "list_checkpoints",
    "scene_summary", "export_glb", "arview_capture", "finish",
)
PROTECTED_KEYS = ("schema", "scenes", "view_layers", "collections", "objects", "meshes", "curves", "other")


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def gltf_document(path):
    import struct
    payload = Path(path).read_bytes()
    if payload[:4] != b"glTF":
        raise ValueError("Expected a GLB")
    length, chunk = struct.unpack_from("<II", payload, 12)
    if chunk != 0x4E4F534A:
        raise ValueError("Export missing glTF JSON chunk")
    return json.loads(payload[20:20 + length])


def glass_material_names(document):
    """Keep nontransmitting laser marks fixed even when historic exports tag them lens."""
    return sorted({m["name"] for m in document.get("materials", [])
                   if m.get("extras", {}).get("partRole") == "lens"
                   and m.get("extensions", {}).get("KHR_materials_transmission", {}).get("transmissionFactor", 0) > 0})


def write_json(path, value):
    from glasses3d.agent.procs import write_json_atomic
    write_json_atomic(Path(path), value)


def ensure_heavy_allowed(status_path=None):
    path = Path(status_path or TASK17_STATUS)
    text = path.read_text(encoding="utf-8")
    if not re.search(r"(?im)^\s*exit(?:\s|:|=|$)", text):
        raise RuntimeError(f"L1 heavy work waits for task 17's exit line: {path}")
    return {"path": str(path), "sha256": sha256(path), "text": text}


def trial_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(DATA.resolve()) or path == DATA.resolve():
        raise ValueError("A lab trial must be a private subdirectory of glasses3d/data/lens_lab")
    return path


@contextmanager
def trial_lock(root):
    """An OS lock releases on crash; no stale lock takeover or concurrent head writes."""
    import msvcrt
    lock = Path(root) / ".trial.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("This lab trial is already running a tool") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def scope_violations(baseline, current, lens_materials, baseline_deps, current_deps):
    """Exact mesh/scene freeze; only lens materials and exclusively owned graph assets vary."""
    errors = [key for key in PROTECTED_KEYS if baseline.get(key) != current.get(key)]
    allowed = set(lens_materials)
    before_mats, after_mats = baseline["materials"], current["materials"]
    if set(before_mats) != set(after_mats):
        errors.append("material membership")
    if any(before_mats[name] != after_mats.get(name) for name in before_mats if name not in allowed):
        errors.append("non-lens material")
    for category in ("node_groups", "images"):
        before, after = baseline[category], current[category]
        permitted = (set(baseline_deps["lens"][category]) | set(current_deps["lens"][category]))
        permitted -= set(baseline_deps["other"][category]) | set(current_deps["other"][category])
        for name in set(before) | set(after):
            if before.get(name) != after.get(name) and name not in permitted:
                errors.append(f"{category}:{name}")
    return errors


def dependency_code(lens_names):
    return """import json
names = set(%r)
def visit(tree, groups, images):
    if tree is None:
        return
    for node in tree.nodes:
        if node.type == 'GROUP' and node.node_tree is not None and node.node_tree.name not in groups:
            groups.add(node.node_tree.name)
            visit(node.node_tree, groups, images)
        if hasattr(node, 'image') and node.image is not None:
            images.add(node.image.name)
result = {}
for kind in ('lens', 'other'):
    groups, images = set(), set()
    for material in bpy.data.materials:
        if (material.name in names) == (kind == 'lens'):
            visit(material.node_tree, groups, images)
    if kind == 'other':
        for world in bpy.data.worlds:
            visit(world.node_tree, groups, images)
        for light in bpy.data.lights:
            visit(light.node_tree, groups, images)
        for scene in bpy.data.scenes:
            visit(getattr(scene, 'node_tree', None), groups, images)
            visit(getattr(scene, 'compositing_node_group', None), groups, images)
        for texture in bpy.data.textures:
            visit(getattr(texture, 'node_tree', None), groups, images)
            if getattr(texture, 'image', None) is not None:
                images.add(texture.image.name)
        # Geometry/compositor groups and orphan graph roots can also share an
        # image with the lens. They are never lens-exclusive dependencies.
        for tree in bpy.data.node_groups:
            if tree.name not in result['lens']['node_groups']:
                groups.add(tree.name)
                visit(tree, groups, images)
        for obj in bpy.data.objects:
            if obj.type == 'EMPTY' and obj.data is not None and hasattr(obj.data, 'pixels'):
                images.add(obj.data.name)
        for camera in bpy.data.cameras:
            for background in camera.background_images:
                if background.image is not None:
                    images.add(background.image.name)
    result[kind] = {'node_groups': sorted(groups), 'images': sorted(images)}
print(json.dumps(result))
""" % list(lens_names)


def neutral_code(lens_names):
    return """for name in %r:
    material = bpy.data.materials[name]
    material.use_nodes = True
    material.node_tree.nodes.clear()
    shader = material.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    output = material.node_tree.nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(shader.outputs['BSDF'], output.inputs['Surface'])
    shader.inputs['Base Color'].default_value = (1., 1., 1., 1.)
    shader.inputs['Metallic'].default_value = 0.
    shader.inputs['Roughness'].default_value = 0.
    shader.inputs['Transmission Weight'].default_value = 1.
    shader.inputs['IOR'].default_value = 1.5
    shader.inputs['Alpha'].default_value = 1.
    shader.inputs['Thin Film Thickness'].default_value = 0.
    material.diffuse_color = (1., 1., 1., 1.)
    material.use_backface_culling = True
    if 'g3d_lens_coating' in material:
        del material['g3d_lens_coating']
""" % list(lens_names)


def material_code(lens_names):
    return """import json
records = []
for name in %r:
    material = bpy.data.materials[name]
    nodes = []
    for node in material.node_tree.nodes:
        values = {}
        for socket in node.inputs:
            if hasattr(socket, 'default_value'):
                value = socket.default_value
                if isinstance(value, (float, int, str, bool)):
                    values[socket.name] = value
                else:
                    try:
                        values[socket.name] = list(value)
                    except TypeError:
                        pass
        nodes.append({'name': node.name, 'type': node.type, 'inputs': values})
    records.append({'name': name, 'diffuse_color': list(material.diffuse_color),
                    'use_backface_culling': material.use_backface_culling, 'nodes': nodes,
                    'links': [[link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name]
                              for link in material.node_tree.links]})
print(json.dumps(records))
""" % list(lens_names)


def _probe(worker, code):
    result = worker.call("exec", {"code": code, "soft_timeout_s": 30}, deadline_s=60)
    if result.get("status") != "ok" or result.get("changed"):
        raise RuntimeError(f"Read-only lab inspection failed: {result}")
    return json.loads(result["stdout"].strip())


def _state(worker):
    return worker.call("fingerprint", {"state": True}, deadline_s=60)["state"]


def worker_process_record(worker, started):
    """Record only process metadata, never worker credentials or its receipt body."""
    return {"started": started, "pid": worker.proc.pid, "argv": worker.proc.args,
            "log": str(worker.dir / "worker.log"), "qos": worker.qos,
            "exit": worker.proc.poll()}


def close_recorded(scene, root, started, label):
    worker = scene.s.worker
    record = worker_process_record(worker, started) if worker and worker.proc else {"started": started}
    scene.s.close()
    record.update(finished=now(), exit=worker.proc.poll() if worker and worker.proc else None, label=label)
    directory = Path(root) / "processes"
    directory.mkdir(exist_ok=True)
    write_json(directory / f"{label}-{uuid.uuid4().hex}.json", record)


def init_trial(trial_dir, source_job=None):
    """Copy the verified accepted checkpoint and frozen basis, then reset only lens materials."""
    root = trial_path(trial_dir)
    heavy = ensure_heavy_allowed()
    if (root / "trial.json").exists() or (root / "job").exists():
        raise FileExistsError("Refusing to initialize over an existing lab trial")
    source = Path(source_job or SOURCE_JOB).resolve()
    # A copied source is acceptable only when it carries the exact accepted fixture.
    blend = source / "checkpoints" / f"{SOURCE_CHECKPOINT}.blend"
    glb = source / "deliverable/model.glb"
    if sha256(blend) != SOURCE_BLEND_SHA or sha256(glb) != SOURCE_GLB_SHA:
        raise ValueError("Source is not run 4's final verified accepted checkpoint and GLB")
    job = json.loads((source / "job.json").read_text(encoding="utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    job_dir = root / "job"
    (job_dir / "inputs").mkdir(parents=True)
    tracked_sources = [source / "job.json", blend, glb]
    # Historic run-4 intake includes agent-added matte/fit hints. They are not
    # owner inputs and are unnecessary with a frozen basis; do not copy them
    # into this lab job. build_brief uses none of these removed fields.
    job = {**job, "images": [{k: v for k, v in row.items() if k not in {"matte_mode", "fit_kinds"}}
                             for row in job["images"]]}
    write_json(job_dir / "job.json", job)
    shutil.copy2(blend, job_dir / "inputs/seed.blend")
    (root / "reference").mkdir()
    shutil.copy2(glb, root / "reference/run4.glb")
    for row in job["images"]:
        for key in ("file", "sent_file"):
            if row.get(key):
                rel = Path(row[key])
                src, dst = (source / rel).resolve(), (job_dir / rel).resolve()
                if not src.is_relative_to(source) or not dst.is_relative_to(job_dir):
                    raise ValueError("Reference photo escaped its job")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                tracked_sources.append(src)
    source_basis = source / "cameras/basis-04"
    shutil.copytree(source_basis, job_dir / "cameras/basis-04")
    tracked_sources.extend(p for p in source_basis.rglob("*") if p.is_file())
    # Paths are rewritten only inside the copied basis. Camera values and masks stay exact.
    for path in (job_dir / "cameras/basis-04").glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        def localize(v):
            if isinstance(v, dict):
                return {k: localize(x) for k, x in v.items()}
            if isinstance(v, list):
                return [localize(x) for x in v]
            if isinstance(v, str):
                return v.replace(str(source), str(job_dir)).replace(source.as_posix(), job_dir.as_posix())
            return v
        write_json(path, localize(value))
    # Basis readers verify the camera JSON hash.
    basis_path = job_dir / "cameras/basis-04/basis.json"
    basis = json.loads(basis_path.read_text(encoding="utf-8"))
    basis["cameras_json_sha256"] = sha256(basis_path.with_name("cameras.json"))
    write_json(basis_path, basis)
    from glasses3d.agent.job_scene import JobScene
    scene = JobScene(job_dir)
    worker_started = now()
    try:
        scene.start()
        initial = _state(scene.s.worker)
        source_native_fingerprint = scene.s.head["fingerprint"]
        # The immutable file SHA above identifies the accepted checkpoint.
        # Recompute its content fingerprint using today's implementation;
        # historical fingerprint algorithms are not assumed identical.
        summary = scene.s.worker.call("scene_summary", {}, deadline_s=60)
        roles = {}
        for obj in summary["objects"].values():
            for material in obj.get("materials", []):
                if material:
                    roles.setdefault(material, set()).add(obj.get("partRole"))
        lenses = glass_material_names(gltf_document(glb))
        if not lenses or any(roles.get(name) != {"lens"} for name in lenses):
            raise ValueError("Expected lens-only material assignments with no shared frame material")
        result = scene.s.execute(neutral_code(lenses), label="LAB neutral clear-glass starting point")
        if result.get("status") != "ok":
            raise RuntimeError(f"Neutral reset refused: {result}")
        frozen = _state(scene.s.worker)
        if any(initial[key] != frozen[key] for key in PROTECTED_KEYS):
            raise RuntimeError("Neutral material reset changed protected geometry/scene")
        deps = _probe(scene.s.worker, dependency_code(lenses))
        write_json(root / "scope.json", {"state": frozen, "dependencies": deps})
        manifest = {
            "schema": "g3d.lens_lab.trial/1", "lab": True, "product_deliverable": False,
            "created": now(), "job_dir": str(job_dir), "lens_materials": lenses,
            "baseline_checkpoint": scene.s.head["id"], "baseline_fingerprint": scene.s.head["fingerprint"],
            "mesh_fingerprint": canonical_hash(frozen["meshes"]),
            "source_checkpoint": SOURCE_CHECKPOINT, "source_blend_sha256": SOURCE_BLEND_SHA,
            "source_native_fingerprint": source_native_fingerprint,
            "source_glb_sha256": SOURCE_GLB_SHA, "basis": "basis-04", "heavy_gate": heavy,
            "source_files": {str(p): sha256(p) for p in sorted(set(tracked_sources))},
            "tools": list(TOOLS), "api_requests": 0, "api_spend_usd": 0,
            "baseline_description": "LAB neutral clear glass, metallic 0, no coating or tint; not a product recipe",
        }
        from glasses3d.agent.tools import build_tools
        schemas = {t.name: {"description": t.description, "parameters": t.params_json_schema}
                   for t in build_tools(scene) if t.name in TOOLS and t.name != "finish"}
        schemas["finish"] = {"description": "LAB ONLY: export and record final material; terminal, no product delivery.",
                             "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
                                            "additionalProperties": False}}
        write_json(root / "tool-schemas.json", schemas)
        write_json(root / "trial.json", manifest)
        return manifest
    finally:
        close_recorded(scene, root, worker_started, "init-blender")


class LabSession:
    """Reusable worker for one trial; no two callers may mutate a trial simultaneously."""
    def __init__(self, trial_dir):
        self.root = trial_path(trial_dir)
        self.manifest = json.loads((self.root / "trial.json").read_text(encoding="utf-8"))
        job_dir = Path(self.manifest["job_dir"]).resolve()
        if job_dir != (self.root / "job").resolve() or not job_dir.is_relative_to(self.root):
            raise ValueError("The lab job_dir must be this trial's private job directory")
        self.manifest["job_dir"] = str(job_dir)
        self.scope = json.loads((self.root / "scope.json").read_text(encoding="utf-8"))
        self.scene = None

    def __enter__(self):
        ensure_heavy_allowed()
        for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[name] = "1"
        self.lock = trial_lock(self.root)
        self.lock.__enter__()
        try:
            from glasses3d.agent import procs
            procs.opt_out_self()
            from glasses3d.agent.job_scene import JobScene
            self.scene = JobScene(self.manifest["job_dir"])
            self.worker_started = now()
            self.scene.start()
            emitted = self.root / "emitted-images.json"
            self.scene._emitted_images = json.loads(emitted.read_text(encoding="utf-8")) if emitted.exists() else {}
            self.verify()
            original = self.scene.s._mutating
            lab = self
            def guarded(session, *args, **kwargs):
                before = session.head
                result = original(*args, **kwargs)
                try:
                    lab.verify()
                except ValueError as error:
                    rejected = session.head["id"]
                    session.restore_checkpoint(before["id"], call_id=f"lab-scope-{uuid.uuid4().hex}")
                    lab.verify()
                    result = {"status": "refused", "code": "E_LAB_SCOPE", "message": str(error),
                              "checkpoint": session.head["id"], "restored_to": before["id"],
                              "refused_checkpoint": rejected}
                return result
            self.scene.s._mutating = types.MethodType(guarded, self.scene.s)
            original_restore = self.scene.s.restore_checkpoint
            def guarded_restore(session, checkpoint, *args, **kwargs):
                before = session.head
                result = original_restore(checkpoint, *args, **kwargs)
                try:
                    lab.verify()
                except ValueError as error:
                    original_restore(before["id"], call_id=f"lab-restore-refusal-{uuid.uuid4().hex}")
                    lab.verify()
                    result = {"status": "refused", "code": "E_LAB_SCOPE", "message": str(error),
                              "checkpoint": session.head["id"], "restored_to": before["id"]}
                return result
            self.scene.s.restore_checkpoint = types.MethodType(guarded_restore, self.scene.s)
            from .variants import build_variant_tools
            self.registry = {t.name: t for t in build_variant_tools(self.scene, self.manifest.get("variant", "baseline"))
                             if t.name in TOOLS and t.name != "finish"}
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        if self.scene is not None:
            close_recorded(self.scene, self.root, self.worker_started, "tool-blender")
        self.lock.__exit__(*exc)

    def verify(self):
        state = _state(self.scene.s.worker)
        deps = _probe(self.scene.s.worker, dependency_code(self.manifest["lens_materials"]))
        errors = scope_violations(self.scope["state"], state, self.manifest["lens_materials"],
                                  self.scope["dependencies"], deps)
        if errors:
            raise ValueError("Only lens materials may change; protected changes: " + ", ".join(errors))
        return {"mesh_fingerprint": canonical_hash(state["meshes"]), "protected_scene_unchanged": True}

    def call(self, name, arguments=None):
        from agents.tool_context import ToolContext
        from agents.tool import ToolOutputText
        if name not in TOOLS:
            raise ValueError(f"Not a lens-lab tool: {name}")
        args = arguments or {}
        start, started = time.perf_counter(), now()
        cid = f"lab-{name}-{uuid.uuid4().hex}"
        self.verify()
        if (self.root / "finish.json").exists() and name in {
                "execute_blender_code", "save_checkpoint", "restore_checkpoint"}:
            raise ValueError("Lab finish is terminal; initialize a new trial for more material changes")
        if name == "finish":
            if set(args) - {"summary"}:
                raise ValueError("Lab finish takes only summary")
            export = self.scene.seeing.export_head(call_id=cid)
            if export.get("status") != "exported" or not export.get("validate_spec", {}).get("ok"):
                result = {"status": "refused", "lab": True, "code": "E_EXPORT", "export": export}
            else:
                # Keep the final raw glTF material records rather than an inferred optics summary.
                source = Path(self.scene.root) / export["path"]
                doc = gltf_document(source)
                material_rows = [m for m in doc.get("materials", []) if m.get("name") in self.manifest["lens_materials"]]
                result = {"status": "lab_finished", "lab": True, "product_deliverable": False,
                          "summary": str(args.get("summary", "")), "finished": now(),
                          "final_materials": material_rows, "export": {**export, "path": str(source)},
                          "native_materials": _probe(self.scene.s.worker, material_code(self.manifest["lens_materials"])),
                          "scope": self.verify(), "api_requests": 0, "api_spend_usd": 0}
                changed = [p for p, digest in self.manifest["source_files"].items() if sha256(p) != digest]
                if changed:
                    raise ValueError("Protected originals changed: " + ", ".join(changed))
                result["source_files_unchanged"] = len(self.manifest["source_files"])
                write_json(self.root / "finish.json", result)
            output = [ToolOutputText(text=json.dumps(result, ensure_ascii=False))]
        else:
            raw = json.dumps(args, ensure_ascii=False)
            context = ToolContext(context=None, tool_name=name, tool_call_id=cid, tool_arguments=raw)
            output = asyncio.run(self.registry[name].on_invoke_tool(context, raw))
        self.verify()
        write_json(self.root / "emitted-images.json", getattr(self.scene, "_emitted_images", {}))
        texts, images = [], []
        call_dir = self.root / "receipts" / cid
        call_dir.mkdir(parents=True)
        for part in output if isinstance(output, list) else [output]:
            if getattr(part, "type", None) == "text":
                texts.append(part.text)
            elif getattr(part, "type", None) == "image":
                url = part.image_url
                if not isinstance(url, str) or not url.startswith("data:image/"):
                    raise ValueError("Expected the real pipeline's inline image transport")
                header, body = url.split(",", 1)
                suffix = ".png" if "png" in header else ".jpg"
                image = call_dir / f"image-{len(images):03d}{suffix}"
                image.write_bytes(base64.b64decode(body))
                images.append({"path": str(image), "sha256": sha256(image),
                               "transport": "exact pipeline ToolOutputImage bytes", "detail": part.detail})
            elif isinstance(part, str):
                texts.append(part)
        result = {}
        if texts:
            try:
                result = json.JSONDecoder().raw_decode(texts[0])[0]
            except ValueError:
                result = {"text": texts[0]}
        receipt = {"schema": "g3d.lens_lab.tool/1", "lab": True, "call_id": cid, "tool": name,
                   "arguments": args, "started": started, "finished": now(), "seconds": time.perf_counter() - start,
                   "output_text": texts, "images": images, "result": result, "scope": self.verify()}
        write_json(call_dir / "receipt.json", receipt)
        (call_dir / "text.txt").write_text("\n".join(texts), encoding="utf-8")
        with (self.root / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False) + "\n")
        return receipt


def call_tool(trial_dir, name, arguments=None):
    with LabSession(trial_dir) as lab:
        return lab.call(name, arguments)
