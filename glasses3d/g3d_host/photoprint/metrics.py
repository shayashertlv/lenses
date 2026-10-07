"""Runtime print measurements, migrated from the frozen WP5 test instrument.

The independent proof remains in tests/instruments/print_metrics.py and imports none of this package.
Do not use these runtime verdicts alone as proof of correctness. Numeric criteria are unchanged.
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

A5_OFFSETS_TX = (1, 2)

A5_STEP_TX = 0.5

A5_WELD_MM = 1e-4

B5_IGNORED = ("baseColorTexture", "baseColorFactor")

NORMAL_TOL_DEG = 0.05

LEVEL_QUANTILES = (10, 50, 90)

LEVEL_TOL = 2.0

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

def part_name_matches(name, parts):
    """Exact names take priority; tolerate only Blender's numeric duplicate suffix, preserving embedded dots."""
    name = str(name)
    base, dot, suffix = name.rpartition('.')
    return name in parts or bool(dot and len(suffix) == 3 and suffix.isdigit() and base in parts)


def ar_frame_mask(part_id_rgb, palette: list, parts, *, tol: int = 24, erode: int = 1) -> np.ndarray:
    """(H, W) bool: pixels of an arview part-id render whose colour is one of ``parts``' palette colours
    (``palette`` = the capture report's ``id_palette`` rows {'rgb', 'name'}; a Blender duplicate suffix '.001' is
    ignored, q001.glb's own export carries it), within ``tol`` per channel, eroded ``erode`` px (the beauty render's
    anti-aliased silhouette pixels are mixtures with the background)."""
    a = np.asarray(part_id_rgb, np.int32)[..., :3]
    m = np.zeros(a.shape[:2], bool)
    want = set(parts)
    for row in palette:
        if part_name_matches(row.get("name", ""), want):
            m |= (np.abs(a - np.asarray(row["rgb"], np.int32)[None, None]) <= tol).all(-1)
    return ndimage.binary_erosion(m, iterations=erode) if erode > 0 else m

HIGHLIGHT_L = 35.0

HIGHLIGHT_C = 10.0

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

CHROMA_TOL = 1.0

CONTRAST_RATIO = (0.85, 1.18)

CHROMA_SPREAD_RATIO = (0.85, 1.18)

HAVANA_HUE = (20.0, 100.0)

MAGENTA_HUE = (300.0, 360.0)

HUE_C_MIN = 4.0

HUE_TOL_DEG = 5.0

MAGENTA_EXCESS = 0.02

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

STREAK_BAND_MM = (0.25, 1.0)
