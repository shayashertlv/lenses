"""S3 ``s3_cameras``: one frozen camera per photo, fitted to the S1 decimated generator on the S0 matte.

Every camera is a ``reconstruction.camera.Camera`` in the product's NormFrame that maps MODEL
millimetres to NATIVE photo pixels (``core.project_mm``). The canonical generator is the only shape:
the same metric object is fitted in all five photos, so every camera carries the same metric scale.

Sign conventions (checked on the renders: ``yaw_sign_test`` in result.json and the textured row of
sheet.png, where the temple lettering reads the right way round in both photo and render):

- yaw 0 looks from +Z (the front photo): +X image-right, +Y up.
- yaw +90 looks from +X, the glasses' own LEFT side (the viewer's right in the front photo); +Z, the
  lens plate, is at image-LEFT. ``left.jpg`` shows the plate at image-left on all five products, so
  ``left`` = yaw +90 and ``right`` = yaw -90. The file names agree with the geometry: the camera of
  ``left.jpg`` sees the wearer's left temple.
- yaw 180 looks from -Z (the back photo). Positive pitch = the camera is above the glasses looking down.
- Orthographic silhouettes of a mirror-symmetric object satisfy S(yaw, pitch) = S(180 - yaw, -pitch).
  For a side view (yaw 90) that makes the pitch SIGN a near-tie that only perspective and asymmetry
  break (which temple is nearer). Both signs are always refined; the margin is reported (``pitch_twin``).

Method (DESIGN.md S3):
- Seeds per view: yaw prior (front 0, back 180, left +90, right -90; +/-4, 8, 12 deg around it; angled
  +/-20..65 in 7.5 deg steps, both signs) x pitch {-20, -10, 0, 10, 20, 40}, each scale/centre-aligned
  on the matte bounding box and scored at ~256 px glasses width. The best pitch-distinct starts (per yaw
  sign for 'angled') are refined there; then CROSS-SEEDING: every view restarts from the other views'
  winners (rotated by the view priors, mirrored by the symmetry, and pitch-twinned). The best coarse
  solutions (plus the side views' pitch twin) are refined at ~512 px, the winner again at ~1024 px.
- Refinement = bounded Powell with shrinking bounds on (1 - soft IoU) + 4 x symmetric boundary
  distance / glasses width, each stage followed by a contour-ICP least-squares step that is kept only
  when it lowers that same loss. The parameters are decoupled: the optimizer moves the image position
  and magnification of a PIVOT (front-piece centre; frame centre for the side views), so rotation,
  perspective and zoom no longer drag the silhouette sideways.
- Lenses in side views: S0 has no lens proposal for left/right, so a clear lens (or one seen edge-on)
  is absent from the matte. The generator's lens faces are labelled through the first front camera and
  the S2 outlines; where a side matte covers < 60 % of the rendered lens, lens hits are OPTIONAL (they
  may explain foreground, are not penalised over background).
- The angled view is fitted like the others but only as a TARGET: it never seeds, scales or chooses
  another view's camera.

Diagnostics (not used to choose anything): the opposite-yaw-sign fit for left/right (the yaw sign
margin), the pitch-twin margin, the angled yaw profile (+/-8 deg), a front-piece-only scale refit
(temple pixels masked: rendered temples and the photo pixels nearer to them than to the rendered front
piece), extent ratios, outer-contour distances, and the front camera's px/mm against S2's provisional
width scale.
"""
from __future__ import annotations

import argparse
import json
import math
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage, optimize

from reconstruction.camera import Camera, project

from . import generator, raster
from .core import (FIT_VIEWS, HELD_OUT_VIEWS, PRODUCTS, VIEWS, NormFrame, camera_from_dict, load_photo,
                   px_per_mm, run_dir, stage_dir)

STAGE = "s3_cameras"

# ----------------------------------------------------------------------------- policy
PRIOR_YAW = {"front": 0.0, "back": 180.0, "left": 90.0, "right": -90.0}
ANGLED_YAWS = (20.0, 27.5, 35.0, 42.5, 50.0, 57.5, 65.0)
PITCHES = (-20.0, -10.0, 0.0, 10.0, 20.0, 40.0)    # the plan's {-20, 0, 20, 40} plus +/-10: the basins are narrow
PRIOR_YAW_OFFSETS = (-12.0, -8.0, -4.0, 0.0, 4.0, 8.0, 12.0)   # front/back/left/right seeds around the prior
SEED_PERSPECTIVE = 0.1
YAW_HALF_WINDOW = 35.0            # front/back/left/right: prior +/- 35 deg
ANGLED_WINDOW = (8.0, 80.0)       # |yaw| of the angled view, per sign
PITCH_BOUNDS = (-40.0, 60.0)
ROLL_BOUNDS = (-25.0, 25.0)
PERSPECTIVE_BOUNDS = (0.0, 0.6)   # normalized units: 1 / camera distance in NormFrame extents
COARSE_W, FIT_W, FINAL_W = 256.0, 512.0, 1024.0   # target glasses width (grid px) per level
ROI_MARGIN = 0.3                  # ROI = matte bbox + 30 % of its width on every side (coarse, fit)
FINAL_ROI_MARGIN = 0.1            # final level: matte bbox U fit-level render bbox + 10 %
OUTSIDE_PENALTY = 0.5             # loss per unit share of the projected hull bbox that leaves the ROI
BOUNDARY_WEIGHT = 4.0
BOUNDARY_TRUNC = 0.1              # boundary distances truncated at 10 % of the glasses width
N_COARSE_POWELL = 3               # prior starts refined per view (per yaw sign for 'angled') at the coarse level
N_CROSS_POWELL = 1                # cross seeds refined per view
N_FIT_POWELL = 2                  # coarse winners refined at the fit level ...
FIT_SECOND_WITHIN = 1.25          # ... the second only when its coarse loss is within 25 % of the best
FRONT_IOU_MIN, VIEW_IOU_MIN = 0.93, 0.85
LOW_WEIGHT = 0.25
PITCH_TWIN_TIE = 0.005            # loss margin under which the pitch sign counts as a tie
PITCH_TWIN_MIN_DEG = 3.0          # below this |pitch| the twin is the same camera: no test
YAW_PROFILE_DEG = 8.0             # angled: loss increase with yaw forced to best +/- 8 deg (others refit)
YAW_SIGN_MIN_MARGIN = 0.02
FP_SCALE_TOL = 0.03               # front-piece-only scale vs full-silhouette scale
MAXFEV = {"coarse": 160, "fit": 220, "final": 140, "twin": 120, "fp": 100, "profile": 100}
LENS_ERODE_MM = -0.5              # S2 lens outline erosion (negative = dilation) before labelling lens faces;
                                  # rimless/tucked lens faces reach the outline, and the depth band keeps rims out
LENS_VISIBLE_MIN = 0.6            # side views: lens hits become optional when the matte covers < 60 % of them
LENS_DEPTH_BAND_MM = 5.0          # lens faces lie within 5 mm behind the front camera's first hit
ICP_ITERS = 6                     # contour-ICP re-render/re-match rounds after each Powell stage
ICP_NFEV = 25                     # least-squares evaluations per round (projection only, no ray casting)
ICP_TRUNC = 0.04                  # contour pairs farther apart than 4 % of the width are ignored
_BAD = 3.0


