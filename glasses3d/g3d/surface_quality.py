"""Read-only surface measurements. No score in this module is an acceptance gate.

Positions are millimetres. Split render vertices are welded for measurement only,
at 1e-5 mm, to compare normals on opposite sides of an exported triangle edge.
"""
from __future__ import annotations

import numpy as np


def plane(points):
    points = np.asarray(points, float)
    if len(points) < 3:
        return {"status": "unavailable", "reason": "fewer than three points"}
    centre = points.mean(0)
    _, singular, vectors = np.linalg.svd(points-centre, full_matrices=False)
    if singular[1] <= 1e-10:
        return {"status": "unavailable", "reason": "collinear points"}
    normal = vectors[-1]
    residual = (points-centre) @ normal
    return {"status": "measured", "centre_mm": centre.tolist(), "normal": normal.tolist(),
            "rms_mm": float(np.sqrt(np.mean(residual**2))), "max_mm": float(abs(residual).max())}


def analyse(vertices, triangles, corner_normals=None, *, facets=None):
    """Measure the actual surface, including degenerate faces and discontinuous loop normals.

    facets maps plain names to triangle IDs supplied by the caller's verified
    provenance or explicit selection. Never infer a product's facet names here.
    """
    v, t = np.asarray(vertices, float), np.asarray(triangles, int)
    p = v[t]
    cross = np.cross(p[:, 1]-p[:, 0], p[:, 2]-p[:, 0])
    twice_area = np.linalg.norm(cross, axis=1)
    valid = twice_area > 1e-10
    face_n = cross / np.maximum(twice_area[:, None], 1e-30)
    _, welded = np.unique(np.rint(v/1e-5).astype(np.int64), axis=0, return_inverse=True)
    edges = {}
    for i, tri in enumerate(t):
        if not valid[i]:
            continue
        for a, b in ((0, 1), (1, 2), (2, 0)):
            x, y = int(welded[tri[a]]), int(welded[tri[b]])
            if x != y:
                edges.setdefault(tuple(sorted((x, y))), []).append((i, a, b))
    cn = None if corner_normals is None else np.asarray(corner_normals, float)
    if cn is not None and cn.shape != (len(t), 3, 3):
        raise ValueError("corner normals must have one vector per triangle corner")
    if cn is not None:
        cn = cn/np.maximum(np.linalg.norm(cn, axis=2, keepdims=True), 1e-30)
    folds, seams, lengths = [], [], []
    for key, rows in edges.items():
        i, a, b = rows[0]
        lengths.append(float(np.linalg.norm(p[i, a]-p[i, b])))
        if len(rows) != 2:
            continue
        j, c, d = rows[1]
        angle = float(np.degrees(np.arccos(np.clip(face_n[i] @ face_n[j], -1, 1))))
        folds.append(angle)
        if cn is not None:
            left = {int(welded[t[i, k]]): cn[i, k] for k in (a, b)}
            right = {int(welded[t[j, k]]): cn[j, k] for k in (c, d)}
            jump = max(float(np.degrees(np.arccos(np.clip(left[k] @ right[k], -1, 1)))) for k in key)
            if jump > .1:
                seams.append({"triangle_ids": [i, j], "jump_deg": jump, "geometric_fold_deg": angle,
                              "at_mm": ((p[i, a]+p[i, b])/2).tolist()})
    named = {}
    for label, ids in (facets or {}).items():
        ids = np.asarray(ids, int)
        ids = ids[valid[ids]]
        ids_v = np.unique(t[ids]) if len(ids) else []
        row = plane(v[ids_v])
        if len(ids) and row["status"] == "measured":
            n = np.asarray(row["normal"])
            row["geometric_normal_spread_deg"] = float(np.degrees(np.arccos(np.clip(abs(face_n[ids] @ n), 0, 1))).max())
            if cn is not None:
                row["shading_normal_spread_deg"] = float(np.degrees(np.arccos(np.clip(abs(cn[ids] @ n), 0, 1))).max())
        named[label] = {**row, "triangle_count": len(ids)}
    def stats(values):
        return ({"max": float(np.max(values)), "p95": float(np.percentile(values, 95)),
                 "median": float(np.median(values))} if len(values) else None)
    return {"informational_only": True, "vertices": len(v), "triangles": len(t),
            "degenerate_triangle_ids": np.flatnonzero(~valid).tolist(), "degenerate_triangles": int((~valid).sum()),
            "edge_length_mm": stats(lengths), "adjacent_geometric_normal_angle_deg": stats(folds),
            "normal_seam_count": len(seams), "largest_normal_seams": sorted(seams, key=lambda r: -r["jump_deg"])[:24],
            "facets": named, "normal_continuity_available": cn is not None,
            "interpretation": "Planarity and normal jumps describe geometry, not likeness. Intentional sharp edges also appear as seams. "
                              "Edge lengths and normal changes expose coarse curvature; they are not a measured optical ripple amplitude."}
