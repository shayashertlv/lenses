"""Contour-normal proposal residuals, preserving directionless observations.

An image gradient can vanish where opposing thin-object boundaries cancel.
Such a sample has no normal direction: use its complete point residual rather
than asserting, dropping it or treating it as zero error. Image-loss acceptance
and geometric constraints remain the responsibility of the caller.
"""
import numpy as np


def normal_or_point_residual(delta, normals):
    """Project onto a valid unit normal, or retain the full point residual.

    Inputs have shape (N, 2). Normals shorter than .99 retain both coordinates,
    matching the diagnostic aligner's existing unit-normal validity requirement.
    """
    delta = np.asarray(delta, dtype=float)
    normals = np.asarray(normals, dtype=float)
    if delta.ndim != 2 or delta.shape[1] != 2 or normals.shape != delta.shape:
        raise ValueError('delta and normals must be matching (N, 2) arrays')
    if not np.isfinite(delta).all() or not np.isfinite(normals).all():
        raise ValueError('residuals and normals must be finite')
    length = np.linalg.norm(normals, axis=1)
    valid = length > .99
    out = delta.copy()
    # Preserve the established arithmetic for usable normals exactly.
    unit = normals[valid]
    out[valid] = np.sum(delta[valid] * unit, axis=1, keepdims=True) * unit
    return out
