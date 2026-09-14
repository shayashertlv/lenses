"""Native worker. Editor scripts run only in memory; this host owns all IO."""
from __future__ import annotations

import array
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

import bpy
import bmesh
import mathutils
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def meshes():
    return sorted([o for o in bpy.context.scene.objects if o.type == "MESH"], key=lambda o: o.name)


def lens(o):
    return o.get("auto_role") in ("lens_left", "lens_right")


def geometry_record(o):
    me = o.data
    coords = array.array("f", [0]) * (len(me.vertices) * 3)
    me.vertices.foreach_get("co", coords)
    loops = array.array("i", [0]) * len(me.loops)
    me.loops.foreach_get("vertex_index", loops)
    starts = array.array("i", [0]) * len(me.polygons)
    counts = array.array("i", [0]) * len(me.polygons)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", counts)
    h = hashlib.sha256()
    for values in (coords, loops, starts, counts):
        h.update(values.tobytes())
    h.update(json.dumps([list(row) for row in o.matrix_world]).encode())
    h.update(json.dumps([o.hide_render, o.hide_viewport, o.hide_get()]).encode())
    h.update(json.dumps(sorted((c.name, c.hide_render, c.hide_viewport) for c in o.users_collection)).encode())
    for layer in me.uv_layers:
        uv = array.array("f", [0]) * (len(layer.data) * 2)
        layer.data.foreach_get("uv", uv)
        h.update(layer.name.encode()); h.update(uv.tobytes())
    for a in ("sharp_edge", "sharp_face"):
        if a in me.attributes:
            vals = [bool(x.value) for x in me.attributes[a].data]
            h.update(a.encode()); h.update(bytes(vals))
    normal = array.array("f", [0]) * (len(me.corner_normals) * 3)
    me.corner_normals.foreach_get("vector", normal)
    h.update(normal.tobytes())
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = o.evaluated_get(deps).to_mesh()
    try:
        ec = array.array("f", [0]) * (len(evaluated.vertices) * 3)
        evaluated.vertices.foreach_get("co", ec)
        ei = array.array("i", [0]) * len(evaluated.loops)
        evaluated.loops.foreach_get("vertex_index", ei)
        h.update(ec.tobytes()); h.update(ei.tobytes())
    finally:
        o.evaluated_get(deps).to_mesh_clear()
    topo = hashlib.sha256(loops.tobytes() + starts.tobytes() + counts.tobytes()).hexdigest()
    return {"hash": h.hexdigest(), "coords": coords, "topology": topo,
            "matrix": tuple(v for row in o.matrix_world for v in row),
            "vertices": len(me.vertices), "polygons": len(me.polygons),
            "hide": (o.hide_render, o.hide_viewport, o.hide_get()),
            "collections": tuple(sorted((c.name, c.hide_render, c.hide_viewport) for c in o.users_collection)),
            "role": o.get("auto_role", "")}


def geometry_snapshot():
    return {o.name: geometry_record(o) for o in meshes()}


def scene_digest(snapshot):
    return hashlib.sha256(json.dumps({k: v["hash"] for k, v in snapshot.items()}, sort_keys=True).encode()).hexdigest()


def rna_values(value):
    """Host-only snapshot of editable scalar configuration, excluding UI selection."""
    result = {}
    for prop in value.bl_rna.properties:
        if prop.is_readonly or prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            continue
        if prop.identifier in {"name", "name_full", "select", "show_expanded", "location", "width", "height"}:
            continue
        try:
            item = getattr(value, prop.identifier)
            if prop.is_array:
                item = list(item)
            if isinstance(item, set):
                item = sorted(item)
            if item is None or isinstance(item, (bool, int, float, str, list)):
                result[prop.identifier] = item
        except (AttributeError, TypeError, RuntimeError):
            continue
    return result


def node_tree_state(tree):
    if tree is None:
        return None
    return {
        "nodes": {n.name: {"type": n.bl_idname, "values": rna_values(n),
                            "inputs": [{"name": s.name, "values": rna_values(s)} for s in n.inputs]}
                  for n in tree.nodes},
        "links": sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in tree.links),
    }


