"""S4 `s4_depth`: the generator's front surface, frame thickness and lens surfaces as smooth models.

Inputs (read only): S1 generator (full mesh), S2 outline-source masks and lens polygons, S3 cameras.

1. Front raycast. An ORTHOGRAPHIC +Z grid in the MODEL frame (600 samples per front width, first and
   second hit per ray) on the full generator. Why not the photo camera's rays: the field is stored and
   used as a height field over model (x, y); a +Z grid samples it uniformly and independently of the
   camera (INVU's front camera comes from a 175 px photo with a weakly constrained perspective), and the
   photo pixels are placed on it later by an exact ray/surface intersection (``lift_px``), so nothing is
   lost. The camera is used only to label samples as frame / lens through the S2 masks.
2. Front-most depth-continuous component (4-neighbours closer than 3 mm in depth), limited to the S1
   front piece (+5 mm).
3. Wrap: robust z = a + b x + c y + e x^2 on the frame and lens samples; R = -1/(2e). R < 150 mm ->
   CYLINDER: the grid is re-cast radially toward a vertical axis (x_c, z_c) and every model below is a
   function d(s, y) of arc length s = R phi and height y, where d is the radial offset from the base
   cylinder. Otherwise PLANAR with s = x and d = z.
4. Front surface = robust (soft-L1) low-order biquadratic + robust tensor cubic B-spline residual with
   8 mm knots (P-spline: second-difference smoothing plus a ridge, so the residual decays to 0 away from
   the generator coverage and the surface EXTRAPOLATES with the low-order fit), fitted on FRAME samples
   only (frame mask eroded 0.5 mm): lens plates sit behind the rim and would bias it.
5. Thickness = entry/exit distance along the grid ray on frame samples, same robust spline, clamped per
   stroke class: acetate 1.5-9 mm, metal 0.8-2.5 mm where the S2 stroke width < 2 mm.
6. Lens surfaces: plate hits inside each S2 lens polygon eroded 1.5 mm (and within -8/+3 mm of the
   front surface), robust quartic d(s, y). Convexity clamp: a principal (s or y) radius that is concave
   is replaced by a base-4 curve (R = 132.5 mm), one tighter than 50 mm is clamped to 50 mm, and the
   offset/tilt are refitted with the curvatures fixed. < 2000 hits -> base-curve sphere.

Library: ``load_depth(product, run) -> DepthField`` (``.z``, ``.thickness``, ``.lens_z``, surfaces for
lifting, ``.plate_back_d`` / ``.plate_back_surface``: the plate's back = front - thickness along the depth axis,
where S6's back cap lies and S5's donor geometry starts) and ``lift_px(px, camera, frame, surface) -> (K, 3) mm``
(exact for the Camera model).
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable

import cv2
import numpy as np
import open3d as o3d
from scipy import ndimage, sparse
from scipy.sparse.csgraph import connected_components

from reconstruction.camera import Camera

from . import core, raster
from .core import NormFrame, stage_dir

STAGE = "s4_depth"
SAMPLES_PER_WIDTH = 600
KNOT_MM = 8.0
CYLINDER_MAX_R_MM = 150.0
CONTINUITY_MM = 3.0
FRONT_PIECE_MARGIN_MM = 5.0
FRAME_ERODE_MM = 0.5
LENS_ERODE_MM = 1.5
LENS_BAND_BEHIND_MM = 8.0
LENS_BAND_FRONT_MM = 3.0
MIN_LENS_HITS = 2000
BASE4_R_MM = 132.5            # base-4 front curve at n = 1.53: (1.53 - 1) / 4 D
MIN_LENS_R_MM = 50.0
CONCAVE_TOL_R_MM = 1000.0     # a "concave" radius flatter than this counts as flat (tolerated)
SOFT_L1_LOW_MM = 0.5
SOFT_L1_FRONT_MM = 0.3
SOFT_L1_THICK_MM = 0.5
SOFT_L1_LENS_MM = 0.2
LAMBDA2_REL = 0.05            # P-spline second-difference weight (relative to the mean data weight)
LAMBDA1_REL = 0.05            # first-difference (membrane) weight: no overshoot across lens holes
LAMBDA0_REL = 0.005           # ridge: the residual decays to the low-order fit away from coverage
ACETATE_T_MM = (1.5, 9.0)
METAL_T_MM = (0.8, 2.5)
METAL_STROKE_MM = 2.0
STROKE_RUN_MM = 3.0           # a stroke is "thin" only if it stays < 2 mm wide for this long
EXTRAPOLATED_MAX = 0.10
MARCH_STEP_MM = 0.5
LOW_TERMS = tuple((i, j) for i in range(3) for j in range(3))                        # biquadratic
QUARTIC_TERMS = tuple((i, j) for i in range(5) for j in range(5) if i + j <= 4)
_EPS_MM = 0.01


# =========================================================================== parametrisation
@dataclass(frozen=True)
class Base:
    """Planar (s = x, d = z) or vertical-cylinder (s = R phi, d = rho - R) parametrisation of the front."""
    kind: str = "planar"
    R: float = 0.0
    xc: float = 0.0
    zc: float = 0.0

    def to_param(self, P) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        P = np.asarray(P, float)
        if self.kind == "planar":
            return P[..., 0], P[..., 1], P[..., 2]
        dx, dz = P[..., 0] - self.xc, P[..., 2] - self.zc
        return self.R * np.arctan2(dx, dz), P[..., 1], np.hypot(dx, dz) - self.R

    def from_param(self, s, y, d) -> np.ndarray:
        s, y, d = np.broadcast_arrays(np.asarray(s, float), np.asarray(y, float), np.asarray(d, float))
        if self.kind == "planar":
            return np.stack([s, y, d], -1)
        phi, rho = s / self.R, self.R + d
        return np.stack([self.xc + rho * np.sin(phi), y, self.zc + rho * np.cos(phi)], -1)

    def outward(self, s) -> np.ndarray:
        """Unit direction of increasing d at arc position s."""
        s = np.asarray(s, float)
        if self.kind == "planar":
            return np.broadcast_to(np.array([0.0, 0.0, 1.0]), s.shape + (3,)).copy()
        phi = s / self.R
        return np.stack([np.sin(phi), np.zeros_like(phi), np.cos(phi)], -1)

    def curvature_s(self, d_ss, d) -> np.ndarray:
        """3D curvature (convex toward +d is positive) along s of the surface d(s)."""
        if self.kind == "planar":
            return -np.asarray(d_ss, float)
        rho = self.R + np.asarray(d, float)
        return 1.0 / rho - np.asarray(d_ss, float) * (self.R / rho) ** 2

    def d_ss_for(self, kappa, d) -> float:
        if self.kind == "planar":
            return -float(kappa)
        rho = self.R + float(d)
        return (1.0 / rho - float(kappa)) * (rho / self.R) ** 2

    def to_dict(self) -> dict:
        return {"kind": self.kind, "R": self.R, "xc": self.xc, "zc": self.zc}

    @staticmethod
    def from_dict(d: dict) -> "Base":
        return Base(d["kind"], float(d["R"]), float(d["xc"]), float(d["zc"]))


# =========================================================================== smooth models
@dataclass(frozen=True)
class BSpline2D:
    """Uniform tensor-product cubic B-spline on [s0, s0 + (ns-3) h] x [y0, y0 + (ny-3) h]; constant
    extension outside (the argument is clamped to the domain)."""
    s0: float
    y0: float
    h: float
    ns: int
    ny: int

    @staticmethod
    def covering(smin: float, smax: float, ymin: float, ymax: float, h: float) -> "BSpline2D":
        ns = max(1, int(math.ceil((smax - smin) / h))) + 3
        ny = max(1, int(math.ceil((ymax - ymin) / h))) + 3
        return BSpline2D(float(smin), float(ymin), float(h), ns, ny)

    @property
    def size(self) -> int:
        return self.ns * self.ny

    def _axis(self, x, x0, n):
        t = np.clip((np.asarray(x, float) - x0) / self.h, 0.0, n - 3 - 1e-9)
        i = np.floor(t).astype(np.int64)
        u = t - i
        u2 = u * u
        u3 = u2 * u
        w = np.stack([(1 - u) ** 3, 3 * u3 - 6 * u2 + 4, -3 * u3 + 3 * u2 + 3 * u + 1, u3], -1) / 6.0
        return i, w

    def design(self, s, y) -> sparse.csr_matrix:
        s, y = np.ravel(s), np.ravel(y)
        i, ws = self._axis(s, self.s0, self.ns)
        j, wy = self._axis(y, self.y0, self.ny)
        k4 = np.arange(4)
        cols = ((i[:, None] + k4)[:, :, None] * self.ny + (j[:, None] + k4)[:, None, :]).reshape(len(s), 16)
        vals = (ws[:, :, None] * wy[:, None, :]).reshape(len(s), 16)
        rows = np.repeat(np.arange(len(s)), 16)
        return sparse.csr_matrix((vals.ravel(), (rows, cols.ravel())), shape=(len(s), self.size))

    def eval(self, coef: np.ndarray, s, y) -> np.ndarray:
        s, y = np.broadcast_arrays(np.asarray(s, float), np.asarray(y, float))
        shape = s.shape
        s, y = s.ravel(), y.ravel()
        C = np.asarray(coef, float).reshape(self.ns, self.ny)
        out = np.empty(len(s))
        k4 = np.arange(4)
        for a in range(0, len(s), 262144):
            b = a + 262144
            i, ws = self._axis(s[a:b], self.s0, self.ns)
            j, wy = self._axis(y[a:b], self.y0, self.ny)
            G = C[(i[:, None] + k4)[:, :, None], (j[:, None] + k4)[:, None, :]]
            out[a:b] = np.einsum("na,nb,nab->n", ws, wy, G)
        return out.reshape(shape)

    def penalty(self, lam2: float, lam0: float, lam1: float = 0.0) -> sparse.csr_matrix:
        def dk(n, k):
            if n <= k:
                return sparse.csr_matrix((0, n))
            coeffs = ([-1.0, 1.0], [1.0, -2.0, 1.0])[k - 1]
            return sparse.diags([c * np.ones(n - k) for c in coeffs], list(range(k + 1)), shape=(n - k, n))
        out = lam0 * sparse.identity(self.size)
        for k, lam in ((1, lam1), (2, lam2)):
            if lam:
                Ds = sparse.kron(dk(self.ns, k), sparse.identity(self.ny))
                Dy = sparse.kron(sparse.identity(self.ns), dk(self.ny, k))
                out = out + lam * (Ds.T @ Ds + Dy.T @ Dy)
        return out.tocsr()

    def to_dict(self) -> dict:
        return {"s0": self.s0, "y0": self.y0, "h": self.h, "ns": self.ns, "ny": self.ny}

    @staticmethod
    def from_dict(d: dict) -> "BSpline2D":
        return BSpline2D(float(d["s0"]), float(d["y0"]), float(d["h"]), int(d["ns"]), int(d["ny"]))


@dataclass(frozen=True)
class Poly2D:
    """Monomials ((s - sc)/scale)^i ((y - yc)/scale)^j."""
    terms: tuple
    sc: float = 0.0
    yc: float = 0.0
    scale: float = 30.0

    def design(self, s, y) -> np.ndarray:
        a = (np.asarray(s, float) - self.sc) / self.scale
        b = (np.asarray(y, float) - self.yc) / self.scale
        return np.stack([a ** i * b ** j for i, j in self.terms], -1)

    def eval(self, coef, s, y) -> np.ndarray:
        return self.design(s, y) @ np.asarray(coef, float)

    def second(self, coef, s, y) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(d_ss, d_yy, d_sy) in 1/mm."""
        a = (np.asarray(s, float) - self.sc) / self.scale
        b = (np.asarray(y, float) - self.yc) / self.scale
        dss = np.zeros(np.broadcast(a, b).shape)
        dyy = np.zeros_like(dss)
        dsy = np.zeros_like(dss)
        for c, (i, j) in zip(np.asarray(coef, float), self.terms):
            if i >= 2:
                dss = dss + c * i * (i - 1) * a ** (i - 2) * b ** j
            if j >= 2:
                dyy = dyy + c * j * (j - 1) * a ** i * b ** (j - 2)
            if i >= 1 and j >= 1:
                dsy = dsy + c * i * j * a ** (i - 1) * b ** (j - 1)
        k = 1.0 / self.scale ** 2
        return dss * k, dyy * k, dsy * k

    def to_dict(self) -> dict:
        return {"terms": [list(t) for t in self.terms], "sc": self.sc, "yc": self.yc, "scale": self.scale}

    @staticmethod
    def from_dict(d: dict) -> "Poly2D":
        return Poly2D(tuple(tuple(int(v) for v in t) for t in d["terms"]), float(d["sc"]), float(d["yc"]), float(d["scale"]))


