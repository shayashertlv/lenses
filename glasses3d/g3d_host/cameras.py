"""Camera fitting: one camera per reference photo, fitted to a fixed mesh on the photo's matte (silhouette IoU).

Ported from automation/bsa/cameras.py (WP3b; glasses3d/plan/archive/20261019/PORT_REVIEW.md section 9). Every camera is a
``glasses3d.g3d_host.camera.Camera`` in the model's NormFrame that maps MODEL millimetres to NATIVE photo pixels
(``camera.project_mm``).

Sign conventions (unchanged from bsa):

- yaw 0 looks from +Z (the front photo): +X image-right, +Y up.
- yaw +90 looks from +X, the glasses' own LEFT side (the viewer's right in the front photo); +Z, the lens plate, is at
  image-LEFT. ``left`` = yaw +90, ``right`` = yaw -90.
- yaw 180 looks from -Z (the back photo). Positive pitch = the camera is above the glasses looking down; +90 = straight
  down (a top view).
- Orthographic silhouettes of a mirror-symmetric object satisfy S(yaw, pitch) = S(180 - yaw, -pitch). For a side view
  (yaw 90) that makes the pitch SIGN a near-tie that only perspective and asymmetry break; ``refine`` always carries
  the side views' pitch twin.

Views (``PRIOR_YAW``, ``prior_seeds``): front 0, back 180, left +90, right -90, angled (|yaw| 8..80, both signs),
and, first-class since WP3b (the prototype monkeypatched them): ``top`` (yaw 0, camera above; seeds +/-70..88 both
signs, as the prototype fitted FT1119 reference-04, then a +45..62 band), ``topb`` (the same from yaw 180), and
``top_oblique_left`` / ``top_oblique_right`` (an elevated side view: yaw +90 / -90, pitch seeds +15..+60, pitch window
``PITCH_WINDOWS``, coarse starts distinct in yaw too: ``YAW_DISTINCT_VIEWS``).
``bottom`` / ``bottomb`` mirror the top views' pitch grids for cameras below the glasses. Top and bottom search
the full image-plane roll circle using uniform 45-degree seeds and overlapping +/-25-degree optimizer charts;
the original zero-roll seed prefix and optimizer bounds remain unchanged. The half-turn chart crosses 180 smoothly.
``rear_threequarter_left`` / ``rear_threequarter_right`` cover signed rear-oblique views (yaw +100..172 /
-172..-100); their seed grids mirror the front angled grid about the back, with ordinary pitch seeds.
``PITCH_BOUNDS`` is +/-95 for every view (bsa used (-40, 60); the prototype's +/-95 is what fitted the reference
cameras, and the bounds shape the contour-ICP trust region, so they are part of the reproduced result).

Pitch sign: the top views keep the prototype's signed seeds and +/-95 window (that is what reproduces reference-04),
so a camera BELOW the glasses can win a photo labelled 'top'. Every ``fit_view`` therefore reports a sign margin
(``ViewFit.pitch_twin``: the best loss with the pitch held on the opposite sign), and ``fit_views`` refuses a winner
whose pitch sign contradicts a sign-fixed label ('top', 'bottom', 'elevated side').

Method (bsa DESIGN.md S3): seeds are scale/centre-aligned on the matte bounding box and scored at ~256 px glasses
width; the best pitch-distinct starts are Powell-refined there; the best coarse solutions (plus the side views' pitch
twin) are refined at ~512 px and the winner again at ~1024 px. Refinement = bounded Powell with shrinking bounds on
(1 - soft IoU) + 4 x symmetric boundary distance / glasses width, each stage followed by a contour-ICP least-squares
step kept only when it lowers that same loss. The optimizer moves the image position and magnification of a PIVOT
(the front-piece centre), so rotation, perspective and zoom do not drag the silhouette sideways.

Dropped from bsa (product/generator-specific): ``fit_all`` (its schedule is ``fit_views``), ``front_piece_*``,
``lens_faces``, ``lens_coverage``, ``run``, ``_consistency``, ``load_cameras``, the sheets and two per-view diagnostics
(``sign_test``: left/right yaw sign; ``yaw_profile``). ``pitch_twin`` is re-ported as ``ViewFit.pitch_twin``. See
glasses3d/plan/archive/20261019/PORT_REVIEW.md section 9.
"""
from __future__ import annotations

import math
from dataclasses import replace

import cv2
import numpy as np
from scipy import ndimage, optimize

from . import raster
from .camera import Camera, NormFrame, project, project_mm, px_per_mm  # noqa: F401  (NormFrame/px_per_mm re-exported)

# ----------------------------------------------------------------------------- policy
PRIOR_YAW = {"front": 0.0, "back": 180.0, "left": 90.0, "right": -90.0,
             "top": 0.0, "topb": 180.0, "bottom": 0.0, "bottomb": 180.0,
             "top_oblique_left": 90.0, "top_oblique_right": -90.0,
             "rear_threequarter_left": 140.0, "rear_threequarter_right": -140.0}
