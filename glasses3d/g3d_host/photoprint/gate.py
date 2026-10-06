"""The print-registration gate of WP5_DESIGN §5.4 for a region g and an ordered view pair (w -> v).

Ported from the scratch `design_reg/p7b_axis_null.gate_axis` (decoys along the region's principal image axis),
`step2b_global.ncc_shift_map` and `shiftlib.single_source_render`; PORT_REVIEW section 10.

**The null (build step 3, design §5.4 option (c): a null that keeps the region's overlap).** Decoys displace the
TEXTURE, not the photo: decoy k recolours the true render's pixels by a cyclic shift ALONG THE STRIP of the region:
pixel p takes the rendered colour of the pixel q whose body point lies D_k mm further along the strip (in strip
coordinates (c, beta): c = |stripe-axis coordinate| wrapped in a period L', beta = the relative position across the
region at that c, on the plane perpendicular to the region's facing axis; the region is a height field over that
plane since its faces lie within 54.7 deg of the axis; q = the nearest rendered pixel within 2 px at the pivot scale).
The decoy is a permutation of the true render's own colours (w's photo at points v and w both see), so the photo, E,
the filters and the +/-R window are exactly the true statistic's; only the correspondence is broken. Hence:
- **overlap is kept by construction**: every E pixel whose shifted position lands on the rendered set keeps a colour
  (only the wrap gap L' - L and holes in the rendered set lose it). The displacement-of-the-photo null lost it: on thin
  curved regions seen obliquely (the brow fronts from 01) the design's displacements kept 0-1 of 6 decoys at >= 60 %
  overlap even on A0's perfect photos, so the gate could never register anything;
- **folds are kept**: D and L' are multiples of 8 mm (two 4 mm stripes; stripes are |coord| mod 8), so q lies in the
  same fold as p, and a decoy only reuses colours the true render already has (w's pixels the fit read stay
  forbidden);
- **decoys cannot reach the truth**: a decoy counts only when the median Chebyshev IMAGE displacement |p - q| in v is
  >= R + 3 mm x px/mm (foreshortening shrinks 8 mm on an oblique view), its overlap (decoy-coloured share of E) is
  >= 60 % and its window has a finite maximum. The six displacements are spread over (0, L');
- the rule is unchanged: warmth peak >= max(0.45, max counted decoy + 0.15), at least 4 counted decoys, irange within
  1 px. ``gate_images`` takes the decoy renders (``decoys=``); without them it falls back to the legacy
  displacement-of-the-photo null below (``null='displace'``), kept for reports (``alt_null`` rows) and its own tests.

Legacy null (displacement of the photo), changes from the scratch:

- **Decoys outside the search window** (the probe's p7b defect: a decoy displaced by less than twice the window found
  the TRUE peak; null 0.68 = peak 0.68). The photo is displaced along g's principal image axis (both signs; offsets
  are Chebyshev, the search window being the square +/-R, so a Euclidean R + 3 mm along a diagonal axis would leave
  only ~1 mm) and each decoy is searched over the same +/-R window. The first decoy sits at R + 3 mm, so a decoy
  window starts at least 3 mm from zero shift: it can reach the true peak only when the true residual exceeds 3 mm,
  and such a pair fails the 0.8 mm residual rule anyway.
- **Disjoint decoy windows** (fix round 2; ``DECOY_LAYOUT = 'disjoint'``): decoys at R + 3 mm, then every 2R + 1 px
  (FT1119 at 4.8 px/mm: 7.0, 15.2, 23.4 mm), so the six windows are six independent null samples. §5.4 step 5's
  R + 3, R + 6, R + 9 mm (``'design'``) puts windows 2R = 8 mm wide only 3 mm apart, and neighbouring decoys return the
  same maximum: on this module's synthetic 80 mm strip (tests/photoprint/test_gate.py) one injected shift gave only 2
  distinct maxima from 6 decoys, and on FT1119 Brow_Right 05 -> 01 B the +3 and +6 mm decoys both returned 0.4363 at
  the same shift. Either layout also drops a decoy whose maximum repeats an earlier counted decoy's shift.
- **A decoy counts only when its OWN displacement overlaps E by >= 60 %** (§5.4 step 5, "each with >= 60 % overlap";
  fix round 2) and its window maximum is not the same shift as an already counted decoy's. The first build counted
  a decoy when ANY of the ~1,681 shifts of its window kept 60 %, and the six windows (2R = 8 mm wide, 3 mm apart)
  overlap, so neighbouring decoys returned the same maximum (FT1119 Brow_Right 05 -> 01 B: +3 and +6 mm both 0.4363
  at the same shift; overlaps at the six displacements 0.42-0.59, none >= 60 %, yet counted as 5). Every decoy is
  reported (``decoy_rows``: its displacement, the overlap there, the window's usable-shift count, its maximum and
  argmax, ``usable`` and ``duplicate_of``); ``decoy_max`` is the maximum over the COUNTED decoys and
  ``decoy_max_any_window`` the maximum over every window shift with >= 60 % overlap (reported, never deciding).
- **At least 4 decoys must count** (``MIN_DECOYS``), else the pair is not significant (reason ``too_few_decoys``): a
  maximum over one or two decoys underestimates the null (fix round 1). Measured cost: on thin curved regions seen
  obliquely (brow fronts and temple faces from 01) the design's displacements along the principal axis rarely keep
  60 % overlap; ``min_decoys`` is a parameter so that tests of other properties can set it. Whether to keep this rule
  is an owner decision (PORT_REVIEW section 10).
- **Signals**: warmth decides (peak >= max(0.45, decoy max + 0.15)); the irange peak must lie within 1 px (Chebyshev,
  on the sub-pixel peaks) of the warmth peak; luminance is reported alongside with the same rule, never deciding.
- **Band**: masked DoG at 0.2 and 1.6 mm (x v's px/mm at the front pivot); the probe used 1 and 8 px.
- **The render is band-passed on the evaluated fold only** (fix round 1): its filter support is the rendered pixels of
  the evaluated fold, so a held-out (fold B) evaluation never reads the render at fitted (fold A) pixels; with sigma
  1.6 mm (3 sigma about 5 mm) against 4 mm stripes, a both-fold support moved FT1119's Brow_Right residuals by up to
  0.6 mm. The photo keeps its full support (eroded matte & acetate): it is the fixed reference the shift search and
  the decoys move over (a held-out-only support would leave no overlap beyond a few mm of shift), and only its E
  pixels are graded, none of which the fit read (below). Its band-pass therefore mixes in neighbouring pixels the fit
  did read; the render it is correlated with carries none of them.
- **NCC for every shift by FFT** (masked normalised cross-correlation as six correlations of masked moments; equal to
  the probe's direct loop within 1e-9, tests/photoprint/test_gate.py), so the window and the decoys cost one transform.
- The source w samples the surface only where it would donate (cos >= 0.5, first surface, ``valid`` pixel).
- **Never a pixel the fit read** (fix round 2; ``reads`` = ``register.RegResult.reads`` of the fit on the OTHER fold):
  E drops every v pixel in ``reads[v]``, and a source sample whose bilinear footprint touches ``reads[w]`` is not
  rendered (so it is neither graded nor in the render's filter support; E's 2 px erosion is taken on the region as
  rendered WITHOUT that drop, so a dropped sample is a hole, not an edge). A fold fit relabels its support at each band
  start, so pixels its early bands read can carry the evaluated fold at the final pose (first leak-free proof: 152 of
  the 890 graded pixels of Brow_Right 05 -> 01 B had been read by band 1). The counts removed are in every row.

Steps for (w -> v, g, fold): 1. ray-cast v (registered camera, v's S') and keep the pixels whose first hit is a face of
g; colour them from w's photo at the same body points on w's S' (w's camera). 2. Band-pass render and photo.
3. E = rendered pixels eroded 2 px & v's acetate eroded 1 px & the pixels of the evaluated fold & not read by the fit.
4. Integer shifts up to R = ceil(4 mm x px/mm), parabola refinement. 5. Decoys. 6. Significance. 7. Residual = |best
shift| in mm.

``fold_label_image`` labels every pixel of a view by the stripe fold of the body point its first hit lands on (the
registration's fold supports, ``register.Objective.start_band``, are built from it).
"""
from __future__ import annotations

