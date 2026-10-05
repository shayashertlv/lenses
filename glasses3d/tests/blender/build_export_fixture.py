"""Build deterministic export fixture scenes in a factory-startup Blender and save them.

    blender -b --factory-startup --python build_export_fixture.py -- <out.blend> [variant]
    blender -b --factory-startup --python build_export_fixture.py -- <out_dir> <variant> [<variant> ...]

The second form writes <out_dir>/fixture-<variant>.blend for each variant (relative_image goes to
<out_dir>/relimg/fixture-relative_image.blend with its texture at relimg/tex/print.png).

The 'ok' scene: numeric mm, +Y up / +Z front, bridge underside at the origin; a bridge and brows (frame, boxes with
UVs), two lenses (lens, transmissive), two FACETED 12-sided temples (flat-shaded: facets 30 deg apart, so a 30-40 deg
re-smooth would merge them), a SMOOTH sphere hinge cap (frame), a clearcoat acetate material (coat 0.8, coat
roughness 0.05) and an image-textured material (64x48 generated image, packed) on the UV-mapped brows.

Variants that must be refused: no_role, no_origin, cm_units, wrong_axes, hidden_viewport, hidden_viewport_coll,
viewport_modifier, subsurf_levels, simplify (Scene Simplify: Max Subdivision 0 in viewports, 6 for render, on a
level-2 Subdivision), missing_image, role_conflict, gn_instances, curve_instances, empty_mesh (an empty mesh WITH
partRole) and masked_lens (Lens_R's Mask modifier removes every face: a declared part that renders nothing), and
layers_disagree (two render layers, only one of which excludes the 'Archive' collection).
Variants that must export: ok, shared_lens, offset_origin (geometry and mdl_bridge_underside moved by (0, 5, 3) mm),
normals (smooth faces with sharp edges, Bevel + WEIGHTED_NORMAL, custom normals plain / mirrored / non-uniformly
scaled), excluded_coll (a role-less scan in a view-layer-excluded collection), unused_layer (the active layer 'Work'
is not used for rendering, includes 'Archive' with a role-tagged Old_brow and excludes 'Hardware' with Screw_R; the
render layer 'Final' does the opposite, so only Final's own depsgraph evaluates Screw_R),
relative_image, guide_curve (a role-less curve without bevel: renders no surface, skipped), data_role (partRole on the mesh data), curve_smooth (a beveled curve with
Smooth by Angle), nan_hidden (a render-hidden helper with a NaN vertex), no_uvs (textured brows without UVs: noted),
volume_mm (a lens volume Thickness of 2.0: millimetres, noted) and volume_dropped (a glTF Material Output group
without an Occlusion socket: Blender drops the volume, noted).
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

OFFSET = (0.0, 5.0, 3.0)
NORMAL_OBJECTS = ("Temple_L", "N_smooth_sharp", "N_weighted", "N_custom_plain", "N_custom_mirror", "N_custom_squash")


def material(name, color, *, coat=0.0, transmission=0.0, image=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.25
    if coat:
        bsdf.inputs["Coat Weight"].default_value = coat
        bsdf.inputs["Coat Roughness"].default_value = 0.05
    if transmission:
        bsdf.inputs["Transmission Weight"].default_value = transmission
        bsdf.inputs["IOR"].default_value = 1.5
    if image is not None:
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def link(ob, coll=None):
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def box(name, x0, x1, y0, y1, z0, z1, mat, role, coll=None, uvs=True):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if uvs:
        bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_cube(bm, size=1.0, calc_uvs=uvs)
    for v in bm.verts:
        v.co.x = x0 + (v.co.x + 0.5) * (x1 - x0)
        v.co.y = y0 + (v.co.y + 0.5) * (y1 - y0)
        v.co.z = z0 + (v.co.z + 0.5) * (z1 - z0)
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me), coll)
    me.materials.append(mat)
    if role:
        ob["partRole"] = role
    return ob


def prism_mesh(name, cx, cy, z0, z1, sides, rx, ry, *, smooth=False, sharp_every=0):
    """A closed `sides`-gon prism along Z (facet normals 360/sides deg apart)."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    ring = []
    for z in (z0, z1):
        r = []
        for k in range(sides):
            a = math.radians(360.0 / sides * k + 180.0 / sides)
            r.append(bm.verts.new((cx + rx * math.cos(a), cy + ry * math.sin(a), z)))
        ring.append(r)
    for k in range(sides):
        bm.faces.new((ring[0][k], ring[0][(k + 1) % sides], ring[1][(k + 1) % sides], ring[1][k]))
    bm.faces.new(ring[0][::-1])
    bm.faces.new(ring[1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        f.smooth = smooth
    for e in bm.edges:
        side_edge = abs(e.verts[0].co.z - e.verts[1].co.z) > 1e-6
        if not smooth:
            e.smooth = False
        elif not side_edge:
            e.smooth = False                                   # the cap rims are always sharp
        elif sharp_every:
            k = min(ring[0].index(v) if v in ring[0] else ring[1].index(v) for v in e.verts)
            e.smooth = k % sharp_every != 0
    bm.to_mesh(me)
    bm.free()
    return me


def custom_normal_prism(name, cx, cy, z0, z1, tilt):
    """A smooth 8-gon prism whose custom normals are normalize(radial + tilt): they differ from the auto normals."""
    me = prism_mesh(name, cx, cy, z0, z1, 8, 3.0, 3.0, smooth=True)
    normals = []
    for v in me.vertices:
        radial = Vector((v.co.x - cx, v.co.y - cy, 0.0)).normalized()
        normals.append(tuple((radial + Vector(tilt)).normalized()))
    me.normals_split_custom_set_from_vertices(normals)
    return me


def faceted_temple(name, side, mat):
    """A 12-sided prism along -Z from z=-2 to z=-140 at |x|=68: twelve exactly planar facets, flat shaded."""
    me = prism_mesh(name, side * 68, 5, -2.0, -140.0, 12, 2.2, 3.0)
    ob = link(bpy.data.objects.new(name, me))
    me.materials.append(mat)
    ob["partRole"] = "temple"
    return ob


def gltf_output_group(sockets):
    """A hand-made 'glTF Material Output' group (Blender's exporter reads its Thickness only with an Occlusion
    socket present)."""
    ng = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
    for s in sockets:
        ng.interface.new_socket(s, in_out="INPUT", socket_type="NodeSocketFloat")
    return ng


def gn_instance_group():
    ng = bpy.data.node_groups.new("Instance rivets", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    ico = ng.nodes.new("GeometryNodeMeshIcoSphere")
    iop = ng.nodes.new("GeometryNodeInstanceOnPoints")
    join = ng.nodes.new("GeometryNodeJoinGeometry")
    ng.links.new(gi.outputs[0], iop.inputs["Points"])
    ng.links.new(ico.outputs["Mesh"], iop.inputs["Instance"])
    ng.links.new(gi.outputs[0], join.inputs[0])
    ng.links.new(iop.outputs[0], join.inputs[0])
    ng.links.new(join.outputs[0], go.inputs[0])
    return ng


def build(variant, out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001 if variant != "cm_units" else 0.01
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene["blender_agent_axes"] = ("+Z up; -Y front" if variant == "wrong_axes"
                                   else "+Y up; +Z front; numeric millimetres")
    if variant != "no_origin":
        scene["mdl_bridge_underside"] = list(OFFSET) if variant == "offset_origin" else [0.0, 0.0, 0.0]

    img = bpy.data.images.new("havana_print", width=64, height=48, alpha=False)
    px = []
    for y in range(48):
        for x in range(64):
            v = 0.5 + 0.5 * math.sin(x * 0.7) * math.cos(y * 0.9)
            px += [0.35 * v, 0.18 * v, 0.07 * v, 1.0]
    img.pixels = px
    if variant == "relative_image":
        tex = os.path.join(os.path.dirname(out), "tex", "print.png")
        os.makedirs(os.path.dirname(tex), exist_ok=True)
        img.filepath_raw = tex
        img.file_format = "PNG"
        img.save()
        img.source = "FILE"
    elif variant == "missing_image":
        img.source = "FILE"
        img.filepath = "C:/nonexistent/g3d/havana.png"
    else:
        img.pack()

    acetate = material("Acetate | glossy black", (0.02, 0.02, 0.02), coat=0.8)
    havana = material("Havana | photo print", (1, 1, 1), image=img)
    lens = material("Lens | clear", (1, 1, 1), transmission=1.0)
    metal = material("Hinge metal", (0.8, 0.7, 0.4))

    box("Bridge", -9, 9, 0, 4, -2, 2, acetate, "frame")
    box("Brow_L", -70, -9, 4, 10, -2, 2, havana, "frame", uvs=variant != "no_uvs")
    box("Brow_R", 9, 70, 4, 10, -2, 2, havana, "frame", uvs=variant != "no_uvs")
    box("Lens_L", -60, -10, -25, 6, -1, 1, lens, None if variant == "no_role" else "lens")
    box("Lens_R", 10, 60, -25, 6, -1, 1, lens, "lens")
    faceted_temple("Temple_L", -1, acetate)
    faceted_temple("Temple_R", 1, acetate)

    me = bpy.data.meshes.new("Hinge_cap")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.5)
    for v in bm.verts:
        v.co.x += 68
        v.co.y += 5
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(me)
    bm.free()
    cap = link(bpy.data.objects.new("Hinge_cap", me))
    me.materials.append(metal)
    cap["partRole"] = "frame"

    if variant == "empty_mesh":
        e = link(bpy.data.objects.new("Empty_mesh", bpy.data.meshes.new("Empty_mesh")))
        e["partRole"] = "frame"
    elif variant == "guide_curve":
        cu = bpy.data.curves.new("Temple_path", "CURVE")
        cu.dimensions = "3D"
        sp = cu.splines.new("POLY")
        sp.points.add(1)
        sp.points[0].co, sp.points[1].co = (68, 5, -2, 1), (68, 5, -140, 1)
        link(bpy.data.objects.new("Temple_path", cu))                       # no bevel, no partRole
    elif variant == "shared_lens":
        bpy.data.objects["Bridge"].data.materials[0] = lens                  # a frame part painted with the lens material
    elif variant == "offset_origin":
        for ob in scene.objects:
            ob.matrix_world = Matrix.Translation(OFFSET) @ ob.matrix_world
    elif variant == "normals":
        ob = link(bpy.data.objects.new("N_smooth_sharp", prism_mesh("N_smooth_sharp", -25, -5, -40, -60, 12, 3, 3,
                                                                     smooth=True, sharp_every=3)))
        ob.data.materials.append(acetate)
        ob = box("N_weighted", -5, 5, -10, 0, -50, -40, metal, "frame")
        bev = ob.modifiers.new("Bevel", "BEVEL")
        bev.width, bev.segments = 1.0, 3
        wn = ob.modifiers.new("Weighted", "WEIGHTED_NORMAL")
        wn.keep_sharp = True
        for p in ob.data.polygons:
            p.use_smooth = True
        ob = link(bpy.data.objects.new("N_custom_plain", custom_normal_prism("N_custom_plain", 25, -5, -40, -60,
                                                                             (0.0, 0.4, 0.5))))
        ob.data.materials.append(metal)
        ob = link(bpy.data.objects.new("N_custom_mirror", custom_normal_prism("N_custom_mirror", 25, -5, -70, -90,
                                                                              (0.0, 0.4, 0.5))))
        ob.matrix_world = Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))           # a mirrored copy (det < 0)
        ob.data.materials.append(metal)
        ob = link(bpy.data.objects.new("N_custom_squash", custom_normal_prism("N_custom_squash", 0, -2, -80, -100,
                                                                              (0.3, 0.4, 0.5))))
        ob.matrix_world = Matrix.Diagonal((1.0, 2.5, 1.0, 1.0))            # non-uniform scale
        ob.data.materials.append(metal)
        for name in NORMAL_OBJECTS[1:]:
            bpy.data.objects[name]["partRole"] = "frame"
    elif variant == "excluded_coll":
        coll = bpy.data.collections.new("Reference")
        scene.collection.children.link(coll)
        box("Meshy scan (reference)", -60, 60, -70, -60, -2, 2, metal, None, coll)
        bpy.context.view_layer.layer_collection.children["Reference"].exclude = True
    elif variant in ("hidden_viewport", "hidden_viewport_coll"):
        coll = None
        if variant == "hidden_viewport_coll":
            coll = bpy.data.collections.new("Rivets")
            scene.collection.children.link(coll)
            coll.hide_viewport = True
        ob = box("Rivets", -3, -1, 6, 8, -2, 0, metal, "frame", coll)
        arr = ob.modifiers.new("Array", "ARRAY")
        arr.count = 3
        if variant == "hidden_viewport":
            ob.hide_viewport = True
    elif variant == "viewport_modifier":
        dec = cap.modifiers.new("Decimate", "DECIMATE")
        dec.ratio = 0.25
        dec.show_render = False
    elif variant == "subsurf_levels":
        sub = cap.modifiers.new("Subdivision", "SUBSURF")
        sub.levels, sub.render_levels = 0, 2
    elif variant == "simplify":
        sub = cap.modifiers.new("Subdivision", "SUBSURF")
        sub.levels = sub.render_levels = 2
        scene.render.use_simplify = True
        scene.render.simplify_subdivision, scene.render.simplify_subdivision_render = 0, 6
    elif variant in ("unused_layer", "layers_disagree"):
        work = bpy.context.view_layer
        work.name = "Work"
        work.use = variant == "layers_disagree"
        final = scene.view_layers.new("Final")
        final.use = True
        coll = bpy.data.collections.new("Archive")
        scene.collection.children.link(coll)
        box("Old_brow", -70, 70, 30, 40, -2, 2, metal, "frame", coll)
        final.layer_collection.children["Archive"].exclude = True
        hw = bpy.data.collections.new("Hardware")                           # renders, but only in 'Final'
        scene.collection.children.link(hw)
        box("Screw_R", 66, 68, 3, 5, -6, -4, metal, "frame", hw)
        work.layer_collection.children["Hardware"].exclude = True
    elif variant == "masked_lens":
        lens_r = bpy.data.objects["Lens_R"]
        lens_r.vertex_groups.new(name="keep")                               # empty: the Mask removes every face
        lens_r.modifiers.new("Mask", "MASK").vertex_group = "keep"
    elif variant == "data_role":
        del cap["partRole"]
        cap.data["partRole"] = "frame"
    elif variant == "role_conflict":
        cap.data["partRole"] = "lens"
    elif variant == "gn_instances":
        mod = cap.modifiers.new("Rivets", "NODES")
        mod.node_group = gn_instance_group()
    elif variant == "curve_smooth":
        cu = bpy.data.curves.new("Rim_wire", "CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = 0.6
        sp = cu.splines.new("POLY")
        sp.points.add(2)
        for i, p in enumerate(sp.points):
            p.co = (-8 + 8 * i, 11.0, -1.0 + 0.5 * i, 1)
        ob = link(bpy.data.objects.new("Rim_wire", cu))
        cu.materials.append(acetate)
        ob["partRole"] = "frame"
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        assert bpy.ops.object.shade_auto_smooth() == {"FINISHED"}
        ob.select_set(False)
        assert [m.type for m in ob.modifiers] == ["NODES"]
    elif variant == "curve_instances":
        cu = bpy.data.curves.new("Rim_rivets", "CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = 0.6
        sp = cu.splines.new("POLY")
        sp.points.add(2)
        for i, p in enumerate(sp.points):
            p.co = (-8 + 8 * i, 11.0, -1.0, 1)
        ob = link(bpy.data.objects.new("Rim_rivets", cu))
        ob["partRole"] = "frame"
        ob.modifiers.new("Rivets", "NODES").node_group = gn_instance_group()   # real instances on a curve
    elif variant in ("volume_mm", "volume_dropped"):
        group = gltf_output_group(["Occlusion", "Thickness"] if variant == "volume_mm" else ["Thickness"])
        node = lens.node_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        node.inputs["Thickness"].default_value = 2.0                         # millimetres: the wrong convention
    elif variant == "nan_hidden":
        ob = box("Broken_helper", 0, 1, 20, 21, -50, -49, metal, None)
        ob.data.vertices[0].co = (float("nan"), 0.0, 0.0)
        ob.hide_render = True

    bpy.ops.wm.save_as_mainfile(filepath=out, compress=False, relative_remap=True)
    print("FIXTURE_SAVED", out, variant)


args = sys.argv[sys.argv.index("--") + 1:]
if args[0].lower().endswith(".blend"):
    build(args[1] if len(args) > 1 else "ok", args[0])
else:
    for v in args[1:]:
        if v == "relative_image":
            path = os.path.join(args[0], "relimg", f"fixture-{v}.blend")
            os.makedirs(os.path.dirname(path), exist_ok=True)
        else:
            path = os.path.join(args[0], f"fixture-{v}.blend")
        build(v, path)
