"""Build the WP4 synthetic seating scenes as .blend files (headless Blender 5.2; tests only).

    blender -b --factory-startup --python tests/blender/seat_build.py -- <out_dir> [scene ...]

Every scene follows the Studio convention the exporter checks: numeric millimetres, '+Y up; +Z front', the
bridge-underside marker `mdl_bridge_underside`, partRole on every object. Geometry comes from the deterministic
numpy builder tests/fixtures/seat_fixtures.py (WP4_DESIGN §10 P4). Lens quads are rebuilt from the builder's triangle
pairs (Studio lenses are quad meshes; the edge profiles need a one-ring quad wall). Variants:
- `origin` scenes move everything by ORIGIN_OFFSET and set the marker there (asset frame != world frame);
- `pair_transformed`: two lenses whose objects carry a rotation + translation, Lens_R also a mirror (-1 x scale), with
  local coordinates chosen so the world geometry is the fixture's (the tools write back through M3^-1);
- `custom_normals`: the acetate lens carrying authored custom normals (its own smooth normals, stored);
- `browline_pair`: two browline sides (two lenses, two eyewires, two brows) for unhide_eyewire's one-side rule.
"""
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True        # tests/fixtures/ is re-included in .gitignore: no __pycache__ there
from glasses3d.tests.fixtures import seat_fixtures as SF       # noqa: E402

ORIGIN_OFFSET = (0.0, 2.0, 1.5)


def quadify(T: np.ndarray, poly_edges) -> list:
    """Faces from triangle pairs (t0, t1) that share a diagonal not in poly_edges (the builder's quads)."""
    pe = set() if poly_edges is None else {tuple(sorted(e)) for e in np.asarray(poly_edges).tolist()}
    T = np.asarray(T).tolist()
    out, i = [], 0
    while i < len(T):
        a = T[i]
        if i + 1 < len(T) and pe:
            b = T[i + 1]
            shared = set(a) & set(b)
            if len(shared) == 2 and tuple(sorted(shared)) not in pe:
                x = [v for v in a if v not in shared][0]
                y = [v for v in b if v not in shared][0]
                k = a.index(x)
                p, q = a[(k + 1) % 3], a[(k + 2) % 3]           # a = (x, p, q); the quad is x, p, y, q
                out.append((x, p, y, q))
                i += 2
                continue
        out.append(tuple(a))
        i += 1
    return out


def material(name: str, transmission: float = 0.0, colour=(0.05, 0.05, 0.05, 1.0)):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = colour
        bsdf.inputs["Transmission Weight"].default_value = transmission
        bsdf.inputs["Roughness"].default_value = 0.2
    return m


def add(name: str, mesh: SF.Mesh, role: str, mat, offset=(0, 0, 0), matrix: Matrix | None = None, quads=False):
    V = np.asarray(mesh.V, np.float64) + np.asarray(offset, np.float64)
    if matrix is not None:
        Mi = np.array(matrix.inverted(), np.float64)
        V = V @ Mi[:3, :3].T + Mi[:3, 3]
    faces = quadify(mesh.T, mesh.poly_edges) if quads else [tuple(t) for t in np.asarray(mesh.T).tolist()]
    me = bpy.data.meshes.new(name)
    me.from_pydata(V.tolist(), [], faces)
    me.update()
    me.shade_smooth()                                   # Studio lenses and frames are smooth-shaded
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    if matrix is not None:
        ob.matrix_world = matrix
    ob["partRole"] = role
    return ob


def scene_setup(origin=(0.0, 0.0, 0.0)):
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = 0.001
    sc.unit_settings.length_unit = "MILLIMETERS"
    sc["blender_agent_axes"] = "+Y up; +Z front"
    sc["mdl_bridge_underside"] = list(origin)


def build_scene(sc: dict, offset=(0, 0, 0), lens_name="Lens_L"):
    lens_mat = material("Lens | clear", 1.0, (0.9, 0.9, 0.9, 1.0))
    frame_mat = material("Frame | acetate")
    crystal = material("Frame | crystal", 0.9, (0.8, 0.8, 0.85, 1.0))
    add(lens_name, sc["lens"], "lens", lens_mat, offset, quads=True)
    for name, mesh in sc["parts"].items():
        add(name, mesh, "frame", crystal if name in sc.get("translucent", ()) else frame_mat, offset)


def custom_normals(ob):
    me = ob.data
    n = np.zeros(len(me.loops) * 3, np.float32)
    me.corner_normals.foreach_get("vector", n)
    me.normals_split_custom_set([tuple(x) for x in n.reshape(-1, 3)])
    me.update()


SCENES = {}


def scene(fn):
    SCENES[fn.__name__] = fn
    return fn


@scene
def metal():
    scene_setup()
    build_scene(SF.metal_scene())


@scene
def metal_narrow():
    scene_setup()
    build_scene(SF.metal_scene(0.5))


for _c in ("sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded", "coarse"):
    def _make(c=_c):
        def f():
            scene_setup()
            build_scene(SF.acetate_scene(c, inset=0.10))
        f.__name__ = f"acetate_{c}"
        return f
    scene(_make())


@scene
def acetate_origin():
    scene_setup(ORIGIN_OFFSET)
    build_scene(SF.acetate_scene("sphere", inset=0.10), ORIGIN_OFFSET)


