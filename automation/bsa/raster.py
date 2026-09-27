"""Shared BSA renderer: open3d ray casting of a model-frame mesh through a photo Camera.

Every render is exactly consistent with ``reconstruction.camera.project`` (via ``core.project_mm``):

- Pixel convention: the native pixel at (row r, col c) has its CENTRE at the continuous image
  coordinate (u, v) = (c, r) that ``project`` returns. This is also the convention of
  ``reconstruction.camera.render_mask`` (OpenCV fixed-point fill: integer = pixel centre).
- Camera basis (normalized frame): right = (cy, 0, -sy), up = (-sp sy, cp, -sp cy),
  toward = (cp sy, sp, cp cy); orthonormal and right-handed (right x up = toward).
- ``project``: for a normalized point P with camera coordinates (X, Y, Z) = (P.right, P.up,
  P.toward), depth = 1 - p Z, (x, y) = (X, Y) / depth, then roll and scale:
  u = cx + s (cr x - sr y), v = cy - s (sr x + cr y).
- Matching ray model. Invert roll and scale: a = (u - cx) / s, b = (cy - v) / s,
  x = cr a + sr b, y = -sr a + cr b. Every P on the ray satisfies X = x (1 - p Z), Y = y (1 - p Z),
  so P(Z) = x (1 - p Z) right + y (1 - p Z) up + Z toward: a straight line through the eye
  E = toward / p with direction dP/dZ = toward - p (x right + y up). Rays start on the plane
  Z = z0 (z0 = max(1, 1.05 max|P| + 0.01), above every vertex for every camera; clamped to the eye
  1/p) and travel along -dP/dZ. Orthographic (p = 0) is the same formula: origin
  x right + y up + z0 toward, direction -toward.
- ``depth`` output: millimetres along the viewing direction, measured from the plane through the
  NormFrame centre perpendicular to ``toward``: depth_mm = -(P . toward) * extent. Smaller is
  nearer the camera; +inf where the ray misses. (For the yaw 0 / pitch 0 front camera,
  model z_mm = frame.center[2] - depth_mm.)
- ``stride`` > 1 renders a sub-sampled grid: output pixel (i, j) samples the native coordinate
  (x0 + j*stride + (stride-1)/2, y0 + i*stride + (stride-1)/2), i.e. the CENTRE of the
  stride x stride block of native pixels. ``downsample_mask`` block-averages a native mask onto the
  same grid, so a strided render compares like-for-like with a strided reference.
- ``roi`` = (x0, y0, x1, y1) native pixels, half-open; the grid covers only that region.

Rays are only cast inside the projected bounding box of the mesh vertices (exact: a triangle's
image lies inside the convex hull of its projected vertices for any camera in front of the
geometry), so thin frames in large photos render quickly. Scenes are cached by mesh content.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import math
import threading

import numpy as np
import open3d as o3d

from reconstruction.camera import Camera

from .core import NormFrame

INVALID = np.uint32(0xFFFFFFFF)
_CACHE_SIZE = 6


def camera_basis(camera: Camera) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(right, up, toward) unit vectors of ``reconstruction.camera.project`` in the normalized frame."""
    yaw, pitch = math.radians(camera.yaw), math.radians(camera.pitch)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    return (np.array([cy, 0.0, -sy]), np.array([-sp * sy, cp, -sp * cy]), np.array([cp * sy, sp, cp * cy]))


