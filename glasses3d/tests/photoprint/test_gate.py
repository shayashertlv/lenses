"""The §5.4 gate on synthetic images: FFT NCC equals the probe's direct loop, injected shifts are recovered, decoys
never reach the true peak, and the significance / agreement / pixel rules refuse what they must."""
import math

import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d_host.photoprint import gate as G

PPM = 5.0


def probe_ncc_shift_map(a, am, b, bm, R):
    """The scratch step2b_global.ncc_shift_map (direct loop), restated for the equivalence test."""
    H, W = a.shape
    out = np.full((2 * R + 1, 2 * R + 1), np.nan)
    ys, xs = np.nonzero(am)
    for iy, dy in enumerate(range(-R, R + 1)):
        for ix, dx in enumerate(range(-R, R + 1)):
            y2, x2 = ys + dy, xs + dx
            inb = (y2 >= 0) & (y2 < H) & (x2 >= 0) & (x2 < W)
            m = np.zeros(len(ys), bool)
            m[inb] = bm[y2[inb], x2[inb]]
            if m.sum() < 0.6 * len(ys):
                continue
            av, bv = a[ys[m], xs[m]], b[ys[m] + dy, xs[m] + dx]
            av, bv = av - av.mean(), bv - bv.mean()
            d = math.sqrt(float((av * av).sum() * (bv * bv).sum()))
            out[iy, ix] = (av * bv).sum() / d if d > 0 else np.nan
    return out


def texture(shape, seed, sigma=2.0):
    t = ndimage.gaussian_filter(np.random.default_rng(seed).standard_normal(shape), sigma)
    t = (t - t.min()) / (t.max() - t.min())
    return t


def havana(t):
    return np.stack([60 + 140 * t, 35 + 80 * t, 22 + 25 * t], -1)


def strip_mask(shape, y0, y1, x0, x1):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def test_fft_ncc_equals_the_direct_loop():
    rng = np.random.default_rng(0)
    a = ndimage.gaussian_filter(rng.standard_normal((70, 90)), 1.5)
    b = np.roll(a, (2, -3), (0, 1)) + 0.3 * rng.standard_normal((70, 90))
    am = strip_mask(a.shape, 20, 45, 15, 70) & (rng.random(a.shape) > 0.1)
    bm = strip_mask(a.shape, 5, 66, 3, 88) & (rng.random(a.shape) > 0.05)
    ref = probe_ncc_shift_map(a, am, b, bm, 8)
    maps, n = G.ncc_shift_maps([a], am, [b], bm, 8)
    got = maps[0]
    assert np.array_equal(np.isnan(ref), np.isnan(got))
    assert np.nanmax(np.abs(ref - got)) < 1e-9
    iy, ix = np.unravel_index(np.nanargmax(got), got.shape)
    assert (iy - 8, ix - 8) == (2, -3)


def _case(shift, shape=(300, 520), region=(130, 175, 60, 460), seed=1, noise=0.0):
    t = texture(shape, seed)
    photo_t = ndimage.shift(t, (shift[1], shift[0]), order=3, mode="nearest")   # photo(y + dy, x + dx) = t(y, x)
    photo = havana(photo_t) + noise * np.random.default_rng(seed + 9).standard_normal(shape + (3,))
    sel = strip_mask(shape, *region)
    ren = np.zeros(shape + (3,))
    ren[sel] = havana(t)[sel]
    matte = strip_mask(shape, region[0] - 30, region[1] + 30, 20, 500)
    E = ndimage.binary_erosion(sel, iterations=2)
    return ren, sel, photo, matte, E


@pytest.mark.parametrize("shift", [(0.0, 0.0), (3.0, -2.0), (1.4, 0.6), (-6.3, 4.2)])
def test_injected_shift_is_recovered(shift):
    ren, ok, photo, matte, E = _case(shift)
    g = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8)
    w = g["signals"]["warmth"]
    assert g["significant"], g
    assert abs(w["shift_px"][0] - shift[0]) < 0.2 and abs(w["shift_px"][1] - shift[1]) < 0.2, w
    assert g["residual_mm"] == pytest.approx(math.hypot(*w["shift_px"]) / PPM)
    assert g["registered"] == (g["residual_mm"] <= 0.8)
    assert w["decoy_max"] < w["peak"] - 0.15