# ----------------------------------------------------------------------------- matte levels
class Level:
    """The S0 matte on a strided grid: soft coverage target, its boundary and distance field."""

    def __init__(self, fg: np.ndarray, target_width: float, name: str = "", margin: float = ROI_MARGIN,
                 include_xyxy: tuple[float, float, float, float] | None = None):
        ys, xs = np.nonzero(fg)
        if len(xs) == 0:
            raise ValueError("empty matte")
        self.name = name
        self.shape = fg.shape
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        self.bbox = (x0, y0, x1, y1)
        self.width_native = float(x1 - x0)
        self.stride = max(1, int(round(self.width_native / target_width)))
        m = int(math.ceil(margin * self.width_native))
        H, W = fg.shape
        if include_xyxy is not None:     # e.g. the projected hull bbox of an already close camera
            x0, y0 = min(x0, int(math.floor(include_xyxy[0]))), min(y0, int(math.floor(include_xyxy[1])))
            x1, y1 = max(x1, int(math.ceil(include_xyxy[2])) + 1), max(y1, int(math.ceil(include_xyxy[3])) + 1)
        self.roi = (max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m))
        self.ref = raster.downsample_mask(fg, self.stride, self.roi).astype(np.float64)
        self.ref_sum = float(self.ref.sum())
        refb = self.ref >= 0.5
        self.edge = _edge(refb)
        self.dist = _dist_to(self.edge)
        self.width = self.width_native / self.stride
        self.trunc = BOUNDARY_TRUNC * self.width
        self.ignore_faces: np.ndarray | None = None    # bool per decimated face: its first hits are optional

    def ignored(self, r: dict) -> np.ndarray | None:
        """Optional grid pixels: first hit on an ``ignore_faces`` face (dilated 1 px), else None."""
        if self.ignore_faces is None:
            return None
        fid = r["face_id"]
        hit = fid >= 0
        W = np.zeros(hit.shape, bool)
        W[hit] = self.ignore_faces[fid[hit]]
        if not W.any():
            return None
        return cv2.dilate(W.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)

    def render(self, scene: raster.RasterScene, cam: Camera) -> dict | None:
        try:
            return scene.render(cam, self.shape, self.stride, self.roi)
        except ValueError:          # near plane crosses the geometry
            return None

    def outside_share(self, scene: raster.RasterScene, cam: Camera) -> float:
        """Share of the projected hull bbox area outside the ROI (render there is invisible to the loss)."""
        pts = raster._project_norm(scene.hull, cam)
        if pts is None:
            return 1.0
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        area = max((hi[0] - lo[0]) * (hi[1] - lo[1]), 1e-9)
        x0, y0, x1, y1 = self.roi
        ix = max(0.0, min(hi[0], x1 - 0.5) - max(lo[0], x0 - 0.5))
        iy = max(0.0, min(hi[1], y1 - 0.5) - max(lo[1], y0 - 0.5))
        return float(max(0.0, 1.0 - ix * iy / area))

    def score(self, mask: np.ndarray, optional: np.ndarray | None = None) -> tuple[float, float, float]:
        """(loss, soft IoU, boundary distance / width) of a rendered grid mask.

        ``optional`` render pixels (lens hits in a view whose matte has no lens proposal) may explain photo
        foreground but are not penalised over background: they take the photo's own coverage. They never
        remove photo pixels from the score, so hiding unexplained photo pixels behind a lens gains nothing.
        """
        ref = self.ref
        if optional is not None:
            hard = mask & ~optional
            inter = float(ref[mask].sum())
            union = float(hard.sum()) + float(ref[~hard].sum())
            eff = hard | (mask & optional & (ref >= 0.5))
        else:
            inter = float(ref[mask].sum())
            union = float(mask.sum()) + self.ref_sum - inter
            eff = mask
        if not mask.any() or union <= 0:
            return _BAD, 0.0, 1.0
        iou = inter / union
        er = _edge(eff)
        d1 = float(np.minimum(self.dist[er], self.trunc).mean()) if er.any() else self.trunc
        d2 = float(np.minimum(_dist_to(er)[self.edge], self.trunc).mean()) if self.edge.any() else self.trunc
        bd = 0.5 * (d1 + d2) / self.width
        return (1.0 - iou) + BOUNDARY_WEIGHT * bd, iou, bd

    def effective(self, mask: np.ndarray, optional: np.ndarray | None) -> np.ndarray:
        return mask if optional is None else (mask & ~optional) | (mask & optional & (self.ref >= 0.5))

    def loss(self, scene: raster.RasterScene, cam: Camera) -> float:
        r = self.render(scene, cam)
        if r is None:
            return _BAD
        return self.score(r["mask"], self.ignored(r))[0] + OUTSIDE_PENALTY * self.outside_share(scene, cam)


def _edge(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    return mask & (cv2.erode(m, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0) == 0)


def _dist_to(target: np.ndarray) -> np.ndarray:
    """Euclidean distance (grid px) to the nearest True pixel of ``target`` (large if none)."""
    if not target.any():
        return np.full(target.shape, 1e6, np.float32)
    return cv2.distanceTransform((~target).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)


# ----------------------------------------------------------------------------- parametrisation
def _pivot_terms(cam: Camera, pivot: np.ndarray | None) -> tuple[np.ndarray, float]:
    """(image offset of the pivot at unit scale and zero centre, perspective depth factor at the pivot)."""
    if pivot is None:
        return np.zeros(2), 1.0
    P = np.asarray(pivot, float).reshape(1, 3)
    unit = Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, 1.0, 0.0, 0.0)
    q = raster._project_norm(P, unit)
    depth = 1.0 - cam.perspective * float(P[0] @ raster.camera_basis(unit)[2])
    if q is None or depth <= 1e-6:
        return np.zeros(2), 1.0
    return q[0], depth


def pack(cam: Camera, unit_px: float, pivot: np.ndarray | None = None) -> np.ndarray:
    """Optimizer vector: yaw, pitch, roll (deg), perspective x10, 100 log(magnification AT THE PIVOT)
    (1 = 1 %), and the image position of the PIVOT (a normalized model point) in % of the matte width.

    Rotating, zooming or changing perspective then keeps the pivot's image position and magnification
    still, which decouples the parameters (a degree of yaw otherwise moves the front piece ~3 px and a
    perspective change rescales it); Powell's coordinate line searches need that on thin silhouettes.
    """
    q, depth = _pivot_terms(cam, pivot)
    return np.array([cam.yaw, cam.pitch, cam.roll, 10.0 * cam.perspective, 100.0 * math.log(cam.scale / depth),
                     100.0 * (cam.center_x + cam.scale * q[0]) / unit_px,
                     100.0 * (cam.center_y + cam.scale * q[1]) / unit_px])


def unpack(x: np.ndarray, unit_px: float, pivot: np.ndarray | None = None) -> Camera:
    ang = Camera(float(x[0]), float(x[1]), float(x[2]), float(x[3]) / 10.0, 1.0, 0.0, 0.0)
    q, depth = _pivot_terms(ang, pivot)
    s = float(math.exp(x[4] / 100.0)) * depth
    return Camera(ang.yaw, ang.pitch, ang.roll, ang.perspective, s,
                  float(x[5] * unit_px / 100.0 - s * q[0]), float(x[6] * unit_px / 100.0 - s * q[1]))


def align(scene: raster.RasterScene, lv: Level, yaw: float, pitch: float, roll: float,
          perspective: float = SEED_PERSPECTIVE) -> Camera:
    """Camera with these angles whose projected hull bbox matches the matte bbox width and centre."""
    pts = project(scene.hull, Camera(yaw, pitch, roll, perspective, 1.0, 0.0, 0.0))
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    x0, y0, x1, y1 = lv.bbox
    scale = (x1 - x0) / max(hi[0] - lo[0], 1e-9)
    c = np.array([(x0 + x1 - 1) / 2.0, (y0 + y1 - 1) / 2.0]) - (lo + hi) / 2.0 * scale
    return Camera(float(yaw), float(pitch), float(roll), float(perspective), float(scale), float(c[0]), float(c[1]))


def yaw_window(view: str, yaw: float) -> tuple[float, float]:
    if view == "angled":
        return ANGLED_WINDOW if yaw >= 0 else (-ANGLED_WINDOW[1], -ANGLED_WINDOW[0])
    y0 = PRIOR_YAW[view]
    return (y0 - YAW_HALF_WINDOW, y0 + YAW_HALF_WINDOW)


def _bounds(center: Camera, unit_px: float, yaw_win: tuple[float, float], *, local: dict | None = None,
            pivot: np.ndarray | None = None):
    x = pack(center, unit_px, pivot)
    if local is None:       # global windows (coarse / fit levels)
        return [yaw_win, PITCH_BOUNDS, ROLL_BOUNDS, (10 * PERSPECTIVE_BOUNDS[0], 10 * PERSPECTIVE_BOUNDS[1]),
                (x[4] - 35.0, x[4] + 30.0), (x[5] - 15.0, x[5] + 15.0), (x[6] - 15.0, x[6] + 15.0)]
    d = local
    return [(max(yaw_win[0], x[0] - d["yaw"]), min(yaw_win[1], x[0] + d["yaw"])),
            (max(PITCH_BOUNDS[0], x[1] - d["pitch"]), min(PITCH_BOUNDS[1], x[1] + d["pitch"])),
            (max(ROLL_BOUNDS[0], x[2] - d["roll"]), min(ROLL_BOUNDS[1], x[2] + d["roll"])),
            (max(10 * PERSPECTIVE_BOUNDS[0], x[3] - d.get("persp", 10.0)),
             min(10 * PERSPECTIVE_BOUNDS[1], x[3] + d.get("persp", 10.0))),
            (x[4] - d["scale"], x[4] + d["scale"]), (x[5] - d["shift"], x[5] + d["shift"]),
            (x[6] - d["shift"], x[6] + d["shift"])]


def _clip_into(x: np.ndarray, bounds) -> np.ndarray:
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    return np.clip(x, lo, hi)