def camera_rays(camera: Camera, u: np.ndarray, v: np.ndarray, z0: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Ray origins and directions (normalized frame, float64) through image points (u, v).

    Origins lie on the plane P.toward = z0 (clamped to the eye for perspective); every point
    O + t D with t >= 0 projects exactly onto (u, v).
    """
    right, up, toward = camera_basis(camera)
    roll = math.radians(camera.roll)
    cr, sr = math.cos(roll), math.sin(roll)
    a = (np.asarray(u, float) - camera.center_x) / camera.scale
    b = (camera.center_y - np.asarray(v, float)) / camera.scale
    x = cr * a + sr * b
    y = -sr * a + cr * b
    p = float(camera.perspective)
    if p > 0:
        z0 = min(float(z0), 1.0 / p)
    k = 1.0 - p * z0
    origins = (x * k)[:, None] * right + (y * k)[:, None] * up + z0 * toward
    directions = p * (x[:, None] * right + y[:, None] * up) - toward
    return origins, directions


def _project_norm(points: np.ndarray, camera: Camera) -> np.ndarray | None:
    """``project`` without its near-plane exception (None when anything is at/behind the eye)."""
    right, up, toward = camera_basis(camera)
    depth = 1.0 - camera.perspective * (points @ toward)
    if np.any(depth <= 1e-6):
        return None
    x, y = (points @ right) / depth, (points @ up) / depth
    roll = math.radians(camera.roll)
    cr, sr = math.cos(roll), math.sin(roll)
    return np.column_stack((camera.center_x + camera.scale * (cr * x - sr * y),
                            camera.center_y - camera.scale * (sr * x + cr * y)))


def grid_shape(shape: tuple[int, int], stride: int = 1, roi: tuple[int, int, int, int] | None = None) -> tuple[int, int]:
    x0, y0, x1, y1 = _roi(shape, roi)
    return (-(-(y1 - y0) // stride), -(-(x1 - x0) // stride))


def pixel_grid(shape: tuple[int, int], stride: int = 1, roi: tuple[int, int, int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Native image coordinates sampled by output columns (u, 1D) and rows (v, 1D)."""
    x0, y0, x1, y1 = _roi(shape, roi)
    gh, gw = grid_shape(shape, stride, roi)
    off = (stride - 1) / 2.0
    return x0 + np.arange(gw) * stride + off, y0 + np.arange(gh) * stride + off


def _roi(shape, roi):
    h, w = int(shape[0]), int(shape[1])
    if roi is None:
        return 0, 0, w, h
    x0, y0, x1, y1 = (int(round(c)) for c in roi)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"Empty region of interest {roi} for shape {shape}")
    return x0, y0, x1, y1


