"""Closed, smooth quadratic lens solids with explicit sampling error bounds (millimetres).

The caller supplies the optical shape; this helper never guesses it from a product
name. It handles a simple outline visible from its supplied centre. Rim/seating
geometry must be checked separately after construction.
"""
from __future__ import annotations

import math
import numpy as np


def quadratic_lens(outline_xy_mm, coefficients, thickness_mm, *, centre_xy_mm=None,
                   max_edge_mm=1.5, max_chord_mm=.003):
    """z = c0+c1*x+c2*y+c3*x*x+c4*x*y+c5*y*y; rear is z-thickness.

    Boundary segments are split along their original straight XY outline. Both
    surfaces use analytic normals, with separate side-wall normals at the rim.
    max_edge_mm bounds optical-surface edges in 3D (not rim diagonals).
    max_chord_mm bounds the midpoint error on each optical-surface edge.
    Sampling uses the exact quadratic Hessian and a global gradient bound.
    """
    outline, coef = np.asarray(outline_xy_mm, float), np.asarray(coefficients, float)
    if outline.ndim != 2 or outline.shape[1] != 2 or len(outline) < 3 or coef.shape != (6,):
        raise ValueError("outline must be at least three XY points and coefficients six numbers")
    values = [thickness_mm, max_edge_mm, max_chord_mm]
    if not np.isfinite(outline).all() or not np.isfinite(coef).all() or not all(math.isfinite(x) and x > 0 for x in values):
        raise ValueError("finite outline/coefficients and positive thickness, edge and chord bounds required")
    from .temple.arrange import simple_polygon, poly_area
    if not simple_polygon(outline)[0]:
        raise ValueError("outline must be simple")
    if poly_area(outline) < 0:
        outline = outline[::-1]
    centre = outline.mean(0) if centre_xy_mm is None else np.asarray(centre_xy_mm, float)
    if centre.shape != (2,) or not np.isfinite(centre).all():
        raise ValueError("centre must be finite XY")
    rays = outline-centre
    area = rays[:, 0]*np.roll(rays[:, 1], -1)-rays[:, 1]*np.roll(rays[:, 0], -1)
    if np.any(area <= 1e-10):
        raise ValueError("outline must be strictly star-shaped about centre; choose a valid centre or another surface builder")
    hessian = np.array([[2*coef[3], coef[4]], [coef[4], 2*coef[5]]])
    curvature = float(np.linalg.norm(hessian, 2))
    gradient = np.c_[coef[1]+2*coef[3]*outline[:, 0]+coef[4]*outline[:, 1],
                     coef[2]+coef[4]*outline[:, 0]+2*coef[5]*outline[:, 1]]
    # The affine gradient's norm is convex, so its maximum over the outline's
    # convex hull occurs at an original vertex. This also covers every radial
    # segment and bounds a chord's 3D/XY length ratio.
    slope = float(np.linalg.norm(gradient, axis=1).max())
    xy_bound = min(max_edge_mm/math.sqrt(1+slope*slope),
                   math.sqrt(8*max_chord_mm/max(curvature, 1e-30)))
    # A cell diagonal is the SUM of a radial and circumferential step. These
    # directions need not be perpendicular on a general star-shaped outline.
    step = xy_bound/2
    boundary = []
    for a, b in zip(outline, np.roll(outline, -1, axis=0)):
        count = max(1, int(math.ceil(np.linalg.norm(b-a)/step)))
        boundary.extend(a+(b-a)*(i/count) for i in range(count))
    boundary = np.asarray(boundary)
    rings = max(1, int(math.ceil(np.linalg.norm(boundary-centre, axis=1).max()/step)))
    n = len(boundary)
    if rings*n > 120000:
        raise ValueError("requested lens sampling exceeds 120000 surface vertices; use a coarser stated bound")
    xy = np.concatenate([centre[None], *[centre+(boundary-centre)*(i/rings) for i in range(1, rings+1)]])
    x, y = xy.T
    z = coef[0]+coef[1]*x+coef[2]*y+coef[3]*x*x+coef[4]*x*y+coef[5]*y*y
    front = np.c_[xy, z]
    back = front.copy(); back[:, 2] -= thickness_mm
    vertices = np.vstack([front, back]); offset = len(front)
    faces = [[0, 1+i, 1+(i+1)%n] for i in range(n)]
    for ring in range(1, rings):
        a, b = 1+(ring-1)*n, 1+ring*n
        for i in range(n):
            j = (i+1)%n
            faces.extend([[a+i, b+i, b+j], [a+i, b+j, a+j]])
    front_count = len(faces)
    faces += [[c+offset, b+offset, a+offset] for a, b, c in faces.copy()]
    rim = 1+(rings-1)*n
    for i in range(n):
        j = (i+1)%n
        faces.extend([[rim+i, rim+i+offset, rim+j+offset], [rim+i, rim+j+offset, rim+j]])
    triangles = np.asarray(faces, int)
    normals = np.c_[-coef[1]-2*coef[3]*x-coef[4]*y, -coef[2]-coef[4]*x-2*coef[5]*y, np.ones(len(x))]
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    corner_normals = np.zeros((len(triangles), 3, 3))
    corner_normals[:front_count] = normals[triangles[:front_count]]
    corner_normals[front_count:2*front_count] = -normals[triangles[front_count:2*front_count]-offset]
    wall = vertices[triangles[2*front_count:]]
    wn = np.cross(wall[:, 1]-wall[:, 0], wall[:, 2]-wall[:, 0])
    wn /= np.linalg.norm(wn, axis=1, keepdims=True)
    corner_normals[2*front_count:] = wn[:, None]
    edge = vertices[triangles[:front_count]][:, [1, 2, 0]]-vertices[triangles[:front_count]]
    xy_edge = edge[:, :, :2]
    chord = float(np.max(abs(np.einsum('...i,ij,...j->...', xy_edge, hessian, xy_edge))/8))
    edge_max = float(np.linalg.norm(edge, axis=2).max())
    if chord > max_chord_mm*(1+1e-12) or edge_max > max_edge_mm*(1+1e-12):
        raise ValueError('requested optical-surface sampling bounds were not met')
    return {"vertices_mm": vertices, "triangles": triangles, "corner_normals": corner_normals,
            "radial_rings": rings, "outline_samples": n, "max_quadratic_edge_chord_mm": chord,
            "max_optical_edge_mm": edge_max,
            "axial_thickness_mm": float(thickness_mm), "boundary_xy_preserved": True,
            "note": "Analytic surface normals; front/rear are split from the rim. Thickness is axial, not a minimum-normal-thickness certificate."}
