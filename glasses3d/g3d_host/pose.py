"""Temple pose on the host (WP6_FACETS §3.3): each temple opens about its vertical hinge axis exactly as the worker's
posed copies (g3d/render.py pose_matrix) and the temple builder (g3d/temple/build.py to_world) do, so host raster
masks, metrics, crops and overlays agree with the Blender render of the same view.

- ``pose`` = {"+1": {"theta_deg": t, "axis_xz_mm": [x, z]}, "-1": {...}} (the render's per-view pose and a basis
  image's pose): t in degrees, + = tip outward on both sides; the axis in asset mm with x signed (the -1 axis has
  negative x). ``parse_pose`` turns it into {side: (theta, (|x|, z))} for ``rot_about_hinge``.
- ``part_roles(glb)`` = each GLB node's ``partRole`` extra; ``temple_parts(mesh, side, roles)`` = the temple-role
  parts (temple body, decals, temple knuckles, leaf, pin) whose box centre x has the side's sign, as the worker
  selects its posed copies.
- The acetate bodies follow ONE rule, here and in g3d/render.py acetate_bodies (which runs in Blender and cannot
  import this module; tests/test_render_dome.py checks that both pick the same objects): the front = the frame-role
  part widest in x; a temple body = the side's temple-role part reaching >= TEMPLE_MIN_REACH_MM back in z whose box
  centre is >= TEMPLE_MIN_OFFSET_MM from x = 0, the furthest-reaching one.
- ``posed(mesh, pose, roles)`` = a FitMesh with those parts' vertices opened (same faces, parts and frame).
- ``chart_at(points_px, camera, mesh, pose, roles, hinge_z)`` = per pixel the first hit through the posed mesh
  (raster.cast) and, on a temple part, its chart (d, y, X) at opening 0: d = hinge_z - z, y, X = side x.
- Depth readings (a pixel read as a thickness, no mesh): ``chart_to_posed`` (the inverse of chart_of + unpose_points,
  with a temple's own opening first), ``toward_camera`` / ``faces_camera`` (camera.project's camera centre) and
  ``line_depth(uv, camera, frame, P0, n)`` = the t of the 3D line P0 + t n whose projection is nearest a pixel, its
  px per mm of t and the pixel's perpendicular miss from the projected line.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from . import fitmesh, glb
from .camera import Camera, NormFrame, project_mm

SIDE_KEYS = {1: "+1", -1: "-1"}
POSE_SIDES = {"+1": 1, "1": 1, "-1": -1}
TEMPLE_MIN_REACH_MM = 60.0           # = g3d/render.py TEMPLE_MIN_REACH_MM (one body rule, module doc)
TEMPLE_MIN_OFFSET_MM = 20.0          # = g3d/render.py TEMPLE_MIN_OFFSET_MM


def rot_about_hinge(P: np.ndarray, side: int, theta: float, axis_xz) -> np.ndarray:
    """Open a temple by theta (deg, + = tip outward on both sides) about its vertical hinge axis."""
    if not theta:
        return P
    t = math.radians(theta) * side
    c, s = math.cos(t), math.sin(t)
    x0, z0 = side * axis_xz[0], axis_xz[1]
    dx, dz = P[:, 0] - x0, P[:, 2] - z0
    return np.stack([x0 + c * dx - s * dz, P[:, 1], z0 + s * dx + c * dz], axis=1)


def make_pose(theta: dict, axis_xz) -> dict:
    """The pose of ``theta`` {side or '+1'/'-1': deg} about the mirrored axis ``axis_xz`` = [|x|, z]."""
    out = {}
    for k, t in theta.items():
        side = POSE_SIDES[str(k)] if not isinstance(k, int) else int(k)
        out[SIDE_KEYS[side]] = {"theta_deg": float(t), "axis_xz_mm": [side * abs(float(axis_xz[0])),
                                                                       float(axis_xz[1])]}
    return out


def parse_pose(pose) -> dict:
    """{side: (theta_deg, (|axis x|, axis z))} of a pose dict (None -> {}). Raises ValueError."""
    out = {}
    for key, rec in (pose or {}).items():
        side = POSE_SIDES.get(str(key))
        if side is None or side in out:
            raise ValueError(f"pose side {key!r} must be '+1' or '-1' (once each)")
        th = float(rec["theta_deg"])
        ax, az = (float(v) for v in rec["axis_xz_mm"])
        if side * ax <= 0 or not all(math.isfinite(v) for v in (th, ax, az)):
            raise ValueError(f"pose {key}: axis x must lie on its side and every value be finite")
        out[side] = (th, (abs(ax), az))
    return out


# --------------------------------------------------------------------------------------------- parts
def part_roles(glb_path) -> dict:
    """{GLB node name: partRole} from the nodes' extras (the g3d exporter writes them)."""
    doc = glb.load(glb_path).doc
    out = {}
    for n in doc.get("nodes", []):
        role = (n.get("extras") or {}).get("partRole")
        if isinstance(role, str) and "name" in n:
            out[n["name"]] = role
    return out


