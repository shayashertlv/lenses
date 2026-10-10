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
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from . import fitmesh, glb
from .camera import Camera

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
