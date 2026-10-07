"""Independent print metrics (TESTS AND check_print ONLY): the instrument WP5 is graded by (WP5_DESIGN §7, §8).

It never imports ``glasses3d.g3d_host.photoprint`` (tests/photoprint/test_no_import.py parses this file and checks
``sys.modules`` in a fresh interpreter): the tool's own numbers are never the proof (the WP4 rule). Its filters,
masks and correlations are written here again on purpose. It shares only WP3b's colour helpers
(``g3d_host.texture_utils``: CIEDE2000 and the median-anchored ``quantile_gain``), which the probe's definitions use
and which are not part of the tool under test.

Definitions (the probe's `wp5lib.metrics`, `p3_havana.stats`, `ap5_chroma_lovo.appearance/tol_bp`; the design's
additions marked NEW). Images are sRGB 0..255 floats; ``support`` = where the filters may read (both images defined);
``ren_support`` (optional, default ``support``) = where the RENDER's filters may read: on a held-out stripe set the
caller passes the evaluated fold's pixels, so the render's band-pass never reads the fitted fold (the photo's filters
keep ``support``; nothing fitted is in the photo); ``E`` = the graded pixels; ``ppm`` = the graded view's px/mm at the
front pivot.

- luminance l = mean(R, G, B); masked Gaussian G_s(l S) / G_s(S) on ``support``.
- ``lum_r``: Pearson r of the sigma 1 px masked Gaussians on E.
- ``bp_1_4px_r``: Pearson r of the masked DoG (1 px - 4 px) on E; ``bp_mm_r``: the same at 0.25 mm - 1.0 mm.
- null: the 18-shift no-registration null of bp 1-4 px (and bp mm): the photo's band image displaced by dx in
  +/-{30, 45, 60} px, dy in {-2, 0, 2} px; an E pixel counts when its displaced partner lies in the filter support
  (the probe required the partner in E, which leaves no null on striped held-out sets); > 200 px per shift; mean and
  std of r. Without a null, C2 fails ("null_unavailable"). ``null_bp_1_4px_bias`` = |mean r| (the null's BIAS, what
  A0's null-mean bound tests; build step 3) beside ``null_bp_1_4px_mean_abs`` = mean |r|, which for a centred null
  estimates sqrt(2 / pi) x its spread (about 0.8 sigma): the spread is set by E's effective sample count, not by any
  bias, so mean |r| <= 0.05 fails even for a perfect render (A0: 0.057 with |mean r| 0.004).
- ``de00_raw``, ``de00_gain``: CIEDE2000 per E pixel, raw and after ONE quantile_gain fitted on E (render -> photo);
  mean and median.
- ``de00_gain_xfit`` (NEW, the cross-fitted gain): the gain is fitted on the black 8 x 8 px blocks of E and applied to
  the white ones, and the reverse; mean over E.
- ``energy_0.4_1.6mm``, ``energy_1_4px`` (NEW): std of the render's masked DoG / std of the photo's, on E.
- ``spectrum_error`` (NEW): mean |log2| of the band-contrast ratio (std of the masked DoG / mean l on E) over the
  octaves 0.2-0.4, 0.4-0.8, 0.8-1.6, 1.6-3.2 mm.
- ``corr_len_mm`` (NEW): the distance at which the luminance autocorrelation on E (4 directions, mean r) first falls
  below 0.5; ``corr_len_ratio`` = render / photo.
- ``decile_de00_raw``, ``decile_de00_gain`` (NEW): CIEDE2000 between the 10..90 % deciles of L*, a*, b* of the
  render and of the photo on E (unpaired), mean over the nine deciles.
- ``contamination`` (NEW; the design names it without a formula, defined here): share of E where the RENDER's colour
  is not acetate by this instrument's own test (neither luminance < 90 nor (luminance < 170 and max - min > 25)):
  background, glare or metal colour baked onto print. E lies on the photo's acetate, so such a pixel is foreign.
- paired (NEW): per E pixel d = gained dE00(projection) - gained dE00(Astra); block bootstrap over 8 x 8 px blocks
  (2000 resamples, fixed seed) -> 95 % CI of mean d.
- ``tol_bp`` (diagnostic only): the best bp 1-4 px r over shifts within 2.5 mm, against the same search at +/-40,
  +/-60 px along x.
- NEW (step 6): ``level_compare(...)['appearance']``: weighted C*ab p50 / p90 / mean and L* standard deviation of
  the AR and photo pixels, their differences and ratios (verdict: |C* p50 difference| <= ``CHROMA_TOL``, AND (fix
  round 1 of step 6: the median alone passed a print whose saturated tail was 1.4x the photo's, C* p90 26.06 against
  18.54 at photo 05's own angle) the C* p90 and mean ratios within ``CHROMA_SPREAD_RATIO``, AND the std ratio within
  ``CONTRAST_RATIO``; ``criteria`` names each; ``chroma_units``: the three chroma terms in units of their own window;
  the level verdict ``pass`` is unchanged); ``band_energy``: std of the masked DoG of L* at 0.5-3 mm (the havana's
  patch contrast) over a mask, at a given px/mm.
- NEW (fix round 2 of step 6: a chroma lift amplified off-hue noise and nothing graded hue): ``appearance['hue']``
  over the pixels with C*ab > ``HUE_C_MIN`` (weighted like the rest): the median hue h_ab (circular, about the havana
  band's centre), the share of all pixels that are magenta (``MAGENTA_HUE``, the dark-pixel noise hue: 3 % of photo
  05's brow pixels with C* > 4, 0.2 % above C* 8) and the share off ``HAVANA_HUE`` (20-100 deg; photo 05's brows have
  13 % of their C* > 4 pixels at 0-20 deg, real red-brown, so this share is reported, not graded). Verdict terms:
  |hue median AR - photo| <= ``HUE_TOL_DEG`` and the AR's magenta share at most ``MAGENTA_EXCESS`` over the photo's.

A6 / B5 (step 4, the Blender side; section at the end): exported GLBs are read with WP3's reader (g3d_host.glb), never
with the tool's own (g3d.printuv.glbmesh); ``print_uv_invariance``, ``a6_round_trip``, ``b5_material_control`` and
``uv_identity`` compare them.

Criteria (§8): B2 C1 gained dE00(P) <= dE00(A) - 0.5 and the CI's upper end < 0; C2 bp(P) - bp(A) >= max(0.15,
2 sigma_null) and bp(P) >= mu_null + 2 sigma_null; C3 energy at 0.4-1.6 mm in [0.8, 1.25] and at 1-4 px >= 0.7;
C4 contamination <= 2 %; a case needs >= 1000 E px. B3 spectrum error <= 0.25, correlation-length ratio 0.8-1.25,
raw decile dE00 <= 4.0 and <= Astra's - 3.0, and C3.
"""
from __future__ import annotations

import hashlib
import io
import json
import math

import numpy as np
from scipy import ndimage

from glasses3d.g3d_host import texture_utils as T

NULL_SHIFTS = [(dy, dx) for dx in (-60, -45, -30, 30, 45, 60) for dy in (-2, 0, 2)]
OCTAVES_MM = ((0.2, 0.4), (0.4, 0.8), (0.8, 1.6), (1.6, 3.2))
BLOCK_PX = 8
BOOTSTRAP_N = 2000
B2_MIN_PX = 1000
C1_MARGIN = 0.5
C2_MIN_GAIN = 0.15
C3_MID_BAND = (0.8, 1.25)
C3_FINE_MIN = 0.7
C4_MAX = 0.02
B3_SPECTRUM_MAX = 0.25
B3_CORR_LEN = (0.8, 1.25)
B3_DECILE_MAX = 4.0
B3_DECILE_VS_ASTRA = 3.0
TOL_MM = 2.5


# ----------------------------------------------------------------------------- primitives (own copies)
def lum(img) -> np.ndarray:
    return np.asarray(img, np.float64).mean(-1)


def masked_gauss(x, m, s: float) -> np.ndarray:
    x = np.asarray(x, np.float64)
    if s <= 0:
        return x
    mf = np.asarray(m, np.float64)
    return ndimage.gaussian_filter(x * mf, s) / np.maximum(ndimage.gaussian_filter(mf, s), 1e-6)


def bandpass(l, sup, s1: float, s2: float) -> np.ndarray:
    return masked_gauss(l, sup, s1) - masked_gauss(l, sup, s2)


def corr(a, b) -> float:
    a = np.asarray(a, np.float64) - np.mean(a)
    b = np.asarray(b, np.float64) - np.mean(b)
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else float("nan")


def acetate_like(img) -> np.ndarray:
    f = np.asarray(img, np.float64)
    l = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    return ((l < 170.0) & (sat > 25.0)) | (l < 90.0)


def null_spread(a_img, b_img, E, shifts=NULL_SHIFTS, partner=None) -> list[float]:
    """r of a vs b displaced by each (dy, dx) on E; pixels whose partner leaves ``partner`` (default E, the probe's
    rule) are dropped, and a shift counts only with > 200 pixels left. grade() passes the filter support as
    ``partner``: on a held-out STRIPE set, E is 4 mm bands with gaps and E & shifted E is often empty (FT1119 01:
    743-959 E px gave no null at all), while the photo's band image is defined on the whole support."""
    rs = []
    H, W = E.shape
    P = E if partner is None else np.asarray(partner, bool)
    for dy, dx in shifts:
        Ps = np.zeros_like(P)
        bs = np.zeros_like(b_img)
        ys, xs = slice(max(0, dy), H + min(0, dy)), slice(max(0, dx), W + min(0, dx))
        yd, xd = slice(max(0, -dy), H + min(0, -dy)), slice(max(0, -dx), W + min(0, -dx))
        bs[ys, xs] = b_img[yd, xd]
        Ps[ys, xs] = P[yd, xd]
        m = E & Ps
        if m.sum() > 200:
            rs.append(corr(a_img[m], bs[m]))
    return rs


