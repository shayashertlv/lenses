"""Readings (plan/WP6_V3.md §4): a named chart point (d, y, X) read from pixel picks in 2..PHOTOS_MAX photos with
cameras, with its error and the facts that limit it. Host only (numpy, scipy, PIL); no Blender, no render.

Why (data/wp6v2/review_20261012/FINDINGS.md): crisp two-photo corner readings were good to 0.2-0.5 mm, yet all six v2
runs put the crown's tip at d 85-96.5 where the photos end it at d 75.5-77.2: a tip is where two edges cross, Astra
followed one edge to the silhouette, and a pick that slides along the epipolar line between two photos is invisible to
their residual. Back corners also carry a camera theta term (~1.3 mm of X per degree at d 75-85). So a reading is either
a corner pick or the crossing of two traced edges, its error has a pick part and a camera part, and the text says where
the residual cannot see a slide, for both kinds (review 2026-10-12, v3/v3-readings-fix.md).

Model (= v2's lab.corner_reading's, now the reference in tests/agent/test_temple_readings.py): the chart point p =
(d, y, X) of the +X temple at opening 0 is placed as the lab builds it (the spec's hinge opening; the -X temple is its
mirror), opened by each photo's temple pose (pose.chart_to_posed) and projected by the photo's camera
(camera.project_mm). Each photo is
on the temple whose outer face it sees at p (both or neither, e.g. a front view: both tried, the smaller cost kept).
That map is projective, so each photo is a 3 x 4 matrix (``camera_matrix``; every solution is checked against
project_mm to SELF_CHECK_PX): a linear (DLT) triangulation starts every side combination, so a reading needs no start
from the model, and least squares with the analytic Jacobian (scipy, method "lm") fits it, weighted by the stacked pixel
covariance below. A start or solution outside the lab's engineering bounds (geometry.D_LIMIT_MM / Y_LIMIT_MM, and
|t| <= T_LIMIT_MM 40 with t = X - x_in(d)) is dropped: the t bound rejects the mirrored-chart ghost. When every photo
sees both temples' outer faces or neither (03, 04, 06, B3), the -X temple's chart point of the same world point has
X ~ -X_true (t ~ -136) and fits the pixels as well (only the two hinge axes' theta tell them apart), so with ordinary
pick noise it often won, with smaller residuals than the true point and no warning: real basis 5 of 567 such readings
over d 1-76 (review p06) and 105 of 640 seeded draws at d 1-3, synthetic rig 18 of 189; with the bound 0, and the same
refusals. Readings depend on the model only through x_in (t, the bound) and the printed model_t.

Kinds (``picks`` = [{photo, uv, edges, sd_px}], one per photo, photos distinct):
    corner    ``uv`` [u, v] native px, ``edges`` null; the pixel's sd is sd_px (null: SD_PX_DEFAULT 1.0) both ways.
    crossing  ``edges`` = two traced edges of 2..EDGE_POINTS_MAX points [[u, v], ...] each (spanning
              >= EDGE_SPAN_MIN_PX), ``uv`` null. Each edge is a total-least-squares line; the pixel is the lines'
              crossing (inside the photo, at >= CROSS_MIN_DEG), its 2x2 covariance from each line's offset and angle
              error: point sd sd_px, or the edge's own rms off its line when larger (>= 3 points), normal error at the
              crossing sd^2 (1/n + s^2 / sum s_k^2) + EDGE_SYS_PX^2 for s its distance along the line from the points'
              centroid. EDGE_SYS_PX 0.7 is the per-edge "which side of a blurred edge" offset shared by every point of
              a trace, so it does not shrink with n (crown-geometry picks.json sys_px 0.5-0.7; without it the tip E's
              d spread was 1.24-1.33x its reported sd under Astra-like traces, review p5). So a shallow crossing is
              weak along its edges (1 / sin(angle) px per px of edge offset) and strong across them, and a crossing
              extrapolated past the traced points is weaker the farther it lies.

Error (record sd_* in mm):
    pick    the stacked pixel covariance: each photo's pick covariance plus its camera error, propagated by the
            weighted least-squares covariance (anisotropic for crossings): sd_X_pick, sd_d_pick, sd_y_pick and the full
            3 x 3 ``cov_pick`` (d, y, X; the tip E's d and X correlate -0.7: a node placed at another d needs it,
            ``conditional_x``). Camera error: a fitted camera's is CAMERA_PX_SD 1.0 px both ways; a transported
            camera (``camera_source``: B1 <- 05, B2 <- 02, B3 <- 04 on the Prada basis) is its source's error mapped
            by the transport (M = J_twin J_source^+, the image similarity: B1 is 05 scaled 1.124) plus its own
            ``transport_px`` (the contour residual its record names, 0.82 / 1.09 px, else TRANSPORT_PX_DEFAULT 1.0),
            so photos of one fit share M_i M_j^T CAMERA_PX_SD^2.
    camera  each camera fit's temple theta (both sides) moved by +-THETA_SD_DEG 0.3 deg, the point re-solved, half the
            change taken (``camera_shift`` {fit: [dd, dy, dX] for +0.3 deg}); the fits added in quadrature (a twin's
            theta is copied from its source, so they move together): sd_X_camera, sd_d_camera, sd_y_camera. It is one
            rotation of the whole temple about the hinge axis (chart d = hinge z - axis z = -0.5 on Prada), the same
            for every reading of that fit and nearly linear in d, so a plane stays a plane: a fit testing shape uses
            the pick part per reading and one theta per camera fit (``camera_shift``), never sd_X as independent noise.
    sd_X, sd_d, sd_y = the two in quadrature (absolute comparisons and the text).

Refusals (ValueError; the lab returns them as E_ARGS): a bad name, kind or pick; fewer than 2 or more than PHOTOS_MAX
photos, a repeated photo or one without a camera; a pixel or edge point outside the photo; parallel edges or a crossing
outside the photo; no point in front of the cameras that fits the pixels within the engineering bounds; and a pixel
Jacobian (px per mm of d, y, X, unweighted) whose condition number exceeds COND_MAX 20 (the photos see the point along
nearly one line: = v2's lab.CORNER_COND_MAX, the test's REF_COND_MAX; run-6 cameras B2+B1 1.9, 05+01 ~940).

Directions and the slide line: photos whose lines of sight at the point lie within SAME_DIR_DEG 2 of each other are one
direction (a twin and its source exactly: B1 and 05 see one line). ``lines`` gives each photo's epipolar line to every
other direction (signed image angle from horizontal, + rising to the right). A reading with exactly two directions
cannot check where its pixels lie along that line: a slide along it (the same in a direction's photos) moves the point
along the other direction's line of sight with no residual. ``slide`` gives, per photo, that line's angle and the
point's move per px of slide in that photo (mm of d, y, X, for a slide to the right), and the text prints it for both
kinds: a corner pick of a tip is as blind along it as a crossing (the tip E picked as a corner in B1 + B2 and slid 8 /
16 / 24 px back along the top edge read d 76.5 / 77.5 / 78.4 with residuals 1.3 / 1.0 / 0.7 px, review p2). With three
or more directions a slide in one direction shows in the others' residuals.

Warnings (fixed wording, facts only, no remedy; at most one per code, in this order, record "warnings" [{code,
message, photos, items?}]):
    W_ALONG_LINE        a crossing in a two-direction reading with a traced edge within EPIPOLAR_WARN_DEG 10 of its
                        photo's slide line: per direction the edges, their angles to the line and the crossing's slide
                        along it per px of an edge's offset (|e . N^-1 u_k|, the larger edge), with the largest point
                        move that gives; the residual cannot check that each edge is the same feature in both
                        directions. On the Prada crown every near-horizontal edge in B1/05/B2/02 is one (3.6-7.9 deg):
                        K's crossing slides 1.4 px per px (d ~0.17 mm), the tip E's 7.0 (d ~0.8 mm).
    W_SHALLOW_CROSSING  a photo's edges cross at < SHALLOW_DEG 15: along them the crossing moves 1 / sin(angle) px per
                        px of edge offset (the crown tip E in B1 and 05: 8.2 deg, 7.1).
    W_RESIDUAL          the residuals' chi2 in the reading's whole stated pixel error (the stacked covariance above plus
                        each camera fit's theta THETA_SD_DEG as the pixel shift it gives that fit's photos, shared:
                        fits disagree by it), minimised over the point, is above its 1 - RESIDUAL_P (99.7 %, "3 sigma")
                        point for 2n - 3 degrees of freedom: the pixels are not one point within their stated error.
                        A flat 3 px limit fired on 87 % of consistent 4-photo tip crossings (a shallow crossing is
                        ~10 px weak along its edges, review p11/p12); with Astra-like traces of the real crown corners
                        (4 points over 70 px, 1 px, a 0.7 px edge offset, 0.5 px rounding) this fires on <= 0.7 %, and
                        on an 8 px off-feature pick of K unless the offset lies along the slide line. The text prints
                        each photo's residual in px and in sd of its own stated error (res_sd).
(The v3 design's W_ONE_FIT is not raised: every camera of a reading from one fit is refused by the condition number,
and the sharing is in the pick covariance and ``fits``.)

Record (``read_point``): {name, kind, d, y, X, t (X - x_in(d)), sd_X, sd_X_pick, sd_X_camera, sd_d, sd_d_pick,
sd_d_camera, sd_y, sd_y_pick, sd_y_camera, cov_pick (3 x 3 mm^2, d y X, rounded to 1e-6), camera_shift, photos (as
given), rids (basis references), fits (each photo's camera fit, a lab photo id), sides (+1 charted / -1 mirrored), uv
(the pixel used per photo), uv_cov (its 2x2 pick covariance, px^2, before the camera part), uv_fit (the read point's
projection), res_px, res_sd (each photo's residual in sd of its own stated pixel error), chi2 and dof (W_RESIDUAL's
statistic), crossing_deg (per photo; null for a corner reading), edge_epipolar_deg (per photo [edge 1, edge 2] the
smallest angle to a line to another direction; null for a corner reading), lines, slide (null unless two directions),
cond, warnings, model_t (the build's t at (d, y), ``model_t``: within MODEL_EDGE_MM outside the outline the nearest
probe's; null farther out), picks (the input, rounded)}; every other float rounded to 0.001 (as v2's depth records), and the
text prints from those numbers (``fixed`` = lab.fixed). t and model_t are the build's at read time: ``refresh``
recomputes them from a later build (X, d, y do not depend on it).

``reading_text(rec)`` one line (the Prada crown's K, 4-photo crossing, tests/agent/test_temple_readings.py): name, kind
and photos; d, y, X with sd in mm (X's pick part, its camera part per fit); t in mm at that d ("model t 6.50", or
"outside the model's outline"); residual px (in sd of the photo's stated pixel error) per photo; crossing deg; the
condition number with its limit; the slide line (two directions); then each warning as "; CODE message".

``pick_sheet(basis, rec, out_path)``: one image per reading, a row per photo: the clean crop and the same crop drawn
(both at one scale, centred on the pixel used, at least BOX_MIN_W x BOX_MIN_H native px so a tip's closing band and
its neighbouring corners show, at most BOX_MAX_W x BOX_MAX_H so the crossing shows at >= 2x): the corner pick or the
crossing (a hollow green ring and the crossing's 2-sigma pick ellipse), the traced points (cyan dots) and their lines
(cyan), the read point's projection (a magenta cross with a gap at its pixel), and each line to another direction
(orange dashed) through the pixel used, so Astra sees which feature it picked and which way the residual is blind.
"""
from __future__ import annotations

