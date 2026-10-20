"""Actual exporter regressions on synthetic cubes, never a product or paid model call."""
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import coating, glb_patch, lib
from glasses3d.g3d.export_glb import export_glb, snapshot

OUT = Path(sys.argv[sys.argv.index("--") + 1])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"cases": {}, "preserved": {}, "refusals": {}}
PARAMS = dict(factor=1., ior=1.38, thickness_min_nm=140., thickness_max_nm=460., tint=(.12, .55, .4))


def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = .001
    sc.unit_settings.length_unit = "MILLIMETERS"
    sc["blender_agent_axes"] = "+Y up; +Z front"
    sc["mdl_bridge_underside"] = [0., 0., 0.]
    bpy.ops.mesh.primitive_cube_add(size=20)
    bpy.context.object["partRole"] = "lens"
    mat = bpy.data.materials.new("Synthetic lens")
    mat.use_nodes = True
    bpy.context.object.data.materials.append(mat)
    p = mat.node_tree.nodes.get("Principled BSDF")
    return mat, p


def group(mat, factor_name=None, occlusion=False):
    tree = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
    sockets = [("Iridescence Thickness Minimum", 140.)]
    if factor_name:
        sockets.append((factor_name, 1.))
    if occlusion:
        sockets.append(("Occlusion", 1.))
    for name, value in sockets:
        tree.interface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketFloat").default_value = value
    n = mat.node_tree.nodes.new("ShaderNodeGroup")
    n.node_tree = tree
    return n


def export(name):
    path = OUT / f"{name}.glb"
    receipt = export_glb(path)
    doc, _ = glb_patch.read_chunks(path.read_bytes())
    REPORT["cases"][name] = {"receipt": receipt, "materials": doc.get("materials", []),
                             "extensions_used": doc.get("extensionsUsed", [])}
    return REPORT["cases"][name]


for factor_name, occlusion, name in (("Iridescence", False, "wrong_both"),
                                    ("Iridescence Factor", False, "factor_only"),
                                    ("Iridescence Factor", True, "correct_both"),
                                    ("Iridescence", True, "occlusion_only")):
    mat, p = scene()
    p.inputs["Thin Film Thickness"].default_value = 460.
    p.inputs["Thin Film IOR"].default_value = 1.38
    p.inputs["Transmission Weight"].default_value = .76
    group(mat, factor_name, occlusion)
    export(name)

mat, p = scene()
p.inputs["Thin Film Thickness"].default_value = 400.
p.inputs["Thin Film IOR"].default_value = 1.3
export("missing_extension_at_numeric_defaults")

mat, p = scene()
p.inputs["Transmission Weight"].default_value = .76
p.inputs["IOR"].default_value = 1.46
p.inputs["Alpha"].default_value = .8
facade, errors = lib.build()
assert not errors, errors
REPORT["helper"] = facade.lens_coating(mat, **PARAMS)
REPORT["helper_repeat"] = facade.lens_coating(mat.name, **PARAMS)
REPORT["preserved"]["other_principled"] = [p.inputs[n].default_value for n in ("Transmission Weight", "IOR", "Alpha")]
REPORT["preserved"]["group_count"] = len([n for n in mat.node_tree.nodes if n.type == "GROUP"])
REPORT["preserved"]["native_thickness"] = p.inputs["Thin Film Thickness"].default_value
export("helper")
facade.lens_coating(mat, **{**PARAMS, "factor": 0})
export("factor_zero")
REPORT["preserved"]["native_zero"] = p.inputs["Thin Film Thickness"].default_value

# A white "coating tint" replaces an existing coloured body. Report that target explicitly.
mat, p = scene()
p.inputs["Base Color"].default_value = (.13, .47, .71, .37)
for name, value in (("Metallic", .67), ("Transmission Weight", .83), ("IOR", 1.61),
                    ("Alpha", .79), ("Roughness", .23)):
    p.inputs[name].default_value = value
REPORT["white_tint"] = {"before_base_color": list(p.inputs["Base Color"].default_value)}
REPORT["white_tint"]["helper"] = facade.lens_coating(mat, **{**PARAMS, "tint": (1., 1., 1.)})
REPORT["white_tint"]["after_base_color"] = list(p.inputs["Base Color"].default_value)
REPORT["white_tint"]["preserved_inputs"] = {
    name: p.inputs[name].default_value for name in ("Metallic", "Transmission Weight", "IOR", "Alpha", "Roughness")}
export("white_tint")

# Live workers evaluate and checkpoint an existing scene between edits. Exercise repeated exports on shared data.
mat, p = scene()
first = bpy.context.object
second = first.copy()
bpy.context.scene.collection.objects.link(second)
bpy.context.view_layer.update()
facade.lens_coating(mat, **{**PARAMS, "factor": 0})
export("live_uncoated")
facade.lens_coating(mat, **PARAMS)
REPORT["live_before_export"] = {"original": coating.coating_range(mat),
    "evaluated": coating.coating_range(first.evaluated_get(bpy.context.evaluated_depsgraph_get()).material_slots[0].material)}
export("live_coated")

for node_type, output_name, name in (("ShaderNodeValue", "Value", "linked_transmission"),
                                     ("ShaderNodeRGB", "Color", "rgb_transmission")):
    mat, p = scene()
    group(mat, occlusion=True)
    n = mat.node_tree.nodes.new(node_type)
    if node_type == "ShaderNodeValue":
        n.outputs[0].default_value = .76
    mat.node_tree.links.new(n.outputs[output_name], p.inputs["Transmission Weight"])
    export(name)

