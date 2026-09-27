"""Bounded photographic camera fitting; the candidate mesh stays fixed.

This estimates a camera from silhouette evidence, not a calibrated physical
camera. Multiple shapes/cameras can explain the same mask. Final scoring and
held-out views are required; fitted IoU is not independent reconstruction proof.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time

import cv2
import numpy as np
from scipy import ndimage, optimize

from .mesh import TriangleMesh


@dataclass(frozen=True)
class Camera:
    yaw: float
    pitch: float
    roll: float
    perspective: float
    scale: float
    center_x: float
    center_y: float

    def to_dict(self):
        return asdict(self)


def project(vertices, camera):
    yaw, pitch, roll = np.radians([camera.yaw, camera.pitch, camera.roll])
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right, up, toward = np.array([cy, 0, -sy]), np.array([-sp*sy, cp, -sp*cy]), np.array([cp*sy, sp, cp*cy])
    depth = 1 - camera.perspective * (vertices @ toward)
    if np.any(depth <= 0.05):
        raise ValueError("Geometry crosses the camera near plane")
    x, y = (vertices @ right) / depth, (vertices @ up) / depth
    cr, sr = math.cos(roll), math.sin(roll)
    return np.column_stack((camera.center_x + camera.scale*(cr*x - sr*y),
                            camera.center_y - camera.scale*(sr*x + cr*y)))


def render_mask(mesh: TriangleMesh, camera: Camera, shape: tuple[int, int]):
    height, width = shape
    if min(shape) < 2:
        raise ValueError("Render dimensions must be at least two pixels")
    points = project(mesh.vertices, camera)
    if not np.isfinite(points).all() or np.max(np.abs(points)) > 1e7:
        raise ValueError("Invalid projected geometry")
    # Fixed-point drawing keeps subpixel coordinates instead of rounding vertices
    # to whole pixels, which otherwise erases thin frames during camera fitting.
    pixels = np.rint(points * 16).astype(np.int32)
    image = np.zeros((height, width), dtype=np.uint8)
    for triangle in pixels[mesh.faces]:
        cv2.fillConvexPoly(image, triangle, 1, lineType=cv2.LINE_8, shift=4)
    return image.astype(bool)


def initial_camera(mesh, reference, yaw, pitch=0, roll=0, perspective=0):
    rows, cols = np.nonzero(reference)
    if len(rows) == 0:
        raise ValueError("Cannot fit a camera to an empty reference")
    points = project(mesh.vertices, Camera(yaw, pitch, roll, perspective, 1, 0, 0))
    lo, hi = points.min(axis=0), points.max(axis=0)
    scale = (cols.max() - cols.min() + 1) / max(hi[0] - lo[0], 1e-6)
    center = np.array([(cols.min()+cols.max())/2, (rows.min()+rows.max())/2]) - (hi+lo)/2 * scale
    return Camera(yaw, pitch, roll, perspective, scale, float(center[0]), float(center[1]))


def fit_camera(mesh, reference, *, view="front", max_evaluations=140):
    if reference.ndim != 2 or reference.dtype != bool or not reference.any():
        raise ValueError("Reference must be a nonempty boolean mask")
    if max_evaluations < 20:
        raise ValueError("Camera fitting requires at least 20 evaluations")
    centres = {"front": [0], "back": [180], "left": [90], "right": [-90], "angled": [40, -40],
               "unknown": [0, 45, -45, 90, -90, 135, -135, 180]}
    if view not in centres:
        raise ValueError(f"Unknown view: {view}")
    started = time.monotonic()
    reference_boundary = reference & ~ndimage.binary_erosion(reference)
    distance = ndimage.distance_transform_edt(~reference_boundary)
    rows, cols = np.nonzero(reference)
    reference_width = float(cols.max() - cols.min() + 1)
    history, candidates = [], []

    def loss(camera):
        mask = render_mask(mesh, camera, reference.shape)
        union = np.count_nonzero(mask | reference)
        overlap = np.count_nonzero(mask & reference) / union
        boundary = mask & ~ndimage.binary_erosion(mask)
        edge_error = float(np.mean(distance[boundary])) / reference_width if boundary.any() else 1.0
        result = (1 - overlap) + min(edge_error, 1)
        history.append(result)
        return result

    # Multiple fixed starts keep convergence repeatable and expose side/oblique
    # ambiguity. No candidate geometry or independent X/Y stretch is optimized.
    for yaw in centres[view]:
        for pitch in ((0, 30) if view == 'unknown' else (0, 15, 30)):
            camera = initial_camera(mesh, reference, yaw, pitch)
            candidates.append((loss(camera), camera))
    candidates.sort(key=lambda value: value[0])
    initial_loss, seed = candidates[0]
    best_loss, best_camera = initial_loss, seed
    yaw_center = min(centres[view], key=lambda value: abs(seed.yaw-value))
    width = 50 if view == "angled" else 45 if view == 'unknown' else 35
    bounds = [(yaw_center-width, yaw_center+width), (-20, 55), (-20, 20), (0, .8),
              (math.log(seed.scale*.65), math.log(seed.scale*1.5)),
              (seed.center_x-reference_width*.2, seed.center_x+reference_width*.2),
              (seed.center_y-reference_width*.2, seed.center_y+reference_width*.2)]

    def unpack(values):
        return Camera(*map(float, values[:4]), math.exp(values[4]), float(values[5]), float(values[6]))

    def objective(values):
        nonlocal best_loss, best_camera
        camera = unpack(values)
        value = loss(camera)
        if value < best_loss:
            best_loss, best_camera = value, camera
        return value

    values = [seed.yaw, seed.pitch, seed.roll, seed.perspective, math.log(seed.scale), seed.center_x, seed.center_y]
    result = optimize.minimize(objective, values, method="Powell", bounds=bounds,
                               options={"maxfev": max_evaluations-len(history), "xtol": .003, "ftol": .001})
    boundary_parameters = []
    fitted = [best_camera.yaw, best_camera.pitch, best_camera.roll, best_camera.perspective,
              math.log(best_camera.scale), best_camera.center_x, best_camera.center_y]
    for name, value, (lo, hi) in zip(("yaw", "pitch", "roll", "perspective", "scale", "center_x", "center_y"), fitted, bounds):
        if min(abs(value-lo), abs(value-hi)) < .01*(hi-lo):
            boundary_parameters.append(name)
    return {"camera": best_camera.to_dict(), "initial_camera": seed.to_dict(),
            "view_label_prior": view,
            "seed_hypotheses": [{"camera": candidate.to_dict(), "loss": value} for value, candidate in candidates],
            "seed_scope": "Candidate-dependent footprint hypotheses; only the lowest-loss seed basin is optimized. No semantic view identification.",
            "initial_loss": initial_loss, "final_loss": best_loss, "evaluations": len(history),
            "optimizer_converged": bool(result.success), "optimizer_message": str(result.message),
            "parameters_at_bounds": boundary_parameters, "seconds": time.monotonic()-started,
            "projection": "bounded perspective; zero perspective is orthographic",
            "independent_validation": False,
            "limitations": ["Camera and geometry are ambiguous from silhouettes alone.",
                            "Camera was fitted to this reference; its residual is in-sample.",
                            "Per-photo temple articulation is not yet estimated.",
                            "Materials, transparency and hidden geometry are not evaluated."]}
