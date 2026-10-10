"""Opt-in image-space junction measurement; no geometry or camera fitting.

The signed outline gap is measured along C -> midpoint(A, B), in units of that
length. It is invariant to 2D similarity transforms, not arbitrary viewpoint
changes. A positive value means the extrapolated crease intersection is outside
the silhouette. Callers must establish the boundary/crease identities, preserve
competing crossings and account for segmentation and camera uncertainty.
"""
import numpy as np


def triangle_outline_gap(a, b, c, boundaries):
    a, b, c = (np.asarray(p, float) for p in (a, b, c))
    if any(p.shape != (2,) or not np.isfinite(p).all() for p in (a, b, c)):
        raise ValueError('Triangle points must be finite 2-vectors')
    direction = (a+b)/2-c
    length = np.linalg.norm(direction)
    ab, ac = b-a, c-a
    if length < 1e-9 or abs(ab[0]*ac[1]-ab[1]*ac[0]) < 1e-9:
        raise ValueError('Degenerate triangle')
    hits = []
    for path in boundaries:
        path = np.asarray(path, float)
        if path.ndim != 2 or path.shape[1] != 2 or not np.isfinite(path).all():
            raise ValueError('Boundary must contain finite 2-vectors')
        for p, q in zip(path[:-1], path[1:]):
            segment = q-p
            den = direction[0]*segment[1]-direction[1]*segment[0]
            if abs(den) < 1e-12*length*max(1., np.linalg.norm(segment)):
                continue
            v = p-c
            t = (v[0]*segment[1]-v[1]*segment[0])/den
            u = (v[0]*direction[1]-v[1]*direction[0])/den
            if -1e-10 <= u <= 1+1e-10:
                hits.append(float(t))
    if not hits:
        raise ValueError('No boundary crossing along triangle axis')
    # Nearest crossing is the local contact. Report all others, never conceal
    # another surface or a reflection that could invalidate this correspondence.
    hits = sorted(set(round(t, 12) for t in hits), key=abs)
    t = hits[0]
    return {'gap_fraction': t, 'gap_px': float(t*length), 'axis_length_px': float(length),
            'contact_px': (c+t*direction).tolist(), 'all_crossing_fractions': hits}