def downsample_mask(mask: np.ndarray, stride: int = 1, roi: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Coverage fraction (float32) of a native bool mask on the grid of ``pixel_grid``.

    Blocks at the right/bottom edge that extend past the image average only their inside pixels.
    """
    x0, y0, x1, y1 = _roi(mask.shape, roi)
    m = np.asarray(mask[y0:y1, x0:x1], np.float32)
    if stride == 1:
        return m
    gh, gw = grid_shape(mask.shape, stride, roi)
    pad = np.zeros((gh * stride, gw * stride), np.float32)
    cnt = np.zeros_like(pad)
    pad[:m.shape[0], :m.shape[1]] = m
    cnt[:m.shape[0], :m.shape[1]] = 1
    s = pad.reshape(gh, stride, gw, stride).sum(axis=(1, 3))
    c = cnt.reshape(gh, stride, gw, stride).sum(axis=(1, 3))
    return s / np.maximum(c, 1)


def mesh_key(V: np.ndarray, F: np.ndarray, frame: NormFrame) -> str:
    h = hashlib.blake2b(digest_size=16)
    for a in (np.ascontiguousarray(V, np.float64), np.ascontiguousarray(F, np.int64)):
        h.update(str(a.shape).encode())
        h.update(a.tobytes())
    h.update(repr((tuple(float(c) for c in frame.center), float(frame.extent))).encode())
    return h.hexdigest()


class RasterScene:
    """A mesh in model millimetres, normalized with its product's NormFrame, ready to ray cast."""

    def __init__(self, V_mm: np.ndarray, F: np.ndarray, frame: NormFrame):
        V_mm = np.asarray(V_mm, np.float64)
        F = np.asarray(F)
        if V_mm.ndim != 2 or V_mm.shape[1] != 3 or F.ndim != 2 or F.shape[1] != 3 or len(F) == 0:
            raise ValueError("Expected V (N,3) and F (M,3) with at least one face")
        if F.min() < 0 or F.max() >= len(V_mm) or not np.isfinite(V_mm).all():
            raise ValueError("Invalid mesh: face index out of range or non-finite vertex")
        self.frame = frame
        self.V_norm = frame.to_norm(V_mm)
        self.F = np.ascontiguousarray(F, np.int32)
        self.n_faces = len(self.F)
        self._scene = o3d.t.geometry.RaycastingScene()
        self._scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(self.V_norm, np.float32)),
                                  o3d.core.Tensor(self.F.astype(np.uint32)))
        # Rays start on the plane P.toward = z0, above every vertex (|P| < z0) for every camera.
        self.max_radius = float(np.max(np.linalg.norm(self.V_norm, axis=1)))
        self.z0 = max(1.0, 1.05 * self.max_radius + 0.01)
        # Convex-hull vertices bound the image of the whole mesh for any camera in front of it
        # (projection maps segments to segments), so culling and the near-plane test use only them.
        self.hull = self.V_norm
        if len(self.V_norm) > 64:
            try:
                from scipy.spatial import ConvexHull
                self.hull = self.V_norm[ConvexHull(self.V_norm).vertices]
            except Exception:          # flat or degenerate input: keep every vertex
                self.hull = self.V_norm

    # ------------------------------------------------------------------ casting
    def cast(self, camera: Camera, u: np.ndarray, v: np.ndarray, *, want_bary: bool = False,
             want_normal: bool = False, want_points: bool = False) -> dict:
        """Cast rays through arbitrary native image points (u, v) (1D arrays of equal length).

        Returns ``hit`` bool, ``face_id`` int32 (-1 miss), ``depth`` float32 mm (inf miss) and, when
        asked, ``bary`` (K,2) float32 (open3d primitive uvs: P = (1-b0-b1) V0 + b0 V1 + b1 V2),
        ``normal`` (K,3) float32 unit geometric normals in the MODEL frame, ``points`` (K,3) float64 mm.
        """
        if camera.perspective * self.max_radius >= 0.95:
            # Same rule as reconstruction.camera.project: nothing may reach depth <= 0.05.
            toward = camera_basis(camera)[2]
            if np.any(1.0 - camera.perspective * (self.hull @ toward) <= 0.05):
                raise ValueError("Geometry crosses the camera near plane")
        u = np.asarray(u, float).ravel()
        v = np.asarray(v, float).ravel()
        out = {"hit": np.zeros(len(u), bool), "face_id": np.full(len(u), -1, np.int32),
               "depth": np.full(len(u), np.inf, np.float32)}
        if want_bary:
            out["bary"] = np.zeros((len(u), 2), np.float32)
        if want_normal:
            out["normal"] = np.zeros((len(u), 3), np.float32)
        if want_points:
            out["points"] = np.full((len(u), 3), np.nan, np.float64)
        if len(u) == 0:
            return out
        origins, directions = camera_rays(camera, u, v, self.z0)
        rays = np.concatenate([origins, directions], axis=1).astype(np.float32)
        ans = self._scene.cast_rays(o3d.core.Tensor(rays))
        prim = ans["primitive_ids"].numpy()
        t = ans["t_hit"].numpy().astype(np.float64)
        hit = (prim != INVALID) & np.isfinite(t)
        out["hit"] = hit
        out["face_id"][hit] = prim[hit].astype(np.int32)
        pts = origins[hit] + t[hit, None] * directions[hit]
        toward = camera_basis(camera)[2]
        out["depth"][hit] = (-(pts @ toward) * self.frame.extent).astype(np.float32)
        if want_bary:
            out["bary"][hit] = ans["primitive_uvs"].numpy()[hit]
        if want_normal:
            out["normal"][hit] = ans["primitive_normals"].numpy()[hit]
        if want_points:
            out["points"][hit] = self.frame.to_mm(pts)
        return out

    def render(self, camera: Camera, shape: tuple[int, int], stride: int = 1,
               roi: tuple[int, int, int, int] | None = None, *, want_bary: bool = False,
               want_normal: bool = False, want_points: bool = False, cull: bool = True) -> dict:
        """Render on the (optionally strided / ROI) grid of a native image of ``shape`` (H, W).

        Returns ``mask`` bool (gh,gw), ``depth`` float32 mm (inf miss), ``face_id`` int32 (-1 miss),
        plus ``bary``/``normal``/``points`` images when asked, and ``grid`` = (u, v) 1D coordinates.
        """
        stride = int(stride)
        if stride < 1:
            raise ValueError("stride must be >= 1")
        us, vs = pixel_grid(shape, stride, roi)
        gh, gw = len(vs), len(us)
        out = {"mask": np.zeros((gh, gw), bool), "depth": np.full((gh, gw), np.inf, np.float32),
               "face_id": np.full((gh, gw), -1, np.int32)}
        if want_bary:
            out["bary"] = np.zeros((gh, gw, 2), np.float32)
        if want_normal:
            out["normal"] = np.zeros((gh, gw, 3), np.float32)
        if want_points:
            out["points"] = np.full((gh, gw, 3), np.nan, np.float64)
        out["grid"] = (us, vs)
        # Exact culling: only grid samples inside the projected vertex bounding box can hit.
        j0, j1, i0, i1 = 0, gw, 0, gh
        if cull:
            pts = _project_norm(self.hull, camera)
            if pts is not None:
                lo, hi = pts.min(axis=0) - 1e-6, pts.max(axis=0) + 1e-6
                j0, j1 = np.searchsorted(us, lo[0], "left"), np.searchsorted(us, hi[0], "right")
                i0, i1 = np.searchsorted(vs, lo[1], "left"), np.searchsorted(vs, hi[1], "right")
                if j1 <= j0 or i1 <= i0:
                    return out
        uu, vv = np.meshgrid(us[j0:j1], vs[i0:i1])
        res = self.cast(camera, uu.ravel(), vv.ravel(), want_bary=want_bary, want_normal=want_normal,
                        want_points=want_points)
        sub = (slice(i0, i1), slice(j0, j1))
        h, w = i1 - i0, j1 - j0
        out["mask"][sub] = res["hit"].reshape(h, w)
        out["depth"][sub] = res["depth"].reshape(h, w)
        out["face_id"][sub] = res["face_id"].reshape(h, w)
        for key, k in (("bary", 2), ("normal", 3), ("points", 3)):
            if key in res:
                out[key][sub] = res[key].reshape(h, w, k)
        return out