def protected_scene_state():
    scene = bpy.context.scene
    return {
        "current_scene": scene.name,
        "scenes": {s.name: {"values": rna_values(s), "render": rna_values(s.render),
                            "camera": s.camera.name if s.camera else None,
                            "world": s.world.name if s.world else None,
                            "root_children": sorted(c.name for c in s.collection.children),
                            "compositor": node_tree_state(getattr(s, "node_tree", None))}
                   for s in bpy.data.scenes},
        "worlds": {w.name: {"values": rna_values(w), "nodes": node_tree_state(w.node_tree)} for w in bpy.data.worlds},
        "collections": {c.name: {"hide_render": c.hide_render, "hide_viewport": c.hide_viewport,
                                  "children": sorted(x.name for x in c.children)} for c in bpy.data.collections},
        "other_objects": {o.name: {"type": o.type, "matrix": [list(row) for row in o.matrix_world],
                                    "values": rna_values(o), "data": rna_values(o.data) if o.data else None,
                                    "parent": o.parent.name if o.parent else None,
                                    "collections": sorted(c.name for c in o.users_collection)}
                          for o in scene.objects if o.type != "MESH"},
    }


def enforce_scene_state(before):
    if before != protected_scene_state():
        raise ValueError("Editing may not change scenes, cameras, lights, world, render settings or collection visibility")


def bounds():
    points = [o.matrix_world @ Vector(c) for o in meshes() for c in o.bound_box]
    if not points:
        raise ValueError("Model has no mesh geometry")
    lo = Vector([min(p[i] for p in points) for i in range(3)])
    hi = Vector([max(p[i] for p in points) for i in range(3)])
    if not all(math.isfinite(x) for p in points for x in p) or max(hi - lo) <= 1e-9:
        raise ValueError("Model has invalid or empty bounds")
    return lo, hi


def static_check(require_lenses=False):
    total_vertices = 0
    role_counts = {"lens_left": 0, "lens_right": 0}
    for o in meshes():
        if not o.data.vertices or not o.data.polygons:
            raise ValueError(f"Empty mesh: {o.name}")
        total_vertices += len(o.data.vertices)
        for v in o.data.vertices:
            if not all(math.isfinite(x) for x in v.co):
                raise ValueError(f"Non-finite mesh coordinates: {o.name}")
        if lens(o):
            role_counts[o["auto_role"]] += 1
            if require_lenses and (o.hide_render or not o.visible_get()):
                raise ValueError(f"{o.name} must remain visible")
            bm = bmesh.new(); bm.from_mesh(o.data)
            try:
                if any(not e.is_manifold for e in bm.edges):
                    raise ValueError(f"{o.name} must be a closed solid lens")
                if abs(bm.calc_volume(signed=True)) <= 1e-16:
                    raise ValueError(f"{o.name} has no solid volume")
            finally:
                bm.free()
    if total_vertices > 10_000_000:
        raise ValueError("Model exceeds the configured 10 million vertex native memory guard")
    if require_lenses and any(v != 1 for v in role_counts.values()):
        raise ValueError("Lens edit must retain exactly one closed object tagged auto_role lens_left and lens_right")
    bounds()


def enforce_scope(before, stage, extent):
    after = geometry_snapshot()
    if stage == "finish":
        if scene_digest(before) != scene_digest(after):
            raise ValueError("Material edit changed geometry, UVs, normals, transforms or visibility")
        return {"scope": "materials_only", "geometry_unchanged": True}
    if stage not in {"lenses", "connections"}:
        raise ValueError("Unknown edit stage")
    for name, old in before.items():
        if old["role"] in {"lens_left", "lens_right"}:
            continue
        if name not in after:
            raise ValueError(f"Protected source object was removed or renamed: {name}")
        new = after[name]
        if new["role"] in {"lens_left", "lens_right"}:
            raise ValueError("Do not reclassify original source meshes as editable lenses; create separate lens objects")
        if stage == "connections":
            if old["hash"] != new["hash"]:
                raise ValueError(f"Lens connection edit changed protected source geometry: {name}")
        else:
            if (old["vertices"] != new["vertices"] or old["topology"] != new["topology"]
                    or old["matrix"] != new["matrix"] or old["hide"] != new["hide"]
                    or old["collections"] != new["collections"]):
                raise ValueError(f"Lens/smoothing edit rebuilt, transformed or hid source geometry: {name}")
            linear = bpy.data.objects[name].matrix_world.to_3x3()
            maximum = max(((linear @ (Vector(old["coords"][i:i+3]) - Vector(new["coords"][i:i+3]))).length
                           for i in range(0, len(old["coords"]), 3)), default=0)
            if maximum > extent * 0.003 + 1e-8:
                raise ValueError(f"Smoothing moved source vertices beyond 0.3% of model extent: {name}")
            # New evaluated modifiers cannot reconstruct the source behind unchanged control vertices.
            obj = bpy.data.objects[name]
            if obj.modifiers:
                raise ValueError("Apply bounded smoothing directly; source geometry modifiers are not permitted")
    added = set(after) - set(before)
    if any(after[name]["role"] not in {"lens_left", "lens_right"} for name in added):
        raise ValueError("Only new lens geometry is permitted")
    static_check(require_lenses=True)
    return {"scope": stage, "protected_source_topology": True,
            "max_source_displacement_fraction": 0.003 if stage == "lenses" else 0}