import itertools
import math
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.optimize import least_squares
from scipy.stats import chi2 as _chi2

from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import project_mm
from . import geometry as G

KINDS = ("corner", "crossing")
PHOTOS_MIN, PHOTOS_MAX = 2, 4
EDGE_POINTS_MIN, EDGE_POINTS_MAX = 2, 6
EDGE_SPAN_MIN_PX = 4.0          # a traced edge's points span at least this (a shorter one gives no direction)
EDGE_SYS_PX = 0.7               # per-edge offset error shared by a trace's points (px; picks.json sys_px 0.5-0.7)
SD_PX_DEFAULT = 1.0             # a pick's sd when sd_px is null (px, both ways)
SD_PX_MIN, SD_PX_MAX = 0.1, 20.0
CAMERA_PX_SD = 1.0              # a fitted camera's error in px at a point (both ways), added to the pick's
TRANSPORT_PX_DEFAULT = 1.0      # a transported camera's own error when its record names no contour residual (px)
THETA_SD_DEG = 0.3              # each camera fit's temple theta error (deg): the camera term
COND_MAX = 20.0                 # = v2's lab.CORNER_COND_MAX (test_temple_readings pins it)
COND_PRINT_MAX = 1e4            # a larger condition number prints as "above 10000" (one direction twice: ~1e16)
CROSS_MIN_DEG = 1.0             # two edges closer to parallel do not cross at one pixel: refused
SHALLOW_DEG = 15.0              # W_SHALLOW_CROSSING below this crossing angle
EPIPOLAR_WARN_DEG = 10.0        # W_ALONG_LINE: a traced edge this close to its photo's slide line
SAME_DIR_DEG = 2.0              # photos whose lines of sight at the point are this close: one direction
RESIDUAL_P = 0.003              # W_RESIDUAL: chi2 above its 1 - RESIDUAL_P point (2n - 3 dof): "3 sigma"
NEAR = 0.05                     # = camera.project's near plane (depth <= 0.05 raises there)
REF_D_MM, REF_T_MM = 40.0, 5.0  # the temple point (d, y 0, t) where a refusal's condition number is taken (no fit)
SELF_CHECK_PX = 1e-5            # camera_matrix against project_mm at every solution (a code defect otherwise)
LSQ_TOL = 1e-12
NAME_MAX = 24                   # = geometry.NAME_RE (a node's name)
MODEL_EDGE_MM = 0.05            # model_t: a reading this close outside the build's outline takes the surface's t at
#                                 the nearest probe inside (a corner read ON the outline lies a few microns out once
#                                 the fit moves its node: session2's C 0.006 mm and D 0.029 mm below revision 4's
#                                 bottom outline printed "model out"); farther out it has none
EDGE_PROBES = 16                # model_t: probes per ring (rings at 1/4, 1/2 and all of MODEL_EDGE_MM)
OUTSIDE_MODEL = "outside the model's outline"   # reading_text (and facet_inspect's readings) where model_t is null
TRANSPORT_RE = re.compile(r"camera transported from ([A-Za-z0-9_.-]+)")
CONTOUR_RE = re.compile(r"contour ([0-9]+(?:\.[0-9]+)?) px")
REF_PREFIX = "reference-"
# pick_sheet
PANEL_W, PANEL_H = 480, 320
ZOOM_MAX_K = 4.0
BOX_MIN_W, BOX_MIN_H = 204, 136  # native px: a tip's closing band and its neighbouring corners show (~2.35x)
BOX_MAX_W, BOX_MAX_H = 240, 160  # native px: the crossing shows at >= 2x; far traced points fall outside, their
#                                  lines still cross the panel
BOX_MARGIN_PX = 14
TITLE_PX, LABEL_PX, GAP_PX = 40, 18, 4
TITLE_FONT_PX, LABEL_FONT_PX = 14, 13
PICK_RGB, EDGE_RGB, FIT_RGB, LINE_RGB = (0, 220, 60), (0, 210, 230), (235, 0, 200), (255, 150, 0)
DASH_PX = 8


