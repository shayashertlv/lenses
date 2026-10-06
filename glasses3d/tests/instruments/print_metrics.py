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
  std of r. Without a null, C2 fails ("null_unavailable").
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