def test_decoy_windows_never_contain_shifts_within_3_mm():
    for region in ((130, 175, 60, 460), (40, 260, 240, 290)):          # long along x, long along y
        E = strip_mask((300, 520), *region)
        R = int(math.ceil(G.GATE_R_MM * PPM))
        cs = G.decoy_centers(E, R, PPM)
        assert len(cs) == 6
        for cy, cx in cs:
            assert max(abs(cy), abs(cx)) - R >= 3.0 * PPM - 1e-9
    # diagonal region: a Euclidean R + 3 mm offset would leave only ~1 mm; the Chebyshev rule keeps 3 mm
    yy, xx = np.mgrid[0:300, 0:520]
    E = (np.abs((yy - 150) - 0.5 * (xx - 260)) < 12) & (np.abs(xx - 260) < 200)
    R = int(math.ceil(G.GATE_R_MM * PPM))
    for cy, cx in G.decoy_centers(E, R, PPM):
        assert max(abs(cy), abs(cx)) - R >= 3.0 * PPM - 1e-9


def test_disjoint_decoy_windows_never_overlap_and_the_design_spacing_does():
    """Fix round 2: the default layout spaces the decoys 2R + 1 px apart (independent null samples); §5.4's R + 3/6/9 mm
    puts 2R-wide windows 3 mm apart, and on the injected (-6.3, 4.2) px case its six decoys give only 2 distinct
    maxima (the rest repeat a neighbour's shift), so the 4-decoy rule refuses a perfect match."""
    E = strip_mask((300, 520), 130, 175, 60, 460)
    R = int(math.ceil(G.GATE_R_MM * PPM))
    d = G.decoy_distances_px(R, PPM)
    assert d[0] >= R + 3.0 * PPM and all(b - a >= 2 * R + 1 for a, b in zip(d, d[1:]))
    dd = G.decoy_distances_px(R, PPM, "design")
    assert all(b - a < 2 * R + 1 for a, b in zip(dd, dd[1:]))
    ren, ok, photo, matte, E = _case((-6.3, 4.2))
    g = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8, decoy_layout="design")
    assert g["signals"]["warmth"]["decoys_usable"] == 6 and g["decoys"] == 2 and g["reason"] == "too_few_decoys"
    g = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8)
    assert g["decoys"] == 6 and g["significant"] and g["decoy_layout"] == "disjoint"


def test_a_decoy_inside_the_window_would_return_the_true_peak():
    """Why decoys must sit outside: a displacement smaller than R lets the decoy search find the true alignment."""
    ren, ok, photo, matte, E = _case((0.0, 0.0))
    pok = ndimage.binary_erosion(matte, iterations=1)
    a = ren.mean(-1)
    b = photo.mean(-1)
    R = 20
    maps, _ = G.ncc_shift_maps([a], E, [b], pok, R + 15)
    true = G.window_peak(maps[0], R + 15, (0, 0), R)["peak"]
    inside = G.window_peak(maps[0], R + 15, (0, 12), R)["peak"]       # decoy displaced 12 px < R
    assert inside == pytest.approx(true)


def test_unrelated_photo_is_not_significant():
    ren, ok, _, matte, E = _case((0.0, 0.0), seed=1)
    _, _, other, _, _ = _case((0.0, 0.0), seed=5)
    g = G.gate_images(ren, ok, other, matte, E, PPM, reg_mm=0.8)
    assert not g["significant"] and not g["registered"]
    assert g["reason"] in ("warmth_peak_below_decoys", "irange_disagrees")


def test_irange_must_agree_with_warmth():
    shape, region = (300, 520), (130, 175, 60, 460)
    t1, t2 = texture(shape, 2), texture(shape, 3)
    sel = strip_mask(shape, *region)
    def rgb(a, b):
        return np.stack([100 + 40 * a, 30 + 200 * b, 100 - 40 * a], -1)
    photo = rgb(t1, ndimage.shift(t2, (0, 6), order=3, mode="nearest"))   # chroma magnitude displaced 6 px
    ren = np.zeros(shape + (3,))
    ren[sel] = rgb(t1, t2)[sel]
    matte = strip_mask(shape, 100, 205, 20, 500)
    E = ndimage.binary_erosion(sel, iterations=2)
    g = G.gate_images(ren, sel, photo, matte, E, PPM, reg_mm=0.8)
    assert g["signals"]["warmth"]["passes_rule"]
    assert not g["irange_agrees"] and g["reason"] == "irange_disagrees" and not g["significant"]


def test_too_few_pixels():
    ren, ok, photo, matte, E = _case((0.0, 0.0))
    small = np.zeros_like(E)
    small[150:160, 100:130] = True
    g = G.gate_images(ren, ok, photo, matte, small, PPM, reg_mm=0.8)
    assert g["reason"] == "too_few_pixels" and not g["significant"]