@dataclass
class SmoothField:
    """low-order polynomial + optional B-spline residual, as a function of (s, y)."""
    low: Poly2D
    low_coef: np.ndarray
    spline: BSpline2D | None = None
    spline_coef: np.ndarray | None = None

    def __call__(self, s, y) -> np.ndarray:
        out = self.low.eval(self.low_coef, s, y)
        if self.spline is not None:
            out = out + self.spline.eval(self.spline_coef, s, y)
        return out

    def low_only(self, s, y) -> np.ndarray:
        return self.low.eval(self.low_coef, s, y)


def soft_l1_weights(r: np.ndarray, f_scale: float) -> np.ndarray:
    """IRLS weights of the soft-L1 loss 2 f^2 (sqrt(1 + (r/f)^2) - 1)."""
    return 1.0 / np.sqrt(1.0 + (np.asarray(r, float) / f_scale) ** 2)


def robust_poly(poly: Poly2D, s, y, d, f_scale: float, iters: int = 15,
                w0: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    A = poly.design(s, y)
    d = np.asarray(d, float)
    w0 = np.ones(len(d)) if w0 is None else np.asarray(w0, float)
    w = w0.copy()
    coef = np.zeros(A.shape[1])
    for _ in range(iters):
        sw = np.sqrt(w)
        coef = np.linalg.lstsq(A * sw[:, None], d * sw, rcond=None)[0]
        w = w0 * soft_l1_weights(d - A @ coef, f_scale)
    return coef, d - A @ coef


def robust_spline(spline: BSpline2D, s, y, d, f_scale: float, iters: int = 10,
                  lam2_rel: float = LAMBDA2_REL, lam0_rel: float = LAMBDA0_REL,
                  lam1_rel: float = LAMBDA1_REL) -> tuple[np.ndarray, np.ndarray]:
    """Soft-L1 P-spline fit (IRLS); the penalty is fixed relative to the mean unweighted data weight."""
    B = spline.design(s, y)
    d = np.asarray(d, float)
    diag = np.asarray(B.multiply(B).sum(axis=0)).ravel()
    ref = float(diag[diag > 0].mean()) if np.any(diag > 0) else 1.0
    P = spline.penalty(lam2_rel * ref, lam0_rel * ref, lam1_rel * ref).toarray()
    w = np.ones(len(d))
    coef = np.zeros(spline.size)
    for _ in range(iters):
        BtW = B.T.multiply(w).tocsr()
        A = (BtW @ B).toarray() + P
        coef = np.linalg.solve(A, BtW @ d)
        w = soft_l1_weights(d - B @ coef, f_scale)
    return coef, d - B @ coef


def fit_field(s, y, d, f_low: float, f_spline: float, domain: tuple[float, float, float, float],
              knot_mm: float = KNOT_MM, centre: tuple[float, float] = (0.0, 0.0)) -> tuple[SmoothField, np.ndarray]:
    """Robust low-order biquadratic, then the robust B-spline residual on ``domain`` (+2 knots)."""
    low = Poly2D(LOW_TERMS, centre[0], centre[1], 30.0)
    c_low, r_low = robust_poly(low, s, y, d, f_low)
    smin, smax, ymin, ymax = domain
    sp = BSpline2D.covering(smin - 2 * knot_mm, smax + 2 * knot_mm, ymin - 2 * knot_mm, ymax + 2 * knot_mm, knot_mm)
    c_sp, r = robust_spline(sp, s, y, r_low, f_spline)
    return SmoothField(low, c_low, sp, c_sp), r


def residual_stats(r: np.ndarray) -> dict:
    a = np.abs(np.asarray(r, float))
    if not len(a):
        return {"n": 0}
    return {"n": int(len(a)), "median_mm": round(float(np.median(a)), 4), "p95_mm": round(float(np.percentile(a, 95)), 4),
            "rms_mm": round(float(np.sqrt(np.mean(a ** 2))), 4)}


# =========================================================================== surfaces + lifting
class ParamSurface:
    """The surface d = fn(s, y) in a Base parametrisation. ``signed(P)`` > 0 in front (outside)."""

    def __init__(self, base: Base, fn: Callable, s_range: tuple[float, float] | None = None,
                 box: tuple | None = None, name: str = ""):
        self.base, self.fn, self.name = base, fn, name
        self.s_range = s_range
        self.box = box

    def d(self, s, y) -> np.ndarray:
        return self.fn(s, y)

    def point(self, s, y) -> np.ndarray:
        return self.base.from_param(s, y, self.fn(s, y))

    def signed(self, P) -> np.ndarray:
        s, y, d = self.base.to_param(P)
        out = d - self.fn(s, y)
        if self.s_range is not None:
            out = np.where((s < self.s_range[0]) | (s > self.s_range[1]), np.nan, out)
        return out

    def normal(self, P, h: float = 0.05) -> np.ndarray:
        s, y, _ = self.base.to_param(P)
        Ps = self.point(s + h, y) - self.point(s - h, y)
        Py = self.point(s, y + h) - self.point(s, y - h)
        n = np.cross(Ps, Py)
        n *= np.sign(np.sum(n * self.base.outward(s), axis=-1, keepdims=True) + 1e-300)
        return n / np.linalg.norm(n, axis=-1, keepdims=True)

    def shifted(self, delta: Callable | float, name: str = "") -> "ParamSurface":
        fn = self.fn
        if callable(delta):
            return ParamSurface(self.base, lambda s, y: fn(s, y) + delta(s, y), self.s_range, self.box, name or self.name)
        dv = float(delta)
        return ParamSurface(self.base, lambda s, y: fn(s, y) + dv, self.s_range, self.box, name or self.name)


def _as_surface(surface_fn) -> ParamSurface:
    if isinstance(surface_fn, ParamSurface):
        return surface_fn
    if callable(surface_fn):          # a height field z = f(x, y)
        return ParamSurface(Base("planar"), surface_fn, None, None, "height_field")
    raise TypeError("surface_fn must be a ParamSurface or a callable z = f(x, y)")


def _slab(O: np.ndarray, D: np.ndarray, box) -> tuple[np.ndarray, np.ndarray]:
    tmin = np.zeros(len(O))
    tmax = np.full(len(O), np.inf)
    for k in range(3):
        lo, hi = box[k]
        if not (np.isfinite(lo) or np.isfinite(hi)):
            continue
        dk = D[:, k]
        par = np.abs(dk) < 1e-12
        with np.errstate(divide="ignore", invalid="ignore"):
            t1 = (lo - O[:, k]) / dk
            t2 = (hi - O[:, k]) / dk
        a, b = np.minimum(t1, t2), np.maximum(t1, t2)
        inside = (O[:, k] >= lo) & (O[:, k] <= hi)
        a = np.where(par, np.where(inside, -np.inf, np.inf), a)
        b = np.where(par, np.where(inside, np.inf, -np.inf), b)
        tmin, tmax = np.maximum(tmin, a), np.minimum(tmax, b)
    return tmin, tmax


def intersect_rays(surface: ParamSurface, O: np.ndarray, D: np.ndarray, box=None, step: float = MARCH_STEP_MM,
                   iters: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """Front-most crossing of unit rays O + t D (mm) with the surface.

    Marches from the FRONT end of each ray segment inside ``box`` (the camera end when the ray points
    toward -Z, the far end otherwise) to the first + -> - sign change of ``signed``, then bisects.
    Returns (points (K,3) with NaN on a miss, t (K,))."""
    O = np.asarray(O, float).reshape(-1, 3)
    D = np.asarray(D, float).reshape(-1, 3)
    box = box if box is not None else (surface.box if surface.box is not None else
                                       ((-np.inf, np.inf), (-np.inf, np.inf), (-400.0, 400.0)))
    t0, t1 = _slab(O, D, box)
    ok = np.isfinite(t0) & np.isfinite(t1) & (t1 > t0)
    t_out = np.full(len(O), np.nan)
    if not ok.any():
        return np.full((len(O), 3), np.nan), t_out
    n = int(math.ceil(float(np.max((t1 - t0)[ok])) / step)) + 1
    idx_all = np.nonzero(ok)[0]
    chunk = max(1, 3_000_000 // (n + 1))
    frac = np.linspace(0.0, 1.0, n + 1)
    for a in range(0, len(idx_all), chunk):
        idx = idx_all[a:a + chunk]
        forward = D[idx, 2] <= 0            # looking toward -Z: the front is at the near end
        ta = np.where(forward, t0[idx], t1[idx])
        tb = np.where(forward, t1[idx], t0[idx])
        T = ta[:, None] + (tb - ta)[:, None] * frac[None, :]
        P = O[idx, None, :] + T[..., None] * D[idx, None, :]
        g = surface.signed(P.reshape(-1, 3)).reshape(T.shape)
        cross = (g[:, :-1] > 0) & (g[:, 1:] <= 0) & np.isfinite(g[:, :-1]) & np.isfinite(g[:, 1:])
        has = cross.any(axis=1)
        k = np.argmax(cross, axis=1)
        rows = np.nonzero(has)[0]
        lo = T[rows, k[rows]]
        hi = T[rows, k[rows] + 1]
        Or, Dr = O[idx[rows]], D[idx[rows]]
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            gm = surface.signed(Or + mid[:, None] * Dr)
            front = gm > 0
            lo = np.where(front, mid, lo)
            hi = np.where(front, hi, mid)
        t_out[idx[rows]] = 0.5 * (lo + hi)
    P = O + t_out[:, None] * D
    return P, t_out


def closest_on_rays(surface: ParamSurface, O: np.ndarray, D: np.ndarray, box=None, step: float = MARCH_STEP_MM,
                    iters: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """For rays that do not cross the surface: the point of each ray (inside ``box``) where |signed| is
    smallest (the grazing point just outside the surface's silhouette). Returns (points, |signed| there)."""
    O = np.asarray(O, float).reshape(-1, 3)
    D = np.asarray(D, float).reshape(-1, 3)
    box = box if box is not None else (surface.box if surface.box is not None else
                                       ((-np.inf, np.inf), (-np.inf, np.inf), (-400.0, 400.0)))
    t0, t1 = _slab(O, D, box)
    ok = np.isfinite(t0) & np.isfinite(t1) & (t1 > t0)
    P = np.full((len(O), 3), np.nan)
    g_out = np.full(len(O), np.inf)
    if not ok.any():
        return P, g_out
    idx = np.nonzero(ok)[0]
    n = int(math.ceil(float(np.max((t1 - t0)[ok])) / step)) + 1
    frac = np.linspace(0.0, 1.0, n + 1)
    T = t0[idx, None] + (t1 - t0)[idx, None] * frac[None, :]
    g = np.abs(surface.signed((O[idx, None, :] + T[..., None] * D[idx, None, :]).reshape(-1, 3))).reshape(T.shape)
    g = np.where(np.isfinite(g), g, np.inf)
    k = np.argmin(g, axis=1)
    lo = T[np.arange(len(idx)), np.maximum(k - 1, 0)]
    hi = T[np.arange(len(idx)), np.minimum(k + 1, n)]
    phi = (math.sqrt(5) - 1) / 2                     # golden-section refinement of the minimum
    for _ in range(iters):
        a = hi - phi * (hi - lo)
        b = lo + phi * (hi - lo)
        ga = np.abs(surface.signed(O[idx] + a[:, None] * D[idx]))
        gb = np.abs(surface.signed(O[idx] + b[:, None] * D[idx]))
        ga = np.where(np.isfinite(ga), ga, np.inf)
        gb = np.where(np.isfinite(gb), gb, np.inf)
        left = ga < gb
        hi = np.where(left, b, hi)
        lo = np.where(left, lo, a)
    t = 0.5 * (lo + hi)
    P[idx] = O[idx] + t[:, None] * D[idx]
    g_out[idx] = np.abs(surface.signed(P[idx]))
    return P, g_out


@dataclass(frozen=True)
class SourceView:
    """The camera of S2's outline-source pixels: the front camera, or the back camera read through a
    horizontal mirror (u_back = W - 1 - u) when S2's outline source is the mirrored back photo."""
    view: str
    camera: Camera
    shape: tuple[int, int]
    mirrored: bool = False

    @property
    def mirror_width(self) -> int | None:
        return int(self.shape[1]) if self.mirrored else None

    def project(self, P_mm: np.ndarray, frame: NormFrame) -> np.ndarray:
        uv = raster._project_norm(frame.to_norm(np.asarray(P_mm, float).reshape(-1, 3)), self.camera)
        if uv is None:
            raise ValueError("Geometry crosses the camera near plane")
        if self.mirrored:
            uv[:, 0] = (self.shape[1] - 1) - uv[:, 0]
        return uv


def camera_rays_mm(camera: Camera, frame: NormFrame, px: np.ndarray, mirror_width: int | None = None
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Unit rays (mm, model frame) through native pixel centres (u, v) of ``camera`` (exact Camera model)."""
    px = np.asarray(px, float).reshape(-1, 2)
    u = px[:, 0] if mirror_width is None else (mirror_width - 1) - px[:, 0]
    O, D = raster.camera_rays(camera, u, px[:, 1], 5.0)
    O_mm = frame.to_mm(O)
    D_mm = D * frame.extent
    return O_mm, D_mm / np.linalg.norm(D_mm, axis=1, keepdims=True)


def lift_px(px, camera: Camera, frame: NormFrame, surface_fn, *, mirror_width: int | None = None,
            box=None) -> np.ndarray:
    """(K, 3) model mm: the ray from ``camera`` through each native pixel (u, v) intersected with the
    surface (a ``ParamSurface`` or a height field z = f(x, y)); the front-most crossing; NaN on a miss.
    Exact for the Camera model: ``core.project_mm`` of the result returns ``px``."""
    surface = _as_surface(surface_fn)
    O, D = camera_rays_mm(camera, frame, px, mirror_width)
    P, _ = intersect_rays(surface, O, D, box)
    return P


# =========================================================================== the depth field
@dataclass
class LensModel:
    """d = poly(s, y); a pair's second lens is the first one's model mirrored about s = mirror_s."""
    index: int
    side: str
    poly: Poly2D
    coef: np.ndarray
    kind: str
    mirror_s: float | None = None

    def __call__(self, s, y) -> np.ndarray:
        if self.mirror_s is not None:
            s = 2.0 * self.mirror_s - np.asarray(s, float)
        return self.poly.eval(self.coef, s, y)


class DepthField:
    """S4 result as callables. ``z``/``thickness``/``lens_z`` take model (x, y) in mm."""

    def __init__(self, base: Base, front: SmoothField, s_axis: np.ndarray, y_axis: np.ndarray,
                 thick: np.ndarray, lenses: list[LensModel], box, result: dict | None = None):
        self.base, self.front, self.lenses, self.box = base, front, lenses, box
        self.s_axis, self.y_axis, self.thick = np.asarray(s_axis, float), np.asarray(y_axis, float), np.asarray(thick, float)
        self.result = result or {}
        s_range = None
        if base.kind == "cylinder":
            s_range = (-0.75 * math.pi * base.R, 0.75 * math.pi * base.R)
        self.s_range = s_range

    def front_surface(self) -> ParamSurface:
        return ParamSurface(self.base, self.front, self.s_range, self.box, "front")

    def lens_surface(self, i: int) -> ParamSurface:
        return ParamSurface(self.base, self.lenses[i - 1], self.s_range, self.box, f"lens{i}")

    def _vertical(self, surface: ParamSurface, x, y) -> np.ndarray:
        x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
        shape = x.shape
        if self.base.kind == "planar":
            return surface.fn(x, y)
        O = np.stack([x.ravel(), y.ravel(), np.full(x.size, self.box[2][1])], -1)
        D = np.tile([0.0, 0.0, -1.0], (x.size, 1))
        P, _ = intersect_rays(surface, O, D, self.box)
        return P[:, 2].reshape(shape)

    def z(self, x, y) -> np.ndarray:
        return self._vertical(self.front_surface(), x, y)

    def lens_z(self, i: int, x, y) -> np.ndarray:
        return self._vertical(self.lens_surface(i), x, y)

    def thickness_param(self, s, y) -> np.ndarray:
        s, y = np.broadcast_arrays(np.asarray(s, float), np.asarray(y, float))
        fs = np.interp(s, self.s_axis, np.arange(len(self.s_axis)))
        fy = np.interp(y, self.y_axis[::-1], np.arange(len(self.y_axis))[::-1]) if self.y_axis[0] > self.y_axis[-1] \
            else np.interp(y, self.y_axis, np.arange(len(self.y_axis)))
        return ndimage.map_coordinates(self.thick, [fy.ravel(), fs.ravel()], order=1, mode="nearest").reshape(s.shape)

    def thickness_at(self, P) -> np.ndarray:
        s, y, _ = self.base.to_param(P)
        return self.thickness_param(s, y)

    def plate_back_d(self, s, y) -> np.ndarray:
        """Param depth of the plate's back surface: the front surface minus the S4 plate thickness along the depth
        axis (where S6's back cap lies away from the rim-hold band, and where S5's donor geometry starts)."""
        return self.front(s, y) - self.thickness_param(s, y)

    def plate_back_surface(self) -> ParamSurface:
        return ParamSurface(self.base, self.plate_back_d, self.s_range, self.box, "plate_back")

    def thickness(self, x, y) -> np.ndarray:
        x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
        z = self.z(x, y)
        return self.thickness_at(np.stack([x, y, z], -1))


# =========================================================================== stage helpers
def source_view(product: str, run: str = "m1") -> SourceView:
    from . import cameras
    s2, _ = stage_dir(run, product, "s2_front").load()
    _, cams, _ = cameras.load_cameras(product, run)
    shape = tuple(int(v) for v in s2["source_shape"])
    if s2["outline_source"] == "back_mirrored":
        return SourceView("back", cams["back"], shape, True)
    return SourceView("front", cams["front"], shape, False)


def lens_polys(a2: dict) -> list[np.ndarray]:
    out, i = [], 1
    while f"lens{i}_poly" in a2:
        out.append(np.asarray(a2[f"lens{i}_poly"], float))
        i += 1
    return out


def poly_mask(polys: list[np.ndarray], shape: tuple[int, int]) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.round(np.asarray(p) * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    return m.astype(bool)


def erode_mm(mask: np.ndarray, mm: float, px_per_mm: float) -> np.ndarray:
    r = mm * px_per_mm
    if r <= 0.5:
        return mask.copy()
    return ndimage.distance_transform_edt(mask) > r


def local_thickness_px(mask: np.ndarray, step_px: float = 1.0) -> np.ndarray:
    """Local thickness (diameter of the largest inscribed disk containing the pixel), in px."""
    out = np.zeros(mask.shape, float)
    if not mask.any():
        return out
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = max(0, ys.min() - 2), ys.max() + 3, max(0, xs.min() - 2), xs.max() + 3
    m = mask[y0:y1, x0:x1]
    dt = ndimage.distance_transform_edt(m)
    th = np.zeros(m.shape)
    r = 0.5
    rmax = float(dt.max())
    while r <= rmax + 1e-9:
        core_ = dt >= r
        if not core_.any():
            break
        cover = ndimage.distance_transform_edt(~core_) <= r
        th[cover & m] = 2.0 * r
        r += step_px
    out[y0:y1, x0:x1] = th
    return out


def stroke_width_mm(frame_mask: np.ndarray, px_per_mm: float, reach_px: float = 3.0,
                    default_mm: float = 10.0, run_mm: float = STROKE_RUN_MM) -> np.ndarray:
    """Stroke width (mm) of the frame mask: local thickness (largest inscribed disk through the pixel), then
    the largest value within ``run_mm`` (a stroke counts as thin only when it STAYS thin for that long, so a
    short narrowing of an acetate rim is not a metal wire). Pixels within ``reach_px`` of the mask inherit
    the nearest frame pixel's width; others get ``default_mm``."""
    if not frame_mask.any():
        return np.full(frame_mask.shape, default_mm)
    width = local_thickness_px(frame_mask, max(1.0, 0.1 * px_per_mm)) / px_per_mm
    if run_mm > 0:
        k = 2 * int(round(run_mm * px_per_mm)) + 1
        width = np.where(frame_mask, ndimage.maximum_filter(width, size=k), 0.0)
    dist, (iy, ix) = ndimage.distance_transform_edt(~frame_mask, return_indices=True)
    return np.where(dist <= reach_px, width[iy, ix], default_mm)


def front_component(hit: np.ndarray, d: np.ndarray, thr: float = CONTINUITY_MM) -> np.ndarray:
    """Largest 4-connected component of hits whose neighbours differ by < thr in depth."""
    if not hit.any():
        return hit.copy()
    idx = -np.ones(hit.shape, np.int64)
    idx[hit] = np.arange(int(hit.sum()))
    H, W = hit.shape
    ei, ej = [], []
    for dr, dc in ((0, 1), (1, 0)):
        a = hit[:H - dr, :W - dc] & hit[dr:, dc:]
        with np.errstate(invalid="ignore"):
            a &= np.abs(d[:H - dr, :W - dc] - d[dr:, dc:]) < thr
        ei.append(idx[:H - dr, :W - dc][a])
        ej.append(idx[dr:, dc:][a])
    ei, ej = np.concatenate(ei), np.concatenate(ej)
    n = int(hit.sum())
    _, lab = connected_components(sparse.coo_matrix((np.ones(len(ei)), (ei, ej)), shape=(n, n)), directed=False)
    counts = np.bincount(lab)
    keep = np.zeros(hit.shape, bool)
    keep[hit] = lab == int(np.argmax(counts))
    return keep


def _cast(scene, O, D):
    rays = o3d.core.Tensor(np.hstack([O, D]).astype(np.float32))
    a = scene.cast_rays(rays)
    t1 = a["t_hit"].numpy().astype(np.float64)
    hit = np.isfinite(t1)
    O2 = O.copy()
    O2[hit] = O[hit] + (t1[hit] + _EPS_MM)[:, None] * D[hit]
    b = scene.cast_rays(o3d.core.Tensor(np.hstack([O2, D]).astype(np.float32)))
    t2 = b["t_hit"].numpy().astype(np.float64)
    exit_d = np.where(hit & np.isfinite(t2), t2 + _EPS_MM, np.inf)
    return np.where(hit, t1, np.inf), exit_d


def cast_param_grid(scene, base: Base, s_axis: np.ndarray, y_axis: np.ndarray, d_out: float) -> dict:
    """First hit (param depth d) and entry/exit thickness along the base-normal rays of an (s, y) grid."""
    S, Y = np.meshgrid(s_axis, y_axis)
    O = base.from_param(S.ravel(), Y.ravel(), np.full(S.size, d_out))
    D = -base.outward(S.ravel())
    t1, t2 = _cast(scene, O, D)
    hit = np.isfinite(t1)
    d = np.where(hit, d_out - t1, np.nan)
    P = np.where(hit[:, None], O + np.where(hit, t1, 0.0)[:, None] * D, np.nan)
    return {"S": S, "Y": Y, "hit": hit.reshape(S.shape), "d": d.reshape(S.shape),
            "t": np.where(np.isfinite(t2), t2, np.nan).reshape(S.shape), "P": P.reshape(S.shape + (3,))}


def _classify(P: np.ndarray, keep: np.ndarray, src: SourceView, frame: NormFrame, frame_er: np.ndarray,
              lens_er: list[np.ndarray]) -> np.ndarray:
    """int8 per grid cell: 0 other, 1 frame (eroded mask), 1 + i lens i (eroded polygon)."""
    cls = np.zeros(keep.shape, np.int8)
    if not keep.any():
        return cls
    uv = src.project(P[keep], frame)
    H, W = src.shape
    ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    inside = (uv[:, 0] >= 0) & (uv[:, 0] <= W - 1) & (uv[:, 1] >= 0) & (uv[:, 1] <= H - 1)
    c = np.zeros(len(uv), np.int8)
    c[inside & frame_er[vi, ui]] = 1
    for i, le in enumerate(lens_er, start=1):
        c[inside & le[vi, ui]] = 1 + i
    cls[keep] = c
    return cls


def fit_wrap(x, y, z) -> dict:
    """Robust z = a + b x + c y + e x^2; wrap radius R = -1/(2e) (inf when not convex)."""
    poly = Poly2D(((0, 0), (1, 0), (0, 1), (2, 0)), 0.0, float(np.median(y)), 30.0)
    c, r = robust_poly(poly, x, y, z, SOFT_L1_LOW_MM)
    e = c[3] / 30.0 ** 2
    b = c[1] / 30.0
    R = float(-1.0 / (2 * e)) if e < 0 else float("inf")
    xc = float(-b / (2 * e)) if e != 0 else 0.0
    z_apex = float(poly.eval(c, xc, poly.yc))
    return {"R_mm": R, "xc_mm": xc, "z_apex_mm": z_apex, "residual": residual_stats(r)}


def fit_lens(base: Base, s, y, d, centre: tuple[float, float], front_d_centre: float, n_min: int = MIN_LENS_HITS
             ) -> tuple[Poly2D, np.ndarray, dict]:
    """Robust quartic lens surface with the convexity clamp, or the base-curve sphere fallback."""
    sc, yc = centre
    info: dict = {"hits": int(len(d)), "centre_sy_mm": [round(sc, 3), round(yc, 3)]}
    quad_terms = ((0, 0), (1, 0), (0, 1), (2, 0), (0, 2))
    qpoly = Poly2D(quad_terms, sc, yc, 30.0)

    def fixed_curvature(ks, ky, d0_guess):
        """Quadratic with the given 3D curvatures; offset + tilt robustly refitted (or d0_guess)."""
        dss = base.d_ss_for(ks, d0_guess)
        dyy = -ky
        c = np.zeros(5)
        c[3], c[4] = 0.5 * dss * 30.0 ** 2, 0.5 * dyy * 30.0 ** 2
        if len(d) >= 50:
            A = qpoly.design(s, y)
            target = np.asarray(d, float) - A[:, 3:] @ c[3:]
            lin = Poly2D(quad_terms[:3], sc, yc, 30.0)
            cl, _ = robust_poly(lin, s, y, target, SOFT_L1_LENS_MM)
            c[:3] = cl
        else:
            c[0] = d0_guess
        return c

    if len(d) < n_min:
        d0 = float(np.median(d)) if len(d) >= 50 else front_d_centre - 1.0
        ks = 1.0 / BASE4_R_MM if base.kind == "planar" else base.curvature_s(0.0, d0)
        coef = fixed_curvature(ks, 1.0 / BASE4_R_MM, d0)
        info.update(model="base_curve_sphere", radius_s_mm=round(1 / ks, 2), radius_y_mm=BASE4_R_MM,
                    reason=f"{len(d)} hits < {n_min}", clamped=False)
        if len(d):
            info["residual_final"] = residual_stats(np.asarray(d) - qpoly.eval(coef, s, y))
        return qpoly, coef, info
    poly = Poly2D(QUARTIC_TERMS, sc, yc, 30.0)
    coef, r = robust_poly(poly, s, y, d, SOFT_L1_LENS_MM)
    info["residual_quartic"] = residual_stats(r)
    w = soft_l1_weights(r, SOFT_L1_LENS_MM)
    dss, dyy, _ = poly.second(coef, s, y)
    dmid = float(np.median(d))
    ks = float(np.sum(w * base.curvature_s(dss, d)) / np.sum(w))
    ky = float(np.sum(w * -dyy) / np.sum(w))
    info["fitted_radius_s_mm"] = round(1 / ks, 2) if ks != 0 else None
    info["fitted_radius_y_mm"] = round(1 / ky, 2) if ky != 0 else None

    def clamp(k):
        if k < -1.0 / CONCAVE_TOL_R_MM:
            return 1.0 / BASE4_R_MM, "concave->base4"
        if k > 1.0 / MIN_LENS_R_MM:
            return 1.0 / MIN_LENS_R_MM, "tight->R50"
        return k, None

    ks2, why_s = clamp(ks)
    ky2, why_y = clamp(ky)
    if why_s is None and why_y is None:
        info.update(model="quartic", radius_s_mm=info["fitted_radius_s_mm"], radius_y_mm=info["fitted_radius_y_mm"],
                    clamped=False, residual_final=info["residual_quartic"])
        return poly, coef, info
    c2 = fixed_curvature(ks2, ky2, dmid)
    info.update(model="quadratic_clamped", clamped=True, clamp={"s": why_s, "y": why_y},
                radius_s_mm=round(1 / ks2, 2) if ks2 else None, radius_y_mm=round(1 / ky2, 2) if ky2 else None,
                residual_final=residual_stats(np.asarray(d) - qpoly.eval(c2, s, y)))
    return qpoly, c2, info


# =========================================================================== stage
def run(product: str, run: str = "m1", force: bool = False, log=print) -> dict:
    sd = stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    from . import generator
    t_start = time.time()
    gen = generator.load(product, run)
    frame = gen.frame
    s1 = gen.result
    s2, a2 = stage_dir(run, product, "s2_front").load()
    src = source_view(product, run)
    from . import cameras
    ppm_src = cameras.px_per_mm_at(src.camera, frame, cameras.front_piece_centre(gen))
    flags: list[str] = []
    width_mm = float(s1["front_width_mm"])
    h = width_mm / SAMPLES_PER_WIDTH
    front_z = float(s1["front_z_mm"])
    front_depth = float(s1["front_depth_mm"])
    z_limit = front_z - front_depth - FRONT_PIECE_MARGIN_MM
    bb_lo, bb_hi = np.asarray(s1["bbox_mm"]["min"]), np.asarray(s1["bbox_mm"]["max"])
    x0, x1 = s1["front_x_range_mm"]
    box = ((min(x0, bb_lo[0]) - 40.0, max(x1, bb_hi[0]) + 40.0), (bb_lo[1] - 30.0, bb_hi[1] + 30.0),
           (z_limit - 40.0, front_z + 25.0))

    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(gen.V, np.float32)),
                        o3d.core.Tensor(np.ascontiguousarray(gen.F, np.uint32)))

    frame_mask = np.asarray(a2["frame_mask"], bool)
    polys = lens_polys(a2)
    sides = [l["side"] for l in s2["lenses"]]
    frame_er = erode_mm(frame_mask, FRAME_ERODE_MM, ppm_src)
    lens_er = [erode_mm(poly_mask([p], src.shape), LENS_ERODE_MM, ppm_src) for p in polys]

    # ---- planar pass: +Z grid
    xs = np.arange(math.floor((x0 - 8.0) / h), math.ceil((x1 + 8.0) / h) + 1) * h
    ys = (np.arange(math.ceil((bb_hi[1] + 4.0) / h), math.floor((bb_lo[1] - 4.0) / h) - 1, -1)) * h
    planar = Base("planar")
    g = cast_param_grid(scene, planar, xs, ys, front_z + 20.0)
    z1 = g["P"][..., 2]
    in_piece = g["hit"] & (np.nan_to_num(z1, nan=-1e9) >= z_limit)
    keep = front_component(in_piece, g["d"])
    cls = _classify(g["P"], keep, src, frame, frame_er, lens_er)
    wsel = cls > 0
    if wsel.sum() < 200:
        raise RuntimeError(f"{product}: only {int(wsel.sum())} front samples fall inside the S2 masks")
    wrap = fit_wrap(g["P"][wsel][:, 0], g["P"][wsel][:, 1], g["P"][wsel][:, 2])
    log(f"[s4 {product}] planar grid {g['S'].shape}, kept {int(keep.sum())}, frame {int((cls == 1).sum())}, "
        f"wrap R {wrap['R_mm']:.1f} mm")
    base = planar
    if wrap["R_mm"] < CYLINDER_MAX_R_MM:
        base = Base("cylinder", wrap["R_mm"], wrap["xc_mm"], wrap["z_apex_mm"] - wrap["R_mm"])
        Pk = g["P"][wsel]
        s_k, _, d_k = base.to_param(Pk)
        smax = min(float(np.max(np.abs(s_k))) + 12.0, 0.61 * math.pi * base.R)
        s_axis = np.arange(math.floor(-smax / h), math.ceil(smax / h) + 1) * h
        d_out = float(np.max(d_k)) + 20.0
        g = cast_param_grid(scene, base, s_axis, ys, d_out)
        z1 = g["P"][..., 2]
        in_piece = g["hit"] & (np.nan_to_num(z1, nan=-1e9) >= z_limit)
        keep = front_component(in_piece, g["d"])
        cls = _classify(g["P"], keep, src, frame, frame_er, lens_er)
        log(f"[s4 {product}] cylinder R {base.R:.1f} xc {base.xc:.2f} zc {base.zc:.2f}; grid {g['S'].shape}, "
            f"kept {int(keep.sum())}, frame {int((cls == 1).sum())}")
    S, Y, Dd = g["S"], g["Y"], g["d"]
    s_axis, y_axis = S[0], Y[:, 0]

    # ---- front surface on frame samples
    fsel = cls == 1
    if fsel.sum() < 1000:
        flags.append("front_few_frame_samples")
        fsel = cls > 0
    s_f, y_f, d_f = S[fsel], Y[fsel], Dd[fsel]
    cov = keep & (cls > 0)
    dom = (float(S[cov].min()), float(S[cov].max()), float(Y[cov].min()), float(Y[cov].max()))
    front, r_front = fit_field(s_f, y_f, d_f, SOFT_L1_LOW_MM, SOFT_L1_FRONT_MM, dom,
                               centre=(0.0, float(np.median(y_f))))
    r_low = d_f - front.low_only(s_f, y_f)

    # ---- thickness on frame samples
    t_raw = g["t"]
    tsel = (cls == 1) & np.isfinite(t_raw)             # thickness from frame samples only (plates are thin)
    if tsel.sum() < 200:
        tsel = fsel & np.isfinite(t_raw)
    t_vals = np.clip(t_raw[tsel], 0.2, 20.0)
    tfield, r_thick = fit_field(S[tsel], Y[tsel], t_vals, SOFT_L1_THICK_MM, SOFT_L1_THICK_MM, dom,
                                centre=(0.0, float(np.median(Y[tsel]))))
    d_grid = front(S, Y)
    P_grid = base.from_param(S, Y, d_grid)
    try:
        uv_grid = src.project(P_grid.reshape(-1, 3), frame)
    except ValueError:
        uv_grid = np.zeros((S.size, 2))
    stroke = stroke_width_mm(frame_mask, ppm_src)
    H, W = src.shape
    ui = np.clip(np.round(uv_grid[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv_grid[:, 1]).astype(int), 0, H - 1)
    stroke_grid = stroke[vi, ui].reshape(S.shape)
    metal = stroke_grid < METAL_STROKE_MM
    lo_t = np.where(metal, METAL_T_MM[0], ACETATE_T_MM[0])
    hi_t = np.where(metal, METAL_T_MM[1], ACETATE_T_MM[1])
    t_fit = tfield(S, Y)
    thick = np.clip(t_fit, lo_t, hi_t)
    in_frame_px = frame_mask[vi, ui].reshape(S.shape)
    clamp_share = float(np.mean((t_fit != thick)[in_frame_px])) if in_frame_px.any() else 0.0

    # ---- depth field (lens models added below)
    df = DepthField(base, front, s_axis, y_axis, thick, [], box)
    surf = df.front_surface()

    # ---- lens surfaces (a pair is fitted jointly, mirrored about the S2 symmetry axis)
    lens_models: list[LensModel] = []
    lens_info = []
    cents, samp = [], []
    for i, p in enumerate(polys, start=1):
        c_px = np.asarray(s2["lenses"][i - 1]["centroid_px"], float)
        Pc = lift_px(c_px[None], src.camera, frame, surf, mirror_width=src.mirror_width)[0]
        if not np.all(np.isfinite(Pc)):
            Pc = base.from_param(0.0, float(np.median(y_f)), float(np.median(d_f)))
            flags.append(f"lens{i}_centroid_lift_failed")
        sc_, yc_, _ = base.to_param(Pc)
        cents.append((float(sc_), float(yc_)))
        lsel = cls == 1 + i
        dl, fl = Dd[lsel], front(S[lsel], Y[lsel])
        band = (dl <= fl + LENS_BAND_FRONT_MM) & (dl >= fl - LENS_BAND_BEHIND_MM)
        samp.append({"s": S[lsel][band], "y": Y[lsel][band], "d": dl[band], "f": fl[band],
                     "in_poly": int(lsel.sum()), "in_band": int(band.sum())})
    mirror_s = None
    if s2.get("layout") == "pair" and len(polys) == 2:
        v_mid = 0.5 * (s2["lenses"][0]["centroid_px"][1] + s2["lenses"][1]["centroid_px"][1])
        Pa = lift_px(np.array([[float(s2["axis_x_px"]), v_mid]]), src.camera, frame, surf, mirror_width=src.mirror_width)[0]
        if np.all(np.isfinite(Pa)):
            mirror_s = float(base.to_param(Pa)[0])
    if mirror_s is not None:
        a_, b_ = samp
        ss = np.concatenate([a_["s"], 2 * mirror_s - b_["s"]])
        yy_ = np.concatenate([a_["y"], b_["y"]])
        dd = np.concatenate([a_["d"], b_["d"]])
        centre = (0.5 * (cents[0][0] + 2 * mirror_s - cents[1][0]), 0.5 * (cents[0][1] + cents[1][1]))
        lp, lc, joint = fit_lens(base, ss, yy_, dd, centre, float(front(*centre)))
        fits = [(lp, lc, dict(joint), None), (lp, lc, dict(joint), mirror_s)]
    else:
        fits = []
        for i, sm in enumerate(samp):
            lp, lc, info = fit_lens(base, sm["s"], sm["y"], sm["d"], cents[i], float(front(*cents[i])))
            fits.append((lp, lc, info, None))
    for i, ((lp, lc, info, ms), sm) in enumerate(zip(fits, samp), start=1):
        m = LensModel(i, sides[i - 1] if i - 1 < len(sides) else "?", lp, lc, info["model"], ms)
        info["side"] = m.side
        info["joint_symmetric_pair"] = mirror_s is not None
        info["mirror_s_mm"] = ms
        info["samples_in_polygon"] = sm["in_poly"]
        info["samples_in_depth_band"] = sm["in_band"]
        info["poly"] = lp.to_dict()
        if len(sm["d"]):
            info["residual_this_lens"] = residual_stats(sm["d"] - m(sm["s"], sm["y"]))
            info["plate_behind_front_median_mm"] = round(float(np.median(sm["f"] - sm["d"])), 3)
        if info.get("clamped"):
            flags.append(f"lens{i}_convexity_clamped")
        if info["model"] == "base_curve_sphere":
            flags.append(f"lens{i}_base_curve_fallback")
        lens_models.append(m)
        lens_info.append(info)
    df.lenses = lens_models

    # ---- regular (x, y) grid for the DESIGN arrays
    gx = np.arange(math.floor((x0 - 8.0) / h), math.ceil((x1 + 8.0) / h) + 1) * h
    gy = ys
    GX, GY = np.meshgrid(gx, gy)
    ZF = df.z(GX, GY)
    TH = np.where(np.isfinite(ZF), df.thickness_at(np.stack([GX, GY, np.nan_to_num(ZF)], -1)), np.nan)
    # valid = generator coverage: the nearest param cell was a kept front sample
    s_q, _, _ = base.to_param(np.stack([GX, GY, np.nan_to_num(ZF)], -1))
    js = np.clip(np.round((s_q - s_axis[0]) / h).astype(int), 0, len(s_axis) - 1)
    iy = np.clip(np.round((y_axis[0] - GY) / h).astype(int), 0, len(y_axis) - 1)
    valid = np.isfinite(ZF) & keep[iy, js]

    # ---- metrics
    # extrapolated share: frame-mask pixels whose lifted point has no generator sample within 1 mm
    fy, fx = np.nonzero(frame_mask[::2, ::2])
    fpx = np.stack([fx * 2.0, fy * 2.0], 1)
    Pf = lift_px(fpx, src.camera, frame, surf, mirror_width=src.mirror_width)
    okf = np.all(np.isfinite(Pf), axis=1)
    s_p, y_p, _ = base.to_param(Pf[okf])
    cov_d = ndimage.binary_dilation(keep, iterations=max(1, int(round(1.0 / h))))
    jj = np.clip(np.round((s_p - s_axis[0]) / h).astype(int), 0, len(s_axis) - 1)
    ii = np.clip(np.round((y_axis[0] - y_p) / h).astype(int), 0, len(y_axis) - 1)
    extrap = 1.0 - float(cov_d[ii, jj].mean()) if len(jj) else 1.0
    if extrap > EXTRAPOLATED_MAX:
        flags.append("depth_extrapolated_high")
    if (~okf).any():
        flags.append("frame_pixels_missed_surface")
    # lumpiness: normal deviation of the full model from its low-order part on frame samples
    sub = np.nonzero(fsel.ravel())[0][:: max(1, int(fsel.sum() // 20000))]
    Sf, Yf = S.ravel()[sub], Y.ravel()[sub]
    Pn = base.from_param(Sf, Yf, front(Sf, Yf))
    n_full = surf.normal(Pn)
    n_low = ParamSurface(base, front.low_only).normal(Pn)
    ang = np.degrees(np.arccos(np.clip(np.sum(n_full * n_low, axis=1), -1, 1)))
    res = {
        "stage": STAGE, "product": product, "run": run,
        "method": {"raycast": "orthographic +Z grid in the MODEL frame (radial toward the axis when cylinder)",
                   "why_not_camera_rays": "the field is a height field over model (x, y); a model-frame grid samples "
                   "it uniformly and camera-independently, and photo pixels are placed on it by exact ray/surface "
                   "intersection (lift_px). The camera only labels samples through the S2 masks.",
                   "samples_per_width": SAMPLES_PER_WIDTH, "grid_mm": h, "knot_mm": KNOT_MM,
                   "continuity_mm": CONTINUITY_MM, "front_fit_samples": "frame mask eroded 0.5 mm",
                   "loss": "soft-L1 IRLS", "lambda2_rel": LAMBDA2_REL, "lambda0_rel": LAMBDA0_REL},
        "source": {"view": src.view, "mirrored": src.mirrored, "shape": list(src.shape), "px_per_mm": round(ppm_src, 4)},
        "wrap": {"kind": base.kind, "fitted_radius_mm": round(wrap["R_mm"], 2) if np.isfinite(wrap["R_mm"]) else None,
                 "radius_mm": round(base.R, 3) if base.kind == "cylinder" else None,
                 "threshold_mm": CYLINDER_MAX_R_MM, "fit_residual": wrap["residual"]},
        "base": base.to_dict(),
        "front": {"low": front.low.to_dict(), "spline": front.spline.to_dict(),
                  "samples": int(fsel.sum()), "residual_spline": residual_stats(r_front),
                  "residual_low_order": residual_stats(r_low),
                  "normal_dev_from_low_order_deg": {"median": round(float(np.median(ang)), 3),
                                                    "p95": round(float(np.percentile(ang, 95)), 3)},
                  "domain_sy_mm": [round(v, 3) for v in dom]},
        "thickness": {"low": tfield.low.to_dict(), "spline": tfield.spline.to_dict(), "samples": int(tsel.sum()),
                      "residual": residual_stats(r_thick),
                      "raw_median_mm": round(float(np.median(t_vals)), 3) if len(t_vals) else None,
                      "frame_median_mm": round(float(np.median(thick[in_frame_px])), 3) if in_frame_px.any() else None,
                      "frame_p05_p95_mm": [round(float(np.percentile(thick[in_frame_px], q)), 3) for q in (5, 95)]
                      if in_frame_px.any() else None,
                      "metal_share_of_frame": round(float(metal[in_frame_px].mean()), 4) if in_frame_px.any() else None,
                      "clamped_share_of_frame": round(clamp_share, 4),
                      "clamps_mm": {"acetate": ACETATE_T_MM, "metal": METAL_T_MM, "metal_stroke_below_mm": METAL_STROKE_MM}},
        "lenses": lens_info,
        "coverage": {"kept_samples": int(keep.sum()), "frame_samples": int((cls == 1).sum()),
                     "lens_samples": [int((cls == 1 + i).sum()) for i in range(1, len(polys) + 1)],
                     "extrapolated_share_of_frame": round(extrap, 4),
                     "frame_pixels_missed": int((~okf).sum())},
        "box_mm": [list(map(float, b)) for b in box],
        "grid": {"x0": float(gx[0]), "y0": float(gy[0]), "h": h, "nx": len(gx), "ny": len(gy)},
        "param_grid": {"s0": float(s_axis[0]), "y0": float(y_axis[0]), "h": h, "ns": len(s_axis), "ny": len(y_axis)},
        "front_piece": {"z_front_mm": front_z, "depth_mm": front_depth, "z_limit_mm": z_limit},
        "flags": sorted(set(flags)),
    }
    arrays = {"grid_x": gx, "grid_y": gy, "z_front": ZF.astype(np.float32), "thickness": TH.astype(np.float32),
              "valid": valid, "param_s": s_axis, "param_y": y_axis, "param_d_raw": Dd.astype(np.float32),
              "param_t_raw": np.nan_to_num(t_raw, nan=-1).astype(np.float32), "param_keep": keep,
              "param_class": cls, "param_thickness": thick.astype(np.float32),
              "front_low_coef": front.low_coef, "front_spline_coef": front.spline_coef,
              "thick_low_coef": tfield.low_coef, "thick_spline_coef": tfield.spline_coef}
    for m in lens_models:
        arrays[f"lens{m.index}_surface"] = np.asarray(m.coef, float)
    res["seconds"] = round(time.time() - t_start, 1)
    sd.save(res, arrays)
    try:
        make_sheet(product, run, df, res, arrays, src, frame, polys, frame_mask)
    except Exception as e:  # the sheet must never hide a stage result
        res["flags"] = sorted(set(res["flags"] + ["sheet_failed"]))
        res["sheet_error"] = repr(e)
        sd.save(res)
    log(f"[s4 {product}] done in {res['seconds']} s: wrap {base.kind}, front resid {res['front']['residual_spline']}, "
        f"flags {res['flags']}")
    return res


def load_depth(product: str, run: str = "m1") -> DepthField:
    sd = stage_dir(run, product, STAGE)
    if not sd.done():
        raise FileNotFoundError(f"S4 artifacts missing for {product}/{run}: run bsa.depth first")
    res, a = sd.load()
    base = Base.from_dict(res["base"])
    front = SmoothField(Poly2D.from_dict(res["front"]["low"]), a["front_low_coef"],
                        BSpline2D.from_dict(res["front"]["spline"]), a["front_spline_coef"])
    lenses = []
    for i, info in enumerate(res["lenses"], start=1):
        lenses.append(LensModel(i, info.get("side", "?"), Poly2D.from_dict(info["poly"]), a[f"lens{i}_surface"],
                                info["model"], info.get("mirror_s_mm")))
    box = tuple(tuple(b) for b in res["box_mm"])
    return DepthField(base, front, a["param_s"], a["param_y"], a["param_thickness"], lenses, box, res)


# =========================================================================== sheet
def make_sheet(product, run, df: DepthField, res, arrays, src: SourceView, frame, polys, frame_mask) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    S_ax, Y_ax = arrays["param_s"], arrays["param_y"]
    ext = [S_ax[0], S_ax[-1], Y_ax[-1], Y_ax[0]]
    keep, cls = arrays["param_keep"], arrays["param_class"]
    Dd = arrays["param_d_raw"].astype(float)
    S, Y = np.meshgrid(S_ax, Y_ax)
    model = df.front(S, Y)
    fig, axs = plt.subplots(3, 2, figsize=(16, 13))
    ax = axs[0, 0]
    photo = core.load_photo(core.PRODUCTS[product], src.view)
    if src.mirrored:
        photo = photo[:, ::-1]
    ax.imshow(photo)
    Pk = df.base.from_param(S[keep], Y[keep], Dd[keep])
    uv = src.project(Pk, frame)
    c = cls[keep]
    step = max(1, len(uv) // 40000)
    colours = np.array([[0.6, 0.6, 0.6], [0.1, 0.8, 0.1], [0.1, 0.4, 1.0], [1.0, 0.3, 0.1]])
    ax.scatter(uv[::step, 0], uv[::step, 1], s=0.3, c=colours[np.clip(c[::step], 0, 3)], alpha=0.5)
    for p in polys:
        q = np.vstack([p, p[:1]])
        ax.plot(q[:, 0], q[:, 1], "m-", lw=0.8)
    ys_, xs_ = np.nonzero(frame_mask)
    ax.set_xlim(xs_.min() - 40, xs_.max() + 40)
    ax.set_ylim(ys_.max() + 40, ys_.min() - 40)
    ax.set_title(f"{product}: generator front samples on the {src.view}{' (mirrored)' if src.mirrored else ''} photo "
                 "(green frame, blue/red lens, grey other)")
    ax = axs[0, 1]
    im = ax.imshow(np.where(keep, Dd, np.nan), extent=ext, cmap="turbo", aspect="equal")
    ax.set_title(f"raw first-hit d (kept component), {res['wrap']['kind']}"
                 + (f" R={res['wrap']['radius_mm']:.1f} mm" if res['wrap']['radius_mm'] else ""))
    fig.colorbar(im, ax=ax, fraction=0.03)
    ax = axs[1, 0]
    im = ax.imshow(model - df.front.low_only(S, Y), extent=ext, cmap="coolwarm", vmin=-3, vmax=3, aspect="equal")
    ax.contour(S, Y, np.where(keep & (cls > 0), 1.0, 0.0), levels=[0.5], colors="k", linewidths=0.5)
    ax.set_title("fitted front minus its low-order part (mm); black = sample coverage")
    fig.colorbar(im, ax=ax, fraction=0.03)
    ax = axs[1, 1]
    r = np.where(cls == 1, Dd - model, np.nan)
    im = ax.imshow(r, extent=ext, cmap="coolwarm", vmin=-1, vmax=1, aspect="equal")
    fr = res["front"]["residual_spline"]
    ax.set_title(f"front residual on frame samples (mm): median {fr['median_mm']} p95 {fr['p95_mm']}")
    fig.colorbar(im, ax=ax, fraction=0.03)
    ax = axs[2, 0]
    th = np.where(ndimage.binary_dilation(keep, iterations=8), arrays["param_thickness"], np.nan)
    im = ax.imshow(th, extent=ext, cmap="viridis", aspect="equal")
    ax.set_title(f"clamped thickness (mm), frame median {res['thickness']['frame_median_mm']}")
    fig.colorbar(im, ax=ax, fraction=0.03)
    ax = axs[2, 1]
    lr = np.full(S.shape, np.nan)
    for m in df.lenses:
        sel = cls == 1 + m.index
        lr[sel] = Dd[sel] - m(S[sel], Y[sel])
    im = ax.imshow(lr, extent=ext, cmap="coolwarm", vmin=-0.5, vmax=0.5, aspect="equal")
    txt = "; ".join(f"L{m.index}{m.side} {m.kind} R_s {li.get('radius_s_mm')} R_y {li.get('radius_y_mm')} "
                    f"p95 {li.get('residual_this_lens', {}).get('p95_mm')}" for m, li in zip(df.lenses, res["lenses"]))
    ax.set_title("lens plate residual (mm)\n" + txt, fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.03)
    fig.suptitle(f"S4 depth {product}: flags {res['flags']}  extrapolated {res['coverage']['extrapolated_share_of_frame']}")
    fig.tight_layout()
    path = stage_dir(run, product, STAGE).root / "sheet.png"
    fig.savefig(path, dpi=70)
    plt.close(fig)
    return str(path)


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="S4 depth: generator front surface, thickness, lens surfaces")
    ap.add_argument("--product", choices=sorted(core.PRODUCTS))
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    for p in ([a.product] if a.product else list(core.PRODUCTS)):
        run(p, a.run, a.force)


if __name__ == "__main__":
    main()