def _part_boxes(mesh: fitmesh.FitMesh) -> list:
    boxes = []
    for pid in range(len(mesh.node_names)):
        F = mesh.F[mesh.part == pid]
        if len(F) == 0:
            boxes.append(None)
            continue
        V = mesh.V[np.unique(F)]
        boxes.append((V.min(0), V.max(0)))
    return boxes


def temple_parts(mesh: fitmesh.FitMesh, side: int, roles: dict) -> list[int]:
    """Part ids of the temple-role parts on ``side`` (box centre x sign), as g3d/render.py temple_sides."""
    out = []
    for pid, box in enumerate(_part_boxes(mesh)):
        if box is None or roles.get(mesh.node_names[pid]) != "temple":
            continue
        cx = float((box[0][0] + box[1][0]) / 2)
        if abs(cx) > 1e-6 and (1 if cx > 0 else -1) == side:
            out.append(pid)
    return out


def temple_body(mesh: fitmesh.FitMesh, side: int, roles: dict, min_reach_mm: float = TEMPLE_MIN_REACH_MM) -> int | None:
    """The side's temple-role part reaching furthest back (>= min_reach_mm in z, box centre >= TEMPLE_MIN_OFFSET_MM
    from x = 0), or None (the one body rule of the module doc)."""
    best = None
    boxes = _part_boxes(mesh)
    for pid in temple_parts(mesh, side, roles):
        lo, hi = boxes[pid]
        reach = float(hi[2] - lo[2])
        if abs(float(lo[0] + hi[0]) / 2) < TEMPLE_MIN_OFFSET_MM:
            continue
        if reach >= min_reach_mm and (best is None or reach > best[0]):
            best = (reach, pid)
    return None if best is None else best[1]


def front_body(mesh: fitmesh.FitMesh, roles: dict) -> int | None:
    """The frame-role part widest in x (the front), or None."""
    best = None
    for pid, box in enumerate(_part_boxes(mesh)):
        if box is None or roles.get(mesh.node_names[pid]) != "frame":
            continue
        w = float(box[1][0] - box[0][0])
        if best is None or w > best[0]:
            best = (w, pid)
    return None if best is None else best[1]


def side_of_faces(mesh: fitmesh.FitMesh, roles: dict) -> np.ndarray:
    """Per face: +1 / -1 for temple-role faces of that side, 0 elsewhere."""
    out = np.zeros(len(mesh.F), np.int8)
    for side in (1, -1):
        out[np.isin(mesh.part, temple_parts(mesh, side, roles))] = side
    return out


def posed(mesh: fitmesh.FitMesh, pose, roles: dict) -> fitmesh.FitMesh:
    """``mesh`` with each posed side's temple parts opened (a new FitMesh; parts never share vertices)."""
    p = parse_pose(pose) if not (isinstance(pose, dict) and all(isinstance(k, int) for k in pose)) else pose
    V = mesh.V.copy()
    for side, (th, axis) in p.items():
        idx = np.unique(mesh.F[np.isin(mesh.part, temple_parts(mesh, side, roles))])
        if len(idx):
            V[idx] = rot_about_hinge(mesh.V[idx], side, th, axis)
    return replace(mesh, V=V)


def unpose_points(P: np.ndarray, side: np.ndarray, pose) -> np.ndarray:
    """Points on posed temples (``side`` per point: +1 / -1, 0 = not a temple) back to opening 0."""
    p = parse_pose(pose) if not (isinstance(pose, dict) and all(isinstance(k, int) for k in pose)) else pose
    Q = np.asarray(P, float).copy()
    for s, (th, axis) in p.items():
        sel = side == s
        if sel.any():
            Q[sel] = rot_about_hinge(Q[sel], s, -th, axis)
    return Q


def chart_of(Q: np.ndarray, side: np.ndarray, hinge_z: float) -> np.ndarray:
    """Chart (d, y, X) of unposed temple points: d = hinge_z - z, y, X = side x (nan where side is 0)."""
    out = np.full((len(Q), 3), np.nan)
    sel = side != 0
    out[sel, 0] = hinge_z - Q[sel, 2]
    out[sel, 1] = Q[sel, 1]
    out[sel, 2] = side[sel] * Q[sel, 0]
    return out


def chart_at(points_px, camera: Camera, mesh_posed: fitmesh.FitMesh, pose, roles: dict, hinge_z: float) -> list:
    """Per pixel (u, v): {uv, hit, part, side, point_mm (posed), d_mm, y_mm, X_mm} through the posed mesh."""
    uv = np.asarray(points_px, float).reshape(-1, 2)
    r = mesh_posed.scene().cast(camera, uv[:, 0], uv[:, 1], want_points=True)
    fs = side_of_faces(mesh_posed, roles)
    side = np.zeros(len(uv), np.int8)
    side[r["hit"]] = fs[r["face_id"][r["hit"]]]
    ch = chart_of(unpose_points(np.nan_to_num(r["points"]), side, pose), side, hinge_z)
    out = []
    for i, (u, v) in enumerate(uv.tolist()):
        rec = {"uv": [round(u, 1), round(v, 1)], "hit": bool(r["hit"][i]), "part": None, "side": int(side[i])}
        if r["hit"][i]:
            rec["part"] = mesh_posed.node_names[int(mesh_posed.part[r["face_id"][i]])]
            rec["point_mm"] = [round(float(x), 2) for x in r["points"][i]]
        if side[i]:
            rec.update(d_mm=round(float(ch[i, 0]), 2), y_mm=round(float(ch[i, 1]), 2), X_mm=round(float(ch[i, 2]), 2))
        out.append(rec)
    return out