import math

import numpy as np
from scipy import fft as sfft
from scipy import ndimage

from .. import cameras as C
from ..camera import project_mm
from .. import raster
from .. import texture_utils as T
from . import signals as S
from .pose import Deformer, Pose, bary3, points
from .regions import FOLD_A, FOLD_B, pixel_fold, stripe_fold

GATE_R_MM = 4.0
DECOY_MM = (3.0, 6.0, 9.0)        # 'design' layout (§5.4 step 5): R + these mm
DECOY_FIRST_MM = 3.0              # 'disjoint' layout: R + 3 mm, then every 2R + 1 px
DECOY_LAYOUT = "disjoint"
DECOY_MIN_OVERLAP = 0.6
PEAK_MIN = 0.45
DECOY_MARGIN = 0.15
AGREE_PX = 1.0
MIN_GATE_PX = 400
MIN_DECOYS = 4                    # of the 6 designed decoys, with >= 60 % overlap each
NO_REGION = -2                    # fold_label_image: the pixel's first hit is not a face of the given regions
FOLD_CODE = {"A": FOLD_A, "B": FOLD_B}
SOURCE_MIN_COS = 0.5
SOURCE_TOL_MM = 0.5
GATE_SIGNALS = ("warmth", "irange", "lum")
NULL = "strip"                     # default null of ``gate`` (build step 3); 'displace' = the legacy null
STRIP_PERIOD_MM = 8.0              # two 4 mm stripes: a displacement by a multiple keeps the fold
STRIP_DECOYS = 6
STRIP_TOL_PX = 2.0                 # a shifted point takes the nearest rendered pixel within 2 px (pivot scale)
STRIP_MIN_IMAGE_MM = 3.0           # a counted decoy moves its points >= R + 3 mm in v's image (median, Chebyshev)
FACING_AXIS = {"front": 2, "back": 2, "top": 1, "bottom": 1, "outer": 0, "inner": 0}


# ----------------------------------------------------------------------------- rendering a region from one source
def region_pixels(job, deformer: Deformer, pose: Pose, region, view: str, roi=None) -> dict:
    """Pixels of ``view`` whose first hit (registered camera, the view's S') is a face of ``region``."""
    v = job.views[view]
    H, W = v.shape
    roi = roi or C.matte_roi(v.matte)
    r = deformer.scene(pose, view).render(pose.cameras[view], (H, W), 1, roi, want_bary=True)
    x0, y0, x1, y1 = roi
    fid = np.full((H, W), -1, np.int32)
    fid[y0:y1, x0:x1] = r["face_id"]
    bar = np.zeros((H, W, 2), np.float32)
    bar[y0:y1, x0:x1] = r["bary"]
    inreg = np.zeros(len(job.mesh.F) + 1, bool)
    inreg[region.faces] = True
    sel = inreg[fid]                       # fid -1 indexes the trailing False
    ii, jj = np.nonzero(sel)
    face = fid[sel].astype(np.int64)
    bary = bary3(bar[sel])
    P_body, _ = points(job.mesh.V, job.mesh.F, face, bary)
    return {"sel": sel, "ii": ii, "jj": jj, "face": face, "bary": bary, "fold": pixel_fold(region, P_body),
            "face_id": fid}


