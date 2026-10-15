"""Product-independent closed rim sweeps, in millimetres (numpy only).

The section axes are outward from the opening and toward the front. An acetate
section has a circular crowned face tangent to circular edge fillets; it is not
a beveled rectangular extrusion. No faceting or sharp-angle threshold is inferred.
"""
from __future__ import annotations

import math
from collections import Counter

import numpy as np


class RimError(ValueError):
    """The requested section or sweep cannot be built as specified."""


def acetate_section(width_mm, depth_mm, edge_round_mm, crown_mm=0.0, segments=8) -> dict:
    """Return a smooth closed section, compatible with polygon_section's output.

    width/depth are the exact bounding dimensions. edge_round is the radius of
    all four edge fillets. crown is the front face's rise from its tangency with
    those fillets to its centre (zero gives a flat face). The rear face is flat.
    segments >= 8 is the samples per quarter fillet; the crown uses twice that
    many segments. All parameter choices have the same ordered vertex count,
    allowing per-station profiles. No vertex is sharp unless the caller marks it.
    Dimensions leaving a sample spacing below 1e-9 mm are refused explicitly.
    """
    w, d, r, c = map(float, (width_mm, depth_mm, edge_round_mm, crown_mm))
    if not all(math.isfinite(x) for x in (w, d, r, c)):
        raise RimError("section dimensions must be finite")
    if r <= 0 or w <= 2 * r or d <= 2 * r or c < 0 or c >= w / 2:
        raise RimError("need edge_round > 0, width/depth > 2*edge_round and 0 <= crown < width/2")
    if isinstance(segments, bool) or int(segments) != segments or not 8 <= segments <= 128:
        raise RimError("segments must be an integer from 8 to 128 (at least 8 per quarter round)")
    n = int(segments)
    b, front, back = w / 2 - r, d / 2, -d / 2
    # Offset a convex circular-front core by r. Solve the visible crown sagitta,
    # rather than treating the unfilleted core's sagitta as the owner's dimension.
    if c:
        lo, hi = 0.0, math.pi / 2
        for _ in range(64):
            th = (lo + hi) / 2
            # (b/sin(th) + r)*(1-cos(th)), stable for a shallow crown.
            sag = b * math.tan(th / 2) + 2 * r * math.sin(th / 2) ** 2
            if sag < c:
                lo = th
            else:
                hi = th
        th = (lo + hi) / 2
        radius = b / math.sin(th)
        corner_z = front - r - b * math.tan(th / 2)
        phi = math.pi / 2 - th
    else:
        th, radius, corner_z, phi = 0.0, None, front - r, math.pi / 2
    if corner_z <= back + r + 1e-9:
        raise RimError("crown and edge rounds leave no side wall; increase depth or reduce their dimensions")

    parts, kinds = [], []

    def arc(center, rad, start, end, count, kind, skip_first=False):
        angles = np.linspace(start, end, count + 1)
        p = np.c_[center[0] + rad * np.cos(angles), center[1] + rad * np.sin(angles)]
        if skip_first:
            p = p[1:]
        parts.append(p)
        kinds.extend([kind] * len(p))

    arc((b, back + r), r, -math.pi / 2, 0, n, "rear_outer_round")
    arc((b, corner_z), r, 0, phi, n, "front_outer_round")
    if c:
        # Stable z expression avoids subtracting two large radii for tiny crowns.
        angles = np.linspace(-th, th, 2 * n + 1)
        p = np.c_[-(radius + r) * np.sin(angles), front - 2 * (radius + r) * np.sin(angles / 2) ** 2]
    else:
        p = np.c_[np.linspace(b, -b, 2 * n + 1), np.full(2 * n + 1, front)]
    # Only the two analytic tangent joins are shared points. Distance-based
    # deduplication would silently change profile topology for a steep crown.
    parts.append(p[1:])
    kinds.extend(["crown"] * (len(p)-1))
    arc((-b, corner_z), r, math.pi - phi, math.pi, n, "front_inner_round", skip_first=True)
    arc((-b, back + r), r, math.pi, 3 * math.pi / 2, n, "rear_inner_round")
    p = np.vstack(parts)
    if np.any(np.linalg.norm(np.roll(p, -1, axis=0)-p, axis=1) < 1e-9):
        raise RimError("section samples are below 1e-9 mm resolution; its edge fillets or widths are too small")
    area = float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1)) / 2)
    return {"points": p.tolist(), "sharp": [False] * len(p),
            "kind": kinds,
            "width_mm": w, "depth_mm": d, "edge_round_mm": r, "crown_mm": c,
            "area_mm2": area, "segments_per_quarter": n}