VIEWS = tuple(PRIOR_YAW) + ("angled",)
HELD_OUT_VIEWS = ("angled",)      # bsa core: never seeds another view
SIDE_VIEWS = ("left", "right")    # the orthographic pitch twin is always carried to the fit level
TOP_VIEWS = ("top", "topb")       # pitch near +90: yaw and roll are nearly degenerate, so they never seed another view
BOTTOM_VIEWS = ("bottom", "bottomb")  # camera below, looking over the front/back respectively
AXIAL_VIEWS = TOP_VIEWS + BOTTOM_VIEWS
TOP_OBLIQUE_VIEWS = ("top_oblique_left", "top_oblique_right")
REAR_THREEQUARTER_VIEWS = ("rear_threequarter_left", "rear_threequarter_right")
# cross_seeds sources: never the held-out view, top/bottom views (see above) or elevated side views (their pitch is a
# deliberately different camera height, not the shared turntable elevation the rotated/mirrored seeds assume)
NOT_CROSS_SOURCES = HELD_OUT_VIEWS + AXIAL_VIEWS + TOP_OBLIQUE_VIEWS + REAR_THREEQUARTER_VIEWS
ANGLED_YAWS = (20.0, 27.5, 35.0, 42.5, 50.0, 57.5, 65.0)
PITCHES = (-20.0, -10.0, 0.0, 10.0, 20.0, 40.0)    # the plan's {-20, 0, 20, 40} plus +/-10: the basins are narrow
PRIOR_YAW_OFFSETS = (-12.0, -8.0, -4.0, 0.0, 4.0, 8.0, 12.0)   # front/back/left/right seeds around the prior
# top/topb: the seed grid make_uv_test.py fitted FT1119 reference-04 with (both pitch signs: a straight-down photo is a
# near-tie with its straight-up twin; the photo's real pitch came out at +60, below every seed) ...
TOP_YAW_OFFSETS = (-6.0, 0.0, 6.0)
TOP_PITCHES = (70.0, 80.0, 88.0, -70.0, -80.0, -88.0)
# ... plus a low band (camera above only), appended AFTER that grid. Every real top photo so far sits below it (FT1119
# reference-04 at +60, Prada reference-03 at +54), and on the synthetic test glasses a top photo at +60 fitted from
# the grid alone ended at pitch 77.7, IoU 0.46 (with the band: IoU 0.9999). On the two real photos the band ends in the
# same basin (reference-04 loss 0.18902 vs 0.18893, 0.06 deg; 0.21 deg under scipy 1.16.0; Prada reference-03 0.23135 vs
# 0.2322).
TOP_LOW_PITCHES = (45.0, 55.0, 62.0)
# views whose coarse starts must also be distinct in YAW (bsa: 'angled' only). For the elevated side views the label
# fixes the side and the camera height band but not the photographer's yaw offset; keyed on pitch alone, one yaw per
# height was refined and the best-scoring wrong yaw's basin was the only one explored (synthetic elevated side at
# pitch 18: IoU 0.869 -> 0.997; FT1119 reference-06: loss 0.3204 -> 0.3136, IoU 0.7079 -> 0.7141).
YAW_DISTINCT_VIEWS = ("angled",) + TOP_OBLIQUE_VIEWS + REAR_THREEQUARTER_VIEWS
# top_oblique_*: an elevated side view (FT1119 reference-06 shows the inner temple print over the top). 25..60 was the
# brief; 15 and 20 were added because reference-06 itself fits at pitch ~19 (seeded from 25+ it ended in a worse basin:
# IoU 0.692 against 0.708 from the 'left' prior, whose seeds include 20)
TOP_OBLIQUE_PITCHES = (15.0, 20.0, 25.0, 32.0, 39.0, 46.0, 53.0, 60.0)
SEED_PERSPECTIVE = 0.1
YAW_HALF_WINDOW = 35.0            # every view with a yaw prior: prior +/- 35 deg
ANGLED_WINDOW = (8.0, 80.0)       # |yaw| of the angled view, per sign
REAR_THREEQUARTER_WINDOW = (100.0, 172.0)  # 180 - ANGLED_WINDOW, per explicitly named side
PITCH_BOUNDS = (-95.0, 95.0)      # bsa: (-40, 60); +/-95 is what the prototype fitted every reference camera with
PITCH_WINDOWS = {"top_oblique_left": (5.0, 85.0), "top_oblique_right": (5.0, 85.0)}   # the label fixes the sign
ROLL_BOUNDS = (-25.0, 25.0)
# Overlapping charts cover the full roll circle without changing the old zero-roll optimizer bounds.
# The half-turn chart spans 155..205: a fit can cross 180 smoothly, then canonicalize its reported roll.
AXIAL_ROLL_STEP = 45.0
AXIAL_ROLL_SEEDS = (0.0, -45.0, 45.0, -90.0, 90.0, -135.0, 135.0, 180.0)
PERSPECTIVE_BOUNDS = (0.0, 0.6)   # normalized units: 1 / camera distance in NormFrame extents
COARSE_W, FIT_W, FINAL_W = 256.0, 512.0, 1024.0   # target glasses width (grid px) per level
ROI_MARGIN = 0.3                  # ROI = matte bbox + 30 % of its width on every side (coarse, fit)
FINAL_ROI_MARGIN = 0.1            # final level: matte bbox U fit-level render bbox + 10 %
OUTSIDE_PENALTY = 0.5             # loss per unit share of the projected hull bbox that leaves the ROI
BOUNDARY_WEIGHT = 4.0
BOUNDARY_TRUNC = 0.1              # boundary distances truncated at 10 % of the glasses width
N_COARSE_POWELL = 3               # prior starts refined per view (per yaw sign for 'angled') at the coarse level
                                  # (fit_view's default; make_uv_test.py used 2, the repro test passes 2 explicitly)
N_CROSS_POWELL = 1                # cross seeds refined per view (per yaw sign for 'angled')
N_FIT_POWELL = 2                  # coarse winners refined at the fit level ...
FIT_SECOND_WITHIN = 1.25          # ... the second only when its coarse loss is within 25 % of the best
PITCH_TWIN_MIN_DEG = 3.0          # below this |pitch| the twin is the same camera: not carried, no sign margin
PITCH_TWIN_TIE = 0.005            # sign margin under which the pitch sign counts as a tie (bsa)
MAXFEV = {"coarse": 160, "fit": 220, "final": 140, "twin": 120}
ROI_FIT_MARGIN = 0.15             # native_metrics ROI (make_uv_test.py): matte bbox + 15 % of its width + 8 px
ROI_FIT_PAD = 8
ICP_ITERS = 6                     # contour-ICP re-render/re-match rounds after each Powell stage
ICP_NFEV = 25                     # least-squares evaluations per round (projection only, no ray casting)
ICP_TRUNC = 0.04                  # contour pairs farther apart than 4 % of the width are ignored
_BAD = 3.0


# ----------------------------------------------------------------------------- matte levels
class Level:
    """The photo matte on a strided grid: soft coverage target, its boundary and distance field."""

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
        self.required_hull: np.ndarray | None = None   # required geometry's vertices, for bbox alignment only

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


