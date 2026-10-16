"""In-memory quadratic lens construction for Astra; no file I/O."""
import bpy
from .lens_surface import quadratic_lens as build


def quadratic_lens(outline_xy_mm, coefficients, thickness_mm, *, centre_xy_mm=None,
                   max_edge_mm=1.5, max_chord_mm=.003, name=None, material=None):
    try:
        result = build(outline_xy_mm, coefficients, thickness_mm, centre_xy_mm=centre_xy_mm,
                       max_edge_mm=max_edge_mm, max_chord_mm=max_chord_mm)
        mat = bpy.data.materials.get(material) if isinstance(material, str) else material
        if material is not None and not isinstance(mat, bpy.types.Material):
            raise ValueError("material must name an existing Blender material")
    except (ValueError, TypeError) as error:
        return {"status": "refused", "code": "E_SPEC", "message": str(error)}
    report = {"status": "ok", **{k: v for k, v in result.items() if k not in ("vertices_mm", "triangles", "corner_normals")}}
    if name:
        mesh = bpy.data.meshes.new(str(name)); obj = None
        try:
            mesh.from_pydata(result["vertices_mm"].tolist(), [], result["triangles"].tolist())
            mesh.update(); mesh.shade_smooth()
            mesh.normals_split_custom_set(result["corner_normals"].reshape(-1, 3).tolist())
            if mat is not None:
                mesh.materials.append(mat)
            obj = bpy.data.objects.new(str(name), mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj["partRole"] = "lens"
            report["object"] = obj.name
        except Exception:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            raise
    return report