def fold_label_image(job, deformer: Deformer, pose: Pose, regions, view: str, roi=None, *,
                     with_region: bool = False):
    """(H, W) int8: per pixel of ``view`` (its camera in ``pose``, its S'), the stripe fold (FOLD_A, FOLD_B or
    FOLD_GUARD) of the body point its first hit lands on when that face belongs to one of ``regions`` (each region's
    own stripe axis), NO_REGION elsewhere (other parts, background, outside the matte's box). ``with_region``: also
    the (H, W) region id image (-1 elsewhere)."""
    v = job.views[view]
    H, W = v.shape
    roi = roi or C.matte_roi(v.matte)
    r = deformer.scene(pose, view).render(pose.cameras[view], (H, W), 1, roi, want_bary=True)
    x0, y0, x1, y1 = roi
    fid = r["face_id"]
    axis_of = np.full(len(job.mesh.F) + 1, -1, np.int64)      # fid -1 indexes the trailing -1
    rid_of = np.full(len(job.mesh.F) + 1, -1, np.int64)
    for reg in regions:
        axis_of[reg.faces] = reg.axis
        rid_of[reg.faces] = reg.rid
    ax = axis_of[fid]
    sel = ax >= 0
    face = fid[sel].astype(np.int64)
    P_body, _ = points(job.mesh.V, job.mesh.F, face, bary3(r["bary"][sel]))
    sub = np.full(fid.shape, NO_REGION, np.int8)
    sub[sel] = stripe_fold(np.abs(P_body[np.arange(len(face)), ax[sel]]))
    out = np.full((H, W), NO_REGION, np.int8)
    out[y0:y1, x0:x1] = sub
    if not with_region:
        return out
    rid = np.full((H, W), -1, np.int64)
    rid[y0:y1, x0:x1] = rid_of[fid]
    return out, rid