# --------------------------------------------------------------------------------------------- small helpers
def fixed(x: float, nd: int) -> str:
    """= lab.fixed: x to nd decimals, rounded half up from its value to 0.001; never "-0.00" (a test pins it)."""
    q = Decimal(repr(round(float(x), 3))).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    return f"{abs(q) if q == 0 else q}"


def signed(x: float, nd: int) -> str:
    """fixed with an explicit sign ("+0.12", "-0.03", "+0.00")."""
    s = fixed(x, nd)
    return s if s.startswith("-") else "+" + s


def _r(x, nd: int = 3):
    """Every float of x (nested lists) rounded to nd decimals; None stays None."""
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_r(v, nd) for v in x]
    return None if x is None else round(float(x), nd) + 0.0


def _inner_x(tb, d: float) -> float:
    return float(np.asarray(tb.spec.inner(d), float))


def model_t(tb, d: float, y: float) -> float | None:
    """The build's t (outer surface X - x_in(d)) at (d, y) (geometry.surface_at); a point outside the outline by at
    most MODEL_EDGE_MM takes the X of the nearest probe inside (EDGE_PROBES on rings of a quarter, a half and all of
    MODEL_EDGE_MM; t from the reading's own d's inner face); None farther out (the text: "outside the model's
    outline")."""
    ang = np.linspace(0.0, 2.0 * math.pi, EDGE_PROBES, endpoint=False)
    ring = np.column_stack([np.cos(ang), np.sin(ang)])
    P = np.vstack([[[d, y]]] + [np.array([d, y]) + f * MODEL_EDGE_MM * ring for f in (0.25, 0.5, 1.0)])
    X, _reg = G.surface_at(tb, P)
    hit = np.nonzero(np.isfinite(X))[0]
    return float(X[hit[0]]) - _inner_x(tb, d) if len(hit) else None


def _opening(tb):
    h = tb.spec.hinge
    deg = float(h.get("opening_deg") or 0.0)
    return (deg, (abs(float(h["axis_xz_mm"][0])), float(h["axis_xz_mm"][1]))) if deg else None


def short_id(rid: str) -> str:
    """A basis reference as a lab photo id: "reference-05" -> "05" (= Lab.pid for a target photo)."""
    return rid[len(REF_PREFIX):] if rid.startswith(REF_PREFIX) else rid


def photo_ref(basis, pid: str, refs: dict | None = None) -> str:
    """The basis reference of photo ``pid``: refs[pid] when ``refs`` is given (the lab's {photo id: basis ref}), else
    pid itself when the basis holds it, else "reference-<pid>". ValueError when the basis has no camera for it."""
    if not isinstance(pid, str) or not pid:
        raise ValueError("each pick names its photo")
    rid = (refs or {}).get(pid) if refs is not None else (
        pid if pid in basis.refs else (REF_PREFIX + pid if REF_PREFIX + pid in basis.refs else None))
    if rid is None or rid not in basis.refs:
        raise ValueError(f"photo {pid} has no camera")
    return rid


def camera_source(basis, rid: str) -> str:
    """The basis reference whose fitted camera this one is: a transported camera's record names its source in its
    fit_status ("camera transported from 05 ...") or a transport block's "source" ("reference-02.webp"); a fitted
    camera (or a source the basis does not hold) is its own."""
    rec = basis.refs.get(rid) or {}
    tokens = []
    m = TRANSPORT_RE.search(str(rec.get("fit_status") or ""))
    if m:
        tokens.append(m.group(1).rstrip("."))
    src = (rec.get("transport") or {}).get("source") if isinstance(rec.get("transport"), dict) else None
    if src:
        tokens.append(str(src))
    for tok in tokens:
        for cand in (tok, Path(tok).stem, REF_PREFIX + tok, REF_PREFIX + Path(tok).stem):
            if cand in basis.refs and cand != rid:
                return cand
    return rid


def transport_px(basis, rid: str) -> float:
    """A transported camera's own error in px (both ways, independent of its source's): the contour residual of the
    transport its fit_status names ("camera transported from 02 by image similarity (contour 0.82 px, ...)"), else
    TRANSPORT_PX_DEFAULT; 0 for a fitted camera (whose fit_status contour is against the model, not a transport)."""
    if camera_source(basis, rid) == rid:
        return 0.0
    m = CONTOUR_RE.search(str((basis.refs.get(rid) or {}).get("fit_status") or ""))
    return float(m.group(1)) if m else TRANSPORT_PX_DEFAULT


# --------------------------------------------------------------------------------------------- projection
def camera_matrix(camera, frame, side: int, hinge_z: float, pose=None, opening=None) -> np.ndarray:
    """(3, 4) H: a chart point p = (d, y, X) of the ``side`` temple shows at pixel H[:2] @ [p, 1] / H[2] @ [p, 1], and
    H[2] @ [p, 1] is camera.project's depth (1 - perspective * V . toward; <= NEAR is behind its near plane). The
    composition of pose.chart_to_posed (affine: mirror, hinge opening, the pose's theta; read off at the origin and the
    unit vectors), the NormFrame and camera.project (a pinhole with roll, scale and centre), written as one projective
    matrix so a reading can triangulate linearly (DLT) and fit with an analytic Jacobian."""
    O = PO.chart_to_posed(np.vstack([np.zeros(3), np.eye(3)]), side, float(hinge_z), pose, opening)
    A = (O[1:] - O[0]).T / float(frame.extent)                       # V = A p + b, V in the normalized frame
    b = (O[0] - np.asarray(frame.center, float)) / float(frame.extent)
    yaw, pitch, roll = np.radians([camera.yaw, camera.pitch, camera.roll])
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right, up, toward = np.array([cy, 0, -sy]), np.array([-sp * sy, cp, -sp * cy]), np.array([cp * sy, sp, cp * cy])
    cr, sr = math.cos(roll), math.sin(roll)
    rw = np.append(-float(camera.perspective) * toward, 1.0)
    rx, ry = np.append(right, -camera.eye_x), np.append(up, -camera.eye_y)
    R = np.vstack([float(camera.center_x) * rw + float(camera.scale) * (cr * rx - sr * ry),
                   float(camera.center_y) * rw - float(camera.scale) * (sr * rx + cr * ry), rw])
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = A, b
    return R @ T


def _project(H: np.ndarray, p) -> tuple[np.ndarray, float]:
    h = H @ np.append(np.asarray(p, float), 1.0)
    return h[:2] / h[2], float(h[2])


def _jac_px(H: np.ndarray, p) -> np.ndarray:
    """(2, 3) px per mm of (d, y, X) at p."""
    pix, w = _project(H, p)
    return (H[:2, :3] - np.outer(pix, H[2, :3])) / w