def _stripes(shape, period=40, width=20):
    """Even / odd vertical stripes along x (stand-ins for folds A / B)."""
    xx = np.arange(shape[1])[None, :].repeat(shape[0], 0)
    return (xx // width) % 2 == 0, (xx // width) % 2 == 1


def test_render_support_keeps_the_fitted_fold_out():
    """Fix round 1: with ren_support = the evaluated fold's rendered pixels, render values on the other fold cannot
    change the verdict; with the render's full support (the first build) they do."""
    ren, ok, photo, matte, E = _case((1.4, 0.6))
    fa, fb = _stripes(E.shape)
    Eb = E & fb
    garbage = ren.copy()
    garbage[ok & fa] = np.random.default_rng(7).uniform(0, 255, (int((ok & fa).sum()), 3))
    g1 = G.gate_images(ren, ok, photo, matte, Eb, PPM, reg_mm=0.8, ren_support=ok & fb)
    g2 = G.gate_images(garbage, ok, photo, matte, Eb, PPM, reg_mm=0.8, ren_support=ok & fb)
    assert g1["signals"] == g2["signals"] and g1["significant"] and g1["render_support_px"] == int((ok & fb).sum())
    full1 = G.gate_images(ren, ok, photo, matte, Eb, PPM, reg_mm=0.8)
    full2 = G.gate_images(garbage, ok, photo, matte, Eb, PPM, reg_mm=0.8)
    assert full1["signals"]["warmth"]["peak"] != full2["signals"]["warmth"]["peak"]


def test_decoy_count_is_reported_and_too_few_decoys_refuse():
    ren, ok, photo, matte, E = _case((1.4, 0.6))
    g = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8)
    w = g["signals"]["warmth"]
    rows = g["decoy_rows"]
    assert len(rows) == 6 and w["decoys_usable"] == 6 and all(r["overlap_at_offset"] >= 0.6 for r in rows)
    assert g["decoys"] == w["decoys"] == 6 - w["decoy_duplicates"] == sum(r["counted"] for r in rows)
    assert w["decoy_max"] == max(r["peak"] for r in rows if r["counted"])
    # a short region (60 px of E along x) in a matte no wider than it: at the R + 3 mm displacement (35 px) E keeps
    # only ~44 % overlap with the photo, although shifts nearer zero inside that decoy's window keep >= 60 %. The
    # first build counted such a decoy (any usable shift in its window); the design counts a decoy only when its own
    # displacement keeps 60 % (fix round 2)
    ren, ok, photo, matte, E = _case((1.4, 0.6), region=(130, 175, 200, 264))
    tight = strip_mask(E.shape, 100, 205, 200, 264)
    g = G.gate_images(ren, ok, photo, tight, E, PPM, reg_mm=0.8)
    near = [r for r in g["decoy_rows"] if max(abs(c) for c in r["offset_px"]) < 40]
    assert len(near) == 2 and all(r["usable_shifts"] > 0 and r["overlap_at_offset"] < 0.6 for r in near)
    assert g["decoys"] == 0 and g["signals"]["warmth"]["decoys_usable"] == 0
    assert g["reason"] == "too_few_decoys" and not g["significant"] and not g["registered"]
    assert g["signals"]["warmth"]["decoy_max_any_window"] is not None       # reported, never deciding


def test_overlapping_decoy_windows_count_one_maximum_once():
    """Two decoy windows that overlap and share their maximum (FT1119 Brow_Right 05 -> 01 B: +3 and +6 mm both
    0.4363 at the same shift) are ONE null sample."""
    Rm, R = 40, 10
    m = np.full((2 * Rm + 1, 2 * Rm + 1), 0.1)
    n = np.full(m.shape, 100.0)
    m[Rm + 0, Rm + 22] = 0.5                      # inside the windows centred at dx = 15 and dx = 25
    m[Rm + 0, Rm - 30] = 0.3
    rows = G.decoy_rows(m, n, 100.0, Rm, [(0, 15), (0, 25), (0, -30), (0, -22)], R)
    assert [r["argmax_px"] for r in rows[:2]] == [[0, 22], [0, 22]]
    assert rows[0]["counted"] and rows[1]["duplicate_of"] == 0 and not rows[1]["counted"]
    assert rows[2]["counted"] and rows[3]["counted"] is False and rows[3]["duplicate_of"] == 2
    n2 = n.copy()
    n2[Rm, Rm + 15] = 50.0                        # the displacement itself below 60 %: not usable, whatever its window
    rows = G.decoy_rows(m, n2, 100.0, Rm, [(0, 15), (0, 25)], R)
    assert not rows[0]["usable"] and rows[0]["usable_shifts"] == (2 * R + 1) ** 2 - 1
    assert rows[1]["counted"]                     # the shared maximum now counts once, for the decoy at 25