@scene
def metal_origin():
    scene_setup(ORIGIN_OFFSET)
    build_scene(SF.metal_scene(), ORIGIN_OFFSET)


@scene
def crystal():
    scene_setup()
    build_scene(SF.crystal_scene())


@scene
def nylor():
    scene_setup()
    build_scene(SF.nylor_scene())


@scene
def nylor_nocord():
    scene_setup()
    build_scene(SF.nylor_scene(with_cord=False))


@scene
def nylor_thin():
    scene_setup()
    build_scene(SF.nylor_scene(t=1.4))


@scene
def nylor_outline():
    """nylor with the stage-proof cord (horizontal outline normal, 0.30 mm inside): an existing cord off the §5
    groove, which overlaps the grooved lens by ~0.74 mm3 (proof review round 2: seat_lens must say so)."""
    scene_setup()
    build_scene(SF.nylor_scene(cord_frame="outline"))


@scene
def short_channel():
    """semi_rimless, a channel over the top 20% only: seat_lens says partial, the check fails (TRANSITION > 3%)."""
    scene_setup()
    build_scene(SF.short_channel_scene())


@scene
def browline_lifted():
    scene_setup()
    build_scene(SF.browline_lifted_scene())


@scene
def browline():
    scene_setup()
    build_scene(SF.browline_scene())


@scene
def browline_pair():
    """Two eyewires, two brows, two lenses (unhide_eyewire must touch one side only)."""
    scene_setup()
    sc = SF.browline_pair_scene()
    lens_mat = material("Lens | clear", 1.0, (0.9, 0.9, 0.9, 1.0))
    frame_mat = material("Frame | acetate")
    for name, mesh in sc["lenses"].items():
        add(name, mesh, "lens", lens_mat, quads=True)
    for name, mesh in sc["parts"].items():
        add(name, mesh, "frame", frame_mat)


@scene
def custom_normals_acetate():
    scene_setup()
    build_scene(SF.acetate_scene("sphere", inset=0.10))
    custom_normals(bpy.data.objects["Lens_L"])


@scene
def pair_transformed():
    scene_setup(ORIGIN_OFFSET)
    L = SF.base_lens()
    R = SF.mirrored_lens()
    lens_mat = material("Lens | clear", 1.0, (0.9, 0.9, 0.9, 1.0))
    frame_mat = material("Frame | acetate")
    ML = Matrix.Translation((3.0, -1.0, 0.5)) @ Matrix.Rotation(0.35, 4, "Y") @ Matrix.Rotation(-0.2, 4, "X")
    MR = (Matrix.Translation((-2.0, 0.7, -1.2)) @ Matrix.Rotation(-0.3, 4, "Z")
          @ Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)))
    add("Lens_L", L, "lens", lens_mat, ORIGIN_OFFSET, ML, quads=True)
    add("Lens_R", R, "lens", lens_mat, ORIGIN_OFFSET, MR, quads=True)
    add("Rim_L", SF.ring_frame(L, 0.10), "frame", frame_mat, ORIGIN_OFFSET)
    add("Rim_R", SF.ring_frame(R, 0.10), "frame", frame_mat, ORIGIN_OFFSET)



# §10 P4 robustness refusals and multi-body lenses (the WP4 proof stage), each in an acetate ring (inset 0.10 unless
# stated)
@scene
def acetate_open():
    """the lens with 4 front-cap triangles removed: E_OPEN."""
    scene_setup()
    L = SF.base_lens()
    build_scene({"lens": SF.open_lens(L), "parts": {"Rim": SF.ring_frame(L, 0.10)}})


@scene
def acetate_selfx():
    """the lens whose front cap is pushed back through its back cap around the apex (self-intersection)."""
    scene_setup()
    L = SF.base_lens()
    build_scene({"lens": SF.self_intersecting(L), "parts": {"Rim": SF.ring_frame(L, 0.10)}})


@scene
def acetate_notch():
    """a 0.54 mm radius notch in the outline (CONCAVE)."""
    scene_setup()
    L = SF.lens(SF.notched_outline(depth=2.0, width=0.08, theta0=0.0123), t=1.6)
    build_scene({"lens": L, "parts": {"Rim": SF.ring_frame(L, 0.10)}})


@scene
def acetate_wide():
    """an aperture 2.5 mm larger than the lens all round (NEEDS_GROWTH -> E_TOO_MANY_BLOCKED)."""
    scene_setup()
    L = SF.base_lens()
    build_scene({"lens": L, "parts": {"Rim": SF.ring_frame(L, -2.5)}})


@scene
def acetate_fused():
    """two lenses in one object, each in its own ring."""
    scene_setup()
    A, B = SF.base_lens(), SF.mirrored_lens()
    build_scene({"lens": SF.fused_with_edges(A, B),
                 "parts": {"Rim_L": SF.ring_frame(A, 0.10), "Rim_R": SF.ring_frame(B, 0.10)}})


@scene
def acetate_drilled():
    """a drilled lens (a through hole: genus 1)."""
    scene_setup()
    D = SF.drilled()
    build_scene({"lens": D, "parts": {"Rim": SF.ring_frame(D, 0.10)}})


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    names = args[1:] or sorted(SCENES)
    for name in names:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        SCENES[name]()
        bpy.ops.wm.save_as_mainfile(filepath=str(out / f"{name}.blend"), check_existing=False)
        print("built", name)


main()
