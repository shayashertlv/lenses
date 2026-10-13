"""Real Blender checkpoint proof; synthetic tool fixture, never product geometry."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import checkpoint as C


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    material = bpy.data.materials.new("Node material")
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = .29
    noise = material.node_tree.nodes.new("ShaderNodeTexNoise")
    material.node_tree.links.new(noise.outputs["Color"], bsdf.inputs["Base Color"])
    group = bpy.data.node_groups.new("Orphan nodes", "ShaderNodeTree")
    group.nodes.new("ShaderNodeValue").outputs[0].default_value = .37

    for name, fake in (("Live text", False), ("Converted text", False), ("Fake text", True), ("Orphan text", False)):
        curve = bpy.data.curves.new(name, "FONT")
        curve.body = "Checkpoint text"
        curve.size = 1.7
        curve.extrude = .01
        curve.materials.append(material)
        curve.use_fake_user = fake
        if name in {"Live text", "Converted text"}:
            obj = bpy.data.objects.new(name, curve)
            bpy.context.scene.collection.objects.link(obj)
            if name == "Converted text":
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                bpy.ops.object.convert(target="MESH")
                obj.select_set(False)
    for name, kind in (("Bezier", "BEZIER"), ("Nurbs", "NURBS")):
        curve = bpy.data.curves.new(name, "CURVE")
        curve.dimensions = "3D"
        spline = curve.splines.new(kind)
        if kind == "BEZIER":
            spline.bezier_points.add(2)
            for i, point in enumerate(spline.bezier_points):
                point.co = (i, i * i * .2, .1)
                point.handle_left_type = point.handle_right_type = "AUTO"
        else:
            spline.points.add(3)
            for i, point in enumerate(spline.points):
                point.co = (i, i * .2, .4, 1)
            spline.order_u = 3
            spline.use_endpoint_u = True
        curve.materials.append(material)
        obj = bpy.data.objects.new(name, curve)
        bpy.context.scene.collection.objects.link(obj)
    manual = bpy.data.curves.new("Manual texture space", "CURVE")
    manual.use_auto_texspace = False
    manual.texspace_location = (1, 2, 3)
    manual.texspace_size = (4, 5, 6)
    mesh = bpy.data.objects["Converted text"]
    mesh.modifiers.new("Bevel", "BEVEL").width = .02
    mesh.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
    for name, fake in (("Generated", False), ("Packed", True)):
        im = bpy.data.images.new(name, width=8, height=8)
        im.pixels[:] = [.2, .4, .6, 1] * 64
        im.use_fake_user = fake
        if name == "Packed":
            im.pack()

    # Cover every additional fingerprinted ID collection, including classes absent from the former KEEP list.
    constructors = {
        "materials": lambda name: bpy.data.materials.new(name),
        "meshes": lambda name: bpy.data.meshes.new(name),
        "curves": lambda name: bpy.data.curves.new(name, "CURVE"),
        "textures": lambda name: bpy.data.textures.new(name, "CLOUDS"),
        "lights": lambda name: bpy.data.lights.new(name, "POINT"),
        "cameras": lambda name: bpy.data.cameras.new(name),
        "worlds": lambda name: bpy.data.worlds.new(name),
        "objects": lambda name: bpy.data.objects.new(name, None),
        "collections": lambda name: bpy.data.collections.new(name),
        "actions": lambda name: bpy.data.actions.new(name),
        "armatures": lambda name: bpy.data.armatures.new(name),
        "lattices": lambda name: bpy.data.lattices.new(name),
        "metaballs": lambda name: bpy.data.metaballs.new(name),
        "grease_pencils": lambda name: bpy.data.grease_pencils.new(name),
        "particles": lambda name: bpy.data.particles.new(name),
        "volumes": lambda name: bpy.data.volumes.new(name),
        "pointclouds": lambda name: bpy.data.pointclouds.new(name),
        "hair_curves": lambda name: bpy.data.hair_curves.new(name),
        "speakers": lambda name: bpy.data.speakers.new(name),
        "lightprobes": lambda name: bpy.data.lightprobes.new(name, "SPHERE"),
    }
    retained = {}
    for collection, create in constructors.items():
        for fake in (False, True):
            idb = create(f"{'Fake' if fake else 'Orphan'} {collection}")
            idb.use_fake_user = fake
            idb["content_marker"] = collection
            retained[idb.name] = {"collection": collection, "users": idb.users, "fake": fake}
    retained["Converted text"] = {"collection": "curves", "users": bpy.data.curves["Converted text"].users,
                                  "fake": False}
    for name in ("Inter.woff2", "DejaVuSansMono.woff2"):
        font = bpy.data.fonts.load(str(Path(bpy.utils.resource_path("LOCAL")) / "datafiles/fonts" / name))
        font.name = name
        retained[name] = {"collection": "fonts", "users": font.users, "fake": False}
    bpy.data.curves["Live text"].font = bpy.data.fonts["Inter.woff2"]
    return retained


def main(out):
    out.mkdir(parents=True, exist_ok=True)
    retained = build()
    bpy.data.volumes["Fake volumes"].filepath = str(out / "volume-one.vdb")
    receipt = C.save(out / "checkpoint.blend", out)
    before = C.scene_state()
    before_rna = {coll: {idb.name: dict(C._rna(idb)) for idb in getattr(bpy.data, coll, ())}
                  for coll in C.OTHER_IDS}
    restored = C.restore(out / "checkpoint.blend", out, expect_fingerprint=receipt["fingerprint"],
                         expect_sha256=receipt["sha256"])
    after = C.scene_state()
    after_rna = {coll: {idb.name: dict(C._rna(idb)) for idb in getattr(bpy.data, coll, ())}
                 for coll in C.OTHER_IDS}
    # Write evidence before asserting, so a regression identifies changed components instead of losing its state.
    report = {"receipt": receipt, "restore": restored, "before": before, "after": after, "retained": retained,
              "survived": {name: name in getattr(bpy.data, row["collection"]) for name, row in retained.items()},
              "before_rna": before_rna, "after_rna": after_rna}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert restored["matches"], [(k, before[k], after[k]) for k in before if k != "summary" and before[k] != after[k]]
    assert all(report["survived"].values())
    assert "texspace_location" not in dict(C._rna(bpy.data.curves["Converted text"]))
    assert "texspace_size" not in dict(C._rna(bpy.data.curves["Converted text"]))
    assert dict(C._rna(bpy.data.curves["Manual texture space"]))["texspace_location"] == (1, 2, 3)
    assert dict(C._rna(bpy.data.curves["Manual texture space"]))["texspace_size"] == (4, 5, 6)
    changes = {
        "text_body": "bpy.data.curves['Live text'].body += ' changed'",
        "orphan_text_body": "bpy.data.curves['Orphan text'].body += ' changed'",
        "converted_orphan": "bpy.data.curves['Converted text'].resolution_u += 1",
        "fake_text_size": "bpy.data.curves['Fake text'].size += .5",
        "text_format": "bpy.data.curves['Live text'].body_format[0].use_bold = True",
        "text_font": "bpy.data.curves['Live text'].font = bpy.data.fonts['DejaVuSansMono.woff2']",
        "text_material": "bpy.data.curves['Live text'].materials.clear()",
        "bezier_geometry": "bpy.data.curves['Bezier'].splines[0].bezier_points[0].co.x += .25",
        "nurbs_geometry": "bpy.data.curves['Nurbs'].splines[0].points[0].co.x += .25",
        "mesh_geometry": "bpy.data.objects['Converted text'].data.vertices[0].co.x += .25",
        "modifier": "bpy.data.objects['Converted text'].modifiers['Bevel'].width += .25",
        "node_socket": "bpy.data.materials['Node material'].node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = .7",
        "node_link": "m = bpy.data.materials['Node material']; m.node_tree.links.remove(m.node_tree.links[0])",
        "orphan_node": "bpy.data.node_groups['Orphan nodes'].nodes[0].outputs[0].default_value = .8",
        "generated_pixels": "bpy.data.images['Generated'].pixels[0] = .9",
        "packed_pixels": "bpy.data.images['Packed'].pixels[0] = .9",
        "manual_texspace_location": "bpy.data.curves['Manual texture space'].texspace_location.x += .25",
        "manual_texspace_size": "bpy.data.curves['Manual texture space'].texspace_size.y += .25",
        "texspace_mode": "bpy.data.curves['Manual texture space'].use_auto_texspace = True",
        "volume_filepath": f"bpy.data.volumes['Fake volumes'].filepath = {str(out / 'volume-two.vdb')!r}",
        "particle_trail_count": "bpy.data.particles['Fake particles'].trail_count = 2",
    }
    for name, row in retained.items():
        changes[f"custom:{name}"] = f"getattr(bpy.data, {row['collection']!r})[{name!r}]['content_marker'] = 'changed'"
    mutations = {}
    for name, code in changes.items():
        reset = C.restore(out / "checkpoint.blend", out, expect_fingerprint=receipt["fingerprint"])
        assert reset["matches"], name
        exec(code, {"bpy": bpy})
        C.protect()
        mutations[name] = C.fingerprint() != receipt["fingerprint"]
        assert mutations[name], f"real authored change was not detected: {name}"
    C.restore(out / "checkpoint.blend", out, expect_fingerprint=receipt["fingerprint"])
    bpy.data.volumes["Fake volumes"].filepath = "//volume-relative.vdb"
    relative = C.save(out / "nested/relative.blend", out)
    relative_restore = C.restore(out / "nested/relative.blend", out, expect_fingerprint=relative["fingerprint"])
    assert relative_restore["matches"], "relative volume target changed when checkpoint moved to a subdirectory"
    assert Path(bpy.path.abspath(bpy.data.volumes["Fake volumes"].filepath)).resolve() == (out / "volume-relative.vdb").resolve()
    report["relative_volume_restore"] = relative_restore
    report["mutations_detected"] = mutations
    report["ok"] = True
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[sys.argv.index("--") + 1]))