def powell(scene: raster.RasterScene, lv: Level, cam: Camera, bounds, maxfev: int, unit_px: float,
           xtol: float = 0.05, pivot: np.ndarray | None = None) -> tuple[Camera, float, int]:
    """Bounded Powell on the level loss; returns the best camera SEEN (never a worse line-search end)."""
    best = [lv.loss(scene, cam), cam]
    n = [1]

    def f(x):
        c = unpack(x, unit_px, pivot)
        v = lv.loss(scene, c)
        n[0] += 1
        if v < best[0]:
            best[0], best[1] = v, c
        return v

    x0 = _clip_into(pack(cam, unit_px, pivot), bounds)
    optimize.minimize(f, x0, method="Powell", bounds=bounds,
                      options={"maxfev": maxfev, "xtol": xtol, "ftol": 1e-4})
    return best[1], float(best[0]), n[0]


# ----------------------------------------------------------------------------- contour ICP
def contour_icp(scene: raster.RasterScene, lv: Level, cam: Camera, unit_px: float, pivot: np.ndarray | None,
                yaw_win: tuple[float, float], iters: int = None) -> Camera | None:
    """Silhouette-contour ICP: match rendered contour samples (with their 3D ray hits) to the nearest photo
    contour pixel and vice versa, then a robust least-squares step on the camera; repeat.

    Gradient information lets it walk the narrow coupled valleys (yaw/pitch/perspective/scale) that
    coordinate-wise Powell line searches stall in on thin silhouettes. Returns None if it cannot run.
    """
    iters = ICP_ITERS if iters is None else iters
    us, vs = raster.pixel_grid(lv.shape, lv.stride, lv.roi)
    if not lv.edge.any():
        return None
    pi, pj = np.nonzero(lv.edge)
    photo_uv = np.column_stack([us[pj], vs[pi]])
    _, pidx = ndimage.distance_transform_edt(~lv.edge, return_indices=True)
    trunc = max(ICP_TRUNC * lv.width * lv.stride, 3.0 * lv.stride)      # native px
    x = pack(cam, unit_px, pivot)
    lo = np.array([yaw_win[0], PITCH_BOUNDS[0], ROLL_BOUNDS[0], 10 * PERSPECTIVE_BOUNDS[0], -np.inf, -np.inf, -np.inf])
    hi = np.array([yaw_win[1], PITCH_BOUNDS[1], ROLL_BOUNDS[1], 10 * PERSPECTIVE_BOUNDS[1], np.inf, np.inf, np.inf])
    x = np.clip(x, lo + 1e-9, hi - 1e-9)
    for _ in range(iters):
        c = unpack(x, unit_px, pivot)
        try:
            r = scene.render(c, lv.shape, lv.stride, lv.roi, want_points=True)
        except ValueError:
            return None
        e = _edge(lv.effective(r["mask"], lv.ignored(r)))
        if e.sum() < 8:
            return None
        ri, rj = np.nonzero(e)
        X1 = r["points"][ri, rj]
        t1 = np.column_stack([us[pidx[1][ri, rj]], vs[pidx[0][ri, rj]]])
        _, ridx = ndimage.distance_transform_edt(~e, return_indices=True)
        qi, qj = ridx[0][pi, pj], ridx[1][pi, pj]
        X2 = r["points"][qi, qj]
        X = scene.frame.to_norm(np.concatenate([X1, X2]))
        T = np.concatenate([t1, photo_uv])
        ok = np.isfinite(X).all(axis=1)
        cur = np.concatenate([np.column_stack([us[rj], vs[ri]]), np.column_stack([us[qj], vs[qi]])])
        ok &= np.hypot(*(cur - T).T) <= trunc          # drop gross mismatches (missing / extra parts)
        if ok.sum() < 16:
            return None
        X, T = X[ok], T[ok]
        n1 = int(ok[:len(X1)].sum())
        w = np.ones(len(X))
        w[:n1] = 1.0 / max(n1, 1)
        w[n1:] = 1.0 / max(len(X) - n1, 1)
        w = np.sqrt(w * len(X) / 2.0)[:, None]

        def resid(z):
            uv = raster._project_norm(X, unpack(z, unit_px, pivot))
            if uv is None:
                return np.full(2 * len(X), 1e3)
            return ((uv - T) * w).ravel()

        sol = optimize.least_squares(resid, x, bounds=(lo, hi), loss="soft_l1", f_scale=1.5 * lv.stride,
                                     max_nfev=ICP_NFEV, x_scale="jac")
        step = sol.x - x
        x = sol.x
        if np.all(np.abs(step) < 1e-3):
            break
    return unpack(x, unit_px, pivot)


# ----------------------------------------------------------------------------- per-view search
COARSE_LOCAL = {"yaw": 15.0, "pitch": 15.0, "roll": 10.0, "scale": 15.0, "shift": 8.0}
STAGES_FIT = ({"yaw": 12.0, "pitch": 12.0, "roll": 8.0, "scale": 10.0, "shift": 5.0},
              {"yaw": 3.0, "pitch": 3.0, "roll": 2.0, "persp": 0.5, "scale": 2.5, "shift": 1.2})
STAGES_FINAL = ({"yaw": 1.5, "pitch": 1.5, "roll": 1.0, "persp": 0.3, "scale": 1.0, "shift": 0.5},)


