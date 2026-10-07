"""WP5 correction round (2026-10-06), task item 1: the photos' own shading and the AR level (WP5_DESIGN §6.6, §11.7).

**Diagnosis (the AR tool test of 10bf932).** The painted frame rendered lighter and lower-contrast than the photos:
measured by ``tests/photoprint/ar_levels.py`` (independent instrument ``print_metrics.level_compare``, frame pixels =
the print parts' part-id pixels of the capture against the print parts rendered at the photo's WP3b camera inside
its matte) the front pose had L* p10 / p50 22.63 / 36.82 against photo 05's 16.60 / 24.91, yaw -30 21.40 / 31.20
against photo 01's 13.29 / 25.15, while Astra's texture on the same model rendered front p10 9.61 (so the renderer's
clearcoat floor does not stop the darks from reaching the photos'). The print's texels ARE photo radiance: the studio's
light (front faces lit by the softbox, the top band's sheen) is in them, and arview (ACES filmic, a key light of 2 and
three's RoomEnvironment at 0.8; q001's havana: clearcoat 0.28 at coat roughness 0.16 over base roughness 0.23, read from
the GLB in fix round 1 of step 6, where this line's earlier "clearcoat 0.8" was found to be a misreading) lights that
radiance again as if it were albedo.

Two steps, both measured:

- **Flatten** (``fit_view`` / ``flatten``): per photo, an order-2 spherical-harmonic (9 coefficients) model of
  log-luminance (linear light, Rec. 709 Y) over the texel normal in that photo's CAMERA frame, fitted on the photo's
  donor texels (``view_samples.ok``: acetate, not clipped, not metal; the specular ones excluded) by Huber IRLS (the
  print's own variation is the noise of this regression: R^2 0.03-0.16 on FT1119), then DIVIDED OUT of every donor
  colour in linear light (achromatic: the three channels by the same factor), normalised so the median factor over
  the photo's donor texels is 1 (each photo keeps its exposure; the gains then align the photos). **Keep test** (task
  item 1; §6.6's registered-overlap test cannot run in single-source mode, nothing registers): for every view pair
  with >= ``PAIR_MIN`` same-surface texels (both donors, neither specular), the pair's own linear-light median gain is
  fitted (b -> a) and the mean CIEDE2000 and the median |log Y difference| are measured before and after; flatten is
  kept only if the overlap-weighted pooled dE00 falls by >= ``KEEP_DE00`` and the pooled median |dlog Y| falls too,
  AND (fix round 1 of the correction round: the review found the drop rested on one pair) the same holds with any
  one pair left out (``loo``: the pooled drop over the other pairs >= ``KEEP_DE00`` and their |dlog Y| falls; at least
  2 pairs). Every pair's own deltas are reported (``keep.per_pair``). Measured on FT1119, the correction bake
  (``data/wp5/pre_fr1c_bake/report.json``): pooled gained dE00 9.29 -> 8.84 over 10 pairs (drop 0.449), pooled
  median |dlog Y| 0.482 -> 0.446; per pair 04-05 11.79 -> 9.75, 02-04 7.03 -> 5.11, 04-06 9.33 -> 7.44, 01-05
  8.19 -> 7.93, worse 01-04 9.39 -> 9.62 and 01-06 8.48 -> 8.74; without 04-05 (photo 04 is the 60 deg top view) the
  drop is 0.031: that bake's flatten fails the leave-one-pair-out rule and is NOT kept under it. The bake of fix
  round 1 (``data/wp5/ft1119_bake/report.json``, the 'edges' footprint): 9.17 -> 8.73 over 10 pairs (drop 0.444),
  |dlog Y| 0.477 -> 0.441, 8 of 10 pairs better (54 % of the overlap texels; 01-04 9.23 -> 9.45 and 01-06
  8.36 -> 8.61 worse), without 04-05 (11.76 -> 9.77) a drop of 0.035: flatten NOT kept (``flatten='on'`` forces it).
  The factor is clipped to ``SHADE_CLIP`` (a fit is never extrapolated into a gain the data cannot carry).
- **AR level** (``level_map``): the print is photo radiance after the gains (the reference photo's exposure), not
  albedo; a calibration TO THE PHOTOS maps it to the albedo whose AR render looks like them:
  ``rgb_lin' = rgb_lin x gain x Y^(gamma - 1)`` (Y = the texel's linear luminance; chromaticity kept; step 6 adds a
  calibrated chroma factor ``sat``, its fix round 1 a power of the texel's C*ab, its fix round 2 the options to hold
  the factor at 1 or more and to act only inside the havana hue band, see ``level_map``), applied to every
  textured texel of the composed atlas as the LAST step (after compose's byte-identity check, before the gutters), so a
  calibration found by re-texturing a capture with the map equals the bake made with it. ``gain`` and ``gamma`` come
  from ``tests/photoprint/ar_calibrate.py`` (arview captures of the tool-test GLB at the poses whose camera lies within
  5 deg of a photo's own camera, against that photo: darkest decile and median L*, contrast, then chroma; the poses 18
  to 30 deg off are graded beside them, never fitted), recorded in the report with their
  source; ``level=None`` leaves the print as photo radiance and the report says the AR level is not calibrated.
"""
from __future__ import annotations