def _section(value):
    if not isinstance(value, dict) or "points" not in value:
        raise RimError("section must be a dict with points and optional sharp flags")
    p = np.asarray(value["points"], dtype=np.float64)
    if p.ndim != 2 or p.shape[1:] != (2,) or len(p) < 3 or not np.isfinite(p).all():
        raise RimError("section points must be at least three finite (outward, front) pairs")
    flags = np.asarray(value.get("sharp", [False] * len(p)), dtype=bool)
    if flags.shape != (len(p),):
        raise RimError("section sharp flags must match the point count")
    if np.any(np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1) < 1e-9):
        raise RimError("section repeats a point or has a zero-length edge")
    area = float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1)) / 2)
    if area <= 1e-9:
        raise RimError("section points must run counter-clockwise and enclose positive area")
    return p, flags


def sweep_rim(outline, section, *, sections=None) -> dict:
    """Sweep a section around a closed 3D centreline, without a duplicated seam.

    outline: >= 3 ordered (x,y,z) samples; omit the repeated endpoint. Its XY
    projection defines outward, +Z defines front; Z may vary for a wrapped rim.
    sections: optional one section per outline sample, same count/order of points.
    This makes measured thickness changes and local facets explicit. A profile
    vertex marked sharp at BOTH ends marks that span's longitudinal edge sharp;
    no automatically detected angle marks an edge sharp. The caller supplies
    enough outline samples to reproduce the measured curvature.
    """
    q = np.asarray(outline, dtype=np.float64)
    if q.ndim != 2 or q.shape[1:] != (3,) or len(q) < 3 or not np.isfinite(q).all():
        raise RimError("outline must contain at least three finite (x,y,z) points")
    edge = np.roll(q, -1, axis=0) - q
    lengths = np.linalg.norm(edge, axis=1)
    if np.any(lengths < 1e-9):
        raise RimError("outline repeats an endpoint or has a zero-length segment")
    area = float(np.sum(q[:, 0] * np.roll(q[:, 1], -1) - q[:, 1] * np.roll(q[:, 0], -1)) / 2)
    if abs(area) < 1e-9:
        raise RimError("outline XY projection must enclose an opening")
    tangent = edge / lengths[:, None] + np.roll(edge / lengths[:, None], 1, axis=0)
    norm = np.linalg.norm(tangent, axis=1)
    if np.any(norm < 1e-9):
        raise RimError("outline reverses direction at a station")
    tangent /= norm[:, None]
    outward = np.cross(tangent, [0.0, 0.0, 1.0]) * (1 if area > 0 else -1)
    norm = np.linalg.norm(outward, axis=1)
    if np.any(norm < 1e-9):
        raise RimError("outline has a tangent parallel to front; its section frame is undefined")
    outward /= norm[:, None]
    front = np.array([0.0, 0.0, 1.0]) - tangent[:, 2:3] * tangent
    front /= np.linalg.norm(front, axis=1)[:, None]
    base, base_flags = _section(section)
    n, m = len(q), len(base)
    if sections is not None and len(sections) != n:
        raise RimError("sections must contain one profile for every outline sample")
    parsed = [(base, base_flags)] * n if sections is None else [_section(s) for s in sections]
    if any(len(p) != m for p, _ in parsed):
        raise RimError("per-station sections must have the same point count and order")
    points = np.asarray([p for p, _ in parsed])
    flags = np.asarray([f for _, f in parsed])
    v = (q[:, None, :] + points[:, :, 0:1] * outward[:, None, :] +
         points[:, :, 1:2] * front[:, None, :]).reshape(-1, 3)
    faces, sharp, sharp_spans = [], [], []
    for i in range(n):
        k = (i + 1) % n
        for j in range(m):
            h = (j + 1) % m
            a, b, c, d = i*m+j, i*m+h, k*m+h, k*m+j
            if np.linalg.norm(v[a] - v[c]) <= np.linalg.norm(v[b] - v[d]):
                faces.extend(((a, d, c), (a, c, b)))
            else:
                faces.extend(((a, d, b), (d, c, b)))
            if flags[i, j] and flags[k, j]:
                sharp.append((a, d))
                sharp_spans.append((i, j))
    f = np.asarray(faces, dtype=np.int64)
    cross = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    if np.any(np.linalg.norm(cross, axis=1) < 1e-10):
        raise RimError("sweep contains a zero-area triangle; inspect outline/profile spacing")
    volume = float(np.einsum("ij,ij->i", v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum() / 6)
    if abs(volume) < 1e-9:
        raise RimError("sweep has zero signed volume")
    if volume < 0:
        f = f[:, ::-1].copy()
        volume = -volume
    directed = Counter((int(a), int(b)) for face in f for a, b in zip(face, np.roll(face, -1)))
    closed = all(c == 1 and directed[(b, a)] == 1 for (a, b), c in directed.items())
    return {"V": v, "F": f, "sharp_edges": sharp, "sharp_spans": sharp_spans,
            "closed": closed, "euler": len(v) - len(directed)//2 + len(f), "volume_mm3": volume,
            "vertices_per_section": m, "stations": n,
            "note": "Topology and volume describe the sweep; inspect tight bends for self-intersection."}
