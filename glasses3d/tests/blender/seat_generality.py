"""Task 7 seating controls in real Blender; all scenes are synthetic scratch copies."""
import json
from pathlib import Path
import sys
import traceback

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import seat_bpy as SB, export_glb as EX
from glasses3d.tests.fixtures import seat_fixtures as SF


def snapshot():
    return {"meshes": {m.name: EX._mesh_digest(m) for m in bpy.data.meshes},
            "objects": {o.name: {"mesh": o.data.name if o.type == "MESH" else None,
                                  "props": {k: o[k] for k in o.keys()},
                                  "modifiers": [(m.name, getattr(getattr(m, "object", None), "name", None))
                                                for m in o.modifiers]} for o in bpy.data.objects}}


def pair():
    left = bpy.data.objects["Lens_L"]
    right = left.copy()
    right.data = left.data.copy()
    right.name = "Lens_R"
    right.location.x = 64
    bpy.context.scene.collection.objects.link(right)
    frame = bpy.data.objects["Crystal rim"]
    mesh = SF.crystal_scene()["parts"]["Crystal rim"]
    fused = SF.fused(mesh, SF.translated(mesh, [64, 0, 0]))
    frame.data.clear_geometry()
    frame.data.from_pydata(fused.V.tolist(), [], fused.T.tolist())
    frame.data.update()
    bpy.context.view_layer.update()
    first = SB.seat_lens(left, frame_type="acetate")
    first_mods = [(m.name, m.object.name) for m in frame.modifiers if m.type == "BOOLEAN"]
    second = SB.seat_lens(right, frame_type="acetate")
    both_mods = [(m.name, m.object.name) for m in frame.modifiers if m.type == "BOOLEAN"]
    checked = SB.check_lens_seating()
    SB.unseat_lens(right)
    remaining = [(m.name, m.object.name) for m in frame.modifiers if m.type == "BOOLEAN"]
    return dict(first=first, second=second, first_mods=first_mods, both_mods=both_mods,
                remaining=remaining, check=checked)


def nonintersecting():
    frame = bpy.data.objects["Crystal rim"]
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-32, 23, 0))
    pad = bpy.context.object
    pad.name = "Clear nearby pad"
    pad["partRole"] = "frame"
    pad.data.materials.append(frame.data.materials[0])
    digest = EX._mesh_digest(pad.data)
    result = SB.seat_lens("Lens_L", frame_type="acetate")
    return {"result": result, "pad_unchanged": EX._mesh_digest(pad.data) == digest and not pad.modifiers}


def rollback(error_kind):
    before = snapshot()
    original = SB.deform.lift_arrays
    count = 0
    def fail_after_write(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise {"key": KeyError, "index": IndexError}[error_kind]("forced post-write lookup failure")
        return original(*args, **kwargs)
    SB.deform.lift_arrays = fail_after_write
    try:
        result = SB.hide_eyewire("Eyewire", ["Brow"], [SF.browline_scene()["range"]], lens="Lens_L")
    finally:
        SB.deform.lift_arrays = original
    return {"result": result, "calls": count, "restored": snapshot() == before}


def caps(kind):
    ob = bpy.data.objects["Lens_L"]
    n = 200
    th = 2 * np.pi * np.arange(n) / n
    ring = np.c_[-32 + 25 * np.cos(th), 19 * np.sin(th)]
    v = np.vstack([np.c_[ring, np.full(n, .8)], np.c_[ring, np.full(n, -.8)]])
    if kind == "ngon":
        faces = [list(range(n)), list(range(2 * n - 1, n - 1, -1))]
    else:
        v = np.vstack([v, [-32, 0, .8], [-32, 0, -.8]])
        faces = [(2 * n, i, (i + 1) % n) for i in range(n)]
        faces += [(2 * n + 1, n + (i + 1) % n, n + i) for i in range(n)]
    faces += [(i, n + i, n + (i + 1) % n, (i + 1) % n) for i in range(n)]
    ob.data.clear_geometry()
    ob.data.from_pydata(v.tolist(), [], faces)
    ob.data.update()
    return SB.seat_lens(ob, frame_type="acetate")


def materials(out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    variants = ["opaque", "transmission", "disconnected", "glass", "unused", "procedural_transmission",
                "value_transmission", "texture_transmission", "alpha", "texture_alpha", "procedural_alpha", "volume"]
    got = {}
    for i, name in enumerate(variants):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
        ob = bpy.context.object
        ob.name = name
        mat = bpy.data.materials.new(name)
        ob.data.materials.append(mat)
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        p = nodes.get("Principled BSDF")
        output = nodes.get("Material Output")
        if name == "transmission":
            p.inputs["Transmission Weight"].default_value = .8
        elif name == "disconnected":
            nodes.new("ShaderNodeBsdfPrincipled").inputs["Transmission Weight"].default_value = .8
        elif name == "glass":
            links.new(nodes.new("ShaderNodeBsdfGlass").outputs[0], output.inputs["Surface"])
        elif name == "unused":
            spare = mat.copy()
            spare.node_tree.nodes.get("Principled BSDF").inputs["Transmission Weight"].default_value = .8
            ob.data.materials.append(spare)
        elif name in ("procedural_transmission", "procedural_alpha"):
            node = nodes.new("ShaderNodeTexNoise")
            links.new(node.outputs["Fac"], p.inputs["Alpha" if name.endswith("alpha") else "Transmission Weight"])
        elif name == "value_transmission":
            node = nodes.new("ShaderNodeValue")
            node.outputs[0].default_value = .8
            links.new(node.outputs[0], p.inputs["Transmission Weight"])
        elif name in ("texture_transmission", "texture_alpha"):
            node = nodes.new("ShaderNodeTexImage")
            node.image = bpy.data.images.new(name, width=2, height=2, alpha=True)
            node.image.pixels = [.6, .6, .6, .4] * 4
            links.new(node.outputs["Alpha" if name.endswith("alpha") else "Color"],
                      p.inputs["Alpha" if name.endswith("alpha") else "Transmission Weight"])
        elif name == "alpha":
            p.inputs["Alpha"].default_value = .5
        elif name == "volume":
            links.new(nodes.new("ShaderNodeVolumeAbsorption").outputs[0], output.inputs["Volume"])
        got[name] = SB.object_translucent(ob)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", export_yup=False)
    return got


def main():
    source, output = [Path(x) for x in sys.argv[sys.argv.index("--") + 1:]]
    results = {}
    cases = [("pair", "crystal", pair), ("near", "crystal", nonintersecting),
             ("rollback_key", "browline", lambda: rollback("key")),
             ("rollback_index", "browline", lambda: rollback("index")),
             ("ngon", "acetate_flat", lambda: caps("ngon")),
             ("fan", "acetate_flat", lambda: caps("fan"))]
    for name, scene, fn in cases:
        bpy.ops.wm.open_mainfile(filepath=str(source / f"{scene}.blend"))
        try:
            results[name] = fn()
        except Exception:
            results[name] = {"error": traceback.format_exc()}
    try:
        results["materials"] = materials(output.with_suffix(".glb"))
    except Exception:
        results["materials"] = {"error": traceback.format_exc()}
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")


main()
