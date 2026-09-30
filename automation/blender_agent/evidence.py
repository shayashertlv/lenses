"""Local recovery/evidence around upstream MCP calls; never an authoring loop.

Every arbitrary Blender script is saved unchanged and protected by a verified
pre-call save copy. Post-call evidence is best effort, including after errors:
a failed script may have partially edited the scene. Checkpoints are authoritative;
compact fingerprints are change indicators, not geometric/visual quality scores.
Blender remains a native process, not a security sandbox. No telemetry or inference
is performed here, and no inventory is appended to the model's tool response.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import uuid

from agents.mcp import MCPServerStdio


STATE_MARKER = "BLENDER_EVIDENCE_STATE:"
PROTECTED_TOOLS = frozenset({"execute_blender_code", "export_scene"})


def snapshot_code(checkpoint: Path) -> str:
    """MCP-safe read-only inventory plus a compressed save copy (no active-file change)."""
    return _SNAPSHOT.replace("_CHECKPOINT_", repr(str(checkpoint.resolve())))


def _json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dump(result):
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else vars(result)


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def state_delta(before: dict, after: dict) -> dict:
    """Identity is the object name; a rename is reported as removal/addition."""
    result = {}
    for section in ("objects", "materials", "node_groups"):
        old, new = ({row["name"]: row for row in state.get(section, [])} for state in (before, after))
        result[section] = {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
                           "changed": {name: sorted(key for key in set(old[name]) | set(new[name])
                                                    if old[name].get(key) != new[name].get(key))
                                       for name in sorted(old.keys() & new.keys()) if old[name] != new[name]}}
    result["scene_changed_fields"] = sorted(key for key in set(before.get("scene", {})) | set(after.get("scene", {}))
                                             if before.get("scene", {}).get(key) != after.get("scene", {}).get(key))
    result["limits"] = "Fingerprints identify changes, not quality. Names identify rows; checkpoints retain omitted data."
    return result


class EvidenceJournal:
    def __init__(self, directory: Path, context_callback=None):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.context_callback = context_callback or (lambda: {})
        self.lock = asyncio.Lock()

    async def _snapshot(self, raw_call, directory: Path, phase: str) -> dict:
        checkpoint = directory / f"{phase}.blend"
        code = snapshot_code(checkpoint)
        (directory / f"{phase}-probe.py").write_text(code, encoding="utf-8")
        result = await asyncio.wait_for(raw_call("execute_blender_code", {
            "code": code, "user_prompt": "Save an automatic recovery copy and read scene evidence without editing the product",
        }), timeout=120)
        _json(directory / f"{phase}-probe-response.json", _dump(result))
        records = [json.loads(line.split(STATE_MARKER, 1)[1]) for line in _text(result).splitlines() if STATE_MARKER in line]
        if getattr(result, "isError", False) or len(records) != 1 or not checkpoint.is_file() or checkpoint.stat().st_size < 12:
            raise RuntimeError(f"Automatic {phase} evidence did not produce a verified checkpoint: {directory}")
        state = records[0]
        if state.get("checkpoint", {}).get("path") != str(checkpoint) or state["checkpoint"].get("identity_preserved") is not True:
            raise RuntimeError(f"Automatic {phase} checkpoint did not preserve Blender's active-file identity")
        state["checkpoint"].update(sha256=_sha256(checkpoint), bytes=checkpoint.stat().st_size)
        _json(directory / f"{phase}.json", state)
        return state

    async def call(self, raw_call, tool_name: str, arguments: dict | None, meta=None):
        if tool_name not in PROTECTED_TOOLS:
            return await raw_call(tool_name, arguments, meta=meta) if meta is not None else await raw_call(tool_name, arguments)
        async with self.lock:
            directory = self.directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f") + "-" + uuid.uuid4().hex[:8])
            directory.mkdir()
            started = time.monotonic()
            record = {"started_at": datetime.now(timezone.utc).isoformat(), "tool_name": tool_name,
                      "context": self.context_callback(), "status": "protecting", "dispatched": False}
            _json(directory / "arguments.json", arguments)
            if isinstance(arguments, dict) and isinstance(arguments.get("code"), str):
                (directory / "issued-code.py").write_text(arguments["code"], encoding="utf-8", newline="")
            _json(directory / "journal.json", record)
            before = None
            primary_error = None
            post_cancel = None
            try:
                before = await self._snapshot(raw_call, directory, "before")
                record.update(status="executing", dispatched=True, pre_seconds=time.monotonic() - started)
                _json(directory / "journal.json", record)
                executing = time.monotonic()
                result = await raw_call(tool_name, arguments, meta=meta) if meta is not None else await raw_call(tool_name, arguments)
                record["execution_seconds"] = time.monotonic() - executing
                _json(directory / "result.json", _dump(result))
                text = _text(result)
                record["status"] = ("tool_error" if getattr(result, "isError", False) or
                                    text.startswith(("Error executing code:", "Rejected by safe mode", "Error exporting scene:"))
                                    else "returned")
                return result
            except BaseException as exc:
                primary_error = exc
                if record["dispatched"]:
                    record["execution_seconds"] = time.monotonic() - executing
                record.update(status="execution_exception" if record["dispatched"] else "blocked_before_execution",
                              error=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                if record["dispatched"]:
                    try:
                        post_started = time.monotonic()
                        after = await self._snapshot(raw_call, directory, "after")
                        record["post_seconds"] = time.monotonic() - post_started
                        _json(directory / "delta.json", state_delta(before, after))
                        record["post_evidence"] = "saved"
                    except BaseException as exc:
                        record["post_evidence"] = f"unavailable: {type(exc).__name__}: {exc}"
                        if isinstance(exc, asyncio.CancelledError) and primary_error is None:
                            post_cancel = exc
                record.update(finished_at=datetime.now(timezone.utc).isoformat(), seconds=time.monotonic() - started)
                # Failure to write final notes must not hide the original tool exception.
                try:
                    _json(directory / "journal.json", record)
                except OSError:
                    pass
                if post_cancel is not None:
                    raise post_cancel


class EvidenceMCPServer(MCPServerStdio):
    """The standard SDK transport with local journaling at its tool-call boundary."""
    def __init__(self, *args, evidence_directory: Path | None = None, evidence_dir: Path | None = None,
                 context_callback=None, **kwargs):
        super().__init__(*args, **kwargs)
        if evidence_directory is not None and evidence_dir is not None:
            raise ValueError("Specify one evidence directory")
        directory = evidence_directory if evidence_directory is not None else evidence_dir
        if directory is None:
            raise ValueError("An evidence directory is required")
        self.evidence_context = {}
        self.evidence = EvidenceJournal(directory, context_callback or (lambda: dict(self.evidence_context)))

    def bind_context(self, tool_call_id=None, response_index=None, parent_tool=None):
        self.evidence_context = {"tool_call_id": tool_call_id, "response_index": response_index, "parent_tool": parent_tool}

    def set_evidence_context(self, call_id=None, interaction=None, parent_tool=None):
        self.bind_context(call_id, interaction, parent_tool)

    async def call_tool(self, tool_name, arguments, meta=None):
        return await self.evidence.call(super().call_tool, tool_name, arguments, meta)


_SNAPSHOT = '''\
import bpy
import json
from array import array

def evidence_snapshot():
    scene = bpy.context.scene
    original_path = bpy.data.filepath
    original_mode = bpy.context.mode
    original_active = bpy.context.view_layer.objects.active
    selected = sorted(obj.name for obj in bpy.context.selected_objects)
    depsgraph = bpy.context.evaluated_depsgraph_get()

    def signature(value):
        return str(hash(json.dumps(value, sort_keys=True, default=str)))

    def named(item):
        return item.name

    def values(collection, field, width=1, kind='f'):
        data = array(kind, [0]) * (len(collection) * width)
        collection.foreach_get(field, data)
        return str(hash(tuple(data)))

    def value(item):
        if isinstance(item, (str, bool, int, float)) or item is None:
            return item
        try:
            return list(item)
        except TypeError:
            return str(item)

    def socket(socket):
        try:
            return value(socket.default_value)
        except AttributeError:
            return None

    def nodes(tree):
        if not tree:
            return None
        rows = []
        for node in tree.nodes:
            row = {'name': node.name, 'type': node.bl_idname, 'mute': node.mute,
                   'inputs': [(s.identifier, socket(s)) for s in node.inputs],
                   'outputs': [(s.identifier, socket(s)) for s in node.outputs],
                   'operation': getattr(node, 'operation', None), 'blend_type': getattr(node, 'blend_type', None),
                   'distribution': getattr(node, 'distribution', None), 'attribute_name': getattr(node, 'attribute_name', None),
                   'layer_name': getattr(node, 'layer_name', None), 'uv_map': getattr(node, 'uv_map', None),
                   'vector_type': getattr(node, 'vector_type', None), 'space': getattr(node, 'space', None),
                   'interpolation': getattr(node, 'interpolation', None), 'extension': getattr(node, 'extension', None)}
            image = getattr(node, 'image', None)
            group = getattr(node, 'node_tree', None)
            ramp = getattr(node, 'color_ramp', None)
            if image:
                row['image'] = (image.name, image.filepath, image.colorspace_settings.name)
            if group:
                row['group'] = group.name
            if ramp:
                row['ramp'] = (ramp.interpolation, [(e.position, list(e.color)) for e in ramp.elements])
            rows.append(row)
        return signature((rows, sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in tree.links)))

    def mesh(mesh):
        attrs = []
        kinds = {'FLOAT': ('value', 1, 'f'), 'INT': ('value', 1, 'i'), 'BOOLEAN': ('value', 1, 'b'),
                 'FLOAT_VECTOR': ('vector', 3, 'f'), 'FLOAT_COLOR': ('color', 4, 'f'),
                 'BYTE_COLOR': ('color', 4, 'f'), 'FLOAT2': ('vector', 2, 'f')}
        for attr in mesh.attributes:
            spec = kinds.get(attr.data_type)
            try:
                digest = values(attr.data, *spec) if spec else 'unsupported_type_in_checkpoint'
            except Exception as error:
                digest = 'unavailable: ' + str(error)[:120]
            attrs.append((attr.name, attr.domain, attr.data_type, digest))
        return {'vertices': len(mesh.vertices), 'edges': len(mesh.edges), 'polygons': len(mesh.polygons),
                'triangles': sum(max(0, p.loop_total - 2) for p in mesh.polygons),
                'positions': values(mesh.vertices, 'co', 3), 'edges_signature': values(mesh.edges, 'vertices', 2, 'i'),
                'topology': signature((values(mesh.loops, 'vertex_index', 1, 'i'), values(mesh.polygons, 'loop_total', 1, 'i'))),
                'material_indices': values(mesh.polygons, 'material_index', 1, 'i'),
                'smooth_faces': values(mesh.polygons, 'use_smooth', 1, 'b'),
                'corner_normals': values(mesh.corner_normals, 'vector', 3),
                'attributes': signature(attrs), 'has_custom_normals': mesh.has_custom_normals}

    objects = []
    all_objects = sorted(scene.objects, key=named)
    for obj in all_objects[:128]:
        row = {'name': obj.name, 'type': obj.type, 'part': obj.get('part'), 'partRole': obj.get('partRole'),
               'transform': [list(v) for v in obj.matrix_world], 'hide_render': obj.hide_render,
               'hide_viewport': obj.hide_viewport, 'hide_get': obj.hide_get(),
               'bounds': [list(v) for v in obj.bound_box],
               'materials': [slot.material.name if slot.material else None for slot in obj.material_slots],
               'modifiers': [(m.name, m.type, m.show_viewport, m.show_render) for m in obj.modifiers]}
        if obj.type == 'MESH':
            row['source_mesh'] = mesh(obj.data)
        if obj.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
            evaluated = obj.evaluated_get(depsgraph)
            try:
                evaluated_mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
                row['evaluated_mesh'] = mesh(evaluated_mesh) if evaluated_mesh else None
            finally:
                evaluated.to_mesh_clear()
        if obj.type == 'CAMERA':
            row['camera'] = (obj.data.type, obj.data.lens, obj.data.ortho_scale, obj.data.shift_x, obj.data.shift_y,
                             obj.data.sensor_width, obj.data.sensor_height, obj.data.clip_start, obj.data.clip_end)
        if obj.type == 'LIGHT':
            row['light'] = (obj.data.type, obj.data.energy, list(obj.data.color), getattr(obj.data, 'size', None),
                           getattr(obj.data, 'size_y', None), getattr(obj.data, 'shape', None),
                           getattr(obj.data, 'shadow_soft_size', None), getattr(obj.data, 'angle', None))
        objects.append(row)
    materials = [{'name': mat.name, 'nodes': nodes(mat.node_tree), 'use_nodes': mat.use_nodes,
                  'diffuse_color': list(mat.diffuse_color), 'roughness': mat.roughness, 'metallic': mat.metallic,
                  'surface_render_method': getattr(mat, 'surface_render_method', None)}
                 for mat in sorted(bpy.data.materials, key=named)[:128]]
    groups = [{'name': group.name, 'nodes': nodes(group)} for group in sorted(bpy.data.node_groups, key=named)[:128]]
    result = {'schema': 1, 'blender_version': bpy.app.version_string,
              'fingerprint_scope': 'Non-cryptographic process-local change indicators; compare only before/after in this process. Checkpoint SHA-256 is authoritative.',
              'limitations': '128 rows per section; external asset bytes are not packed; edit-mode source meshes may be stale; geometry does not certify smoothness or fidelity.',
              'counts': {'objects': len(all_objects), 'meshes': len(bpy.data.meshes), 'materials': len(bpy.data.materials),
                         'node_groups': len(bpy.data.node_groups), 'images': len(bpy.data.images)},
              'objects': objects, 'materials': materials, 'node_groups': groups,
              'scene': {'name': scene.name, 'filepath': original_path, 'mode': original_mode, 'selected': selected,
                        'active': original_active.name if original_active else None, 'frame': scene.frame_current,
                        'unit_scale': scene.unit_settings.scale_length, 'bridge_origin': value(scene.get('mdl_bridge_underside')),
                        'camera': scene.camera.name if scene.camera else None, 'world': scene.world.name if scene.world else None,
                        'world_nodes': nodes(scene.world.node_tree) if scene.world else None,
                        'world_color': list(scene.world.color) if scene.world else None, 'engine': scene.render.engine,
                        'resolution': (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage),
                        'film_transparent': scene.render.film_transparent, 'view_transform': scene.view_settings.view_transform,
                        'look': scene.view_settings.look, 'exposure': scene.view_settings.exposure, 'gamma': scene.view_settings.gamma}}
    saved = bpy.ops.wm.save_as_mainfile(filepath=_CHECKPOINT_, copy=True, check_existing=False, compress=True)
    identity_preserved = (bpy.data.filepath == original_path and bpy.context.mode == original_mode
                          and bpy.context.view_layer.objects.active == original_active
                          and sorted(obj.name for obj in bpy.context.selected_objects) == selected)
    if 'FINISHED' not in saved or not identity_preserved:
        raise RuntimeError('Recovery save did not finish without changing active-file/mode/selection identity')
    result['checkpoint'] = {'path': _CHECKPOINT_, 'identity_preserved': identity_preserved}
    print('BLENDER_EVIDENCE_STATE:' + json.dumps(result, default=str))

evidence_snapshot()
'''
