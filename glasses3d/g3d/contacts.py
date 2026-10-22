"""Near-miss contacts: separate parts that come within NEAR_MM of each other but never touch.

A brow that stops 0.75 mm short of the hinge block, a pad hovering beside its arm or a lens floating in its rim
shows up here as numbers. Information only, never a gate: some products have real clearances, and the photos
decide. Pure numpy; the caller supplies the nearest-surface query (a Blender BVH in the worker, brute force in
tests), so the same rules run everywhere.
"""
from __future__ import annotations

import time

import numpy as np

SCHEMA = "g3d.contacts/1"
TOUCH_MM = 0.25
NEAR_MM = 1.5
MAX_ROWS = 20
MAX_POINTS = 60_000
# A gap under TOUCH_MM is below about one AR pixel at face scale, so it counts as joined.
RULE = (f"Pairs of separate parts that come within {NEAR_MM} mm of each other but never closer than {TOUCH_MM} mm "
        "and do not intersect. Information, not a gate: compare each gap with the photos; real clearances exist.")


def samples(vertices, triangles):
    """World-space vertices plus triangle centroids, so a large face's interior is checked too."""
    vertices = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if len(triangles):
        return np.vstack([vertices, vertices[triangles].mean(axis=1)])
    return vertices


def _inside(points, lo, hi, margin):
    return np.all((points >= lo - margin) & (points <= hi + margin), axis=1)


def _strided(points, cap):
    """Deterministic thinning for very dense parts; the closest approach is refined from the kept points."""
    return points if len(points) <= cap else points[np.linspace(0, len(points) - 1, cap).astype(np.int64)]


def near_misses(parts, nearest, *, intersects=None, touch_mm=TOUCH_MM, near_mm=NEAR_MM, max_rows=MAX_ROWS,
                max_points=MAX_POINTS, budget_s=None, clock=time.perf_counter):
    """parts: [{"name", "role", "points": (n, 3) world mm}]. nearest(j, points, limit) returns each point's
    distance to part j's surface, inf beyond limit. intersects(i, j) reports surfaces that cross."""
    start = clock()
    boxes = [(np.min(p["points"], axis=0), np.max(p["points"], axis=0)) if len(p["points"]) else None for p in parts]
    pairs = [(i, j) for i in range(len(parts)) for j in range(i + 1, len(parts))
             if boxes[i] is not None and boxes[j] is not None
             and np.all(boxes[i][0] - near_mm <= boxes[j][1]) and np.all(boxes[j][0] - near_mm <= boxes[i][1])]
    rows, touching, checked = [], 0, 0
    for i, j in pairs:
        if budget_s is not None and clock() - start > budget_s:
            break
        checked += 1
        if intersects is not None and intersects(i, j):
            touching += 1
            continue
        close, minimum, where = [], np.inf, None
        for a, b in ((i, j), (j, i)):
            points = parts[a]["points"]
            points = _strided(points[_inside(points, *boxes[b], near_mm)], max_points)
            if not len(points):
                continue
            distance = np.asarray(nearest(b, points, near_mm), dtype=np.float64)
            hits = np.isfinite(distance) & (distance <= near_mm)
            if hits.any():
                k = int(np.argmin(np.where(hits, distance, np.inf)))
                if distance[k] < minimum:
                    minimum, where = float(distance[k]), points[k]
                close.append(points[hits])
        if not close:
            continue
        if minimum <= touch_mm:
            touching += 1
            continue
        band = np.vstack(close)
        rows.append({"parts": [parts[i]["name"], parts[j]["name"]],
                     "roles": [parts[i].get("role"), parts[j].get("role")],
                     "min_gap_mm": round(minimum, 3),
                     "gap_span_mm": round(float(np.ptp(band, axis=0).max()) if len(band) > 1 else 0.0, 1),
                     "near_points": int(len(band)),
                     "closest_mm": [round(float(v), 2) for v in where]})
    rows.sort(key=lambda row: (-row["gap_span_mm"], row["min_gap_mm"]))
    return {"schema": SCHEMA, "rule": RULE, "touch_mm": touch_mm, "near_mm": near_mm,
            "pairs_checked": checked, "pairs_nearby": len(pairs), "complete": checked == len(pairs),
            "touching_pairs": touching, "near_misses": rows[:max_rows], "omitted_near_misses": max(0, len(rows) - max_rows),
            "seconds": round(clock() - start, 3)}
