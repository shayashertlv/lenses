"""Image-to-image registration and camera transport between photos of the same rigid pose.

Restored from the local tag ``wp6-v1-final`` (where the WP6 v1 basis gave black-side 05's camera with it) and adapted
for the WP6 v2 pose basis: ``tests/proofs/wp6v2_basis.py`` uses ``transport`` to give a colourway twin (the same model
in another colour, shot from the same pose as a calibrated target photo) that photo's camera. The functions
``similarity_matrix``, ``transport_camera``, ``mask_contour`` and ``register_masks`` are the tag's, unchanged;
``bbox_seed``, ``warped_iou`` and ``transport`` are new.

For photos of the same rigid pose, crop/zoom/roll are an image similarity. That transform can transport a camera
without estimating new depth or 3D landmarks. Contour registration is local and conditional: report residuals, check
other features and retain failed starts. It never certifies that two photos share a pose, and the transported camera
inherits every error of the source camera (it is never checked against the model here).
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


# ------------------------------------------------------------------------------------------------ new (WP6 v2)
def _bbox(mask):
    ys, xs = np.nonzero(np.asarray(mask, bool))
    if not len(xs):
        raise ValueError('Expected a mask with foreground')
    return xs.min(), ys.min(), xs.max(), ys.max()


def bbox_seed(source_mask, target_mask):
    """The registration start [a, b, tx, ty] from the two masks' bounding boxes: no rotation, the width ratio as scale,
    the left edges and the vertical centres aligned. A start only: ``register_masks`` refines it locally."""
    a, b = _bbox(source_mask), _bbox(target_mask)
    if a[2] <= a[0]:
        raise ValueError('The source mask has no width')
    s = (b[2] - b[0]) / (a[2] - a[0])
    return [float(s), 0.0, float(b[0] - s * a[0]), float((b[1] + b[3]) / 2 - s * (a[1] + a[3]) / 2)]


def warped_iou(source_mask, target_mask, matrix) -> float:
    """IoU of the target mask with the source mask warped by the source -> target similarity (nearest neighbour)."""
    target = np.asarray(target_mask, bool)
    warped = cv2.warpAffine(np.asarray(source_mask, np.uint8), np.asarray(matrix, float),
                            (target.shape[1], target.shape[0]), flags=cv2.INTER_NEAREST).astype(bool)
    union = int((warped | target).sum())
    return float((warped & target).sum() / union) if union else 0.0


def transport(camera, source_mask, target_mask, start=None, *, max_iterations=80) -> dict:
    """Register the source photo's mask onto the target photo's (``register_masks`` from ``start``, default
    ``bbox_seed``) and transport the source camera through that similarity. Returns {camera (the transported
    Camera), start, parameters, matrix, scale, rotation_deg, the registration's residuals (mean / rms bidirectional px,
    forward and reverse percentiles p50/p90/p95/p99, samples, termination, iterations) and mask_iou_after_warp}. The
    residuals describe photo-to-photo agreement only; they say nothing about how well the camera fits the model."""
    src = np.asarray(source_mask, bool).astype(np.uint8)
    dst = np.asarray(target_mask, bool).astype(np.uint8)
    seed = list(bbox_seed(src, dst) if start is None else np.asarray(start, float).tolist())
    reg = register_masks(src, dst, seed, max_iterations=max_iterations)
    m = np.asarray(reg['matrix'], float)
    return {'camera': transport_camera(camera, m), 'start': seed, 'parameters': reg['parameters'],
            'matrix': reg['matrix'], 'scale': float(math.hypot(m[0, 0], m[1, 0])),
            'rotation_deg': float(math.degrees(math.atan2(m[1, 0], m[0, 0]))),
            'mean_bidirectional_px': reg['mean_bidirectional_px'], 'rms_bidirectional_px': reg['rms_bidirectional_px'],
            'forward_percentiles_px': reg['forward_percentiles_px'],
            'reverse_percentiles_px': reg['reverse_percentiles_px'], 'source_samples': reg['source_samples'],
            'target_samples': reg['target_samples'], 'termination': reg['termination'],
            'iterations': len(reg['history']), 'mask_iou_after_warp': warped_iou(src, dst, m), 'note': reg['note']}
