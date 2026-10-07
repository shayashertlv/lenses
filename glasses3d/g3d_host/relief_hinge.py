"""WP6 hinge constraint on the host (plan/WP6_DESIGN.md §3 "Hinge parts are never moved", §8 V: W_HINGE_LEAF and
W_KNUCKLE_PENETRATION clear): the same three numbers g3d.faceted_temple's Blender hinge report grades, computed for a
candidate spec against the delivered GLB's hinge parts, so the joint fit can hold them as a hard constraint.

    hinge_parts(job)            per side the delivered hinge parts by geometry (measure.hinge_part_kind on partRole +
                                bbox, never names): temple-mounting leaves, temple knuckles, frame knuckles; their
                                vertices in world mm. Cached on the job; a synthetic job carries its source job's parts
    hinge_report(job, spec)     per side: the leaf-to-inner-face gap (temple_bpy._leaf_gap's rule: an x-ray from x = 0
                                through each leaf vertex, first hit on the temple must be its inner face; gap = side *
                                (hit x - vertex x), the minimum), frame-knuckle penetration (minus the minimum signed
                                distance to the closed temple), temple-knuckle contact; the warnings the tool would raise
    hinge_violation_mm(rep)     the summed excess over the tool's own warning limits (spec.HINGE_LEAF_GAP_MM,
                                HINGE_LEAF_EMBED_MM, KNUCKLE_PENETRATION_MM): 0 exactly when V's hinge clause is clear

Why it is in the fit: the WP6 diagnostic candidate let x_in at the hinge drift inward to 64.33 mm (no photo sees the
inner face there), embedding the leaf 1.37 mm and the frame knuckles 0.49 mm (V failed); the hand-traced tool test set
x_in = 65.6 from the leaf and the knuckles and V passed. The hinge parts are scene facts, not photo measurements.
"""
from __future__ import annotations

import numpy as np

from ..g3d.temple import build as TB
from ..g3d.temple import measure as MS
from ..g3d.temple import spec as TS
from . import glb as glbmod
from . import raster


def hinge_parts(job) -> dict | None:
    """{side: {"leaf": [V, ...], "frame_knuckle": [...], "temple_knuckle": [...], "names": {...}}} (world mm) of the
    job's delivered GLB, or None for a job without one (and without parts handed over by `attach_parts`)."""
    cached = job.__dict__.get("_hinge_parts")
    if cached is not None:
        return cached or None
    glb = getattr(job, "glb", None)
    if glb is None:
        job.__dict__["_hinge_parts"] = {}
        return None
    g = glbmod.load(glb)
    role, verts = {}, {}
    for p in g.primitives:
        if p.mode != 4 or len(p.triangles) == 0:
            continue
        nm = p.node_name
        verts.setdefault(nm, []).append(p.positions * 1000.0)
        if nm not in role:
            node = g.nodes[p.node_index] if 0 <= p.node_index < len(g.nodes) else {}
            r = (node.get("extras") or {}).get("partRole")
            if r is None and "mesh" in node:
                r = (g.meshes[node["mesh"]].get("extras") or {}).get("partRole")
            role[nm] = r if isinstance(r, str) else None
    hz = float(job.hinge["z_mm"])
    ax = float(job.hinge["axis_xz_mm"][0])
    out = {}
    for s in (1, -1):
        rec = {"leaf": [], "frame_knuckle": [], "temple_knuckle": [], "names": {"leaf": [], "frame_knuckle": [],
                                                                                "temple_knuckle": []}}
        for nm, Vs in verts.items():
            V = np.vstack(Vs)
            kind = MS.hinge_part_kind(V.min(axis=0), V.max(axis=0), role.get(nm), s, ax, hz, len(V))
            if kind is None:
                continue
            rec[kind].append(V)
            rec["names"][kind].append(nm)
        out[s] = rec
    job.__dict__["_hinge_parts"] = out
    return out


def attach_parts(job, parts: dict | None) -> None:
    """Hand a job (a synthetic S1 job) the hinge parts of the real job it was built from, so S1 runs the same
    hard constraint as the real fit."""
    job.__dict__["_hinge_parts"] = parts or {}