class ViewFit:
    """Multi-start camera search for one photo. ``pivot`` (normalized model point) is held still by the
    rotation/zoom parameters: the front-piece centre for front/back/angled, the frame centre for sides."""

    def __init__(self, view: str, fg: np.ndarray, scene: raster.RasterScene, pivot: np.ndarray | None = None):
        self.view = view
        self.fg = fg
        self.scene = scene
        self.pivot = None if pivot is None else np.asarray(pivot, float)
        self.levels = {"coarse": Level(fg, COARSE_W, "coarse"), "fit": Level(fg, FIT_W, "fit")}
        self.unit = self.levels["coarse"].width_native
        self.starts: list[dict] = []
        self.coarse: list[tuple[float, Camera, str]] = []     # (loss, camera, origin) after coarse Powell
        self.evals = 0
        self.icp_accepted = 0
        self.ignore_faces: np.ndarray | None = None

    def set_ignore(self, faces: np.ndarray | None) -> None:
        self.ignore_faces = faces
        for lv in self.levels.values():
            lv.ignore_faces = faces

    def powell(self, lv: Level, cam: Camera, maxfev: int, *, local: dict | None = None,
               yaw_win: tuple[float, float] | None = None, xtol: float = 0.05) -> tuple[Camera, float]:
        win = yaw_win if yaw_win is not None else yaw_window(self.view, cam.yaw)
        b = _bounds(cam, self.unit, win, local=local, pivot=self.pivot)
        c2, l2, n = powell(self.scene, lv, cam, b, maxfev, self.unit, xtol=xtol, pivot=self.pivot)
        self.evals += n
        return c2, l2

    def staged(self, lv: Level, cam: Camera, stages, maxfev: int) -> tuple[Camera, float]:
        """Successive Powell runs with shrinking bounds (fresh directions each time), each followed by a
        contour-ICP step that is kept only when it lowers the same level loss."""
        loss = None
        for st in stages:
            cam, loss = self.powell(lv, cam, maxfev, local=st, xtol=0.05 if lv.name == "coarse" else 0.02)
            c2 = contour_icp(self.scene, lv, cam, self.unit, self.pivot, yaw_window(self.view, cam.yaw))
            self.evals += ICP_ITERS
            if c2 is not None:
                l2 = lv.loss(self.scene, c2)
                self.evals += 1
                if l2 < loss:
                    cam, loss = c2, l2
                    self.icp_accepted += 1
        return cam, loss

    def _seed(self, yaw: float, pitch: float, roll: float, origin: str) -> tuple[float, Camera]:
        lv = self.levels["coarse"]
        cam = align(self.scene, lv, yaw, pitch, roll)
        loss = lv.loss(self.scene, cam)
        self.evals += 1
        return loss, cam

    def run_starts(self, seeds: list[tuple[float, float, float, str]], n_refine: int) -> None:
        """Score seeds at the coarse level, Powell-refine the best ``n_refine`` distinct ones
        (per yaw sign for the angled view, so both handedness hypotheses are always refined)."""
        scored = []
        for yaw, pitch, roll, origin in seeds:
            loss, cam = self._seed(yaw, pitch, roll, origin)
            scored.append((loss, cam, origin))
        order = sorted(range(len(scored)), key=lambda i: (scored[i][0], i))
        picked, keys = [], set()
        groups = (True, False) if self.view == "angled" else (None,)
        for g in groups:
            n = 0
            for i in order:
                loss, cam, origin = scored[i]
                if g is not None and (cam.yaw >= 0) != g:
                    continue
                # distinct pitches first (the side-view pitch twin, camera height); yaw too for 'angled'
                key = ((round(cam.yaw / 5.0),) if self.view == "angled" else ()) +                       (round(cam.pitch / 10.0), round(cam.roll / 5.0))
                if key in keys:
                    continue
                keys.add(key)
                picked.append(i)
                n += 1
                if n >= n_refine:
                    break
        for i, (loss, cam, origin) in enumerate(scored):
            rec = {"origin": origin, "seed": [round(cam.yaw, 2), round(cam.pitch, 2), round(cam.roll, 2)],
                   "seed_loss": round(loss, 5)}
            if i in picked:
                c2, l2 = self.staged(self.levels["coarse"], cam, (COARSE_LOCAL,), MAXFEV["coarse"])
                self.coarse.append((l2, c2, origin))
                rec["refined_loss"] = round(l2, 5)
                rec["refined"] = [round(c2.yaw, 2), round(c2.pitch, 2), round(c2.roll, 2), round(c2.perspective, 3)]
            self.starts.append(rec)

    def best_coarse(self) -> tuple[float, Camera, str]:
        return min(self.coarse, key=lambda t: (t[0], t[2]))

    def refine(self) -> dict:
        """Fit level from the best distinct coarse winners, then the final level from the best."""
        order = sorted(self.coarse, key=lambda t: (t[0], t[2]))
        cands, keys = [], set()
        for loss, cam, origin in order:
            key = (cam.yaw >= 0, round(cam.pitch / 15.0))
            if key in keys or loss > FIT_SECOND_WITHIN * order[0][0]:
                continue
            keys.add(key)
            cands.append((loss, cam, origin))
            if len(cands) >= N_FIT_POWELL:
                break
        if self.view in ("left", "right") and abs(cands[0][1].pitch) >= PITCH_TWIN_MIN_DEG:
            # the orthographic silhouette twin (yaw, -pitch) is always carried to the fit level
            c0 = cands[0][1]
            twin = align(self.scene, self.levels["coarse"], c0.yaw, -c0.pitch, c0.roll, c0.perspective)
            cands.append((self.levels["coarse"].loss(self.scene, twin), twin, cands[0][2] + "+pitch_twin"))
        lv = self.levels["fit"]
        fits = []
        for loss, cam, origin in cands:
            c2, l2 = self.staged(lv, cam, STAGES_FIT, MAXFEV["fit"])
            fits.append((l2, c2, origin))
        fit_loss, fit_cam, origin = min(fits, key=lambda t: (t[0], t[2]))
        pts = project(self.scene.hull, fit_cam)
        lvF = Level(self.fg, FINAL_W, "final", margin=FINAL_ROI_MARGIN,
                    include_xyxy=(*pts.min(axis=0), *pts.max(axis=0)))
        lvF.ignore_faces = self.ignore_faces
        self.levels["final"] = lvF
        if lvF.stride == lv.stride:
            final_cam, final_loss = fit_cam, lvF.loss(self.scene, fit_cam)
        else:
            final_cam, final_loss = self.staged(lvF, fit_cam, STAGES_FINAL, MAXFEV["final"])
        return {"camera": final_cam, "loss_final_level": final_loss, "loss_fit_level": fit_loss,
                "origin": origin, "fit_candidates": [{"origin": o, "loss": round(l, 5),
                                                      "camera": [round(c.yaw, 2), round(c.pitch, 2), round(c.roll, 2)]}
                                                     for l, c, o in fits]}

    def final_losses(self, cams: list[Camera]) -> list[float]:
        """Final-level loss of each camera AS IT IS on one common level (the matte bbox united with every camera's
        projected hull, ``FINAL_ROI_MARGIN``): the fit-level ROI depends on the fitted render, so two cameras are only
        comparable on a level built for both. Used to hold a refit against the previous revision's camera
        (modeler.observe.fit_view): on test-pilot-002 the parent back camera scored 0.2848 there against the refit's 0.2905,
        but had never been evaluated unchanged (its perspective was re-seeded at ``SEED_PERSPECTIVE``)."""
        boxes = []
        for c in cams:
            pts = raster._project_norm(self.scene.hull, c)
            if pts is not None:
                boxes.append((*pts.min(axis=0), *pts.max(axis=0)))
        inc = None
        if boxes:
            b = np.asarray(boxes, float)
            inc = (float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 2].max()), float(b[:, 3].max()))
        lv = Level(self.fg, FINAL_W, "final_common", margin=FINAL_ROI_MARGIN, include_xyxy=inc)
        lv.ignore_faces = self.ignore_faces
        self.evals += len(cams)
        return [float(lv.loss(self.scene, c)) for c in cams]


def prior_seeds(view: str) -> list[tuple[float, float, float, str]]:
    yaws = ([PRIOR_YAW[view] + d for d in PRIOR_YAW_OFFSETS] if view != "angled"
            else [s * y for s in (1.0, -1.0) for y in ANGLED_YAWS])
    return [(y, p, 0.0, "prior") for y in yaws for p in PITCHES]


def prior_seeds_near(view: str, yaw: float, pitch: float, n: int) -> list[tuple[float, float, float, str]]:
    """The ``n`` prior seeds nearest (yaw, pitch), yaw difference wrapped to +/-180 (ties keep the prior order). A warm
    start used ``prior_seeds(view)[:n]``, the first two yaw offsets only (front -12/-8, back 168/172): the prior yaw
    itself was never seeded again once a revision had a camera."""
    seeds = prior_seeds(view)
    d = [math.hypot((s[0] - yaw + 180.0) % 360.0 - 180.0, s[1] - pitch) for s in seeds]
    order = sorted(range(len(seeds)), key=lambda i: (d[i], i))
    return [seeds[i] for i in order[:n]]


def cross_seeds(target: str, winners: dict[str, Camera]) -> list[tuple[float, float, float, str]]:
    """Rotation seeds for ``target`` from other views' winners. The angled view is never a source."""
    out = []
    t_priors = [PRIOR_YAW[target]] if target != "angled" else [40.0, -40.0]
    for src, cam in winners.items():
        if src == target or src in HELD_OUT_VIEWS:
            continue
        dy = cam.yaw - PRIOR_YAW[src]
        for tp in t_priors:
            out.append((tp + dy, cam.pitch, cam.roll, f"cross:{src}:rotated"))
            out.append((tp - dy, cam.pitch, -cam.roll, f"cross:{src}:mirrored"))
            out.append((tp + dy, -cam.pitch, cam.roll, f"cross:{src}:pitch_twin"))
    return out


def _wrap_yaw(yaw: float, view: str) -> float:
    """Express a yaw inside the view's window convention (back around 180)."""
    if view == "back":
        return yaw % 360.0
    return (yaw + 180.0) % 360.0 - 180.0


# ----------------------------------------------------------------------------- measurement
def native_metrics(scene: raster.RasterScene, cam: Camera, fg: np.ndarray, roi,
                   ignore_faces: np.ndarray | None = None) -> dict:
    """Stride-1 IoU and symmetric contour distance (native px) inside ``roi``; mask for the sheet.
    With ``ignore_faces`` also the IoU with the pixels whose first hit is such a face removed."""
    x0, y0, x1, y1 = roi
    r = scene.render(cam, fg.shape, 1, roi)
    m = r["mask"]
    ref = fg[y0:y1, x0:x1]
    inter = int(np.count_nonzero(m & ref))
    union = int(np.count_nonzero(m | ref))
    extra = {}
    if ignore_faces is not None:
        fid = r["face_id"]
        W = np.zeros(m.shape, bool)
        W[fid >= 0] = ignore_faces[fid[fid >= 0]]
        hard = m & ~W
        extra = {"iou_lens_optional": np.count_nonzero(m & ref) / max(np.count_nonzero(hard | ref), 1),
                 "lens_pixels_optional": int(W.sum())}
    mean, p95 = contour_stats(m, ref)
    # Outer contour (holes filled on both): the part a camera controls; holes are mostly matte/lens issues.
    omean, op95 = contour_stats(ndimage.binary_fill_holes(m), ndimage.binary_fill_holes(ref))
    ys, xs = np.nonzero(m)
    ry, rx = np.nonzero(ref)
    ext = {}
    if len(xs) and len(rx):
        ext = {"photo_w_px": int(rx.max() - rx.min() + 1), "photo_h_px": int(ry.max() - ry.min() + 1),
               "render_w_px": int(xs.max() - xs.min() + 1), "render_h_px": int(ys.max() - ys.min() + 1)}
        ext["ratio_w"] = ext["photo_w_px"] / ext["render_w_px"]
        ext["ratio_h"] = ext["photo_h_px"] / ext["render_h_px"]
    return {**extra, "iou": inter / max(union, 1), "contour_mean_px": mean, "contour_p95_px": p95,
            "outer_contour_mean_px": omean, "outer_contour_p95_px": op95,
            "photo_only_px": int(np.count_nonzero(ref & ~m)), "render_only_px": int(np.count_nonzero(m & ~ref)),
            "extent": ext, "mask": m, "face_id": r["face_id"]}


