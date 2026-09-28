"""glasses_lib (``gl``): helper library for glasses construction programs. Runs INSIDE Blender 5.x.

Conventions (the MODEL frame of the AR contract, authored in millimetres):
  * 1 Blender unit = 1 mm. +X = the viewer's right in the front photo (the wearer's LEFT), +Y = up,
    +Z = toward the front camera. Lenses face +Z; temples extend toward -Z.
  * Every visible object is REGISTERED with a part identity: ``frame`` (front piece and everything attached to it:
    rims, bridge, endpieces, hinges, pads, logo plates), ``temple_R`` / ``temple_L`` (R = +X side), and the lenses
    ``lens_R`` / ``lens_L`` (a pair) or ``lens_C`` (a single shield). A ``component`` label names the piece inside
    the part (``rim``, ``bridge``, ``hinge_R``, ``logo_plate_R`` ...) so a later program can replace one piece.
    Unregistered objects (cutters, guides) are not exported and not rendered unless ``keep_visible``.
  * Materials are created through ``material_*`` so the host knows their PBR values exactly. A material with
    ``transmission > 0`` is a LENS material in the AR runtime, except ``material_translucent`` (crystal or translucent
    acetate), which is allowed on frame and temple parts; hardware (metal) stays opaque; the exporter forces
    single-sided.
  * Only the geometry and materials matter for the export: the host re-origins the model at the bridge underside
    (``set_bridge_underside`` may state it), converts to metres and writes the GLB through the tested exporter.

The library favours plain numpy polygons (N,2) / (N,3) as the exchange format and bmesh for construction.
Everything raises informative exceptions; nothing is silently skipped.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector
from mathutils import geometry as mgeo

PARTS = ("frame", "temple_R", "temple_L", "lens_R", "lens_L", "lens_C")
LENS_PARTS = ("lens_R", "lens_L", "lens_C")
DEFAULT_SMOOTH_ANGLE_DEG = 40.0

EVIDENCE: dict = {}
_NOTES: list[str] = []


# --------------------------------------------------------------------------- scene and registry
def scene() -> bpy.types.Scene:
    return bpy.context.scene


def reset_scene() -> None:
    """Factory-empty scene in millimetres, neutral colour management (renders compare against photos)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = 0.001
    sc.unit_settings.length_unit = "MILLIMETERS"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0.0
    sc.view_settings.gamma = 1.0
    sc.render.engine = "BLENDER_EEVEE"
    _NOTES.clear()


def load_evidence(path) -> dict:
    global EVIDENCE
    with open(path, "r", encoding="utf-8") as f:
        EVIDENCE = json.load(f)
    return EVIDENCE


def note(text: str) -> None:
    """Record a remark for the host log (the author's own observations while building)."""
    _NOTES.append(str(text))


def notes() -> list[str]:
    return list(_NOTES)


def register(obj: bpy.types.Object, part: str, component: str | None = None) -> bpy.types.Object:
    if part not in PARTS:
        raise ValueError(f"Unknown part {part!r}; use one of {PARTS}")
    obj["part"] = part
    obj["component"] = component or obj.name
    return obj


def objects(part: str | None = None, component: str | None = None) -> list[bpy.types.Object]:
    """Registered mesh objects (optionally one part / component), in creation order."""
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH" or "part" not in o:
            continue
        if part is not None and o["part"] != part:
            continue
        if component is not None and o.get("component") != component:
            continue
        out.append(o)
    return out


def delete(target) -> int:
    """Delete objects: an object, a list, a part name or a component name. Returns the count."""
    if isinstance(target, bpy.types.Object):
        objs = [target]
    elif isinstance(target, str):
        objs = objects(part=target) if target in PARTS else objects(component=target)
        if not objs:
            objs = [o for o in bpy.data.objects if o.name == target]
    else:
        objs = list(target)
    n = 0
    for o in objs:
        mesh = o.data if o.type == "MESH" else None
        bpy.data.objects.remove(o, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        n += 1
    return n


def _link(obj: bpy.types.Object) -> None:
    bpy.context.scene.collection.objects.link(obj)


def new_mesh_object(name: str, verts, faces, part: str, component: str | None = None, *,
                    uvs=None, smooth: bool = True, smooth_angle_deg: float = DEFAULT_SMOOTH_ANGLE_DEG,
                    remove_doubles_mm: float | None = 1e-4) -> bpy.types.Object:
    """Mesh object from vertex coordinates (N,3) and faces (lists of vertex indices, any polygon size).
    ``uvs``: optional per-face list of (u, v) tuples matching ``faces``."""
    me = bpy.data.meshes.new(name)
    verts = [tuple(map(float, v)) for v in np.asarray(verts, float).reshape(-1, 3)]
    faces = [tuple(int(i) for i in f) for f in faces]
    me.from_pydata(verts, [], faces)
    me.validate(verbose=False)
    me.update()
    if uvs is not None:
        layer = me.uv_layers.new(name="UVMap")
        loop_uv = np.zeros((len(me.loops), 2), float)
        for poly, face_uv in zip(me.polygons, uvs):
            for li, uv in zip(range(poly.loop_start, poly.loop_start + poly.loop_total), face_uv):
                loop_uv[li] = uv
        layer.data.foreach_set("uv", loop_uv.ravel())
    obj = bpy.data.objects.new(name, me)
    _link(obj)
    register(obj, part, component)
    if remove_doubles_mm:
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=remove_doubles_mm)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(me)
        bm.free()
    if smooth:
        shade_smooth(obj, smooth_angle_deg)
    return obj


def from_bmesh(name: str, bm: bmesh.types.BMesh, part: str, component: str | None = None, *,
               smooth: bool = True, smooth_angle_deg: float = DEFAULT_SMOOTH_ANGLE_DEG) -> bpy.types.Object:
    me = bpy.data.meshes.new(name)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    _link(obj)
    register(obj, part, component)
    if smooth:
        shade_smooth(obj, smooth_angle_deg)
    return obj


def bmesh_of(obj: bpy.types.Object) -> bmesh.types.BMesh:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    return bm


def write_bmesh(obj: bpy.types.Object, bm: bmesh.types.BMesh) -> None:
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def shade_smooth(obj: bpy.types.Object, angle_deg: float = DEFAULT_SMOOTH_ANGLE_DEG) -> None:
    """Smooth shading with hard edges above ``angle_deg`` (an Edge Split modifier: robust headless, exported as
    split vertices, which the host re-welds for its own crease normals)."""
    me = obj.data
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    mod = obj.modifiers.get("mdl_smooth") or obj.modifiers.new("mdl_smooth", "EDGE_SPLIT")
    mod.split_angle = math.radians(angle_deg)
    mod.use_edge_angle = True
    mod.use_edge_sharp = True


def apply_modifiers(obj: bpy.types.Object) -> bpy.types.Object:
    """Bake the modifier stack into the mesh data (keeps the object registered). shade_smooth's edge split is not
    baked: it stays a live modifier, so the mesh stays connected across its hard edges (a baked split leaves every
    smoothing patch a separate island: bmesh operations, solidify and normal recalculation then see an open mesh)."""
    smooth = obj.modifiers.get("mdl_smooth")
    smooth_angle = math.degrees(smooth.split_angle) if smooth is not None and smooth.type == "EDGE_SPLIT" else None
    if smooth_angle is not None:
        smooth.show_viewport = False                 # the evaluated mesh below leaves the split out
        smooth.show_render = False
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    old = obj.data
    obj.modifiers.clear()
    obj.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    if smooth_angle is not None:
        shade_smooth(obj, smooth_angle)
    return obj