def _dlt(Hs: list, uvs: list) -> np.ndarray | None:
    """The chart point whose projections best meet the pixels linearly (each row u H2 - H0, v H2 - H1 normalised)."""
    rows, rhs = [], []
    for H, (u, v) in zip(Hs, uvs):
        for a in (u * H[2] - H[0], v * H[2] - H[1]):
            n = float(np.linalg.norm(a[:3]))
            if n > 0:
                rows.append(a[:3] / n)
                rhs.append(-a[3] / n)
    if len(rows) < 3:
        return None
    p, *_ = np.linalg.lstsq(np.asarray(rows), np.asarray(rhs), rcond=None)
    return p if np.all(np.isfinite(p)) else None


def _solve(Hs: list, uvs: list, Linv: np.ndarray, x0) -> tuple[np.ndarray, float]:
    """Weighted least squares (whitened residuals Linv @ (pixel - uv), stacked) from x0: (p, chi2 = their sum of
    squares)."""
    U = np.concatenate([np.asarray(uv, float) for uv in uvs])

    def fun(p):
        return Linv @ (np.concatenate([_project(H, p)[0] for H in Hs]) - U)

    def jac(p):
        return Linv @ np.vstack([_jac_px(H, p) for H in Hs])
    r = least_squares(fun, np.asarray(x0, float), jac=jac, method="lm", xtol=LSQ_TOL, ftol=LSQ_TOL, gtol=LSQ_TOL)
    return np.asarray(r.x, float), float(2.0 * r.cost)


def _facing(cam, frame, pose, p, hinge_z: float, opening) -> list:
    """= v2's lab._photo_sides at p (test_temple_readings.ref_photo_sides): the temple whose outer face the camera
    sees there, else both."""
    seen = []
    for side in (1, -1):
        P = PO.chart_to_posed([p, np.asarray(p, float) + [0.0, 0.0, 1.0]], side, hinge_z, pose, opening)
        seen.append(bool(PO.faces_camera(P[:1], P[1] - P[0], cam, frame)[0]))
    return [1] if seen == [True, False] else ([-1] if seen == [False, True] else [1, -1])


def _in_bounds(tb, p) -> bool:
    """Inside the lab's engineering bounds: |d| <= D_LIMIT_MM, |y| <= Y_LIMIT_MM and |t| <= T_LIMIT_MM (t = X -
    x_in(d)); the t bound drops the mirrored-chart ghost (module doc)."""
    return bool(np.all(np.isfinite(p)) and abs(p[0]) <= G.D_LIMIT_MM and abs(p[1]) <= G.Y_LIMIT_MM
                and abs(p[2] - _inner_x(tb, p[0])) <= G.T_LIMIT_MM)


def _cond(Hs: list, p) -> float:
    sv = np.linalg.svd(np.vstack([_jac_px(H, p) for H in Hs]), compute_uv=False)
    return float(sv[0] / sv[-1]) if sv[-1] > 0 else float("inf")


def one_line(photos: list, cond: float) -> str:
    """The condition refusal's text (facts only: the photos, the number, the limit)."""
    c = f"{cond:.0f}" if np.isfinite(cond) and cond < COND_PRINT_MAX else f"above {COND_PRINT_MAX:.0f}"
    return (f"photos {', '.join(photos)} see this point along nearly one line: the pixel Jacobian's condition number "
            f"is {c} (limit {COND_MAX:g})")


def _theta_moved(pose, delta: float, axis_xz) -> dict:
    """The pose with both temples' theta moved by delta (a side the pose lacks gets delta about the spec's axis)."""
    out = {}
    for side in (1, -1):
        k = PO.SIDE_KEYS[side]
        rec = (pose or {}).get(k)
        if rec is None:
            out.update(PO.make_pose({side: delta}, axis_xz))
        else:
            out[k] = {"theta_deg": float(rec["theta_deg"]) + delta, "axis_xz_mm": [float(v) for v in rec["axis_xz_mm"]]}
    return out


def _line_dir(e: np.ndarray) -> tuple[np.ndarray, float]:
    """(unit image direction pointing right (else up), its signed angle from horizontal in deg, + rising to the right
    (image v grows downward))."""
    u = np.asarray(e, float) / float(np.linalg.norm(e))
    if u[0] < 0 or (u[0] == 0 and u[1] > 0):
        u = -u
    return u, math.degrees(math.atan2(-u[1], u[0]))


# --------------------------------------------------------------------------------------------- edges
def edge_line(points, sd_px: float) -> dict:
    """A traced edge's total-least-squares line: {c (centroid), t (unit direction, u-ward), n (unit normal), S (sum of
    the points' squared distances along t from c), count, rms (off the line; 0 for 2 points), sigma (the point sd used:
    sd_px, or rms when larger with >= 3 points), span (px)}. ValueError for fewer than 2 or more than 6 points or a
    span under EDGE_SPAN_MIN_PX."""
    Q = np.asarray(points, float)
    if Q.ndim != 2 or Q.shape[1] != 2 or not EDGE_POINTS_MIN <= len(Q) <= EDGE_POINTS_MAX or not np.all(np.isfinite(Q)):
        raise ValueError(f"an edge is {EDGE_POINTS_MIN}..{EDGE_POINTS_MAX} points [u, v]")
    c = Q.mean(axis=0)
    _u, _s, Vt = np.linalg.svd(Q - c)
    t = Vt[0] if (Vt[0][0] > 0 or (Vt[0][0] == 0 and Vt[0][1] > 0)) else -Vt[0]
    n = np.array([-t[1], t[0]])
    s, off = (Q - c) @ t, (Q - c) @ n
    span = float(s.max() - s.min())
    if span < EDGE_SPAN_MIN_PX:
        raise ValueError(f"an edge's points must span at least {EDGE_SPAN_MIN_PX:g} px (these span {span:.1f})")
    rms = float(np.sqrt((off ** 2).sum() / (len(Q) - 2))) if len(Q) >= 3 else 0.0
    return {"c": c, "t": t, "n": n, "S": float((s ** 2).sum()), "count": len(Q), "rms": rms,
            "sigma": max(float(sd_px), rms), "span": span}


def crossing_pixel(e1: dict, e2: dict) -> tuple[np.ndarray, np.ndarray, float]:
    """(pixel, 2x2 covariance px^2, angle deg) of two edge_line lines' crossing: each line's error normal to itself at
    the crossing has variance sigma^2 (1/count + s^2 / S) + EDGE_SYS_PX^2, s the crossing's distance along it from its
    centroid (offset and angle errors of a least-squares line, uncorrelated at the centroid, plus the trace's shared
    offset); the crossing moves by N^-1 of those two normal errors (N = the normals as rows). ValueError when the edges
    are within CROSS_MIN_DEG of parallel."""
    ang = math.degrees(math.acos(min(1.0, abs(float(e1["t"] @ e2["t"])))))
    if ang < CROSS_MIN_DEG:
        raise ValueError(f"the two edges are {ang:.2f}° from parallel: they do not cross at one pixel")
    N = np.vstack([e1["n"], e2["n"]])
    x = np.linalg.solve(N, np.array([e1["n"] @ e1["c"], e2["n"] @ e2["c"]]))
    var = [e["sigma"] ** 2 * (1.0 / e["count"] + float((x - e["c"]) @ e["t"]) ** 2 / e["S"]) + EDGE_SYS_PX ** 2
           for e in (e1, e2)]
    Ni = np.linalg.inv(N)
    return x, Ni @ np.diag(var) @ Ni.T, ang


def _angle_deg(a, b) -> float:
    """The unsigned angle (0..90 deg) between two image directions."""
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return math.degrees(math.acos(min(1.0, abs(float(np.dot(a, b))) / (na * nb))))