import numpy as np

from .. import raster
from .. import texture_utils as T
from . import signals as S

HUBER_K = 1.345
IRLS_ITERS = 8
FIT_MIN_TEXELS = 2000
PAIR_MIN = 500
KEEP_DE00 = 0.3
LOO_MIN_PAIRS = 2               # the leave-one-pair-out condition needs at least this many pairs
SHADE_CLIP = (1 / 3, 3.0)
Y_FLOOR = 1e-4
REC709 = np.array([0.2126, 0.7152, 0.0722])
FLATTEN_MODES = ("off", "test", "on")


def sh9(n: np.ndarray) -> np.ndarray:
    """Real order-2 spherical-harmonic basis (unnormalised) of unit vectors (k, 3) -> (k, 9)."""
    n = np.asarray(n, float)
    x, y, z = n[:, 0], n[:, 1], n[:, 2]
    return np.stack([np.ones_like(x), x, y, z, x * y, y * z, x * z, x * x - y * y, 3 * z * z - 1], 1)


def camera_normals(cam, N: np.ndarray) -> np.ndarray:
    """Outward unit normals (model frame; the NormFrame is a uniform scale + shift, so normals carry over) in the
    camera's (right, up, toward) frame."""
    r, u, t = raster.camera_basis(cam)
    N = np.asarray(N, float)
    return np.stack([N @ r, N @ u, N @ t], 1)


def ylin(col) -> np.ndarray:
    return T.srgb_to_linear(np.clip(np.asarray(col, float), 0, 255)) @ REC709


def fit_view(cam, N: np.ndarray, col: np.ndarray, use: np.ndarray) -> dict:
    """The photo's SH shading model on the texels ``use`` (normals ``N``, sRGB colours ``col``): {'coef' (9,),
    'median' (the median predicted log factor over ``use``: the normalisation), 'r2', 'texels', 'fitted'}."""
    use = np.asarray(use, bool)
    if int(use.sum()) < FIT_MIN_TEXELS:
        return {"fitted": False, "texels": int(use.sum()), "reason": f"< {FIT_MIN_TEXELS} donor texels"}
    A = sh9(camera_normals(cam, N[use]))
    y = np.log(np.maximum(ylin(col[use]), Y_FLOOR))
    w = np.ones(len(y))
    c = np.zeros(A.shape[1])
    for _ in range(IRLS_ITERS):
        sw = np.sqrt(w)
        c, *_ = np.linalg.lstsq(A * sw[:, None], y * sw, rcond=None)
        res = y - A @ c
        s = 1.4826 * float(np.median(np.abs(res - np.median(res))))
        k = HUBER_K * max(s, 1e-6)
        w = np.where(np.abs(res) <= k, 1.0, k / np.maximum(np.abs(res), 1e-12))
    pred = A @ c
    r2 = 1.0 - float(np.var(y - pred)) / max(float(np.var(y)), 1e-12)
    med = float(np.median(pred))
    sh = np.exp(np.clip(pred - med, np.log(SHADE_CLIP[0]), np.log(SHADE_CLIP[1])))
    return {"fitted": True, "coef": [float(x) for x in c], "median": med, "r2": round(r2, 4),
            "texels": int(use.sum()),
            "factor_p5_p50_p95": [round(float(x), 4) for x in np.percentile(sh, (5, 50, 95))]}


def factor(fit: dict, cam, N: np.ndarray) -> np.ndarray:
    """The normalised shading factor (median 1 over the fit's texels, clipped to ``SHADE_CLIP``) at normals ``N``."""
    if not fit.get("fitted"):
        return np.ones(len(N))
    p = sh9(camera_normals(cam, N)) @ np.asarray(fit["coef"], float) - float(fit["median"])
    return np.exp(np.clip(p, np.log(SHADE_CLIP[0]), np.log(SHADE_CLIP[1])))