def _gain(ren_px, photo_px):
    g, o, _ = T.quantile_gain(ren_px, photo_px)
    return g, o


def _gained(ren_px, g, o):
    return np.clip(T.apply_gain(ren_px, g, o), 0, 255)


def corr_length_mm(img, E, ppm: float, max_mm: float = 4.0) -> float | None:
    l = lum(img)
    H, W = E.shape
    for d in range(1, int(max_mm * ppm) + 1):
        rs = []
        for ang in np.linspace(0, np.pi, 4, endpoint=False):
            dx, dy = int(round(d * np.cos(ang))), int(round(d * np.sin(ang)))
            A = E[max(0, -dy):H - max(0, dy), max(0, -dx):W - max(0, dx)]
            B = E[max(0, dy):, max(0, dx):][:A.shape[0], :A.shape[1]]
            m = A & B
            if m.sum() < 200:
                continue
            la = l[max(0, -dy):H - max(0, dy), max(0, -dx):W - max(0, dx)][m]
            lb = l[max(0, dy):, max(0, dx):][:A.shape[0], :A.shape[1]][m]
            r = corr(la, lb)
            if np.isfinite(r):
                rs.append(r)
        if rs and np.mean(rs) < 0.5:
            return d / ppm
    return None


def decile_de00(ren_px, photo_px) -> float:
    la, lb = T.lab_of(ren_px), T.lab_of(photo_px)
    q = np.arange(10, 91, 10)
    qa = np.percentile(la, q, axis=0)
    qb = np.percentile(lb, q, axis=0)
    from skimage.color import deltaE_ciede2000
    return float(deltaE_ciede2000(qa, qb).mean())


def band_contrast(img, sup, E, ppm: float) -> np.ndarray:
    l = lum(img)
    mu = max(float(l[E].mean()), 1e-6)
    return np.array([float(bandpass(l, sup, s1 * ppm, s2 * ppm)[E].std()) / mu for s1, s2 in OCTAVES_MM])


def tolerant_bp(ren, photo, sup, E, ppm: float, rad_mm: float = TOL_MM, ren_sup=None) -> tuple[float, float]:
    a = bandpass(lum(ren), sup if ren_sup is None else ren_sup, 1, 4)
    b = bandpass(lum(photo), sup, 1, 4)
    rad = int(round(rad_mm * ppm))
    ys, xs = np.nonzero(E)
    H, W = E.shape

    def best_in(cx, cy):
        best = -1.0
        for dy in range(cy - rad, cy + rad + 1):
            for dx in range(cx - rad, cx + rad + 1):
                if (dx - cx) ** 2 + (dy - cy) ** 2 > rad * rad:
                    continue
                y2, x2 = ys + dy, xs + dx
                inb = (y2 >= 0) & (y2 < H) & (x2 >= 0) & (x2 < W)
                if inb.sum() < 0.6 * len(ys):
                    continue
                r = corr(a[ys[inb], xs[inb]], b[y2[inb], x2[inb]])
                if np.isfinite(r):
                    best = max(best, r)
        return best

    return best_in(0, 0), max(best_in(c, 0) for c in (-60, -40, 40, 60))