# --------------------------------------------------------------------------------------------- picks
def _check_px(pid: str, shape, uv, where: str) -> np.ndarray:
    H, W = shape
    try:
        a = np.asarray(uv, float).reshape(-1)
    except (TypeError, ValueError):
        a = np.full(1, np.nan)
    if a.shape != (2,) or not np.all(np.isfinite(a)):
        raise ValueError(f"{where} must be [u, v] finite native pixels")
    if not (-0.5 <= a[0] <= W - 0.5 and -0.5 <= a[1] <= H - 0.5):
        raise ValueError(f"{where} {_r(a.tolist(), 2)} must lie inside the native {W} x {H} photo {pid}")
    return a


def _parse_picks(basis, picks, kind: str, refs) -> list[dict]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not isinstance(picks, list) or not PHOTOS_MIN <= len(picks) <= PHOTOS_MAX:
        raise ValueError(f"picks must name {PHOTOS_MIN}..{PHOTOS_MAX} photos with a camera")
    out, seen = [], set()
    for i, pk in enumerate(picks):
        if not isinstance(pk, dict):
            raise ValueError(f"picks[{i}] must be {{photo, uv, edges, sd_px}}")
        pid = pk.get("photo")
        rid = photo_ref(basis, pid, refs)
        if pid in seen:
            raise ValueError(f"picks[{i}] repeats photo {pid}")
        seen.add(pid)
        sd = pk.get("sd_px")
        if sd is None:
            sd = SD_PX_DEFAULT
        if isinstance(sd, bool) or not isinstance(sd, (int, float)) or not SD_PX_MIN <= float(sd) <= SD_PX_MAX:
            raise ValueError(f"picks[{i}].sd_px must be null or {SD_PX_MIN:g}..{SD_PX_MAX:g} px")
        sd = float(sd)
        shape = basis.shape(rid)
        rec = {"photo": pid, "rid": rid, "sd_px": sd, "uv_in": None, "edges_in": None}
        if kind == "corner":
            if pk.get("edges") is not None or pk.get("uv") is None:
                raise ValueError(f"picks[{i}]: a corner reading takes uv [u, v] and edges null")
            rec["uv"] = _check_px(pid, shape, pk["uv"], f"picks[{i}].uv")
            rec["cov"] = np.eye(2) * sd ** 2
            rec["uv_in"] = rec["uv"].tolist()
        else:
            edges = pk.get("edges")
            if pk.get("uv") is not None or not isinstance(edges, list) or len(edges) != 2:
                raise ValueError(f"picks[{i}]: a crossing reading takes edges (two traced edges of "
                                 f"{EDGE_POINTS_MIN}..{EDGE_POINTS_MAX} points [u, v]) and uv null")
            lines = []
            for k, e in enumerate(edges):
                if not isinstance(e, list) or not EDGE_POINTS_MIN <= len(e) <= EDGE_POINTS_MAX:
                    raise ValueError(f"picks[{i}].edges[{k}] must be {EDGE_POINTS_MIN}..{EDGE_POINTS_MAX} points "
                                     f"[u, v]")
                pts = [_check_px(pid, shape, q, f"picks[{i}].edges[{k}][{j}]") for j, q in enumerate(e)]
                try:
                    lines.append(edge_line(pts, sd))
                except ValueError as exc:
                    raise ValueError(f"picks[{i}].edges[{k}]: {exc}") from None
            try:
                x, cov, ang = crossing_pixel(*lines)
            except ValueError as exc:
                raise ValueError(f"picks[{i}] ({pid}): {exc}") from None
            H, W = shape
            if not (-0.5 <= x[0] <= W - 0.5 and -0.5 <= x[1] <= H - 0.5):
                raise ValueError(f"picks[{i}]: the edges of photo {pid} cross outside the photo, at "
                                 f"{_r(x.tolist(), 1)}")
            rec.update(uv=x, cov=cov, lines=lines, crossing_deg=ang,
                       edges_in=[[[float(a) for a in q] for q in e] for e in edges])
        out.append(rec)
    return out


