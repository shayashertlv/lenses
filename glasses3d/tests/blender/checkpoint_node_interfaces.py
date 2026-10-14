"""Synthetic interface-authoring fingerprints and Blender save/reload stability; no product scene."""
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import checkpoint as CP, coating, glb_patch
from glasses3d.g3d.export_glb import _plain, _rna_simple, _tree_state, export_glb

out = Path(sys.argv[sys.argv.index("--") + 1])
out.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = .001
scene["mdl_bridge_underside"] = [0., 0., 0.]
scene["g3d_axes"] = "+Y up; +Z front"
bpy.ops.mesh.primitive_cube_add(size=10)
bpy.context.object["partRole"] = "lens"
mat = bpy.data.materials.new("Synthetic coating")
mat.use_nodes = True
bpy.context.object.data.materials.append(mat)
tree = mat.node_tree
legacy = (tree.name,
          tuple((n.name, n.bl_idname, _rna_simple(n),
                 tuple(_plain(getattr(s, "default_value", None)) for s in n.inputs)) for n in tree.nodes),
          tuple(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                       for l in tree.links)))
report = {"legacy_plain_tree_unchanged": _tree_state(tree, set()) == legacy, "changes": {}}
coating.lens_coating(mat, factor=1., ior=1.38, thickness_min_nm=140., thickness_max_nm=460., tint=(.2, .5, .3))
group = next(n.node_tree for n in tree.nodes if n.type == "GROUP")
group_name = group.name


def export(name):
    path = out / f"{name}.glb"
    receipt = export_glb(path)
    doc, _ = glb_patch.read_chunks(path.read_bytes())
    return {"iridescence": doc["materials"][0].get("extensions", {}).get("KHR_materials_iridescence"),
            "scene_unchanged": receipt["scene_unchanged"]}


def changed(name, operation):
    before = CP.fingerprint()
    operation()
    after = CP.fingerprint()
    report["changes"][name] = before != after


report["correct_export"] = export("correct")
changed("used_socket_name", lambda: setattr(group.interface.items_tree["Iridescence Factor"], "name", "Iridescence"))
report["renamed_export"] = export("renamed")
changed("restored_socket_name", lambda: setattr(group.interface.items_tree["Iridescence"], "name", "Iridescence Factor"))

unused = bpy.data.node_groups.new("Unused controls", "ShaderNodeTree")
socket = unused.interface.new_socket(name="Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
changed("zero_user_socket_name", lambda: setattr(socket, "name", "Iridescence Factor"))
changed("zero_user_socket_default", lambda: setattr(socket, "default_value", .4))
changed("zero_user_socket_type", lambda: setattr(socket, "socket_type", "NodeSocketInt"))
unused.use_fake_user = True
socket = unused.interface.items_tree["Iridescence Factor"]
changed("fake_user_socket_default", lambda: setattr(socket, "default_value", 3))
panel = unused.interface.new_panel(name="Optics")
changed("panel_name", lambda: setattr(panel, "name", "Optical controls"))
changed("interface_parent", lambda: unused.interface.move_to_parent(socket, panel, 0))
unused.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")

saved = CP.save(out / "interface.blend", out)
restored = CP.restore(out / "interface.blend", out, expect_fingerprint=saved["fingerprint"],
                      expect_sha256=saved["sha256"])
report["roundtrip"] = restored
report["restored_export"] = export("restored")
group = bpy.data.node_groups[group_name]
changed("socket_name_after_restore", lambda: setattr(group.interface.items_tree["Iridescence Factor"], "name", "Iridescence"))
(out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("INTERFACE_PROOF", out / "report.json")
