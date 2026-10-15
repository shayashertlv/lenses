"""Hash-bound geometric hinge anchors shared by camera fitting and the private stage.

Recognition failure is information. It is never a requirement to alter real hardware.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from glasses3d.g3d.temple import measure as MS
from . import fitmesh, pose


def derive_axes(path, expected_sha256=None, *, mesh=None, roles=None, require_both=False):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("captured temple source GLB differs from its SHA-256")
    mesh = fitmesh.load_fit_mesh(path) if mesh is None else mesh
    if not len(mesh.V) or not np.isfinite(mesh.V).all():
        raise ValueError("captured temple source has no finite geometry")
    roles = pose.part_roles(path) if roles is None else roles
    boxes = pose._part_boxes(mesh)
    rows = [(lo, hi, roles.get(mesh.node_names[i])) for i, box in enumerate(boxes)
            if box is not None for lo, hi in [box]]
    axes, bodies, unavailable = {}, {}, {}
    for side in (1, -1):
        key = f"{side:+d}"
        body = pose.temple_body(mesh, side, roles)
        if body is None:
            unavailable[key] = "no recognized temple body on this side"
            continue
        hinge_z = float(boxes[body][1][2])
        axis = MS.knuckle_axis(rows, side, hinge_z)
        if axis is None:
            unavailable[key] = (
                f"no geometric knuckle axis for side {side:+d}; recognition requires a temple-role part "
                f"centered on that side, with world X and Z extents strictly below {MS.KNUCKLE_MAX_MM:g} mm "
                f"and center Z within {MS.HINGE_NEAR_MM:g} mm of the temple-front hinge plane. "
                "This is a tool recognition heuristic, not a product geometry requirement; "
                "do not shrink real hardware just to satisfy it.")
            continue
        axes[key] = [side * abs(float(axis[0])), float(axis[1])]
        bodies[key] = mesh.node_names[body]
    pose.parse_pose({k: {"theta_deg": 0.0, "axis_xz_mm": v} for k, v in axes.items()})
    if require_both and unavailable:
        raise ValueError("captured temple source has " + "; ".join(unavailable.values()))
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("captured temple source changed while deriving hinge axes")
    return {"hinge_axis_xz_mm": axes, "unavailable": unavailable,
            "axis_source": {"glb_sha256": digest, "method": "temple_body_and_knuckle_bbox",
                            "temple_bodies": bodies}}