# --------------------------------------------------------------------------------------------- the reading
def read_point(tb, basis, name: str, picks: list, *, kind: str, refs: dict | None = None) -> dict:
    """The reading ``name`` from ``picks`` (module doc): tb = a build (its spec's hinge and inner face; model_t from
    its surface), basis = the camera basis (camera, frame, pose, shape, refs), refs = the lab's {photo id: basis ref}
    (None: a photo id is a basis ref or "reference-<id>"). Returns the record; ValueError for a refusal."""
    if not isinstance(name, str) or not G.NAME_RE.match(name) or name == "base":
        raise ValueError(f"name must be a node name (a letter, then up to {NAME_MAX - 1} letters, digits, _ or -; "
                         f"not base)")
    P = _parse_picks(basis, picks, kind, refs)
    hinge_z, opening = float(tb.spec.hinge["z_mm"]), _opening(tb)
    axis_xz = (abs(float(tb.spec.hinge["axis_xz_mm"][0])), float(tb.spec.hinge["axis_xz_mm"][1]))
    frame = basis.frame
    cams = [basis.camera(r["rid"]) for r in P]
    poses = [basis.pose(r["rid"]) for r in P]
    uvs = [r["uv"] for r in P]
    n = len(P)
    photos = [r["photo"] for r in P]
    sources = [camera_source(basis, r["rid"]) for r in P]
    own = [np.zeros((2, 2)) if s == r["rid"] else np.eye(2) * transport_px(basis, r["rid"]) ** 2
           for r, s in zip(P, sources)]

    def mats(combo, pose_list):
        return [camera_matrix(c, frame, s, hinge_z, ps, opening) for c, s, ps in zip(cams, combo, pose_list)]

    def pixel_cov(combo, Hs, p) -> np.ndarray:
        """The stacked pixel covariance at p (module doc): picks, plus camera errors e_i = M_i e_fit + own_i, with
        M_i = J_i J_fit^+ (the identity for a fitted camera, the transport's similarity for a twin)."""
        M = []
        for i in range(n):
            if sources[i] == P[i]["rid"]:
                M.append(np.eye(2))
                continue
            Hf = camera_matrix(basis.camera(sources[i]), frame, combo[i], hinge_z, basis.pose(sources[i]), opening)
            M.append(_jac_px(Hs[i], p) @ np.linalg.pinv(_jac_px(Hf, p)))
        S = np.zeros((2 * n, 2 * n))
        for i in range(n):
            S[2 * i:2 * i + 2, 2 * i:2 * i + 2] += P[i]["cov"] + own[i]
            for j in range(n):
                if sources[i] == sources[j]:
                    S[2 * i:2 * i + 2, 2 * j:2 * j + 2] += CAMERA_PX_SD ** 2 * M[i] @ M[j].T
        return S

    best = None
    for combo in itertools.product((1, -1), repeat=n):
        Hs = mats(combo, poses)
        p0 = _dlt(Hs, uvs)
        if p0 is None or not _in_bounds(tb, p0) or min(_project(H, p0)[1] for H in Hs) <= NEAR:
            continue
        Sig = pixel_cov(combo, Hs, p0)
        try:
            Linv = np.linalg.inv(np.linalg.cholesky(Sig))
            p, cost = _solve(Hs, uvs, Linv, p0)
        except (ValueError, np.linalg.LinAlgError):
            continue
        if not _in_bounds(tb, p) or min(_project(H, p)[1] for H in Hs) <= NEAR:
            continue
        if any(s not in _facing(c, frame, ps, p, hinge_z, opening) for c, ps, s in zip(cams, poses, combo)):
            continue
        if best is None or cost < best[1] - 1e-12:
            best = (p, cost, combo, Hs, Sig)
    if best is None:
        # nearly parallel lines of sight (one direction seen twice, e.g. B1 + 05) put the linear start far away or
        # behind a camera: their condition number, at a temple point and on the temples each photo sees there, says so
        ref = np.array([REF_D_MM, 0.0, _inner_x(tb, REF_D_MM) + REF_T_MM])
        sides = [_facing(c, frame, ps, ref, hinge_z, opening) for c, ps in zip(cams, poses)]
        conds = [_cond(mats(combo, poses), ref) for combo in itertools.product(*sides)]
        if conds and min(conds) > COND_MAX:
            raise ValueError(one_line(photos, min(conds)))
        raise ValueError(f"no chart point in front of the cameras fits these pixels (photos {', '.join(photos)})")
    p, _cost, combo, Hs, Sig0 = best
    cond = _cond(Hs, p)
    if not np.isfinite(cond) or cond > COND_MAX:
        raise ValueError(one_line(photos, cond))
    Sig = pixel_cov(combo, Hs, p)                            # a twin's map at the solution (a similarity: unchanged)
    Linv = np.linalg.inv(np.linalg.cholesky(Sig))
    if not np.allclose(Sig, Sig0, rtol=0.0, atol=1e-9):
        p = _solve(Hs, uvs, Linv, p)[0]
    fit_uv = []
    for H, c, s, ps in zip(Hs, cams, combo, poses):         # the matrix is the lab's projection (module doc)
        q, _w = _project(H, p)
        ref = project_mm(PO.chart_to_posed([p], s, hinge_z, ps, opening), c, frame)[0]
        if float(np.max(np.abs(q - ref))) > SELF_CHECK_PX:
            raise RuntimeError(f"camera_matrix differs from project_mm by {float(np.max(np.abs(q - ref))):.2e} px")
        fit_uv.append(q)
    J = [_jac_px(H, p) for H in Hs]
    Jw = Linv @ np.vstack(J)
    cov = np.linalg.inv(Jw.T @ Jw)
    sd_pick = np.sqrt(np.maximum(np.diag(cov), 0.0))
    # the camera term: each camera fit's theta moved by +-THETA_SD_DEG, the point re-solved (same temples)
    short = {r["rid"]: r["photo"] for r in P}
    fits = [short.get(src, short_id(src)) for src in sources]
    shift, gth = {}, []
    for src in dict.fromkeys(sources):
        moved, pix = [], []
        for sgn in (1.0, -1.0):
            pl = [_theta_moved(ps, sgn * THETA_SD_DEG, axis_xz) if sources[i] == src else ps
                  for i, ps in enumerate(poses)]
            Hm = mats(combo, pl)
            moved.append(_solve(Hm, uvs, Linv, p)[0])
            pix.append(np.concatenate([_project(H, p)[0] for H in Hm]))
        shift[short.get(src, short_id(src))] = (moved[0] - moved[1]) / 2.0
        gth.append((pix[0] - pix[1]) / 2.0)                 # the pixels' shift for +THETA_SD_DEG of this fit
    var_cam = np.sum([v ** 2 for v in shift.values()], axis=0)
    sd_cam = np.sqrt(var_cam)
    sd = np.sqrt(sd_pick ** 2 + var_cam)
    # residuals: px, and in sd of each photo's whole stated pixel error (the stacked covariance plus each fit's theta
    # shift, shared by its photos: a fit's theta moves its photos' pixels unlike the others', so readings across fits
    # disagree by it; without it W_RESIDUAL fired on 3.3 % of honest random readings, review p13 noise); the reading's
    # chi2 in that covariance, minimised over the point (linear about p)
    Sr = Sig + np.sum([np.outer(g, g) for g in gth], axis=0)
    rvec = [q - uv for q, uv in zip(fit_uv, uvs)]
    res = [float(np.hypot(*v)) for v in rvec]
    res_sd = [math.sqrt(max(float(v @ np.linalg.solve(Sr[2 * i:2 * i + 2, 2 * i:2 * i + 2], v)), 0.0))
              for i, v in enumerate(rvec)]
    Lr = np.linalg.inv(np.linalg.cholesky(Sr))
    rw, Jr = Lr @ np.concatenate(rvec), Lr @ np.vstack(J)
    chi2 = max(float(rw @ rw - rw @ Jr @ np.linalg.solve(Jr.T @ Jr, Jr.T @ rw)), 0.0)
    dof = 2 * n - 3
    # directions (lines of sight at p, chart mm: each Jacobian's null vector), their epipolar lines, the slide line
    los = [np.linalg.svd(Ji)[2][-1] for Ji in J]
    groups: list[list[int]] = []
    for i in range(n):
        g = next((g for g in groups if abs(float(los[i] @ los[g[0]])) >= math.cos(math.radians(SAME_DIR_DEG))), None)
        if g is None:
            groups.append([i])
        else:
            g.append(i)
    gof = {i: k for k, g in enumerate(groups) for i in g}
    lines, ldir = [], []
    for i in range(n):
        row, dirs = [], []
        for k, g in enumerate(groups):
            if k == gof[i]:
                continue
            e = J[i] @ los[g[0]]
            if float(np.linalg.norm(e)) < 1e-12:
                continue
            u, deg = _line_dir(e)
            row.append({"to": [photos[j] for j in g], "deg": _r(deg)})
            dirs.append((u, float(np.linalg.norm(e)), los[g[0]]))
        lines.append(row)
        ldir.append(dirs)
    slide = None
    if len(groups) == 2 and all(len(d) == 1 for d in ldir):
        mm = []
        for i in range(n):
            u, norm, w = ldir[i][0]
            s = 1.0 if float(u @ (J[i] @ w)) > 0 else -1.0     # 1 px along u moves the point s w / |J_i w|
            mm.append(s * w / norm)
        slide = {"groups": [[photos[i] for i in g] for g in groups],
                 "deg": [lines[i][0]["deg"] for i in range(n)], "mm_per_px": [_r(v) for v in mm]}
    warnings, epi, along = [], [], []
    for i, r in enumerate(P):
        if kind != "crossing":
            epi.append(None)
            continue
        epi.append([min((_angle_deg(u, ln["t"]) for u, _nm, _w in ldir[i]), default=None) for ln in r["lines"]])
        if slide is None:
            continue
        u = ldir[i][0][0]
        near = [k for k, a in enumerate(epi[-1]) if a is not None and a < EPIPOLAR_WARN_DEG]
        if near:
            Ni = np.linalg.inv(np.vstack([ln["n"] for ln in r["lines"]]))
            gain = max(abs(float(u @ Ni[:, k])) for k in (0, 1))
            along.append({"photo": r["photo"], "edges": [k + 1 for k in near],
                          "deg": [_r(epi[-1][k], 2) for k in near], "gain": _r(gain, 2),
                          "mm": np.asarray(slide["mm_per_px"][i]) * gain})
    if along:
        edges_at, gains = [], []
        for g in slide["groups"]:                           # one item per direction (a twin and its source merged)
            its = [a for a in along if a["photo"] in g]
            if not its:
                continue
            es = sorted({k for a in its for k in a["edges"]})
            lo, hi = (fixed(f(x for a in its for x in a["deg"]), 1) for f in (min, max))
            who = ", ".join(a["photo"] for a in its)
            edges_at.append(f"{who} edge{'s' if len(es) > 1 else ''} {' and '.join(map(str, es))} "
                            f"({lo if lo == hi else lo + '-' + hi}°)")
            gains.append(f"{who} {fixed(max(a['gain'] for a in its), 1)}")
        worst = max(along, key=lambda a: float(np.linalg.norm(a["mm"])))
        v = np.abs(worst["mm"])
        warnings.append({"code": "W_ALONG_LINE", "photos": [a["photo"] for a in along],
                         "items": [{k: a[k] for k in ("photo", "edges", "deg", "gain")} for a in along],
                         "message": f"edges along the slide line: {', '.join(edges_at)}; along it the crossing moves "
                                    f"{', '.join(gains)} px per px of an edge's offset (the point up to d "
                                    f"{fixed(v[0], 2)}, y {fixed(v[1], 2)}, X {fixed(v[2], 2)} mm per px, "
                                    f"{worst['photo']}), and the residual cannot check that each edge is the same "
                                    f"feature in both directions"})
    shallow = [(r["photo"], r["crossing_deg"]) for r in P if kind == "crossing" and r["crossing_deg"] < SHALLOW_DEG]
    if shallow:
        warnings.append({"code": "W_SHALLOW_CROSSING", "photos": [s[0] for s in shallow],
                         "items": [{"photo": pid, "deg": _r(a, 2)} for pid, a in shallow],
                         "message": f"the edges cross at "
                                    f"{', '.join(f'{fixed(a, 1)}° in {pid}' for pid, a in shallow)}: "
                                    f"along them the crossing moves "
                                    f"{', '.join(fixed(1.0 / math.sin(math.radians(a)), 1) for _p, a in shallow)} px "
                                    f"per px of edge offset"})
    limit = float(_chi2.ppf(1.0 - RESIDUAL_P, dof))
    if chi2 > limit:
        warnings.append({"code": "W_RESIDUAL", "photos": photos,
                         "message": f"the pixels are not one point within their stated error: the residuals' chi2 is "
                                    f"{fixed(chi2, 1)} for {dof} degrees of freedom (its {100 * (1 - RESIDUAL_P):g} % "
                                    f"point is {fixed(limit, 1)})"})
    d, y, X = (float(v) for v in p)
    xin, mt = _inner_x(tb, d), model_t(tb, d, y)
    return {"name": name, "kind": kind, "d": _r(d), "y": _r(y), "X": _r(X), "t": _r(X - xin),
            "sd_X": _r(sd[2]), "sd_X_pick": _r(sd_pick[2]), "sd_X_camera": _r(sd_cam[2]),
            "sd_d": _r(sd[0]), "sd_d_pick": _r(sd_pick[0]), "sd_d_camera": _r(sd_cam[0]),
            "sd_y": _r(sd[1]), "sd_y_pick": _r(sd_pick[1]), "sd_y_camera": _r(sd_cam[1]),
            "cov_pick": _r(cov.tolist(), 6), "camera_shift": {f: _r(v) for f, v in shift.items()},
            "photos": photos, "rids": [r["rid"] for r in P], "fits": fits, "sides": list(combo),
            "uv": [_r(r["uv"].tolist()) for r in P], "uv_cov": [_r(r["cov"].tolist()) for r in P],
            "uv_fit": [_r(q.tolist()) for q in fit_uv], "res_px": [_r(v) for v in res],
            "res_sd": [_r(v) for v in res_sd], "chi2": _r(chi2), "dof": dof,
            "crossing_deg": [_r(r["crossing_deg"]) for r in P] if kind == "crossing" else None,
            "edge_epipolar_deg": [_r(e) for e in epi] if kind == "crossing" else None,
            "lines": lines, "slide": slide, "cond": _r(cond), "warnings": warnings,
            "model_t": _r(mt),
            "picks": [{"photo": r["photo"], "uv": _r(r["uv_in"], 2) if r["uv_in"] is not None else None,
                       "edges": _r(r["edges_in"], 2) if r["edges_in"] is not None else None, "sd_px": _r(r["sd_px"])}
                      for r in P]}


