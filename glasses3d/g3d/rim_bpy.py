"""In-memory Blender facade for the general rim core; no file or product inputs."""
from __future__ import annotations

import bpy
import numpy as np

from .rim import RimError, acetate_section
from .rim import sweep_rim as build_rim


def sweep_rim(outline, section, *, sections=None, name=None, material=None, part_role="frame") -> dict:
    """Plan a closed rim; with name create one new smooth mesh object and return its name.

    Existing objects, materials and geometry are never changed. Sharp edges come
    solely from the profiles' explicit local flags. A material can be its name or
    datablock. The pure section helper and the geometry arguments use millimetres.
    """
    try:
        built = build_rim(outline, section, sections=sections)
        mat = bpy.data.materials.get(material) if isinstance(material, str) else material
        if isinstance(material, str) and mat is None:
            raise RimError(f"material {material!r} does not exist")
        if mat is not None and not isinstance(mat, bpy.types.Material):
            raise RimError("material must be a Blender material or its name")
        if part_role not in (None, "frame", "lens", "temple"):
            raise RimError("part_role must be frame, lens, temple or None")
    except (RimError, TypeError, ValueError) as exc:
        return {"status": "refused", "code": "E_SPEC", "message": str(exc)}
    report = {"status": "ok", **{k: built[k] for k in (
        "closed", "euler", "volume_mm3", "vertices_per_section", "stations", "note")},
              "sharp_edges": len(built["sharp_edges"]), "sharp_spans": built["sharp_spans"]}
    if name:
        mesh = bpy.data.meshes.new(str(name))
        obj = None
        try:
            mesh.from_pydata(built["V"].tolist(), [], built["F"].tolist())
            mesh.update()
            mesh.shade_smooth()
            sharp = {tuple(sorted(e)) for e in built["sharp_edges"]}
            attr = mesh.attributes.get("sharp_edge") or mesh.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
            attr.data.foreach_set("value", np.asarray([tuple(sorted(e.vertices)) in sharp for e in mesh.edges], bool))
            if mat is not None:
                mesh.materials.append(mat)
            obj = bpy.data.objects.new(str(name), mesh)
            bpy.context.scene.collection.objects.link(obj)
            if part_role:
                obj["partRole"] = part_role
            report["object"] = obj.name
        except Exception:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            raise
    return report
