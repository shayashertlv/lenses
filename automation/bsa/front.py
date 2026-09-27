"""S2 front partition: lens outlines, frame mask, symmetry axis, layout and rim class.

All pixel quantities are in the OUTLINE SOURCE's native pixels: the front photo, or the
horizontally mirrored back photo when the front glasses are < 600 px wide and the back is
larger (INVU). `source_to_front` (2x3, source px -> front px similarity) and the extra
arrays `lens{i}_poly_front` let later stages lift outlines through the FRONT camera.

Pipeline (DESIGN.md S2):
1. detector lens proposals (S0 `lens_<view>`), cleaned;
2. point-typed edge refinement along the smoothed proposal contour normals (profiles -5..+3 mm,
   so a detector that is off by up to ~3 mm can still be corrected):
   - frame-bounded points (the outside at +1.5..2.5 mm is matte), three steps:
     a. lens side: half-contrast crossing between the lens colour (-3.5..-2 mm) and the frame
        colour (c_in, "where the lens colour ends");
     b. lens-tinted band (front-photo source only): the crossing is carried outward across a
        band that keeps the LENS's chromaticity (a, b) / (L* + 16) (darkening-invariant in
        CIELAB above the linear toe). Physics: the lens seen over a dark frame part behind it
        (the groove's rear lip, a nose piece) is T * dark + r and keeps the lens's tint or
        its coating reflection colour, while frame material seen directly keeps the FRAME's
        chromaticity at any shading. Only a band wider than the chroma resolution counts
        (3 px: JPEG 4:2:0 chroma + resampling blur), and only for a tinted lens: a neutral
        lens cannot tell a band from a neutral groove, which stays frame;
     c. frame side: the rim's own structure (groove line, bevel, lit face; 1.5 mm outward of
        the edge) is registered against the median signature of the nearest confident
        neighbours on both sides of the ring. A point whose lens-side estimate is not a
        confident measurement (the lens window straddles the edge, the crossing sits at the
        window start, the offset is far from the lens's bevel, or no contrast) moves to the
        registered edge when that match is distinct and clearly better than its own
        estimate. This handles a temple or hinge seen THROUGH the lens near the rim (the
        lens-side colour is then the temple, not the lens) and a detector that bulges by
        several mm there. When the band between the lens estimate and the registered frame
        is backdrop (a lens vent/notch), the lens estimate stays and the point becomes free.
     Points without a measurement or a registration take the lens's median bevel offset and
     stay inside the bevel window (+/- 0.6 mm); measured/registered points do not;
   - free points (outside is backdrop, lens contrasts with it): the same half-contrast
     crossing, i.e. the 0.5 iso-contour of a linear matte between lens and backdrop;
   - rimless points (outside is backdrop and the lens looks like the backdrop): snap to the
     faint lens-edge ridge (Sato) with a smoothness-regularised dynamic programme, then a
     robust low-order Fourier fit of the offsets;
3. mirror symmetrisation (Gaussian sigma 0.3 % W). Lens: a pixel claimed by only one side
   is kept when it or its mirror twin looks like the lens (restores a logo/highlight
   under-inclusion from the clean twin, drops an over-inclusion into the frame). Matte:
   SDF average guarded so that where the sides disagree by > 0.5 mm the outer side wins
   (highlight notches);
   3b. non-lens carve (``carve_nonlens``, frame evidence), between the dispute and the SDF average: the
       refinement moves points along normals and cannot remove structure the detector swallowed whole (a
       centre stem joining the brow to the nose piece, an endpiece or nose pad over the lens edge). A region
       inside the lens that is not lens-coloured, frame-coloured and continuous with the frame (within reach of
       frame at least 1 mm thick outside the lens), at least CARVE_THICK_MM thick and NOT lens-tinted (the band
       rule's chromaticity test inverted: frame seen THROUGH a tinted lens keeps the lens's chromaticity) is
       removed. A clear lens (looks like the backdrop) or a neutral one cannot tell frame seen through it from
       frame in front of it and never carves. A stem splits a shield into a pair. With a back-mirrored source
       every carve must be confirmed by the FRONT photo (``front_confirm``): from behind, nose pads and endpieces
       sit between the camera and the lens, and where the front photo shows lens there, the lens continues
       under them;
   3c. vents (``exclude_backdrop``), after the Fourier step as a hard constraint (K = 24 rounds a 1 mm notch):
       ENCLOSED backdrop (an opening the glasses surround, not the open backdrop a free edge meets) inside a
       tinted lens's outline, with the frame lip that joins it to the outside, and its mirror image, are
       subtracted from the outline; the new boundary is typed free there;
4. arc-length Fourier outlines, K = 24 harmonics;
5. frame mask = symmetrised matte minus the lens polygons (no row band);
6. rim field w(theta) outward from each outline -> layout and rim class.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
from scipy import ndimage, optimize
from skimage import measure
from skimage.filters import sato

from . import core, intake
from .intake import (BAND_CHROMA_SEP, BAND_L_MIN, CARVE_CORE_MM, CARVE_EVIDENCE_MM2, CARVE_FRAME_SIGMA_MM,  # noqa: F401
                     CARVE_FRAME_TOL_MIN, CARVE_LENS_P, CARVE_MIN_LAB, CARVE_REF_SIGMA_MM, CARVE_RING_DEPTH_MM,
                     CARVE_RING_MM, CARVE_THICK_MM, _nconv, carve_nonlens, chromaticity)

STAGE = "s2_front"
FOURIER_K = 24
SYM_SIGMA_FRAC = 0.003
PROFILE_MM = (-5.0, 3.0, 0.1)        # normal profile range and step (mm); -5 mm reaches a ~3 mm detector bulge
LENS_REF_MM = (-3.5, -2.0)
OUT_REF_MM = (1.5, 2.5)
SEARCH_FROM_MM = -2.0
MIN_EDGE_CONTRAST = 10.0             # Lab distance lens <-> outside below which a point is low-contrast
OFFSET_CLIP_MM = 2.0                 # offsets of unvalidated points
VALIDATED_CLIP_MM = 4.0              # offsets of measured/registered frame points (profile reach - signature)
# lens-tinted band (step 2b); BAND_L_MIN and BAND_CHROMA_SEP live in intake (shared with the S0/S2 carve)
BAND_SKIP_PX = 3.0                   # chroma resolution: JPEG 4:2:0 (2 px) + ~1 px resampling blur
BAND_MAX_MM = 1.5                    # a band never extends the lens further (groove / nose-piece overlap depth)
# frame-side registration (step 2c)
REG_T_MM = 1.5                       # frame signature length outward of the edge
REG_K_SIDE = 12                      # confident neighbours per ring side for the template
REG_MAX_FRAC = 0.25                  # look for neighbours up to 25 % of the ring away on each side
REG_TRUNC = 30.0                     # per-sample Lab distance truncation (robust to highlights)
REG_TOL_MM = 0.3                     # lens estimate and registered edge agree within this
REG_DISTINCT = 0.5                   # a match must cost <= half the median cost over all offsets
RIDGE_RANGE_MM = 4.0
RIDGE_LAMBDA = 0.02
RIDGE_FOURIER_K = 6
RIM_PRESENT_FRAC_W = 0.01            # rim present at an angle when w > 1 % W
TYPE_FRAME, TYPE_FREE, TYPE_RIMLESS = 0, 1, 2
TYPE_NAMES = ("frame", "free", "rimless")
SOURCE_MIN_WIDTH_PX = 600
LENS_MIRROR_IOU_MIN = 0.90
LENS_SHARE_RANGE = (0.35, 0.95)
LOW_CONTRAST_MAX = 0.60             # share of points left on the bevel fallback (unmeasured) before flagging
RIDGE_SUPPORT_MIN = 0.5             # rimless: share of points with a visible edge ridge below which the outline is unsupported
RIDGE_SNR = 5.0                      # a visible ridge: >= 5 x the backdrop's ridge-response noise (its 99th percentile)
OUT_SPACING_MM = 0.25
FREE_BAND_MAX_MM = 1.0              # free edge: widest lens-edge band before it counts as a rim
SYM_GUARD_MM = 0.5                   # symmetrisation: disagreement beyond which the outer side wins
FREE_OUT_MAX_MM = 1.0                # free edges never move more than this outward of the proposal
FRAME_DEV_MM = 0.6                   # unvalidated frame-bounded offsets: lens median bevel +/- this
CLIPPED_SHARE_MAX = 0.15             # share of points held by the bevel window before flagging
# non-lens carve (steps 3b/3c): the CARVE_* policy lives in intake (S0 cuts lens openings with it)
CONFIRM_RING_MM = (2.0, 6.0)         # back-mirrored source: the front photo's lens reference ring around a mapped carve
CONFIRM_SPREAD_K = 3.0               # lens-coloured in the front photo: within max(CARVE_MIN_LAB, k x the ring's median spread)
CONFIRM_SHARE_MAX = 0.5              # a carve group is confirmed when less than this share of it is lens-coloured in the front
CARVE_TYPE_REACH_MM = 1.0            # a vertex whose outward normal meets a carved pixel within this is a carved boundary


# --------------------------------------------------------------------------- geometry helpers

def sdf(mask: np.ndarray) -> np.ndarray:
    """Signed distance in px, negative inside; the zero level lies on pixel boundaries."""
    m = np.asarray(mask, bool)
    if not m.any():
        return np.full(m.shape, 1e6, np.float32)
    return (ndimage.distance_transform_edt(~m) - ndimage.distance_transform_edt(m)).astype(np.float32)


def mirror_field(a: np.ndarray, x0: float, fill: float) -> np.ndarray:
    """Mirror a float field about x = x0; exact when 2 x0 is an integer (``partition`` snaps the axis so)."""
    W = a.shape[1]
    src = np.round(2.0 * x0 - np.arange(W)).astype(int)
    ok = (src >= 0) & (src < W)
    out = np.full_like(a, fill)
    out[:, ok] = a[:, src[ok]]
    return out


def signed_area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def poly_centroid(poly: np.ndarray) -> np.ndarray:
    x, y = poly[:, 0], poly[:, 1]
    cr = x * np.roll(y, -1) - np.roll(x, -1) * y
    a = cr.sum() / 2.0
    return np.array([((x + np.roll(x, -1)) * cr).sum() / (6 * a), ((y + np.roll(y, -1)) * cr).sum() / (6 * a)])


def resample_closed(poly: np.ndarray, n: int) -> np.ndarray:
    """Uniform arc-length resampling of a closed polyline (no repeated end point) to n points."""
    p = np.vstack([poly, poly[:1]])
    d = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
    t = np.linspace(0.0, d[-1], n, endpoint=False)
    return np.stack([np.interp(t, d, p[:, 0]), np.interp(t, d, p[:, 1])], 1)


def perimeter(poly: np.ndarray) -> float:
    p = np.vstack([poly, poly[:1]])
    return float(np.hypot(*np.diff(p, axis=0).T).sum())


def canonical_order(poly: np.ndarray) -> np.ndarray:
    """Positive shoelace area in (u, v) (clockwise on screen, v down), starting at the
    top-most vertex (min v, then min u)."""
    if signed_area(poly) < 0:
        poly = poly[::-1]
    k = int(np.lexsort((poly[:, 0], poly[:, 1]))[0])
    return np.roll(poly, -k, axis=0)


def fourier_outline(contour: np.ndarray, K: int = FOURIER_K, n_out: int | None = None, n_fit: int = 1024) -> np.ndarray:
    """Arc-length Fourier smoothing: keep harmonics -K..K of the complex contour."""
    z = resample_closed(contour, n_fit)
    zc = z[:, 0] + 1j * z[:, 1]
    F = np.fft.fft(zc)
    keep = np.zeros_like(F)
    keep[:K + 1] = F[:K + 1]
    keep[-K:] = F[-K:]
    zr = np.fft.ifft(keep)
    out = np.stack([zr.real, zr.imag], 1)
    if n_out is not None and n_out != n_fit:
        out = resample_closed(out, n_out)
    return out


def rasterize(polys: list[np.ndarray], shape: tuple[int, int], values: list[int] | None = None) -> np.ndarray:
    """Fill polygons (px, pixel-centre coordinates) into an int8 label image."""
    out = np.zeros(shape, np.uint8)
    for i, p in enumerate(polys):
        v = 1 if values is None else int(values[i])
        cv2.fillPoly(out, [np.round(np.asarray(p) * 16).astype(np.int32)], v, lineType=cv2.LINE_8, shift=4)
    return out.astype(np.int8)


def level_contours(field: np.ndarray, min_area: float = 0.0) -> list[np.ndarray]:
    """Closed zero-level contours (x, y) of a field that is negative inside, outer ones only,
    sorted by decreasing area."""
    cs = measure.find_contours(field, 0.0)
    out = []
    for c in cs:
        if len(c) < 8 or np.hypot(*(c[0] - c[-1])) > 1.5:
            continue
        p = c[:-1, ::-1].astype(np.float64)   # (row, col) -> (x, y)
        a = signed_area(p)
        # find_contours keeps the negative (inside) region on a fixed side; outer vs hole
        # is decided by the field at the centroid side rather than orientation conventions.
        if abs(a) < min_area:
            continue
        out.append(p)
    # drop holes: a contour whose interior sample is positive (outside)
    keep = []
    for p in out:
        m = rasterize([p], field.shape) > 0
        if m.any() and float(np.median(field[m])) < 0:
            keep.append(p)
    keep.sort(key=lambda q: -abs(signed_area(q)))
    return keep


def normals(poly: np.ndarray, smooth: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """Smoothed contour and unit outward normals (for positive shoelace area)."""
    cx = ndimage.gaussian_filter1d(poly[:, 0], smooth, mode="wrap")
    cy = ndimage.gaussian_filter1d(poly[:, 1], smooth, mode="wrap")
    tx, ty = np.gradient(cx), np.gradient(cy)
    n = np.stack([ty, -tx], 1)          # outward for positive shoelace area (clockwise on screen)
    if signed_area(np.stack([cx, cy], 1)) < 0:
        n = -n
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    return np.stack([cx, cy], 1), n


def contour_distance(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Symmetric mean and p95 distance (px) between two closed polylines."""
    from scipy.spatial import cKDTree
    A = resample_closed(a, max(64, int(perimeter(a))))
    B = resample_closed(b, max(64, int(perimeter(b))))
    da = cKDTree(B).query(A)[0]
    db = cKDTree(A).query(B)[0]
    return float((da.mean() + db.mean()) / 2), float(max(np.percentile(da, 95), np.percentile(db, 95)))