def refresh(rec: dict, tb) -> dict:
    """A stored reading against a later build: t and model_t recomputed from tb's inner face and surface at the
    reading's (d, y) (they are the build's at read time; X, d, y and the errors do not depend on it). A new dict."""
    d, y, X = float(rec["d"]), float(rec["y"]), float(rec["X"])
    return {**rec, "t": _r(X - _inner_x(tb, d)), "model_t": _r(model_t(tb, d, y))}


def conditional_x(rec: dict, d: float, y: float) -> tuple[float, float]:
    """(X, sd) of the read point where its (d, y) is the given (d, y) (a node placed apart from its reading's (d, y)
    that is the same point): the pick part's Gaussian conditional, X + C_X,dy C_dy^-1 (d - d_r, y - y_r) with sd
    sqrt(C_XX - C_X,dy C_dy^-1 C_dy,X) from ``cov_pick``. The camera part is one rotation per fit (camera_shift),
    not in this sd."""
    C = np.asarray(rec["cov_pick"], float)
    k = C[2, :2] @ np.linalg.inv(C[:2, :2])
    X = float(rec["X"]) + float(k @ np.array([d - float(rec["d"]), y - float(rec["y"])]))
    return X, math.sqrt(max(float(C[2, 2] - k @ C[:2, 2]), 0.0))


def reading_text(rec: dict) -> str:
    """One reading as one line (module doc), its numbers through ``fixed`` from the record's: d, y, X in mm, t in mm
    with the model's t at that (d, y) ("outside the model's outline" when it has none there: session2 printed "model
    out", unexplained), each photo's residual in px and in sd of that photo's stated pixel error (res_sd; "residual px
    (sd)" did not say whose sd, v3p3 astra P3-9), the pixel Jacobian's condition number with its limit."""
    m = OUTSIDE_MODEL if rec.get("model_t") is None else f"model t {fixed(rec['model_t'], 2)}"
    res = ", ".join(f"{pid} {fixed(v, 1)} ({fixed(s, 1)})"
                    for pid, v, s in zip(rec["photos"], rec["res_px"], rec["res_sd"]))
    cross = ("; crossing deg " + ", ".join(f"{pid} {fixed(a, 1)}" for pid, a in zip(rec["photos"], rec["crossing_deg"]))
             if rec.get("crossing_deg") else "")
    shift = rec.get("camera_shift") or {}
    big = max(shift, key=lambda f: abs(shift[f][2]), default=None)  # every fit that moves X by >= 0.01, the largest
    cam = ", ".join(f"{f} {fixed(abs(v[2]), 2)}" for f, v in shift.items() if f == big or fixed(abs(v[2]), 2) != "0.00")
    text = (f"{rec['name']} ({rec['kind']}; {', '.join(rec['photos'])}): d {fixed(rec['d'], 2)} +-"
            f"{fixed(rec['sd_d'], 2)}, y {fixed(rec['y'], 2)} +-{fixed(rec['sd_y'], 2)}, X {fixed(rec['X'], 2)} +-"
            f"{fixed(rec['sd_X'], 2)} mm (pick {fixed(rec['sd_X_pick'], 2)}; camera {fixed(rec['sd_X_camera'], 2)}: "
            f"theta +-{THETA_SD_DEG:g}° of fit {cam}); t {fixed(rec['t'], 2)} mm at that d ({m}); residual px (in sd "
            f"of the photo's stated pixel error) {res}{cross}; condition number {fixed(rec['cond'], 1)} (limit "
            f"{COND_MAX:g})")
    sl = rec.get("slide")
    if sl:
        ix = {pid: i for i, pid in enumerate(rec["photos"])}
        parts = []
        for g in sl["groups"]:
            v = sl["mm_per_px"][ix[g[0]]]
            parts.append(f"{', '.join(f'{pid} {signed(sl['deg'][ix[pid]], 1)}°' for pid in g)}: d {signed(v[0], 2)} "
                         f"y {signed(v[1], 2)} X {signed(v[2], 2)} mm ({g[0]})")
        text += ("; slide line (+ rises to the right; a slide along it does not show in the residual; per px to the "
                 "right the point moves): " + "; ".join(parts))
    return text + "".join(f"; {w['code']} {w['message']}" for w in rec.get("warnings") or [])


