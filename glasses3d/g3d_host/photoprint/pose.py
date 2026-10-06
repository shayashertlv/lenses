"""The registration state of WP5 step 0 and the surface S' it implies (WP5_DESIGN §5.2).

Ported from the scratch `design_reg/p2_parallax.AnchorRayWarp`, `geomlib.set_vertices/points` and
`p5_temple_hinge.rotated`; PORT_REVIEW section 10.

- **Cameras**: one per view (``cameras.pack`` parametrisation, unit 100 px, pivot = the front pivot: yaw, pitch, roll in
  degrees, pivot image position u, v in px; perspective and magnification are never changed here).
- **Hinges**: per (view, temple group) an opening angle alpha (deg) about the group's vertical axis through its pivot;
  positive opens the temple (its tip moves away from x = 0) on either side.
- **Parallax**: per region d(s) = a + b s (mm) along the region's ANCHOR view axis (``toward`` of the anchor's camera),
  s in [-1, 1] along the part's stripe axis. A vertex touched by several displaced regions takes the mean of their
  displacements (regions without parallax do not dilute it). Moving along the anchor's axis leaves the anchor's
  projection unchanged up to its perspective (q001, view 05: at most 0.10 mm for 4 mm).

S' for view v = S + parallax, then v's hinges. The GLB keeps S (D5: the parallax is a sampling correction).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import cameras as C
from .. import raster
from ..camera import Camera
from .inputs import face_normals

UNIT_PX = 100.0
CAM_FREE = (0, 1, 2, 5, 6)                     # yaw, pitch, roll, pivot u, pivot v of cameras.pack
CAM_BOUND = (6.0, 4.0, 4.0)                    # deg (§5.2); pivot u/v bound = 3 mm x px/mm
PIVOT_BOUND_MM = 3.0
HINGE_BOUND_DEG = 6.0
PARALLAX_BOUND_MM = 4.0


def pack(job, cam: Camera) -> np.ndarray:
    return C.pack(cam, UNIT_PX, job.pivot)


def unpack(job, x) -> Camera:
    return C.unpack(np.asarray(x, float), UNIT_PX, job.pivot)


def cam_delta(job, c0: Camera, c1: Camera, view: str) -> dict:
    a, b = pack(job, c0), pack(job, c1)
    d = b - a
    ppm = job.ppm(view, c0)
    return {"dyaw_deg": float(d[0]), "dpitch_deg": float(d[1]), "droll_deg": float(d[2]),
            "dpivot_px": [float(d[5]), float(d[6])], "dpivot_mm": float(np.hypot(d[5], d[6]) / ppm),
            "dmag_pct": float(d[4]), "dperspective": float(d[3] / 10.0)}


@dataclass
class Pose:
    cameras: dict[str, Camera]
    hinges: dict[tuple[str, str], float] = field(default_factory=dict)       # (view, group) -> deg
    parallax: dict[int, tuple[float, float]] = field(default_factory=dict)    # region id -> (a, b) mm

    def hinge_state(self, view: str) -> tuple:
        return tuple(sorted((g, round(a, 12)) for (v, g), a in self.hinges.items() if v == view and a != 0.0))

    def parallax_state(self) -> tuple:
        return tuple(sorted((r, round(a, 12), round(b, 12)) for r, (a, b) in self.parallax.items()))

    def copy(self) -> "Pose":
        return Pose(dict(self.cameras), dict(self.hinges), dict(self.parallax))


def hinge_matrix(alpha_deg: float, side: int) -> np.ndarray:
    """Rotation about +y; for side +1 a positive alpha moves a point at -z toward +x (opens), mirrored for side -1."""
    a = np.radians(alpha_deg) * (1.0 if side > 0 else -1.0)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])


class Deformer:
    """S' per view from a Pose. ``axes``: region id -> unit displacement direction (the anchor's toward)."""

    def __init__(self, job, regions, groups, axes: dict[int, np.ndarray]):
        self.job = job
        self.V0 = job.mesh.V
        self.F = job.mesh.F
        self.regions = {r.rid: r for r in regions}
        self.groups = {g.name: g for g in groups if g.side != 0}
        self.axes = {int(k): np.asarray(v, float) / np.linalg.norm(v) for k, v in axes.items()}
        cnt = np.zeros(len(self.V0))
        for rid in self.axes:
            cnt[self.regions[rid].vidx] += 1
        self._count = cnt
        self._cache: dict = {}
        self._scenes: dict = {}

    def base_vertices(self, pose: Pose) -> np.ndarray:
        """S + parallax (no hinge)."""
        key = ("base", pose.parallax_state())
        V = self._cache.get(key)
        if V is None:
            V = self.V0.copy()
            for rid, (a, b) in pose.parallax.items():
                if rid not in self.axes or (a == 0.0 and b == 0.0):
                    continue
                r = self.regions[rid]
                d = (a + b * r.s_vert) / self._count[r.vidx]
                V[r.vidx] += d[:, None] * self.axes[rid][None]
            self._remember(key, V)
        return V

    def vertices(self, pose: Pose, view: str) -> np.ndarray:
        """S' for ``view``: S + parallax, then the view's temple hinges."""
        hs = pose.hinge_state(view)
        if not hs:
            return self.base_vertices(pose)
        key = ("view", pose.parallax_state(), hs)
        V = self._cache.get(key)
        if V is None:
            V = self.base_vertices(pose).copy()
            for gname, alpha in hs:
                g = self.groups[gname]
                R = hinge_matrix(alpha, g.side)
                V[g.vidx] = (V[g.vidx] - g.pivot_mm) @ R.T + g.pivot_mm
            self._remember(key, V)
        return V

    def _remember(self, key, V):
        if len(self._cache) > 32:
            self._cache.clear()
        self._cache[key] = V

    def scene(self, pose: Pose, view: str) -> raster.RasterScene:
        key = (pose.parallax_state(), pose.hinge_state(view))
        sc = self._scenes.get(key)
        if sc is None:
            if len(self._scenes) > 12:
                self._scenes.clear()
            sc = raster.RasterScene(self.vertices(pose, view), self.F, self.job.frame)
            self._scenes[key] = sc
        return sc


def points(V: np.ndarray, F: np.ndarray, face: np.ndarray, bary: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Positions (mm) and outward unit face normals of body samples on the surface with vertices ``V``."""
    tri = V[F[face]]
    P = np.einsum("kj,kjd->kd", bary, tri)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return P, n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)


def bary3(b2: np.ndarray) -> np.ndarray:
    """open3d primitive uvs (b0, b1) -> full barycentrics (1 - b0 - b1, b0, b1)."""
    b = np.asarray(b2, np.float64)
    return np.stack([1 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1)


__all__ = ["Pose", "Deformer", "pack", "unpack", "cam_delta", "hinge_matrix", "points", "bary3", "face_normals"]
