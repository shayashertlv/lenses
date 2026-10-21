"""Synthetic real Blender/export parity; no product geometry or API call."""
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import glb_patch, lib
from glasses3d.g3d.export_glb import export_glb, snapshot

out = Path(sys.argv[sys.argv.index("--") + 1])
out.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = .001
scene.unit_settings.length_unit = "MILLIMETERS"
scene["blender_agent_axes"] = "+Y up; +Z front"
scene["mdl_bridge_underside"] = [0., 0., 0.]
bpy.ops.mesh.primitive_cube_add(size=20)
bpy.context.object["partRole"] = "lens"
material = bpy.data.materials.new("Synthetic lens")
material.use_nodes = True
bpy.context.object.data.materials.append(material)
shader = material.node_tree.nodes.get("Principled BSDF")
shader.inputs["Transmission Weight"].default_value = .7
shader.inputs["IOR"].default_value = 1.6
facade, errors = lib.build()
assert "lens_candidate" not in errors, errors
export_glb(out / "before.glb")
values = dict(tint=[.123456789, .27654321, .73456789], transmission=.87654321, metallic=.213456789,
    roughness=.1423456789, ior=2.3456789, iridescence=.456789, iridescence_ior=1.73456789,
    iridescence_thickness_min=123.456789, iridescence_thickness_max=567.123456789)
cases = [values, {**values, "ior": 0, "iridescence": 0},
         {**values, "iridescence_thickness_min": 700, "iridescence_thickness_max": 0},
         {**values, "metallic": 1, "transmission": 0, "roughness": 1, "ior": 1.5}]
report = {"cases": []}
for index, value in enumerate(cases):
    configured = facade.apply_lens_candidate(material.name, source_double_sided=False, **value)
    before = snapshot(scene)
    path = out / f"after-{index}.glb"
    receipt = export_glb(path)
    assert before == snapshot(scene)
    doc, _ = glb_patch.read_chunks(path.read_bytes())
    report["cases"].append({"values": value, "configured": configured, "receipt": receipt,
                            "materials": doc["materials"], "path": str(path)})
shader.inputs["Roughness"].default_value = .333
receipt = export_glb(out / "stale.glb")
report["stale_receipt"] = receipt
report["transitions"] = []
for opened in (False, True):
    if opened:
        bpy.data.objects.remove(bpy.context.object, do_unlink=True)
        bpy.ops.mesh.primitive_plane_add(size=20)
        bpy.context.object["partRole"] = "lens"
        bpy.context.object.data.materials.append(material)
    for source_transmission, target_transmission in ((0, .8), (.8, 0)):
        shader.inputs["Transmission Weight"].default_value = source_transmission
        material.use_backface_culling = False
        before_path = out / f"transition-{opened}-{source_transmission}-before.glb"
        before_receipt = export_glb(before_path)
        before_doc, _ = glb_patch.read_chunks(before_path.read_bytes())
        source_double = before_doc["materials"][0].get("doubleSided", False)
        facade.apply_lens_candidate(material, source_double_sided=source_double,
            **{**values, "transmission": target_transmission})
        after_path = out / f"transition-{opened}-{source_transmission}-after.glb"
        after_receipt = export_glb(after_path)
        after_doc, _ = glb_patch.read_chunks(after_path.read_bytes())
        report["transitions"].append({"open": opened, "source_transmission": source_transmission,
            "target_transmission": target_transmission, "source_double_sided": source_double,
            "after_double_sided": after_doc["materials"][0].get("doubleSided", False),
            "source_audit": before_receipt["material_sidedness"], "after_receipt": after_receipt})
before = snapshot(scene)
rgb = material.node_tree.nodes.new("ShaderNodeRGB")
material.node_tree.links.new(rgb.outputs[0], shader.inputs["Base Color"])
before = snapshot(scene)
try:
    facade.apply_lens_candidate(material, source_double_sided=False, **values)
except ValueError as error:
    report["linked_refusal"] = str(error)
else:
    raise AssertionError("linked scalar must refuse")
report["linked_unchanged"] = before == snapshot(scene)
(out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
