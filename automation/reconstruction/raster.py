"""Pixel-center geometry rasterization with depth and original surface points.

This produces the geometric footprint of an opaque triangle scene. It does not
simulate lens transparency or establish which photographic edges are glasses.
Its surface points let frozen image correspondences drive a shared 3D edit.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy import ndimage

from .camera import Camera, project
from .mesh import TriangleMesh


DEPTH_UNITS = 'negative_camera_toward_dot_input_position'


@dataclass
class Raster:
    face_index: np.ndarray
    barycentric: np.ndarray
    depth: np.ndarray

    @property
    def mask(self):
        return self.face_index >= 0

    def surface_points(self, mesh, rows, cols):
        indices = self.face_index[rows, cols]
        if np.any(indices < 0):
            raise ValueError('Requested pixels are outside the geometry')
        return np.einsum('ni,nij->nj', self.barycentric[rows, cols], mesh.vertices[mesh.faces[indices]])


def rasterize(mesh: TriangleMesh, camera: Camera, shape, *, max_candidates=250_000, after_depth=None):
    """Return the first hit, optionally strictly behind a supplied depth field.

    Depth is -dot(surface_position, camera_toward) in INPUT MESH LENGTH UNITS
    for both orthographic and perspective cameras. Perspective-correct surface
    weights are computed before this depth, avoiding cancellation in 1-p*z as
    perspective approaches zero. Smaller depth is nearer the camera; missing
    pixels are +inf. ``after_depth`` must use the same mesh units and pixel grid.
    The caller supplies separation tolerance explicitly; equal-depth seams skip.
    """
    height, width = shape
    if min(shape) < 2 or max_candidates < 1:
        raise ValueError('A nonempty image and positive batch size are required')
    if after_depth is not None:
        after_depth = np.asarray(after_depth, dtype=float)
        if after_depth.shape != tuple(shape) or np.isnan(after_depth).any():
            raise ValueError('Depth peel requires a matching grid without NaN')
    vertices, faces = mesh.vertices, mesh.faces
    screen = project(vertices, camera)
    yaw, pitch = np.radians([camera.yaw, camera.pitch])
    toward = np.array([math.cos(pitch) * math.sin(yaw), math.sin(pitch), math.cos(pitch) * math.cos(yaw)])
    perspective_depth = 1 - camera.perspective * (vertices @ toward)
    linear_depth = -(vertices @ toward)
    triangle = screen[faces]
    x, y = triangle[:, :, 0], triangle[:, :, 1]
    area = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (y[:, 1] - y[:, 0]) * (x[:, 2] - x[:, 0])
    lower = np.ceil(triangle.min(axis=1)).astype(int)
    upper = np.floor(triangle.max(axis=1)).astype(int)
    lower = np.maximum(lower, [0, 0])
    upper = np.minimum(upper, [width - 1, height - 1])
    valid = np.flatnonzero((np.abs(area) > 1e-12) & np.all(upper >= lower, axis=1))
    box_width = upper[valid, 0] - lower[valid, 0] + 1
    box_height = upper[valid, 1] - lower[valid, 1] + 1
    box_area = box_width * box_height
    cumulative = np.cumsum(box_area)
    zbuffer = np.full(height * width, np.inf)
    ids = np.full(height * width, -1, dtype=np.int32)
    barycentric = np.zeros((height * width, 3), dtype=np.float64)
    start = 0
    while start < len(valid):
        stop = max(start + 1, int(np.searchsorted(cumulative, cumulative[start] - box_area[start] + max_candidates, side='right')))
        counts = box_area[start:stop]
        local_tri = np.repeat(np.arange(stop - start), counts)
        face = valid[start:stop][local_tri]
        offset = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        cols = lower[face, 0] + offset % box_width[start:stop][local_tri]
        rows = lower[face, 1] + offset // box_width[start:stop][local_tri]
        tx, ty = x[face], y[face]
        first = ((tx[:, 1] - cols) * (ty[:, 2] - rows) - (ty[:, 1] - rows) * (tx[:, 2] - cols)) / area[face]
        second = ((tx[:, 2] - cols) * (ty[:, 0] - rows) - (ty[:, 2] - rows) * (tx[:, 0] - cols)) / area[face]
        weights = np.column_stack((first, second, 1 - first - second))
        inside = np.all(weights >= -1e-9, axis=1)
        weights, face, cols, rows = weights[inside], face[inside], cols[inside], rows[inside]
        if len(face):
            if camera.perspective:
                weights /= perspective_depth[faces[face]]
                weights /= weights.sum(axis=1)[:, None]
            # The old perspective depth 1-p*z compresses distinct interfaces
            # below any fixed tolerance at small p. Surface-space view depth
            # preserves the same ordering without that ill-conditioned scale.
            z = np.sum(weights * linear_depth[faces[face]], axis=1)
            pixels = rows * width + cols
            if after_depth is not None:
                keep = z > after_depth.ravel()[pixels]
                weights, face, z, pixels = weights[keep], face[keep], z[keep], pixels[keep]
                if not len(face):
                    start = stop
                    continue
            # Deterministic frontmost surface, including overlaps within a batch.
            order = np.lexsort((face, z, pixels))
            ordered_pixels = pixels[order]
            first_at_pixel = np.r_[True, ordered_pixels[1:] != ordered_pixels[:-1]]
            chosen = order[first_at_pixel]
            p = pixels[chosen]
            closer = (z[chosen] < zbuffer[p]) | ((z[chosen] == zbuffer[p]) & ((ids[p] < 0) | (face[chosen] < ids[p])))
            chosen, p = chosen[closer], p[closer]
            zbuffer[p], ids[p], barycentric[p] = z[chosen], face[chosen], weights[chosen]
        start = stop
    return Raster(ids.reshape(shape), barycentric.reshape(*shape, 3), zbuffer.reshape(shape))


def contour_samples(mesh, raster: Raster, *, maximum_points=240, minimum_spacing=3):
    """Deterministic visible-footprint samples and outward image normals.

    Surface points lie at visible pixel centers just inside the raster boundary;
    the discrepancy from a continuous edge is at most pixel sampling precision.
    No semantic labels or independent photographic evidence are generated here.
    """
    mask = raster.mask
    border = mask & ~ndimage.binary_erosion(mask)
    # Image-border truncation is not an observed object boundary.
    border[[0, -1], :] = False
    border[:, [0, -1]] = False
    signed = ndimage.distance_transform_edt(~mask) - ndimage.distance_transform_edt(mask)
    gy, gx = np.gradient(ndimage.gaussian_filter(signed, 1.0))
    rows, cols = np.nonzero(border)
    if not len(rows):
        return {'xy': np.empty((0, 2)), 'xyz': np.empty((0, 3)), 'normals': np.empty((0, 2)), 'faces': np.empty(0, int)}
    # One point per fixed spatial bin, followed by uniform index subsampling.
    bins = np.column_stack((rows // minimum_spacing, cols // minimum_spacing))
    _, selected = np.unique(bins, axis=0, return_index=True)
    if len(selected) > maximum_points:
        selected = selected[np.linspace(0, len(selected) - 1, maximum_points).round().astype(int)]
    rows, cols = rows[selected], cols[selected]
    normals = np.column_stack((gx[rows, cols], gy[rows, cols]))
    norm = np.linalg.norm(normals, axis=1)
    keep = norm > 1e-8
    rows, cols, normals, norm = rows[keep], cols[keep], normals[keep], norm[keep]
    return {'xy': np.column_stack((cols, rows)), 'xyz': raster.surface_points(mesh, rows, cols),
            'normals': normals / norm[:, None], 'faces': raster.face_index[rows, cols]}
