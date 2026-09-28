"""Shared synthetic meshes of the bsa tests (this module holds no tests)."""
from __future__ import annotations

import numpy as np
import open3d as o3d


def box(x0, x1, y0, y1, z0, z1, subdivide=0):
    """An axis-aligned closed box in model mm, optionally midpoint-subdivided: (V float64, F int64)."""
    m = o3d.geometry.TriangleMesh.create_box(x1 - x0, y1 - y0, z1 - z0)
    m.translate((x0, y0, z0))
    if subdivide:
        m = m.subdivide_midpoint(subdivide)
    return np.asarray(m.vertices, np.float64), np.asarray(m.triangles, np.int64)