def apply_transform(obj: bpy.types.Object) -> bpy.types.Object:
    me = obj.data
    me.transform(obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    me.update()
    return obj


def duplicate(obj: bpy.types.Object, name: str, part: str | None = None, component: str | None = None) -> bpy.types.Object:
    new = obj.copy()
    new.data = obj.data.copy()
    new.name = name
    new.data.name = name
    _link(new)
    register(new, part or obj["part"], component or name)
    return new


def mirror_x(obj: bpy.types.Object, name: str, part: str, component: str | None = None) -> bpy.types.Object:
    """A mirrored copy across the symmetry plane x = 0 (the left twin of a right-side piece), normals fixed."""
    new = duplicate(obj, name, part, component)
    apply_transform(new)
    bm = bmesh_of(new)
    for v in bm.verts:
        v.co.x = -v.co.x
    bmesh.ops.reverse_faces(bm, faces=bm.faces)
    write_bmesh(new, bm)
    return new


def join(objs, name: str, part: str, component: str | None = None) -> bpy.types.Object:
    """Merge several objects (modifiers applied) into one registered object; the inputs are removed."""
    bm = bmesh.new()
    dg = bpy.context.evaluated_depsgraph_get()
    mats = []
    for o in objs:
        ev = o.evaluated_get(dg)
        me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        me.transform(o.matrix_world)
        offset = len(mats)
        for m in me.materials:
            mats.append(m)
        tmp = bmesh.new()
        tmp.from_mesh(me)
        for f in tmp.faces:
            f.material_index += offset
        tmp.to_mesh(me)
        tmp.free()
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    obj = from_bmesh(name, bm, part, component)
    for m in mats:
        obj.data.materials.append(m)
    delete(list(objs))
    return obj


def translate(obj: bpy.types.Object, dx: float = 0, dy: float = 0, dz: float = 0) -> bpy.types.Object:
    obj.location = obj.location + Vector((dx, dy, dz))
    return obj


def rotate(obj: bpy.types.Object, axis: str, angle_deg: float, pivot=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    """Rotate the object's mesh about a world axis through ``pivot`` (baked into the mesh data)."""
    apply_transform(obj)
    R = Matrix.Rotation(math.radians(angle_deg), 4, axis.upper())
    T = Matrix.Translation(Vector(pivot))
    obj.data.transform(T @ R @ T.inverted())
    obj.data.update()
    return obj


def scale(obj: bpy.types.Object, sx: float, sy: float | None = None, sz: float | None = None, pivot=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    apply_transform(obj)
    S = Matrix.Diagonal(Vector((sx, sx if sy is None else sy, sx if sz is None else sz, 1.0)))
    T = Matrix.Translation(Vector(pivot))
    obj.data.transform(T @ S @ T.inverted())
    obj.data.update()
    return obj


# --------------------------------------------------------------------------- 2D outline utilities (numpy)
def as_poly(poly) -> np.ndarray:
    p = np.asarray(poly, float)
    if p.ndim != 2 or p.shape[0] < 3 or p.shape[1] not in (2, 3):
        raise ValueError("A polygon is an (N,2) or (N,3) array with at least three points")
    if np.allclose(p[0], p[-1]) and len(p) > 3:
        p = p[:-1]
    return p


def signed_area(poly) -> float:
    p = as_poly(poly)[:, :2]
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def ensure_ccw(poly) -> np.ndarray:
    p = as_poly(poly)
    return p if signed_area(p) > 0 else p[::-1].copy()


def perimeter(poly) -> float:
    p = as_poly(poly)
    return float(np.linalg.norm(np.roll(p, -1, 0) - p, axis=1).sum())


def resample_closed(poly, n: int) -> np.ndarray:
    """``n`` points evenly spaced along the closed polyline."""
    p = as_poly(poly)
    seg = np.linalg.norm(np.roll(p, -1, 0) - p, axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = s[-1]
    t = np.linspace(0.0, total, n, endpoint=False)
    idx = np.searchsorted(s, t, side="right") - 1
    idx = np.clip(idx, 0, len(p) - 1)
    a = p[idx]
    b = p[(idx + 1) % len(p)]
    w = ((t - s[idx]) / np.maximum(seg[idx], 1e-12))[:, None]
    return a + (b - a) * w


def resample_open(poly, n: int) -> np.ndarray:
    p = np.asarray(poly, float)
    seg = np.linalg.norm(p[1:] - p[:-1], axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0.0, s[-1], n)
    out = np.empty((n, p.shape[1]))
    for k in range(p.shape[1]):
        out[:, k] = np.interp(t, s, p[:, k])
    return out


def smooth_closed(poly, iterations: int = 2, alpha: float = 0.5) -> np.ndarray:
    p = as_poly(poly).copy()
    for _ in range(iterations):
        p = (1 - alpha) * p + alpha * 0.5 * (np.roll(p, 1, 0) + np.roll(p, -1, 0))
    return p


def outward_normals(poly) -> np.ndarray:
    """Unit outward normals per vertex of a CCW closed polygon (2D)."""
    p = ensure_ccw(poly)[:, :2]
    t = np.roll(p, -1, 0) - np.roll(p, 1, 0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    return np.column_stack([t[:, 1], -t[:, 0]])


def offset_closed(poly, distance: float, n_out: int | None = None, smooth_iters: int = 1) -> np.ndarray:
    """Offset a closed 2D outline by ``distance`` (positive = outward). Vertex-normal offset followed by a light
    smoothing; adequate for smooth rim outlines (sharp concave corners are rounded slightly). A concave stretch that
    turns more tightly than ``distance`` folds into a small loop: smooth such an outline first."""
    p = ensure_ccw(poly)[:, :2]
    p = resample_closed(p, n_out or len(p))
    q = p + outward_normals(p) * float(distance)
    if smooth_iters:
        q = smooth_closed(q, smooth_iters, 0.5)
    return q


ROUNDED_MIN_PER_CORNER = 6          # rounded_rect: at least 6 points per 90 deg corner, so no vertex turns more than 15 deg
TUBE_PER_CORNER = 8                 # tube_along_path: 8 points per corner (11.25 deg), well below the export's 40 deg crease
TUBE_MIN_RADIUS_MM = 0.05           # tube_along_path: a square section is built with this rounding (its points stay apart)


def rounded_rect(width: float, height: float, radius: float, n: int = 96, center=(0.0, 0.0)) -> np.ndarray:
    """A rounded rectangle of 4 * max(6, n // 4) points (``n`` when it is a multiple of 4 and at least 24): each corner
    arc carries its points at the middles of equal sub-arcs, so the outline turns by at most 90 / per-corner degrees at
    any vertex (15 deg at the floor) and the straight sides stay parallel to the axes. A coarse corner (4 points, 22.5 deg
    steps, 34 deg at the arc ends before 2026-09-28) turned by more than the exporter's smoothing allows in a few steps."""
    r = min(radius, width / 2, height / 2)
    cx, cy = center
    corners = [(cx + width / 2 - r, cy + height / 2 - r), (cx - width / 2 + r, cy + height / 2 - r),
               (cx - width / 2 + r, cy - height / 2 + r), (cx + width / 2 - r, cy - height / 2 + r)]
    pts = []
    per_corner = max(ROUNDED_MIN_PER_CORNER, n // 4)
    step = math.pi / 2 / per_corner
    for k, (x, y) in enumerate(corners):
        a0 = math.radians(90 * k)
        for a in a0 + step * (np.arange(per_corner) + 0.5):
            pts.append((x + r * math.cos(a), y + r * math.sin(a)))
    return np.asarray(pts)


def ellipse(width: float, height: float, n: int = 96, center=(0.0, 0.0)) -> np.ndarray:
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([center[0] + width / 2 * np.cos(a), center[1] + height / 2 * np.sin(a)])


def superellipse(width: float, height: float, exponent: float = 3.0, n: int = 128, center=(0.0, 0.0)) -> np.ndarray:
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    c, s = np.cos(a), np.sin(a)
    x = np.sign(c) * np.abs(c) ** (2 / exponent) * width / 2
    y = np.sign(s) * np.abs(s) ** (2 / exponent) * height / 2
    return np.column_stack([center[0] + x, center[1] + y])


def point_in_polygon(points, poly) -> np.ndarray:
    """Even-odd test for (M,2) points against a closed 2D polygon."""
    pts = np.asarray(points, float)[:, :2]
    p = as_poly(poly)[:, :2]
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), bool)
    j = len(p) - 1
    for i in range(len(p)):
        xi, yi = p[i]
        xj, yj = p[j]
        cond = (yi > y) != (yj > y)
        xint = (xj - xi) * (y - yi) / ((yj - yi) if yj != yi else 1e-30) + xi
        inside ^= cond & (x < xint)
        j = i
    return inside


def evidence_outline(key: str, *keys) -> np.ndarray:
    """A polygon from the evidence JSON, e.g. ``evidence_outline('front', 'lenses', 0, 'outline_mm')``."""
    node = EVIDENCE.get(key)
    for k in keys:
        node = node[k]
    return np.asarray(node, float)


# --------------------------------------------------------------------------- triangulation / solids
def triangulate_region(outer, holes=(), interior_spacing: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Constrained Delaunay triangulation of a 2D region: (verts (N,2), tris (M,3)). ``interior_spacing`` adds a
    grid of interior points (needed before bending or displacing the surface)."""
    outer = ensure_ccw(outer)[:, :2]
    hole_list = [ensure_ccw(h)[:, :2][::-1] for h in holes]      # holes clockwise (orientation is informative only)
    verts = [outer] + hole_list
    coords = np.vstack(verts)
    edges = []
    start = 0
    for ring in verts:
        n = len(ring)
        edges += [(start + i, start + (i + 1) % n) for i in range(n)]
        start += n
    extra = np.zeros((0, 2))
    if interior_spacing:
        lo, hi = outer.min(0), outer.max(0)
        xs = np.arange(lo[0] + interior_spacing / 2, hi[0], interior_spacing)
        ys = np.arange(lo[1] + interior_spacing / 2, hi[1], interior_spacing)
        grid = np.array([(x, y) for y in ys for x in xs]) if len(xs) and len(ys) else np.zeros((0, 2))
        if len(grid):
            keep = point_in_polygon(grid, outer)
            for h in hole_list:
                keep &= ~point_in_polygon(grid, h)
            # keep away from the boundary by half a spacing
            grid = grid[keep]
            if len(grid):
                d = _dist_to_polylines(grid, verts)
                grid = grid[d > interior_spacing * 0.45]
            extra = grid
    all_coords = np.vstack([coords, extra]) if len(extra) else coords
    vc = [Vector((float(x), float(y))) for x, y in all_coords]
    out = mgeo.delaunay_2d_cdt(vc, edges, [], 1, 1e-6)
    v_out = np.array([(v.x, v.y) for v in out[0]])
    faces = [list(f) for f in out[2] if len(f) >= 3]
    tris = []
    for f in faces:
        for k in range(1, len(f) - 1):
            tris.append((f[0], f[k], f[k + 1]))
    tris = np.asarray(tris, int).reshape(-1, 3)
    # remove triangles inside holes (the CDT fills the whole hull between constraints)
    if len(tris):
        c = v_out[tris].mean(1)
        keep = point_in_polygon(c, outer)
        for h in hole_list:
            keep &= ~point_in_polygon(c, h)
        tris = tris[keep]
    return v_out, tris


def _dist_to_polylines(points, rings) -> np.ndarray:
    pts = np.asarray(points, float)
    best = np.full(len(pts), np.inf)
    for ring in rings:
        a = ring
        b = np.roll(ring, -1, 0)
        ab = b - a
        L2 = np.maximum((ab ** 2).sum(1), 1e-12)
        for i in range(len(a)):
            t = np.clip(((pts - a[i]) @ ab[i]) / L2[i], 0, 1)
            proj = a[i] + t[:, None] * ab[i]
            best = np.minimum(best, np.linalg.norm(pts - proj, axis=1))
    return best


def plate_with_holes(outer, holes, z_front: float, thickness: float, name: str, part: str,
                     component: str | None = None, *, interior_spacing: float | None = None,
                     smooth: bool = True) -> bpy.types.Object:
    """A closed prism: the 2D region (outer minus holes) as the front face at ``z_front`` and the back face at
    ``z_front - thickness``, walls along -Z. The classic acetate front plate before bevelling/wrapping."""
    if thickness <= 0:
        raise ValueError("thickness must be positive")
    v2, tris = triangulate_region(outer, holes, interior_spacing)
    n = len(v2)
    front = np.column_stack([v2, np.full(n, z_front)])
    back = np.column_stack([v2, np.full(n, z_front - thickness)])
    verts = np.vstack([front, back])
    faces = [tuple(int(i) for i in t) for t in tris]                       # front, CCW seen from +Z
    faces += [tuple(int(i) + n for i in t[::-1]) for t in tris]             # back, reversed
    rings = [ensure_ccw(outer)[:, :2]] + [ensure_ccw(h)[:, :2][::-1] for h in holes]
    # wall quads follow the boundary vertices in the CDT output (they keep their input order first)
    start = 0
    for ring in rings:
        m = len(ring)
        for i in range(m):
            a, b = start + i, start + (i + 1) % m
            faces.append((a, a + n, b + n, b))
        start += m
    obj = new_mesh_object(name, verts, faces, part, component, smooth=smooth)
    return obj


def extrude_outline(outline, z_front: float, thickness: float, name: str, part: str, component: str | None = None,
                    **kw) -> bpy.types.Object:
    return plate_with_holes(outline, [], z_front, thickness, name, part, component, **kw)


FRONT_FLARE_ANGLE_RAD = 0.5        # the hinge shoulder: 29 degrees above the temporal horizontal, seen from the lens centre
FRONT_FLARE_SIGMA_RAD = 0.3        # its width (17 degrees)
FRONT_NASAL_COS = -0.5             # a rim vertex is nasal when its outward normal points within 60 degrees of the centre line


def _orient(p, q, r):
    return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])


def _proper_crossings(a0, a1, b0, b1, block: int = 256) -> np.ndarray:
    """Index pairs (i, j), (K,2), where segment a0[i]-a1[i] properly crosses segment b0[j]-b1[j] (the interiors meet at
    one point; a shared end or a collinear touch does not count). Vectorised in row blocks of ``block``."""
    C, D = np.asarray(b0, float)[None, :, :2], np.asarray(b1, float)[None, :, :2]
    a0, a1 = np.asarray(a0, float)[:, :2], np.asarray(a1, float)[:, :2]
    out = [np.zeros((0, 2), np.int64)]
    for s in range(0, len(a0), block):
        A, B = a0[s:s + block, None], a1[s:s + block, None]
        hit = (_orient(A, B, C) * _orient(A, B, D) < 0) & (_orient(C, D, A) * _orient(C, D, B) < 0)
        i, j = np.nonzero(hit)
        out.append(np.column_stack([i + s, j]).astype(np.int64))
    return np.vstack(out)


SEGMENT_CONTACT_EPS_MM = 1e-6      # the weld epsilon of triangulate_region's CDT: closer than this, two rings share a point


def _segment_contacts(a0, a1, b0, b1, eps_mm: float = SEGMENT_CONTACT_EPS_MM, block: int = 256) -> np.ndarray:
    """Index pairs (i, j), (K,2), where the closed segment a0[i]-a1[i] meets the closed segment b0[j]-b1[j] at all: a
    proper crossing, a T-touch (an end of one within ``eps_mm`` of the other), a shared end or a collinear overlap.
    Between two rings of one ``plate_with_holes`` region every one of these builds an open or non-manifold plate (the
    CDT splits the constraint edge that the wall quad still spans). Vectorised in row blocks of ``block``."""
    C, D = np.asarray(b0, float)[None, :, :2], np.asarray(b1, float)[None, :, :2]
    a0, a1 = np.asarray(a0, float)[:, :2], np.asarray(a1, float)[:, :2]
    eps2 = float(eps_mm) ** 2

    def near(p, q, r):             # r within eps of the closed segment p-q (a degenerate segment is its point)
        pq, pr = q - p, r - p
        t = np.clip((pq * pr).sum(-1) / np.maximum((pq * pq).sum(-1), 1e-300), 0.0, 1.0)
        d = pr - t[..., None] * pq
        return (d * d).sum(-1) <= eps2

    out = [np.zeros((0, 2), np.int64)]
    for s in range(0, len(a0), block):
        A, B = a0[s:s + block, None], a1[s:s + block, None]
        hit = (_orient(A, B, C) * _orient(A, B, D) < 0) & (_orient(C, D, A) * _orient(C, D, B) < 0)
        hit |= near(A, B, C) | near(A, B, D) | near(C, D, A) | near(C, D, B)
        i, j = np.nonzero(hit)
        out.append(np.column_stack([i + s, j]).astype(np.int64))
    return np.vstack(out)


def front_outline(lens_outlines, *, rim_width_mm: float, bridge_top_crown_mm: float, bridge_bottom_mm: float,
                  endpiece_flare_mm: float = 0.0, n: int = 192, bridge_top_mm: float | None = None,
                  bridge_bottom_attach_mm: float | None = None) -> np.ndarray:
    """The one-piece closed front silhouette (N,2) CCW around a PAIR of lens outlines: each lens offset outward by
    ``rim_width_mm`` (the rim), the two rims joined by a crowned bridge, an optional endpiece flare on the temporal
    sides. Feed it to ``plate_with_holes(outline, lens_outlines, ...)`` for the classic acetate front as ONE closed
    solid (no boolean union of rims and bridge: that left seams and a stray material slot). ``bridge_bottom_mm`` is
    the y (mm) of the bridge underside at x = 0 (the natural ``set_bridge_underside`` point); the underside rises from
    the rims to it as 1 - |u|^4. ``bridge_top_crown_mm`` is how far the top edge rises at x = 0 above where it leaves
    the rims (1 - |u|^6, a flat crown; 0 = straight). The bridge leaves each rim at the nasal vertices nearest
    y = ``bridge_top_mm`` (default: the upper nasal corner, where the rim's outward normal points 45 degrees nasal-up)
    and y = ``bridge_bottom_attach_mm`` (default ``bridge_bottom_mm``: a flat underside). ``endpiece_flare_mm`` pushes
    the temporal side outward along x around the hinge shoulder (a Gaussian bump centred 29 degrees above the temporal
    horizontal, sigma 17 degrees, as seen from the lens centre). ``n`` points per rim. Pure numpy, deterministic, and
    mirror-symmetric for mirror-symmetric input. The result is always a simple polygon enclosing both lenses; raises
    ValueError instead for anything but two outlines (a shield front is one ``extrude_outline``), a negative
    ``endpiece_flare_mm``, rims that overlap anywhere, at the bridge or below or above it (reduce ``rim_width_mm`` or
    move the lenses apart), a silhouette that self-intersects or touches itself (the rim offset folds where a lens outline
    turns concave more tightly than ``rim_width_mm``: smooth the lens outline with
    ``gl.smooth_closed(gl.resample_closed(lens, 512), iterations=...)`` (resample first: smoothing a coarse 64-point
    outline shrinks it, 20 passes about 4%, against under 0.1% at 512 points), more passes until it clears, and build the lens from the same smoothed outline, or reduce the
    rim; or a bridge edge crosses a rim: move the attachment heights), a lens edge that crosses or touches the
    silhouette anywhere, between its vertices too (typically the underside climbing steeply from a low
    ``bridge_bottom_attach_mm`` to a higher ``bridge_bottom_mm`` cuts the lens's nasal edge above it: raise
    bridge_bottom_attach_mm toward bridge_bottom_mm or lower bridge_bottom_mm; moving the attachment down cuts deeper), or
    attachments that cannot be found. Each message says where (x, y in mm) and what to change."""
    if len(lens_outlines) != 2:
        raise ValueError(f"front_outline needs exactly two lens outlines (R and L), got {len(lens_outlines)}; a shield front is one extrude_outline")
    rim = float(rim_width_mm)
    if not rim > 0:
        raise ValueError(f"rim_width_mm must be positive, got {rim_width_mm}")
    crown = float(bridge_top_crown_mm)
    if crown < 0:
        raise ValueError(f"bridge_top_crown_mm must be >= 0, got {bridge_top_crown_mm}")
    if int(n) < 32:
        raise ValueError(f"n must be at least 32, got {n}")
    y_bottom = float(bridge_bottom_mm)
    y_top = None if bridge_top_mm is None else float(bridge_top_mm)
    y_battach = y_bottom if bridge_bottom_attach_mm is None else float(bridge_bottom_attach_mm)
    flare = float(endpiece_flare_mm)
    if not flare >= 0:
        raise ValueError(f"endpiece_flare_mm must be >= 0 (it pushes the temporal side outward), got {endpiece_flare_mm}")
    lenses = [ensure_ccw(p)[:, :2] for p in lens_outlines]
    lenses.sort(key=lambda p: float(p[:, 0].mean()), reverse=True)          # R (+x) first, then L
    arcs = []
    for sign, lens in ((1.0, lenses[0]), (-1.0, lenses[1])):
        # each side in its own +x frame (nasal side toward -x) from a canonical start vertex, so the two sides of a
        # mirror-symmetric pair go through identical arithmetic
        p = lens.copy()
        p[:, 0] *= sign
        p = ensure_ccw(p)
        p = np.roll(p, -int(np.argmax(p[:, 0])), axis=0)
        outer = offset_closed(p, rim, n_out=int(n))
        if flare:
            c = p.mean(0)
            half = np.maximum((p.max(0) - p.min(0)) / 2, 1e-9)
            theta = np.arctan2((outer[:, 1] - c[1]) / half[1], (outer[:, 0] - c[0]) / half[0])
            outer[:, 0] += flare * np.exp(-((theta - FRONT_FLARE_ANGLE_RAD) / FRONT_FLARE_SIGMA_RAD) ** 2)
        normals = outward_normals(outer)
        nasal = np.nonzero(normals[:, 0] < FRONT_NASAL_COS)[0]
        if len(nasal) < 3:
            raise ValueError("front_outline: no nasal side found on a rim (is the outline a closed lens shape?)")
        side = "R" if sign > 0 else "L"
        if y_top is None:
            corner = nasal[np.argmax(normals[nasal] @ np.array([-math.sqrt(0.5), math.sqrt(0.5)]))]
            it = int(corner)
        else:
            it = int(nasal[np.argmin(np.abs(outer[nasal, 1] - y_top))])
        ib = int(nasal[np.argmin(np.abs(outer[nasal, 1] - y_battach))])
        if it == ib or outer[it, 1] <= outer[ib, 1]:
            raise ValueError(f"front_outline: the bridge attachments on rim {side} coincide or are inverted (top at y={outer[it, 1]:.2f}, "
                             f"bottom at y={outer[ib, 1]:.2f}); set bridge_top_mm above bridge_bottom_attach_mm within the nasal edge")
        if not it < ib:
            raise ValueError(f"front_outline: unexpected vertex order on rim {side}; the outline may be self-intersecting")
        arc = np.vstack([outer[ib:], outer[:it + 1]])     # CCW from the bottom attachment through the temporal side to the top one
        if len(arc) < len(outer) // 2:
            raise ValueError(f"front_outline: the bridge would replace more than half of rim {side}; check the attachment heights")
        if sign < 0:
            arc = arc[::-1].copy()
            arc[:, 0] *= -1.0
        arcs.append((arc, perimeter(outer) / int(n)))
    arc_r, spacing = arcs[0]
    arc_l, _ = arcs[1]
    (xtr, ytr), (xbr, ybr) = arc_r[-1], arc_r[0]
    (xtl, ytl), (xbl, ybl) = arc_l[0], arc_l[-1]
    if xtr <= xtl or xbr <= xbl:
        raise ValueError(f"front_outline: the rim-offset apertures overlap at the bridge (top gap {xtr - xtl:.2f} mm, bottom gap "
                         f"{xbr - xbl:.2f} mm): reduce rim_width_mm or move the lenses apart")
    # the attachments can clear while the rims still cross below or above the bridge (a lens whose nasal-most point
    # lies under the attachments): the two arcs must not meet anywhere
    hits = _segment_contacts(arc_r[:-1], arc_r[1:], arc_l[:-1], arc_l[1:])
    if len(hits):
        at = 0.5 * (arc_r[hits[:, 0]] + arc_r[hits[:, 0] + 1])
        closest = float(np.sqrt(((lenses[0][:, None, :] - lenses[1][None, :, :]) ** 2).sum(-1)).min())
        raise ValueError(f"front_outline: the rims overlap: rim R and rim L cross {len(hits)} times between y={at[:, 1].min():.2f} and "
                         f"y={at[:, 1].max():.2f} mm (near x={at[0, 0]:.2f}), away from the bridge attachments; the lenses are "
                         f"{closest:.2f} mm apart at their closest and two rims need more than 2 x rim_width_mm = {2 * rim:g} mm: "
                         "reduce rim_width_mm or move the lenses apart")
    if 0.5 * (ytr + ytl) + crown <= y_bottom:
        raise ValueError(f"front_outline: the bridge underside (y={y_bottom}) is above its top edge (y={0.5 * (ytr + ytl) + crown:.2f})")

    def span(a, b, rise: float, exponent: int) -> np.ndarray:
        m = max(3, int(round(abs(b[0] - a[0]) / spacing)))
        t = np.linspace(0.0, 1.0, m + 2)[1:-1]
        bump = 1.0 - np.abs(2.0 * t - 1.0) ** exponent
        return np.column_stack([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t + rise * bump])

    top = span((xtr, ytr), (xtl, ytl), crown, 6)
    bottom = span((xbl, ybl), (xbr, ybr), y_bottom - 0.5 * (ybl + ybr), 4)
    outline = np.vstack([arc_r, top, arc_l, bottom])
    if signed_area(outline) <= 0:
        raise ValueError("front_outline: the assembled silhouette is not counter-clockwise; check the lens outlines")
    # the silhouette must be a simple polygon: plate_with_holes builds an OPEN front from a self-intersecting one, and
    # from one that merely touches itself (the CDT splits the touched edge under the wall quad)
    m = len(outline)
    nxt = np.roll(outline, -1, 0)
    bounds = np.cumsum([len(arc_r), len(top), len(arc_l), len(bottom)])
    pieces = ("rim R", "the bridge top edge", "rim L", "the bridge underside")

    def piece(k: int) -> str:
        return pieces[int(np.searchsorted(bounds, k, side="right"))]

    hits = _segment_contacts(outline, nxt, outline, nxt)
    hits = hits[(hits[:, 1] - hits[:, 0] > 1) & ~((hits[:, 0] == 0) & (hits[:, 1] == m - 1))]
    if len(hits):
        i, j = (int(k) for k in hits[0])
        pi, pj = piece(i), piece(j)
        x, y = 0.5 * (outline[i] + nxt[i])
        if pi == pj and pi.startswith("rim"):
            advice = (f"{pi}'s offset folds where the lens outline turns concave more tightly than rim_width_mm ({rim:g} mm): smooth "
                      "the lens outline with gl.smooth_closed(gl.resample_closed(lens, 512), iterations=...) (resample first, or the "
                      "smoothing shrinks a coarse outline), raising the passes until it clears, and build "
                      "the lens from the same smoothed outline; or reduce rim_width_mm")
        elif pi.startswith("rim") and pj.startswith("rim"):
            advice = "the rims overlap: reduce rim_width_mm or move the lenses apart"
        else:
            advice = (f"{pi} crosses {pj}: move bridge_top_mm / bridge_bottom_attach_mm along the nasal rim, lower bridge_top_crown_mm "
                      "or change bridge_bottom_mm (a fold in a rim can cause it too: rim_width_mm, smoother lens outline)")
        raise ValueError(f"front_outline: the assembled silhouette self-intersects or touches itself ({len(hits)} contact(s); the first near x={x:.2f}, "
                         f"y={y:.2f} mm): {advice}")
    # every lens EDGE must clear the silhouette, a touch included: a 6-point hexagon or a 4-point diamond is cut between
    # its vertices with all of them still inside, and plate_with_holes then builds an open front
    y_battach_rims = 0.5 * (ybl + ybr)
    climb = y_bottom - y_battach_rims
    for side, lens in (("R", lenses[0]), ("L", lenses[1])):
        hits = _segment_contacts(lens, np.roll(lens, -1, 0), outline, nxt)
        outside = np.nonzero(~point_in_polygon(lens, outline))[0]
        if not len(hits) and not len(outside):
            continue
        if len(hits):
            k = int(hits[0, 1])                       # a silhouette edge (~1 mm): a low-vertex lens edge spans the lens
            x, y = 0.5 * (outline[k] + nxt[k])
            by = piece(k)
            where = f"{len(hits)} lens edge contact(s) with the silhouette, the first with {by}"
        else:
            x, y = lens[outside[0]]
            by = None
            where = f"{len(outside)} lens vertices outside the silhouette"
        if by == "the bridge underside" and climb > 0:
            advice = (f"the underside climbs {climb:.2f} mm from its rim attachments (y={y_battach_rims:.2f}) to bridge_bottom_mm = "
                      f"{y_bottom:g} at x = 0 too steeply to clear the lens's nasal edge above them: raise bridge_bottom_attach_mm (now "
                      f"{y_battach:g}) toward bridge_bottom_mm, or lower bridge_bottom_mm; moving the attachment down cuts deeper")
        elif by == "the bridge underside":
            advice = (f"the underside drops {-climb:.2f} mm from its rim attachments (y={y_battach_rims:.2f}) to bridge_bottom_mm = "
                      f"{y_bottom:g} at x = 0 through the lens: lower bridge_bottom_attach_mm (now {y_battach:g}) toward "
                      "bridge_bottom_mm, or raise bridge_bottom_mm")
        elif by == "the bridge top edge":
            advice = (f"the top edge leaves the rims (y={ytr:.2f} R, {ytl:.2f} L) below part of the lens's upper nasal edge: move "
                      "bridge_top_mm up along the nasal rim or raise bridge_top_crown_mm")
        elif by is not None:
            advice = (f"{by} runs into the lens: the rim offset folds or the rims crowd each other; smooth the lens outline "
                      "(gl.smooth_closed on gl.resample_closed(lens, 512)), reduce rim_width_mm or move the lenses apart")
        else:
            advice = "check the lens outlines: a lens lies outside the rims it was offset from"
        raise ValueError(f"front_outline: lens {side} crosses the front silhouette near x={x:.2f}, y={y:.2f} mm ({where}): {advice}")
    return outline


def sweep_profile(path, profile, name: str, part: str, component: str | None = None, *, closed: bool = True,
                  up=(0.0, 0.0, 1.0), smooth: bool = True, cap: bool = True) -> bpy.types.Object:
    """Sweep a 2D cross-section ``profile`` (K,2) along a 3D ``path`` (N,3). Profile axis a = the path's in-plane
    outward normal (perpendicular to the tangent and to ``up``), axis b = ``up``. For a rim: path = the lens
    outline in the XY plane at the rim's mid depth, profile = the rim cross-section (a across the rim, b along Z)."""
    P = np.asarray(path, float)
    if P.shape[1] == 2:
        P = np.column_stack([P, np.zeros(len(P))])
    prof = np.asarray(profile, float)
    K = len(prof)
    upv = np.asarray(up, float)
    upv /= np.linalg.norm(upv)
    N = len(P)
    # a path running along ``up`` (a temple along -Z with up = +Z) has no in-plane normal: fall back to +Y then +X
    tangents = np.array([(P[(i + 1) % N] - P[i - 1]) if closed else (P[min(i + 1, N - 1)] - P[max(i - 1, 0)]) for i in range(N)])
    tangents /= np.maximum(np.linalg.norm(tangents, axis=1, keepdims=True), 1e-12)
    if np.abs(tangents @ upv).max() > 0.95:
        for cand in (np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0])):
            if np.abs(tangents @ cand).max() < 0.95:
                upv = cand
                break
    verts = []
    for i in range(N):
        t = tangents[i]
        a = np.cross(t, upv)
        a /= max(np.linalg.norm(a), 1e-12)
        b = np.cross(a, t)
        b /= max(np.linalg.norm(b), 1e-12)
        for (pa, pb) in prof:
            verts.append(P[i] + pa * a + pb * b)
    faces = []
    rings = N if closed else N - 1
    for i in range(rings):
        j = (i + 1) % N
        for k in range(K):
            k2 = (k + 1) % K
            faces.append((i * K + k, j * K + k, j * K + k2, i * K + k2))
    if not closed and cap:
        faces.append(tuple(range(K))[::-1])
        faces.append(tuple((N - 1) * K + k for k in range(K)))
    return new_mesh_object(name, np.asarray(verts), faces, part, component, smooth=smooth)


def loft(sections, name: str, part: str, component: str | None = None, *, cap: bool = True,
         smooth: bool = True, closed_loop: bool = False) -> bpy.types.Object:
    """Skin a sequence of closed 3D cross-sections (each (K,3), same K, consistently ordered) into a tube.
    Temples, bridges and endpieces with varying cross-sections are lofts."""
    S = [np.asarray(s, float) for s in sections]
    K = len(S[0])
    if any(len(s) != K for s in S):
        raise ValueError(f"All loft sections need the same number of points, got {sorted({len(s) for s in S})} (a rounded "
                         "section_rect / rounded_rect has 4 * max(6, n // 4) points; section_ellipse has n)")
    N = len(S)
    verts = np.vstack(S)
    faces = []
    rings = N if closed_loop else N - 1
    for i in range(rings):
        j = (i + 1) % N
        for k in range(K):
            k2 = (k + 1) % K
            faces.append((i * K + k, j * K + k, j * K + k2, i * K + k2))
    if cap and not closed_loop:
        faces.append(tuple(range(K))[::-1])
        faces.append(tuple((N - 1) * K + k for k in range(K)))
    return new_mesh_object(name, verts, faces, part, component, smooth=smooth)


def smooth_path(points, n: int = 40, iterations: int = 3, keep_ends: bool = True) -> np.ndarray:
    """An open 3D polyline through a few control points, resampled to ``n`` points and Laplacian-smoothed (ends
    fixed). Use for temple paths (hinge -> straight run -> bend -> tip), pad arms, wire bridges."""
    P = resample_open(np.asarray(points, float), n)
    for _ in range(iterations):
        Q = P.copy()
        Q[1:-1] = 0.5 * P[1:-1] + 0.25 * (P[:-2] + P[2:])
        if not keep_ends:
            Q[0], Q[-1] = 0.5 * P[0] + 0.5 * P[1], 0.5 * P[-1] + 0.5 * P[-2]
        P = Q
    return P


def path_frames(path, up=(0.0, 1.0, 0.0)) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-point (tangent, side, up) unit vectors of an open path, side = tangent x up, up re-orthogonalised."""
    P = np.asarray(path, float)
    N = len(P)
    T = np.array([P[min(i + 1, N - 1)] - P[max(i - 1, 0)] for i in range(N)])
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-12)
    upv = np.asarray(up, float)
    upv /= np.linalg.norm(upv)
    if np.abs(T @ upv).max() > 0.95:
        upv = np.array([0.0, 0.0, 1.0]) if abs(upv[2]) < 0.5 else np.array([0.0, 1.0, 0.0])
    S = np.cross(T, upv)
    S /= np.maximum(np.linalg.norm(S, axis=1, keepdims=True), 1e-12)
    U = np.cross(S, T)
    U /= np.maximum(np.linalg.norm(U, axis=1, keepdims=True), 1e-12)
    return T, S, U


def tube_along_path(path, widths, heights, name: str, part: str, component: str | None = None, *, radius=0.0,
                    up=(0.0, 1.0, 0.0), n: int = 24, cap: bool = True, smooth: bool = True) -> bpy.types.Object:
    """A closed tube along an open 3D path with a (rounded) rectangular cross-section that varies along it:
    ``widths``/``heights`` are scalars or per-point arrays (mm; width = across ``side``, height = along ``up``),
    ``radius`` the corner rounding (scalar or per point). Temples, bridges, pad arms and endpieces in one call.

    Every section is a ``rounded_rect`` of 4 * max(8, ceil(n / 4)) points (8 per corner: 11.25 deg steps, so the
    exporter's 40 deg auto-smooth shades the rounding smooth), the same count along the whole tube; a radius below
    TUBE_MIN_RADIUS_MM (0 = a square section) is built with that small rounding, which reads as a crisp edge. Until
    2026-09-28 the section was resampled by arc length to ``n`` points: 1-2 points per corner, 30-53 deg turns, hard
    facet lines along every temple and core wire (test-pilot-002 r0006)."""
    P = np.asarray(path, float)
    N = len(P)
    W = np.broadcast_to(np.asarray(widths, float), (N,))
    H = np.broadcast_to(np.asarray(heights, float), (N,))
    R = np.broadcast_to(np.asarray(radius, float), (N,))
    T, S, U = path_frames(P, up)
    per_corner = max(TUBE_PER_CORNER, -(-int(n) // 4))
    secs = []
    for i in range(N):
        r = min(max(float(R[i]), TUBE_MIN_RADIUS_MM), W[i] / 2 * 0.999, H[i] / 2 * 0.999)
        p2 = rounded_rect(float(W[i]), float(H[i]), r, 4 * per_corner)
        secs.append(P[i] + p2[:, :1] * S[i] + p2[:, 1:2] * U[i])
    return loft(secs, name, part, component, cap=cap, smooth=smooth)


def rim_ring(lens_outline, width: float, thickness: float, z_front: float, name: str, part: str = "frame",
             component: str | None = None, *, profile: str = "rounded", n_profile: int = 16, n_path: int = 160,
             front_round: float | None = None, smooth: bool = True) -> bpy.types.Object:
    """A rim around a lens outline: a cross-section ``width`` (radially, from the lens edge outward) by
    ``thickness`` (along Z, front face at ``z_front``) swept along the outline. ``profile``: 'rounded' (elliptical
    front corners, flat back), 'box' (sharp) or 'round' (full ellipse, wire rims). ``front_round``: how far the
    front face rolls over (mm), default 45 % of the thickness. Inner edge sits on the outline."""
    path = resample_closed(ensure_ccw(lens_outline)[:, :2], n_path)
    path = np.column_stack([path, np.full(len(path), z_front - thickness / 2)])
    w, t = float(width), float(thickness)
    if profile == "box":
        prof = np.array([(0.0, t / 2), (w, t / 2), (w, -t / 2), (0.0, -t / 2)])
        prof = resample_closed(prof, max(n_profile, 8))
    elif profile == "round":
        prof = ellipse(w, t, n_profile, center=(w / 2, 0.0))
    else:
        r = t * 0.45 if front_round is None else min(float(front_round), t * 0.5, w * 0.5)
        pts = [(0.0, t / 2)]
        # rounded outer-front corner: quarter ellipse from the top-outer to the outer side
        for a in np.linspace(math.pi / 2, 0.0, max(4, n_profile // 2), endpoint=True):
            pts.append((w - r + r * math.cos(a), t / 2 - r + r * math.sin(a)))
        pts += [(w, -t / 2), (0.0, -t / 2)]
        prof = np.asarray(pts)
    # sweep_profile's a-axis is the outward in-plane normal, b-axis is +Z (up)
    return sweep_profile(path, prof, name, part, component, closed=True, up=(0.0, 0.0, 1.0), smooth=smooth)


def section_rect(center, w: float, h: float, axis_u, axis_v, radius: float = 0.0, n: int = 24) -> np.ndarray:
    """A (rounded) rectangular cross-section in 3D: centre point, size w along ``axis_u`` and h along ``axis_v``."""
    u = np.asarray(axis_u, float)
    u /= np.linalg.norm(u)
    v = np.asarray(axis_v, float)
    v /= np.linalg.norm(v)
    p2 = rounded_rect(w, h, radius, n) if radius > 0 else np.array([(w / 2, h / 2), (-w / 2, h / 2), (-w / 2, -h / 2), (w / 2, -h / 2)])
    if radius <= 0 and n > 4:
        p2 = resample_closed(p2, n)
    c = np.asarray(center, float)
    return c + p2[:, :1] * u + p2[:, 1:2] * v


def section_ellipse(center, w: float, h: float, axis_u, axis_v, n: int = 24) -> np.ndarray:
    u = np.asarray(axis_u, float)
    u /= np.linalg.norm(u)
    v = np.asarray(axis_v, float)
    v /= np.linalg.norm(v)
    p2 = ellipse(w, h, n)
    return np.asarray(center, float) + p2[:, :1] * u + p2[:, 1:2] * v


def lens_solid(outline, z_front: float, name: str, part: str, *, base_curve: float | None = 4.0,
               thickness: float = 2.0, component: str | None = None, spacing: float = 2.0,
               smooth: bool = True) -> bpy.types.Object:
    """A lens: the 2D outline (XY, mm) lifted onto a sphere of the given base curve (R = 530 / base_curve mm,
    apex at the outline centroid, front apex at ``z_front``), uniform ``thickness`` behind it, closed wall.
    ``base_curve=None`` or 0 gives a flat lens. UVs: u across, v = height (bottom 0, top 1)."""
    outline = ensure_ccw(outline)[:, :2]
    v2, tris = triangulate_region(outline, [], spacing)
    c = outline.mean(0)
    r2 = ((v2 - c) ** 2).sum(1)
    if base_curve:
        R = 530.0 / float(base_curve)
        if r2.max() >= R * R:
            raise ValueError(f"Lens outline too large for base curve {base_curve} (R = {R:.0f} mm)")
        sag = R - np.sqrt(R * R - r2)
    else:
        sag = np.zeros(len(v2))
    n = len(v2)
    front = np.column_stack([v2, z_front - sag])
    back = np.column_stack([v2, z_front - sag - thickness])
    verts = np.vstack([front, back])
    faces = [tuple(int(i) for i in t) for t in tris] + [tuple(int(i) + n for i in t[::-1]) for t in tris]
    m = len(outline)
    for i in range(m):
        a, b = i, (i + 1) % m
        faces.append((a, a + n, b + n, b))
    lo, hi = outline.min(0), outline.max(0)
    uv_of = lambda p: ((p[0] - lo[0]) / max(hi[0] - lo[0], 1e-9), (p[1] - lo[1]) / max(hi[1] - lo[1], 1e-9))
    uvs = []
    for f in faces:
        uvs.append([uv_of(verts[i]) for i in f])
    obj = new_mesh_object(name, verts, faces, part, component, uvs=uvs, smooth=smooth, smooth_angle_deg=60.0)
    return obj


def wrap_cylinder(obj: bpy.types.Object, radius: float, z_apex: float | None = None) -> bpy.types.Object:
    """Face-form wrap: bend the (flat, XY-built) mesh around a vertical cylinder of ``radius`` whose tangent plane
    is z = z_apex (default: the mesh's front-most z). x -> R sin(x/R), z -> z_apex - (R - R cos(x/R)) + (z - z_apex)."""
    apply_transform(obj)
    me = obj.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    if z_apex is None:
        z_apex = float(co[:, 2].max())
    R = float(radius)
    ang = co[:, 0] / R
    depth_off = co[:, 2] - z_apex                     # keep the local thickness along the bent normal
    x = (R + depth_off) * np.sin(ang)
    z = z_apex - (R - (R + depth_off) * np.cos(ang))
    co[:, 0], co[:, 2] = x, z
    me.vertices.foreach_set("co", co.ravel())
    me.update()
    return obj


def bevel(obj: bpy.types.Object, width: float, segments: int = 3, angle_limit_deg: float = 30.0,
          profile: float = 0.5, harden_normals: bool = True, clamp: bool = True) -> bpy.types.Modifier:
    """Rounded edges (a Bevel modifier, angle limited). Put it before ``shade_smooth``'s edge split in the stack."""
    mod = obj.modifiers.new("mdl_bevel", "BEVEL")
    mod.width = float(width)
    mod.segments = int(segments)
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(angle_limit_deg)
    mod.profile = float(profile)
    mod.harden_normals = bool(harden_normals)
    mod.use_clamp_overlap = bool(clamp)
    # keep the smoothing split last
    sm = obj.modifiers.get("mdl_smooth")
    if sm is not None:
        idx = list(obj.modifiers).index(sm)
        obj.modifiers.move(len(obj.modifiers) - 1, idx)
    return mod


def subdivide(obj: bpy.types.Object, levels: int = 1, use_creases: bool = False) -> bpy.types.Modifier:
    mod = obj.modifiers.new("mdl_subsurf", "SUBSURF")
    mod.levels = mod.render_levels = int(levels)
    mod.use_limit_surface = False
    sm = obj.modifiers.get("mdl_smooth")
    if sm is not None:
        obj.modifiers.move(len(obj.modifiers) - 1, list(obj.modifiers).index(sm))
    return mod


def solidify(obj: bpy.types.Object, thickness: float, offset: float = -1.0) -> bpy.types.Modifier:
    mod = obj.modifiers.new("mdl_solidify", "SOLIDIFY")
    mod.thickness = float(thickness)
    mod.offset = float(offset)
    mod.use_even_offset = True
    sm = obj.modifiers.get("mdl_smooth")
    if sm is not None:
        obj.modifiers.move(len(obj.modifiers) - 1, list(obj.modifiers).index(sm))
    return mod


def boolean(obj: bpy.types.Object, cutter: bpy.types.Object, operation: str = "DIFFERENCE",
            hide_cutter: bool = True, solver: str = "EXACT") -> bpy.types.Modifier:
    """Boolean with another object (the cutter is hidden from renders and never exported unless registered)."""
    mod = obj.modifiers.new(f"mdl_bool_{cutter.name}", "BOOLEAN")
    mod.object = cutter
    mod.operation = operation
    mod.solver = solver
    if hide_cutter:
        cutter.hide_render = True
        cutter.hide_viewport = True
        cutter.display_type = "WIRE"
    sm = obj.modifiers.get("mdl_smooth")
    if sm is not None:
        obj.modifiers.move(len(obj.modifiers) - 1, list(obj.modifiers).index(sm))
    return mod


# --------------------------------------------------------------------------- primitives
BOX_BEVEL_CLEARANCE_MM = 0.01      # the flat left between two opposite bevels is at least twice this (or 10 % of the side)
BOX_MIN_BEVEL_MM = 0.005           # the smallest bevel built (3 segments; scaled with more): its rounds' vertices must stay
                                   # farther apart than the export's 1e-3 mm weld or the part arrives unclosed


def box_bevel_limit(size) -> float:
    """The largest bevel width a box of ``size`` (mm) takes and stays a closed solid: below half of its thinnest side
    by a clearance (max(BOX_BEVEL_CLEARANCE_MM, 5 % of that side)), so a flat remains between the two rounds. At
    exactly half the side the bevel's clamp leaves a zero-width strip that the export welds into non-manifold edges
    (the six unclosed gold parts of the first paid run)."""
    thin = float(np.min(np.asarray(size, float)))
    return thin / 2.0 - max(BOX_BEVEL_CLEARANCE_MM, 0.05 * thin)


def box(center, size, name: str, part: str, component: str | None = None, *, bevel_mm: float = 0.0,
        segments: int = 3, smooth: bool = True) -> bpy.types.Object:
    """An axis-aligned box of ``size`` (w, h, d) mm about ``center``, optionally bevelled. ``bevel_mm`` is clamped to
    ``box_bevel_limit(size)`` with a note naming the object (an oversized bevel on a thin plate or inlay would leave
    the part unclosed); a bevel that fits is used as given. A bevel below BOX_MIN_BEVEL_MM (0.005 mm at 3 segments,
    proportionally more with more segments), requested or left by the clamp on a side thinner than about 0.03 mm, is
    dropped with a note: its rounds would weld together at export and the part would arrive unclosed."""
    c = np.asarray(center, float)
    size = np.asarray(size, float)
    if size.shape != (3,) or np.any(size <= 0):
        raise ValueError(f"{name}: box size must be three positive lengths (mm), got {np.asarray(size).tolist()}")
    s = size / 2
    verts = np.array([[sx, sy, sz] for sx in (-s[0], s[0]) for sy in (-s[1], s[1]) for sz in (-s[2], s[2])]) + c
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    obj = new_mesh_object(name, verts, faces, part, component, smooth=smooth)
    if bevel_mm > 0:
        width = float(bevel_mm)
        limit = box_bevel_limit(size)
        floor = BOX_MIN_BEVEL_MM * max(int(segments), 3) / 3.0
        if width > limit:
            if limit < floor:
                note(f"{name}: bevel_mm {width:g} dropped (the thinnest side is {size.min():g} mm, too thin to bevel and stay closed: "
                     f"the largest bevel that leaves a flat, {max(limit, 0.0):.4f} mm, is under the {floor:g} mm the export's weld needs)")
                return obj
            note(f"{name}: bevel_mm {width:g} clamped to {limit:.3f} mm (the thinnest side is {size.min():g} mm; a bevel of half the side "
                 "or more leaves no face between the two rounds and the part arrives unclosed)")
            width = limit
        elif width < floor:
            note(f"{name}: bevel_mm {width:g} dropped (under {floor:g} mm the export's 0.001 mm weld merges the rounds' vertices "
                 "and the part arrives unclosed)")
            return obj
        bevel(obj, width, segments)
    return obj


def cylinder(p0, p1, radius: float, name: str, part: str, component: str | None = None, *, segments: int = 24,
             smooth: bool = True) -> bpy.types.Object:
    a, b = np.asarray(p0, float), np.asarray(p1, float)
    axis = b - a
    L = np.linalg.norm(axis)
    if L <= 0:
        raise ValueError("Degenerate cylinder")
    axis /= L
    ref = np.array([0, 0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0, 0])
    u = np.cross(axis, ref)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    ang = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    ring = np.outer(np.cos(ang), u) * radius + np.outer(np.sin(ang), v) * radius
    return loft([a + ring, b + ring], name, part, component, cap=True, smooth=smooth)


def uv_sphere(center, radius: float, name: str, part: str, component: str | None = None, *, segments: int = 24,
              rings: int = 12) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    for v in bm.verts:
        v.co += Vector(center)
    return from_bmesh(name, bm, part, component, smooth_angle_deg=80.0)


_WORKER_FONTS = "/usr/share/fonts/truetype"
FONT_STYLES = {
    # generic style -> font files tried in order; the first that exists is used. The worker image's fonts come first
    # (DejaVu, then Liberation, under /usr/share/fonts), then the Windows host's (file names in _FONT_DIRS)
    "sans": [f"{_WORKER_FONTS}/dejavu/DejaVuSans.ttf", f"{_WORKER_FONTS}/liberation/LiberationSans-Regular.ttf",
             f"{_WORKER_FONTS}/liberation2/LiberationSans-Regular.ttf", "arial.ttf", "segoeui.ttf", "calibri.ttf"],
    "sans_bold": [f"{_WORKER_FONTS}/dejavu/DejaVuSans-Bold.ttf", f"{_WORKER_FONTS}/liberation/LiberationSans-Bold.ttf",
                  f"{_WORKER_FONTS}/liberation2/LiberationSans-Bold.ttf", "ariblk.ttf", "impact.ttf", "arial.ttf"],
    "serif": [f"{_WORKER_FONTS}/dejavu/DejaVuSerif.ttf", f"{_WORKER_FONTS}/liberation/LiberationSerif-Regular.ttf",
              f"{_WORKER_FONTS}/liberation2/LiberationSerif-Regular.ttf", "times.ttf", "georgia.ttf", "pala.ttf"],
    "serif_italic": [f"{_WORKER_FONTS}/liberation/LiberationSerif-Italic.ttf",
                     f"{_WORKER_FONTS}/liberation2/LiberationSerif-Italic.ttf", f"{_WORKER_FONTS}/dejavu/DejaVuSerif-Italic.ttf",
                     "georgiai.ttf", "times.ttf"],
    "script": ["MTCORSVA.TTF", "FRSCRIPT.TTF", "BRADHITC.TTF", "MISTRAL.TTF"],   # signature-style logos (Ray-Ban, Persol ...)
    "handwritten": ["LHANDW.TTF", "BRADHITC.TTF", "MISTRAL.TTF"],
    "mono": [f"{_WORKER_FONTS}/dejavu/DejaVuSansMono.ttf", f"{_WORKER_FONTS}/liberation/LiberationMono-Regular.ttf",
             f"{_WORKER_FONTS}/liberation2/LiberationMono-Regular.ttf", "consola.ttf", "cour.ttf"],
}
_FONT_DIRS = [Path("C:/Windows/Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts"]


def font_file(font: str | None) -> str | None:
    """The font file a style name in FONT_STYLES ('sans', 'serif', 'mono', ...), a font file name in the system font
    folders or a path resolves to: the first candidate that exists (the worker's fonts before the Windows host's), or
    None when none does."""
    if not font:
        return None
    for c in FONT_STYLES.get(str(font).lower(), [font]):
        p = Path(c)
        paths = [p] if p.is_absolute() or c.startswith("/") else [d / c for d in _FONT_DIRS]
        for fp in paths:
            if fp.is_file():
                return str(fp)
    return None


def _note_once(text: str) -> None:
    if text not in _NOTES:
        note(text)


def load_font(font: str | None):
    """A Blender VectorFont from a style name in FONT_STYLES, a font file name in the system font folders, or a
    path (``font_file``); None keeps Blender's default. The file used is noted once per build; when no candidate exists,
    Blender's built-in font is the last resort, with a note."""
    if not font:
        return None
    path = font_file(font)
    if path is None:
        _note_once(f"font {font!r} not found ({FONT_STYLES.get(str(font).lower(), [font])}); Blender's default font is "
                   "used as the last resort")
        return None
    _note_once(f"font {font!r}: {path}")
    fp = Path(path)
    for vf in bpy.data.fonts:
        try:
            same = bool(vf.filepath) and Path(bpy.path.abspath(vf.filepath)).resolve() == fp.resolve()
        except (OSError, ValueError):     # Blender's '<builtin>' font has no file
            same = False
        if same:
            return vf
    return bpy.data.fonts.load(str(fp))


def text_mesh(text: str, size_mm: float, thickness: float, name: str, part: str, component: str | None = None, *,
              position=(0.0, 0.0, 0.0), rotation_deg=(0.0, 0.0, 0.0), align: str = "CENTER", font: str | None = None,
              letter_spacing: float = 1.0) -> bpy.types.Object:
    """Raised text as a solid mesh (brand marks, logo lettering). ``size_mm`` is the cap height, roughly; ``font`` is a
    style from FONT_STYLES ('sans', 'sans_bold', 'serif', 'script', 'handwritten', 'mono'), a system font file name or
    a path; a signature-style logo (Ray-Ban, Persol, Oakley script) wants 'script'. A style resolves to its first
    existing file (``font_file``: the worker's DejaVu / Liberation fonts, then the Windows host's); the build notes name
    the file used, or say that Blender's default font was the last resort."""
    curve = bpy.data.curves.new(name + "_font", "FONT")
    curve.body = text
    curve.size = float(size_mm)
    curve.extrude = float(thickness) / 2
    curve.align_x = align
    curve.align_y = "CENTER"
    curve.space_character = float(letter_spacing)
    vf = load_font(font)
    if vf is not None:
        curve.font = vf
    tmp = bpy.data.objects.new(name + "_font", curve)
    _link(tmp)
    tmp.location = Vector(position)
    tmp.rotation_euler = [math.radians(a) for a in rotation_deg]
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg), depsgraph=dg)
    me.transform(tmp.matrix_world)
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.curves.remove(curve)
    obj = bpy.data.objects.new(name, me)
    _link(obj)
    register(obj, part, component)
    shade_smooth(obj, 60.0)
    return obj


def lens_print(lens: bpy.types.Object, text: str, height_mm: float, at_xy, name: str, *, font: str = "script",
               thickness: float = 0.12, standoff: float = 0.15, part: str = "frame", component: str | None = None,
               rotation_deg: float = 0.0, align: str = "CENTER") -> bpy.types.Object:
    """A printed mark on a lens (a logo or wordmark): thin text laid on the lens's front surface at ``at_xy`` (mm, the
    lens's XY plane), standing ``standoff`` mm in front of it so it draws over the lens in the runtime. The mark is
    an opaque part (default ``frame``: a lens part must stay a single optical sheet) and should be small and thin
    (height 3-6 mm, thickness ~0.1 mm); give it a print-like material (matte, no metal). ``font``: see text_mesh."""
    x, y = float(at_xy[0]), float(at_xy[1])
    # the lens's front surface height at (x, y): the +Z-most vertex nearest to the point
    me = lens.data
    best_z, best_d = None, None
    for v in me.vertices:
        w = lens.matrix_world @ v.co
        d = (w.x - x) ** 2 + (w.y - y) ** 2
        if best_d is None or d < best_d - 1e-9 or (abs(d - best_d) <= 1e-9 and w.z > best_z):
            best_d, best_z = d, w.z
    z = (best_z if best_z is not None else 0.0) + float(standoff)
    obj = text_mesh(text, float(height_mm), float(thickness), name, part, component or name,
                    position=(x, y, z), rotation_deg=(0.0, 0.0, float(rotation_deg)), align=align, font=font)
    return obj


# --------------------------------------------------------------------------- materials
def _srgb_to_linear(c):
    c = np.asarray(c, float)
    if c.max() > 1.0:
        c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def material_pbr(name: str, base_color_srgb, *, metallic: float = 0.0, roughness: float = 0.4,
                 transmission: float = 0.0, ior: float = 1.5, alpha: float = 1.0, coat: float = 0.0,
                 texture_path: str | None = None, kind: str = "pbr") -> bpy.types.Material:
    """A Principled material whose exact values are recorded for the exporter. ``base_color_srgb``: (r,g,b) in
    0..255 or 0..1 sRGB. Frame/temple materials keep ``transmission == 0`` (opaque) unless they are
    ``material_translucent`` (crystal or translucent acetate on frame and temple parts; hardware (metal) stays opaque;
    the exporter forces single-sided)."""
    lin = _srgb_to_linear(base_color_srgb)
    mat = bpy.data.materials.new(name)
    if not mat.use_nodes:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (float(lin[0]), float(lin[1]), float(lin[2]), 1.0)
    bsdf.inputs["Metallic"].default_value = float(metallic)
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["IOR"].default_value = float(ior)
    bsdf.inputs["Alpha"].default_value = float(alpha)
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = float(transmission)
    if "Coat Weight" in bsdf.inputs and coat > 0:
        bsdf.inputs["Coat Weight"].default_value = float(coat)
        bsdf.inputs["Coat Roughness"].default_value = 0.03
    if texture_path:
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(texture_path)
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if alpha < 1.0:
        mat.blend_method = "BLEND"
    mat["mdl"] = json.dumps({"kind": kind, "base_color_linear": [float(x) for x in lin], "metallic": float(metallic),
                             "roughness": float(roughness), "transmission": float(transmission), "ior": float(ior),
                             "alpha": float(alpha), "coat": float(coat), "texture_path": texture_path})
    return mat


def material_acetate(name: str, base_color_srgb, *, roughness: float = 0.22, coat: float = 0.4) -> bpy.types.Material:
    """Polished acetate: dielectric, low roughness, a clear coat for the gloss. Opaque in the runtime."""
    return material_pbr(name, base_color_srgb, metallic=0.0, roughness=roughness, coat=coat, kind="acetate")


TRANSLUCENT_REFERENCE_MM = 4.0     # the wall through which material_translucent's tint is defined


def material_translucent(name: str, tint_srgb=(250, 248, 240), *, thickness_mm: float = 4.0, roughness: float = 0.08,
                         ior: float = 1.49, transmission: float = 1.0, coat: float = 0.0) -> bpy.types.Material:
    """Crystal or translucent acetate for frame and temple parts: the wearer's skin shows through it in the runtime
    (physical transmission with volume absorption: KHR_materials_transmission / ior / volume in the GLB; the exporter
    forces single-sided; the runtime classifies it as frame or temple by the part's role, never as a lens).
    ``tint_srgb`` is the colour seen through 4 mm of the material (TRANSLUCENT_REFERENCE_MM): near-white = clear
    crystal, a warm honey = translucent amber, a smoky grey = translucent grey. ``thickness_mm`` is the wall the
    runtime absorbs through (the rim's or the temple's depth): at 8 mm the tint applies twice, at 2 mm half; so a
    thicker wall reads denser with the same tint. The tint lives in the volume only (the GLB base colour is white), so
    it is not applied twice. Frame and temple parts may be translucent; hardware (metal) stays opaque; the exporter
    forces single-sided; lenses use material_lens. The see-through of the exported front is measured in
    summary.frame_see_through."""
    if not 0.2 <= float(thickness_mm) <= 20.0:
        raise ValueError("thickness_mm must be within 0.2..20")
    if not 0.05 <= float(transmission) <= 1.0:
        raise ValueError("transmission must be within 0.05..1")
    if not 1.0 <= float(ior) <= 2.0:
        raise ValueError("ior must be within 1..2")
    mat = material_pbr(name, tint_srgb, metallic=0.0, roughness=roughness, transmission=transmission, ior=ior, coat=coat,
                       kind="translucent")
    # EEVEE shows the scene through a transmissive surface only with raytraced refraction (dithered surfaces)
    for attr, value in (("use_raytrace_refraction", True), ("surface_render_method", "DITHERED"), ("use_backface_culling", True)):
        if hasattr(mat, attr):
            try:
                setattr(mat, attr, value)
            except Exception:  # noqa: BLE001 - render-only preference
                pass
    spec = json.loads(mat["mdl"])
    lin = spec["base_color_linear"]
    spec["translucent"] = {"thickness_mm": float(thickness_mm), "attenuation_rgb_linear": [float(min(max(x, 0.01), 1.0)) for x in lin],
                           "attenuation_distance_mm": float(TRANSLUCENT_REFERENCE_MM), "ior": float(ior), "transmission": float(transmission)}
    mat["mdl"] = json.dumps(spec)
    return mat


def material_metal(name: str, base_color_srgb=(212, 175, 90), *, roughness: float = 0.25) -> bpy.types.Material:
    return material_pbr(name, base_color_srgb, metallic=1.0, roughness=roughness, kind="metal")


def material_rubber(name: str, base_color_srgb=(30, 30, 30), *, roughness: float = 0.85) -> bpy.types.Material:
    return material_pbr(name, base_color_srgb, metallic=0.0, roughness=roughness, kind="rubber")


def lens_optics(*, transmission_top_rgb, transmission_bottom_rgb=None, profile: str = "smooth",
                reflectance_rgb=(0.04, 0.04, 0.04), mirror_rgb=None, mirror_angular=None, roughness: float = 0.05) -> dict:
    """Optics of a lens for the runtime's canonical descriptor. Transmissions are LINEAR per-channel fractions of
    light passing straight through (0.005..1): a clear lens ~ (0.92, 0.92, 0.92); a brown gradient darker at the
    top than at the bottom. ``mirror_rgb``: head-on reflectance of a mirror coat (replaces the 4 % glass value).
    ``mirror_angular``: the coat's reflectance COLOUR by viewing angle, [(angle_deg, rgb), ...] starting at 0 (head-on;
    it replaces mirror_rgb) and rising to 90 (grazing; appended as near-white when omitted): this is how a Prizm-style
    pink-to-green or a sky-blue-to-violet mirror is expressed, and the runtime renders the shift from it."""
    top = [float(x) for x in transmission_top_rgb]
    bot = top if transmission_bottom_rgb is None else [float(x) for x in transmission_bottom_rgb]
    for t in top + bot:
        if not 0.005 <= t <= 1.0:
            raise ValueError("Lens transmissions must be within 0.005..1")
    angular = None
    if mirror_angular is not None:
        rows = []
        for a, rgb in mirror_angular:
            rgb = [float(min(max(x, 0.0), 0.95)) for x in rgb]
            if len(rgb) != 3:
                raise ValueError("mirror_angular colours must be rgb triples")
            rows.append([float(a), rgb])
        if not rows or rows[0][0] != 0.0:
            raise ValueError("mirror_angular must start at angle 0 (the head-on reflectance)")
        for (a0, _), (a1, _) in zip(rows, rows[1:]):
            if a1 <= a0:
                raise ValueError("mirror_angular angles must increase")
        if rows[-1][0] > 90.0:
            raise ValueError("mirror_angular angles must lie within 0..90 degrees")
        if rows[-1][0] < 90.0:
            rows.append([90.0, [0.95, 0.95, 0.95]])
            note("lens_optics: mirror_angular extended to 90 degrees with near-white (grazing reflectance)")
        angular = rows
        refl = list(rows[0][1])
    else:
        refl = [float(min(max(x, 0.0), 0.95)) for x in (mirror_rgb if mirror_rgb is not None else reflectance_rgb)]
    # energy: per channel transmission + reflectance <= 1; a request above the limit is capped and noted, not refused
    capped = []
    for i, r in enumerate(refl):
        lim = 1.0 - r
        for arr, name in ((top, "top"), (bot, "bottom")):
            if arr[i] > lim + 1e-9:
                capped.append(f"{name}[{i}] {arr[i]:.3f} -> {lim:.3f}")
                arr[i] = lim
    if capped:
        note("lens_optics: transmission capped by the energy rule (T + R <= 1): " + ", ".join(capped))
    if profile not in ("smooth", "linear", "flat"):
        raise ValueError("profile must be smooth, linear or flat")
    return {"transmission_top_rgb": top, "transmission_bottom_rgb": bot, "profile": profile,
            "reflectance_rgb": refl, "mirror": mirror_rgb is not None or angular is not None, "angular": angular,
            "roughness": float(roughness)}


LENS_GLASS_F0 = 0.04                # head-on reflectance of uncoated glass (IOR 1.5): Principled's Specular IOR Level 0.5


def material_lens(name: str, optics: dict) -> bpy.types.Material:
    """The lens material: the ``optics`` dict is exported as the runtime's canonical descriptor (density profile over the
    lens height + head-on reflectance). In Blender it is an EEVEE stand-in for the author's renders: a see-through
    tinted glass (Transmission 1, base colour = the mean transmission) whose coat or flash is a tinted specular
    reflection of the head-on reflectance (Specular IOR Level and Specular Tint; the plain 4 % glass is the default
    level 0.5), never metallic: a metallic mirror renders the lens opaque and dark in EEVEE."""
    top, bot = np.asarray(optics["transmission_top_rgb"]), np.asarray(optics["transmission_bottom_rgb"])
    mean_t = (top + bot) / 2
    lin = np.clip(mean_t, 0.0, 1.0)
    mat = bpy.data.materials.new(name)
    if not mat.use_nodes:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (float(lin[0]), float(lin[1]), float(lin[2]), 1.0)
    bsdf.inputs["Roughness"].default_value = float(optics.get("roughness", 0.05))
    bsdf.inputs["IOR"].default_value = 1.5
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = 1.0
    # EEVEE shows the scene through a transmissive surface only with raytraced refraction (dithered surfaces)
    for attr, value in (("use_raytrace_refraction", True), ("surface_render_method", "DITHERED"), ("use_backface_culling", True)):
        if hasattr(mat, attr):
            try:
                setattr(mat, attr, value)
            except Exception:  # noqa: BLE001 - render-only preference
                pass
    # the coat/flash as the dielectric's head-on reflectance: F0 = 0.08 x level x tint at IOR 1.5 (level 0.5 = 4 % glass)
    refl = np.clip(np.asarray(optics.get("reflectance_rgb", (0.04, 0.04, 0.04)), float), 0.0, 0.95)
    peak = float(refl.max())
    if peak > 0 and "Specular IOR Level" in bsdf.inputs and "Specular Tint" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = peak / (2.0 * LENS_GLASS_F0)
        bsdf.inputs["Specular Tint"].default_value = (*[float(x) for x in refl / peak], 1.0)
    mat["mdl"] = json.dumps({"kind": "lens", "base_color_linear": [float(x) for x in lin], "metallic": 0.0,
                             "roughness": float(optics.get("roughness", 0.05)), "transmission": 1.0, "ior": 1.5,
                             "alpha": 1.0, "coat": 0.0, "texture_path": None})
    mat["mdl_lens"] = json.dumps(optics)
    return mat


def assign(obj: bpy.types.Object, material: bpy.types.Material, faces=None) -> None:
    """Assign a material to the whole object (it becomes the object's ONLY slot: empty or inherited slots left by a
    boolean, a join or an import are cleared), or to the polygons whose indices are in ``faces`` (a slot added beside
    the existing ones; the other faces keep theirs)."""
    if faces is None:
        me = obj.data
        me.materials.clear()
        me.materials.append(material)
        me.polygons.foreach_set("material_index", [0] * len(me.polygons))
        me.update()
        return
    slots = [m for m in obj.data.materials]
    if material.name not in [m.name for m in slots if m]:
        obj.data.materials.append(material)
        slots.append(material)
    idx = [m.name if m else None for m in obj.data.materials].index(material.name)
    for f in faces:
        obj.data.polygons[int(f)].material_index = idx
    obj.data.update()


def assign_by_normal(obj: bpy.types.Object, material: bpy.types.Material, direction, min_cos: float = 0.7) -> int:
    """Assign to the faces whose normal points within acos(min_cos) of ``direction`` (e.g. the front caps)."""
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    me = obj.data
    normals = np.empty(len(me.polygons) * 3)
    me.polygons.foreach_get("normal", normals)
    normals = normals.reshape(-1, 3)
    sel = np.nonzero(normals @ d >= min_cos)[0]
    assign(obj, material, sel.tolist())
    return int(len(sel))


def uv_planar(obj: bpy.types.Object, axis: str = "Z", bounds=None) -> None:
    """Planar UVs from the world XY (axis Z), XZ (axis Y) or YZ (axis X) plane; ``bounds`` ((u0,v0),(u1,v1)) in mm
    maps to 0..1 (default: the object's own extent). Use for photo-projected decals."""
    apply_transform(obj)
    me = obj.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    cols = {"Z": (0, 1), "Y": (0, 2), "X": (2, 1)}[axis.upper()]
    P = co[:, cols]
    if bounds is None:
        lo, hi = P.min(0), P.max(0)
    else:
        lo, hi = np.asarray(bounds[0], float), np.asarray(bounds[1], float)
    uv = (P - lo) / np.maximum(hi - lo, 1e-9)
    layer = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    loops = np.empty(len(me.loops), int)
    me.loops.foreach_get("vertex_index", loops)
    layer.data.foreach_set("uv", uv[loops].ravel())
    me.update()


# --------------------------------------------------------------------------- declarations for the host
def set_bridge_underside(point) -> None:
    """State where the origin belongs: the lowest point of the bridge on the symmetry axis (mm). Optional; the host
    derives it from the front parts when absent."""
    bpy.context.scene["mdl_bridge_underside"] = [float(x) for x in point]


def set_temple_clip_z(z_mm: float) -> None:
    """Optional: how far back (negative z, mm) the drawn temple may extend before the runtime clips it behind the
    ear (the handover's ``clip`` parameter)."""
    bpy.context.scene["mdl_temple_clip_z_mm"] = float(z_mm)


# --------------------------------------------------------------------------- inventory and export (used by the harness)
def _evaluated_mesh(obj: bpy.types.Object, dg) -> bpy.types.Mesh:
    """The object with modifiers applied, in world space, with every n-gon (> 4 vertices) fanned around its centroid:
    Blender's ear-clipping of caps whose boundary carries collinear samples produces zero-area ears, which give
    non-finite shading normals in the exporter; a centroid fan never does."""
    ev = obj.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    me.transform(obj.matrix_world)
    big = [p.index for p in me.polygons if len(p.vertices) > 4]
    if big:
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        faces = [bm.faces[i] for i in big]
        bmesh.ops.poke(bm, faces=faces, offset=0.0, center_mode="MEAN_WEIGHTED", use_relative_offset=False)
        bm.to_mesh(me)
        bm.free()
        me.update()
    return me


WELD_MM = 1e-3                     # the position weld of the exporter, the contract (bsa.contract.WELD_M) and the inventory


def _welded_half_edges(me: bpy.types.Mesh):
    """The mesh's polygon edges on the position-welded topology (vertices within WELD_MM are one vertex, as the contract
    welds): arrays ``a``, ``b`` (welded ids of each directed polygon edge), ``face`` (its polygon), and a per-polygon
    ``degenerate`` mask. Edges that collapse under the weld are dropped; a polygon left with fewer than three edges is
    degenerate and contributes none (the contract drops such triangles)."""
    nv, npoly = len(me.vertices), len(me.polygons)
    empty = np.zeros(0, np.int64)
    if nv == 0 or npoly == 0:
        return empty, empty, empty, np.zeros(npoly, bool)
    co = np.empty(nv * 3)
    me.vertices.foreach_get("co", co)
    key = np.round(co.reshape(-1, 3) / WELD_MM).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    lv = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("vertex_index", lv)
    start = np.empty(npoly, np.int64)
    me.polygons.foreach_get("loop_start", start)
    total = np.empty(npoly, np.int64)
    me.polygons.foreach_get("loop_total", total)
    w = inv.ravel()[lv]
    nxt = np.arange(len(lv)) + 1
    nxt[start + total - 1] = start
    a, b = w, w[nxt]
    face = np.repeat(np.arange(npoly), total)
    keep = a != b
    degenerate = np.bincount(face[keep], minlength=npoly) < 3
    keep &= ~degenerate[face]
    return a[keep], b[keep], face[keep], degenerate


def _misoriented(a: np.ndarray, b: np.ndarray) -> int:
    """Directed edges traversed more than once (two adjacent faces wound the same way along them)."""
    if not len(a):
        return 0
    _, counts = np.unique(np.column_stack([a, b]), axis=0, return_counts=True)
    return int((counts > 1).sum())


def _orient_welded(a, b, face, degenerate, area, volume) -> dict:
    """Consistent windings on the welded topology: breadth-first over the manifold edges (exactly two faces) from one
    face per connected component, each neighbour taking the flip that makes the shared edge run opposite ways. Only
    components that carry an inconsistency change; a closed one (no boundary or non-manifold edge) is then turned
    outward (positive signed volume), an open one keeps the orientation most of its area had. Returns the per-polygon
    ``flip`` mask and the welded counts: bad (before), left (after), boundary, nonmanifold, conflicts (edges the
    search could not satisfy: a non-orientable surface)."""
    from collections import deque
    npoly = len(degenerate)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    _, uinv, ucount = np.unique(np.column_stack([lo, hi]), axis=0, return_inverse=True, return_counts=True)
    uinv = uinv.ravel()
    order = np.argsort(uinv, kind="stable")
    pair = np.flatnonzero(ucount[uinv[order]] == 2)[::2]          # the first half-edge of every two-face edge
    h1, h2 = order[pair], order[pair + 1]
    ok = face[h1] != face[h2]
    h1, h2 = h1[ok], h2[ok]
    rel = (a[h1] == a[h2]).astype(np.int64)                       # 1: same direction, the two faces need opposite flips
    src, dst, rr = np.r_[face[h1], face[h2]], np.r_[face[h2], face[h1]], np.r_[rel, rel]
    o = np.argsort(src, kind="stable")
    dst_l, rr_l = dst[o].tolist(), rr[o].tolist()
    ptr = np.searchsorted(src[o], np.arange(npoly + 1)).tolist()
    flip, comp = [-1] * npoly, [-1] * npoly
    conflicts, nc = 0, 0
    for s in range(npoly):
        if flip[s] >= 0 or degenerate[s]:
            continue
        flip[s], comp[s] = 0, nc
        queue = deque([s])
        while queue:
            f = queue.popleft()
            ff = flip[f]
            for j in range(ptr[f], ptr[f + 1]):
                g, want = dst_l[j], ff ^ rr_l[j]
                if flip[g] < 0:
                    flip[g], comp[g] = want, nc
                    queue.append(g)
                elif flip[g] != want:
                    conflicts += 1
        nc += 1
    fl = np.asarray(flip, np.int64) > 0
    cc = np.asarray(comp, np.int64)
    cc = np.where(cc >= 0, cc, nc)                                 # degenerate polygons: their own bin, never flipped
    open_c = np.zeros(nc + 1, bool)
    open_c[cc[face[ucount[uinv] != 2]]] = True
    _, dinv, dcount = np.unique(np.column_stack([a, b]), axis=0, return_inverse=True, return_counts=True)
    touched = np.zeros(nc + 1, bool)
    touched[cc[face[dcount[dinv.ravel()] > 1]]] = True
    touched[nc] = False
    vol = np.bincount(cc, weights=np.where(fl, -volume, volume), minlength=nc + 1)
    flipped_area = np.bincount(cc, weights=np.where(fl, area, 0.0), minlength=nc + 1)
    all_area = np.bincount(cc, weights=area, minlength=nc + 1)
    invert = np.where(open_c, flipped_area > all_area / 2, vol < 0)
    fl = (fl ^ invert[cc]) & touched[cc]
    g = fl[face]
    return {"flip": fl, "bad": _misoriented(a, b), "left": _misoriented(np.where(g, b, a), np.where(g, a, b)),
            "boundary": int((ucount == 1).sum()), "nonmanifold": int((ucount > 2).sum()), "conflicts": conflicts // 2}


def _consistent_normals(me: bpy.types.Mesh, name: str) -> bool:
    """Make face windings consistent when they are not (the mistake that failed paid turns: a mirrored or boolean-built
    solid with a patch of flipped faces, text glyphs recalculated before a weld, a smoothing split baked into the mesh).
    The orientation is found on the position-welded topology (WELD_MM, the contract's weld) and written back to the
    source polygons, which keep their split vertices, UVs and sharp edges. Closed components end outward (positive signed
    volume), open ones keep the orientation most of their area had. A result that is not better is not written. Holes
    and non-manifold edges are left alone: they are the author's to fix, and the note counts them on the same welded
    topology as the contract and the inventory. Returns True when the mesh was changed."""
    a, b, face, degenerate = _welded_half_edges(me)
    bad = _misoriented(a, b)
    if bad == 0:
        return False
    npoly = len(me.polygons)
    area = np.empty(npoly)
    me.polygons.foreach_get("area", area)
    # signed-volume term of each polygon (a fan from its first corner)
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("vertex_index", lv)
    start = np.empty(npoly, np.int64)
    me.polygons.foreach_get("loop_start", start)
    total = np.empty(npoly, np.int64)
    me.polygons.foreach_get("loop_total", total)
    li = np.arange(len(lv))
    pf = np.repeat(np.arange(npoly), total)
    inner = li < (start + total - 1)[pf]                           # corner k with k + 1 inside the polygon
    inner &= li > start[pf]                                        # ... and not the fan's apex
    p0, p1, p2 = co[lv[start[pf[inner]]]], co[lv[li[inner]]], co[lv[li[inner] + 1]]
    volume = np.bincount(pf[inner], weights=np.einsum("ij,ij->i", p0, np.cross(p1, p2)) / 6.0, minlength=npoly)
    o = _orient_welded(a, b, face, degenerate, area, volume)
    left, flips = o["left"], np.flatnonzero(o["flip"])
    faults = ("" if o["boundary"] == 0 else f"; the mesh is open ({o['boundary']} boundary edges after welding)") \
        + ("" if o["nonmanifold"] == 0 else f"; {o['nonmanifold']} non-manifold edges (more than two faces on one edge)")
    faults += ", which the export cannot repair" if faults else ""
    if left >= bad:
        why = ("the surface is not orientable (a twist, or faces joined inside out)" if o["conflicts"] else
               "the inconsistent edges are non-manifold")
        note(f"{name}: {bad} inconsistently wound edges; a re-orientation on the welded topology would leave {left}, so "
             f"the mesh is left unchanged ({why})" + faults)
        return False
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.reverse_faces(bm, faces=[bm.faces[int(i)] for i in flips])
    bm.to_mesh(me)
    bm.free()
    me.update()
    note(f"{name}: {bad} inconsistently wound edges; normals made consistent at export ({len(flips)} of {npoly} faces "
         "flipped on the welded topology" + (")" if left == 0 else f"; {left} edges remain inconsistent at non-manifold "
                                              "or non-orientable joins)") + faults)
    return True


def collect_parts(dg=None) -> tuple[dict, dict]:
    """Every registered object as triangle arrays in world mm: {object name: {part, component, V, F, UV(M,3,2) or
    None, face_material (M,), materials [names]}}, plus the material specs {name: dict}. Inconsistent face windings
    are repaired first (oriented on the position-welded topology, noted per object; holes are only counted)."""
    # export-time repair of inconsistent windings on the source meshes (before modifiers), then a fresh depsgraph
    repaired = False
    for obj in objects():
        if not obj.hide_render and obj.type == "MESH" and _consistent_normals(obj.data, obj.name):
            repaired = True
    if repaired:
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
    dg = dg or bpy.context.evaluated_depsgraph_get()
    out, mats = {}, {}
    for obj in objects():
        if obj.hide_render:
            continue
        me = _evaluated_mesh(obj, dg)
        me.calc_loop_triangles()
        nv = len(me.vertices)
        co = np.empty(nv * 3)
        me.vertices.foreach_get("co", co)
        V = co.reshape(-1, 3)
        nt = len(me.loop_triangles)
        F = np.empty(nt * 3, np.int64)
        me.loop_triangles.foreach_get("vertices", F)
        F = F.reshape(-1, 3)
        loops = np.empty(nt * 3, np.int64)
        me.loop_triangles.foreach_get("loops", loops)
        poly = np.empty(nt, np.int64)
        me.loop_triangles.foreach_get("polygon_index", poly)
        mat_index = np.empty(len(me.polygons), np.int64)
        me.polygons.foreach_get("material_index", mat_index)
        face_mat = mat_index[poly] if len(mat_index) else np.zeros(nt, np.int64)
        UV = None
        if me.uv_layers.active is not None and len(me.loops):
            uv = np.empty(len(me.loops) * 2)
            me.uv_layers.active.data.foreach_get("uv", uv)
            uv = uv.reshape(-1, 2)[loops].reshape(nt, 3, 2)
            UV = np.column_stack([uv[..., 0].ravel(), 1.0 - uv[..., 1].ravel()]).reshape(nt, 3, 2)   # glTF v down
        names = []
        for m in me.materials:
            if m is None:
                names.append(None)
                continue
            names.append(m.name)
            if m.name not in mats:
                spec = json.loads(m.get("mdl", "{}")) if m.get("mdl") else {"kind": "unknown", "base_color_linear": [0.5, 0.5, 0.5],
                                                                          "metallic": 0.0, "roughness": 0.5, "transmission": 0.0,
                                                                          "ior": 1.5, "alpha": 1.0, "coat": 0.0, "texture_path": None}
                if m.get("mdl_lens"):
                    spec["lens"] = json.loads(m["mdl_lens"])
                mats[m.name] = spec
        if not names:
            names = [None]
            face_mat = np.zeros(nt, np.int64)
        out[obj.name] = {"part": obj["part"], "component": obj.get("component", obj.name), "V": V, "F": F, "UV": UV,
                         "face_material": face_mat, "materials": names}
        bpy.data.meshes.remove(me)
    return out, mats


def inventory(parts: dict) -> list[dict]:
    """Per object: counts, bbox, manifoldness (from the exported triangles, on the contract's position-welded topology:
    vertices within WELD_MM are one, triangles that collapse under the weld carry no edges)."""
    rows = []
    for name, p in parts.items():
        V, F = p["V"], p["F"]
        row = {"object": name, "part": p["part"], "component": p["component"], "triangles": int(len(F)),
               "vertices": int(len(V)), "materials": [m for m in p["materials"] if m]}
        if len(V):
            row["bbox_mm"] = [V.min(0).round(2).tolist(), V.max(0).round(2).tolist()]
        if len(F):
            key = np.round(V / WELD_MM).astype(np.int64)
            _, inv = np.unique(key, axis=0, return_inverse=True)
            Fw = inv.ravel()[F]
            # triangles that collapse under the weld carry no edges, as in the contract's topology
            Fw = Fw[(Fw[:, 0] != Fw[:, 1]) & (Fw[:, 1] != Fw[:, 2]) & (Fw[:, 2] != Fw[:, 0])]
            e = np.sort(np.concatenate([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]]), axis=1)
            _, counts = np.unique(e, axis=0, return_counts=True)
            row["boundary_edges"] = int((counts == 1).sum())
            row["nonmanifold_edges"] = int((counts > 2).sum())
            # an edge traversed twice in the SAME direction means two adjacent faces wound inconsistently
            d = np.concatenate([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]])
            d = d[d[:, 0] != d[:, 1]]
            _, dcounts = np.unique(d, axis=0, return_counts=True)
            row["misoriented_edges"] = int((dcounts > 1).sum())
            row["closed"] = bool(row["boundary_edges"] == 0 and row["nonmanifold_edges"] == 0 and row["misoriented_edges"] == 0)
            # signed volume (divergence theorem): negative = inside-out normals; only meaningful when closed
            a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
            vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
            row["signed_volume_mm3"] = round(vol, 2)
            if row["closed"] and vol < 0:
                row["inverted_normals"] = True
        rows.append(row)
    return rows
