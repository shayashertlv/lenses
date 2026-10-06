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

Criteria (§8): B2 C1 gained dE00(P) <= dE00(A) - 0.5 and the CI's upper end < 0; C2 bp(P) - bp(A) >= max(0.15,
2 sigma_null) and bp(P) >= mu_null + 2 sigma_null; C3 energy at 0.4-1.6 mm in [0.8, 1.25] and at 1-4 px >= 0.7;
C4 contamination <= 2 %; a case needs >= 1000 E px. B3 spectrum error <= 0.25, correlation-length ratio 0.8-1.25,
raw decile dE00 <= 4.0 and <= Astra's - 3.0, and C3.
"""
from __future__ import annotations

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
                    weld_mm: float = A5_WELD_MM) -> dict:
    """Sample points on both sides of every uv seam, computed here from the geometry and the layout's per-corner uv
    (not from the tool's seam pairs): a uv seam is a mesh edge (positions welded at ``weld_mm``) shared by two layout
    faces whose uv differ at either end. Points every ``step`` texels along the edge in each face's uv; for offset k the
    point moves k texels along the edge's inward perpendicular in that face's uv (dropped when it leaves the face's
    triangle), and the WITHIN-chart baseline partner moves 3k texels (the same 2k separation, on one side only).
    Returns {k: {'a', 'b' (m, 2) uv across the seam, 'a3', 'b3' (m, 2) baseline partners, 'ok3' (m,)}}."""
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
    for kk in offsets:
        kk = int(kk)
        a, b = pa + kk * na, pb + kk * nb
        a3, b3 = pa + 3 * kk * na, pb + 3 * kk * nb
        if kk:
            ok = _inside(a, ua0[rep], ua1[rep], ua2[rep]) & _inside(b, ub0[rep], ub1[rep], ub2[rep])
            ok3 = _inside(a3, ua0[rep], ua1[rep], ua2[rep]) & _inside(b3, ub0[rep], ub1[rep], ub2[rep])
        else:
            ok = np.ones(len(a), bool)
            ok3 = np.zeros(len(a), bool)
        out[kk] = {"a": a[ok], "b": b[ok], "a3": a3[ok], "b3": b3[ok], "ok3": ok3[ok]}
    return out


def seam_steps(img, samples: dict, *, p95_max: float = A5_P95_MAX) -> dict:
    """A5 off the seam (fix round 1): per offset k, CIEDE2000 of the rendered (bilinear, glTF) colours on the two
    sides of every uv seam, k texels inside each chart, against the within-chart baseline at the same 2k separation
    (k and 3k texels inside one chart, both sides pooled). The verdict is on offsets >= 1 (the tool's seam
    harmonisation writes the seam texels themselves; a renderer's bilinear lookup reads their neighbours): at every
    graded offset, p95 across <= max(``p95_max``, the within-chart p95 at the same separation), i.e. the step across a
    seam is no larger than the design's 3 dE00 or than what the texture itself changes by over the same distance.
    **Deviation (measured, reported):** the design's literal "p95 <= 3 between texels of the same 3D point" can only
    be read at offset 0, which the tool writes; 1-2 texels inside the charts the texture itself already varies by
    p95 9-20 dE00 on FT1119 (seams lie on creases and edges), so a bare 3 dE00 would fail any texture there.
    Offset 0 (the seam itself, the bilinear footprint reaching the gutters) is reported only."""
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
    crit = {f"p95_at_{kk}_texels": bool("across_p95" in r and "within_p95" in r
                                        and r["across_p95"] <= max(p95_max, r["within_p95"]))
            for kk, r in out["offsets"].items() if kk >= 1}
    out["criteria"] = crit
    out["pass"] = bool(crit and all(crit.values()))
    return out