# ----------------------------------------------------------------------------- one render
def grade(ren, photo, support, E, ppm: float, *, with_null: bool = True, tolerant: bool = False,
          ren_support=None) -> dict:
    ren = np.asarray(ren, np.float64)
    photo = np.asarray(photo, np.float64)
    E = np.asarray(E, bool)
    support = np.asarray(support, bool)
    rs = support if ren_support is None else np.asarray(ren_support, bool)
    res = {"pixels": int(E.sum()), "render_support_px": int(rs.sum())}
    if E.sum() < 50:
        return res
    lr, lp = lum(ren), lum(photo)
    res["lum_r"] = corr(masked_gauss(lr, rs, 1)[E], masked_gauss(lp, support, 1)[E])
    b1r, b1p = bandpass(lr, rs, 1, 4), bandpass(lp, support, 1, 4)
    res["bp_1_4px_r"] = corr(b1r[E], b1p[E])
    s1, s2 = 0.25 * ppm, 1.0 * ppm
    b2r, b2p = bandpass(lr, rs, s1, s2), bandpass(lp, support, s1, s2)
    res["bp_mm_r"] = corr(b2r[E], b2p[E])
    if with_null:
        n1 = null_spread(b1r, b1p, E, partner=support)
        n2 = null_spread(b2r, b2p, E, partner=support)
        res["null_bp_1_4px"] = [float(np.mean(n1)), float(np.std(n1))] if n1 else None
        res["null_bp_1_4px_mean_abs"] = float(np.mean(np.abs(n1))) if n1 else None
        res["null_bp_1_4px_bias"] = float(abs(np.mean(n1))) if n1 else None
        res["null_bp_1_4px_n"] = int(len(n1))
        res["null_bp_mm"] = [float(np.mean(n2)), float(np.std(n2))] if n2 else None
    rp, pp = ren[E], photo[E]
    de = T.de00(rp, pp)
    res["de00_raw_mean"], res["de00_raw_median"] = float(de.mean()), float(np.median(de))
    g, o = _gain(rp, pp)
    deg = T.de00(_gained(rp, g, o), pp)
    res["de00_gain_mean"], res["de00_gain_median"] = float(deg.mean()), float(np.median(deg))
    res["gain"] = [float(x) for x in g]
    res["_de00_gain_px"] = deg
    # cross-fitted gain on an 8 x 8 px block checkerboard
    ys, xs = np.nonzero(E)
    black = ((ys // BLOCK_PX + xs // BLOCK_PX) % 2) == 0
    xf = np.empty(len(ys))
    for fit, app in ((black, ~black), (~black, black)):
        if fit.sum() >= T.GAIN_MIN_PAIRS and app.any():
            gg, oo = _gain(rp[fit], pp[fit])
            xf[app] = T.de00(_gained(rp[app], gg, oo), pp[app])
        else:
            xf[app] = deg[app]
    res["de00_gain_xfit_mean"] = float(xf.mean())
    for name, a1, a2 in (("energy_1_4px", 1.0, 4.0), ("energy_0.4_1.6mm", 0.4 * ppm, 1.6 * ppm)):
        a = bandpass(lr, rs, a1, a2)[E]
        b = bandpass(lp, support, a1, a2)[E]
        res[name] = float(a.std() / max(b.std(), 1e-6))
    bcr, bcp = band_contrast(ren, rs, E, ppm), band_contrast(photo, support, E, ppm)
    res["band_contrast"] = [float(x) for x in bcr]
    res["band_contrast_photo"] = [float(x) for x in bcp]
    res["spectrum_error"] = float(np.mean(np.abs(np.log2(np.maximum(bcr, 1e-6) / np.maximum(bcp, 1e-6)))))
    clr, clp = corr_length_mm(ren, E, ppm), corr_length_mm(photo, E, ppm)
    res["corr_len_mm"], res["corr_len_photo_mm"] = clr, clp
    res["corr_len_ratio"] = (clr / clp) if (clr and clp) else None
    res["decile_de00_raw"] = decile_de00(rp, pp)
    res["decile_de00_gain"] = decile_de00(_gained(rp, g, o), pp)
    res["contamination"] = float(1.0 - acetate_like(rp[None])[0].mean())
    if tolerant:
        t, nm = tolerant_bp(ren, photo, support, E, ppm, ren_sup=rs)
        res["tol_bp_r"], res["tol_null_max"] = t, nm
    return res


def block_bootstrap_ci(d: np.ndarray, ys: np.ndarray, xs: np.ndarray, *, seed: int = 0, n: int = BOOTSTRAP_N,
                       block: int = BLOCK_PX) -> tuple[float, float]:
    """95 % CI of mean(d) resampling 8 x 8 px blocks with replacement (fixed seed)."""
    key = (ys // block) * 1_000_003 + (xs // block)
    _, inv = np.unique(key, return_inverse=True)
    nb = int(inv.max()) + 1
    sums = np.bincount(inv, weights=d, minlength=nb)
    cnts = np.bincount(inv, minlength=nb).astype(np.float64)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, nb, size=(n, nb))
    means = sums[pick].sum(1) / np.maximum(cnts[pick].sum(1), 1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ----------------------------------------------------------------------------- one case: projection vs Astra
def evaluate_case(proj, astra, photo, support, E, ppm: float, *, seed: int = 0, tolerant: bool = False,
                  ren_support=None) -> dict:
    """Both renders graded on identical pixels (and identical render supports), the paired difference, and the B2 /
    B3 criteria."""
    E = np.asarray(E, bool)
    P = grade(proj, photo, support, E, ppm, tolerant=tolerant, ren_support=ren_support)
    A = grade(astra, photo, support, E, ppm, tolerant=tolerant, ren_support=ren_support)
    out = {"pixels": int(E.sum()), "px_per_mm": float(ppm), "b2_eligible": bool(E.sum() >= B2_MIN_PX)}
    if "_de00_gain_px" not in P or "_de00_gain_px" not in A:
        out.update({"projection": _strip(P), "astra": _strip(A), "criteria": None, "b2_pass": False,
                    "b3_pass": False})
        return out
    ys, xs = np.nonzero(E)
    d = P["_de00_gain_px"] - A["_de00_gain_px"]
    lo, hi = block_bootstrap_ci(d, ys, xs, seed=seed)
    out["paired"] = {"de00_gain_diff_mean": float(d.mean()), "ci95": [lo, hi]}
    out["projection"], out["astra"] = _strip(P), _strip(A)
    out["criteria"] = criteria(out["projection"], out["astra"], out["paired"])
    out["b2_pass"] = bool(out["b2_eligible"] and all(c["pass"] for k, c in out["criteria"].items() if k.startswith("C")))
    out["b3"] = b3(out["projection"], out["astra"])
    out["b3_pass"] = bool(out["b3"]["pass"])
    return out


def _strip(m: dict) -> dict:
    return {k: v for k, v in m.items() if not k.startswith("_")}


def criteria(P: dict, A: dict, paired: dict) -> dict:
    dP, dA = P["de00_gain_mean"], A["de00_gain_mean"]
    c1 = {"projection": dP, "astra": dA, "margin": dA - C1_MARGIN - dP, "ci95_hi": paired["ci95"][1]}
    c1["pass"] = bool(dP <= dA - C1_MARGIN and paired["ci95"][1] < 0)
    mu, sd = (P.get("null_bp_1_4px") or [float("nan"), float("nan")])
    bpP, bpA = P["bp_1_4px_r"], A["bp_1_4px_r"]
    if not (np.isfinite(mu) and np.isfinite(sd)):          # no null: C2 cannot be decided, so it fails
        c2 = {"projection": bpP, "astra": bpA, "null_mean": None, "null_std": None, "need": None, "margin": None,
              "pass": False, "reason": "null_unavailable"}
    else:
        need = max(C2_MIN_GAIN, 2 * sd)
        c2 = {"projection": bpP, "astra": bpA, "null_mean": mu, "null_std": sd, "need": need,
              "margin": float(min(bpP - bpA - need, bpP - (mu + 2 * sd)))}
        c2["pass"] = bool(np.isfinite(c2["margin"]) and c2["margin"] >= 0)
    e1, e2 = P["energy_0.4_1.6mm"], P["energy_1_4px"]
    c3 = {"energy_0.4_1.6mm": e1, "energy_1_4px": e2,
          "pass": bool(C3_MID_BAND[0] <= e1 <= C3_MID_BAND[1] and e2 >= C3_FINE_MIN)}
    c4 = {"contamination": P["contamination"], "pass": bool(P["contamination"] <= C4_MAX)}
    return {"C1": c1, "C2": c2, "C3": c3, "C4": c4}


def b3(P: dict, A: dict) -> dict:
    cl = P.get("corr_len_ratio")
    rows = {"spectrum": bool(P["spectrum_error"] <= B3_SPECTRUM_MAX),
            "corr_len": bool(cl is not None and B3_CORR_LEN[0] <= cl <= B3_CORR_LEN[1]),
            "decile": bool(P["decile_de00_raw"] <= B3_DECILE_MAX
                           and P["decile_de00_raw"] <= A["decile_de00_raw"] - B3_DECILE_VS_ASTRA),
            "C3": bool(C3_MID_BAND[0] <= P["energy_0.4_1.6mm"] <= C3_MID_BAND[1] and P["energy_1_4px"] >= C3_FINE_MIN)}
    rows["pass"] = all(rows.values())
    return rows


# ----------------------------------------------------------------------------- build step 3: A2, A4, A5, B3 (NEW)
A2_BP_MIN = 0.99
A2_ENERGY_MIN = 0.95
A2_DE00_MAX = 1.0
A4_LOG_ENERGY_MAX = 0.15
A4_DE00_MAX = 2.0
A4_SEAM_MAX = 1.2
A4_BANDS_MM = ((0.25, 1.0), (1.0, 4.0))
A5_P95_MAX = 3.0
B3_MIN_SHARE = 0.5
B3_MIN_PX = 1500


def round_trip(ren, photo, support, E, ppm: float) -> dict:
    """A2 (bake from v, render at v): bp 1-4 px r >= 0.99, energy (both C3 bands) >= 0.95, raw dE00 <= 1.0, on E."""
    m = grade(ren, photo, support, E, ppm, with_null=False)
    if "bp_1_4px_r" not in m:
        return {"pixels": m["pixels"], "pass": False, "reason": "too_few_pixels"}
    e = min(m["energy_1_4px"], m["energy_0.4_1.6mm"])
    crit = {"bp": bool(m["bp_1_4px_r"] >= A2_BP_MIN), "energy": bool(e >= A2_ENERGY_MIN),
            "de00_raw": bool(m["de00_raw_mean"] <= A2_DE00_MAX)}
    return {"pixels": m["pixels"], "bp_1_4px_r": m["bp_1_4px_r"], "energy_1_4px": m["energy_1_4px"],
            "energy_0.4_1.6mm": m["energy_0.4_1.6mm"], "de00_raw_mean": m["de00_raw_mean"], "criteria": crit,
            "pass": bool(all(crit.values()))}


def knockout(filled, truth, hole, support, tx: float) -> dict:
    """A4, one knocked-out hole (texture space, ``tx`` texels per mm): |log energy| of the masked DoG luminance
    inside the hole (eroded 1 texel) at 0.25-1 mm and 1-4 mm, the CIEDE2000 of the hole's mean Lab colour, and the
    seam ratio = mean gradient magnitude (sigma 1 texel) on the hole's 1-texel ring, filled / truth."""
    from skimage.color import deltaE_ciede2000
    filled = np.asarray(filled, np.float64)
    truth = np.asarray(truth, np.float64)
    hole = np.asarray(hole, bool)
    support = np.asarray(support, bool)
    Hc = ndimage.binary_erosion(hole, iterations=1)
    if Hc.sum() < 20:
        Hc = hole
    lf, lt = lum(filled), lum(truth)
    out = {"texels": int(hole.sum())}
    for k, (a, b) in enumerate(A4_BANDS_MM):
        bf = bandpass(lf, support, a * tx, b * tx)[Hc]
        bt = bandpass(lt, support, a * tx, b * tx)[Hc]
        out[f"log_energy_{a:g}_{b:g}mm"] = float(abs(math.log(max(bf.std(), 1e-6) / max(bt.std(), 1e-6))))
    la, lb = T.lab_of(filled[Hc]).mean(0), T.lab_of(truth[Hc]).mean(0)
    out["mean_colour_de00"] = float(deltaE_ciede2000(la[None], lb[None])[0])
    ring = (ndimage.binary_dilation(hole, iterations=1) & ~ndimage.binary_erosion(hole, iterations=1)) & support
    gf = np.hypot(*np.gradient(masked_gauss(lf, support, 1.0)))
    gt = np.hypot(*np.gradient(masked_gauss(lt, support, 1.0)))
    out["seam_ratio"] = float(gf[ring].mean() / max(gt[ring].mean(), 1e-6)) if ring.any() else None
    return out


def ring_baseline(truth, hole, ring) -> float | None:
    """A4 calibration (fix round 2): CIEDE2000 between the mean Lab colour of the TRUTH in the donor ring around a hole
    and inside the hole: how far the hole's colour is from what its surroundings predict (None with < 50 ring
    texels)."""
    from skimage.color import deltaE_ciede2000
    truth = np.asarray(truth, np.float64)
    ring = np.asarray(ring, bool)
    hole = np.asarray(hole, bool)
    if ring.sum() < 50 or hole.sum() < 20:
        return None
    la, lb = T.lab_of(truth[ring]).mean(0), T.lab_of(truth[hole]).mean(0)
    return float(deltaE_ciede2000(la[None], lb[None])[0])


def _spread(x) -> dict:
    x = np.asarray([v for v in x if v is not None], float)
    if not len(x):
        return {}
    return {"median": float(np.median(x)), "p25": float(np.percentile(x, 25)), "p75": float(np.percentile(x, 75)),
            "min": float(x.min()), "max": float(x.max()), "n": int(len(x))}


def a4_criteria(rows: list[dict]) -> dict:
    """A4 over the knocked-out holes of one part: median |log energy| <= 0.15 in both bands, median seam ratio <= 1.2,
    and the median mean-colour dE00 <= max(2.0, the median donor-ring baseline) (fix round 2, the CALIBRATED limit: a
    fill can only predict a hole's colour from its surroundings, so a hole whose truth differs from its ring by more
    than 2 dE00 is graded against that difference; the literal <= 2.0 is reported as ``mean_colour_literal``). Every
    statistic carries its spread over the holes (median, quartiles, min, max)."""
    if not rows:
        return {"pass": False, "reason": "no holes"}
    k1, k2 = (f"log_energy_{a:g}_{b:g}mm" for a, b in A4_BANDS_MM)
    e1 = float(np.median([r[k1] for r in rows]))
    e2 = float(np.median([r[k2] for r in rows]))
    de = [r["mean_colour_de00"] for r in rows]
    sr = [r["seam_ratio"] for r in rows if r["seam_ratio"] is not None]
    base = [r.get("ring_baseline_de00") for r in rows if r.get("ring_baseline_de00") is not None]
    limit = max(A4_DE00_MAX, float(np.median(base))) if base else A4_DE00_MAX
    out = {"median_log_energy": [e1, e2], "median_mean_colour_de00": float(np.median(de)), "max_mean_colour_de00":
           float(np.max(de)), "median_seam_ratio": float(np.median(sr)) if sr else None,
           "max_seam_ratio": float(np.max(sr)) if sr else None, "holes": len(rows),
           "mean_colour_limit": limit, "median_ring_baseline_de00": float(np.median(base)) if base else None,
           "spread": {"log_energy_0.25_1mm": _spread([r[k1] for r in rows]),
                      "log_energy_1_4mm": _spread([r[k2] for r in rows]),
                      "mean_colour_de00": _spread(de), "seam_ratio": _spread(sr), "ring_baseline_de00": _spread(base)}}
    out["criteria"] = {"log_energy": bool(e1 <= A4_LOG_ENERGY_MAX and e2 <= A4_LOG_ENERGY_MAX),
                       "mean_colour": bool(out["median_mean_colour_de00"] <= limit),
                       "seam": bool(sr and out["median_seam_ratio"] <= A4_SEAM_MAX)}
    out["mean_colour_literal"] = bool(out["median_mean_colour_de00"] <= A4_DE00_MAX)
    out["pass"] = bool(all(out["criteria"].values()))
    # step 4: the real-vs-real floor (each hole refilled by the caller with a nearby same-shaped REAL region of the same
    # photo, graded identically): how far real print itself differs in fine-scale energy over that distance. A
    # DIAGNOSTIC with its spread over the holes; it never changes the literal verdict and is no excuse for a failing
    # one (fix round 1 of step 4: the per-hole floors ran 0.09-1.55, and its neighbour region could lie on another
    # chart / facing / photo magnification; the caller now restricts it and says how).
    fl = [r["real_vs_real"] for r in rows if r.get("real_vs_real")]
    if fl:
        f1 = float(np.median([r[k1] for r in fl]))
        f2 = float(np.median([r[k2] for r in fl]))
        out["real_vs_real_floor"] = {"median_log_energy": [f1, f2], "holes": len(fl), "holes_without": len(rows) - len(fl),
                                     "median_mean_colour_de00": float(np.median([r["mean_colour_de00"] for r in fl])),
                                     "spread": {"log_energy_0.25_1mm": _spread([r[k1] for r in fl]),
                                                "log_energy_1_4mm": _spread([r[k2] for r in fl])},
                                     "role": "diagnostic only; never changes or excuses the A4 verdict"}
    return out


def seam_continuity(col_a, col_b) -> dict:
    """CIEDE2000 between the two texels of each uv-seam pair (same 3D point); p95 <= 3. Fix round 1: NOT the A5
    verdict any more (the tool's chart-seam harmonisation writes exactly these texels, so on its own pairs this passes
    by construction); kept for the pre-harmonisation diagnostic and the exact-pair report."""
    a = np.asarray(col_a, np.float64).reshape(-1, 3)
    b = np.asarray(col_b, np.float64).reshape(-1, 3)
    if not len(a):
        return {"pairs": 0, "pass": False, "reason": "no pairs"}
    d = T.de00(a, b)
    p95 = float(np.percentile(d, 95))
    return {"pairs": int(len(a)), "de00_p50": float(np.median(d)), "de00_p95": p95, "de00_max": float(d.max()),
            "pass": bool(p95 <= A5_P95_MAX)}


def evaluate_b3(proj, astra, photo, support, E, ppm: float, projected_share: float, *, ren_support=None) -> dict:
    """B3 (realism) for one held-out render, registered or not: eligible when the projected share (E pixels whose
    colour came from a photo texel, not a fill) is >= 0.5 and E has >= 1,500 px; graded on identical pixels against
    Astra's render: spectrum error <= 0.25, correlation-length ratio 0.8-1.25, raw decile dE00 <= 4.0 and <= Astra's
    - 3.0, and C3."""
    E = np.asarray(E, bool)
    out = {"pixels": int(E.sum()), "projected_share": float(projected_share),
           "eligible": bool(projected_share >= B3_MIN_SHARE and E.sum() >= B3_MIN_PX)}
    if E.sum() < 50:
        out.update({"pass": False, "reason": "too_few_pixels"})
        return out
    P = grade(proj, photo, support, E, ppm, with_null=False, ren_support=ren_support)
    A = grade(astra, photo, support, E, ppm, with_null=False, ren_support=ren_support)
    rows = b3(P, A)
    keys = ("spectrum_error", "corr_len_ratio", "corr_len_mm", "corr_len_photo_mm", "decile_de00_raw",
            "decile_de00_gain", "energy_0.4_1.6mm", "energy_1_4px", "de00_raw_mean", "de00_gain_mean", "bp_1_4px_r",
            "contamination")
    out.update({"projection": {k: P.get(k) for k in keys}, "astra": {k: A.get(k) for k in keys}, "criteria": rows,
                "pass": bool(rows["pass"])})
    return out


# ----------------------------------------------------------------------------- A5 graded off the seam (fix round 1)
A5_OFFSETS_TX = (1, 2)
A5_STEP_TX = 0.5
A5_WELD_MM = 1e-4


def sample_gltf(img, uv) -> np.ndarray:
    """Bilinear lookup with glTF semantics, written here on purpose: ``uv`` in atlas texel units (x right, y down; the
    glTF TEXCOORD times (W, H)), texel (i, j) centred at (j + 0.5, i + 0.5), clamp to edge. (n, C) float."""
    img = np.asarray(img, np.float64)
    H, W = img.shape[:2]
    x = np.asarray(uv, np.float64)[:, 0] - 0.5
    y = np.asarray(uv, np.float64)[:, 1] - 0.5
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]
    x0 = x0.astype(np.int64)
    y0 = y0.astype(np.int64)
    xa, xb = np.clip(x0, 0, W - 1), np.clip(x0 + 1, 0, W - 1)
    ya, yb = np.clip(y0, 0, H - 1), np.clip(y0 + 1, 0, H - 1)
    im = img.reshape(H, W, -1)
    return ((1 - fx) * (1 - fy) * im[ya, xa] + fx * (1 - fy) * im[ya, xb] + (1 - fx) * fy * im[yb, xa]
            + fx * fy * im[yb, xb])


def _inward(p0, p1, p2):
    d = p1 - p0
    n = np.stack([-d[:, 1], d[:, 0]], 1)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    sgn = np.sign(((p2 - p0) * n).sum(1))
    return n * np.where(sgn == 0, 1.0, sgn)[:, None]


def _inside(p, t0, t1, t2):
    v0, v1, v2 = t1 - t0, t2 - t0, p - t0
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-18, 1e-18, den)
    b1 = (d11 * d20 - d01 * d21) / den
    b2 = (d00 * d21 - d01 * d20) / den
    return (b1 >= -1e-9) & (b2 >= -1e-9) & (b1 + b2 <= 1 + 1e-9)


def uv_seam_samples(V, F, faces, uv, *, offsets=(0,) + A5_OFFSETS_TX, step: float = A5_STEP_TX,
                    weld_mm: float = A5_WELD_MM, chart_img=None, face_chart=None) -> dict:
    """Sample points on both sides of every uv seam, computed here from the geometry and the layout's per-corner uv
    (not from the tool's seam pairs): a uv seam is a mesh edge (positions welded at ``weld_mm``) shared by two layout
    faces whose uv differ at either end. Points every ``step`` texels along the edge in each face's uv; for offset k the
    point moves k texels along the edge's inward perpendicular in that face's uv (dropped when it leaves the face's
    triangle), and the WITHIN-chart baseline partner moves 3k texels (the same 2k separation, on one side only).
    Fix round 1 of step 4: with ``chart_img`` ((H, W) chart id of each texel, -1 outside; the caller's own rasteriser of
    the layout) and ``face_chart`` (chart id per layout face), a point is kept when the texel under it belongs to its
    face's CHART instead (offsets beyond one face: 7-8 texels and their 21-24 texel baselines leave q001's 0.5-1 mm
    triangles). Returns {k: {'a', 'b' (m, 2) uv across the seam, 'a3', 'b3' (m, 2) baseline partners, 'ok3' (m,)}}."""
    faces = np.asarray(faces, np.int64)
    uv = np.asarray(uv, np.float64)
    tri = np.asarray(V, np.float64)[np.asarray(F)[faces]]
    key = np.round(tri.reshape(-1, 3) / weld_mm).astype(np.int64)
    _u, wid = np.unique(key, axis=0, return_inverse=True)
    wid = wid.reshape(-1, 3)
    corner = np.tile(np.arange(3), (len(faces), 1))
    e = np.concatenate([wid[:, [0, 1]], wid[:, [1, 2]], wid[:, [2, 0]]])
    c = np.concatenate([corner[:, [0, 1]], corner[:, [1, 2]], corner[:, [2, 0]]])
    fi = np.tile(np.arange(len(faces)), 3)
    sw = e[:, 0] > e[:, 1]
    e = np.where(sw[:, None], e[:, ::-1], e)
    c = np.where(sw[:, None], c[:, ::-1], c)
    k = e[:, 0] * (np.int64(1) << 32) + e[:, 1]
    o = np.argsort(k, kind="stable")
    k, fi, c = k[o], fi[o], c[o]
    same = np.nonzero(k[1:] == k[:-1])[0]
    fa, fb, ca, cb = fi[same], fi[same + 1], c[same], c[same + 1]
    ua0, ua1 = uv[fa, ca[:, 0]], uv[fa, ca[:, 1]]
    ub0, ub1 = uv[fb, cb[:, 0]], uv[fb, cb[:, 1]]
    seam = (fa != fb) & ((np.abs(ua0 - ub0).max(1) > 1e-6) | (np.abs(ua1 - ub1).max(1) > 1e-6))
    ua2 = uv[fa, 3 - ca[:, 0] - ca[:, 1]]
    ub2 = uv[fb, 3 - cb[:, 0] - cb[:, 1]]
    idx = np.nonzero(seam)[0]
    empty = {"a": np.zeros((0, 2)), "b": np.zeros((0, 2)), "a3": np.zeros((0, 2)), "b3": np.zeros((0, 2)),
             "ok3": np.zeros(0, bool)}
    if not len(idx):
        return {int(kk): dict(empty) for kk in offsets}
    la = np.linalg.norm(ua1[idx] - ua0[idx], axis=1)
    lb = np.linalg.norm(ub1[idx] - ub0[idx], axis=1)
    ns = np.maximum(2, np.ceil(np.maximum(la, lb) / step)).astype(np.int64)
    rep = np.repeat(idx, ns)
    t = (np.concatenate([np.arange(n) for n in ns]) + 0.5) / np.repeat(ns, ns)
    pa = ua0[rep] + t[:, None] * (ua1[rep] - ua0[rep])
    pb = ub0[rep] + t[:, None] * (ub1[rep] - ub0[rep])
    na = _inward(ua0[rep], ua1[rep], ua2[rep])
    nb = _inward(ub0[rep], ub1[rep], ub2[rep])
    out = {}
    if chart_img is not None:
        chart_img = np.asarray(chart_img)
        face_chart = np.asarray(face_chart)
        Hc, Wc = chart_img.shape
        ca_, cb_ = face_chart[fa[rep]], face_chart[fb[rep]]

        def in_chart(q, c):
            ii = np.floor(q[:, 1]).astype(np.int64)
            jj = np.floor(q[:, 0]).astype(np.int64)
            ok_ = (ii >= 0) & (ii < Hc) & (jj >= 0) & (jj < Wc)
            ok_[ok_] &= chart_img[ii[ok_], jj[ok_]] == c[ok_]
            return ok_
    for kk in offsets:
        kk = int(kk)
        a, b = pa + kk * na, pb + kk * nb
        a3, b3 = pa + 3 * kk * na, pb + 3 * kk * nb
        if kk and chart_img is not None:
            ok = in_chart(a, ca_) & in_chart(b, cb_)
            ok3 = in_chart(a3, ca_) & in_chart(b3, cb_)
        elif kk:
            ok = _inside(a, ua0[rep], ua1[rep], ua2[rep]) & _inside(b, ub0[rep], ub1[rep], ub2[rep])
            ok3 = _inside(a3, ua0[rep], ua1[rep], ua2[rep]) & _inside(b3, ub0[rep], ub1[rep], ub2[rep])
        else:
            ok = np.ones(len(a), bool)
            ok3 = np.zeros(len(a), bool)
        out[kk] = {"a": a[ok], "b": b[ok], "a3": a3[ok], "b3": b3[ok], "ok3": ok3[ok]}
    return out


def seam_steps(img, samples: dict, *, p95_max: float = A5_P95_MAX, graded=None) -> dict:
    """A5 off the seam (fix round 1): per offset k, CIEDE2000 of the rendered (bilinear, glTF) colours on the two
    sides of every uv seam, k texels inside each chart, against the within-chart baseline at the same 2k separation
    (k and 3k texels inside one chart, both sides pooled). The verdict is on offsets >= 1 (the tool's seam
    harmonisation writes the seam texels themselves; a renderer's bilinear lookup reads their neighbours): at every
    graded offset, p95 across <= max(``p95_max``, the within-chart p95 at the same separation), i.e. the step across a
    seam is no larger than the design's 3 dE00 or than what the texture itself changes by over the same distance.
    **Deviation (measured, reported):** the design's literal "p95 <= 3 between texels of the same 3D point" can only
    be read at offset 0, which the tool writes; 1-2 texels inside the charts the texture itself already varies by
    p95 9-20 dE00 on FT1119 (seams lie on creases and edges), so a bare 3 dE00 would fail any texture there.
    Offset 0 (the seam itself, the bilinear footprint reaching the gutters) is reported only. ``graded`` (fix round 1
    of step 4): the offsets the verdict uses (default every offset >= 1); the caller passes offsets BEYOND a band the
    tool writes on purpose (the chart-seam reflection makes the two sides equal inside it), and the others are reported
    as not graded. Every row carries its p95 and max (the max is the worst case; p95 is not)."""
    out = {"offsets": {}}
    for kk, s in sorted(samples.items()):
        row = {"samples": int(len(s["a"]))}
        if len(s["a"]):
            a, b = sample_gltf(img, s["a"]), sample_gltf(img, s["b"])
            d = T.de00(a, b)
            row.update({"across_p50": float(np.median(d)), "across_p95": float(np.percentile(d, 95)),
                        "across_max": float(d.max())})
            m3 = np.asarray(s["ok3"], bool)
            if kk and m3.any():
                w = np.concatenate([T.de00(a[m3], sample_gltf(img, s["a3"][m3])),
                                    T.de00(b[m3], sample_gltf(img, s["b3"][m3]))])
                row.update({"within_samples": int(len(w)), "within_p50": float(np.median(w)),
                            "within_p95": float(np.percentile(w, 95)),
                            "across_over_within_p95": float(row["across_p95"] / max(np.percentile(w, 95), 1e-6))})
        out["offsets"][int(kk)] = row
    gset = sorted(int(k) for k in (graded if graded is not None else [k for k in out["offsets"] if k >= 1]))
    for kk, r in out["offsets"].items():
        r["graded"] = kk in gset
    crit = {f"p95_at_{kk}_texels": bool("across_p95" in r and "within_p95" in r
                                        and r["across_p95"] <= max(p95_max, r["within_p95"]))
            for kk, r in out["offsets"].items() if kk in gset}
    out["graded_offsets"] = gset
    out["criteria"] = crit
    out["pass"] = bool(crit and len(crit) == len(gset) and all(crit.values()))
    return out


# ------------------------------------------------------------------------------------------------ A6 / B5 (NEW)
# The Blender side (step 4): read exported GLBs with WP3's own reader (g3d_host.glb), never with the tool's
# (g3d.printuv.glbmesh), and compare them. Texture references are resolved to the image bytes' sha256 and the sampler
# (indices shift when another material gains a texture); materials are compared as canonical JSON.
B5_IGNORED = ("baseColorTexture", "baseColorFactor")
NORMAL_TOL_DEG = 0.05


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def resolved_material(g, index) -> dict | None:
    if index is None:
        return None
    mat = json.loads(json.dumps(g.materials[index]))

    def walk(x, key=""):
        if isinstance(x, dict):
            if key.endswith("Texture") and "index" in x:
                tex = g.doc["textures"][x["index"]]
                src = tex.get("source")
                data = g.image_bytes(src) if src is not None else None
                y = {k: walk(v, k) for k, v in x.items() if k != "index"}
                y["image_sha256"] = hashlib.sha256(data).hexdigest() if data is not None else None
                y["image_index"] = src
                y["sampler"] = g.doc.get("samplers", [])[tex["sampler"]] if "sampler" in tex else None
                return y
            return {k: walk(v, k) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v, key) for v in x]
        return x
    return walk(mat)


