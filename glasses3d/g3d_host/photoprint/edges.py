"""Edge registration per (view, part): which views may donate colour to which part (WP5_DESIGN §3, decision D2).

Ported from the scratch `design_reg/p6_region_edges.py`; PORT_REVIEW section 10. Changes: parts are labelled
individually (the probe grouped each temple with its hinge pieces), the geometry may be S' with a view's hinges, and
the result carries the admission decision.

Per view (stride 1): render-edge pixels take the part of the face just inside them; each photo-matte edge pixel takes
the part of its nearest render-edge pixel. Per part: mean of the two directed mean distances and the p90 of their
union, in mm at the front pivot (``job.ppm``). A part with fewer than ``MIN_EDGE_PX`` edge pixels in either direction
is ``unmeasured`` and not admitted (it is not seen well enough to donate). The thresholds are the owner-accepted
engineering defaults (D2): mean <= 1.0 mm and p90 <= 2.5 mm.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from .inputs import view_vertices

EDGE_MEAN_MM = 1.0
EDGE_P90_MM = 2.5
MIN_EDGE_PX = 30


def _edge(m: np.ndarray) -> np.ndarray:
    return m & ~ndimage.binary_erosion(m)


def view_part_edges(job, view: str, part_ids, *, cam=None, V=None, mean_mm: float = EDGE_MEAN_MM,
                    p90_mm: float = EDGE_P90_MM) -> dict:
    """{part name: {mean_mm, p90_mm, edge_px, photo_edge_px, admitted, reason}} for one view."""
    v = job.views[view]
    cam = cam or v.camera
    H, W = v.shape
    scene = job.scene(view_vertices(job, view) if V is None else V)
    r = scene.render(cam, (H, W), 1)
    m, fid = r["mask"], r["face_id"]
    fg = v.matte
    er, ep = _edge(m), _edge(fg)
    lab = np.full((H, W), -1)
    lab[m] = job.mesh.part[fid[m]]
    d_to_p = ndimage.distance_transform_edt(~ep)
    d_to_r, (iy, ix) = ndimage.distance_transform_edt(~er, return_indices=True)
    lab_p = lab[iy, ix]
    ppm = job.ppm(view, cam)
    out = {}
    for p in part_ids:
        a = d_to_p[er & (lab == p)]
        b = d_to_r[ep & (lab_p == p)]
        name = job.mesh.names[p]
        if len(a) < MIN_EDGE_PX or len(b) < MIN_EDGE_PX:
            out[name] = {"mean_mm": None, "p90_mm": None, "edge_px": int(len(a)), "photo_edge_px": int(len(b)),
                         "admitted": False, "reason": "unmeasured"}
            continue
        mean = float((a.mean() + b.mean()) / 2 / ppm)
        p90 = float(np.percentile(np.r_[a, b], 90) / ppm)
        ok = mean <= mean_mm and p90 <= p90_mm
        out[name] = {"mean_mm": mean, "p90_mm": p90, "edge_px": int(len(a)), "photo_edge_px": int(len(b)),
                     "admitted": bool(ok), "reason": None if ok else "edge"}
    return out


def edge_table(job, part_ids, *, views=None, **kw) -> dict:
    """{view: {part: row}} at the WP3b cameras (step 0's ``pose`` / ``deformer`` arguments were deleted on
    2026-10-13 with the step-0 experiment)."""
    out = {}
    for n in (views or list(job.views)):
        out[n] = view_part_edges(job, n, part_ids, **kw)
    return out


def admitted(table: dict) -> dict[tuple[str, str], bool]:
    """(view, part name) -> admitted."""
    return {(v, p): bool(row["admitted"]) for v, parts in table.items() for p, row in parts.items()}