# --------------------------------------------------------------------------- lens proposals

def clean_lens(lens: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """Opening, holes filled, components >= 2 % of the proposal kept, clipped to the matte."""
    L = ndimage.binary_opening(lens, iterations=2)
    L = intake.keep_components(L, 0.0)
    lab, n = ndimage.label(L)
    if n:
        sizes = ndimage.sum_labels(L, lab, np.arange(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes >= 0.02 * sizes.sum()
        L = keep[lab]
    L = ndimage.binary_fill_holes(L) & ndimage.binary_fill_holes(fg)
    return L


def proposal_contours(L: np.ndarray, min_frac: float = 0.02) -> list[np.ndarray]:
    """Sub-pixel outer contours of the lens proposal components (0-level of a lightly smoothed
    SDF), resampled to ~1 px spacing, positive shoelace area."""
    field = ndimage.gaussian_filter(sdf(L), 1.0)
    total = float(L.sum())
    out = []
    for p in level_contours(field, min_area=min_frac * total):
        n = max(64, int(round(perimeter(p))))
        q = resample_closed(p, n)
        if signed_area(q) < 0:
            q = q[::-1]
        out.append(q)
    return out


# --------------------------------------------------------------------------- refinement

def _ridge_image(rgb: np.ndarray, bbox: tuple[int, int, int, int], fg: np.ndarray | None = None,
                 clear_px: float = 0.0) -> tuple:
    """Sato ridge response (dark and bright ridges) on the crop; with ``fg``, also the backdrop noise level of that
    response: its 99th percentile over crop pixels farther than ``clear_px`` from the foreground (JPEG noise on the
    studio backdrop), the reference a faint rimless edge ridge must stand above."""
    x0, y0, x1, y1 = bbox
    gray = cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY).astype(float) / 255.0
    r = np.maximum(sato(gray, sigmas=[1, 1.5, 2], black_ridges=True), sato(gray, sigmas=[1, 1.5, 2], black_ridges=False))
    if fg is None:
        return r, (x0, y0)
    back = ndimage.distance_transform_edt(~fg[y0:y1, x0:x1]) > clear_px
    noise = float(np.percentile(r[back], 99)) if back.sum() >= 100 else None
    return r, (x0, y0), noise


def _dp_path(cost: np.ndarray, lam: float) -> np.ndarray:
    """Min-cost path through a (points x offsets) cost table with |j - j'| smoothness."""
    m, K = cost.shape
    jj = np.arange(K)
    pen = lam * np.abs(jj[:, None] - jj[None, :])
    acc = cost[0].copy()
    back = np.zeros((m, K), int)
    for i in range(1, m):
        trans = acc[None, :] + pen
        back[i] = trans.argmin(1)
        acc = cost[i] + trans.min(1)
    path = np.zeros(m, int)
    path[-1] = int(acc.argmin())
    for i in range(m - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    return path


def _robust_fourier_1d(t: np.ndarray, y: np.ndarray, w: np.ndarray, K: int) -> np.ndarray:
    """Weighted robust (two-pass, worst 20 % dropped) Fourier series fit y(t), t in [0, 2 pi)."""
    A = [np.ones_like(t)]
    for k in range(1, K + 1):
        A += [np.cos(k * t), np.sin(k * t)]
    A = np.array(A).T
    sel = w > 0
    if sel.sum() < 2 * K + 4:
        return np.full_like(y, np.median(y[sel]) if sel.any() else 0.0)
    coef = np.linalg.lstsq(A[sel], y[sel], rcond=None)[0]
    r = np.abs(A @ coef - y)
    sel2 = sel & (r <= np.percentile(r[sel], 80))
    coef = np.linalg.lstsq(A[sel2], y[sel2], rcond=None)[0]
    return A @ coef


def _fill_ring(a: np.ndarray) -> np.ndarray:
    """Periodic linear interpolation over NaNs (all-NaN -> zeros)."""
    good = np.isfinite(a)
    if good.all():
        return a.copy()
    if not good.any():
        return np.zeros_like(a)
    idx = np.arange(len(a))
    return np.interp(idx, idx[good], a[good], period=len(a))


def band_extend(prof: np.ndarray, s_mm: np.ndarray, c_in: np.ndarray, out_sel: np.ndarray, inmatte: np.ndarray,
                skip_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """Lens-tinted band (module doc, step 2b). Per point with a finite ``c_in``: the lens chromaticity qL
    (median over [c_in - 1.0, c_in - 0.2] mm) and the frame chromaticity qF (the outside reference band).
    When the lens is tinted (|qL| >= BAND_CHROMA_SEP) and differs from the frame by as much, walk outward
    from c_in + ``skip_mm`` (beyond the chroma blur of the luminance step) while the samples are measurable
    (L* >= BAND_L_MIN, inside the matte) and more than half-way toward qL both from qF and from neutral; the
    edge becomes the interpolated half crossing. Returns (new c_in, extension mm)."""
    m = len(prof)
    q = chromaticity(prof)
    meas = prof[..., 0] >= BAND_L_MIN
    band_ok = meas & inmatte
    step = float(s_mm[1] - s_mm[0])
    out = c_in.copy()
    ext = np.zeros(m)
    for i in np.nonzero(np.isfinite(c_in))[0]:
        lw = (s_mm >= c_in[i] - 1.0) & (s_mm <= c_in[i] - 0.2) & meas[i]
        fw = out_sel & meas[i]
        if lw.sum() < 3:
            continue
        # a frame too dark to measure (black, L* < 8 over its reference band) has no chromaticity: neutral
        qL = np.median(q[i, lw], 0)
        qF = np.median(q[i, fw], 0) if fw.sum() >= 3 else np.zeros(2)
        ax = qL - qF
        d, dn = float(np.linalg.norm(ax)), float(np.linalg.norm(qL))
        if d < BAND_CHROMA_SEP or dn < BAND_CHROMA_SEP:
            continue
        proj = np.minimum(((q[i] - qF) @ ax) / (d * d), (q[i] @ qL) / (dn * dn))
        j = int(np.clip(np.searchsorted(s_mm, c_in[i] + skip_mm - 1e-9), 0, len(s_mm) - 1))
        last = None
        while j < len(s_mm) and s_mm[j] <= c_in[i] + BAND_MAX_MM:
            if not band_ok[i, j] or proj[j] < 0.5:
                break
            last = j
            j += 1
        if last is None:
            continue
        if j < len(s_mm) and band_ok[i, j] and proj[last] != proj[j]:
            e = s_mm[last] + np.clip((proj[last] - 0.5) / (proj[last] - proj[j]), 0, 1) * step
        else:
            e = s_mm[last] + 0.5 * step
        if e > c_in[i]:
            out[i] = e
            ext[i] = e - c_in[i]
    return out, ext


def _signature(prof_i: np.ndarray, s_mm: np.ndarray, e: float, n_t: int) -> np.ndarray:
    """Lab samples at e + t, t = 0, 0.1, ... (n_t samples), linear interpolation along one profile."""
    step = float(s_mm[1] - s_mm[0])
    x = (e + np.arange(n_t) * step - s_mm[0]) / step
    k = np.arange(len(s_mm))
    return np.stack([np.interp(x, k, prof_i[:, ch]) for ch in range(3)], -1)


def register_frame(prof: np.ndarray, s_mm: np.ndarray, e: np.ndarray, conf: np.ndarray) -> dict:
    """Frame-side registration (module doc, step 2c). For every point: the template is the per-sample
    median of the frame signatures (REG_T_MM outward of the edge) of the REG_K_SIDE nearest confident
    points on each side of the ring (within REG_MAX_FRAC of the ring; both sides required); the cost of an
    edge at offset delta is the mean truncated Lab distance between the profile at delta + t and the
    template. Returns per point: best offset, its cost, the cost at ``e`` and the median cost over all
    offsets (NaN where there is no template)."""
    m = len(prof)
    step = float(s_mm[1] - s_mm[0])
    n_t = int(round(REG_T_MM / step)) + 1
    conf_idx = np.nonzero(conf)[0]
    out = {k: np.full(m, np.nan) for k in ("best", "cost_best", "cost_at_e", "cost_median")}
    if len(conf_idx) < 2 * REG_K_SIDE:
        return out
    sigs = np.full((m, n_t, 3), np.nan)
    for i in conf_idx:
        sigs[i] = _signature(prof[i], s_mm, e[i], n_t)
    deltas = s_mm[:len(s_mm) - n_t + 1]
    windows = np.arange(len(deltas))[:, None] + np.arange(n_t)[None]
    reach = int(REG_MAX_FRAC * m)
    for i in range(m):
        fwd = (conf_idx - i) % m
        bwd = (i - conf_idx) % m
        f_sel = conf_idx[(fwd > 0) & (fwd <= reach)]
        b_sel = conf_idx[(bwd > 0) & (bwd <= reach)]
        f_sel = f_sel[np.argsort((f_sel - i) % m, kind="stable")][:REG_K_SIDE]
        b_sel = b_sel[np.argsort((i - b_sel) % m, kind="stable")][:REG_K_SIDE]
        if len(f_sel) < REG_K_SIDE // 2 or len(b_sel) < REG_K_SIDE // 2:
            continue
        tmpl = np.median(sigs[np.r_[f_sel, b_sel]], 0)
        cost = np.minimum(np.linalg.norm(prof[i][windows] - tmpl[None], axis=-1), REG_TRUNC).mean(1)
        k = int(np.argmin(cost))
        out["best"][i] = deltas[k]
        out["cost_best"][i] = cost[k]
        out["cost_median"][i] = float(np.median(cost))
        if np.isfinite(e[i]):
            sig = _signature(prof[i], s_mm, e[i], n_t)
            out["cost_at_e"][i] = float(np.minimum(np.linalg.norm(sig - tmpl, axis=-1), REG_TRUNC).mean())
    return out


def refine_contour(contour: np.ndarray, lab: np.ndarray, fg: np.ndarray, mm_px: float,
                   ridge: tuple[np.ndarray, tuple[int, int]] | None = None,
                   bg_lab: np.ndarray | None = None, band: bool = True) -> dict:
    """Point-typed refinement of one lens proposal contour (module doc, step 2). Returns points, types,
    offsets (mm, + = outward), per-point evidence and diagnostics. `fg` is the pure contrast matte;
    `bg_lab` the S0 backdrop model (Lab): a point whose outside is neither matte nor backdrop (a specular
    notch in a rim) is frame-bounded but unmeasurable. `band` enables the lens-tinted band rule (front-photo
    source: everything on the lens side of the edge is seen THROUGH the lens)."""
    c, n = normals(contour)
    step = PROFILE_MM[2]
    s_mm = np.arange(PROFILE_MM[0], PROFILE_MM[1] + 1e-9, step)
    s_px = s_mm / mm_px
    X = c[:, 0:1] + s_px[None] * n[:, 0:1]
    Y = c[:, 1:2] + s_px[None] * n[:, 1:2]
    prof = np.stack([ndimage.map_coordinates(lab[..., ch], [Y, X], order=1, mode="nearest") for ch in range(3)], -1)
    fgp = ndimage.map_coordinates(fg.astype(np.float32), [Y, X], order=1, mode="constant") > 0.5
    lens_sel = (s_mm >= LENS_REF_MM[0]) & (s_mm <= LENS_REF_MM[1])
    out_sel = (s_mm >= OUT_REF_MM[0]) & (s_mm <= OUT_REF_MM[1])
    lens_ref = np.median(prof[:, lens_sel], 1)
    out_ref = np.median(prof[:, out_sel], 1)
    d = np.linalg.norm(prof - lens_ref[:, None], axis=-1)
    d_out = np.linalg.norm(out_ref - lens_ref, axis=-1)
    out_fg = fgp[:, out_sel].mean(1)
    m = len(c)
    types = np.where(out_fg >= 0.5, TYPE_FRAME, TYPE_FREE).astype(np.int8)
    notch = np.zeros(len(c), bool)
    if bg_lab is not None:
        bgp = np.stack([ndimage.map_coordinates(bg_lab[..., ch], [Y[:, out_sel], X[:, out_sel]], order=1, mode="nearest")
                        for ch in range(3)], -1)
        not_backdrop = np.linalg.norm(out_ref - np.median(bgp, 1), axis=-1) > MIN_EDGE_CONTRAST
        notch = (types == TYPE_FREE) & not_backdrop
        types[notch] = TYPE_FRAME
    types[(types == TYPE_FREE) & (d_out < MIN_EDGE_CONTRAST)] = TYPE_RIMLESS
    search = s_mm >= SEARCH_FROM_MM
    inner = s_mm <= OUT_REF_MM[0]
    # edge contrast of a frame-bounded point: the lens <-> frame reference contrast, or, when the frame's lit
    # face happens to look like the lens (a mottled tortoise rim), the strongest departure from the lens
    # colour next to the edge (the groove / bevel between them), so a clear step there is still measured
    near = search & inner
    contrast = np.where(d_out >= MIN_EDGE_CONTRAST, d_out, np.max(np.where(near[None], d, 0.0), 1))
    low = (types == TYPE_FRAME) & ((contrast < MIN_EDGE_CONTRAST) | ~fgp[:, out_sel].all(1) | notch)
    off = np.full(m, np.nan)
    c_in = np.full(m, np.nan)
    c_out = np.full(m, np.nan)
    d_o = np.linalg.norm(prof - out_ref[:, None], axis=-1)
    for i in np.nonzero((types != TYPE_RIMLESS) & ~low)[0]:
        half = 0.5 * (contrast[i] if types[i] == TYPE_FRAME else d_out[i])
        # c_in: first departure from the lens colour, searching outward from -2 mm
        cand = np.nonzero(search & (d[i] > half))[0]
        if len(cand):
            j = cand[0]
            if j > 0 and d[i, j] != d[i, j - 1]:
                f = (half - d[i, j - 1]) / (d[i, j] - d[i, j - 1])
                c_in[i] = s_mm[j - 1] + np.clip(f, 0, 1) * step
            else:
                c_in[i] = s_mm[j]
        # c_out: first departure from the outside colour, searching inward from +1.5 mm
        cand = np.nonzero(inner & search & (d_o[i] > half))[0]
        if len(cand):
            j = cand[-1]
            if j + 1 < len(s_mm) and d_o[i, j] != d_o[i, j + 1]:
                f = (half - d_o[i, j + 1]) / (d_o[i, j] - d_o[i, j + 1])
                c_out[i] = s_mm[j + 1] - np.clip(f, 0, 1) * step
            else:
                c_out[i] = s_mm[j]
    c_in_lum = c_in.copy()
    ext = np.zeros(m)
    if band:
        fr0 = types == TYPE_FRAME
        ci2, ext = band_extend(prof, s_mm, np.where(fr0, c_in, np.nan), out_sel, fgp, BAND_SKIP_PX * mm_px)
        c_in = np.where(fr0 & np.isfinite(ci2), ci2, c_in)
    # free edges: the matte's 0.5 iso-contour, i.e. the crossing seen from the backdrop side
    free = types == TYPE_FREE
    # a band wider than FREE_BAND_MAX_MM between the lens colour and the backdrop is a rim
    # whose outside was not matted (a white specular highlight): frame-bounded, use c_in
    rim_band = free & np.isfinite(c_in) & np.isfinite(c_out) & ((c_out - c_in) > FREE_BAND_MAX_MM)
    types[rim_band] = TYPE_FRAME
    free = types == TYPE_FREE
    off[free] = np.where(np.isfinite(c_out[free]), c_out[free], c_in[free])
    # frame-bounded edges: the lens ends where the lens colour (or its tinted band) ends
    low_pure = low.copy()
    fr = types == TYPE_FRAME
    off[fr & ~low] = c_in[fr & ~low]
    # --- frame-side registration (step 2c)
    e = np.where(fr & ~low, c_in, np.nan)
    measured = np.isfinite(e)
    at_start = np.abs(c_in_lum - SEARCH_FROM_MM) < 1.5 * step      # crossing at the window start: the edge may lie further in
    mid = 0.5 * (LENS_REF_MM[0] + LENS_REF_MM[1])
    a_sel = (s_mm >= LENS_REF_MM[0]) & (s_mm < mid)
    b_sel = (s_mm >= mid) & (s_mm <= LENS_REF_MM[1])
    split = np.linalg.norm(np.median(prof[:, a_sel], 1) - np.median(prof[:, b_sel], 1), axis=-1)
    straddle = split >= 0.5 * contrast                                # the lens window crosses an edge
    conf = measured & ~at_start & ~straddle
    if conf.sum() >= 10:
        conf &= np.abs(e - float(np.median(e[conf]))) <= FRAME_DEV_MM  # a normal offset for this lens
    for i in np.nonzero(conf)[0]:
        sel = (s_mm >= e[i] + 0.15) & (s_mm <= e[i] + REG_T_MM)
        conf[i] = bool(fgp[i, sel].all())                              # the frame signature is frame, not backdrop
    reg = register_frame(prof, s_mm, e, conf)
    cc = reg["cost_at_e"][conf & np.isfinite(reg["cost_at_e"])]
    # a confident point's own mismatch to its neighbours' template: robust upper level (median + 3 sigma)
    if len(cc) >= 20:
        med = float(np.median(cc))
        good = med + 3.0 * 1.4826 * float(np.median(np.abs(cc - med)))
    else:
        good = np.inf
    validated = np.zeros(m, bool)
    moved = np.zeros(m, bool)
    vent = np.zeros(m, bool)
    for i in np.nonzero(fr & np.isfinite(reg["best"]))[0]:
        b, cb, ce, cm = reg["best"][i], reg["cost_best"][i], reg["cost_at_e"][i], reg["cost_median"][i]
        if conf[i] or (measured[i] and abs(e[i] - b) <= REG_TOL_MM):
            validated[i] = True
            continue
        distinct = cb <= REG_DISTINCT * cm
        # move only when the frame match is distinct and beats the lens-side estimate by more than a confident
        # point's own mismatch (or, without a lens-side estimate, when the match is as good as that level x 2)
        if measured[i] and np.isfinite(ce):
            ok = distinct and (ce - cb) >= good
        else:
            ok = distinct and cb <= 2.0 * good
        if not ok:
            continue
        if measured[i] and b > e[i]:
            gap = (s_mm > e[i] + step) & (s_mm < b - step)
            if gap.any() and fgp[i, gap].mean() < 0.5:
                # backdrop between the lens edge and the frame: a vent / notch; the lens edge is free there
                vent[i] = validated[i] = True
                types[i] = TYPE_FREE
                continue
        off[i] = b
        moved[i] = validated[i] = True
    low = (types == TYPE_FRAME) & ~validated & (low | ~np.isfinite(off))
    good_frame = (types == TYPE_FRAME) & ~low & np.isfinite(off) & ~rim_band
    bevel = float(np.median(off[good_frame])) if good_frame.sum() >= 10 else None
    shift = np.abs(off[moved] - np.where(np.isfinite(e[moved]), e[moved], 0.0))
    diag = {"points": int(m), "low_contrast": int(low_pure.sum()), "notch": int(notch.sum()), "rim_band": int(rim_band.sum()),
            "good_frame": int(good_frame.sum()), "median_bevel_offset_mm": None if bevel is None else round(bevel, 3),
            "band_extended": int((ext > 0).sum()),
            "band_extension_median_mm": round(float(np.median(ext[ext > 0])), 3) if (ext > 0).any() else 0.0,
            "confident": int(conf.sum()), "registration_validated": int(validated.sum()),
            "registration_moved": int(moved.sum()), "registration_moved_median_mm": round(float(np.median(shift)), 3) if moved.any() else 0.0,
            "vent": int(vent.sum()), "confident_mismatch_level": None if not np.isfinite(good) else round(good, 2),
            "bevel_fallback_points": int(low.sum())}
    # rimless: ridge snap (DP over the ring, rimless points only carry cost), then robust Fourier
    rim_sel = types == TYPE_RIMLESS
    ridge_support = None
    if rim_sel.any() and ridge is not None:
        R, (ox, oy) = ridge[:2]
        noise = ridge[2] if len(ridge) > 2 else None
        rs_mm = np.arange(-RIDGE_RANGE_MM, RIDGE_RANGE_MM + 1e-9, step)
        rs_px = rs_mm / mm_px
        RX = c[:, 0:1] + rs_px[None] * n[:, 0:1] - ox
        RY = c[:, 1:2] + rs_px[None] * n[:, 1:2] - oy
        Rv = ndimage.map_coordinates(R, [RY, RX], order=1, mode="constant")
        cost = -Rv / (Rv.max() + 1e-12)
        cost[~rim_sel] = 0.0
        path = _dp_path(cost, RIDGE_LAMBDA)
        roff = rs_mm[path]
        strength = Rv[np.arange(m), path]
        # support: the share of rimless points whose snapped ridge stands RIDGE_SNR x above the backdrop's own ridge
        # noise (a visible edge line), not above the crop's p90 (which the hardware and temples dominate)
        if noise is not None:
            thr = RIDGE_SNR * noise
        else:
            thr = np.percentile(R[R > 0], 90) if (R > 0).any() else 0.0
        ridge_support = float((strength[rim_sel] > thr).mean())
        t = np.linspace(0, 2 * np.pi, m, endpoint=False)
        fit = _robust_fourier_1d(t, roff, rim_sel.astype(float), RIDGE_FOURIER_K)
        off[rim_sel] = np.clip(fit[rim_sel], -RIDGE_RANGE_MM, RIDGE_RANGE_MM)
    diag["ridge_support"] = None if ridge_support is None else round(ridge_support, 3)
    diag["bevel_fallback"] = bevel is None
    return {"c": c, "n": n, "types": types, "low": low, "low_pure": low_pure, "off": off, "bevel": bevel,
            "diag": diag, "c_in": c_in, "c_in_luminance": c_in_lum, "band_ext": ext, "validated": validated,
            "moved": moved, "vent": vent, "confident": conf, "registration": reg}


def apply_mirror_types(refs: list[dict], x0: float, mm_px: float) -> int:
    """A free point whose mirror twin (nearest mirrored refined point within 2 mm, over all
    lenses) is frame-bounded becomes frame-bounded: an unmatted translucent or highlighted rim
    on one side must not let the lens leak into it. Its offset becomes c_in (bounded later by
    the bevel window) or the bevel fallback. Returns the number of retyped points."""
    from scipy.spatial import cKDTree
    pts = np.vstack([r["c"] for r in refs])
    typ = np.concatenate([r["types"] for r in refs])
    mir = np.stack([2 * x0 - pts[:, 0], pts[:, 1]], 1)
    tree = cKDTree(mir)
    n = 0
    for r in refs:
        free = np.nonzero(r["types"] == TYPE_FREE)[0]
        if len(free) == 0:
            continue
        d, j = tree.query(r["c"][free])
        hit = (d * mm_px <= 2.0) & (typ[j] == TYPE_FRAME)
        k = free[hit]
        r["types"][k] = TYPE_FRAME
        ok = np.isfinite(r["c_in"][k])
        r["off"][k[ok]] = r["c_in"][k[ok]]
        r["low"][k[~ok]] = True
        r["diag"]["mirror_retyped"] = int(len(k))
        n += int(len(k))
    return n


def finish_offsets(r: dict, bevel: float, mm_px: float) -> np.ndarray:
    """Low-contrast points take the bevel offset; gaps are interpolated around the ring;
    median (15) + Gaussian (2) smoothing; clip. Returns the refined polygon (px)."""
    off = r["off"].copy()
    off[r["low"]] = bevel
    # unvalidated frame-bounded offsets stay within the lens's bevel +/- FRAME_DEV_MM (the detector's
    # over-inclusion is a consistent per-lens bias, so an unconfirmed larger excursion is suspect);
    # confident measurements and registered points are exempt: the rim's own structure confirms them
    val = r.get("validated", np.zeros(len(off), bool))
    fr = (r["types"] == TYPE_FRAME) & np.isfinite(off) & ~val
    r["frame_clipped"] = int((fr & (np.abs(off - bevel) > FRAME_DEV_MM)).sum())
    off[fr] = np.clip(off[fr], bevel - FRAME_DEV_MM, bevel + FRAME_DEV_MM)
    good = np.isfinite(off)
    m = len(off)
    if good.sum() < 3:
        off[:] = bevel
    elif not good.all():
        idx = np.arange(m)
        off = np.interp(idx, idx[good], off[good], period=m)
    off = ndimage.median_filter(off, size=15, mode="wrap")
    off = ndimage.gaussian_filter1d(off, 2, mode="wrap")
    lim = np.where(r["types"] == TYPE_RIMLESS, RIDGE_RANGE_MM, np.where(val, VALIDATED_CLIP_MM, OFFSET_CLIP_MM))
    hi = np.where(r["types"] == TYPE_FREE, FREE_OUT_MAX_MM, lim)
    off = np.clip(off, -lim, hi)
    r["off_final"] = off
    return r["c"] + (off / mm_px)[:, None] * r["n"]


# --------------------------------------------------------------------------- registration

def _bbox(mask):
    ys, xs = np.nonzero(mask)
    return xs.min(), ys.min(), xs.max(), ys.max()


def register_similarity(src_mask: np.ndarray, dst_mask: np.ndarray) -> tuple[np.ndarray, float]:
    """Similarity (scale + translation, no rotation) mapping src px -> dst px that maximises
    the IoU of the warped src mask with dst, from a bounding-box start. Returns (2x3, IoU)."""
    sx0, sy0, sx1, sy1 = _bbox(src_mask)
    dx0, dy0, dx1, dy1 = _bbox(dst_mask)
    s0 = ((dx1 - dx0 + 1) / (sx1 - sx0 + 1) + (dy1 - dy0 + 1) / (sy1 - sy0 + 1)) / 2
    tx0 = (dx0 + dx1) / 2 - s0 * (sx0 + sx1) / 2
    ty0 = (dy0 + dy1) / 2 - s0 * (sy0 + sy1) / 2
    H, W = dst_mask.shape
    src_f = src_mask.astype(np.float32)
    # evaluate on a dst grid no larger than 700 px (speed); scale the transform accordingly
    g = min(1.0, 700.0 / max(H, W))
    dst_g = cv2.resize(dst_mask.astype(np.uint8), (max(1, int(W * g)), max(1, int(H * g))), interpolation=cv2.INTER_AREA) > 0

    def warp(p, grid=g, shape=dst_g.shape):
        s, tx, ty = np.exp(p[0]) * s0, tx0 + p[1], ty0 + p[2]
        M = np.array([[s * grid, 0, tx * grid + 0.5 * grid - 0.5], [0, s * grid, ty * grid + 0.5 * grid - 0.5]], np.float32)
        return cv2.warpAffine(src_f, M, (shape[1], shape[0]), flags=cv2.INTER_LINEAR) > 0.5

    def loss(p):
        w = warp(p)
        return -float((w & dst_g).sum() / max(1, (w | dst_g).sum()))

    scale_px = (dx1 - dx0) * 0.02
    res = optimize.minimize(loss, np.zeros(3), method="Powell",
                            options={"xtol": 1e-3, "ftol": 1e-5, "maxfev": 600,
                                     "direc": np.diag([0.02, scale_px, scale_px])})
    p = res.x
    s, tx, ty = np.exp(p[0]) * s0, tx0 + p[1], ty0 + p[2]
    M = np.array([[s, 0, tx], [0, s, ty]])
    w = cv2.warpAffine(src_f, M.astype(np.float32), (W, H), flags=cv2.INTER_LINEAR) > 0.5
    return M, intake.iou(w, dst_mask)


def apply_affine(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ np.asarray(M)[:, :2].T + np.asarray(M)[:, 2]


def invert_affine(M: np.ndarray) -> np.ndarray:
    A = np.asarray(M)[:, :2]
    Ai = np.linalg.inv(A)
    return np.hstack([Ai, -(Ai @ np.asarray(M)[:, 2])[:, None]])


# --------------------------------------------------------------------------- partition

def _symmetrise(field_a: np.ndarray, x0: float, sigma: float, tau: float = 0.0) -> np.ndarray:
    """SDF-averaged mirror symmetrisation with a one-sided-defect guard: where the field and
    its mirror disagree by more than `tau` px, the outer side wins (continuous blend
    avg - 0.5 max(0, |a - b| - tau)); tau = 0 reduces to a plain union guard off."""
    big = float(max(field_a.shape))
    b = mirror_field(field_a, x0, big)
    f = (field_a + b) / 2.0
    if tau > 0:
        f = f - 0.5 * np.maximum(0.0, np.abs(field_a - b) - tau)
    return ndimage.gaussian_filter(f, sigma)


def resolve_mirror_dispute(L: np.ndarray, x0: float, lab: np.ndarray, mm_px: float) -> np.ndarray:
    """Symmetric lens mask from a (possibly one-sided defective) lens mask.

    Pixels claimed by both the mask and its mirror are lens. A pixel claimed by only one
    side is lens when it or its mirror twin looks like the lens: Lab distance to the lens's
    median colour below max(15, p90 of the lens interior). A logo or highlight on one lens
    (under-inclusion) is restored from the clean twin; an over-inclusion into the frame is
    dropped because neither twin looks like lens. The result is exactly mirror symmetric."""
    M = mirror_field(L.astype(np.float32), x0, 0.0) > 0.5
    inter = L & M
    disputed = (L | M) & ~inter
    if not disputed.any():
        return inter
    core_px = max(1, int(round(1.0 / mm_px)))
    interior = ndimage.binary_erosion(inter, iterations=core_px)
    if interior.sum() < 50:
        return L | M
    med = np.median(lab[interior], 0)
    dist = np.linalg.norm(lab - med, axis=-1)
    thr = max(15.0, float(np.percentile(dist[interior], 90)))
    like = dist < thr
    like_m = like | (mirror_field(like.astype(np.float32), x0, 0.0) > 0.5)
    return inter | (disputed & like_m)


def front_confirm(C: np.ndarray, cls: np.ndarray, confirm: dict | None, x0: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """Back-mirrored source (module doc, steps 3b/3c): the back photo sees the lens from BEHIND, where nose pads,
    endpieces and hinges sit between the camera and the lens. Structure seen over the lens from behind is not lens
    only where the FRONT photo also shows it (a centre stem, a nose-piece arm); where the front photo shows lens it is
    behind the lens and the lens continues under it. Each carve group (a component with the components overlapping
    its mirror image, decided together so the SDF average stays symmetric) is mapped into the front photo with
    ``confirm["M"]`` (source px -> front px) and kept when less than CONFIRM_SHARE_MAX of it is lens-coloured there:
    Lab distance to the median front colour of the lens proposal core in a ring CONFIRM_RING_MM around it within
    max(CARVE_MIN_LAB, CONFIRM_SPREAD_K x that ring's own median spread) (a ring that straddles frame or a gradient
    widens the tolerance: an uncertain confirmation keeps the lens). An opening (class 1) continues beyond its carve
    (its part outside the proposal is only marked): it stays with a confirmed carve it is connected to and goes with a
    dropped one. ``confirm`` None (front source): unchanged."""
    info = {"groups": 0, "kept": 0, "dropped": 0, "shares": []}
    if confirm is None or not C.any():
        return C, cls, info
    labf, Lf, M, mmf = confirm["lab"], confirm["lens"], np.asarray(confirm["M"], float), float(confirm["mm_px"])
    Hf, Wf = Lf.shape
    core_f = ndimage.binary_erosion(Lf, iterations=max(1, int(round(1.0 / mmf))))
    comp, n = ndimage.label(C, np.ones((3, 3), int))
    mir = np.round(mirror_field(comp.astype(np.float32), x0, 0.0)).astype(int)
    parent = list(range(n + 1))                    # union-find: components overlapping each other's mirror image

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    both = (comp > 0) & (mir > 0)
    for a, b in np.unique(np.stack([comp[both], mir[both]], 1), axis=0) if both.any() else []:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(k) for k in range(n + 1)])
    keep_lab = np.zeros(n + 1, bool)
    for r in np.unique(roots[1:]):
        members = np.nonzero(roots == r)[0]
        members = members[members > 0]
        yy, xx = np.nonzero(np.isin(comp, members))
        Q = np.stack([xx, yy], 1).astype(np.float64) @ M[:, :2].T + M[:, 2]
        u = np.round(Q[:, 0]).astype(int)
        v = np.round(Q[:, 1]).astype(int)
        ok = (u >= 0) & (u < Wf) & (v >= 0) & (v < Hf)
        fm = np.zeros((Hf, Wf), bool)
        fm[v[ok], u[ok]] = True
        fm = ndimage.binary_closing(fm, iterations=1) | fm
        dist = ndimage.distance_transform_edt(~fm) * mmf
        ring = core_f & (dist >= CONFIRM_RING_MM[0]) & (dist <= CONFIRM_RING_MM[1])
        if ring.sum() < 20 or not fm.any():
            share = 1.0                                   # no front evidence: keep the lens
        else:
            ref = np.median(labf[ring], 0)
            spread = float(np.median(np.linalg.norm(labf[ring] - ref, axis=-1)))
            tol = max(CARVE_MIN_LAB, CONFIRM_SPREAD_K * spread)
            share = float((np.linalg.norm(labf[fm] - ref, axis=-1) <= tol).mean())
        info["groups"] += 1
        info["shares"].append(round(share, 3))
        if share < CONFIRM_SHARE_MAX:
            keep_lab[members] = True
    keep_lab[0] = False
    Ck = keep_lab[comp]
    info["kept"] = int(sum(1 for x in info["shares"] if x < CONFIRM_SHARE_MAX))
    info["dropped"] = info["groups"] - info["kept"]
    info["dropped_px"] = int((C & ~Ck).sum())
    cls_k = np.where(Ck, cls, 0).astype(np.int8)
    # an opening (class 1) extends beyond the carve (its part outside the lens proposal is not carved, only marked):
    # it stays with the confirmed carve it is connected to, and goes with a dropped one
    op = np.asarray(cls) == 1
    if op.any() and Ck.any():
        lab_op, _ = ndimage.label(op | Ck, np.ones((3, 3), int))
        ids = np.unique(lab_op[Ck])
        cls_k[op & np.isin(lab_op, ids[ids > 0])] = 1
    info["opening_px_kept"] = int((cls_k == 1).sum())
    return Ck, cls_k, info


def exclude_backdrop(lenses: list[tuple[str, np.ndarray]], shape: tuple[int, int], lab: np.ndarray, matte: np.ndarray,
                     bg_lab: np.ndarray | None, mm_px: float, x0: float,
                     confirm: dict | None = None) -> tuple[list, np.ndarray, dict]:
    """Step 3c: backdrop inside the smoothed lens outlines (a brow vent between the lens's top edge and the brow; a
    tinted lens cannot look like the backdrop) is removed as a hard constraint AFTER the Fourier step: the carve
    (``carve_nonlens`` with backdrop evidence, together with the black lip that joins it to the outside) and its mirror
    image are subtracted from the outline (shapely difference; the outline is unchanged away from the carve), the
    largest part is kept and resampled to the same vertex count. A pair stays an exact mirror (L = mirror of R).
    Returns (lenses, class map, info)."""
    import shapely
    from shapely.geometry import Polygon
    Lm = rasterize([p for _, p in lenses], shape) > 0
    C, cls, info = carve_nonlens(Lm, lab, matte, bg_lab, mm_px, evidence=("backdrop",))
    C, cls, cf = front_confirm(C, cls, confirm, x0)
    info = dict(info, parts_dropped=0, dropped_mm2=0.0, front_confirm=cf)
    if not C.any():
        return lenses, cls, info
    mir = np.round(mirror_field(cls.astype(np.float32), x0, 0.0)).astype(np.int8)
    cls = np.where(cls > 0, cls, mir).astype(np.int8)
    # the carve (vent inside R + the lip that joins it to the outside) and the whole opening, on both sides
    C = C | (cls == 1)
    Cs = C | (mirror_field(C.astype(np.float32), x0, 0.0) > 0.5)
    G = shapely.union_all([shapely.make_valid(Polygon(c)) for c in level_contours(ndimage.gaussian_filter(sdf(Cs), 1.0))])
    dist_c = ndimage.distance_transform_edt(~ndimage.binary_dilation(Cs, iterations=1))
    out = []
    for side, P in lenses:
        if side == "L":
            continue
        D = shapely.make_valid(Polygon(P)).difference(G)
        parts = sorted([g for g in shapely.get_parts(D) if g.geom_type == "Polygon"], key=lambda g: -g.area)
        if not parts:
            out.append((side, P))
            continue
        info["parts_dropped"] += len(parts) - 1
        info["dropped_mm2"] = round(info["dropped_mm2"] + float(sum(g.area for g in parts[1:])) * mm_px * mm_px, 3)
        Q = canonical_order(resample_closed(canonical_order(np.asarray(parts[0].exterior.coords, np.float64)[:-1]), len(P)))
        # the carved stretch follows a pixel mask: smooth it along the ring (the outline elsewhere stays exact)
        d_c = ndimage.map_coordinates(dist_c, [Q[:, 1], Q[:, 0]], order=1, mode="nearest")
        w = np.clip(1.0 - d_c / 3.0, 0.0, 1.0)[:, None]
        Qs = np.stack([ndimage.gaussian_filter1d(Q[:, k], 2.0, mode="wrap") for k in (0, 1)], 1)
        out.append((side, canonical_order(Q + w * (Qs - Q))))
    if any(side == "L" for side, _ in lenses):
        R = out[0][1]
        Lp = R.copy()
        Lp[:, 0] = 2 * x0 - Lp[:, 0]
        out.append(("L", canonical_order(Lp)))
    return out, cls, info


def carve_types(poly: np.ndarray, types: np.ndarray, cls: np.ndarray, x0: float, reach_px: float) -> tuple[np.ndarray, int]:
    """Vertices on a carved boundary (step 3b) take the carve's class: the first carved pixel along the outward normal
    within ``reach_px`` (the carve and its mirror image: the outline is the SDF average of both sides) decides free
    (backdrop, class 1) or frame-bounded (class 2). Returns (types, number of vertices typed so)."""
    if not (cls > 0).any():
        return types, 0
    sym = cls.copy()
    mir = np.round(mirror_field(cls.astype(np.float32), x0, 0.0)).astype(np.int8)
    sym[sym == 0] = mir[sym == 0]
    _, n = normals(poly, smooth=2.0)
    steps = np.arange(0.5, max(1.0, reach_px) + 1e-9, 0.5)
    X = np.round(poly[:, 0:1] + steps[None] * n[:, 0:1]).astype(int)
    Y = np.round(poly[:, 1:2] + steps[None] * n[:, 1:2]).astype(int)
    H, W = cls.shape
    ok = (X >= 0) & (X < W) & (Y >= 0) & (Y < H)
    v = np.zeros(X.shape, np.int8)
    v[ok] = sym[Y[ok], X[ok]]
    hit = (v > 0).any(1)
    first = v[np.arange(len(poly)), np.argmax(v > 0, axis=1)]
    out = types.copy()
    out[hit & (first == 1)] = TYPE_FREE
    out[hit & (first == 2)] = TYPE_FRAME
    return out, int(hit.sum())


def _mode_filter_ring(t: np.ndarray, win: int) -> np.ndarray:
    m = len(t)
    if win < 3 or m < win:
        return t.copy()
    h = win // 2
    idx = (np.arange(m)[:, None] + np.arange(-h, h + 1)[None]) % m
    votes = np.stack([(t[idx] == k).sum(1) for k in range(3)], 1)
    out = votes.argmax(1).astype(np.int8)
    tie = votes.max(1) == votes[np.arange(m), t]
    out[tie] = t[tie]
    return out


def _types_for(poly: np.ndarray, refs: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """Per-vertex type: the nearest refined point of each reference set votes; frame beats
    free beats rimless (a highlight notch on one side must not open the rim)."""
    from scipy.spatial import cKDTree
    votes = []
    for pts, types in refs:
        if len(pts) == 0:
            continue
        _, j = cKDTree(pts).query(poly)
        votes.append(types[j])
    if not votes:
        return np.zeros(len(poly), np.int8)
    return np.min(np.stack(votes, 0), 0).astype(np.int8)


def rim_widths(poly: np.ndarray, frame: np.ndarray, max_px: float) -> np.ndarray:
    """Outward run length (px) of the frame mask along each vertex normal (first exit)."""
    _, n = normals(poly, smooth=2.0)
    steps = np.arange(0.5, max_px, 0.5)
    X = poly[:, 0:1] + steps[None] * n[:, 0:1]
    Y = poly[:, 1:2] + steps[None] * n[:, 1:2]
    H, W = frame.shape
    inside = (X >= 0) & (X <= W - 1) & (Y >= 0) & (Y <= H - 1)
    v = np.zeros_like(X, bool)
    v[inside] = frame[np.round(Y[inside]).astype(int), np.round(X[inside]).astype(int)]
    # allow the first 1.5 px to be lens/antialias before the rim starts
    start = 3
    run = np.zeros(len(poly))
    for i in range(len(poly)):
        row = v[i]
        j0 = np.argmax(row[:start + 1]) if row[:start + 1].any() else None
        if j0 is None:
            continue
        k = np.argmin(row[j0:]) if not row[j0:].all() else len(row) - j0
        run[i] = steps[min(j0 + k, len(steps) - 1)]
    return run


def rim_class_of(w_raw: np.ndarray, width_px: float) -> tuple[str, float]:
    present = w_raw > RIM_PRESENT_FRAC_W * width_px
    frac = float(present.mean())
    if frac < 0.30:
        return "rimless", frac
    if frac >= 0.85:
        return "full", frac
    # half rim: one contiguous rim arc (gaps <= 3 % closed) that contains the top vertex (index 0)
    m = len(present)
    gap = max(1, int(0.03 * m))
    tiled = np.r_[present, present, present]
    closed = ndimage.binary_closing(tiled, structure=np.ones(2 * gap + 1))[m:2 * m] | present
    # count distinct arcs on the ring
    arcs = ndimage.label(closed)[1]
    if closed[0] and closed[-1] and arcs > 1:
        arcs -= 1
    if arcs == 1 and closed[0]:
        return "half", frac
    return "mixed", frac


def partition(rgb: np.ndarray, fg: np.ndarray, lens: np.ndarray, front_width_mm: float,
              fg_clip: np.ndarray | None = None, matte: np.ndarray | None = None,
              bg_lab: np.ndarray | None = None, band_rule: bool = True, confirm: dict | None = None) -> dict:
    """Full S2 partition on one source image. Returns arrays + result pieces (source px).

    `fg` is the S0 foreground (matte united with the lens proposal), `matte` the pure
    contrast matte (defaults to fg): the frame mask and the frame/backdrop point typing use
    the pure matte so a bloated lens proposal can never become frame. `band_rule` enables the
    lens-tinted band rule (refine_contour ``band``): True for a front-photo source. ``confirm`` (a back-mirrored
    source): the front photo that must confirm every non-lens carve (``front_confirm``)."""
    lab = intake.lab_image(rgb)
    H, W = fg.shape
    matte = fg if matte is None else matte
    fg_eff = fg if fg_clip is None else (fg & fg_clip)
    fg_eff = intake.keep_components(fg_eff)
    matte_eff = matte & fg_eff
    x0, sym_iou = intake.symmetry_axis(fg_eff)
    # snap to the half-pixel lattice: ``mirror_field`` (the SDF symmetrisation, the mirror dispute and the frame mask)
    # is exact only when 2 x0 is an integer, and the Fourier L outline must mirror about the SAME axis
    x0 = round(2.0 * x0) / 2.0
    xs = np.nonzero(fg_eff.any(0))[0]
    width_px = float(xs.max() - xs.min() + 1)
    mm_px = front_width_mm / width_px
    L0 = clean_lens(lens & fg_eff, fg_eff)
    contours = proposal_contours(L0)
    # ridge image for rimless snapping (cropped around all proposals, 5 mm margin)
    ridge = None
    refs = []
    for c in contours:
        refs.append(refine_contour(c, lab, matte_eff, mm_px, None, bg_lab, band_rule))
    if any((r["types"] == TYPE_RIMLESS).any() for r in refs):
        allp = np.vstack(contours)
        mg = 5.0 / mm_px
        bb = (max(0, int(allp[:, 0].min() - mg)), max(0, int(allp[:, 1].min() - mg)),
              min(W, int(allp[:, 0].max() + mg) + 1), min(H, int(allp[:, 1].max() + mg) + 1))
        ridge = _ridge_image(rgb, bb, fg_eff | L0, 2.0 / mm_px)
        refs = [refine_contour(c, lab, matte_eff, mm_px, ridge, bg_lab, band_rule) for c in contours]
    mirror_retyped = apply_mirror_types(refs, x0, mm_px)
    bevels = [r["bevel"] for r in refs if r["bevel"] is not None]
    global_bevel = float(np.median(bevels)) if bevels else 0.0
    refined = []
    for r in refs:
        b = r["bevel"] if r["bevel"] is not None else global_bevel
        refined.append(finish_offsets(r, b, mm_px))
    # --- symmetrisation (SDF average, sigma 0.3 % W), lens field clipped to the matte field
    sigma = SYM_SIGMA_FRAC * width_px
    tau = SYM_GUARD_MM / mm_px
    Fsym = _symmetrise(sdf(fg_eff), x0, sigma, tau)
    fg_sym = Fsym < 0
    matte_sym = _symmetrise(sdf(matte_eff), x0, sigma, tau) < 0

    def lens_polys(polys_in, carve=True):
        Lr = rasterize(polys_in, (H, W)) > 0
        R = resolve_mirror_dispute(Lr, x0, lab, mm_px)
        # step 3b (frame structure), after the dispute (which would restore a one-sided carve from its lens twin) and
        # before the SDF average (which then averages a one-sided carve with its twin like any other lens boundary)
        if carve:
            C, cls, cinfo = carve_nonlens(R, lab, matte_eff, bg_lab, mm_px, evidence=("frame",))
            C, cls, cinfo["front_confirm"] = front_confirm(C, cls, confirm, x0)
            cinfo["carved_px_confirmed"] = int(C.sum())
            R = R & ~C
        else:
            cls, cinfo = None, None
        Lsym = _symmetrise(sdf(R), x0, sigma)
        field = np.maximum(Lsym, Fsym)
        comps = level_contours(field, min_area=0.0)
        total = sum(abs(signed_area(p)) for p in comps)
        comps = [p for p in comps if abs(signed_area(p)) >= 0.05 * total]
        return Lr, comps, cls, cinfo

    Lref_mask, comps, carve_cls, carve_info = lens_polys(refined)
    Lraw_mask, comps_raw, _, _ = lens_polys(contours, carve=False)     # detector only (A/B): no refinement, no carve
    flags = []
    if len(comps) not in (1, 2):
        flags.append(f"lens_components_{len(comps)}")
        comps = comps[:2] if len(comps) > 2 else comps
    if not comps:
        raise RuntimeError("S2: no lens outline survived symmetrisation")
    layout = "pair" if len(comps) == 2 else "single"
    lens_mirror_iou = intake.iou(Lref_mask, mirror_field(Lref_mask.astype(np.float32), x0, 0) > 0.5)

    def fourier_set(cs):
        """R first (larger u), L = exact mirror of R; single -> 'C'."""
        cs = sorted(cs, key=lambda p: -poly_centroid(p)[0])
        per = perimeter(cs[0]) * mm_px
        n_out = int(np.clip(8 * round(per / OUT_SPACING_MM / 8), 256, 2048))
        if len(cs) == 2:
            R = canonical_order(fourier_outline(cs[0], FOURIER_K, n_out))
            Lm = R.copy()
            Lm[:, 0] = 2 * x0 - Lm[:, 0]
            return [("R", R), ("L", canonical_order(Lm))]
        C = canonical_order(fourier_outline(cs[0], FOURIER_K, n_out))
        return [("C", C)]

    lenses = fourier_set(comps)
    lenses_raw = fourier_set(comps_raw) if len(comps_raw) == len(comps) else None
    # step 3c: a vent stays a vent (a hard constraint on the smoothed outline: the Fourier series rounds a 1 mm notch)
    lenses, vent_cls, vent_info = exclude_backdrop(lenses, (H, W), lab, matte_eff, bg_lab, mm_px, x0, confirm)
    carve_cls = np.where(vent_cls > 0, vent_cls, carve_cls).astype(np.int8)
    # a vent is where THIS photo shows backdrop: the mirror image of the other side's vent (the outline is symmetric)
    # that the photo shows as material here (oakley's lens top edge row under its inner vents) stays glasses - an
    # opening wider than the photo's is a hole where the photo shows material (a seam gap, once S6 keeps vents open)
    carve_cls = np.where((carve_cls == 1) & matte_eff, 0, carve_cls).astype(np.int8)
    # a vent is not glasses: neither lens nor frame, and not the S0 foreground the detector's proposal made it (the
    # symmetrised matte closes a 1 mm opening; S6 tucks free edges under fg_sym and never across the backdrop)
    vent = carve_cls == 1
    fg_sym = fg_sym & ~vent
    matte_sym = matte_sym & ~vent
    vent_info["vent_px_removed_from_fg"] = int(vent.sum())
    # --- per-vertex types from the refined points of the lens and of its mirror image
    ref_pts = []
    for r, poly in zip(refs, refined):
        ref_pts.append((poly, r["types"]))
    mirrored = [(np.stack([2 * x0 - p[:, 0], p[:, 1]], 1), t) for p, t in ref_pts]
    all_refs = [(np.vstack([p for p, _ in ref_pts]), np.concatenate([tt for _, tt in ref_pts])),
                (np.vstack([p for p, _ in mirrored]), np.concatenate([tt for _, tt in mirrored]))]
    types_of = {}
    carve_typed = 0
    for side, poly in lenses:
        t = _types_for(poly, all_refs)
        t = _mode_filter_ring(t, max(5, int(0.02 * len(poly)) | 1))
        # a carved boundary was never a refined point: it is free against a carved backdrop (vent), frame-bounded
        # against carved frame (stem, endpiece, pad)
        t, k = carve_types(poly, t, carve_cls, x0, CARVE_TYPE_REACH_MM / mm_px)
        carve_typed += k
        types_of[side] = t
    lens_mask_final = rasterize([p for _, p in lenses], (H, W), values=list(range(1, len(lenses) + 1)))
    frame_mask = intake.keep_components(matte_sym & fg_sym & (lens_mask_final == 0))
    arrays = {"frame_mask": frame_mask, "fg_sym": fg_sym, "lens_label": lens_mask_final,
              "carve_class": carve_cls.astype(np.int8)}
    lens_info = []
    max_rim_px = 25.0 / mm_px
    for i, (side, poly) in enumerate(lenses, start=1):
        t = types_of[side]
        w_raw = rim_widths(poly, frame_mask, max_rim_px)
        fb = w_raw[t == TYPE_FRAME]
        cap = (2.5 * float(np.median(fb)) + 2.0) if len(fb) else RIM_PRESENT_FRAC_W * width_px
        w = np.minimum(w_raw, cap)
        rc, frac = rim_class_of(w_raw, width_px)
        arrays[f"lens{i}_poly"] = poly.astype(np.float64)
        arrays[f"lens{i}_type"] = t.astype(np.int8)
        arrays[f"lens{i}_rimw_px"] = w.astype(np.float64)
        if lenses_raw is not None:
            arrays[f"lens{i}_poly_raw"] = lenses_raw[i - 1][1].astype(np.float64)
        lens_info.append({"side": side, "area_px": round(abs(signed_area(poly)), 1),
                          "centroid_px": [round(float(v), 2) for v in poly_centroid(poly)],
                          "type_fractions": {TYPE_NAMES[k]: round(float((t == k).mean()), 4) for k in range(3)},
                          "vertices": int(len(poly)), "rim_class": rc, "rim_present_fraction": round(frac, 4),
                          "rim_w_median_mm": round(float(np.median(fb)) * mm_px, 3) if len(fb) else 0.0})
    classes = {li["rim_class"] for li in lens_info}
    rim_class = classes.pop() if len(classes) == 1 else "mixed"
    # --- refinement stats
    all_off = np.concatenate([r["off_final"] for r in refs])
    all_types = np.concatenate([r["types"] for r in refs])
    all_low = np.concatenate([r["low_pure"] for r in refs])
    all_fallback = np.concatenate([r["low"] for r in refs])
    n_pts = len(all_types)
    frame_pts = all_types == TYPE_FRAME
    refinement = {
        "median_bevel_offset_mm": round(global_bevel, 3) if bevels else None,
        "low_contrast_share": round(float(all_low.sum() / max(1, n_pts)), 4),
        "low_contrast_share_of_frame_points": round(float(all_low.sum() / max(1, frame_pts.sum())), 4),
        "bevel_fallback_share": round(float(all_fallback.sum() / max(1, n_pts)), 4),
        "point_type_fractions": {TYPE_NAMES[k]: round(float((all_types == k).mean()), 4) for k in range(3)},
        "offset_mm": {"p05": round(float(np.percentile(all_off, 5)), 3), "p50": round(float(np.median(all_off)), 3),
                      "p95": round(float(np.percentile(all_off, 95)), 3)},
        "unmeasured_share_of_frame_points": round(float((all_fallback & frame_pts).sum() / max(1, frame_pts.sum())), 4),
        "mirror_retyped_points": mirror_retyped,
        "frame_points_clipped_share": round(float(sum(r["frame_clipped"] for r in refs) / max(1, n_pts)), 4),
        "band_rule": bool(band_rule),
        "band_extended_points": int(sum(int((r["band_ext"] > 0).sum()) for r in refs)),
        "registration_moved_points": int(sum(int(r["moved"].sum()) for r in refs)),
        "vent_points": int(sum(int(r["vent"].sum()) for r in refs)),
        "carve": dict(carve_info, typed_vertices=carve_typed),
        "vent_exclusion": vent_info,
        "per_component": [r["diag"] for r in refs],
    }
    ridge_supports = [r["diag"]["ridge_support"] for r in refs if r["diag"]["ridge_support"] is not None]
    if ridge_supports:
        refinement["ridge_support"] = round(float(np.mean(ridge_supports)), 3)
    lens_area = float(sum(li["area_px"] for li in lens_info))
    lens_share = lens_area / max(1.0, float(fg_sym.sum()))
    if lens_mirror_iou < LENS_MIRROR_IOU_MIN:
        flags.append("lens_mirror_iou_low")
    if not (LENS_SHARE_RANGE[0] <= lens_share <= LENS_SHARE_RANGE[1]):
        flags.append("lens_share_out_of_range")
    if refinement["unmeasured_share_of_frame_points"] > LOW_CONTRAST_MAX:
        # most frame-bounded points were neither measured nor registered: the outline there is the
        # detector's plus the median bevel, i.e. not measured
        flags.append("low_contrast_high")
    if not bevels:
        flags.append("bevel_offset_unmeasured")
    if refinement["frame_points_clipped_share"] > CLIPPED_SHARE_MAX:
        # many frame-bounded points disagreed with the detector + bevel model by more than
        # FRAME_DEV_MM (typically a temple seen through the lens on BOTH lenses, which mirror
        # symmetry cannot repair): the outline there is bounded, not measured
        flags.append("refinement_clipped_high")
    if rim_class == "rimless":
        flags.append("rimless_review")
        if not ridge_supports or min(ridge_supports) < RIDGE_SUPPORT_MIN:
            flags.append("rimless_low_confidence")
    return {"arrays": arrays, "axis_x_px": float(x0), "width_px": width_px, "mm_per_px": mm_px,
            "fg_mirror_iou": round(sym_iou, 4), "lens_mirror_iou": round(lens_mirror_iou, 4),
            "lens_share": round(lens_share, 4), "layout": layout, "rim_class": rim_class, "lenses": lens_info,
            "refinement": refinement, "flags": flags, "proposal_contours": contours, "refined_contours": refined,
            "refs": refs, "sigma_px": sigma, "carve_cls": carve_cls}


# --------------------------------------------------------------------------- stage

def _mirror_lr(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(a[:, ::-1])


def run(product: str, run: str = "m1", force: bool = False) -> dict:
    sd = core.stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    t0 = time.time()
    prod = core.PRODUCTS[product]
    s0d = core.stage_dir(run, product, intake.STAGE)
    if not s0d.done():
        intake.run(product, run)
    s0, a0 = s0d.load()
    fw = prod.front_width_mm
    front_w = s0["views"]["front"]["width_px"]
    back_w = s0["views"]["back"]["width_px"]
    rgb_front = core.load_photo(prod, "front")
    mt_f = a0.get("matte_front", a0["fg_front"])
    mt_bm = _mirror_lr(a0.get("matte_back", a0["fg_back"]))
    rgb_back_m = _mirror_lr(core.load_photo(prod, "back"))
    fg_f, lens_f = a0["fg_front"], a0["lens_front"]
    fg_bm, lens_bm = _mirror_lr(a0["fg_back"]), _mirror_lr(a0["lens_back"])
    use_back = front_w < SOURCE_MIN_WIDTH_PX and back_w > front_w
    flags = []
    # register mirrored back -> front on the (cleaned) lens proposals
    Lf = clean_lens(lens_f, fg_f)
    Lb = clean_lens(lens_bm, fg_bm)
    reg = {"method": "similarity (scale + translation) on cleaned lens proposals, IoU"}
    M_bf, reg_iou = register_similarity(Lb, Lf) if (Lf.any() and Lb.any()) else (None, 0.0)
    reg["back_mirrored_to_front"] = None if M_bf is None else np.round(M_bf, 6).tolist()
    reg["iou"] = round(reg_iou, 4)
    if use_back and M_bf is None:
        use_back = False
        flags.append("back_registration_failed")
    if use_back:
        source = "back_mirrored"
        rgb, fg, lens, mt = rgb_back_m, fg_bm, lens_bm, mt_bm
        M_sf = M_bf
        # the back view shows the temples hanging toward the camera: clip the matte to the
        # front silhouette mapped into the source frame (dilated by one front pixel)
        Mi = invert_affine(M_bf)
        clip = cv2.warpAffine(ndimage.binary_dilation(fg_f, iterations=1).astype(np.float32), Mi.astype(np.float32),
                              (fg.shape[1], fg.shape[0]), flags=cv2.INTER_LINEAR) > 0.5
        flags.append("outline_from_back_mirrored")
        flags.append("front_low_resolution")
    else:
        source = "front"
        rgb, fg, lens, mt = rgb_front, fg_f, lens_f, mt_f
        M_sf = np.array([[1.0, 0, 0], [0, 1.0, 0]])
        clip = None
    view = "back" if use_back else "front"
    bg = intake.backdrop_lab(np.asarray(s0["views"][view]["backdrop_lab_coef"]), s0["views"][view]["shape"])
    if use_back:
        bg = _mirror_lr(bg)
    # the lens-tinted band rule needs every lens-side pixel near the edge to be seen THROUGH the lens: true for
    # the front photo; in the back photo nose pads, the centre bar and the temples sit between the camera and
    # the lens, and a translucent pad over the lens takes the lens's tint without being lens
    confirm = None
    if use_back:
        # a carve seen from behind must be confirmed by the front photo (``front_confirm``)
        xs_f = np.nonzero(fg_f.any(0))[0]
        confirm = {"lab": intake.lab_image(rgb_front).astype(np.float64), "lens": np.asarray(lens_f, bool), "M": M_sf,
                   "mm_px": fw / float(xs_f.max() - xs_f.min() + 1)}
    part = partition(rgb, fg, lens, fw, clip, mt, bg, band_rule=(source == "front"), confirm=confirm)
    flags += part["flags"]
    arrays = part["arrays"]
    n_lens = len(part["lenses"])
    for i in range(1, n_lens + 1):
        arrays[f"lens{i}_poly_front"] = apply_affine(M_sf, arrays[f"lens{i}_poly"]).astype(np.float64)
    # --- front/back 2D agreement (detector vs detector, other view registered into the source by a 2D
    # similarity). DIAGNOSTIC ONLY: on a curved 3D frame the two views differ by parallax (oakley: 2.2 mm here
    # vs 1.0 mm through the 3D cameras), so it raises no flag; the geometric check (S2 outline lifted onto the S4
    # lens surface and projected through the frozen S3 camera of the other view) is S10's lens_view_consistency
    fb = {"note": "2D similarity-registered detector vs detector; parallax-affected diagnostic, no flag "
                  "(see s10_gate lens_view_consistency)"}
    if M_bf is not None:
        other = Lb if source == "front" else Lf
        M_os = M_bf if source == "front" else invert_affine(M_bf)
        Lsrc = clean_lens(lens & fg, fg)
        warped = cv2.warpAffine(other.astype(np.float32), M_os.astype(np.float32), (Lsrc.shape[1], Lsrc.shape[0]),
                                flags=cv2.INTER_LINEAR) > 0.5
        cs_a = proposal_contours(Lsrc)
        cs_b = proposal_contours(warped)
        fb["lens_count"] = {"source": len(cs_a), "other": len(cs_b)}
        if len(cs_a) != len(cs_b):
            flags.append("lens_count_disagree")
        ds = []
        for ca in cs_a:
            if not cs_b:
                break
            cb = min(cs_b, key=lambda q: np.hypot(*(poly_centroid(q) - poly_centroid(ca))))
            ds.append(contour_distance(ca, cb))
        if ds:
            mean_mm = float(np.mean([d[0] for d in ds])) * part["mm_per_px"]
            p95_mm = float(np.max([d[1] for d in ds])) * part["mm_per_px"]
            fb["lens_contour_mean_mm"] = round(mean_mm, 3)
            fb["lens_contour_p95_mm"] = round(p95_mm, 3)
            fb["lens_iou"] = round(intake.iou(Lsrc, warped), 4)
    result = {"stage": STAGE, "product": product, "run": run, "outline_source": source,
              "source_shape": list(fg.shape), "source_to_front": np.round(M_sf, 6).tolist(),
              "registration": reg, "axis_x_px": part["axis_x_px"], "width_px": part["width_px"],
              "mm_per_px_provisional": part["mm_per_px"], "front_width_mm": fw, "layout": part["layout"],
              "rim_class": part["rim_class"], "lenses": part["lenses"], "refinement": part["refinement"],
              "fg_mirror_iou": part["fg_mirror_iou"], "lens_mirror_iou": part["lens_mirror_iou"],
              "lens_share": part["lens_share"], "front_back": fb, "sym_sigma_px": round(part["sigma_px"], 3),
              "fourier_K": FOURIER_K, "flags": sorted(set(flags)),
              "conventions": {"lens_order": "lens1 = R (viewer's right, larger u) for a pair; lens1 = C for a single",
                              "poly": "closed, no repeated end point, positive shoelace area in (u, v) = clockwise on "
                                      "screen, starts at the top-most vertex; pair outlines are exact mirrors about axis_x_px",
                              "type": "0 frame-bounded, 1 free edge, 2 rimless edge",
                              "rimw_px": "outward run of frame_mask along the vertex normal, capped at 2.5 x median "
                                          "frame-bounded width + 2 px (bridge/temple runs)",
                              "poly_raw": "same pipeline without edge refinement (detector only), for A/B",
                              "poly_front": "lens{i}_poly mapped into FRONT-photo px with source_to_front",
                              "carve_class": "int8 source px: 2 frame structure carved from the lens proposal (step 3b: "
                                             "stem, endpiece, nose pad), 1 an enclosed opening (vent, step 3c: neither "
                                             "lens nor frame nor fg_sym), 0 elsewhere"},
              "seconds": None}
    result["seconds"] = round(time.time() - t0, 1)
    sd.save(result, arrays)
    _sheet(product, run, rgb, part, result, rgb_front)
    return result


# --------------------------------------------------------------------------- sheets

TYPE_COLORS = {TYPE_FRAME: (0, 200, 0), TYPE_FREE: (255, 140, 0), TYPE_RIMLESS: (230, 0, 230)}


def _draw_poly(img, poly, types, scale, thick):
    p = np.round(poly * scale * 16).astype(np.int32)
    m = len(p)
    for k in range(m):
        a, b = p[k], p[(k + 1) % m]
        col = TYPE_COLORS[int(types[k])]
        cv2.line(img, tuple(int(v) for v in a), tuple(int(v) for v in b), col, thick, cv2.LINE_AA, shift=4)


def render_sheet(rgb: np.ndarray, arrays: dict, result: dict, part: dict | None = None, width: int = 1400,
                 front_rgb: np.ndarray | None = None) -> np.ndarray:
    """Left: full source photo, frame mask tinted blue, non-lens carves (step 3b/3c: frame orange, vent cyan), lens
    outlines coloured by segment type (green frame-bounded, orange free, magenta rimless), dashed raw detector outline,
    axis.
    Right: two zooms of the first lens (top and outer end)."""
    H, W = rgb.shape[:2]
    s = min(1.0, (width * 0.62) / W)
    base = rgb.astype(np.float32)
    fm = arrays["frame_mask"]
    base[fm] = base[fm] * 0.55 + np.array([40, 90, 255]) * 0.45
    if part is not None and part.get("carve_cls") is not None:
        # step 3b/3c carves: frame structure removed from the lens (orange), enclosed backdrop / vents (cyan)
        cc = part["carve_cls"]
        base[cc == 2] = base[cc == 2] * 0.4 + np.array([255, 150, 0]) * 0.6
        base[cc == 1] = base[cc == 1] * 0.4 + np.array([0, 220, 255]) * 0.6
    big = base.astype(np.uint8)
    thick = max(1, int(round(1.2 / s)))
    x0 = result["axis_x_px"]
    cv2.line(big, (int(round(x0)), 0), (int(round(x0)), H - 1), (0, 0, 0), max(1, thick // 2))
    n_lens = len(result["lenses"])
    for i in range(1, n_lens + 1):
        if f"lens{i}_poly_raw" in arrays:
            raw = np.round(arrays[f"lens{i}_poly_raw"] * 16).astype(np.int32)
            for k in range(0, len(raw), 6):
                a, b = raw[k], raw[(k + 3) % len(raw)]
                cv2.line(big, tuple(map(int, a)), tuple(map(int, b)), (220, 0, 0), max(1, thick // 2), cv2.LINE_AA, shift=4)
        _draw_poly(big, arrays[f"lens{i}_poly"], arrays[f"lens{i}_type"], 1.0, thick)
    small = cv2.resize(big, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA)
    # zooms around lens 1: top-most vertex and the outer-most vertex
    poly = arrays["lens1_poly"]
    zs = []
    zoom_px = int(max(60, 0.07 * result["width_px"]))
    for idx in (int(np.argmin(poly[:, 1])), int(np.argmax(np.abs(poly[:, 0] - x0))), int(np.argmax(poly[:, 1]))):
        cx, cy = poly[idx]
        xa, ya = int(max(0, cx - zoom_px)), int(max(0, cy - zoom_px))
        xb, yb = int(min(W, cx + zoom_px)), int(min(H, cy + zoom_px))
        crop = rgb[ya:yb, xa:xb].copy()
        z = 300.0 / max(1, max(crop.shape[:2]))
        crop = cv2.resize(crop, (int(crop.shape[1] * z), int(crop.shape[0] * z)), interpolation=cv2.INTER_CUBIC)
        if part is not None:
            for c in part["proposal_contours"]:
                cv2.polylines(crop, [np.round((c - [xa, ya]) * z * 16).astype(np.int32)], True, (220, 0, 0), 1, cv2.LINE_AA, shift=4)
        for i in range(1, n_lens + 1):
            _draw_poly(crop, arrays[f"lens{i}_poly"] - [xa, ya], arrays[f"lens{i}_type"], z, 2)
        if part is not None:
            # refined points: cyan = moved by the frame registration, yellow = lens-tinted band, red = vent
            for r in part["refs"]:
                P = r["c"] + (r["off_final"] * (1.0 / result["mm_per_px_provisional"]))[:, None] * r["n"]
                for key, col in (("moved", (0, 220, 255)), ("band", (255, 220, 0)), ("vent", (255, 0, 0))):
                    sel = (r["band_ext"] > 0) if key == "band" else r[key]
                    for u, v in (P[sel][::3] - [xa, ya]) * z:
                        if 0 <= u < crop.shape[1] and 0 <= v < crop.shape[0]:
                            cv2.circle(crop, (int(u), int(v)), 2, col, -1)
        tile = np.full((300, 300, 3), 255, np.uint8)
        tile[:crop.shape[0], :crop.shape[1]] = crop
        zs.append(tile)
    right = np.vstack(zs)
    h = max(small.shape[0], right.shape[0])
    canvas = np.full((h + 70, small.shape[1] + right.shape[1] + 10, 3), 255, np.uint8)
    canvas[:small.shape[0], :small.shape[1]] = small
    canvas[:right.shape[0], small.shape[1] + 10:] = right
    if result.get("outline_source") != "front" and front_rgb is not None:
        # the outline mapped into the FRONT photo (source_to_front), bottom-left inset
        fr = front_rgb.copy()
        z = min(4.0, 380.0 / max(fr.shape[:2]))
        fr = cv2.resize(fr, (int(fr.shape[1] * z), int(fr.shape[0] * z)), interpolation=cv2.INTER_CUBIC)
        for i in range(1, n_lens + 1):
            _draw_poly(fr, arrays[f"lens{i}_poly_front"], arrays[f"lens{i}_type"], z, 1)
        cv2.putText(fr, "front photo (mapped)", (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
        y0 = max(0, h - fr.shape[0])
        canvas[y0:y0 + fr.shape[0], :fr.shape[1]] = fr
    ref = result["refinement"]
    lines = [f"{result['product']}  source={result['outline_source']}  layout={result['layout']}  rim={result['rim_class']}  "
             f"W={result['width_px']:.0f}px  lensMirrorIoU={result['lens_mirror_iou']}  lensShare={result['lens_share']}",
             f"bevel={ref['median_bevel_offset_mm']} mm  lowContrast={ref['low_contrast_share']}  "
             f"types={ref['point_type_fractions']}  flags={','.join(result['flags'])}"]
    for k, t in enumerate(lines):
        cv2.putText(canvas, t[:190], (6, h + 22 + 26 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return canvas


def _sheet(product, run, rgb, part, result, front_rgb=None):
    img = render_sheet(rgb, part["arrays"], result, part, front_rgb=front_rgb)
    cv2.imwrite(str(core.stage_dir(run, product, STAGE).root / "sheet.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


def contact_sheet(run: str = "m1", products=None) -> str:
    products = list(products or core.PRODUCTS)
    tiles = []
    for p in products:
        f = core.stage_dir(run, p, STAGE).root / "sheet.png"
        im = cv2.imread(str(f))
        if im is None:
            continue
        s = 1400.0 / im.shape[1]
        tiles.append(cv2.resize(im, (1400, int(im.shape[0] * s)), interpolation=cv2.INTER_AREA))
    out = core.BSA_DATA / "runs" / run / "s2_contact.png"
    cv2.imwrite(str(out), np.vstack(tiles))
    return str(out)


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("products", nargs="*", default=list(core.PRODUCTS))
    a = ap.parse_args()
    for p in a.products:
        r = run(p, a.run, a.force)
        print(p, json.dumps({k: r[k] for k in ("outline_source", "layout", "rim_class", "flags", "lens_mirror_iou",
                                                "lens_share", "fg_mirror_iou", "front_back", "seconds")}), flush=True)
        print("   refinement", json.dumps({k: v for k, v in r["refinement"].items() if k != "per_component"}), flush=True)
        print("   lenses", json.dumps(r["lenses"]), flush=True)
    print(contact_sheet(a.run, a.products))