def _scene(W, T):
    o3d = raster.o3d
    sc = o3d.t.geometry.RaycastingScene()
    sc.add_triangles(o3d.core.Tensor(np.ascontiguousarray(W, np.float32)),
                     o3d.core.Tensor(np.ascontiguousarray(T, np.uint32)))
    return sc


def _leaf_gap(sc, V, side) -> float:
    """temple_bpy._leaf_gap on the host: per leaf vertex an x-ray from (0, y, z) toward +side x; when its first hit is
    a front face (entering the temple: its inner face), side * (hit x - vertex x); the minimum (nan: no ray hit)."""
    o3d = raster.o3d
    n = len(V)
    rays = np.zeros((n, 6), np.float32)
    rays[:, 1], rays[:, 2], rays[:, 3] = V[:, 1], V[:, 2], float(side)
    ans = sc.cast_rays(o3d.core.Tensor(rays))
    t = ans["t_hit"].numpy().astype(np.float64)
    nrm = ans["primitive_normals"].numpy().astype(np.float64)
    ok = np.isfinite(t) & (nrm[:, 0] * side < 0)
    if not ok.any():
        return float("nan")
    hit_x = side * t[ok]
    return float(np.min(side * (hit_x - V[ok, 0])))


def hinge_report(job, spec: dict, tb=None) -> dict | None:
    """Per side (+1, -1) the leaf gaps, knuckle penetrations and contacts of the spec's temples against the job's hinge
    parts, plus the warnings g3d.faceted_temple would raise for them; None without hinge parts."""
    parts = hinge_parts(job)
    if not parts:
        return None
    tb = tb if tb is not None else TB.build_temple(spec)
    o3d = raster.o3d
    out = {"sides": {}, "warnings": []}
    for s in (1, -1):
        W, _, T = tb.world(s)
        sc = _scene(W, T)
        rec = {"leaves": [], "frame_knuckles": [], "temple_knuckles": []}
        for V, nm in zip(parts[s]["leaf"], parts[s]["names"]["leaf"]):
            g = _leaf_gap(sc, V, s)
            rec["leaves"].append({"name": nm, "gap_mm": g})
            if np.isfinite(g) and (g > TS.HINGE_LEAF_GAP_MM or -g > TS.HINGE_LEAF_EMBED_MM):
                out["warnings"].append({"code": "W_HINGE_LEAF", "side": s, "part": nm, "gap_mm": g})
        for kind, key in (("frame_knuckle", "frame_knuckles"), ("temple_knuckle", "temple_knuckles")):
            for V, nm in zip(parts[s][kind], parts[s]["names"][kind]):
                sd = sc.compute_signed_distance(o3d.core.Tensor(np.ascontiguousarray(V, np.float32))).numpy()
                m = float(sd.min()) if len(sd) else float("inf")
                e = {"name": nm, "min_signed_mm": m}
                if kind == "frame_knuckle":
                    e["penetration_mm"] = max(0.0, -m)
                    if e["penetration_mm"] > TS.KNUCKLE_PENETRATION_MM:
                        out["warnings"].append({"code": "W_KNUCKLE_PENETRATION", "side": s, "part": nm,
                                                "penetration_mm": e["penetration_mm"]})
                else:
                    e["contact"] = bool(m <= 0.1)
                rec[key].append(e)
        out["sides"][s] = rec
    return out


def hinge_violation_mm(rep: dict | None) -> float:
    """Sum over sides and parts of the excess beyond the tool's warning limits (mm); 0 when V's hinge clause holds."""
    if not rep:
        return 0.0
    v = 0.0
    for rec in rep["sides"].values():
        for lf in rec["leaves"]:
            g = lf["gap_mm"]
            if np.isfinite(g):
                v += max(0.0, g - TS.HINGE_LEAF_GAP_MM) + max(0.0, -g - TS.HINGE_LEAF_EMBED_MM)
        for k in rec["frame_knuckles"]:
            v += max(0.0, k["penetration_mm"] - TS.KNUCKLE_PENETRATION_MM)
    return float(v)