def deshade(col: np.ndarray, f: np.ndarray) -> np.ndarray:
    """sRGB colours divided by the shading factor in linear light (all three channels alike), clipped to 0..255."""
    return np.clip(T.linear_to_srgb(T.srgb_to_linear(col) / np.asarray(f, float)[:, None]), 0, 255)


def _donor_use(s) -> np.ndarray:
    f = np.asarray(s.col, float)
    spec = (f.mean(-1) > S.SPECULAR_LUM) & (f.max(-1) - f.min(-1) < S.SPECULAR_SAT)
    return s.ok & ~spec


def pair_agreement(cols: dict, uses: dict, views: list[str]) -> dict:
    """Per view pair with >= ``PAIR_MIN`` texels both views use: the pair's linear-light median gain (b -> a), then
    the mean CIEDE2000 and the median |log Y difference - its median|; plus the overlap-weighted pooled values."""
    rows, wsum, de_sum, mad_sum = {}, 0, 0.0, 0.0
    for i, a in enumerate(views):
        for b in views[i + 1:]:
            ov = uses[a] & uses[b]
            n = int(ov.sum())
            if n < PAIR_MIN:
                continue
            ca, cb = cols[a][ov], cols[b][ov]
            la, lb = np.log(np.maximum(ylin(ca), Y_FLOOR)), np.log(np.maximum(ylin(cb), Y_FLOOR))
            d = la - lb
            g = float(np.exp(np.median(d)))
            cbg = np.clip(T.linear_to_srgb(T.srgb_to_linear(cb) * g), 0, 255)
            de = float(T.de00(ca, cbg).mean())
            mad = float(np.median(np.abs(d - np.median(d))))
            rows[f"{T_short(a)}-{T_short(b)}"] = {"texels": n, "de00_gained_mean": round(de, 3),
                                                  "mad_log_y": round(mad, 4)}
            wsum += n
            de_sum += n * de
            mad_sum += n * mad
    return {"pairs": rows, "pooled_de00": round(de_sum / wsum, 3) if wsum else None,
            "pooled_mad_log_y": round(mad_sum / wsum, 4) if wsum else None, "texels": int(wsum)}


def T_short(name: str) -> str:
    from .inputs import short_name
    return short_name(name)


def _pooled(rows: dict, key: str, skip: str | None = None) -> float | None:
    n = sum(r["texels"] for k, r in rows.items() if k != skip)
    return sum(r["texels"] * r[key] for k, r in rows.items() if k != skip) / n if n else None


def keep_test(before: dict, after: dict) -> dict:
    """The flatten keep rule on ``pair_agreement`` before / after (module docstring): the pooled gained dE00 falls by
    >= KEEP_DE00 and the pooled median |dlog Y| falls, over all pairs AND with any one pair left out (>= LOO_MIN_PAIRS
    pairs); per-pair deltas, the improving pairs and the leave-one-pair-out table are reported."""
    b, a = before["pairs"], after["pairs"]
    dde = before["pooled_de00"] - after["pooled_de00"]
    dmad = before["pooled_mad_log_y"] - after["pooled_mad_log_y"]
    per = {k: {"texels": b[k]["texels"], "de00_before": b[k]["de00_gained_mean"], "de00_after": a[k]["de00_gained_mean"],
               "de00_drop": round(b[k]["de00_gained_mean"] - a[k]["de00_gained_mean"], 3),
               "mad_log_y_drop": round(b[k]["mad_log_y"] - a[k]["mad_log_y"], 4)} for k in b}
    loo = {}
    for k in b:
        bd, ad = _pooled(b, "de00_gained_mean", k), _pooled(a, "de00_gained_mean", k)
        bm, am = _pooled(b, "mad_log_y", k), _pooled(a, "mad_log_y", k)
        if bd is not None:
            loo[k] = {"de00_drop": round(bd - ad, 3), "mad_log_y_drop": round(bm - am, 4)}
    loo_ok = len(b) >= LOO_MIN_PAIRS and all(r["de00_drop"] >= KEEP_DE00 and r["mad_log_y_drop"] > 0
                                             for r in loo.values())
    worst = min(loo, key=lambda k: loo[k]["de00_drop"]) if loo else None
    improved = [k for k, r in per.items() if r["de00_drop"] > 0]
    tot = sum(r["texels"] for r in per.values())
    pooled_ok = bool(dde >= KEEP_DE00 and dmad > 0)
    return {"pooled_de00_drop": round(dde, 3), "pooled_mad_log_y_drop": round(dmad, 4), "pooled_pass": pooled_ok,
            "per_pair": per, "pairs_improved": f"{len(improved)} of {len(per)}",
            "improved_texel_share": round(sum(per[k]["texels"] for k in improved) / tot, 4) if tot else None,
            "loo": loo, "loo_worst_pair": worst, "loo_min_de00_drop": loo[worst]["de00_drop"] if worst else None,
            "loo_pass": bool(loo_ok),
            "rule": (f"pooled gained dE00 must fall by >= {KEEP_DE00} and pooled median |dlog Y| must fall, over all "
                     f"pairs and with any one pair left out (>= {LOO_MIN_PAIRS} pairs)"),
            "pass": bool(pooled_ok and loo_ok)}