def strip_base_colour(mat: dict | None) -> dict | None:
    """The material without baseColorTexture / baseColorFactor (and without the resolved image index)."""
    if mat is None:
        return None
    m = json.loads(json.dumps(mat))
    pbr = m.get("pbrMetallicRoughness")
    if pbr is not None:
        for k in B5_IGNORED:
            pbr.pop(k, None)
        if not pbr:
            m.pop("pbrMetallicRoughness")
    return _drop_image_index(m)


def _drop_image_index(x):
    if isinstance(x, dict):
        return {k: _drop_image_index(v) for k, v in x.items() if k != "image_index"}
    if isinstance(x, list):
        return [_drop_image_index(v) for v in x]
    return x


def glb_view(path) -> dict:
    """{node name: [primitive rows]}: per primitive every attribute accessor (as stored), the triangles, the resolved
    material, the material name and the node's extras; '__glb__' holds the loaded file."""
    from glasses3d.g3d_host import glb as glbmod
    g = glbmod.load(path)
    out: dict = {"__glb__": g}
    for p in g.primitives:
        node = g.nodes[p.node_index]
        prim = g.doc["meshes"][p.mesh_index]["primitives"][p.primitive_index]
        arrays = {k: g.accessor(v) for k, v in prim.get("attributes", {}).items()}
        out.setdefault(p.node_name, []).append({
            "attributes": arrays, "triangles": p.triangles, "material": resolved_material(g, p.material_index),
            "material_name": (g.material(p.material_index) or {}).get("name"), "extras": node.get("extras") or {}})
    return out


