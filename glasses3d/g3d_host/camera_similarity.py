"""Opt-in image-to-image registration and camera transport.

For photos of the same rigid pose, crop/zoom/roll are an image similarity. That
transform can transport a camera without estimating new depth or 3D landmarks.
Contour registration is local and conditional: report residuals, check other
features and retain failed starts. It never certifies that two photos share a pose.
This module is not connected to the frozen WP6 fitter.
"""
from dataclasses import replace
import math

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree


def similarity_matrix(parameters):
    x = np.asarray(parameters, float)
    if x.shape != (4,) or not np.isfinite(x).all() or np.hypot(*x[:2]) <= 0:
        raise ValueError('Expected finite nonzero similarity [a, b, tx, ty]')
    a, b, tx, ty = x
    return np.array([[a, -b, tx], [b, a, ty]])


def transport_camera(camera, matrix):
    """Apply native-pixel source -> target similarity; keep the same NormFrame.

    Camera roll is counterclockwise in an upward image axis. Pixel matrices use
    a downward vertical axis, so their rotation subtracts from camera roll.
    Reflections, shear and anisotropic scale cannot be represented by this camera.
    """
    matrix = np.asarray(matrix, float)
    if matrix.shape != (2, 3) or not np.isfinite(matrix).all():
        raise ValueError('Expected a finite 2 x 3 image similarity')
    a, b = matrix[0, 0], matrix[1, 0]
    scale = math.hypot(a, b)
    if scale <= 0 or not np.allclose(matrix[:, :2], [[a, -b], [b, a]], rtol=1e-10, atol=1e-12):
        raise ValueError('Camera transport requires an orientation-preserving similarity')
    center = matrix[:, :2] @ [camera.center_x, camera.center_y] + matrix[:, 2]
    return replace(camera, roll=camera.roll-math.degrees(math.atan2(b, a)),
                   scale=camera.scale*scale, center_x=float(center[0]), center_y=float(center[1]))


def mask_contour(mask):
    mask = np.asarray(mask)
    if mask.ndim != 2 or min(mask.shape) < 4 or not np.isin(mask, [0, 1]).all():
        raise ValueError('Expected a binary 2D mask at least 4 pixels wide and high')
    mask = mask.astype(bool)
    edge = mask & ~cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    edge[[0, -1], :] = False
    edge[:, [0, -1]] = False
    y, x = np.nonzero(edge)
    if len(x) < 4:
        raise ValueError('Insufficient observed contour')
    return np.column_stack((x, y)).astype(float)


def register_masks(source_mask, target_mask, start, *, max_iterations=80):
    """Local bidirectional contour ICP for an explicit image-similarity seed.

    Target image cuts are excluded; source contour outside the target viewport
    has no target observation. Cropped source borders likewise are not object
    edges. An input target region unseen in the source is not silently omitted:
    it contributes to the reverse error and may invalidate the same-pose premise.
    Equal total weight per direction; every observed contour sample is retained.
    """
    source, target = mask_contour(source_mask), mask_contour(target_mask)
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError('max_iterations must be a positive integer')
    x = np.asarray(start, float).copy()
    similarity_matrix(x)
    height, width = np.shape(target_mask)
    tree = cKDTree(target)
    history = []
    termination = 'iteration_cap'

    def transform(points, z):
        matrix = similarity_matrix(z)
        return points @ matrix[:, :2].T + matrix[:, 2]

    def visible(q):
        return (q[:, 0] > 2) & (q[:, 0] < width-3) & (q[:, 1] > 2) & (q[:, 1] < height-3)

    for iteration in range(max_iterations):
        q = transform(source, x)
        ids = np.flatnonzero(visible(q))
        if len(ids) < 4:
            raise ValueError('Insufficient source contour in the target viewport')
        dist, match = tree.query(q[ids])
        reverse_dist, reverse_ids = cKDTree(q).query(target)
        weights = np.r_[np.full(len(ids), .5/len(ids)), np.full(len(target), .5/len(target))]**.5
        points = np.vstack((source[ids], source[reverse_ids]))
        observed = np.vstack((target[match], target))

        def residual(z):
            return ((transform(points, z)-observed)*weights[:, None]).ravel()

        result = least_squares(residual, x, x_scale='jac', max_nfev=50,
                               ftol=1e-12, xtol=1e-12, gtol=1e-12)
        history.append({'iteration': iteration, 'parameters_before': x.tolist(),
                        'parameters_after': result.x.tolist(),
                        'matched_rms_before_px': float(np.sqrt((np.mean(dist**2)+np.mean(reverse_dist**2))/2)),
                        'matched_rms_after_px': float(np.linalg.norm(result.fun))})
        change = np.linalg.norm(result.x-x)
        x = result.x
        if change < 1e-7:
            termination = 'parameter_step_converged'
            break
    q = transform(source, x)
    forward = tree.query(q[visible(q)])[0]
    reverse = cKDTree(q).query(target)[0]
    if not len(forward):
        raise ValueError('Fit leaves no source contour visible')
    return {'parameters': x.tolist(), 'matrix': similarity_matrix(x).tolist(),
            'mean_bidirectional_px': float((forward.mean()+reverse.mean())/2),
            'rms_bidirectional_px': float(np.sqrt((np.mean(forward**2)+np.mean(reverse**2))/2)),
            'forward_percentiles_px': np.percentile(forward, [50, 90, 95, 99]).tolist(),
            'reverse_percentiles_px': np.percentile(reverse, [50, 90, 95, 99]).tolist(),
            'source_samples': len(forward), 'target_samples': len(target),
            'termination': termination, 'history': history,
            'note': 'Conditional image similarity; contour agreement does not establish identical physical pose or 3D depth.'}