mat, p = scene()
unused = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
unused.inputs["Transmission Weight"].default_value = 1.
unused.inputs["Thin Film Thickness"].default_value = 460.
inactive = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
inactive.is_active_output = False
volume = mat.node_tree.nodes.new("ShaderNodeVolumeAbsorption")
mat.node_tree.links.new(volume.outputs[0], inactive.inputs["Volume"])
export("unused_nodes")

mat, p = scene()
p.inputs["Coat Weight"].default_value = .7
p.inputs["Coat IOR"].default_value = 1.8
p.inputs["Subsurface Weight"].default_value = .3
export("unsupported_authored_optics")

mat, p = scene()
control = group(mat, occlusion=True)
noise = mat.node_tree.nodes.new("ShaderNodeTexNoise")
for name in ("Thickness", "Dispersion"):
    control.node_tree.interface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketFloat")
    mat.node_tree.links.new(noise.outputs["Fac"], control.inputs[name])
mat.node_tree.links.new(noise.outputs["Fac"], p.inputs["Specular IOR Level"])
export("unverified_controls")

mat, p = scene()
unused = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
unused.inputs["Thin Film Thickness"].default_value = 460.
unused.inputs["Transmission Weight"].default_value = 1.
mix = mat.node_tree.nodes.new("ShaderNodeMixShader")
mix.inputs[0].default_value = 0.
mat.node_tree.links.new(p.outputs[0], mix.inputs[1])
mat.node_tree.links.new(unused.outputs[0], mix.inputs[2])
mat.node_tree.links.new(mix.outputs[0], mat.node_tree.nodes.get("Material Output").inputs["Surface"])
export("zero_weight_shader_branch")

mat, p = scene()
control = group(mat, occlusion=True)
for name, value in (("Thickness", .0015), ("Dispersion", .06)):
    control.node_tree.interface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketFloat").default_value = 0
    control.inputs[name].default_value = value
ao = mat.node_tree.nodes.new("ShaderNodeValue")
ao.outputs[0].default_value = .4
control.inputs["Occlusion"].default_value = .27
mat.node_tree.links.new(ao.outputs[0], control.inputs["Occlusion"])
volume = mat.node_tree.nodes.new("ShaderNodeVolumeAbsorption")
mat.node_tree.links.new(volume.outputs[0], mat.node_tree.nodes.get("Material Output").inputs["Volume"])
p.inputs["Transmission Weight"].default_value = .76
other = bpy.data.materials.new("Other material with shared control group")
other.use_nodes = True
other_control = other.node_tree.nodes.new("ShaderNodeGroup")
other_control.node_tree = control.node_tree
original = other_control.node_tree
facade.lens_coating(mat, **PARAMS)
REPORT["preserved"].update({"shared_copied": control.node_tree != original,
                           "other_not_extended": "Iridescence Factor" not in other_control.inputs,
                           "ao_link": control.inputs["Occlusion"].links[0].from_socket == ao.outputs[0],
                           "ao_unlinked_default": control.inputs["Occlusion"].default_value,
                           "volume_link": mat.node_tree.nodes.get("Material Output").inputs["Volume"].is_linked,
                           "thickness": control.inputs["Thickness"].default_value,
                           "dispersion": control.inputs["Dispersion"].default_value})
export("preserved_other_optics")

for name in ("Base Color", "Thin Film Thickness", "Thin Film IOR", "mixed_surface", "factor_link", "competing", "range_clamp", "evaluated_material"):
    mat, p = scene()
    if name in p.inputs:
        value = mat.node_tree.nodes.new("ShaderNodeValue")
        mat.node_tree.links.new(value.outputs[0], p.inputs[name])
    elif name == "mixed_surface":
        mix = mat.node_tree.nodes.new("ShaderNodeMixShader")
        mat.node_tree.links.new(p.outputs[0], mix.inputs[1])
        mat.node_tree.links.new(mix.outputs[0], mat.node_tree.nodes.get("Material Output").inputs["Surface"])
    elif name == "factor_link":
        n = group(mat, "Iridescence Factor", True)
        value = mat.node_tree.nodes.new("ShaderNodeValue")
        mat.node_tree.links.new(value.outputs[0], n.inputs["Iridescence Factor"])
    elif name == "competing":
        n = group(mat, "Iridescence Factor", True)
        n.node_tree.name = "Unrelated authored shader control"
    elif name == "range_clamp":
        n = group(mat, "Iridescence Factor", True)
        n.node_tree.interface.items_tree["Iridescence Thickness Minimum"].max_value = 100.
    else:
        mat = mat.evaluated_get(bpy.context.evaluated_depsgraph_get())
    before = snapshot(bpy.context.scene)
    try:
        facade.lens_coating(mat, **PARAMS)
    except ValueError as exc:
        REPORT["refusals"][name] = {"error": str(exc), "unchanged": before == snapshot(bpy.context.scene)}
    else:
        raise AssertionError("expected refusal: " + name)

for name, change in (("factor", {"factor": 2}), ("ior", {"ior": .5}),
                     ("range", {"thickness_min_nm": 900}), ("tint", {"tint": (1, -1, 0)}),
                     ("nan", {"thickness_max_nm": float("nan")})):
    mat, _ = scene()
    before = snapshot(bpy.context.scene)
    try:
        facade.lens_coating(mat, **{**PARAMS, **change})
    except ValueError as exc:
        REPORT["refusals"][name] = {"error": str(exc), "unchanged": before == snapshot(bpy.context.scene)}
    else:
        raise AssertionError("expected invalid parameter refusal: " + name)

(OUT / "report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
print("COATING_PROOF", OUT / "report.json")