def inspect(scope=None):
    lo, hi = bounds()
    snapshot = geometry_snapshot()
    objects = []
    for o in meshes():
        objects.append({"name": o.name, "vertices": len(o.data.vertices), "faces": len(o.data.polygons),
                        "auto_role": o.get("auto_role"), "materials": [m.name if m else None for m in o.data.materials],
                        "bounds": [[min((o.matrix_world @ Vector(c))[i] for c in o.bound_box) for i in range(3)],
                                   [max((o.matrix_world @ Vector(c))[i] for c in o.bound_box) for i in range(3)]]})
    warnings = ["Native checks verify software constraints; lens fit, shape and appearance require visual review."]
    images = []
    for im in bpy.data.images:
        if im.type in {"RENDER_RESULT", "COMPOSITING"}:
            continue
        packed = list(im.packed_files)
        images.append({"name": im.name, "size": list(im.size), "channels": im.channels,
                       "packed_sha256": [hashlib.sha256(item.packed_file.data).hexdigest() for item in packed]})
    return {"objects": objects, "materials": [{"name": m.name, "nodes": bool(m.use_nodes)} for m in bpy.data.materials],
            "images": images,
            "bounds": {"min": list(lo), "max": list(hi), "extent": list(hi-lo), "units": "meters"},
            "geometry_sha256": scene_digest(snapshot), "scope_check": scope, "warnings": warnings,
            "coordinate_system": "Blender world coordinates: Z up; front proof camera is on negative Y."}


def setup_render(resolution, samples):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.render.resolution_x = resolution; scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("Auto preview world")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.12, 0.12, 0.12, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.7
    scene.view_settings.view_transform = "AgX"
    lo, hi = bounds(); center = (lo + hi) * 0.5; size = max(hi-lo)
    for name, direction, strength in (("key", (1,-2,2), 12), ("fill",(-2,-1,1),8), ("rim",(0,2,2),14)):
        light = bpy.data.lights.new("Auto " + name, "AREA"); light.energy = strength * size * size * 25
        light.shape = "DISK"; light.size = size * 2
        obj = bpy.data.objects.new(light.name, light); scene.collection.objects.link(obj)
        obj.location = center + Vector(direction) * size
        obj.rotation_euler = (center-obj.location).to_track_quat("-Z", "Y").to_euler()
    cam_data = bpy.data.cameras.new("Auto preview camera")
    camera = bpy.data.objects.new(cam_data.name, cam_data); scene.collection.objects.link(camera)
    scene.camera = camera; cam_data.type = "ORTHO"; cam_data.clip_start = size * .0001; cam_data.clip_end = size * 100
    return camera, center, size


def render_proofs(output, resolution, samples):
    camera, center, size = setup_render(resolution, samples)
    proofs = {}
    views = {"front": (0,-1,0), "back":(0,1,0), "left":(-1,0,0), "right":(1,0,0), "angled":(1,-1,.45)}
    for name, direction in views.items():
        camera.location = center + Vector(direction).normalized() * size * 3
        camera.rotation_euler = (center-camera.location).to_track_quat("-Z","Y").to_euler()
        bpy.context.view_layer.update()
        inverse = camera.matrix_world.inverted()
        camera_points = [inverse @ (o.matrix_world @ Vector(c)) for o in meshes() for c in o.bound_box]
        camera.data.ortho_scale = max(abs(p[i]) for p in camera_points for i in (0, 1)) * 2.16
        path = output / f"{name}.png"; bpy.context.scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True); proofs[name] = str(path)
    tagged = [o for o in meshes() if lens(o)]
    closeup = {"target": "front central region", "tagged_lens": False}
    target = center.copy(); scale = size * .45
    if tagged:
        obj = sorted(tagged, key=lambda o: o.name)[0]
        pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        low = Vector([min(p[i] for p in pts) for i in range(3)])
        high = Vector([max(p[i] for p in pts) for i in range(3)])
        target = (low+high)*.5
        # View the real lens perimeter and adjacent frame, including seating depth.
        target.x += (high.x-low.x) * (.28 if target.x >= center.x else -.28)
        scale = max(high-low) * .82
        closeup = {"target": obj.name, "tagged_lens": True, "target_world": list(target), "ortho_scale": scale}
    camera.location = target + Vector((.7,-1,.4)).normalized() * size * 2
    camera.rotation_euler = (target-camera.location).to_track_quat("-Z","Y").to_euler()
    camera.data.ortho_scale = scale
    path = output / "lens_connection_closeup.png"; bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    # Preview rig is not part of the deliverable or geometry edit context.
    for obj in list(bpy.context.scene.objects):
        if obj.type in {"LIGHT","CAMERA"} and obj.name.startswith("Auto "):
            bpy.data.objects.remove(obj, do_unlink=True)
    return proofs, str(path), closeup


