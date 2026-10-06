"""Build the WP5 print-tool fixture scene in a factory-startup Blender (tests only).

    blender -b --factory-startup --python tests/blender/print_build.py -- <out.blend>

Scene convention as the WP3 fixture (numeric mm, +Y up / +Z front, bridge underside at the origin).
- Brow_L: a smooth bent elliptical tube along x (30 rings x 16 quads, 16-gon end caps) with a 'UVMap' layer;
  Brow_R: its exact mirror in x = 0, built as its own mesh (no Mirror modifier) with each quad keeping its first
  corner, so both triangulate along the same diagonal. Both use 'Havana | photo print' (a packed 64 x 48 image on
  Base Color), shared by the two brows only.
- Temple_L / Temple_R: flat-shaded 12-gon prisms along -z at |x| = 68 (23 rings), no UV layer, using
  'Acetate temple', which the Bridge (a frame part that is not printed) shares.
- Lens_L / Lens_R (partRole lens, transmissive), Bridge, a smooth Hinge_cap.
"""
import math
import sys

import bmesh
import bpy

out = sys.argv[sys.argv.index("--") + 1:][0]
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 0.001
scene.unit_settings.length_unit = "MILLIMETERS"
scene["blender_agent_axes"] = "+Y up; +Z front; numeric millimetres"
scene["mdl_bridge_underside"] = [0.0, 0.0, 0.0]


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


def obj(name, me, mat, role):
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    me.materials.append(mat)
    ob["partRole"] = role
    return ob


def tube(name, sign, mat, uv=True):
    """Bent elliptical tube from |x| = 9 to 70 (sign -1: left), smooth quads, n-gon caps, outward faces."""
    n_s, n_t, L, x0 = 30, 16, 61.0, 9.0
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    rings = []
    for i in range(n_s):
        s = i / (n_s - 1)
        cx = x0 + L * s
        cy = 7.0 + 1.5 * math.sin(math.pi * s)
        cz = -4.0 * (2 * s - 1) ** 2
        r = []
        for j in range(n_t):
            t = 2 * math.pi * j / n_t
            r.append(bm.verts.new((sign * cx, cy + 3.0 * math.cos(t), cz + 2.0 * math.sin(t))))
        rings.append(r)
    centre = [(sign * (x0 + L * i / (n_s - 1)), 7.0 + 1.5 * math.sin(math.pi * i / (n_s - 1)),
               -4.0 * (2 * i / (n_s - 1) - 1) ** 2) for i in range(n_s)]
    made = []                                              # (face, outward test)
    for i in range(n_s - 1):
        for j in range(n_t):
            q = [rings[i][j], rings[i][(j + 1) % n_t], rings[i + 1][(j + 1) % n_t], rings[i + 1][j]]
            # the right brow is the left one mirrored: reverse the winding, keep the first corner (same diagonal)
            q = q if sign < 0 else [q[0]] + q[1:][::-1]
            ax = [(centre[i][k] + centre[i + 1][k]) / 2 for k in range(3)]
            made.append((bm.faces.new(q), lambda f, ax=ax: sum(f.normal[k] * (f.calc_center_median()[k] - ax[k])
                                                                for k in range(3))))
    for ring, out_x in ((rings[0], -sign), (rings[-1], sign)):
        q = list(ring) if sign < 0 else [ring[0]] + list(ring[1:])[::-1]
        made.append((bm.faces.new(q), lambda f, ox=out_x: f.normal.x * ox))
    bm.normal_update()
    for f, outward in made:                                # outward faces (mirror-consistent flips)
        if outward(f) < 0:
            f.normal_flip()
        f.smooth = True
    bm.normal_update()
    if uv:
        lay = bm.loops.layers.uv.new("UVMap")
        for f in bm.faces:
            for lp in f.loops:
                lp[lay].uv = ((abs(lp.vert.co.x) - x0) / L, 0.5 + math.atan2(lp.vert.co.z, lp.vert.co.y - 7) / 6.3)
    bm.to_mesh(me)
    bm.free()
    return obj(name, me, mat, "frame")


def prism(name, cx, cy, z0, z1, sides, rx, ry, rings, mat, role, smooth=False):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    ring = []
    for k in range(rings + 1):
        z = z0 + (z1 - z0) * k / rings
        ring.append([bm.verts.new((cx + rx * math.cos(math.radians(360.0 / sides * j + 180.0 / sides)),
                                   cy + ry * math.sin(math.radians(360.0 / sides * j + 180.0 / sides)), z))
                     for j in range(sides)])
    for k in range(rings):
        for j in range(sides):
            bm.faces.new((ring[k][j], ring[k][(j + 1) % sides], ring[k + 1][(j + 1) % sides], ring[k + 1][j]))
    bm.faces.new(ring[0][::-1])
    bm.faces.new(ring[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        f.smooth = smooth
    bm.to_mesh(me)
    bm.free()
    return obj(name, me, mat, role)


def mirror_of(src, name, role):
    """`src` reflected in x = 0 as its own mesh: every polygon reversed but keeping its first corner, so each quad
    triangulates along the mirrored diagonal; flags, uv layers and materials copied."""
    bm = bmesh.new()
    bm.from_mesh(src.data)
    bmesh.ops.reverse_faces(bm, faces=bm.faces, flip_multires=False)
    for v in bm.verts:
        v.co.x = -v.co.x
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    for m in src.data.materials:
        me.materials.append(m)
    ob["partRole"] = role
    return ob


def box(name, x0, x1, y0, y1, z0, z1, mat, role):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = x0 + (v.co.x + 0.5) * (x1 - x0)
        v.co.y = y0 + (v.co.y + 0.5) * (y1 - y0)
        v.co.z = z0 + (v.co.z + 0.5) * (z1 - z0)
    bm.to_mesh(me)
    bm.free()
    return obj(name, me, mat, role)


img = bpy.data.images.new("havana_print", width=64, height=48, alpha=False)
px = []
for y in range(48):
    for x in range(64):
        v = 0.5 + 0.5 * math.sin(x * 0.7) * math.cos(y * 0.9)
        px += [0.35 * v, 0.18 * v, 0.07 * v, 1.0]
img.pixels = px
img.pack()
havana = material("Havana | photo print", (1, 1, 1), image=img)
temple = material("Acetate temple", (0.08, 0.04, 0.02), coat=0.8)
lens = material("Lens | clear", (1, 1, 1), transmission=1.0)
metal = material("Hinge metal", (0.8, 0.7, 0.4))
mirror_of(tube("Brow_L", -1.0, havana), "Brow_R", "frame")
mirror_of(prism("Temple_L", -68.0, 5.0, -2.0, -140.0, 12, 2.2, 3.0, 23, temple, "temple"), "Temple_R", "temple")
box("Bridge", -9, 9, 0, 4, -2, 2, temple, "frame")
box("Lens_L", -60, -10, -25, 6, -1, 1, lens, "lens")
box("Lens_R", 10, 60, -25, 6, -1, 1, lens, "lens")
prism("Hinge_cap", 68.0, 5.0, 1.5, -1.5, 16, 1.5, 1.5, 2, metal, "frame", smooth=True)
bpy.ops.wm.save_as_mainfile(filepath=out, check_existing=False)
print("PRINT_BUILD", out)