# --------------------------------------------------------------------------------------------- depth readings
def chart_to_posed(ch, side: int, hinge_z: float, pose=None, opening=None) -> np.ndarray:
    """Chart (d, y, X) points of the ``side`` temple -> posed world mm: x = side X, y, z = hinge_z - d (the inverse of
    chart_of), then ``opening`` = (deg, (|axis x|, axis z)), a temple spec's own hinge opening (g3d/temple/build.py
    to_world, mirrored for -1), then the pose's theta for that side (as the render's posed copies; the inverse of
    unpose_points)."""
    C = np.asarray(ch, float).reshape(-1, 3)
    P = np.stack([side * C[:, 2], C[:, 1], hinge_z - C[:, 0]], axis=1)
    if opening is not None and opening[0]:
        P = rot_about_hinge(P, side, float(opening[0]), opening[1])
    p = parse_pose(pose) if not (isinstance(pose, dict) and all(isinstance(k, int) for k in pose)) else pose
    if side in p:
        th, axis = p[side]
        P = rot_about_hinge(P, side, th, axis)
    return P


def toward_camera(P_mm, camera: Camera, frame: NormFrame) -> np.ndarray:
    """(N, 3) the direction from each point toward the camera (not normalised), in mm space: camera.project's basis
    (toward = (cos p sin y, sin p, cos p cos y)) with its centre at toward / perspective in the normalized frame;
    an orthographic camera (perspective 0): toward itself."""
    yaw, pitch = math.radians(camera.yaw), math.radians(camera.pitch)
    toward = np.array([math.cos(pitch) * math.sin(yaw), math.sin(pitch), math.cos(pitch) * math.cos(yaw)])
    Pn = frame.to_norm(np.asarray(P_mm, float).reshape(-1, 3))
    return toward[None, :] - camera.perspective * Pn


def faces_camera(P_mm, n_mm, camera: Camera, frame: NormFrame) -> np.ndarray:
    """Per point: True when moving it along n (e.g. a temple's outward direction) moves it toward the camera, i.e.
    the camera sees the face whose outward direction is n from its front."""
    n = np.asarray(n_mm, float).reshape(-1, 3)
    return (n * toward_camera(P_mm, camera, frame)).sum(axis=1) > 0.0


def line_depth(uv, camera: Camera, frame: NormFrame, P0_mm, n_mm) -> dict:
    """The point P0 + t n (mm; n = the 3D direction of 1 mm of t) whose projection is nearest the pixel uv (native
    photo pixels), exactly: a pinhole camera (camera.project) maps the line to an image line, on which the image
    position is a projective function of t, s(t) = a t / (1 + b t) from P0 (fixed by s(-1) and s(+1)); the foot of
    uv's perpendicular on the image line gives t. Returns {"t" (None when the view looks along the line, or the foot
    lies beyond its vanishing point), "px_per_mm" (image px per mm of t at that t), "miss_px" (uv's perpendicular
    distance from the image line: large = the line is not the one the pixel shows), "uv" (the projection at t)}."""
    P0 = np.asarray(P0_mm, float).reshape(3)
    n = np.asarray(n_mm, float).reshape(3)
    target = np.asarray(uv, float).reshape(2)
    q = project_mm(np.stack([P0 - n, P0, P0 + n]), camera, frame)
    e = q[2] - q[0]
    L = float(np.hypot(*e))
    if L < 1e-9:
        return {"t": None, "px_per_mm": 0.0, "miss_px": float(np.hypot(*(target - q[1]))), "uv": q[1].tolist()}
    e = e / L
    sm, sp = float((q[0] - q[1]) @ e), float((q[2] - q[1]) @ e)
    b = (sm + sp) / (sm - sp)
    a = sp * (1.0 + b)
    w = target - q[1]
    s = float(w @ e)
    miss = float(abs(w[0] * e[1] - w[1] * e[0]))
    den = a - b * s
    t = s / den if abs(den) > 1e-12 else None
    if t is None or 1.0 + b * t <= 1e-9:
        return {"t": None, "px_per_mm": 0.0, "miss_px": miss, "uv": None}
    try:
        at = project_mm((P0 + t * n)[None], camera, frame)[0].tolist()
    except ValueError:                                  # that point is behind the camera's near plane
        return {"t": None, "px_per_mm": 0.0, "miss_px": miss, "uv": None}
    return {"t": t, "px_per_mm": abs(a) / (1.0 + b * t) ** 2, "miss_px": miss, "uv": at}