def contour_stats(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Symmetric contour distance of two masks (px): mean of the two directed means, max of the p95s."""
    ea, eb = _edge(a), _edge(b)
    if not ea.any() or not eb.any():
        return float("inf"), float("inf")
    da = _dist_to(eb)[ea]           # a contour -> b contour
    db = _dist_to(ea)[eb]           # b contour -> a contour
    return float((da.mean() + db.mean()) / 2), float(max(np.percentile(da, 95), np.percentile(db, 95)))


def px_per_mm_at(cam: Camera, frame: NormFrame, point_mm: np.ndarray) -> float:
    """Local image px per model mm at a point (perspective shrinks with depth along the view axis)."""
    toward = raster.camera_basis(cam)[2]
    zn = float(frame.to_norm(np.asarray(point_mm, float).reshape(1, 3))[0] @ toward)
    return px_per_mm(cam, frame) / (1.0 - cam.perspective * zn)


def front_piece_centre(gen: generator.Generator) -> np.ndarray:
    """(0, y_c, front_z - front_depth / 2) in model mm: the centre of the S1 front piece."""
    res = gen.result
    return np.array([0.0, gen.frame.center[1], float(res["front_z_mm"]) - float(res["front_depth_mm"]) / 2.0])


def front_piece_faces(gen: generator.Generator) -> np.ndarray:
    """Decimated faces of the S1 front piece: centroid within ``front_depth_mm`` of the front-most depth."""
    res = gen.result
    zc = gen.Vd[gen.Fd].mean(axis=1)[:, 2]
    return zc >= float(res["front_z_mm"]) - float(res["front_depth_mm"])


def front_piece_scale(scene: raster.RasterScene, lv: Level, cam: Camera, is_front: np.ndarray,
                      unit_px: float, pivot: np.ndarray | None = None) -> dict:
    """Refit only scale + centre with temple pixels masked; rotation and perspective stay frozen.

    Masked: pixels whose first hit is a temple face, and pixels nearer to the rendered temples than
    to the rendered front piece (so a photo temple longer than the generator's cannot pull the zoom).
    """
    def score(c: Camera) -> float:
        r = lv.render(scene, c)
        if r is None:
            return _BAD
        fid = r["face_id"]
        hit = fid >= 0
        front = np.zeros_like(hit)
        front[hit] = is_front[fid[hit]]
        temple = hit & ~front
        if not front.any():
            return _BAD
        keep = ~temple
        if temple.any():
            keep &= _dist_to(front) <= _dist_to(temple)
        ref = lv.ref * keep
        opt = lv.ignored(r)                      # lens hits are optional where the matte has no lens
        hard = front if opt is None else front & ~opt
        inter = float(ref[front].sum())
        return 1.0 - inter / max(float(hard.sum()) + float(ref[~hard].sum()), 1e-9)

    base = score(cam)
    best = [base, cam]
    x_full = pack(cam, unit_px, pivot)

    def f(z):
        x = x_full.copy()
        x[4:7] = z
        c = unpack(x, unit_px, pivot)
        v = score(c)
        if v < best[0]:
            best[0], best[1] = v, c
        return v

    z0 = x_full[4:7]
    bounds = [(z0[0] - 10.0, z0[0] + 10.0), (z0[1] - 4.0, z0[1] + 4.0), (z0[2] - 4.0, z0[2] + 4.0)]
    optimize.minimize(f, z0, method="Powell", bounds=bounds, options={"maxfev": MAXFEV["fp"], "xtol": 0.02, "ftol": 1e-5})
    c = best[1]
    return {"scale_ratio": c.scale / cam.scale, "front_piece_iou_full_camera": 1.0 - base,
            "front_piece_iou_refit": 1.0 - best[0],
            "shift_px": [c.center_x - cam.center_x, c.center_y - cam.center_y]}


# ----------------------------------------------------------------------------- stage
def _cam_dict(c: Camera) -> dict:
    return {k: float(v) for k, v in c.to_dict().items()}


def _union_roi(fg: np.ndarray, cam: Camera, scene: raster.RasterScene, margin: float = 0.08):
    ys, xs = np.nonzero(fg)
    pts = project(scene.hull, cam)
    x0 = min(xs.min(), pts[:, 0].min())
    x1 = max(xs.max() + 1, pts[:, 0].max() + 1)
    y0 = min(ys.min(), pts[:, 1].min())
    y1 = max(ys.max() + 1, pts[:, 1].max() + 1)
    m = margin * (x1 - x0)
    H, W = fg.shape
    return (int(max(0, math.floor(x0 - m))), int(max(0, math.floor(y0 - m))),
            int(min(W, math.ceil(x1 + m))), int(min(H, math.ceil(y1 + m))))


def lens_faces(gen: generator.Generator, cam: Camera, shape: tuple[int, int], polys: list[np.ndarray],
               erode_px: float, band_mm: float = LENS_DEPTH_BAND_MM) -> np.ndarray:
    """Decimated faces of the generator's lens plates, labelled through the FRONT camera: the face centroid
    projects inside an S2 lens outline (eroded by ``erode_px``; negative dilates) and lies within ``band_mm``
    behind the first hit there
    (front and back lens surfaces; nose pads and temples behind the lens are excluded)."""
    frame = gen.frame
    scene = gen.scene(decimated=True)
    L = np.zeros(shape, np.uint8)
    for poly in polys:
        cv2.fillPoly(L, [np.round(np.asarray(poly) * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    k = int(round(abs(erode_px)))
    if k >= 1:
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
        L = cv2.erode(L, se) if erode_px > 0 else cv2.dilate(L, se)
    L = L.astype(bool)
    Cm = gen.Vd[gen.Fd].mean(axis=1).astype(np.float64)
    uv = project(frame.to_norm(Cm), cam)
    ui, vi = np.round(uv[:, 0]).astype(int), np.round(uv[:, 1]).astype(int)
    inb = (ui >= 0) & (ui < shape[1]) & (vi >= 0) & (vi < shape[0])
    inside = np.zeros(len(Cm), bool)
    inside[inb] = L[vi[inb], ui[inb]]
    out = np.zeros(len(Cm), bool)
    if not inside.any():
        return out
    hit = scene.cast(cam, uv[inside, 0], uv[inside, 1])
    toward = raster.camera_basis(cam)[2]
    depth = -(frame.to_norm(Cm[inside]) @ toward) * frame.extent
    out[np.nonzero(inside)[0]] = hit["hit"] & (depth - hit["depth"] <= band_mm)
    return out


def lens_coverage(fit: ViewFit, faces: np.ndarray) -> float | None:
    """Share of the rendered lens pixels (first hit on a lens face) that the matte covers, at the fit
    level through the view's best coarse camera; None when the lens is not seen."""
    lv = fit.levels["fit"]
    r = lv.render(fit.scene, fit.best_coarse()[1])
    if r is None:
        return None
    fid = r["face_id"]
    L = np.zeros(fid.shape, bool)
    L[fid >= 0] = faces[fid[fid >= 0]]
    L = cv2.erode(L.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)   # away from the lens edge
    if L.sum() < 50:
        return None
    return round(float(lv.ref[L].mean()), 4)


def fit_all(gen: generator.Generator, mattes: dict[str, np.ndarray], lens_polys: list[np.ndarray] | None = None,
            log=print) -> dict:
    """The search for all five views.

    Order: front/back priors -> a first front refine that labels the generator's lens faces through the S2
    outlines (the side mattes cannot see a lens edge-on or a clear lens, so those pixels are don't-care
    in left/right) -> left/right/angled priors -> cross-seeding (the angled view only receives) -> the
    fit- and final-level refinement of every view.
    """
    scene = gen.scene(decimated=True)
    fp = gen.frame.to_norm(front_piece_centre(gen).reshape(1, 3))[0]
    fits = {v: ViewFit(v, mattes[v], scene, pivot=None if v in ("left", "right") else fp) for v in VIEWS}
    info = {"lens_faces": 0}
    for v in ("front", "back"):
        fits[v].run_starts(prior_seeds(v), N_COARSE_POWELL)
    if lens_polys:
        cam0 = fits["front"].refine()["camera"]
        ppm = px_per_mm_at(cam0, gen.frame, front_piece_centre(gen))
        faces = lens_faces(gen, cam0, mattes["front"].shape, lens_polys, LENS_ERODE_MM * ppm)
        info["lens_faces"] = int(faces.sum())
    for v in ("left", "right", "angled"):
        fits[v].run_starts(prior_seeds(v), N_COARSE_POWELL)
    info["side_lens_coverage"] = {}
    if lens_polys and info["lens_faces"]:
        # Is the lens visible in this side matte? (tinted/mirror: yes; clear, or edge-on: no.) Only where it
        # is not are the lens hits made optional; a visible lens must keep constraining the pose.
        for v in ("left", "right"):
            cov = lens_coverage(fits[v], faces)
            info["side_lens_coverage"][v] = cov
            if cov is not None and cov < LENS_VISIBLE_MIN:
                fits[v].set_ignore(faces)
                fits[v].coarse, fits[v].starts = [], []
                fits[v].run_starts(prior_seeds(v), N_COARSE_POWELL)
    winners = {v: fits[v].best_coarse()[1] for v in FIT_VIEWS}
    log("  round1 " + " ".join(f"{v}:{fits[v].best_coarse()[0]:.4f}@{fits[v].best_coarse()[1].yaw:.0f}/"
                               f"{fits[v].best_coarse()[1].pitch:.0f}" for v in VIEWS) + f" lens_faces {info['lens_faces']}")
    # Round 2: cross-seeding from the FIT views' round-1 winners (the angled view only receives).
    for v in VIEWS:
        seeds = [(_wrap_yaw(y, v), p, r, o) for y, p, r, o in cross_seeds(v, winners)]
        seeds = [s for s in seeds if yaw_window(v, s[0])[0] <= s[0] <= yaw_window(v, s[0])[1]]
        if seeds:
            fits[v].run_starts(seeds, N_CROSS_POWELL)
    log("  round2 " + " ".join(f"{v}:{fits[v].best_coarse()[0]:.4f}@{fits[v].best_coarse()[1].yaw:.0f}/"
                               f"{fits[v].best_coarse()[1].pitch:.0f}({fits[v].best_coarse()[2]})" for v in VIEWS))
    refined = {v: fits[v].refine() for v in VIEWS}
    return {"scene": scene, "fits": fits, "refined": refined, "info": info}


def sign_test(fit: ViewFit) -> dict:
    """Best coarse loss when the side view's yaw is forced to the OPPOSITE sign (diagnostic only)."""
    lv = fit.levels["coarse"]
    opp = -PRIOR_YAW[fit.view]
    win = (opp - YAW_HALF_WINDOW, opp + YAW_HALF_WINDOW)
    best = (_BAD, None)
    scored = sorted(((lv.loss(fit.scene, align(fit.scene, lv, opp, p, 0.0)), p) for p in PITCHES))
    for loss, p in scored[:2]:
        cam = align(fit.scene, lv, opp, p, 0.0)
        c2, l2 = fit.powell(lv, cam, MAXFEV["coarse"], yaw_win=win)
        if l2 < best[0]:
            best = (l2, c2)
    own = fit.best_coarse()[0]
    return {"opposite_yaw_window": list(win), "best_loss_opposite": round(best[0], 5),
            "best_loss": round(own, 5), "margin": round(best[0] - own, 5),
            "opposite_camera": [round(best[1].yaw, 2), round(best[1].pitch, 2)] if best[1] is not None else None}


def pitch_twin(fit: ViewFit, cam: Camera) -> dict:
    """Refit at the fit level from (yaw, -pitch, roll): the orthographic silhouette twin of a side view."""
    lv = fit.levels["fit"]
    own = lv.loss(fit.scene, cam)
    start = align(fit.scene, lv, cam.yaw, -cam.pitch, cam.roll, cam.perspective)
    c2, l2 = fit.staged(lv, start, STAGES_FIT, MAXFEV["twin"])
    return {"twin_camera": [round(c2.yaw, 2), round(c2.pitch, 2), round(c2.roll, 2), round(c2.perspective, 3)],
            "twin_loss": round(l2, 5), "own_loss": round(own, 5), "margin": round(l2 - own, 5)}


def yaw_profile(fit: ViewFit, cam: Camera, delta: float = YAW_PROFILE_DEG) -> dict:
    """Loss increase at the fit level when yaw is pinned at best +/- delta and the rest is refit.

    A small increase means the silhouette barely determines the yaw (yaw/pitch/perspective trade off).
    """
    lv = fit.levels["fit"]
    own = lv.loss(fit.scene, cam)
    out = {}
    for sgn in (-1.0, 1.0):
        y = cam.yaw + sgn * delta
        start = Camera(y, cam.pitch, cam.roll, cam.perspective, cam.scale, cam.center_x, cam.center_y)
        _, l2 = fit.powell(lv, start, MAXFEV["profile"], yaw_win=(y - 0.01, y + 0.01), xtol=0.03,
                           local={"yaw": 0.01, "pitch": 12.0, "roll": 8.0, "scale": 10.0, "shift": 5.0})
        out[f"{sgn * delta:+.0f}"] = round(l2 - own, 5)
    return {"own_loss": round(own, 5), "loss_increase": out}


def run(product: str, run: str = "m1", force: bool = False, log=print) -> dict:
    sd = stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    t0 = time.perf_counter()
    gen = generator.load(product, run)
    s0_res, s0 = stage_dir(run, product, "s0_intake").load()
    s2_res, s2 = stage_dir(run, product, "s2_front").load()
    lens_polys = [s2.get(f"lens{i}_poly_front", s2.get(f"lens{i}_poly")) for i in range(1, len(s2_res["lenses"]) + 1)]
    lens_polys = [q for q in lens_polys if q is not None and len(q) >= 3]
    frame = gen.frame
    mattes = {v: s0[f"fg_{v}"].astype(bool) for v in VIEWS}
    search = fit_all(gen, mattes, lens_polys, log=log)
    scene, fits, refined = search["scene"], search["fits"], search["refined"]
    is_front = front_piece_faces(gen)
    res1 = gen.result
    fp_center = front_piece_centre(gen)
    fp_plane = np.array([0.0, frame.center[1], float(res1["front_z_mm"])])

    cameras, masks = {}, {}
    for v in VIEWS:
        fit, ref = fits[v], refined[v]
        cam = ref["camera"]
        roi = _union_roi(mattes[v], cam, scene)
        nm = native_metrics(scene, cam, mattes[v], roi, fit.ignore_faces)
        masks[v] = (roi, nm.pop("mask"), nm.pop("face_id"))
        ppm = px_per_mm(cam, frame)
        ppm_fp = px_per_mm_at(cam, frame, fp_center)
        ppm_plane = px_per_mm_at(cam, frame, fp_plane)
        flags = []
        iou_min = FRONT_IOU_MIN if v == "front" else VIEW_IOU_MIN
        iou_judged = nm.get("iou_lens_optional", nm["iou"])
        if iou_judged < iou_min:
            flags.append("low_iou")
        info = {"camera": _cam_dict(cam), "iou": round(nm["iou"], 5),
                "iou_lens_optional": round(nm["iou_lens_optional"], 5) if "iou_lens_optional" in nm else None,
                "lens_pixels_optional": nm.get("lens_pixels_optional", 0),
                "icp_steps_accepted": fit.icp_accepted,
                "contour_mean_px": round(nm["contour_mean_px"], 3), "contour_p95_px": round(nm["contour_p95_px"], 3),
                "contour_mean_mm": round(nm["contour_mean_px"] / ppm_fp, 3),
                "contour_mean_pct_width": round(100.0 * nm["contour_mean_px"] / fit.levels["coarse"].width_native, 3),
                "outer_contour_mean_px": round(nm["outer_contour_mean_px"], 3),
                "outer_contour_p95_px": round(nm["outer_contour_p95_px"], 3),
                "px_per_mm": round(ppm, 5), "px_per_mm_at_front_piece": round(ppm_fp, 5),
                "px_per_mm_at_front_plane": round(ppm_plane, 5),
                "photo_only_px": nm["photo_only_px"], "render_only_px": nm["render_only_px"],
                "extent": {k: (round(x, 4) if isinstance(x, float) else x) for k, x in nm["extent"].items()},
                "roi_xyxy": list(roi),
                "levels": {k: {"stride": lv.stride, "grid_width_px": round(lv.width, 1)} for k, lv in fit.levels.items()},
                "loss_final_level": round(ref["loss_final_level"], 5), "loss_fit_level": round(ref["loss_fit_level"], 5),
                "winner_origin": ref["origin"], "fit_candidates": ref["fit_candidates"],
                "starts": fit.starts, "evaluations": fit.evals,
                "role": "held_out_camera_only" if v in HELD_OUT_VIEWS else "fit"}
        if v in ("left", "right"):
            st = sign_test(fit)
            info["yaw_sign_test"] = st
            if st["margin"] < YAW_SIGN_MIN_MARGIN:
                flags.append("yaw_sign_weak")
            if abs(cam.pitch) < PITCH_TWIN_MIN_DEG:
                info["pitch_twin"] = {"skipped": f"|pitch| < {PITCH_TWIN_MIN_DEG} deg: the twin is the same camera"}
            else:
                tw = pitch_twin(fit, cam)
                info["pitch_twin"] = tw
                if abs(tw["margin"]) < PITCH_TWIN_TIE:
                    flags.append("pitch_sign_ambiguous")
                elif tw["margin"] < 0:
                    flags.append("pitch_twin_better")
        if v == "angled":
            by_sign = {}
            for l, c, o in fit.coarse:
                k = "+" if c.yaw >= 0 else "-"
                if k not in by_sign or l < by_sign[k]:
                    by_sign[k] = l
            info["yaw_sign_losses"] = {k: round(x, 5) for k, x in sorted(by_sign.items())}
            if len(by_sign) == 2 and abs(by_sign["+"] - by_sign["-"]) < YAW_SIGN_MIN_MARGIN:
                flags.append("yaw_sign_weak")
            info["yaw_profile"] = yaw_profile(fit, cam)
            if min(info["yaw_profile"]["loss_increase"].values()) < PITCH_TWIN_TIE:
                flags.append("yaw_poorly_determined")
        fps = front_piece_scale(scene, fit.levels["fit"], cam, is_front, fit.unit, fit.pivot)
        info["front_piece_scale"] = {k: (round(x, 5) if isinstance(x, float) else [round(y, 2) for y in x])
                                     for k, x in fps.items()}
        if abs(fps["scale_ratio"] - 1.0) > FP_SCALE_TOL:
            flags.append("front_piece_scale_disagrees")
        if s0_res["views"][v]["flags"]:
            info["s0_flags"] = s0_res["views"][v]["flags"]
        info["flags"] = flags
        info["weight"] = LOW_WEIGHT if "low_iou" in flags else 1.0
        cameras[v] = info
        log(f"  {v:6s} iou {nm['iou']:.4f} (lens-opt {iou_judged:.4f}) icp {fit.icp_accepted} contour {nm['contour_mean_px']:.2f}/{nm['contour_p95_px']:.2f} px "
            f"yaw {cam.yaw:.1f} pitch {cam.pitch:.1f} roll {cam.roll:.1f} p {cam.perspective:.3f} "
            f"px/mm {ppm:.3f} fp-scale {fps['scale_ratio']:.4f} {flags}")

    consistency = _consistency(product, cameras, s0_res, s2_res, res1)
    result = {"stage": STAGE, "product": product, "run": run, "frame": frame.to_dict(),
              "cameras": cameras, "consistency": consistency,
              "lens_faces": {"count": search["info"]["lens_faces"], "of": int(len(gen.Fd)),
                             "use": "optional render pixels in left/right when the side matte covers < "
                                    f"{LENS_VISIBLE_MIN:.0%} of the rendered lens (the side mattes have no lens proposal): "
                                    "they may explain photo foreground but are not penalised over background",
                             "side_lens_coverage": search["info"].get("side_lens_coverage", {}),
                             "optional_in": [v for v in ("left", "right") if fits[v].ignore_faces is not None],
                             "source": "S2 lens{i}_poly_front through the first front camera, eroded "
                                       f"{LENS_ERODE_MM} mm (negative = dilated), within {LENS_DEPTH_BAND_MM} mm "
                                       "of the first hit"},
              "conventions": {
                  "camera": "reconstruction.camera.Camera in the S1 NormFrame -> native photo px (core.project_mm)",
                  "yaw": "0 = from +Z (front photo), +90 = from +X (glasses' own left side; +Z at image-left), "
                         "-90 = from -X, 180 = from -Z (back photo); +pitch = camera above looking down",
                  "left_right": "left.jpg -> yaw +90, right.jpg -> yaw -90 (verified: plate at image-left in left.jpg; "
                                "see yaw_sign_test margins and the textured renders on sheet.png)",
                  "px_per_mm": "core.px_per_mm (at the NormFrame centre depth); px_per_mm_at_front_piece is the local "
                               "value at the front-piece centre (0, y_c, front_z - front_depth/2)",
                  "contour": "symmetric: mean of the two directed means, max of the two directed p95, native px",
                  "held_out": "the angled camera is fitted like the others but never seeds, scales or chooses another view",
              },
              "policy": {"pitches": PITCHES, "angled_yaws": ANGLED_YAWS, "yaw_half_window": YAW_HALF_WINDOW,
                         "levels_target_width_px": [COARSE_W, FIT_W, FINAL_W], "boundary_weight": BOUNDARY_WEIGHT,
                         "boundary_trunc": BOUNDARY_TRUNC, "front_iou_min": FRONT_IOU_MIN, "view_iou_min": VIEW_IOU_MIN,
                         "frozen_refine_bounds": {"scale_pct": 2.0, "rotation_deg": 3.0, "translation_pct_width": 2.0}},
              "inputs": {"s1_source_sha256": res1["source_sha256"],
                         "photo_sha256": {v: s0_res["views"][v]["photo_sha256"] for v in VIEWS}},
              "flags": sorted({f"{v}:{f}" for v in VIEWS for f in cameras[v]["flags"]}),
              "timings_s": {"total": round(time.perf_counter() - t0, 1)}}
    sd.save(result)
    t = time.perf_counter()
    make_sheet(product, run, gen, cameras, masks, mattes)
    result["timings_s"]["sheet"] = round(time.perf_counter() - t, 1)
    sd.save(result)
    return result


def _consistency(product: str, cameras: dict, s0_res: dict, s2_res: dict, res1: dict) -> dict:
    """Front-camera px/mm vs S2's provisional width scale, and cross-view scale diagnostics."""
    fw = float(PRODUCTS[product].front_width_mm)
    total_x = float(res1["front_piece"]["total_x_extent_mm"])
    out = {"front_width_mm": fw, "generator_total_x_extent_mm": total_x}
    s2_ppm = float(s2_res["width_px"]) / fw
    src = s2_res["outline_source"]
    s2_view = "front" if src == "front" else "back"      # the photo whose px S2's width_px counts
    c = cameras[s2_view]
    chk = {
        "s2_outline_source": src, "s2_view": s2_view, "s2_width_px": float(s2_res["width_px"]),
        "s2_px_per_mm_provisional": round(s2_ppm, 5),
        "camera_px_per_mm_at_front_plane": c["px_per_mm_at_front_plane"],
        "camera_px_per_mm_at_front_piece": c["px_per_mm_at_front_piece"],
        "ratio": round(c["px_per_mm_at_front_plane"] / s2_ppm, 5),
        "ratio_front_piece_centre": round(c["px_per_mm_at_front_piece"] / s2_ppm, 5),
        "ratio_expected_from_total_extent": round(fw / total_x, 5),
        "photo_over_render_width": c["extent"].get("ratio_w"),
        "note": "ratio = camera px/mm at the front plane (z = front_z) / (S2 width_px / 140 mm). S2's width is the "
                "whole silhouette, the generator's whole X extent is total_x_extent_mm, so a camera that reproduces "
                "the silhouette width with the widest points at the front plane gives ratio_expected_from_total_extent; "
                "photo_over_render_width is the direct silhouette-width agreement of this camera.",
    }
    if src != "front":   # back_mirrored: S2 px refer to the back photo; the front camera via source_to_front
        A = np.asarray(s2_res["source_to_front"], float)[:, :2]
        k = math.sqrt(abs(np.linalg.det(A)))
        chk["front_camera"] = {"source_to_front_scale": round(k, 6),
                               "s2_px_per_mm_mapped_to_front": round(s2_ppm * k, 5),
                               "camera_px_per_mm_at_front_plane": cameras["front"]["px_per_mm_at_front_plane"],
                               "ratio": round(cameras["front"]["px_per_mm_at_front_plane"] / (s2_ppm * k), 5)}
    out["front_vs_s2"] = chk
    views = {}
    f_ppm = cameras["front"]["px_per_mm_at_front_piece"]
    f_shape = s0_res["views"]["front"]["shape"]
    for v in VIEWS:
        c = cameras[v]
        views[v] = {"px_per_mm_at_front_piece": c["px_per_mm_at_front_piece"],
                    "ratio_to_front": round(c["px_per_mm_at_front_piece"] / f_ppm, 5),
                    "same_image_size_as_front": s0_res["views"][v]["shape"] == f_shape,
                    "extent_ratio_w": c["extent"].get("ratio_w"), "extent_ratio_h": c["extent"].get("ratio_h"),
                    "front_piece_scale_ratio": c["front_piece_scale"]["scale_ratio"]}
    out["views"] = views
    same = [v for v in VIEWS if v != "front" and views[v]["same_image_size_as_front"]]
    out["same_size_views_ratio_to_front"] = {v: views[v]["ratio_to_front"] for v in same}
    out["note"] = ("Every camera is fitted to the same metric generator, so the scale is shared by construction. "
                   "Cross-photo px/mm ratios equal 1 only if the photographer kept the zoom; extent ratios != 1 inside "
                   "one view and front-piece-only scale ratios != 1 expose a generator proportion (e.g. temple length) "
                   "that disagrees with the photo.")
    return out


def load_cameras(product: str, run: str = "m1") -> tuple[NormFrame, dict[str, Camera], dict]:
    """The frozen S3 cameras: (NormFrame, {view: Camera}, result). Later stages may only apply the bounded
    refine recorded in ``result['policy']['frozen_refine_bounds']``."""
    sd = stage_dir(run, product, STAGE)
    if not sd.done():
        raise FileNotFoundError(f"S3 artifacts missing for {product}/{run}: run bsa.cameras first")
    res = json.loads(sd.result_path.read_text())
    return (NormFrame.from_dict(res["frame"]),
            {v: camera_from_dict(c["camera"]) for v, c in res["cameras"].items()}, res)


# ----------------------------------------------------------------------------- sheets
def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def resized_camera(cam: Camera, k: float, x0: float = 0.0, y0: float = 0.0) -> Camera:
    """The camera of the crop [x0:, y0:] of a native image resized by ``k`` (pixel centres kept exact)."""
    return Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale * k,
                  (cam.center_x - x0 + 0.5) * k - 0.5, (cam.center_y - y0 + 0.5) * k - 0.5)


def overlay_tile(photo: np.ndarray, fg: np.ndarray, roi, render_mask: np.ndarray, width: int) -> np.ndarray:
    """Photo crop: red = photo only, green = render only, render contour drawn in blue."""
    x0, y0, x1, y1 = roi
    rgb = photo[y0:y1, x0:x1].astype(np.float32)
    ref = fg[y0:y1, x0:x1]
    m = render_mask
    po, ro = ref & ~m, m & ~ref
    rgb[po] = rgb[po] * 0.35 + np.array([235, 30, 30]) * 0.65
    rgb[ro] = rgb[ro] * 0.35 + np.array([30, 200, 30]) * 0.65
    rgb = rgb.astype(np.uint8)
    k = width / rgb.shape[1]
    small = cv2.resize(rgb, (width, max(1, int(round(rgb.shape[0] * k)))), interpolation=cv2.INTER_AREA)
    mk = cv2.resize(m.astype(np.uint8) * 255, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) >= 128
    small[_edge(mk)] = (20, 60, 255)
    return small


def textured_tile(gen: generator.Generator, cam: Camera, roi, width: int, tex) -> np.ndarray:
    x0, y0, x1, y1 = roi
    k = width / (x1 - x0)
    shape = (max(1, int(round((y1 - y0) * k))), width)
    img, _ = generator.shaded_render(gen, resized_camera(cam, k, x0, y0), shape, tex)
    return img


def make_sheet(product: str, run: str, gen: generator.Generator, cameras: dict, masks: dict,
               mattes: dict, tile_w: int = 520) -> None:
    prod = PRODUCTS[product]
    tex = gen.texture("basecolor")
    font, small = _font(18), _font(15)
    cols = []
    for v in VIEWS:
        roi, m, _ = masks[v]
        photo = load_photo(prod, v)
        ov = overlay_tile(photo, mattes[v], roi, m, tile_w)
        cam = camera_from_dict(cameras[v]["camera"])
        tx = textured_tile(gen, cam, roi, tile_w, tex)
        h = max(ov.shape[0], tx.shape[0])
        c = cameras[v]
        head = Image.new("RGB", (tile_w, 118), (255, 255, 255))
        d = ImageDraw.Draw(head)
        role = " (held out: camera only)" if v in HELD_OUT_VIEWS else ""
        d.text((6, 4), f"{v}{role}", fill=(0, 0, 0), font=font)
        cc = c["camera"]
        d.text((6, 28), f"yaw {cc['yaw']:.1f}  pitch {cc['pitch']:.1f}  roll {cc['roll']:.1f}  persp {cc['perspective']:.3f}",
               fill=(0, 0, 0), font=small)
        d.text((6, 48), f"IoU {c['iou']:.4f}   contour {c['contour_mean_px']:.2f} / p95 {c['contour_p95_px']:.2f} px"
                        f"  ({c['contour_mean_mm']:.2f} mm)", fill=(0, 0, 0), font=small)
        d.text((6, 68), f"px/mm {c['px_per_mm']:.3f} (front piece {c['px_per_mm_at_front_piece']:.3f})"
                        f"  fp-scale x{c['front_piece_scale']['scale_ratio']:.3f}", fill=(0, 0, 0), font=small)
        d.text((6, 88), ("flags: " + ", ".join(c["flags"]))[:70] if c["flags"] else "flags: none",
               fill=(180, 0, 0) if c["flags"] else (0, 120, 0), font=small)
        col = np.full((118 + 2 * h + 10, tile_w, 3), 255, np.uint8)
        col[:118] = np.asarray(head)
        col[118:118 + ov.shape[0]] = ov
        col[118 + h + 10:118 + h + 10 + tx.shape[0]] = tx
        cols.append(col)
    H = max(c.shape[0] for c in cols)
    sheet = np.full((H + 40, len(cols) * (tile_w + 8), 3), 255, np.uint8)
    for i, c in enumerate(cols):
        sheet[40:40 + c.shape[0], i * (tile_w + 8):i * (tile_w + 8) + tile_w] = c
    im = Image.fromarray(sheet)
    d = ImageDraw.Draw(im)
    d.text((8, 8), f"S3 cameras - {product}   top: photo, red = photo only, green = render only, blue = render contour;"
                   f"   bottom: textured generator through the same camera", fill=(0, 0, 0), font=font)
    im.save(stage_dir(run, product, STAGE).root / "sheet.png")


def contact_sheet(run: str = "m1", products=None, tile_w: int = 300) -> str:
    """All products x views overlay tiles (read back from each product's result + a fresh render)."""
    products = list(products or PRODUCTS)
    font = _font(14)
    rows = []
    for p in products:
        sd = stage_dir(run, p, STAGE)
        if not sd.done():
            continue
        res = sd.load()[0]
        gen = generator.load(p, run)
        s0 = stage_dir(run, p, "s0_intake").load()[1]
        scene = gen.scene(decimated=True)
        tiles = []
        for v in VIEWS:
            c = res["cameras"][v]
            cam = camera_from_dict(c["camera"])
            roi = tuple(c["roi_xyxy"])
            fg = s0[f"fg_{v}"].astype(bool)
            x0, y0, x1, y1 = roi
            stride = max(1, int((x1 - x0) // (2 * tile_w)))
            r = scene.render(cam, fg.shape, stride, roi)["mask"]
            m = cv2.resize(r.astype(np.uint8), (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST).astype(bool)
            t = overlay_tile(load_photo(PRODUCTS[p], v), fg, roi, m, tile_w)
            t = t[:min(t.shape[0], 200)]
            tile = np.full((222, tile_w, 3), 255, np.uint8)
            tile[:t.shape[0]] = t
            im = Image.fromarray(tile)
            ImageDraw.Draw(im).text((4, 204), f"{p} {v} IoU {c['iou']:.3f} cm {c['contour_mean_px']:.1f}px "
                                              f"{'!' if c['flags'] else ''}", fill=(0, 0, 0), font=font)
            tiles.append(np.asarray(im))
        rows.append(np.hstack(tiles))
    out = run_dir(run, products[0]).parent / "s3_contact.png"
    Image.fromarray(np.vstack(rows)).save(out)
    return str(out)


def iou_table(run: str = "m1", products=None) -> str:
    products = list(products or PRODUCTS)
    lines = ["product  " + "  ".join(f"{v:>14s}" for v in VIEWS)]
    for p in products:
        sd = stage_dir(run, p, STAGE)
        if not sd.done():
            continue
        res = sd.load()[0]
        lines.append(f"{p:8s} " + "  ".join(f"{res['cameras'][v]['iou']:.3f}/{res['cameras'][v]['contour_mean_px']:5.2f}px"
                                            for v in VIEWS))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="S3: fit one frozen camera per photo")
    ap.add_argument("--run", default="m1")
    ap.add_argument("--product", action="append")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--contact", action="store_true", help="rebuild data/bsa/runs/<run>/s3_contact.png")
    a = ap.parse_args(argv)
    products = a.product or list(PRODUCTS)
    for p in products:
        print(p, flush=True)
        r = run(p, a.run, a.force, log=lambda s: print(s, flush=True))
        print(p, json.dumps({v: {"iou": r["cameras"][v]["iou"], "flags": r["cameras"][v]["flags"]} for v in VIEWS}),
              r["timings_s"], flush=True)
    print(iou_table(a.run, products))
    if a.contact or len(products) > 1:
        print(contact_sheet(a.run))


if __name__ == "__main__":
    main()