def footprint_hits(mask: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """True where the bilinear sample at uv (``texture_utils.sample_bilinear``'s 2 x 2 footprint, clamped like it)
    reads a pixel of ``mask``."""
    H, W = mask.shape
    u = np.clip(np.asarray(uv[:, 0], float), 0, W - 1)
    v = np.clip(np.asarray(uv[:, 1], float), 0, H - 1)
    x0 = np.minimum(np.floor(u).astype(int), W - 2)
    y0 = np.minimum(np.floor(v).astype(int), H - 2)
    return mask[y0, x0] | mask[y0, x0 + 1] | mask[y0 + 1, x0] | mask[y0 + 1, x0 + 1]


def sample_source(job, deformer: Deformer, pose: Pose, w: str, face: np.ndarray, bary: np.ndarray,
                  min_cos: float = SOURCE_MIN_COS, image: np.ndarray | None = None,
                  forbidden: np.ndarray | None = None) -> dict:
    """Colours of photo w at body points (its camera, its S'): ok (first surface, facing, valid pixel, and the
    bilinear footprint clear of ``forbidden``), colour, cos, uv; ``forbidden_hit`` = samples dropped for
    ``forbidden`` only."""
    vw = job.views[w]
    V = deformer.vertices(pose, w)
    P, N = points(V, job.mesh.F, face, bary)
    cam = pose.cameras[w]
    vis, uv = T.visible_points(P, N, cam, job.frame, deformer.scene(pose, w), vw.shape, min_cos=min_cos,
                               tol_mm=SOURCE_TOL_MM)
    H, W = vw.shape
    inb = (uv[:, 0] > 0) & (uv[:, 0] < W - 1) & (uv[:, 1] > 0) & (uv[:, 1] < H - 1)
    ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    ok = vis & inb & vw.valid[vi, ui]
    hit = np.zeros(len(ok), bool)
    if forbidden is not None:
        hit = ok & footprint_hits(np.asarray(forbidden, bool), uv)
        ok = ok & ~hit
    img = vw.image if image is None else image
    col = T.sample_bilinear(img, uv[:, 0], uv[:, 1]).astype(np.float64)
    col[~ok] = 0.0                         # a dropped sample carries no colour (nothing downstream may read it)
    cos = N @ raster.camera_basis(cam)[2]
    return {"ok": ok, "col": col, "cos": cos, "uv": uv, "P": P, "forbidden_hit": hit}


class StripCoords:
    """Strip coordinates of a region's body points: c = |stripe-axis coordinate|, beta = relative position across the
    region at that c (0..1, from the region's own vertices binned every 0.5 mm along c, on the plane perpendicular to
    its facing axis). ``period`` = the region's c extent rounded up to a multiple of 8 mm. ``ok`` is False when the
    stripe axis is the facing axis (end caps)."""

    BIN_MM = 0.5

    def __init__(self, V: np.ndarray, F: np.ndarray, region, period_mm: float = STRIP_PERIOD_MM):
        fa = FACING_AXIS[region.facing]
        self.a = int(region.axis)
        self.ok = self.a != fa
        if not self.ok:
            return
        self.b = 3 - fa - self.a
        tri = np.asarray(V, float)[np.asarray(F)[np.asarray(region.faces)]]
        P = np.concatenate([tri.reshape(-1, 3), tri.mean(1)])
        c = np.abs(P[:, self.a])
        self.c_lo = float(c.min())
        self.length = float(c.max() - self.c_lo)
        self.period = float(period_mm * math.ceil(self.length / period_mm + 1e-9))
        nb = int(math.ceil(self.length / self.BIN_MM)) + 1
        k = np.clip(((c - self.c_lo) / self.BIN_MM).astype(int), 0, nb - 1)
        lo = np.full(nb, np.inf)
        hi = np.full(nb, -np.inf)
        np.minimum.at(lo, k, P[:, self.b])
        np.maximum.at(hi, k, P[:, self.b])
        good = np.isfinite(lo)
        x = np.arange(nb)
        self.blo = np.interp(x, x[good], lo[good])
        self.bhi = np.interp(x, x[good], hi[good])
        self.height = float(np.median(self.bhi - self.blo))
        self.nb = nb

    def coords(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        P = np.asarray(P, float)
        c = np.abs(P[:, self.a])
        k = np.clip(((c - self.c_lo) / self.BIN_MM).astype(int), 0, self.nb - 1)
        span = np.maximum(self.bhi[k] - self.blo[k], 0.2)
        return c, (P[:, self.b] - self.blo[k]) / span

    def offsets(self, n: int = STRIP_DECOYS) -> list[float]:
        """Up to ``n`` displacements (mm), multiples of the stripe period spread over (0, period)."""
        if not self.ok:
            return []
        m = int(round(self.period / STRIP_PERIOD_MM))
        cand = list(range(1, m))
        if len(cand) <= n:
            return [STRIP_PERIOD_MM * k for k in cand]
        idx = sorted({int(round(i * (len(cand) - 1) / (n - 1))) for i in range(n)})
        return [STRIP_PERIOD_MM * cand[i] for i in idx]

    def permutation(self, P_src: np.ndarray, P_dst: np.ndarray, D: float, tol_mm: float):
        """For each destination body point: the index of the source point nearest to it shifted by D along the strip
        (c -> c_lo + ((c - c_lo + D) mod period), beta kept), and whether one lies within ``tol_mm``."""
        from scipy.spatial import cKDTree
        cs, bs = self.coords(P_src)
        cd, bd = self.coords(P_dst)
        h = max(self.height, 1e-3)
        tree = cKDTree(np.c_[cs, bs * h])
        c2 = self.c_lo + np.mod(cd - self.c_lo + D, self.period)
        dist, idx = tree.query(np.c_[c2, bd * h], k=1)
        return idx, dist <= tol_mm


_STRIPS: dict = {}


def strip_coords(job, region) -> StripCoords:
    key = (id(job.mesh), region.rid, region.name)
    sc = _STRIPS.get(key)
    if sc is None:
        if len(_STRIPS) > 64:
            _STRIPS.clear()
        sc = StripCoords(job.mesh.V, job.mesh.F, region)
        _STRIPS[key] = sc
    return sc


def single_source_render(job, deformer: Deformer, pose: Pose, region, v: str, w: str,
                         forbidden: np.ndarray | None = None, decoy_mm=None) -> dict:
    """v's pixels of the region textured from w alone: ren (H, W, 3), ok (rendered), sel (region pixels), fold image;
    ``forbidden`` = w's pixels a sample may not read (``forbidden_px``: region pixels left unrendered for it).
    ``decoy_mm`` (list of mm, the strip null): also the decoy renders, the same pixels coloured from w at body points
    recoloured by the strip shift (``decoys``: [{D_mm, ren, ok, image_shift_px}]); 'auto' = ``StripCoords.offsets``."""
    px = region_pixels(job, deformer, pose, region, v)
    H, W = job.views[v].shape
    src = sample_source(job, deformer, pose, w, px["face"], px["bary"], forbidden=forbidden)
    ren = np.zeros((H, W, 3))
    ok = np.zeros((H, W), bool)
    ren[px["ii"][src["ok"]], px["jj"][src["ok"]]] = src["col"][src["ok"]]
    ok[px["ii"][src["ok"]], px["jj"][src["ok"]]] = True
    seen = src["ok"] | src["forbidden_hit"]            # rendered if nothing were forbidden (E's erosion uses this)
    ok_any = np.zeros((H, W), bool)
    ok_any[px["ii"][seen], px["jj"][seen]] = True
    fold = np.full((H, W), -2, np.int8)
    fold[px["ii"], px["jj"]] = px["fold"]
    out = {"ren": ren, "ok": ok, "ok_any": ok_any, "sel": px["sel"], "fold": fold,
           "forbidden_px": int(src["forbidden_hit"].sum())}
    if decoy_mm is not None:
        sc = strip_coords(job, region)
        offs = sc.offsets() if (isinstance(decoy_mm, str) and decoy_mm == "auto") else list(decoy_mm)
        out["decoys"] = []
        out["strip"] = {"ok": bool(sc.ok), "period_mm": getattr(sc, "period", None),
                        "length_mm": getattr(sc, "length", None)}
        okk = src["ok"]
        if sc.ok and okk.sum() > 0:
            P_body, _ = points(job.mesh.V, job.mesh.F, px["face"], px["bary"])
            tol = STRIP_TOL_PX / job.ppm(v, pose.cameras[v])
            src_i = np.nonzero(okk)[0]
            for D in offs:
                q, found = sc.permutation(P_body[src_i], P_body[src_i], D, tol)
                rd = np.zeros((H, W, 3))
                od = np.zeros((H, W), bool)
                dst = src_i[found]
                frm = src_i[q[found]]
                rd[px["ii"][dst], px["jj"][dst]] = src["col"][frm]
                od[px["ii"][dst], px["jj"][dst]] = True
                shift_px = None
                if len(dst):
                    cheb = np.maximum(np.abs(px["ii"][dst] - px["ii"][frm]), np.abs(px["jj"][dst] - px["jj"][frm]))
                    shift_px = float(np.median(cheb))
                out["decoys"].append({"D_mm": float(D), "ren": rd, "ok": od, "image_shift_px": shift_px})
    return out


# ----------------------------------------------------------------------------- masked NCC for every shift (FFT)
def _corr_valid(FI, FT, shape, out_shape, th, tw):
    full = sfft.irfft2(FI * FT, s=shape)
    return full[th - 1: th - 1 + out_shape[0], tw - 1: tw - 1 + out_shape[1]]


def ncc_shift_maps(a_list, E: np.ndarray, b_list, bm: np.ndarray, Rm: int, min_overlap: float = DECOY_MIN_OVERLAP):
    """For every signal pair (a, b): the masked NCC of a over E against b shifted by d = (dy, dx), |d|inf <= Rm,
    i.e. a(y, x) ~ b(y + dy, x + dx) over {E} & {bm shifted}; NaN where that overlap is below ``min_overlap`` |E|.
    Returns (list of (2Rm+1, 2Rm+1) maps, overlap counts map). Index [Rm + dy, Rm + dx]."""
    ys, xs = np.nonzero(E)
    if len(ys) == 0:
        raise ValueError("empty E")
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    th, tw = y1 - y0, x1 - x0
    H, W = E.shape
    ih, iw = th + 2 * Rm, tw + 2 * Rm

    def crop(img):
        out = np.zeros((ih, iw), np.float64)
        sy0, sx0 = y0 - Rm, x0 - Rm
        cy0, cx0 = max(sy0, 0), max(sx0, 0)
        cy1, cx1 = min(sy0 + ih, H), min(sx0 + iw, W)
        out[cy0 - sy0: cy1 - sy0, cx0 - sx0: cx1 - sx0] = img[cy0:cy1, cx0:cx1]
        return out

    Et = E[y0:y1, x0:x1].astype(np.float64)
    BM = crop(bm.astype(np.float64))
    shape = (sfft.next_fast_len(ih + th - 1, True), sfft.next_fast_len(iw + tw - 1, True))
    out_shape = (2 * Rm + 1, 2 * Rm + 1)

    def FTt(t):
        return sfft.rfft2(t[::-1, ::-1], s=shape)

    FI_bm = sfft.rfft2(BM, s=shape)
    FT_e = FTt(Et)
    n = np.rint(_corr_valid(FI_bm, FT_e, shape, out_shape, th, tw))
    nE = float(Et.sum())
    valid = n >= min_overlap * nE
    maps = []
    for a, b in zip(a_list, b_list):
        at = np.where(Et > 0, a[y0:y1, x0:x1], 0.0)
        at = np.where(Et > 0, at - at[Et > 0].mean(), 0.0)
        bc = crop(np.where(bm, b, 0.0))
        sel = BM > 0
        if sel.any():
            bc = np.where(sel, bc - bc[sel].mean(), 0.0)
        FT_a, FT_a2 = FTt(at), FTt(at * at)
        FI_b, FI_b2 = sfft.rfft2(bc, s=shape), sfft.rfft2(bc * bc, s=shape)
        Sa = _corr_valid(FI_bm, FT_a, shape, out_shape, th, tw)
        Saa = _corr_valid(FI_bm, FT_a2, shape, out_shape, th, tw)
        Sb = _corr_valid(FI_b, FT_e, shape, out_shape, th, tw)
        Sbb = _corr_valid(FI_b2, FT_e, shape, out_shape, th, tw)
        Sab = _corr_valid(FI_b, FT_a, shape, out_shape, th, tw)
        nn = np.maximum(n, 1.0)
        num = Sab - Sa * Sb / nn
        va = Saa - Sa * Sa / nn
        vb = Sbb - Sb * Sb / nn
        den = np.sqrt(np.clip(va, 0, None) * np.clip(vb, 0, None))
        scale = max(float(np.abs(Saa).max()) * float(np.abs(Sbb).max()), 1e-300)
        ok = valid & (den > 1e-12 * math.sqrt(scale))
        m = np.full(out_shape, np.nan)
        m[ok] = np.clip(num[ok] / den[ok], -1.0, 1.0)
        maps.append(m)
    return maps, n


def _parabola(m: np.ndarray, iy: int, ix: int) -> tuple[float, float]:
    sub = []
    for (i, n_, arr) in ((iy, m.shape[0], m[:, ix]), (ix, m.shape[1], m[iy, :])):
        if 0 < i < n_ - 1 and np.isfinite(arr[i - 1]) and np.isfinite(arr[i + 1]):
            den = arr[i - 1] - 2 * arr[i] + arr[i + 1]
            sub.append(float(0.5 * (arr[i - 1] - arr[i + 1]) / den) if den < 0 else 0.0)
        else:
            sub.append(0.0)
    return sub[0], sub[1]


def window_peak(m: np.ndarray, Rm: int, center: tuple[int, int], R: int) -> dict | None:
    """Best finite NCC in the window center +/- R (center = (dy, dx)) of a full map, refined by a parabola."""
    cy, cx = Rm + center[0], Rm + center[1]
    ylo, yhi = max(cy - R, 0), min(cy + R + 1, m.shape[0])
    xlo, xhi = max(cx - R, 0), min(cx + R + 1, m.shape[1])
    w = m[ylo:yhi, xlo:xhi]
    if w.size == 0 or not np.isfinite(w).any():
        return None
    iy, ix = np.unravel_index(np.nanargmax(w), w.shape)
    iy, ix = iy + ylo, ix + xlo
    sy, sx = _parabola(m, iy, ix)
    dy, dx = iy - Rm + sy, ix - Rm + sx
    edge = abs(iy - cy) == R or abs(ix - cx) == R
    return {"peak": float(m[iy, ix]), "shift_px": [float(dx), float(dy)], "edge": bool(edge),
            "argmax": [int(iy - Rm), int(ix - Rm)]}          # integer (dy, dx) of the maximum


def principal_axis(E: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(E)
    c = np.cov(np.c_[xs, ys].T.astype(np.float64))
    w, vec = np.linalg.eigh(c)
    ax = vec[:, -1]
    if ax[0] < 0 or (ax[0] == 0 and ax[1] < 0):
        ax = -ax
    return ax                                   # (x, y) unit vector


def decoy_rows(m: np.ndarray, n: np.ndarray, nE: float, Rm: int, centers, R: int,
               min_overlap: float = DECOY_MIN_OVERLAP) -> list[dict]:
    """Each decoy of one signal's NCC map ``m`` (overlap counts ``n``, |E| = ``nE``): the overlap at its own
    displacement, the window's usable shifts (>= ``min_overlap``), its window maximum and integer argmax; ``usable``
    when the displacement itself keeps ``min_overlap`` and the window has a finite maximum; ``duplicate_of`` = the
    index of an earlier usable decoy with the same integer argmax (overlapping windows); ``counted`` = usable and not
    a duplicate."""
    rows, firsts = [], {}
    for k, c in enumerate(centers):
        cy, cx = Rm + c[0], Rm + c[1]
        inside = 0 <= cy < n.shape[0] and 0 <= cx < n.shape[1]
        ov = float(n[cy, cx] / nE) if inside and nE > 0 else 0.0
        ylo, yhi = max(cy - R, 0), min(cy + R + 1, n.shape[0])
        xlo, xhi = max(cx - R, 0), min(cx + R + 1, n.shape[1])
        usable_shifts = int((n[ylo:yhi, xlo:xhi] >= min_overlap * nE).sum()) if yhi > ylo and xhi > xlo else 0
        pk = window_peak(m, Rm, c, R)
        row = {"offset_px": [int(c[0]), int(c[1])], "overlap_at_offset": ov, "usable_shifts": usable_shifts,
               "window_shifts": int(max(yhi - ylo, 0) * max(xhi - xlo, 0)),
               "peak": pk["peak"] if pk else None, "argmax_px": pk["argmax"] if pk else None,
               "usable": bool(pk is not None and ov >= min_overlap), "duplicate_of": None}
        if row["usable"]:
            key = tuple(pk["argmax"])
            if key in firsts:
                row["duplicate_of"] = firsts[key]
            else:
                firsts[key] = k
        row["counted"] = bool(row["usable"] and row["duplicate_of"] is None)
        rows.append(row)
    return rows


def decoy_distances_px(R: int, ppm: float, layout: str = DECOY_LAYOUT) -> list[float]:
    """Chebyshev distances (px) of the three decoys on each side: 'disjoint' = ceil(R + 3 mm), then + (2R + 1) twice
    (windows never overlap); 'design' = R + 3, R + 6, R + 9 mm (§5.4 step 5; windows overlap)."""
    if layout == "disjoint":
        d0 = math.ceil(R + DECOY_FIRST_MM * ppm)
        return [float(d0 + k * (2 * R + 1)) for k in range(3)]
    if layout == "design":
        return [R + mm * ppm for mm in DECOY_MM]
    raise ValueError(f"unknown decoy layout {layout!r}")


def decoy_centers(E: np.ndarray, R: int, ppm: float, layout: str = DECOY_LAYOUT) -> list[tuple[int, int]]:
    """(dy, dx) of the six decoy displacements: along E's principal image axis, scaled so that the CHEBYSHEV offset
    (the search window is a square) is each of ``decoy_distances_px``, both signs. Every decoy window lies at least
    3 mm (in its largest coordinate) from zero shift."""
    ax = principal_axis(E)                       # (x, y)
    j = 0 if abs(ax[0]) >= abs(ax[1]) else 1     # dominant image coordinate
    out = []
    for d in decoy_distances_px(R, ppm, layout):
        for sgn in (1, -1):
            c = [0, 0]
            c[j] = int(sgn * np.sign(ax[j]) * math.ceil(d))
            c[1 - j] = int(round(sgn * d * ax[1 - j] / abs(ax[j])))
            out.append((c[1], c[0]))             # (dy, dx)
    return out


# ----------------------------------------------------------------------------- the gate
def strip_null(a_ren_signals, decoys: list, E: np.ndarray, b_list, pok: np.ndarray, R: int, ppm: float) -> dict:
    """The strip null: per decoy render (already band-passed per signal: ``a_ren_signals[k]``), its overlap with E,
    its image displacement, and per signal the window maximum over +/-R (the true statistic's window). A decoy is
    usable when its overlap is >= 60 %, its median image displacement >= R + 3 mm x px/mm and its warmth window has a
    finite maximum; every usable decoy counts (the decoys are different textures, so there are no duplicates)."""
    nE = float(E.sum())
    need_px = R + STRIP_MIN_IMAGE_MM * ppm
    rows, per_sig = [], {name: [] for name in GATE_SIGNALS}
    for k, d in enumerate(decoys):
        Ek = E & np.asarray(d["ok"], bool)
        ov = float(Ek.sum() / nE) if nE > 0 else 0.0
        shift = d.get("image_shift_px")
        row = {"kind": "strip", "D_mm": d.get("D_mm"), "overlap_at_offset": ov, "image_shift_px": shift,
               "image_shift_mm": (float(shift) / ppm) if shift is not None else None, "peak": None,
               "argmax_px": None, "peaks": {}, "usable": False, "duplicate_of": None, "counted": False,
               "offset_px": None, "usable_shifts": None}
        far = shift is not None and shift >= need_px
        if ov >= DECOY_MIN_OVERLAP and far and Ek.sum() >= MIN_GATE_PX // 2:
            maps, n = ncc_shift_maps(a_ren_signals[k], Ek, b_list, pok, R)
            row["usable_shifts"] = int((n >= DECOY_MIN_OVERLAP * Ek.sum()).sum())
            for name, m in zip(GATE_SIGNALS, maps):
                pk = window_peak(m, R, (0, 0), R)
                row["peaks"][name] = pk["peak"] if pk else None
                if name == "warmth" and pk:
                    row["peak"], row["argmax_px"] = pk["peak"], pk["argmax"]
            row["usable"] = row["peak"] is not None
            row["counted"] = row["usable"]
            if row["counted"]:
                for name in GATE_SIGNALS:
                    if row["peaks"].get(name) is not None:
                        per_sig[name].append(row["peaks"][name])
        rows.append(row)
    return {"rows": rows, "per_signal": per_sig, "need_image_shift_px": float(need_px)}


def gate_images(ren: np.ndarray, ok: np.ndarray, photo: np.ndarray, matte: np.ndarray, E: np.ndarray, ppm: float,
                *, reg_mm: float | None = None, ren_support: np.ndarray | None = None,
                min_decoys: int = MIN_DECOYS, decoy_layout: str = DECOY_LAYOUT, decoys: list | None = None) -> dict:
    """§5.4 steps 2 and 4-7 on given images (render, its mask, the photo, its matte) and evaluation pixels E.
    ``ren_support`` = where the render's band-pass may read (default ``ok``; ``gate`` passes the evaluated fold's
    rendered pixels); the photo's band-pass reads its eroded matte & acetate. ``decoys`` = the strip null's decoy
    renders [{ren, ok, support (default ok), image_shift_px, D_mm}] (the default null of ``gate``); without them the
    legacy displacement-of-the-photo null (``decoy_layout``) decides."""
    rs = np.asarray(ok if ren_support is None else ren_support, bool)
    null = "strip" if decoys is not None else "displace"
    out = {"E_px": int(E.sum()), "px_per_mm": float(ppm), "render_support_px": int(rs.sum()), "decoys": 0,
           "min_decoys": int(min_decoys), "null": null, "decoy_layout": "strip" if null == "strip" else decoy_layout}
    if E.sum() < MIN_GATE_PX:
        out.update({"significant": False, "reason": "too_few_pixels"})
        return out
    acet = S.acetate_mask(photo)
    pok = ndimage.binary_erosion(matte, iterations=1) & acet
    s1, s2 = S.band_px(S.GATE_BAND_MM, ppm)
    R = int(math.ceil(GATE_R_MM * ppm))
    if null == "strip":
        return _gate_strip(out, ren, rs, photo, pok, E, ppm, R, s1, s2, decoys, reg_mm, min_decoys)
    centers = decoy_centers(E, R, ppm, decoy_layout)
    Rm = int(max(max(abs(c[0]), abs(c[1])) for c in centers) + R + 2)
    ax = principal_axis(E)
    a_list = [S.bandpass(S.signal(s, ren), rs, s1, s2) for s in GATE_SIGNALS]
    b_list = [S.bandpass(S.signal(s, photo), pok, s1, s2) for s in GATE_SIGNALS]
    maps, n = ncc_shift_maps(a_list, E, b_list, pok, Rm)
    nE = float(E.sum())
    sig, rows_w = {}, None
    for name, m in zip(GATE_SIGNALS, maps):
        pk = window_peak(m, Rm, (0, 0), R)
        rows = decoy_rows(m, n, nE, Rm, centers, R)
        cnt = [r for r in rows if r["counted"]]
        anyw = [r["peak"] for r in rows if r["peak"] is not None]
        zero = m[Rm, Rm]
        sig[name] = {"peak": pk["peak"] if pk else None, "shift_px": pk["shift_px"] if pk else None,
                     "edge": pk["edge"] if pk else None, "ncc_zero": float(zero) if np.isfinite(zero) else None,
                     "decoys": len(cnt), "decoy_max": max(r["peak"] for r in cnt) if cnt else None,
                     "decoys_usable": sum(1 for r in rows if r["usable"]),
                     "decoy_duplicates": sum(1 for r in rows if r["duplicate_of"] is not None),
                     "decoy_max_any_window": max(anyw) if anyw else None}
        if name == "warmth":
            rows_w = rows
        if pk:
            sig[name]["residual_mm"] = float(math.hypot(*pk["shift_px"]) / ppm)
            # min_decoys = 0 (tests of other properties only): the 0.45 floor alone when no decoy counts
            null = sig[name]["decoy_max"] + DECOY_MARGIN if cnt else -math.inf
            sig[name]["passes_rule"] = bool(len(cnt) >= min_decoys and pk["peak"] >= max(PEAK_MIN, null))
    w, ir = sig["warmth"], sig["irange"]
    agree = (w["shift_px"] is not None and ir["shift_px"] is not None
             and max(abs(w["shift_px"][0] - ir["shift_px"][0]), abs(w["shift_px"][1] - ir["shift_px"][1])) <= AGREE_PX)
    reason = None
    if w["peak"] is None:
        reason = "no_valid_shift"
    elif w["decoys"] < min_decoys:
        reason = "too_few_decoys"
    elif not w["passes_rule"]:
        reason = "warmth_peak_below_decoys"
    elif not agree:
        reason = "irange_disagrees"
    significant = reason is None
    win0 = n[Rm - R: Rm + R + 1, Rm - R: Rm + R + 1]
    out.update({"R_px": R, "decoy_offsets_px": [list(c) for c in centers], "principal_axis": [float(x) for x in ax],
                "decoys": int(w["decoys"]), "decoy_rows": rows_w,
                "window_usable_shifts": int((win0 >= DECOY_MIN_OVERLAP * nE).sum()),
                "signals": sig, "irange_agrees": bool(agree), "significant": bool(significant), "reason": reason,
                "residual_mm": w.get("residual_mm"), "shift_px": w["shift_px"]})
    if reg_mm is not None:
        out["registered"] = bool(significant and w.get("residual_mm") is not None and w["residual_mm"] <= reg_mm)
    return out


def _gate_strip(out, ren, rs, photo, pok, E, ppm, R, s1, s2, decoys, reg_mm, min_decoys) -> dict:
    a_list = [S.bandpass(S.signal(s, ren), rs, s1, s2) for s in GATE_SIGNALS]
    b_list = [S.bandpass(S.signal(s, photo), pok, s1, s2) for s in GATE_SIGNALS]
    maps, n = ncc_shift_maps(a_list, E, b_list, pok, R)
    a_dec = []
    for d in decoys:
        sup = np.asarray(d.get("support", d["ok"]), bool)
        a_dec.append([S.bandpass(S.signal(s, d["ren"]), sup, s1, s2) for s in GATE_SIGNALS])
    nul = strip_null(a_dec, decoys, E, b_list, pok, R, ppm)
    nE = float(E.sum())
    sig = {}
    for name, m in zip(GATE_SIGNALS, maps):
        pk = window_peak(m, R, (0, 0), R)
        vals = nul["per_signal"][name]
        zero = m[R, R]
        sig[name] = {"peak": pk["peak"] if pk else None, "shift_px": pk["shift_px"] if pk else None,
                     "edge": pk["edge"] if pk else None, "ncc_zero": float(zero) if np.isfinite(zero) else None,
                     "decoys": len(vals), "decoy_max": max(vals) if vals else None,
                     "decoy_mean": float(np.mean(vals)) if vals else None,
                     "decoys_usable": len(vals), "decoy_duplicates": 0,
                     "decoy_max_any_window": max(vals) if vals else None}
        if pk:
            sig[name]["residual_mm"] = float(math.hypot(*pk["shift_px"]) / ppm)
            null = sig[name]["decoy_max"] + DECOY_MARGIN if vals else -math.inf
            sig[name]["passes_rule"] = bool(len(vals) >= min_decoys and pk["peak"] >= max(PEAK_MIN, null))
    w, ir = sig["warmth"], sig["irange"]
    agree = (w["shift_px"] is not None and ir["shift_px"] is not None
             and max(abs(w["shift_px"][0] - ir["shift_px"][0]), abs(w["shift_px"][1] - ir["shift_px"][1])) <= AGREE_PX)
    reason = None
    if w["peak"] is None:
        reason = "no_valid_shift"
    elif w["decoys"] < min_decoys:
        reason = "too_few_decoys"
    elif not w["passes_rule"]:
        reason = "warmth_peak_below_decoys"
    elif not agree:
        reason = "irange_disagrees"
    significant = reason is None
    out.update({"R_px": R, "decoy_offsets_mm": [d.get("D_mm") for d in decoys],
                "decoy_need_image_shift_px": nul["need_image_shift_px"],
                "decoys": int(w["decoys"]), "decoy_rows": nul["rows"],
                "window_usable_shifts": int((n >= DECOY_MIN_OVERLAP * nE).sum()),
                "signals": sig, "irange_agrees": bool(agree), "significant": bool(significant), "reason": reason,
                "residual_mm": w.get("residual_mm"), "shift_px": w["shift_px"]})
    if reg_mm is not None:
        out["registered"] = bool(significant and w.get("residual_mm") is not None and w["residual_mm"] <= reg_mm)
    return out


def gate(job, deformer: Deformer, pose: Pose, region, w: str, v: str, fold: str | None, *,
         reg_mm: float | None = None, photo: np.ndarray | None = None, min_decoys: int = MIN_DECOYS,
         reads: dict | None = None, decoy_layout: str = DECOY_LAYOUT, null: str = NULL) -> dict:
    """§5.4 for region g and the ordered pair (w -> v) on the evaluated ``fold`` ('A', 'B' or None = every pixel).
    ``photo`` replaces v's photo (tests: substituted photos); its matte is then recomputed. ``reads`` = the photo
    pixels the fit on the other fold read (``RegResult.reads``): never graded (E) and never rendered from (w).
    ``null`` = 'strip' (default; texture displaced along the strip) or 'displace' (legacy; photo displaced along
    E's principal image axis with ``decoy_layout``)."""
    reads = reads or {}
    if null not in ("strip", "displace"):
        raise ValueError(f"unknown null {null!r}")
    rr = single_source_render(job, deformer, pose, region, v, w, forbidden=reads.get(w),
                              decoy_mm="auto" if null == "strip" else None)
    vv = job.views[v]
    img = vv.image if photo is None else np.asarray(photo, np.uint8)
    matte = vv.matte
    if photo is not None:
        from .. import matte as matte_mod
        matte = matte_mod.matte(img)
    acet = S.acetate_mask(img)
    # the 2 px erosion keeps E off the region's rendered edge; a sample dropped for ``reads`` is a hole, not an edge
    # (eroding around it would also drop clean neighbours, e.g. evaluated-fold pixels next to a dropped fitted-fold one)
    E = (ndimage.binary_erosion(rr["ok_any"], iterations=2) & rr["ok"]
         & ndimage.binary_erosion(acet, iterations=1))
    ren_support = rr["ok"]
    if fold is not None:
        in_fold = rr["fold"] == FOLD_CODE[fold]
        E &= in_fold
        ren_support = rr["ok"] & in_fold          # the render never reads the fitted fold (fix round 1)
    e_before = int(E.sum())
    if v in reads:
        E &= ~np.asarray(reads[v], bool)          # never grade a pixel the fit read (fix round 2)
    decoys = None
    if null == "strip":
        decoys = []
        for d in rr["decoys"]:
            sup = d["ok"] & in_fold if fold is not None else d["ok"]
            decoys.append({**d, "support": sup})
    res = gate_images(rr["ren"], rr["ok"], img, matte, E, job.ppm(v, pose.cameras[v]), reg_mm=reg_mm,
                      ren_support=ren_support, min_decoys=min_decoys, decoy_layout=decoy_layout, decoys=decoys)
    if null == "strip":
        res["strip"] = rr.get("strip")
    res.update({"region": region.name, "rid": region.rid, "source": w, "target": v, "fold": fold or "all",
                "E_px_before_reads": e_before, "E_px_read_by_fit": e_before - int(E.sum()),
                "source_px_read_by_fit": rr["forbidden_px"], "reads_applied": bool(reads)})
    return res