def main(request):
    output = Path(request["output_dir"])
    source = Path(request["input_path"])
    if not output.resolve().is_relative_to(ROOT) or not source.resolve().is_relative_to(ROOT):
        raise ValueError("Worker paths outside modeling_auto")
    started = time.monotonic()
    phases = []
    def progress(phase):
        record = {'phase': phase, 'elapsed_seconds': round(time.monotonic() - started, 3)}
        phases.append(record)
        pending = output / 'native_progress.tmp'
        pending.write_text(json.dumps({'current': record, 'phases': phases}, indent=2), encoding='utf-8')
        pending.replace(output / 'native_progress.json')
        print('MODELING_AUTO_PHASE ' + json.dumps(record), flush=True)
    progress('load')
    if source.suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source))
    elif source.suffix.lower() == ".glb":
        bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete(use_global=False)
        bpy.ops.import_scene.gltf(filepath=str(source), import_pack_images=True)
    else:
        raise ValueError("Expected .blend or .glb input")
    progress('input_checks')
    static_check()
    scope = None
    if request["action"] == "edit":
        from app.script_validation import validate_script, safe_builtins
        from blender.pixel_access import PixelAccess
        validate_script(request["script"])
        before = geometry_snapshot(); scene_before = protected_scene_state(); lo, hi = bounds()
        pixels = PixelAccess(bpy.types.Image)
        namespace = {"__builtins__": safe_builtins(attribute_getter=pixels.get_attribute,
                                                  attribute_setter=pixels.set_attribute),
                     "bpy": bpy, "bmesh": bmesh,
                     "math": math, "mathutils": mathutils}
        namespace.update(pixels.bindings)
        progress('astra_edit')
        try:
            exec(pixels.compile(request["script"], '<astra-edit>'), namespace, namespace)
        finally:
            receipt = {'script_sha256': hashlib.sha256(request['script'].encode('utf-8')).hexdigest(),
                       'pixels': pixels.stats()}
            (output / 'execution.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
            pixels.close()
            namespace.clear()
        progress('scope_checks')
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.update()
        enforce_scene_state(scene_before)
        scope = enforce_scope(before, request["stage"], max(hi-lo))
        if request["stage"] == "finish":
            for material in bpy.data.materials:
                if material.use_nodes:
                    for node in material.node_tree.nodes:
                        if node.type == "OUTPUT_MATERIAL" and node.inputs.get("Displacement") and node.inputs["Displacement"].is_linked:
                            raise ValueError("Material-only edits cannot add geometry displacement")
    progress('output_checks')
    static_check()
    result_inspection = inspect(scope)
    progress('render_proofs')
    proofs, closeup, closeup_info = render_proofs(output, request["resolution"], request["samples"])
    result_inspection["closeup"] = closeup_info
    # Native importer owns all PBR decoding. Preserve every returned texture image packed in the file.
    progress('pack_save')
    bpy.ops.file.pack_all()
    blend = output / "master.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
    model = output / "model.glb"
    progress('export_glb')
    bpy.ops.export_scene.gltf(filepath=str(model), export_format="GLB", export_apply=True,
                              export_cameras=False, export_lights=False, export_extras=True)
    progress('reopen_checks')
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    reopened = inspect(scope)
    if reopened["geometry_sha256"] != result_inspection["geometry_sha256"]:
        raise ValueError("Packed Blender revision changed geometry on reopen")
    missing = [im.name for im in bpy.data.images if im.source == "FILE" and not im.packed_file and not im.packed_files]
    if missing:
        raise ValueError("Unpacked texture images: " + ", ".join(missing))
    result_inspection["images"] = reopened["images"]
    files = [blend, model, Path(closeup)] + [Path(p) for p in proofs.values()]
    progress('artifact_hashes')
    hashes = {str(p.relative_to(output)).replace("\\", "/"): digest(p) for p in files}
    progress('complete')
    return {"ok": True, "blend_path": str(blend), "model_path": str(model), "proofs": proofs,
            "closeup_path": closeup, "inspection": result_inspection,
            "hashes": hashes}


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    try:
        result = main(request)
    except BaseException as exc:
        traceback.print_exc()
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
    Path(request["output_dir"], "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not result["ok"]:
        raise SystemExit(1)
