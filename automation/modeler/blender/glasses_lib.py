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
    ``transmission > 0`` is a LENS material in the AR runtime; frame/temple materials must stay opaque.
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
    """Bake the modifier stack into the mesh data (keeps the object registered)."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    old = obj.data
    obj.modifiers.clear()
    obj.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
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
    smoothing; adequate for smooth rim outlines (sharp concave corners are rounded slightly)."""
    p = ensure_ccw(poly)[:, :2]
    p = resample_closed(p, n_out or len(p))
    q = p + outward_normals(p) * float(distance)
    if smooth_iters:
        q = smooth_closed(q, smooth_iters, 0.5)
    return q


def rounded_rect(width: float, height: float, radius: float, n: int = 96, center=(0.0, 0.0)) -> np.ndarray:
    r = min(radius, width / 2, height / 2)
    cx, cy = center
    corners = [(cx + width / 2 - r, cy + height / 2 - r), (cx - width / 2 + r, cy + height / 2 - r),
               (cx - width / 2 + r, cy - height / 2 + r), (cx + width / 2 - r, cy - height / 2 + r)]
    pts = []
    per_corner = max(4, n // 4)
    for k, (x, y) in enumerate(corners):
        a0 = math.radians(90 * k)
        for a in np.linspace(a0, a0 + math.pi / 2, per_corner, endpoint=False):
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
        raise ValueError("All loft sections need the same number of points")
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
    ``radius`` the corner rounding (scalar or per point). Temples, bridges, pad arms and endpieces in one call."""
    P = np.asarray(path, float)
    N = len(P)
    W = np.broadcast_to(np.asarray(widths, float), (N,))
    H = np.broadcast_to(np.asarray(heights, float), (N,))
    R = np.broadcast_to(np.asarray(radius, float), (N,))
    T, S, U = path_frames(P, up)
    secs = []
    for i in range(N):
        r = min(float(R[i]), W[i] / 2 * 0.999, H[i] / 2 * 0.999)
        p2 = rounded_rect(float(W[i]), float(H[i]), r, n) if r > 0 else resample_closed(
            np.array([(W[i] / 2, H[i] / 2), (-W[i] / 2, H[i] / 2), (-W[i] / 2, -H[i] / 2), (W[i] / 2, -H[i] / 2)]), n)
        p2 = resample_closed(p2, n)
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
def box(center, size, name: str, part: str, component: str | None = None, *, bevel_mm: float = 0.0,
        segments: int = 3, smooth: bool = True) -> bpy.types.Object:
    c = np.asarray(center, float)
    s = np.asarray(size, float) / 2
    verts = np.array([[sx, sy, sz] for sx in (-s[0], s[0]) for sy in (-s[1], s[1]) for sz in (-s[2], s[2])]) + c
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    obj = new_mesh_object(name, verts, faces, part, component, smooth=smooth)
    if bevel_mm > 0:
        bevel(obj, bevel_mm, segments)
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


FONT_STYLES = {
    # generic style -> font files tried in order (Windows names; the first that exists is used)
    "sans": ["arial.ttf", "segoeui.ttf", "calibri.ttf"],
    "sans_bold": ["ariblk.ttf", "impact.ttf", "arial.ttf"],
    "serif": ["times.ttf", "georgia.ttf", "pala.ttf"],
    "serif_italic": ["georgiai.ttf", "times.ttf"],
    "script": ["MTCORSVA.TTF", "FRSCRIPT.TTF", "BRADHITC.TTF", "MISTRAL.TTF"],   # signature-style logos (Ray-Ban, Persol ...)
    "handwritten": ["LHANDW.TTF", "BRADHITC.TTF", "MISTRAL.TTF"],
    "mono": ["consola.ttf", "cour.ttf"],
}
_FONT_DIRS = [Path("C:/Windows/Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts"]


def load_font(font: str | None):
    """A Blender VectorFont from a style name in FONT_STYLES, a font file name in the system font folders, or a
    path; None keeps Blender's default. A missing font falls back to the default with a note."""
    if not font:
        return None
    candidates = FONT_STYLES.get(str(font).lower(), [font])
    for c in candidates:
        p = Path(c)
        paths = [p] if p.is_absolute() else [d / c for d in _FONT_DIRS]
        for fp in paths:
            if fp.is_file():
                for vf in bpy.data.fonts:
                    if Path(vf.filepath).resolve() == fp.resolve():
                        return vf
                return bpy.data.fonts.load(str(fp))
    note(f"font {font!r} not found ({candidates}); Blender's default font is used")
    return None


def text_mesh(text: str, size_mm: float, thickness: float, name: str, part: str, component: str | None = None, *,
              position=(0.0, 0.0, 0.0), rotation_deg=(0.0, 0.0, 0.0), align: str = "CENTER", font: str | None = None,
              letter_spacing: float = 1.0) -> bpy.types.Object:
    """Raised text as a solid mesh (brand marks, logo lettering). ``size_mm`` is the cap height, roughly; ``font`` is a
    style from FONT_STYLES ('sans', 'sans_bold', 'serif', 'script', 'handwritten', 'mono'), a system font file name or
    a path; a signature-style logo (Ray-Ban, Persol, Oakley script) wants 'script'."""
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
    0..255 or 0..1 sRGB. Frame/temple materials must keep ``transmission == 0`` (the runtime's lens rule)."""
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


def material_lens(name: str, optics: dict) -> bpy.types.Material:
    """The lens material: transmissive in Blender for the renders; the ``optics`` dict is exported as the runtime's
    canonical descriptor (density profile over the lens height + head-on reflectance)."""
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
    if optics.get("mirror"):
        bsdf.inputs["Metallic"].default_value = 0.6
        bsdf.inputs["Base Color"].default_value = (*[float(x) for x in np.clip(optics["reflectance_rgb"], 0, 1)], 1.0)
    mat["mdl"] = json.dumps({"kind": "lens", "base_color_linear": [float(x) for x in lin], "metallic": 0.0,
                             "roughness": float(optics.get("roughness", 0.05)), "transmission": 1.0, "ior": 1.5,
                             "alpha": 1.0, "coat": 0.0, "texture_path": None})
    mat["mdl_lens"] = json.dumps(optics)
    return mat


def assign(obj: bpy.types.Object, material: bpy.types.Material, faces=None) -> None:
    """Assign a material to the whole object, or to the polygons whose indices are in ``faces``."""
    slots = [m for m in obj.data.materials]
    if material.name not in [m.name for m in slots if m]:
        obj.data.materials.append(material)
        slots.append(material)
    idx = [m.name if m else None for m in obj.data.materials].index(material.name)
    if faces is None:
        obj.data.polygons.foreach_set("material_index", [idx] * len(obj.data.polygons))
    else:
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


def _inconsistent_edge_count(me: bpy.types.Mesh) -> int:
    """Edges traversed twice in the same direction: adjacent faces wound inconsistently (the exporter's
    ``misoriented_edges``). Vertices are welded by position (1e-3 mm) first, as the exporter does, so split or
    duplicated vertices do not hide the inconsistency."""
    nv = len(me.vertices)
    if nv == 0 or len(me.polygons) == 0:
        return 0
    co = np.empty(nv * 3)
    me.vertices.foreach_get("co", co)
    key = np.round(co.reshape(-1, 3) / 1e-3).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    pairs = []
    for p in me.polygons:
        vs = [int(inv[v]) for v in p.vertices]
        pairs.extend((vs[i], vs[(i + 1) % len(vs)]) for i in range(len(vs)))
    d = np.asarray(pairs, np.int64)
    d = d[d[:, 0] != d[:, 1]]
    if not len(d):
        return 0
    _, counts = np.unique(d, axis=0, return_counts=True)
    return int((counts > 1).sum())


def _consistent_normals(me: bpy.types.Mesh, name: str) -> bool:
    """Make face windings consistent when they are not (the mistake that failed the first paid turn on two products:
    a mirrored or boolean-built solid with a patch of flipped faces). Runs on the SOURCE mesh before modifiers, so
    edge-split faces are still connected. Closed solids are then oriented outward (positive signed volume); open
    sheets keep their majority orientation (a lens front sheet must face +Z). Holes and non-manifold edges are left
    alone: they are the author's to fix, and the note says so. Returns True when the mesh was changed."""
    bad = _inconsistent_edge_count(me)
    if bad == 0:
        return False
    bm = bmesh.new()
    bm.from_mesh(me)
    before = [f.normal.copy() for f in bm.faces]
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.normal_update()
    flipped = sum(1 for f, n in zip(bm.faces, before) if f.normal.dot(n) < 0)
    # orientation: a closed mesh must point outward; an open one keeps the majority direction it had
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    if boundary == 0:
        vol = bm.calc_volume(signed=True)
        if vol < 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces)
            flipped = len(bm.faces) - flipped
    else:
        agree = sum(1 for f, n in zip(bm.faces, before) if f.normal.dot(n) >= 0)
        if agree < len(bm.faces) / 2:
            bmesh.ops.reverse_faces(bm, faces=bm.faces)
            flipped = len(bm.faces) - flipped
    bm.to_mesh(me)
    bm.free()
    me.update()
    left = _inconsistent_edge_count(me)
    note(f"{name}: {bad} inconsistently wound edges; normals made consistent at export ({flipped} of {len(me.polygons)} faces flipped"
         + (")" if left == 0 else f"; {left} edges remain inconsistent because faces are disconnected)")
         + ("" if boundary == 0 else f"; the mesh is open ({boundary} boundary edges), which the export cannot repair"))
    return True


def collect_parts(dg=None) -> tuple[dict, dict]:
    """Every registered object as triangle arrays in world mm: {object name: {part, component, V, F, UV(M,3,2) or
    None, face_material (M,), materials [names]}}, plus the material specs {name: dict}."""
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
    """Per object: counts, bbox, manifoldness (from the exported triangles)."""
    rows = []
    for name, p in parts.items():
        V, F = p["V"], p["F"]
        row = {"object": name, "part": p["part"], "component": p["component"], "triangles": int(len(F)),
               "vertices": int(len(V)), "materials": [m for m in p["materials"] if m]}
        if len(V):
            row["bbox_mm"] = [V.min(0).round(2).tolist(), V.max(0).round(2).tolist()]
        if len(F):
            key = np.round(V / 1e-3).astype(np.int64)
            _, inv = np.unique(key, axis=0, return_inverse=True)
            Fw = inv.ravel()[F]
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