def flatten(job, tex, samples: dict, mode: str = "test") -> dict:
    """Fit every photo's shading, test whether dividing it out makes the photos agree better on the same surface, and
    return {'applied', 'mode', 'fits', 'before', 'after', 'keep': {...}, 'cols': {view: de-shaded colours (n, 3)} or
    None}. ``mode`` 'test' applies only when the keep test passes, 'on' always (reported), 'off' never (no fit)."""
    if mode not in FLATTEN_MODES:
        raise ValueError(f"unknown flatten mode {mode!r} (one of {FLATTEN_MODES})")
    if mode == "off":
        return {"applied": False, "mode": mode, "cols": None}
    views = list(samples)
    uses = {v: _donor_use(s) for v, s in samples.items()}
    fits, cols = {}, {}
    for v, s in samples.items():
        cam = job.views[v].camera
        fits[v] = fit_view(cam, tex.N, s.col, uses[v])
        f = factor(fits[v], cam, tex.N)
        c = np.asarray(s.col, float).copy()
        c[s.ok] = deshade(c[s.ok], f[s.ok])
        cols[v] = c
    before = pair_agreement({v: s.col for v, s in samples.items()}, uses, views)
    after = pair_agreement(cols, uses, views)
    if before["pooled_de00"] is None:
        keep = {"pass": False, "reason": f"no view pair with >= {PAIR_MIN} same-surface texels"}
    else:
        keep = keep_test(before, after)
    applied = mode == "on" or keep["pass"]
    return {"applied": bool(applied), "mode": mode, "fits": {T_short(v): f for v, f in fits.items()},
            "before": before, "after": after, "keep": keep, "cols": cols if applied else None,
            "model": "order-2 SH of log Y over the camera-frame normal, Huber IRLS, divided out in linear light, "
                     "median factor 1 per photo, clipped to [1/3, 3]"}


# ----------------------------------------------------------------------------- the AR level (calibrated to the photos)
CHROMA_REF = 10.0               # C*ab the chroma power pivots on (~ the geometric mean of the photos' brow C* p50 and
                                # p90, 4.4-5.8 and 15.6-18.5: the pivot lies inside the graded range, so sat stays ~1)
CHROMA_FLOOR = 1.0              # C*ab below this count as this (a power < 1 would blow up near grey)
CHROMA_FACTOR_MAX = 4.0         # the per-texel chroma factor never exceeds this
HAVANA_HUE_DEG = (20.0, 100.0)  # fix round 2 of step 6: the havana hue band (CIELAB h_ab) the lift may act in. Photo 05's
                                # brow pixels with C* > 4: 83 % in 20-80 deg, 13 % in 0-20 (red-brown), 3 % in 300-360
                                # (magenta: dark-pixel noise; 0.2 % above C* 8); 100-300 empty
CHROMA_HUE_TAPER_DEG = 15.0     # ... outside the band the lift falls linearly to none over this many degrees


def hue_weight(lab: np.ndarray, band=HAVANA_HUE_DEG, taper: float = CHROMA_HUE_TAPER_DEG) -> np.ndarray:
    """(...,) weight in [0, 1] of CIELAB colours ``lab`` (..., 3): 1 for a hue h_ab inside ``band`` (degrees), falling
    linearly to 0 at ``taper`` degrees outside it (the circular distance to the band's nearer edge)."""
    h = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0
    lo, hi = float(band[0]), float(band[1])
    d_lo = (lo - h) % 360.0                     # degrees below the band's low edge (going down from lo)
    d_hi = (h - hi) % 360.0                     # degrees above its high edge
    inside = (h >= lo) & (h <= hi)
    d = np.where(inside, 0.0, np.minimum(d_lo, d_hi))
    return np.clip(1.0 - d / max(float(taper), 1e-9), 0.0, 1.0)