# --------------------------------------------------------------------------------------------- pick sheet
def _font(px: int):
    return ImageFont.load_default(size=px)


def _dashed(dr, a, b, fill, width: int = 1) -> None:
    a, b = np.asarray(a, float), np.asarray(b, float)
    L = float(np.linalg.norm(b - a))
    if L <= 0:
        return
    u = (b - a) / L
    for s in np.arange(0.0, L, 2 * DASH_PX):
        p0, p1 = a + s * u, a + min(s + DASH_PX, L) * u
        dr.line([tuple(p0), tuple(p1)], fill=fill, width=width)


def _panel(rgb: np.ndarray, pick: dict, uv, uv_fit, cov, lines) -> tuple[Image.Image, Image.Image, str]:
    """One photo's crop clean and drawn (module doc), and its label."""
    Hh, Ww = rgb.shape[:2]
    c = np.asarray(uv, float)                       # centred on the pixel used, sized to the picks and the read point
    pts = [np.asarray(uv_fit, float)] + [np.asarray(q, float) for e in pick.get("edges") or [] for q in e]
    reach = np.max(np.abs(np.vstack(pts) - c), axis=0) + BOX_MARGIN_PX
    w = min(max(2 * reach[0], BOX_MIN_W, 2 * reach[1] * PANEL_W / PANEL_H), BOX_MAX_W)
    h = min(max(2 * reach[1], BOX_MIN_H, w * PANEL_H / PANEL_W), BOX_MAX_H)
    x0, y0 = int(max(0, math.floor(c[0] - w / 2))), int(max(0, math.floor(c[1] - h / 2)))
    x1, y1 = int(min(Ww, math.ceil(c[0] + w / 2))), int(min(Hh, math.ceil(c[1] + h / 2)))
    k = min(ZOOM_MAX_K, PANEL_W / (x1 - x0), PANEL_H / (y1 - y0))
    clean = Image.fromarray(np.ascontiguousarray(np.asarray(rgb)[y0:y1, x0:x1, :3]).astype(np.uint8))
    clean = clean.resize((max(1, round((x1 - x0) * k)), max(1, round((y1 - y0) * k))), Image.LANCZOS)
    im = clean.copy()
    dr = ImageDraw.Draw(im)

    def to(q):
        return ((float(q[0]) - x0 + 0.5) * k - 0.5, (float(q[1]) - y0 + 0.5) * k - 0.5)
    u, v = to(uv)
    span = float(np.hypot(im.width, im.height))
    for ln in lines or []:                          # the lines to the other directions through the pixel used
        a = math.radians(float(ln["deg"]))
        e = np.array([math.cos(a), -math.sin(a)])
        _dashed(dr, (u - span * e[0], v - span * e[1]), (u + span * e[0], v + span * e[1]), LINE_RGB)
        tx = min(max(u + 0.42 * im.width * e[0], 4), im.width - 70)
        ty = min(max(v + 0.42 * im.width * e[1] - 16, 2), im.height - 16)
        dr.text((tx, ty), "to " + ", ".join(ln["to"]), fill=LINE_RGB, font=_font(LABEL_FONT_PX))
    for e in pick.get("edges") or []:
        line = edge_line(e, SD_PX_MIN)
        far = max(abs(float((np.asarray(uv, float) - line["c"]) @ line["t"])), line["span"]) + 4.0
        a, b = to(line["c"] - far * line["t"]), to(line["c"] + far * line["t"])
        dr.line([a, b], fill=EDGE_RGB, width=1)
        for q in e:
            qu, qv = to(q)
            dr.ellipse([qu - 2.5, qv - 2.5, qu + 2.5, qv + 2.5], fill=EDGE_RGB)
    if cov is not None and pick.get("edges"):
        ev, evec = np.linalg.eigh(np.asarray(cov, float))
        ang = np.linspace(0, 2 * np.pi, 48)
        E = (evec @ (2.0 * np.sqrt(np.maximum(ev, 0.0))[:, None] * np.vstack([np.cos(ang), np.sin(ang)]))).T * k
        dr.line([(u + a, v + b) for a, b in E] + [(u + E[0, 0], v + E[0, 1])], fill=PICK_RGB, width=1)
    dr.ellipse([u - 7, v - 7, u + 7, v + 7], outline=PICK_RGB, width=2)
    fu, fv = to(uv_fit)
    for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1)):     # a cross with a gap: the read point's pixel stays visible
        dr.line([(fu + 4 * sx, fv + 4 * sy), (fu + 11 * sx, fv + 11 * sy)], fill=FIT_RGB, width=2)
    return clean, im, f"box [{x0}, {y0}, {x1}, {y1}] x{k:.2f}"


def pick_sheet(basis, rec: dict, out_path) -> Path:
    """The reading's pick sheet (module doc): a row per photo (the clean crop, then the drawn crop), each labelled with
    its photo, box, scale, residual and crossing angle; the title names the reading with d, y, X and their sd, and the
    legend. Returns the path."""
    n = len(rec["photos"])
    W = 2 * PANEL_W + 3 * GAP_PX
    Ht = TITLE_PX + n * (PANEL_H + LABEL_PX + GAP_PX) + GAP_PX
    sheet = Image.new("RGB", (W, Ht), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    dr.text((GAP_PX, 3), f"reading {rec['name']} ({rec['kind']}): d {fixed(rec['d'], 2)} +-{fixed(rec['sd_d'], 2)}, y "
                         f"{fixed(rec['y'], 2)} +-{fixed(rec['sd_y'], 2)}, X {fixed(rec['X'], 2)} +-"
                         f"{fixed(rec['sd_X'], 2)} mm", fill=(0, 0, 0), font=_font(TITLE_FONT_PX))
    dr.text((GAP_PX, 21), "left: the photo; right: pick green ring, traced edges cyan, read point magenta cross, "
                          "lines to the other photos orange dashed", fill=(0, 0, 0), font=_font(LABEL_FONT_PX))
    for i, (pid, rid) in enumerate(zip(rec["photos"], rec["rids"])):
        cov = (rec.get("uv_cov") or [None] * n)[i]
        clean, im, box = _panel(basis.photo(rid), rec["picks"][i], rec["uv"][i], rec["uv_fit"][i], cov,
                                (rec.get("lines") or [None] * n)[i])
        y = TITLE_PX + i * (PANEL_H + LABEL_PX + GAP_PX)
        for c, panel in enumerate((clean, im)):
            x = GAP_PX + c * (PANEL_W + GAP_PX)
            sheet.paste(panel, (x + (PANEL_W - panel.width) // 2, y + (PANEL_H - panel.height) // 2))
        cross = (f", edges cross at {fixed(rec['crossing_deg'][i], 1)}°" if rec.get("crossing_deg") else "")
        dr.text((GAP_PX, y + PANEL_H + 2), f"{pid}: {box}, residual {fixed(rec['res_px'][i], 1)} px "
                                           f"({fixed(rec['res_sd'][i], 1)} sd){cross}",
                fill=(0, 0, 0), font=_font(LABEL_FONT_PX))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