_scenes: "OrderedDict[str, RasterScene]" = OrderedDict()
_scenes_lock = threading.Lock()


def get_scene(V_mm: np.ndarray, F: np.ndarray, frame: NormFrame) -> RasterScene:
    """Cached ``RasterScene`` for this exact mesh content + NormFrame (small LRU)."""
    key = mesh_key(V_mm, F, frame)
    with _scenes_lock:
        scene = _scenes.get(key)
        if scene is not None:
            _scenes.move_to_end(key)
            return scene
    scene = RasterScene(V_mm, F, frame)
    with _scenes_lock:
        _scenes[key] = scene
        while len(_scenes) > _CACHE_SIZE:
            _scenes.popitem(last=False)
    return scene


def clear_cache() -> None:
    with _scenes_lock:
        _scenes.clear()


def render(mesh_V_mm: np.ndarray, F: np.ndarray, camera: Camera, frame: NormFrame, shape: tuple[int, int],
           stride: int = 1, roi: tuple[int, int, int, int] | None = None, **kwargs) -> dict:
    """Render a model-frame mesh (mm) through ``camera`` (NormFrame-normalized) at native ``shape``.

    Returns {mask, depth, face_id, grid[, bary, normal, points]}; see ``RasterScene.render``.
    The scene is built once per mesh content and reused (LRU cache).
    """
    return get_scene(mesh_V_mm, F, frame).render(camera, shape, stride, roi, **kwargs)


def view_camera(frame: NormFrame, yaw: float, pitch: float, px_per_mm: float, shape: tuple[int, int],
                roll: float = 0.0, perspective: float = 0.0, center_mm: np.ndarray | None = None) -> Camera:
    """A camera that images ``center_mm`` (default: the NormFrame centre) at the image centre."""
    h, w = shape
    scale = px_per_mm * frame.extent
    cam = Camera(float(yaw), float(pitch), float(roll), float(perspective), float(scale), 0.0, 0.0)
    c = np.zeros((1, 3)) if center_mm is None else frame.to_norm(np.asarray(center_mm, float).reshape(1, 3))
    uv = _project_norm(c, cam)[0]
    return Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale,
                  float((w - 1) / 2.0 - uv[0]), float((h - 1) / 2.0 - uv[1]))