def _describe(a: np.ndarray) -> str:
    return (f"shape={a.shape} dtype={a.dtype} C={a.flags['C_CONTIGUOUS']} writeable={a.flags['WRITEABLE']} "
            f"true={int(np.count_nonzero(a))}")


def _edge(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    try:
        er = cv2.erode(m, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
    except cv2.error as e:      # an unexplained cv2.error was seen once under load (glasses3d/plan/archive/20261019/PORT_REVIEW.md §9, open): keep evidence
        raise RuntimeError(f"cv2.erode failed in cameras._edge on {_describe(m)}: {e}") from e
    return mask & (er == 0)


def _dist_to(target: np.ndarray) -> np.ndarray:
    """Euclidean distance (grid px) to the nearest True pixel of ``target`` (large if none)."""
    if not target.any():
        return np.full(target.shape, 1e6, np.float32)
    src = (~target).astype(np.uint8)
    try:
        return cv2.distanceTransform(src, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    except cv2.error as e:      # see _edge
        raise RuntimeError(f"cv2.distanceTransform failed in cameras._dist_to on {_describe(src)}: {e}") from e


# ----------------------------------------------------------------------------- parametrisation
def _pivot_terms(cam: Camera, pivot: np.ndarray | None) -> tuple[np.ndarray, float]:
    """(image offset of the pivot at unit scale and zero centre, perspective depth factor at the pivot)."""
    if pivot is None:
        return np.zeros(2), 1.0
    P = np.asarray(pivot, float).reshape(1, 3)
    unit = replace(cam, scale=1.0, center_x=0.0, center_y=0.0)
    q = raster._project_norm(P, unit)
    depth = 1.0 - cam.perspective * float(P[0] @ raster.camera_basis(unit)[2])
    if q is None or depth <= 1e-6:
        return np.zeros(2), 1.0
    return q[0], depth


def pack(cam: Camera, unit_px: float, pivot: np.ndarray | None = None, *, include_eye: bool = False) -> np.ndarray:
    """Optimizer vector: yaw, pitch, roll (deg), perspective x10, 100 log(magnification AT THE PIVOT)
    (1 = 1 %), and the image position of the PIVOT (a normalized model point) in % of the matte width.

    Rotating, zooming or changing perspective then keeps the pivot's image position and magnification
    still, which decouples the parameters (a degree of yaw otherwise moves the front piece ~3 px and a
    perspective change rescales it); Powell's coordinate line searches need that on thin silhouettes.
    """
    q, depth = _pivot_terms(cam, pivot)
    x = [cam.yaw, cam.pitch, cam.roll, 10.0 * cam.perspective, 100.0 * math.log(cam.scale / depth),
         100.0 * (cam.center_x + cam.scale * q[0]) / unit_px,
         100.0 * (cam.center_y + cam.scale * q[1]) / unit_px]
    if include_eye or cam.eye_x or cam.eye_y:
        x += [cam.eye_x, cam.eye_y]
    return np.array(x)


def unpack(x: np.ndarray, unit_px: float, pivot: np.ndarray | None = None) -> Camera:
    if len(x) not in (7, 9):
        raise ValueError("camera optimizer vector must have 7 or 9 parameters")
    eye_x, eye_y = (float(x[7]), float(x[8])) if len(x) == 9 else (0.0, 0.0)
    ang = Camera(float(x[0]), float(x[1]), float(x[2]), float(x[3]) / 10.0, 1.0, 0.0, 0.0, eye_x, eye_y)
    q, depth = _pivot_terms(ang, pivot)
    s = float(math.exp(x[4] / 100.0)) * depth
    return Camera(ang.yaw, ang.pitch, ang.roll, ang.perspective, s,
                  float(x[5] * unit_px / 100.0 - s * q[0]), float(x[6] * unit_px / 100.0 - s * q[1]), eye_x, eye_y)


def align(scene: raster.RasterScene, lv: Level, yaw: float, pitch: float, roll: float,
          perspective: float = SEED_PERSPECTIVE) -> Camera:
    """Align the full bbox, or required bbox when it better explains the photo at the same angles.

    Optional lenses may be visible or clear. Keep both physical support hypotheses, scored with the same loss;
    the full candidate wins ties. The angular priors and number of refined starts remain unchanged.
    """
    def aligned(hull):
        pts = project(hull, Camera(yaw, pitch, roll, perspective, 1.0, 0.0, 0.0))
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        x0, y0, x1, y1 = lv.bbox
        scale = (x1 - x0) / max(hi[0] - lo[0], 1e-9)
        c = np.array([(x0 + x1 - 1) / 2.0, (y0 + y1 - 1) / 2.0]) - (lo + hi) / 2.0 * scale
        return Camera(float(yaw), float(pitch), float(roll), float(perspective), float(scale), float(c[0]), float(c[1]))

    full = aligned(scene.hull)
    if lv.required_hull is None:
        return full
    required = aligned(lv.required_hull)
    return required if lv.loss(scene, required) < lv.loss(scene, full) else full


def yaw_window(view: str, yaw: float) -> tuple[float, float]:
    if view == "angled":
        return ANGLED_WINDOW if yaw >= 0 else (-ANGLED_WINDOW[1], -ANGLED_WINDOW[0])
    if view in REAR_THREEQUARTER_VIEWS:
        return (REAR_THREEQUARTER_WINDOW if view.endswith("_left") else
                (-REAR_THREEQUARTER_WINDOW[1], -REAR_THREEQUARTER_WINDOW[0]))
    y0 = PRIOR_YAW[view]
    return (y0 - YAW_HALF_WINDOW, y0 + YAW_HALF_WINDOW)


def pitch_window(view: str) -> tuple[float, float]:
    """The pitch range a view may take: ``PITCH_WINDOWS`` (elevated side views: the label fixes the sign), else
    ``PITCH_BOUNDS``."""
    return PITCH_WINDOWS.get(view, PITCH_BOUNDS)


def eye_side_refusal(view: str, cam: Camera, pivot_norm=None) -> str | None:
    """Explicit lateral translation cannot move the eye to the wrong side of a named view."""
    sign = 1 if view in TOP_VIEWS + TOP_OBLIQUE_VIEWS else -1 if view in BOTTOM_VIEWS else None
    if sign is not None and cam.pitch * sign <= 0:
        return f"pitch {cam.pitch:.2f} contradicts view {view!r}"
    if not (cam.eye_x or cam.eye_y) or cam.perspective == 0:
        return None
    right, up, toward = raster.camera_basis(cam)
    eye = toward/cam.perspective + cam.eye_x*right + cam.eye_y*up
    eye -= np.zeros(3) if pivot_norm is None else np.asarray(pivot_norm, float)
    rules = {"front": ((2,1),), "back": ((2,-1),), "left": ((0,1),), "right": ((0,-1),),
             "angled": ((2,1),), "top": ((1,1),), "topb": ((1,1),),
             "bottom": ((1,-1),), "bottomb": ((1,-1),),
             "top_oblique_left": ((0,1),(1,1)), "top_oblique_right": ((0,-1),(1,1)),
             "rear_threequarter_left": ((0,1),(2,-1)), "rear_threequarter_right": ((0,-1),(2,-1))}
    if any(eye[axis]*sign <= 0 for axis,sign in rules[view]):
        return f"off-axis eye position {eye.tolist()} relative to the front pivot contradicts view {view!r}"
    return None


def roll_window(view: str, roll: float) -> tuple[float, float]:
    """Top/bottom use overlapping 45-degree charts; other views retain the original +/-25-degree prior.

    A chart's unwrapped angles avoid a discontinuity at +/-180. Near zero its bounds are bit-for-bit the
    original ones, including the least-squares trust region when those bounds are inactive.
    """
    center = AXIAL_ROLL_STEP * round(roll / AXIAL_ROLL_STEP) if view in AXIAL_VIEWS else 0.0
    return (center + ROLL_BOUNDS[0], center + ROLL_BOUNDS[1])


def _bounds(center: Camera, unit_px: float, yaw_win: tuple[float, float], *, local: dict | None = None,
            pivot: np.ndarray | None = None, pitch_win: tuple[float, float] | None = None,
            roll_win: tuple[float, float] | None = None, eye_limit: float | None = None):
    pw = PITCH_BOUNDS if pitch_win is None else pitch_win
    rw = ROLL_BOUNDS if roll_win is None else roll_win
    x = pack(center, unit_px, pivot)
    eyes = [] if eye_limit is None else [(-eye_limit, eye_limit)] * 2
    if len(x) == 9 and eye_limit is None:
        raise ValueError("an off-axis optimization requires an explicit lateral eye domain")
    if local is None:       # global windows (coarse / fit levels)
        return [yaw_win, pw, rw, (10 * PERSPECTIVE_BOUNDS[0], 10 * PERSPECTIVE_BOUNDS[1]),
                (x[4] - 35.0, x[4] + 30.0), (x[5] - 15.0, x[5] + 15.0), (x[6] - 15.0, x[6] + 15.0)] + eyes
    d = local
    return [(max(yaw_win[0], x[0] - d["yaw"]), min(yaw_win[1], x[0] + d["yaw"])),
            (max(pw[0], x[1] - d["pitch"]), min(pw[1], x[1] + d["pitch"])),
            (max(rw[0], x[2] - d["roll"]), min(rw[1], x[2] + d["roll"])),
            (max(10 * PERSPECTIVE_BOUNDS[0], x[3] - d.get("persp", 10.0)),
             min(10 * PERSPECTIVE_BOUNDS[1], x[3] + d.get("persp", 10.0))),
            (x[4] - d["scale"], x[4] + d["scale"]), (x[5] - d["shift"], x[5] + d["shift"]),
            (x[6] - d["shift"], x[6] + d["shift"])] + eyes


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

    x0 = _clip_into(pack(cam, unit_px, pivot, include_eye=len(bounds) == 9), bounds)
    optimize.minimize(f, x0, method="Powell", bounds=bounds,
                      options={"maxfev": maxfev, "xtol": xtol, "ftol": 1e-4})
    return best[1], float(best[0]), n[0]


# ----------------------------------------------------------------------------- contour ICP
def contour_icp(scene: raster.RasterScene, lv: Level, cam: Camera, unit_px: float, pivot: np.ndarray | None,
                yaw_win: tuple[float, float], iters: int | None = None,
                pitch_win: tuple[float, float] | None = None,
                roll_win: tuple[float, float] | None = None, eye_limit: float | None = None) -> Camera | None:
    """Silhouette-contour ICP: match rendered contour samples (with their 3D ray hits) to the nearest photo
    contour pixel and vice versa, then a robust least-squares step on the camera; repeat.

    Gradient information lets it walk the narrow coupled valleys (yaw/pitch/perspective/scale) that
    coordinate-wise Powell line searches stall in on thin silhouettes. Returns None if it cannot run.
    The pitch bounds (``pitch_win``, default ``PITCH_BOUNDS``) shape the least-squares trust region even when inactive.
    """
    iters = ICP_ITERS if iters is None else iters
    pw = PITCH_BOUNDS if pitch_win is None else pitch_win
    rw = ROLL_BOUNDS if roll_win is None else roll_win
    us, vs = raster.pixel_grid(lv.shape, lv.stride, lv.roi)
    if not lv.edge.any():
        return None
    pi, pj = np.nonzero(lv.edge)
    photo_uv = np.column_stack([us[pj], vs[pi]])
    _, pidx = ndimage.distance_transform_edt(~lv.edge, return_indices=True)
    trunc = max(ICP_TRUNC * lv.width * lv.stride, 3.0 * lv.stride)      # native px
    x = pack(cam, unit_px, pivot, include_eye=eye_limit is not None)
    lo = np.array([yaw_win[0], pw[0], rw[0], 10 * PERSPECTIVE_BOUNDS[0], -np.inf, -np.inf, -np.inf])
    hi = np.array([yaw_win[1], pw[1], rw[1], 10 * PERSPECTIVE_BOUNDS[1], np.inf, np.inf, np.inf])
    if len(x) == 9:
        if eye_limit is None:
            raise ValueError("off-axis ICP requires an explicit lateral eye domain")
        lo, hi = np.r_[lo, -eye_limit, -eye_limit], np.r_[hi, eye_limit, eye_limit]
    x = np.clip(x, lo + 1e-9, hi - 1e-9)
    for _ in range(iters):
        c = unpack(x, unit_px, pivot)
        try:
            r = scene.render(c, lv.shape, lv.stride, lv.roi, want_points=True)
        except ValueError:
            return None
        points = r["points"]
        if lv.ignore_faces is not None:
            fid = r["face_id"]
            optional = np.zeros(fid.shape, bool)
            optional[fid >= 0] = lv.ignore_faces[fid[fid >= 0]]
            # The effective mask borrows photo foreground inside optional lenses. Its internal edges are
            # observations, not geometric contours at the lens ray hit, and cannot supply ICP derivatives.
            # Actual rendered silhouette edges remain geometric evidence, including an observed opaque lens.
            points = points.copy()
            points[optional & ~_edge(r["mask"])] = np.nan
        e = _edge(lv.effective(r["mask"], lv.ignored(r)))
        if e.sum() < 8:
            return None
        ri, rj = np.nonzero(e)
        X1 = points[ri, rj]
        t1 = np.column_stack([us[pidx[1][ri, rj]], vs[pidx[0][ri, rj]]])
        _, ridx = ndimage.distance_transform_edt(~e, return_indices=True)
        qi, qj = ridx[0][pi, pj], ridx[1][pi, pj]
        X2 = points[qi, qj]
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


def _optional_faces(scene: raster.RasterScene, faces: np.ndarray | None) -> np.ndarray | None:
    if faces is None:
        return None
    mask = np.asarray(faces)
    if mask.dtype != np.bool_ or mask.shape != (scene.n_faces,):
        raise ValueError(f"ignore_faces must be a boolean mask of shape ({scene.n_faces},)")
    return mask


class ViewFit:
    """Multi-start camera search for one photo. ``pivot`` (normalized model point) is held still by the
    rotation/zoom parameters (the front-piece centre; ``fitmesh.FitMesh.front_pivot``). ``view`` selects the yaw window
    (``yaw_window``), pitch/roll windows and whether the side-view pitch twin is carried."""

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
        self.eye_limit: float | None = None  # explicit continuation; legacy search stays seven-parameter

    def set_ignore(self, faces: np.ndarray | None) -> None:
        faces = _optional_faces(self.scene, faces)
        if faces is not None:
            faces = faces.copy()  # keep one stable policy across all levels even if the caller edits its array
            faces.setflags(write=False)
        self.ignore_faces = faces
        required_hull = None
        if faces is not None and faces.any() and not faces.all():
            # Only vertices referenced by required faces: unused lens vertices must not move their bbox.
            required_hull = self.scene.V_norm[np.unique(self.scene.F[~faces])]
            required_hull.setflags(write=False)
        for lv in self.levels.values():
            lv.ignore_faces = faces
            lv.required_hull = required_hull

    def powell(self, lv: Level, cam: Camera, maxfev: int, *, local: dict | None = None,
               yaw_win: tuple[float, float] | None = None, xtol: float = 0.05,
               pitch_win: tuple[float, float] | None = None) -> tuple[Camera, float]:
        """``pitch_win`` overrides the view's ``pitch_window`` (``pitch_twin`` searches the opposite sign)."""
        win = yaw_win if yaw_win is not None else yaw_window(self.view, cam.yaw)
        pw = pitch_window(self.view) if pitch_win is None else pitch_win
        b = _bounds(cam, self.unit, win, local=local, pivot=self.pivot, pitch_win=pw,
                    roll_win=roll_window(self.view, cam.roll), eye_limit=self.eye_limit)
        c2, l2, n = powell(self.scene, lv, cam, b, maxfev, self.unit, xtol=xtol, pivot=self.pivot)
        self.evals += n
        return c2, l2

    def staged(self, lv: Level, cam: Camera, stages, maxfev: int,
               pitch_win: tuple[float, float] | None = None) -> tuple[Camera, float]:
        """Successive Powell runs with shrinking bounds (fresh directions each time), each followed by a
        contour-ICP step that is kept only when it lowers the same level loss."""
        pw = pitch_window(self.view) if pitch_win is None else pitch_win
        loss = None
        for st in stages:
            cam, loss = self.powell(lv, cam, maxfev, local=st, xtol=0.05 if lv.name == "coarse" else 0.02,
                                    pitch_win=pw)
            c2 = contour_icp(self.scene, lv, cam, self.unit, self.pivot, yaw_window(self.view, cam.yaw),
                             pitch_win=pw, roll_win=roll_window(self.view, cam.roll), eye_limit=self.eye_limit)
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
                # distinct pitches first (the side-view pitch twin, camera height); yaw too for YAW_DISTINCT_VIEWS
                key = (((round(cam.yaw / 5.0),) if self.view in YAW_DISTINCT_VIEWS else ())
                       + (round(cam.pitch / 10.0), round(cam.roll / 5.0)))
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

    def refine(self) -> dict:
        """Fit level from the best distinct coarse winners, then the final level from the best."""
        order = sorted(self.coarse, key=lambda t: (t[0], t[2]))
        cands, keys = [], set()
        for loss, cam, origin in order:
            key = (cam.yaw >= 0, round(cam.pitch / 15.0))
            if self.view in AXIAL_VIEWS:
                key += (round(cam.roll / AXIAL_ROLL_STEP) % len(AXIAL_ROLL_SEEDS),)
            if key in keys or loss > FIT_SECOND_WITHIN * order[0][0]:
                continue
            keys.add(key)
            cands.append((loss, cam, origin))
            if len(cands) >= N_FIT_POWELL:
                break
        if self.view in SIDE_VIEWS and abs(cands[0][1].pitch) >= PITCH_TWIN_MIN_DEG:
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
        lvF.required_hull = lv.required_hull
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
        comparable on a level built for both. Use it to hold a refit against a previous camera (in the old modeler, on
        test-pilot-002 the parent back camera scored 0.2848 there against the refit's 0.2905, but had never been
        evaluated unchanged: its perspective was re-seeded at ``SEED_PERSPECTIVE``)."""
        self.evals += len(cams)
        return common_final_losses(self.fg, self.scene, cams, self.ignore_faces)

    def refine_off_axis(self, legacy: dict) -> dict:
        """One explicit continuation after the unchanged seven-parameter search.

        Lateral eye coordinates lie within the model's enclosing sphere radius (normalized extents): a
        camera whose optical axis intersects that sphere has both coordinates inside this domain. This is
        a declared search domain, not a claim of measured calibration accuracy. Both candidates survive.
        """
        self.eye_limit = float(self.scene.max_radius)
        start = legacy["camera"]
        candidate, _ = self.staged(self.levels["fit"], start, STAGES_FIT, MAXFEV["fit"])
        candidate, _ = self.staged(self.levels["final"], candidate, STAGES_FINAL, MAXFEV["final"])
        losses = self.final_losses([start, candidate])
        refusal = eye_side_refusal(self.view, candidate, self.pivot)
        selected = 1 if refusal is None and losses[1] < losses[0] else 0
        winner = (start, candidate)[selected]
        return {**legacy, "camera": winner, "loss_final_level": self.levels["final"].loss(self.scene, winner),
                "off_axis": {"mode": "legacy_search_then_off_axis_continuation", "eye_limit": self.eye_limit,
                             "domain": "each pre-roll lateral eye coordinate bounded by normalized model sphere radius",
                             "selected": "off_axis" if selected else "legacy",
                             "legacy_camera": start.to_dict(), "candidate_camera": candidate.to_dict(),
                             "common_losses": losses,
                             "candidate_refusal": refusal,
                             "candidate_at_eye_bound": any(abs(v) >= self.eye_limit * (1-1e-5)
                                                           for v in (candidate.eye_x, candidate.eye_y))}}

    def pitch_twin(self, cam: Camera) -> dict:
        """The sign margin of ``cam`` (re-port of bsa ``pitch_twin``): refit at the fit level from (yaw, -pitch, roll)
        with the pitch held on the OPPOSITE sign (``opposite_pitch_window``; bsa let the twin drift back), and compare
        with ``cam``'s own fit-level loss. margin = twin loss - own loss: positive = the winner's sign fits better;
        below ``PITCH_TWIN_TIE`` the sign is ambiguous; negative = the opposite sign fits better (the search missed it).
        Skipped (``{"skipped": ...}``) when |pitch| < ``PITCH_TWIN_MIN_DEG`` (the twin is the same camera). Needs
        ``refine()`` first (the fit level)."""
        if abs(cam.pitch) < PITCH_TWIN_MIN_DEG:
            return {"skipped": f"|pitch| {abs(cam.pitch):.2f} < {PITCH_TWIN_MIN_DEG} deg: the twin is the same camera"}
        lv = self.levels["fit"]
        own = lv.loss(self.scene, cam)
        tw = opposite_pitch_window(self.view, cam.pitch)
        start = align(self.scene, lv, cam.yaw, float(np.clip(-cam.pitch, tw[0], tw[1])), cam.roll, cam.perspective)
        if self.eye_limit is not None:
            start = replace(start, eye_x=cam.eye_x, eye_y=cam.eye_y)
        self.evals += 2
        c2, l2 = self.staged(lv, start, STAGES_FIT, MAXFEV["twin"], pitch_win=tw)
        margin = l2 - own
        flag = ("pitch_sign_ambiguous" if abs(margin) < PITCH_TWIN_TIE else
                "pitch_twin_better" if margin < 0 else None)
        extra = {}
        if self.eye_limit is not None:
            def eye(cam):
                if cam.perspective == 0:
                    return None
                right, up, toward = raster.camera_basis(cam)
                pivot = np.zeros(3) if self.pivot is None else self.pivot
                return (toward / cam.perspective + cam.eye_x*right + cam.eye_y*up - pivot).tolist()
            extra = {"own_camera_full": cam.to_dict(), "twin_camera_full": c2.to_dict(),
                     "own_eye_relative_pivot_norm": eye(cam), "twin_eye_relative_pivot_norm": eye(c2),
                     "interpretation": "Optical-pitch evidence; opposite optical pitch does not guarantee "
                                       "opposite physical eye height with lateral eye offsets."}
        return {**extra, "twin_camera": [round(c2.yaw, 3), round(c2.pitch, 3), round(c2.roll, 3), round(c2.perspective, 4)],
                "twin_pitch_window": list(tw), "twin_loss": float(l2), "own_loss": float(own),
                "margin": float(margin), "flag": flag}


def opposite_pitch_window(view: str, pitch: float) -> tuple[float, float]:
    """The pitch range of the opposite sign to ``pitch`` within the view's window, mirrored when the window is
    one-signed: +/-95 and pitch +60 -> (-95, 0); (5, 85) and pitch +19 -> (-85, -5)."""
    lo, hi = pitch_window(view)
    if pitch >= 0:
        return (-hi, -lo) if lo > 0 else (lo, 0.0)
    return (-hi, -lo) if hi < 0 else (0.0, hi)


def common_final_losses(fg: np.ndarray, scene: raster.RasterScene, cams: list[Camera],
                        ignore_faces: np.ndarray | None = None) -> list[float]:
    """Final-level loss of each camera on ONE level built for all of them (``ViewFit.final_losses``)."""
    boxes = []
    for c in cams:
        pts = raster._project_norm(scene.hull, c)
        if pts is not None:
            boxes.append((*pts.min(axis=0), *pts.max(axis=0)))
    inc = None
    if boxes:
        b = np.asarray(boxes, float)
        inc = (float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 2].max()), float(b[:, 3].max()))
    lv = Level(fg, FINAL_W, "final_common", margin=FINAL_ROI_MARGIN, include_xyxy=inc)
    lv.ignore_faces = _optional_faces(scene, ignore_faces)
    return [float(lv.loss(scene, c)) for c in cams]


def prior_seeds(view: str) -> list[tuple[float, float, float, str]]:
    """(yaw, pitch, roll, origin) seeds of a view's prior. front/back/left/right/angled are bsa's grids unchanged;
    ``top``/``topb`` are make_uv_test.py's explicit reference-04 grid (yaw offsets outer, pitches inner, same order;
    ``prior_seeds(v)[:len(TOP_YAW_OFFSETS) * len(TOP_PITCHES)]`` is exactly that grid) followed by the
    ``TOP_LOW_PITCHES`` band. That zero-roll prefix is followed by uniform roll charts around the complete circle.
    Bottom views mirror the pitch grid; ``top_oblique_*`` retain the yaw offsets x ``TOP_OBLIQUE_PITCHES``."""
    if view not in PRIOR_YAW and view != "angled":
        raise ValueError(f"unknown view {view!r}; known: {', '.join(VIEWS)}")
    if view in AXIAL_VIEWS:
        sign = -1.0 if view in BOTTOM_VIEWS else 1.0
        return [(PRIOR_YAW[view] + d, sign * p, roll, "prior") for roll in AXIAL_ROLL_SEEDS
                for pitches in (TOP_PITCHES, TOP_LOW_PITCHES) for d in TOP_YAW_OFFSETS for p in pitches]
    if view in REAR_THREEQUARTER_VIEWS:
        sign = 1.0 if view.endswith("_left") else -1.0
        return [(sign * (180.0 - y), p, 0.0, "prior") for y in ANGLED_YAWS for p in PITCHES]
    yaws = ([PRIOR_YAW[view] + d for d in PRIOR_YAW_OFFSETS] if view != "angled"
            else [s * y for s in (1.0, -1.0) for y in ANGLED_YAWS])
    pitches = TOP_OBLIQUE_PITCHES if view.startswith("top_oblique") else PITCHES
    return [(y, p, 0.0, "prior") for y in yaws for p in pitches]


def cross_seeds(target: str, winners: dict[str, Camera]) -> list[tuple[float, float, float, str]]:
    """Rotation seeds for ``target`` from other views' winners. ``NOT_CROSS_SOURCES`` are never sources: the angled
    view (held out), the top/bottom views (near pitch +/-90 their yaw offset from the prior is not a rotation of the
    photographer's turntable), the elevated side views (their pitch is a deliberately different camera height), and
    the rear three-quarter views (their yaw is oblique, not a measured turntable offset). Seeds whose pitch lies outside the target's
    ``pitch_window`` are dropped (bsa had no per-view pitch window). ``fit_views`` uses it for the angled view only,
    as bsa ``fit_all`` did (the angled view only receives)."""
    out = []
    t_priors = [PRIOR_YAW[target]] if target != "angled" else [40.0, -40.0]
    pw = pitch_window(target)
    for src, cam in winners.items():
        if src == target or src in NOT_CROSS_SOURCES:
            continue
        dy = cam.yaw - PRIOR_YAW[src]
        for tp in t_priors:
            out.append((tp + dy, cam.pitch, cam.roll, f"cross:{src}:rotated"))
            out.append((tp - dy, cam.pitch, -cam.roll, f"cross:{src}:mirrored"))
            out.append((tp + dy, -cam.pitch, cam.roll, f"cross:{src}:pitch_twin"))
    return [s for s in out if pw[0] <= s[1] <= pw[1]]


def _wrap_yaw(yaw: float, view: str) -> float:
    """Express a yaw inside the view's window convention (back/topb/bottomb around 180, others around 0)."""
    if view != "angled" and PRIOR_YAW[view] == 180.0:
        return yaw % 360.0
    return (yaw + 180.0) % 360.0 - 180.0


def wrap_seeds(view: str, seeds: list[tuple[float, float, float, str]]) -> list[tuple[float, float, float, str]]:
    """Seeds re-expressed in the view's yaw convention and kept only inside its yaw window (bsa fit_all's filter)."""
    out = [(_wrap_yaw(y, view), p, r, o) for y, p, r, o in seeds]
    return [s for s in out if yaw_window(view, s[0])[0] <= s[0] <= yaw_window(view, s[0])[1]]


# ----------------------------------------------------------------------------- measurement
def native_metrics(scene: raster.RasterScene, cam: Camera, fg: np.ndarray, roi,
                   ignore_faces: np.ndarray | None = None) -> dict:
    """Stride-1 IoU and symmetric contour distance (native px) inside ``roi``; mask for the sheet.
    With ``ignore_faces`` also the IoU with the pixels whose first hit is such a face removed."""
    ignore_faces = _optional_faces(scene, ignore_faces)
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
        effective = hard | (m & W & ref)
        emean, ep95 = contour_stats(effective, ref)
        extra = {"iou_lens_optional": np.count_nonzero(m & ref) / max(np.count_nonzero(hard | ref), 1),
                 "lens_pixels_optional": int(W.sum()), "contour_mean_px_lens_optional": emean,
                 "contour_p95_px_lens_optional": ep95,
                 "render_only_px_lens_optional": int(np.count_nonzero(hard & ~ref))}
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


def resized_camera(cam: Camera, k: float, x0: float = 0.0, y0: float = 0.0) -> Camera:
    """The camera of the crop [x0:, y0:] of a native image resized by ``k`` (pixel centres kept exact)."""
    return replace(cam, scale=cam.scale*k, center_x=(cam.center_x-x0+0.5)*k-0.5,
                   center_y=(cam.center_y-y0+0.5)*k-0.5)


# ----------------------------------------------------------------------------- one view, end to end (make_uv_test.py)
def matte_roi(fg: np.ndarray, margin: float = ROI_FIT_MARGIN, pad: int = ROI_FIT_PAD) -> tuple[int, int, int, int]:
    """The metrics ROI of make_uv_test.py: the matte bbox grown by ``margin`` x its width + ``pad`` px, clipped."""
    ys, xs = np.nonzero(fg)
    if len(xs) == 0:
        raise ValueError("empty matte")
    w = xs.max() - xs.min() + 1
    m = int(margin * w) + pad
    return (int(max(0, xs.min() - m)), int(max(0, ys.min() - m)),
            int(min(fg.shape[1], xs.max() + m + 1)), int(min(fg.shape[0], ys.max() + m + 1)))


def fit_view(view: str, fg: np.ndarray, scene: raster.RasterScene, pivot: np.ndarray | None,
             seeds: list[tuple[float, float, float, str]] | None = None, n_refine: int = N_COARSE_POWELL, *,
             cross: list[tuple[float, float, float, str]] | None = None, sign_margin: bool = True,
             ignore_faces: np.ndarray | None = None, off_axis: bool = False) -> dict:
    """make_uv_test.py's ``fit``: ViewFit(view, fg, scene, pivot) -> run_starts(seeds or prior_seeds(view), n_refine)
    -> [run_starts(wrap_seeds(view, cross), N_CROSS_POWELL)] -> refine() -> native_metrics on ``matte_roi(fg)``
    [-> pitch_twin, the sign margin].

    ``n_refine`` defaults to bsa's ``N_COARSE_POWELL`` (3 starts per view, per yaw sign for 'angled');
    make_uv_test.py used 2, so reproducing its cameras needs ``n_refine=2`` (tests/test_cameras_repro.py).
    ``cross`` = ``cross_seeds`` from other views' winners (bsa fit_all's round 2). Returns {view, camera, loss (final
    level), metrics (without the mask/face_id images), sign (``ViewFit.pitch_twin``, or None), refine, fit}.
    ``ignore_faces`` makes only those rendered faces optional throughout the search, including initial seeds and
    sign-margin fits. Native raw metrics stay unchanged; separate ``*_lens_optional`` fields report effective fit."""
    vf = ViewFit(view, fg, scene, pivot=pivot)
    vf.set_ignore(ignore_faces)
    vf.run_starts(prior_seeds(view) if seeds is None else seeds, n_refine)
    if cross:
        cs = wrap_seeds(view, cross)
        if cs:
            vf.run_starts(cs, N_CROSS_POWELL)
    res = vf.refine()
    if off_axis:
        res = vf.refine_off_axis(res)
    if view in AXIAL_VIEWS and not -180.0 <= res["camera"].roll < 180.0:
        res["camera"] = replace(res["camera"], roll=(res["camera"].roll + 180.0) % 360.0 - 180.0)
    met = native_metrics(scene, res["camera"], fg, matte_roi(fg), vf.ignore_faces)
    met = {k: v for k, v in met.items() if k not in ("mask", "face_id")}
    sign = vf.pitch_twin(res["camera"]) if sign_margin else None
    return {"view": view, "camera": res["camera"], "loss": float(res["loss_final_level"]), "metrics": met,
            "sign": sign, "refine": res, "fit": vf}


def fit_best(fg: np.ndarray, scene: raster.RasterScene, pivot: np.ndarray | None,
             candidates: list[tuple[str, list | None]], n_refine: int = N_COARSE_POWELL, *,
             cross: dict[str, list] | None = None, sign_margin: bool = True,
             ignore_faces: np.ndarray | None = None, off_axis: bool = False) -> tuple[dict, list[dict]]:
    """Fit each (view, seeds-or-None) candidate with ``fit_view``, then re-score every candidate's camera on ONE
    common final level (``common_final_losses``) and keep the lowest (strict <, so the first wins a tie).

    make_uv_test.py chose 'right'/'left' and 'top'/'topb' by each candidate's own final-level loss, but those levels
    have different ROIs (each built around its own fit), so they are not comparable. Each result carries both:
    ``loss`` (own level) and ``common_loss``. ``cross`` maps a view to its cross seeds."""
    ignore_faces = _optional_faces(scene, ignore_faces)
    results = [fit_view(view, fg, scene, pivot, seeds, n_refine, cross=(cross or {}).get(view),
                        sign_margin=sign_margin and not off_axis, ignore_faces=ignore_faces,
                        **({"off_axis": True} if off_axis else {})) for view, seeds in candidates]
    if off_axis:
        # Every legacy and continuation candidate contributes to ONE final ROI/grid. A pairwise
        # ordering can reverse when another view changes that grid's origin, so never prune early.
        alternatives = [Camera.from_dict(r["refine"]["off_axis"][key]) for r in results
                        for key in ("legacy_camera", "candidate_camera")]
        all_losses = common_final_losses(fg, scene, alternatives, ignore_faces)
        for i, r in enumerate(results):
            vf = r["fit"]
            evidence = r["refine"]["off_axis"]
            pair = alternatives[2*i:2*i+2]
            losses = all_losses[2*i:2*i+2]
            selected = int(evidence["candidate_refusal"] is None and losses[1] < losses[0])
            winner = pair[selected]
            if r["view"] in AXIAL_VIEWS and not -180.0 <= winner.roll < 180.0:
                winner = replace(winner, roll=(winner.roll + 180.0) % 360.0 - 180.0)
            evidence.update(local_common_losses=evidence["common_losses"],
                            local_selected=evidence["selected"], common_losses=losses,
                            selected="off_axis" if selected else "legacy",
                            objective_scope="one final union ROI/grid for all views and both camera families")
            r["camera"] = r["refine"]["camera"] = winner
            r["loss"] = r["refine"]["loss_final_level"] = float(vf.levels["final"].loss(scene, winner))
            r["common_loss"] = losses[selected]
            met = native_metrics(scene, winner, fg, matte_roi(fg), vf.ignore_faces)
            r["metrics"] = {k: v for k, v in met.items() if k not in ("mask", "face_id")}
            r["sign"] = vf.pitch_twin(winner) if sign_margin else None
        return min(results, key=lambda r: r["common_loss"]), results
    common = common_final_losses(fg, scene, [r["camera"] for r in results], ignore_faces)
    best = None
    for r, c in zip(results, common):
        r["common_loss"] = c
        if best is None or c < best["common_loss"]:
            best = r
    return best, results