def chroma_factor(rgb: np.ndarray, level: dict | None):
    """The per-texel chroma factor of ``level_map``, from the input sRGB colour's CIELAB C*ab and h_ab:
    g = sat x (max(C*ab, CHROMA_FLOOR) / CHROMA_REF)^(chroma_power - 1), clipped to ``CHROMA_FACTOR_MAX``; then (fix
    round 2 of step 6, both optional) ``chroma_hold``: g = max(g, 1), the factor never greys a colour; ``chroma_hue``
    [lo, hi] degrees: f = 1 + w(h) x (g - 1) with w = ``hue_weight`` (1 inside the band, 0 beyond ``CHROMA_HUE_TAPER_DEG``
    outside it: an off-hue colour is left as it is). The scalar ``sat`` when chroma_power is 1 and neither option is
    set, so the step-6 map is reproduced byte for byte; without the two options this is fix round 1's map byte for
    byte."""
    lv = level or {}
    sat = float(lv.get("sat", 1.0))
    p = float(lv.get("chroma_power", 1.0))
    hold = bool(lv.get("chroma_hold", False))
    band = lv.get("chroma_hue")
    if p == 1.0 and not hold and band is None:
        return sat
    lab = T.lab_of(np.asarray(rgb, float))
    C = np.maximum(np.hypot(lab[..., 1], lab[..., 2]), CHROMA_FLOOR)
    g = np.minimum(sat * (C / CHROMA_REF) ** (p - 1.0), CHROMA_FACTOR_MAX)
    if hold:
        g = np.maximum(g, 1.0)
    if band is not None:
        g = 1.0 + hue_weight(lab, band) * (g - 1.0)
    return g


def level_map(rgb: np.ndarray, level: dict | None) -> np.ndarray:
    """``rgb`` (..., 3) sRGB 0..255 -> the AR-calibrated albedo: linear rgb x gain x Y^(gamma - 1), clipped to 0..1
    linear; float sRGB out (the caller rounds). ``level`` = {'gain', 'gamma'[, 'sat'[, 'chroma_power'[,
    'chroma_hold'[, 'chroma_hue']]]]} or None.
    ``sat`` (step 6, default 1 = chromaticity kept, byte-identical to the correction round's map) scales each colour's
    distance from grey at its own luminance in linear light (rgb_lin -> Y + f x (rgb_lin - Y), Y = Rec. 709 luminance,
    unchanged), before the gain. Fix round 1 of step 6: one factor for every colour cannot match the photos' chroma at
    both ends (sat 1.322 matched the C* median at photo 05's own angle and overshot its p90 by +7.5, 26.06 against
    18.54: the renderer widens the saturated tail 1.1-1.3x while the level's darkening greys the low end), so the factor
    is a power of the texel's own C*ab, ``f = chroma_factor`` = sat x (C* / CHROMA_REF)^(chroma_power - 1)
    (chroma_power 1 = the step-6 map). A power below 1 makes the factor ABOVE 1 below the pivot and BELOW 1 above it:
    fix round 1's sat 1.107 / power 0.745 gives 1.99 at C* 1, 1.67 at C* 2, 1.11 at 10, 0.88 at 25 and 0.78 at 40, so
    it greyed the texture's honey spots (fix round 2's review: the delivered print's per-texel C* over the level-free
    bake's was x0.85 at radiance C* 15-25 and x0.74 at 25-60, the luminance map's darkening included) and amplified
    off-hue chroma noise (texels off 20-100 deg with C* > 5: 7.7 % radiance, 14.0 % delivered). Fix round 2 of step 6
    adds two options (``chroma_factor``): ``chroma_hold`` (the factor is never below 1: it lifts low chroma and leaves
    the saturated tail as the luminance map leaves it) and ``chroma_hue`` (the lift acts only inside the havana hue
    band). All are calibrated TO THE PHOTOS at their own angles with gain and gamma
    (``tests/photoprint/ar_calibrate.py``)."""
    rgb = np.asarray(rgb, float)
    if not level:
        return rgb.copy()
    lin = T.srgb_to_linear(rgb)
    Y = np.maximum(lin @ REC709, Y_FLOOR)
    f = chroma_factor(rgb, level)
    if np.ndim(f) or f != 1.0:
        f = np.asarray(f, float)[..., None] if np.ndim(f) else f
        lin = np.maximum(Y[..., None] + f * (lin - Y[..., None]), 0.0)
    k = float(level["gain"]) * Y ** (float(level["gamma"]) - 1.0)
    return T.linear_to_srgb(lin * k[..., None])


def apply_level_u8(img: np.ndarray, mask: np.ndarray, level: dict | None) -> np.ndarray:
    """The uint8 atlas with ``level_map`` applied to the ``mask`` texels (rounded)."""
    out = np.asarray(img, np.uint8).copy()
    if level:
        m = np.asarray(mask, bool)
        out[m] = np.clip(np.rint(level_map(out[m].astype(float), level)), 0, 255).astype(np.uint8)
    return out