# ----------------------------------------------------------------------------- the strip null (build step 3)
def _strip_decoys(ren, ok, shifts, x0=60, x1=460):
    """Decoys of the strip null on a horizontal strip: the render's own colours cyclically shifted along x inside
    the strip (what ``gate.single_source_render`` builds along a region's strip coordinate)."""
    out = []
    for d in shifts:
        r = ren.copy()
        r[:, x0:x1] = np.roll(ren[:, x0:x1], d, axis=1)
        o = ok.copy()
        o[:, x0:x1] = np.roll(ok[:, x0:x1], d, axis=1)
        out.append({"D_mm": d / PPM, "ren": r, "ok": o & ok, "image_shift_px": float(min(d, (x1 - x0) - d))})
    return out


FAR = (40, 80, 120, 280, 320, 360)          # px; R + 3 mm = 35 px at 5 px/mm


@pytest.mark.parametrize("shift", [(0.0, 0.0), (3.0, -2.0)])
def test_strip_null_registers_a_true_match(shift):
    ren, ok, photo, matte, E = _case(shift)
    g = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8, decoys=_strip_decoys(ren, ok, FAR))
    w = g["signals"]["warmth"]
    assert g["null"] == "strip" and g["decoys"] == 6 and g["significant"], g["reason"]
    assert all(r["overlap_at_offset"] >= 0.6 for r in g["decoy_rows"])        # overlap kept by construction
    assert w["decoy_max"] < w["peak"] - 0.15 and g["registered"] == (g["residual_mm"] <= 0.8)
    assert abs(w["shift_px"][0] - shift[0]) < 0.2 and abs(w["shift_px"][1] - shift[1]) < 0.2


def test_strip_null_refuses_an_unrelated_photo():
    ren, ok, _, matte, E = _case((0.0, 0.0), seed=1)
    _, _, other, _, _ = _case((0.0, 0.0), seed=5)
    g = G.gate_images(ren, ok, other, matte, E, PPM, reg_mm=0.8, decoys=_strip_decoys(ren, ok, FAR))
    assert g["decoys"] == 6 and not g["significant"] and not g["registered"]


def test_strip_null_refuses_a_shifted_photo_by_its_residual():
    ren, ok, photo, matte, E = _case((10.0, 0.0))                         # 2 mm off
    g = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8, decoys=_strip_decoys(ren, ok, FAR))
    assert g["significant"] and g["residual_mm"] == pytest.approx(2.0, abs=0.05) and not g["registered"]


def test_strip_decoys_too_near_or_without_overlap_do_not_count():
    ren, ok, photo, matte, E = _case((0.0, 0.0))
    near = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8, decoys=_strip_decoys(ren, ok, (8, 16, 24, 30)))
    assert near["decoys"] == 0 and near["reason"] == "too_few_decoys"        # a window could reach the truth
    thin = _strip_decoys(ren, ok, FAR)
    for d in thin:
        d["ok"] = d["ok"].copy()
        d["ok"][:, 60:300] = False                                         # 40 % overlap left
    g = G.gate_images(ren, ok, photo, matte, E, PPM, reg_mm=0.8, decoys=thin)
    assert g["decoys"] == 0 and all(not r["counted"] for r in g["decoy_rows"])


class _Region:
    def __init__(self, faces):
        self.facing, self.axis, self.faces, self.rid, self.name = "front", 0, faces, 0, "flat[+x]/front"


def test_strip_shift_keeps_the_fold_and_the_period_is_a_multiple_of_8():
    from glasses3d.g3d_host.photoprint.regions import FOLD_GUARD, stripe_fold
    xs, ys = np.linspace(10.0, 66.0, 113), np.linspace(0.0, 10.0, 21)
    X, Y = np.meshgrid(xs, ys)
    V = np.c_[X.ravel(), Y.ravel(), np.zeros(X.size)]
    nx = len(xs)
    F = []
    for j in range(len(ys) - 1):
        for i in range(nx - 1):
            a = j * nx + i
            F += [[a, a + 1, a + nx], [a + 1, a + nx + 1, a + nx]]
    F = np.asarray(F)
    sc = G.StripCoords(V, F, _Region(np.arange(len(F))))
    assert sc.ok and sc.period % 8.0 == 0 and sc.period >= sc.length
    offs = sc.offsets()
    assert len(offs) == 6 and all(o % 8.0 == 0 and 0 < o < sc.period for o in offs)
    rng = np.random.default_rng(3)
    P = np.c_[rng.uniform(10, 66, 4000), rng.uniform(0, 10, 4000), np.zeros(4000)]
    for D in offs:
        q, found = sc.permutation(P, P, D, 0.4)
        assert found.mean() > 0.75
        fa, fb = stripe_fold(np.abs(P[found, 0])), stripe_fold(np.abs(P[q[found], 0]))
        both = (fa != FOLD_GUARD) & (fb != FOLD_GUARD)
        assert both.sum() > 1000 and np.all(fa[both] == fb[both]), D
