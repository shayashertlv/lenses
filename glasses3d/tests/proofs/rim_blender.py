"""$0 synthetic-only facade, Blender shading and exported-rim proof.

Run in a fresh headless Blender, --python-exit-code 1 --python this_file -- OUTDIR.
No job, reference photo, product scene or provider is read. Writes its own proof
report, GLB and front/angled images. It never writes a deliverable or .blend.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from glasses3d.agent import procs
from glasses3d.g3d import export_glb, lib, safe_mode
from glasses3d.g3d_host import glb


def main(out):
    out.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "synthetic_only": True,
              "paid_requests": 0, "ecoqos_opt_out": procs.opt_out_self(), "cases": {}}
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene["mdl_bridge_underside"] = [0.0, 0.0, 0.0]
    facade, errors = lib.build()
    assert not errors, errors
    assert all(hasattr(facade, name) for name in ("acetate_section", "sweep_rim"))
    code = "section = g3d.acetate_section(6, 5, .6, .7)"
    assert safe_mode.validate(code) == []
    env = {"g3d": facade}
    exec(code, env)
    section = env["section"]
    p = np.array(section["points"])
    mat = bpy.data.materials.new("Synthetic warm resin")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.35, 0.15, 0.045, 1)
    bsdf.inputs["Roughness"].default_value = .26
    theta = np.arange(96)*2*math.pi/96
    specimens = []
    for label, x, wrap, facet in (("round", -63, 0, False), ("wrapped", 0, .01, False),
                                   ("local_facet", 63, .01, True)):
        q = np.c_[24*np.cos(theta), 17*np.sin(theta), -wrap*(24*np.cos(theta))**2]
        q[:, 0] += x
        profiles = None
        if facet:
            profiles = [deepcopy(section) for _ in q]
            crown = [j for j, k in enumerate(section["kind"]) if k == "crown"]
            a, b = crown[3], crown[-4]
            for i in range(17, 27):
                for j in range(a, b+1):
                    profiles[i]["points"][j][1] = 2.1
                profiles[i]["sharp"][a] = profiles[i]["sharp"][b] = True
        receipt = facade.sweep_rim(q.tolist(), section, sections=profiles, name=label, material=mat)
        assert receipt["status"] == "ok", receipt
        obj = bpy.data.objects[receipt["object"]]
        specimens.append(obj)
        mesh = obj.data
        mesh.calc_loop_triangles()
        v = np.array([list(a.co) for a in mesh.vertices])
        f = np.array([list(a.vertices) for a in mesh.loop_triangles])
        directed = Counter((int(a), int(b)) for face in f for a, b in zip(face, np.roll(face, -1)))
        assert all(c == 1 and directed[(b, a)] == 1 for (a, b), c in directed.items())
        assert len(v)-len(directed)//2+len(f) == 0
        volume = float(np.einsum("ij,ij->i", v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum()/6)
        assert volume > 0 and abs(volume-receipt["volume_mm3"]) < 0.02
        bvh = BVHTree.FromPolygons(v.tolist(), f.tolist(), all_triangles=True)
        assert bvh.ray_cast(Vector((x, 0, 100)), Vector((0, 0, -1)))[0] is None
        assert bvh.ray_cast(Vector((x+24, 0, 100)), Vector((0, 0, -1)))[0] is not None
        assert all(face.use_smooth for face in mesh.polygons)
        sharp = sum(bool(e.value) for e in mesh.attributes["sharp_edge"].data)
        assert sharp == receipt["sharp_edges"] == (18 if facet else 0)
        normals = np.array([list(n.vector) for n in mesh.corner_normals])
        loops = np.array([a.vertex_index for a in mesh.loops])
        spreads = []
        for vi in np.unique(loops):
            at = normals[loops == vi]
            spreads.append(float(np.linalg.norm(at-at[0], axis=1).max()))
        if facet:
            assert max(spreads) > .05
        else:
            assert max(spreads) < 1e-6
        report["cases"][label] = {"topology": "closed oriented two-manifold", "euler": 0,
            "volume_mm3": volume, "opening_ray_clear": True, "solid_ray_hits": True,
            "vertices": len(v), "triangles": len(f), "sharp_edges": sharp,
            "max_normal_split": max(spreads), "receipt": receipt}
    # A refusal creates nothing and changes no existing geometry.
    before = {ob.name: len(ob.data.vertices) for ob in specimens}
    refused = facade.sweep_rim([[0,0,0],[1,0,0],[0,0,0]], section, name="must_not_exist")
    assert refused["status"] == "refused" and bpy.data.objects.get("must_not_exist") is None
    assert before == {ob.name: len(ob.data.vertices) for ob in specimens}
    report["invalid_outline_refused_without_mutation"] = True
    export_path = out / "synthetic-rims.glb"
    receipt = export_glb.export_glb(export_path)
    assert receipt["status"] == "exported" and receipt["scene_unchanged"], receipt
    model = glb.load(export_path)
    for primitive in model.primitives:
        assert primitive.error is None and primitive.normals is not None
        key = primitive.node_name
        assert key in report["cases"]
        assert len(primitive.triangles) == report["cases"][key]["triangles"]
        # Exported normal splits occur only for the authored local facet.
        positions = np.round(primitive.positions*1000, 5)
        distinct = len(np.unique(positions, axis=0))
        assert distinct == report["cases"][key]["vertices"]
        report["cases"][key]["exported_vertices"] = len(positions)
        if key != "local_facet":
            assert len(positions) == distinct
        else:
            assert len(positions) > distinct
    report["export"] = {"path": str(export_path), "scene_unchanged": True}
    # Native Blender renders of synthetic specimens; no product/material edits.
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.render.resolution_x, scene.render.resolution_y = 1440, 500
    scene.render.resolution_percentage = 100
    scene.world.color = (.35, .35, .35)
    for name, loc, power, size in (("Key", (-40,-30,120), 150000, 100), ("Fill", (60,50,90), 100000, 90)):
        light = bpy.data.lights.new(name, "AREA")
        light.energy, light.shape, light.size = power, "DISK", size
        ob = bpy.data.objects.new(name, light)
        scene.collection.objects.link(ob)
        ob.location = loc
        ob.rotation_euler = (Vector((0,0,0))-ob.location).to_track_quat("-Z", "Y").to_euler()
    camera = bpy.data.cameras.new("Proof camera")
    camera.type, camera.ortho_scale = "ORTHO", 190
    ob = bpy.data.objects.new("Proof camera", camera)
    scene.collection.objects.link(ob)
    scene.camera = ob
    images = []
    for name, loc in (("front", (0,0,170)), ("angled", (20,-95,115))):
        ob.location = loc
        ob.rotation_euler = (Vector((0,0,-2))-ob.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(out / f"synthetic-rims-{name}.png")
        bpy.ops.render.render(write_still=True)
        images.append(scene.render.filepath)
    report.update(status="passed", images=images, ended_at=datetime.now(timezone.utc).isoformat())
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "report": str(out / "report.json")}))


if __name__ == "__main__":
    main(Path(sys.argv[sys.argv.index("--")+1]).resolve())