def _nodes(view: dict) -> list[str]:
    return sorted(k for k in view if k != "__glb__")


def corner(row: dict, attr: str) -> np.ndarray:
    return np.asarray(row["attributes"][attr])[row["triangles"]]


def _print_info(row: dict) -> dict:
    raw = row["extras"].get("g3d_print_uv")
    if raw is None:
        return {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _angle_deg(a, b) -> np.ndarray:
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    c = (a * b).sum(-1) / np.maximum(np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1), 1e-30)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def _same_but_name(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a is b
    return _canon({k: v for k, v in a.items() if k != "name"}) == _canon({k: v for k, v in b.items() if k != "name"})


def a6_round_trip(original, painted, parts, *, print_png=None, allow_rename: bool = False) -> dict:
    """A6 (§8): on the painted GLB every print part's primitive has TEXCOORD_k (k = its node's g3d_print_uv texcoord),
    its baseColorTexture reads texCoord k, and its material JSON other than baseColorTexture / baseColorFactor is
    bit-identical to the same node's material in the original export (texture references resolved); every other
    node's material is identical in full. With ``print_png`` the GLB's base-colour image must decode to print.png's
    pixels exactly. ``allow_rename``: a material the tool made single-user (WP5_DESIGN §4: shared with a part that is
    not printed) is a copy with its own name; then the name alone may differ, and the rename is listed."""
    from PIL import Image
    A, B = glb_view(original), glb_view(painted)
    gB = B["__glb__"]
    rows, fails, renamed = {}, [], {}
    want_px = np.asarray(Image.open(print_png).convert("RGB")) if print_png else None
    for node in sorted(set(_nodes(A)) | set(_nodes(B))):
        if node not in A or node not in B or len(A[node]) != len(B[node]):
            fails.append(f"{node}: primitives differ")
            continue
        for a, b in zip(A[node], B[node]):
            if node in parts:
                k = _print_info(b).get("texcoord")
                info = ((b["material"] or {}).get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {}
                sa, sb = strip_base_colour(a["material"]), strip_base_colour(b["material"])
                same = _canon(sa) == _canon(sb)
                if not same and allow_rename and _same_but_name(sa, sb):
                    same = True
                    renamed[node] = [a["material_name"], b["material_name"]]
                row = {"texcoord": k, "has_texcoord": f"TEXCOORD_{k}" in b["attributes"],
                       "base_color_texcoord": info.get("texCoord", 0) if info else None,
                       "other_material_json_identical": same, "material": b["material_name"]}
                if want_px is not None and info:
                    got = np.asarray(Image.open(io.BytesIO(gB.image_bytes(info["image_index"]))).convert("RGB"))
                    row["print_pixels_identical"] = bool(got.shape == want_px.shape and np.array_equal(got, want_px))
                ok = (k is not None and row["has_texcoord"] and row["base_color_texcoord"] == k and same
                      and row.get("print_pixels_identical", True))
                row["pass"] = bool(ok)
                rows[node] = row
                if not ok:
                    fails.append(f"{node}: {row}")
            elif _canon(_drop_image_index(a["material"])) != _canon(_drop_image_index(b["material"])):
                fails.append(f"{node}: material changed (not a print part)")
    missing = [p for p in parts if p not in rows]
    if missing:
        fails.append(f"print parts missing: {missing}")
    return {"parts": rows, "renamed": renamed, "failures": fails[:20], "pass": not fails}


def b5_material_control(before, after, parts, *, allow_rename: bool = False) -> dict:
    """B5 (§8): attaching the print changes the GLB only in baseColorTexture / baseColorFactor: every primitive's
    every accessor array and triangle list bit-identical, node extras identical, materials identical except those two
    keys on the print parts' materials. Images that appear / disappear are listed (the print added; Astra's base
    colour textures no longer sampled). ``allow_rename`` as in a6_round_trip."""
    A, B = glb_view(before), glb_view(after)
    fails, changed, renamed = [], {}, {}
    for node in sorted(set(_nodes(A)) | set(_nodes(B))):
        if node not in A or node not in B or len(A[node]) != len(B[node]):
            fails.append(f"{node}: primitives differ")
            continue
        for a, b in zip(A[node], B[node]):
            if set(a["attributes"]) != set(b["attributes"]):
                fails.append(f"{node}: attribute set differs")
            for k in a["attributes"]:
                if k in b["attributes"] and not np.array_equal(a["attributes"][k], b["attributes"][k]):
                    fails.append(f"{node}: {k} differs")
            if not np.array_equal(a["triangles"], b["triangles"]):
                fails.append(f"{node}: indices differ")
            if _canon(a["extras"]) != _canon(b["extras"]):
                fails.append(f"{node}: node extras differ")
            ma, mb = a["material"], b["material"]
            if node in parts:
                sa, sb = strip_base_colour(ma), strip_base_colour(mb)
                if _canon(sa) != _canon(sb):
                    if allow_rename and _same_but_name(sa, sb):
                        renamed[node] = [a["material_name"], b["material_name"]]
                    else:
                        fails.append(f"{node}: material differs beyond the base colour")
                pa = (ma or {}).get("pbrMetallicRoughness") or {}
                pb = (mb or {}).get("pbrMetallicRoughness") or {}
                changed[node] = {k: [_drop_image_index(pa.get(k)), _drop_image_index(pb.get(k))]
                                 for k in B5_IGNORED if pa.get(k) != pb.get(k)}
            elif _canon(_drop_image_index(ma)) != _canon(_drop_image_index(mb)):
                fails.append(f"{node}: material of a non-print part changed")

    def shas(view):
        g = view["__glb__"]
        return {hashlib.sha256(g.image_bytes(i)).hexdigest() for i in range(len(g.doc.get("images", [])))}
    ia, ib = shas(A), shas(B)
    return {"pass": not fails, "failures": fails[:20], "base_colour_changes": changed, "renamed": renamed,
            "images_added": sorted(ib - ia), "images_no_longer_sampled": sorted(ia - ib)}


def print_uv_invariance(original, printed, parts, *, normal_tol_deg: float = NORMAL_TOL_DEG) -> dict:
    """What g3d.print_uv may change in the GLB: on the print parts only the vertex split (the new TEXCOORD_k seams),
    the new TEXCOORD_k (last) and corner normals within ``normal_tol_deg`` (the split polygons' custom normals);
    corner positions and every older TEXCOORD per triangle corner identical in order; every other node bit-identical;
    materials identical."""
    A, B = glb_view(original), glb_view(printed)
    rows, fails = {}, []
    for node in sorted(set(_nodes(A)) | set(_nodes(B))):
        if node not in A or node not in B or len(A[node]) != len(B[node]):
            fails.append(f"{node}: primitives differ")
            continue
        for a, b in zip(A[node], B[node]):
            if _canon(_drop_image_index(a["material"])) != _canon(_drop_image_index(b["material"])):
                fails.append(f"{node}: material changed")
            if node in parts:
                k = _print_info(b).get("texcoord")
                old_tc = sorted(t for t in a["attributes"] if t.startswith("TEXCOORD_"))
                row = {"texcoord": k, "triangles": [len(a["triangles"]), len(b["triangles"])],
                       "vertices": [len(a["attributes"]["POSITION"]), len(b["attributes"]["POSITION"])]}
                same_n = len(a["triangles"]) == len(b["triangles"])
                row["positions_identical_in_order"] = bool(same_n and np.array_equal(corner(a, "POSITION"),
                                                                                     corner(b, "POSITION")))
                row["older_texcoords_identical"] = bool(same_n and all(
                    t in b["attributes"] and np.array_equal(corner(a, t), corner(b, t)) for t in old_tc))
                row["new_texcoord_is_last"] = bool(k is not None and f"TEXCOORD_{k}" in b["attributes"]
                                                   and k == len(old_tc))
                ang = _angle_deg(corner(a, "NORMAL"), corner(b, "NORMAL")) if same_n else np.array([np.inf])
                row["normal_max_change_deg"] = float(ang.max())
                row["normal_changed_corner_share"] = float((ang > 1e-4).mean())
                ok = (row["positions_identical_in_order"] and row["older_texcoords_identical"]
                      and row["new_texcoord_is_last"] and row["normal_max_change_deg"] <= normal_tol_deg)
                row["pass"] = bool(ok)
                rows[node] = row
                if not ok:
                    fails.append(f"{node}: {row}")
            else:
                same = (set(a["attributes"]) == set(b["attributes"])
                        and all(np.array_equal(a["attributes"][t], b["attributes"][t]) for t in a["attributes"])
                        and np.array_equal(a["triangles"], b["triangles"]))
                if not same:
                    fails.append(f"{node}: geometry changed although it is not a print part")
    return {"parts": rows, "failures": fails[:20], "pass": not fails}


def uv_identity(painted, expected: dict, width: int, height: int, *, tol_texel: float = 1e-3) -> dict:
    """The renderer's uvs are the bake's: per part, TEXCOORD_k of every triangle corner (x = u * width, y = v *
    height) against ``expected[part]`` (M, 3, 2) texel coordinates in the same triangle order (the bake's layout,
    computed independently on the bake's GLB)."""
    B = glb_view(painted)
    rows = {}
    for part, want in expected.items():
        rs = B.get(part) or []
        if len(rs) != 1:
            rows[part] = {"pass": False, "reason": f"{len(rs)} primitives"}
            continue
        k = _print_info(rs[0]).get("texcoord")
        if f"TEXCOORD_{k}" not in rs[0]["attributes"]:
            rows[part] = {"pass": False, "reason": f"no TEXCOORD_{k}"}
            continue
        got = corner(rs[0], f"TEXCOORD_{k}").astype(np.float64) * np.array([width, height], np.float64)
        want = np.asarray(want, np.float64)
        err = float(np.abs(got - want).max()) if got.shape == want.shape else math.inf
        rows[part] = {"texcoord": k, "corners": int(want.size // 2), "max_err_texel": err,
                      "pass": bool(err <= tol_texel)}
    return {"parts": rows, "tol_texel": tol_texel, "pass": bool(rows) and all(r["pass"] for r in rows.values())}


# ----------------------------------------------------------------------------- AR levels (correction round, 2026-10-06)
# The AR render of a painted tool-test GLB against the photos' frame pixels (WP5 correction round, task item 1: the
# frame rendered lighter and lower-contrast than the photos). Written here again, never imported from the tool:
# CIE L* (D65) of sRGB pixels; frame pixels of an arview capture are its part-id render's print parts (exact palette
# colours within the capture's decode tolerance, eroded so no anti-aliased silhouette pixel counts); the photo's frame
# pixels are given as a mask by the caller (print parts rendered at the WP3b camera, eroded, inside the matte).
LEVEL_QUANTILES = (10, 50, 90)
LEVEL_TOL = 2.0                  # task item 1: darkest decile and median L* within 2 units of the photo's


def lstar(rgb) -> np.ndarray:
    """CIE L* (D65 white, Y from linearised sRGB with the Rec. 709 weights) of sRGB 0..255 colours (..., 3)."""
    c = np.asarray(rgb, np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    Y = lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722
    f = np.where(Y > (6 / 29) ** 3, np.cbrt(Y), Y / (3 * (6 / 29) ** 2) + 4 / 29)
    return 116.0 * f - 16.0


def level_stats(rgb_px) -> dict:
    """{'n', 'p10', 'p50', 'p90', 'mean'} of L* over an (N, 3) set of sRGB pixels."""
    L = lstar(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    if not len(L):
        return {"n": 0}
    q = np.percentile(L, LEVEL_QUANTILES)
    return {"n": int(len(L)), **{f"p{k}": round(float(v), 2) for k, v in zip(LEVEL_QUANTILES, q)},
            "mean": round(float(L.mean()), 2)}


def ar_frame_mask(part_id_rgb, palette: list, parts, *, tol: int = 24, erode: int = 1) -> np.ndarray:
    """(H, W) bool: pixels of an arview part-id render whose colour is one of ``parts``' palette colours
    (``palette`` = the capture report's ``id_palette`` rows {'rgb', 'name'}; a Blender duplicate suffix '.001' is
    ignored, q001.glb's own export carries it), within ``tol`` per channel, eroded ``erode`` px (the beauty render's
    anti-aliased silhouette pixels are mixtures with the background)."""
    a = np.asarray(part_id_rgb, np.int32)[..., :3]
    m = np.zeros(a.shape[:2], bool)
    want = set(parts)
    for row in palette:
        if str(row.get("name", "")).split(".")[0] in want:
            m |= (np.abs(a - np.asarray(row["rgb"], np.int32)[None, None]) <= tol).all(-1)
    return ndimage.binary_erosion(m, iterations=erode) if erode > 0 else m


HIGHLIGHT_L = 35.0               # an achromatic highlight: L* above this ...
HIGHLIGHT_C = 10.0               # ... and C*ab below this (a white / grey reflection; havana's dark phase is L* < 30,
                                 # its honey phase C* > 20)


def achromatic_highlight(rgb_px) -> np.ndarray:
    """(N,) bool: sRGB pixels that are white / grey reflections (L* > 35 and C*ab < 10), not havana pigment."""
    lab = T.lab_of(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    return (lab[:, 0] > HIGHLIGHT_L) & (np.hypot(lab[:, 1], lab[:, 2]) < HIGHLIGHT_C)


def _wq(x, w, qs):
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w) - 0.5 * w
    c /= w.sum()
    return np.interp(np.asarray(qs, float) / 100.0, c, x)


def level_stats_w(L, w) -> dict:
    q = _wq(L, w, LEVEL_QUANTILES)
    return {"n": int(len(L)), **{f"p{k}": round(float(v), 2) for k, v in zip(LEVEL_QUANTILES, q)},
            "mean": round(float((L * w).sum() / w.sum()), 2)}


def level_compare(ar_px, photo_px, *, tol: float = LEVEL_TOL, ar_group=None, photo_group=None,
                  exclude_highlights: bool = False, ar_highlight=None) -> dict:
    """AR frame pixels against the photo's: L* deciles of each, the differences (AR - photo) and the task-item-1
    verdict (darkest decile p10 and median p50 both within ``tol``). ``ar_group`` / ``photo_group`` (per-pixel group
    labels, e.g. brows vs temples): the photo's pixels are WEIGHTED so each group has the AR's share (a photo shows
    parts at other angles than an AR pose, e.g. photo 05's temple tops at grazing angle, 1/3 of its frame pixels,
    which the front pose hides). ``exclude_highlights``: highlights left out on both sides: on the photo side its own
    achromatic pixels (``achromatic_highlight``: the studio's reflections, fixed per photo); on the AR side
    ``ar_highlight`` when given, a per-pixel mask that must NOT depend on the print (e.g. the pixels a capture of the
    same GLB with a black albedo shows as achromatic highlights: the renderer's reflections alone), else the AR
    pixels' own colours. Fix round 1 of the correction round: the AR pixels' own test is NOT the renderer's alone; it
    moves with the print (at the front pose 42.1 % of the frame pixels with the 10bf932 print, 33.7 % with the
    calibrated one, 28.6 % with Astra's texture, 29.8 % with a black albedo), because light, desaturated print pixels
    pass it too, so a calibration graded by it can hide a washed-out print; ``ar_highlight_source`` records which."""
    ar_px = np.asarray(ar_px, np.float64).reshape(-1, 3)
    photo_px = np.asarray(photo_px, np.float64).reshape(-1, 3)
    ga = np.zeros(len(ar_px), np.int64) if ar_group is None else np.asarray(ar_group)
    gp = np.zeros(len(photo_px), np.int64) if photo_group is None else np.asarray(photo_group)
    out = {"tol": tol, "composition_matched": ar_group is not None, "highlights_excluded": bool(exclude_highlights)}
    if exclude_highlights:
        if ar_highlight is not None:
            ha = np.asarray(ar_highlight, bool).reshape(-1)
            if len(ha) != len(ar_px):
                raise ValueError(f"ar_highlight has {len(ha)} entries for {len(ar_px)} AR pixels")
        else:
            ha = achromatic_highlight(ar_px)
        hp = achromatic_highlight(photo_px)
        out["ar_highlight_source"] = "mask (independent of the print)" if ar_highlight is not None else             "the AR pixels' own colours (moves with the print)"
        out["highlight_share"] = {"ar": round(float(ha.mean()), 4) if len(ha) else None,
                                  "photo": round(float(hp.mean()), 4) if len(hp) else None}
        ar_px, ga, photo_px, gp = ar_px[~ha], ga[~ha], photo_px[~hp], gp[~hp]
    if not len(ar_px) or not len(photo_px):
        return {**out, "pass": False, "reason": "no pixels"}
    La, Lp = lstar(ar_px), lstar(photo_px)
    wa = np.ones(len(La))
    wp = np.ones(len(Lp))
    if ar_group is not None:
        share = {g: float((ga == g).mean()) for g in np.unique(ga)}
        out["ar_group_share"] = {str(g): round(v, 4) for g, v in share.items()}
        out["photo_group_share_raw"] = {str(g): round(float((gp == g).mean()), 4) for g in np.unique(gp)}
        for g in np.unique(gp):
            m = gp == g
            wp[m] = share.get(g, 0.0) / max(int(m.sum()), 1)
        if wp.sum() <= 0:
            return {**out, "pass": False, "reason": "no shared group"}
    a, p = level_stats_w(La, wa), level_stats_w(Lp, wp)
    d = {k: round(a[k] - p[k], 2) for k in ("p10", "p50", "p90", "mean")}
    # step 6 (NEW): chroma C*ab and contrast (weighted L* standard deviation) on the same pixels and weights; reported
    # beside the level verdict (``appearance`` holds their own verdict, ``pass`` stays the level's)
    ca, cp = chroma_stats_w(ar_px, wa), chroma_stats_w(photo_px, wp)
    sa, sp = _wstd(La, wa), _wstd(Lp, wp)
    app = {"ar_chroma": ca, "photo_chroma": cp,
           "chroma_ar_minus_photo": {k: round(ca[k] - cp[k], 2) for k in ("p50", "p90", "mean")},
           "chroma_ar_over_photo": {k: (round(ca[k] / cp[k], 3) if cp[k] > 0 else None) for k in ("p90", "mean")},
           "ar_lstar_std": round(sa, 2), "photo_lstar_std": round(sp, 2),
           "lstar_std_ratio": round(sa / sp, 3) if sp > 0 else None,
           "chroma_tol": CHROMA_TOL, "chroma_spread_ratio_range": list(CHROMA_SPREAD_RATIO),
           "contrast_ratio_range": list(CONTRAST_RATIO)}
    app["chroma_units"] = chroma_units(app)
    ha_, hp_ = hue_stats_w(ar_px, wa), hue_stats_w(photo_px, wp)       # fix round 2 of step 6: hue
    dh = (None if ha_["p50"] is None or hp_["p50"] is None else round(ha_["p50"] - hp_["p50"], 2))
    app["hue"] = {"ar": ha_, "photo": hp_, "p50_ar_minus_photo": dh,
                  "magenta_share_ar_minus_photo": round(ha_["magenta_share"] - hp_["magenta_share"], 4),
                  "off_band_share_ar_minus_photo": round(ha_["off_band_share"] - hp_["off_band_share"], 4),
                  "c_min": HUE_C_MIN, "tol_deg": HUE_TOL_DEG, "magenta_excess": MAGENTA_EXCESS,
                  "band_deg": list(HAVANA_HUE), "magenta_deg": list(MAGENTA_HUE)}
    app["criteria"] = {"chroma_p50": bool(abs(app["chroma_ar_minus_photo"]["p50"]) <= CHROMA_TOL),
                       **{f"chroma_{k}": _in(app["chroma_ar_over_photo"][k], CHROMA_SPREAD_RATIO) for k in ("p90", "mean")},
                       "contrast": _in(app["lstar_std_ratio"], CONTRAST_RATIO),
                       "hue_p50": bool(dh is not None and abs(dh) <= HUE_TOL_DEG),
                       "hue_magenta": bool(app["hue"]["magenta_share_ar_minus_photo"] <= MAGENTA_EXCESS)}
    app["pass"] = bool(all(app["criteria"].values()))
    return {**out, "ar": a, "photo": p, "ar_minus_photo": d, "appearance": app,
            "pass": bool(abs(d["p10"]) <= tol and abs(d["p50"]) <= tol)}


CHROMA_TOL = 1.0                 # step 6: median C*ab within 1 unit of the photo's (photos' brow medians: 4.0-6.0)
CONTRAST_RATIO = (0.85, 1.18)    # step 6: L* standard deviation within -15 % / +18 % (a factor 0.85 either way)
CHROMA_SPREAD_RATIO = (0.85, 1.18)   # fix round 1 of step 6: C*ab p90 and mean, AR / photo, within the contrast's
                                     # window (they are the chroma's spread as the std is the L*'s)


HAVANA_HUE = (20.0, 100.0)       # fix round 2 of step 6: the havana hue band (CIELAB h_ab, degrees); reported only
MAGENTA_HUE = (300.0, 360.0)     # ... the magenta hues: dark-pixel colour noise, not pigment (graded)
HUE_C_MIN = 4.0                  # pixels above this C*ab carry a hue (below it, 8-bit noise in the darks dominates)
HUE_TOL_DEG = 5.0                # |hue median AR - photo| within this (at C* 6, 5 deg is a hue difference dH ~0.5)
MAGENTA_EXCESS = 0.02            # the AR's magenta share at most 2 points over the photo's (photos, print form: 0.5-2.3 %)


def _in(x, rng) -> bool:
    return bool(x is not None and rng[0] <= x <= rng[1])


def hue_stats_w(rgb_px, w) -> dict:
    """Fix round 2 of step 6: hue of sRGB pixels, weighted: over the pixels with C*ab > ``HUE_C_MIN``, the median hue
    h_ab in degrees (circular: hues are taken within 180 deg of the havana band's centre, 60 deg, then the weighted
    median), and, as shares of ALL the pixels' weight, the chromatic share, the magenta share (``MAGENTA_HUE``) and the
    share off ``HAVANA_HUE``."""
    lab = T.lab_of(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    w = np.asarray(w, np.float64)
    if not len(lab) or w.sum() <= 0:
        return {"p50": None, "chromatic_share": 0.0, "magenta_share": 0.0, "off_band_share": 0.0}
    C = np.hypot(lab[:, 1], lab[:, 2])
    h = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0
    m = C > HUE_C_MIN
    centre = 0.5 * (HAVANA_HUE[0] + HAVANA_HUE[1])
    rel = (h - centre + 180.0) % 360.0 - 180.0 + centre
    tot = w.sum()
    mag = m & (h >= MAGENTA_HUE[0]) & (h < MAGENTA_HUE[1])
    off = m & ~((h >= HAVANA_HUE[0]) & (h <= HAVANA_HUE[1]))
    return {"p50": round(float(_wq(rel[m], w[m], (50,))[0]), 2) if m.any() and w[m].sum() > 0 else None,
            "chromatic_share": round(float(w[m].sum() / tot), 4), "magenta_share": round(float(w[mag].sum() / tot), 4),
            "off_band_share": round(float(w[off].sum() / tot), 4)}


def chroma_units(app: dict) -> list[float]:
    """[p50, p90, mean] of an ``appearance`` block in units of its own verdict's window (|x| <= 1 passes): the C*ab p50
    difference / ``CHROMA_TOL``; ln(AR / photo) of p90 and of the mean over ln of the ``CHROMA_SPREAD_RATIO`` edge on
    its side (a missing ratio counts 99)."""
    out = [round(app["chroma_ar_minus_photo"]["p50"] / CHROMA_TOL, 3)]
    for k in ("p90", "mean"):
        r = app["chroma_ar_over_photo"][k]
        if not r or r <= 0:
            out.append(99.0)
            continue
        edge = CHROMA_SPREAD_RATIO[1] if r >= 1.0 else 1.0 / CHROMA_SPREAD_RATIO[0]
        out.append(round(math.log(r) / math.log(edge), 3))
    return out


def _wstd(x, w) -> float:
    x = np.asarray(x, np.float64)
    w = np.asarray(w, np.float64)
    if not len(x) or w.sum() <= 0:
        return 0.0
    m = (x * w).sum() / w.sum()
    return float(np.sqrt(((x - m) ** 2 * w).sum() / w.sum()))


def chroma_stats_w(rgb_px, w) -> dict:
    """{'p50', 'p90', 'mean'} of CIELAB chroma C*ab (D65; the shared colour helper) of sRGB pixels, weighted."""
    lab = T.lab_of(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    C = np.hypot(lab[:, 1], lab[:, 2])
    w = np.asarray(w, np.float64)
    if not len(C):
        return {"p50": float("nan"), "p90": float("nan"), "mean": float("nan")}
    q = _wq(C, w, (50, 90))
    return {"p50": round(float(q[0]), 2), "p90": round(float(q[1]), 2), "mean": round(float((C * w).sum() / w.sum()), 2)}


def band_energy(img, mask, ppm: float, band_mm=(0.5, 3.0), L=None) -> float | None:
    """Step 6 (NEW): the std of the masked DoG of L* (sigma ``band_mm`` x ``ppm`` px) over ``mask`` (the print's
    patch contrast at 0.5-3 mm, the scale of havana spots); None below 200 pixels. ``L``: the image's L* when the
    caller already has it (fix round 3 of step 6, speed only). The DoG is computed on the mask's bounding box padded by
    the wider Gaussian's reach (``gaussian_filter`` truncates at 4 sigma): outside the mask both masked inputs are
    zero, so on the mask the result is the whole image's (checked by a test)."""
    m = np.asarray(mask, bool)
    if m.sum() < 200:
        return None
    L = lstar(np.asarray(img, np.float64)) if L is None else np.asarray(L, np.float64)
    r = int(4.0 * max(band_mm) * ppm + 0.5) + 2
    ys, xs = np.nonzero(m)
    y0, y1 = max(int(ys.min()) - r, 0), min(int(ys.max()) + r + 1, m.shape[0])
    x0, x1 = max(int(xs.min()) - r, 0), min(int(xs.max()) + r + 1, m.shape[1])
    mc = m[y0:y1, x0:x1]
    b = bandpass(L[y0:y1, x0:x1], mc, band_mm[0] * ppm, band_mm[1] * ppm)
    return float(b[mc].std())


# ----------------------------------------------------------------------------- strip streakiness (correction round)
STREAK_BAND_MM = (0.25, 1.0)


def directional_energy(img, support, E, tx: float, band_mm=STREAK_BAND_MM) -> dict | None:
    """Streakiness on an atlas strip chart (x = s along the part, y = t around it): the ratio of the gradient energy
    ACROSS the strip (d/dy) to ALONG it (d/dx) of the luminance's masked DoG (``band_mm``, read on ``support``), over
    the graded texels ``E`` (eroded 1 texel), and the band's std (contrast). Lines along the strip give a large ratio;
    real temple print is itself elongated along the arm, so a fill is judged against the photo texels of the same
    face. None below 200 texels."""
    E = ndimage.binary_erosion(np.asarray(E, bool), iterations=1)
    if E.sum() < 200:
        return None
    l = lum(img)
    sup = np.asarray(support, bool)
    B = bandpass(l, sup, band_mm[0] * tx, band_mm[1] * tx)
    gy, gx = np.gradient(B)
    ey, ex = float((gy[E] ** 2).mean()), float((gx[E] ** 2).mean())
    return {"texels": int(E.sum()), "across_over_along": round(ey / max(ex, 1e-12), 3),
            "band_std": round(float(B[E].std()), 3)}


def axis_streakiness(img, mask, band_px=(1.0, 4.0)) -> dict | None:
    """Streakiness of an elongated part in an IMAGE (an AR capture or a photo): the luminance's masked DoG (``band_px``
    sigmas) is differentiated across and along the principal axis of ``mask`` (the part's pixels, e.g. a temple);
    returns the ratio of gradient energy across the axis to along it (lines along the part give a large ratio) and the
    band's std, on ``mask`` eroded 1 px. None below 200 px."""
    m = np.asarray(mask, bool)
    E = ndimage.binary_erosion(m, iterations=1)
    if E.sum() < 200:
        return None
    ys, xs = np.nonzero(m)
    c = np.cov(np.stack([xs, ys]).astype(float))
    w, V = np.linalg.eigh(c)
    a = V[:, -1]                                   # (x, y) along the part
    n = np.array([-a[1], a[0]])
    B = bandpass(lum(img), m, band_px[0], band_px[1])
    gy, gx = np.gradient(B)
    ga = gx * a[0] + gy * a[1]
    gn = gx * n[0] + gy * n[1]
    return {"pixels": int(E.sum()), "across_over_along": round(float((gn[E] ** 2).mean() / max((ga[E] ** 2).mean(),
                                                                                                1e-12)), 3),
            "band_std": round(float(B[E].std()), 3), "axis_deg": round(float(np.degrees(np.arctan2(a[1], a[0]))), 1)}
