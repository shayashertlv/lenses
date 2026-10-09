"""Independent dense-area reference for the analytic prism, without mesh clipping.

This is the existing test_temple_measure control, shared with the P1 proof so
the proof actually grades its reported share. The 0.01 mm grid and 0.003
numerical tolerance are unchanged; neither is the product's R1 threshold.
"""
import numpy as np

PRISM_SHARE_NUMERIC_TOL = 0.003


def prism_plane_shares(planes, h=2.0):
    """Area shares within 0.3 mm of candidate planes on a closed-form surface.

    X(d,y) = 64 + max(0, min(h*d/20, h*(40-d)/20, h-h*abs(y)/10))
    before d=40, and 64 thereafter. Surface area uses its numerical derivatives,
    independent of the builder's triangles and the production polygon clipping.
    """
    dd, yy = np.meshgrid(np.arange(2.0 + 0.005, 50.0, 0.01),
                         np.arange(-10.0 + 0.005, 10.0, 0.01), indexing='ij')
    relief = np.minimum(np.minimum(h*dd/20., h*(40.-dd)/20.), h-h*np.abs(yy)/10.)
    X = 64. + np.where(dd < 40., np.maximum(relief, 0.), 0.)
    gx, gy = np.gradient(X, 0.01, 0.01)
    weight = np.sqrt(1. + gx**2 + gy**2)
    points = np.stack([dd, yy, X], -1)
    total = weight.sum()
    return {name: float((weight * (np.abs(points @ normal - offset) <= 0.3)).sum() / total)
            for name, normal, offset in planes}
