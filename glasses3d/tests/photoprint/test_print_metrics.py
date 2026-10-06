"""The independent instrument (tests/instruments/print_metrics.py) on images with known answers."""
import numpy as np
import pytest
from scipy import ndimage

from glasses3d.tests.instruments import print_metrics as PM

PPM = 4.8


def texture(shape, seed, sigma=1.6):
    t = ndimage.gaussian_filter(np.random.default_rng(seed).standard_normal(shape), sigma)
    return (t - t.min()) / (t.max() - t.min())


def havana(t):
    return np.stack([58 + 147 * t, 34 + 94 * t, 22 + 30 * t], -1)


@pytest.fixture(scope="module")
def scene():
    shape = (160, 420)
    photo = havana(texture(shape, 1))
    sel = np.zeros(shape, bool)
    sel[40:120, 30:390] = True
    E = ndimage.binary_erosion(sel, iterations=2)
    return photo, sel, E


def test_identical_render_scores_perfectly(scene):
    photo, sel, E = scene
    m = PM.grade(photo, photo, sel, E, PPM)
    assert m["bp_1_4px_r"] == pytest.approx(1.0) and m["lum_r"] == pytest.approx(1.0)
    assert m["de00_raw_mean"] < 1e-6 and m["de00_gain_mean"] < 0.05 and m["de00_gain_xfit_mean"] < 0.05
    assert m["energy_1_4px"] == pytest.approx(1.0) and m["energy_0.4_1.6mm"] == pytest.approx(1.0)
    assert m["spectrum_error"] < 1e-9 and m["corr_len_ratio"] == pytest.approx(1.0)
    assert m["contamination"] == 0.0 and m["decile_de00_raw"] < 1e-6
    assert abs(m["null_bp_1_4px"][0]) < 0.1 and m["null_bp_1_4px_mean_abs"] < 0.1


def test_blur_loses_energy_and_fails_c3(scene):
    photo, sel, E = scene
    blur = np.stack([PM.masked_gauss(photo[..., c], sel, 4.0) for c in range(3)], -1)
    m = PM.grade(blur, photo, sel, E, PPM)
    assert m["energy_1_4px"] < 0.7
    out = PM.evaluate_case(blur, photo * 0 + photo.mean((0, 1)), photo, sel, E, PPM)
    assert not out["criteria"]["C3"]["pass"]


def test_misregistration_drops_bp_to_the_null(scene):
    photo, sel, E = scene
    shifted = np.roll(photo, 12, 1)                       # 2.5 mm off
    m = PM.grade(shifted, photo, sel, E, PPM)
    assert abs(m["bp_1_4px_r"]) < 0.2


def test_contamination_counts_foreign_colours(scene):
    photo, sel, E = scene
    ren = photo.copy()
    ys, xs = np.nonzero(E)
    k = len(ys) // 20                                     # 5 % of E becomes background grey
    ren[ys[:k], xs[:k]] = 238.0
    m = PM.grade(ren, photo, sel, E, PPM)
    assert m["contamination"] == pytest.approx(k / len(ys), abs=1e-9)
    out = PM.evaluate_case(ren, ren, photo, sel, E, PPM)
    assert not out["criteria"]["C4"]["pass"]


def test_projection_beats_noise_on_every_b2_criterion(scene):
    photo, sel, E = scene
    proj = photo + np.random.default_rng(4).normal(0, 2.0, photo.shape)
    astra = havana(texture(photo.shape[:2], 9))           # same palette, unrelated print
    out = PM.evaluate_case(proj, astra, photo, sel, E, PPM)
    c = out["criteria"]
    assert out["b2_eligible"] and out["b2_pass"], c
    assert c["C1"]["margin"] > 0 and c["C1"]["ci95_hi"] < 0
    assert c["C2"]["margin"] > 0
    lo, hi = out["paired"]["ci95"]
    assert lo <= out["paired"]["de00_gain_diff_mean"] <= hi
    # the reverse comparison must fail C1 and C2
    rev = PM.evaluate_case(astra, proj, photo, sel, E, PPM)
    assert not rev["criteria"]["C1"]["pass"] and not rev["criteria"]["C2"]["pass"]


def test_bootstrap_ci_is_deterministic_and_brackets_the_mean():
    rng = np.random.default_rng(0)
    ys, xs = np.nonzero(np.ones((64, 64), bool))
    d = rng.normal(-1.0, 1.0, len(ys))
    a = PM.block_bootstrap_ci(d, ys, xs, seed=3)
    b = PM.block_bootstrap_ci(d, ys, xs, seed=3)
    assert a == b and a[0] < d.mean() < a[1] and a[1] < 0


def test_tolerant_bp_passes_a_known_offset(scene):
    """The tolerant form forgives a 1.5 mm offset by construction (why it is diagnostic only)."""
    photo, sel, E = scene
    shifted = np.roll(photo, 7, 1)                        # 1.46 mm
    m = PM.grade(shifted, photo, sel, E, PPM, with_null=False, tolerant=True)
    assert abs(m["bp_1_4px_r"]) < 0.3 and m["tol_bp_r"] > 0.8      # measured 0.17 vs 0.89


def test_null_exists_on_a_striped_held_out_set(scene):
    """Held-out stripes: 4 mm bands with gaps. The partner must lie in the support, not in E, or no shift survives."""
    photo, sel, E = scene
    xs = np.arange(E.shape[1])
    stripes = E & ((xs % 80) < 10)[None, :]                 # narrow bands, wide gaps: no 30-60 px shift aligns them
    proj = photo + np.random.default_rng(4).normal(0, 2.0, photo.shape)
    m = PM.grade(proj, photo, sel, stripes, PPM)
    assert m["null_bp_1_4px"] is not None and len(PM.null_spread(proj[..., 0], photo[..., 0], stripes)) == 0
    assert abs(m["null_bp_1_4px"][0]) < 0.2


def test_c2_fails_without_a_null(scene):
    photo, sel, E = scene
    P = {"bp_1_4px_r": 0.9, "null_bp_1_4px": None, "de00_gain_mean": 1.0, "energy_0.4_1.6mm": 1.0,
         "energy_1_4px": 1.0, "contamination": 0.0}
    A = {"bp_1_4px_r": 0.1, "de00_gain_mean": 9.0}
    c = PM.criteria(P, A, {"ci95": [-9.0, -7.0]})
    assert not c["C2"]["pass"] and c["C2"]["reason"] == "null_unavailable"
    assert c["C1"]["pass"] and c["C3"]["pass"] and c["C4"]["pass"]


def test_render_support_keeps_render_values_off_it_out(scene):
    """Fix round 1: the renders' filters read ``ren_support`` only (the evaluated fold), the photo's read ``support``;
    render values outside ren_support change nothing."""
    photo, sel, E = scene
    xx = np.arange(photo.shape[1])[None, :].repeat(photo.shape[0], 0)
    fold_b = (xx // 20) % 2 == 1
    rs = sel & fold_b
    Eb = E & fold_b
    ren = photo + np.random.default_rng(3).normal(0, 4, photo.shape)
    astra = np.clip(photo * 0.7 + 30, 0, 255)
    garbage = ren.copy()
    garbage[sel & ~fold_b] = np.random.default_rng(4).uniform(0, 255, (int((sel & ~fold_b).sum()), 3))
    a = PM.evaluate_case(ren, astra, photo, sel, Eb, PPM, ren_support=rs)
    b = PM.evaluate_case(garbage, astra, photo, sel, Eb, PPM, ren_support=rs)
    assert a["projection"] == b["projection"] and a["projection"]["render_support_px"] == int(rs.sum())
    c = PM.evaluate_case(garbage, astra, photo, sel, Eb, PPM)           # full render support: garbage leaks in
    assert c["projection"]["bp_1_4px_r"] != a["projection"]["bp_1_4px_r"]


# ----------------------------------------------------------------------------- build step 3: A2, A4, A5, B3
def test_round_trip_criteria(scene):
    photo, sel, E = scene
    assert PM.round_trip(photo, photo, sel, E, PPM)["pass"]
    blur = np.stack([PM.masked_gauss(photo[..., c], sel, 1.5) for c in range(3)], -1)
    r = PM.round_trip(blur, photo, sel, E, PPM)
    assert not r["pass"] and not r["criteria"]["energy"]
    off = photo + 4.0                                               # a raw colour offset fails dE00 <= 1
    r = PM.round_trip(off, photo, sel, E, PPM)
    assert r["criteria"]["bp"] and not r["criteria"]["de00_raw"]


def test_knockout_metrics_and_a4():
    truth = havana(texture((120, 300), 4, 2.0))
    sup = np.ones(truth.shape[:2], bool)
    hole = np.zeros_like(sup)
    hole[30:90, 100:180] = True
    same = PM.knockout(truth, truth, hole, sup, 8.0)
    assert same["log_energy_0.25_1mm"] < 1e-9 and same["mean_colour_de00"] < 1e-6 and same["seam_ratio"] == 1.0
    flat = truth.copy()
    flat[hole] = truth[hole].mean(0)                                 # a flat fill: energy gone, a seam appears
    f = PM.knockout(flat, truth, hole, sup, 8.0)
    assert f["log_energy_0.25_1mm"] > 1.0
    assert PM.a4_criteria([same, same, same])["pass"] and not PM.a4_criteria([f, f, same])["pass"]
    other = havana(texture((120, 300), 9, 2.0))                     # same statistics, other print
    swap = truth.copy()
    swap[hole] = other[hole]
    s = PM.knockout(swap, truth, hole, sup, 8.0)
    # one 60 x 80 hole: sampling spread of a correlated texture's energy (0.26 here), far below the flat fill's
    assert s["log_energy_0.25_1mm"] < 0.4 < f["log_energy_0.25_1mm"] and s["mean_colour_de00"] < 2.0


def test_a4_mean_colour_limit_is_calibrated_by_the_donor_ring():
    """Fix round 2: a hole whose truth differs from its surroundings (an amber blotch no donor contains) is graded
    against that difference (the ring baseline), not against a bare 2 dE00; the spread over holes is reported."""
    from scipy import ndimage
    truth = havana(texture((120, 300), 4, 2.0)).astype(float)
    sup = np.ones(truth.shape[:2], bool)
    hole = np.zeros_like(sup)
    hole[40:80, 120:170] = True
    blotch = truth.copy()
    blotch[hole] = np.clip(blotch[hole] + np.array([60.0, 35.0, 10.0]), 0, 255)     # the hole's truth is brighter
    ring = ndimage.binary_dilation(hole, iterations=24) & ~ndimage.binary_dilation(hole, iterations=8)
    base = PM.ring_baseline(blotch, hole, ring)
    assert base is not None and base > 5.0
    g = PM.knockout(truth, blotch, hole, sup, 8.0)               # a fill continuing the surroundings
    assert g["mean_colour_de00"] > 5.0                           # what the bare 2 dE00 would have failed
    g["ring_baseline_de00"] = base
    g["mean_colour_de00"] = 0.95 * base                          # a fill as good as the surroundings allow
    a = PM.a4_criteria([g, g, g])
    assert not a["mean_colour_literal"] and a["mean_colour_limit"] == base
    assert a["criteria"]["mean_colour"] and a["spread"]["mean_colour_de00"]["n"] == 3
    assert PM.ring_baseline(blotch, hole, np.zeros_like(hole)) is None


def test_a4_real_vs_real_floor_is_a_diagnostic_with_spread():
    """Step 4 fix round 1: the floor is reported with its spread and never changes the verdict (no 'within floor'
    alternative pass)."""
    k1, k2 = "log_energy_0.25_1mm", "log_energy_1_4mm"
    bad = {k1: 0.5, k2: 0.3, "mean_colour_de00": 1.0, "seam_ratio": 1.0,
           "real_vs_real": {k1: 0.9, k2: 0.6, "mean_colour_de00": 3.0}}
    a = PM.a4_criteria([bad, dict(bad), dict(bad, real_vs_real=None)])
    assert not a["pass"] and "log_energy_within_floor" not in a
    f = a["real_vs_real_floor"]
    assert f["holes"] == 2 and f["holes_without"] == 1 and f["role"].startswith("diagnostic")
    assert f["spread"]["log_energy_0.25_1mm"]["n"] == 2 and f["median_log_energy"] == [0.9, 0.6]


def test_seam_continuity():
    a = havana(texture((50, 50), 1)).reshape(-1, 3)
    assert PM.seam_continuity(a, a)["pass"] and PM.seam_continuity(a, a)["de00_p95"] == 0.0
    b = a.copy()
    b[: len(b) // 10] += 40                                          # 10 % of the pairs disagree strongly
    r = PM.seam_continuity(a, b)
    assert not r["pass"] and r["de00_p50"] == 0.0


def test_b3_realism(scene):
    photo, sel, E = scene
    noise = np.random.default_rng(3).uniform(0, 255, photo.shape)    # Astra-like unrelated texture
    other = havana(texture(photo.shape[:2], 7))                      # a real-looking print, not registered
    r = PM.evaluate_b3(other, noise, photo, sel, E, PPM, 0.9)
    assert r["eligible"] and r["criteria"]["spectrum"] and r["criteria"]["corr_len"] and r["criteria"]["decile"]
    assert r["pass"]
    blur = np.stack([PM.masked_gauss(other[..., c], sel, 4.0) for c in range(3)], -1)
    rb = PM.evaluate_b3(blur, noise, photo, sel, E, PPM, 0.9)
    assert not rb["pass"] and not rb["criteria"]["C3"]
    assert not PM.evaluate_b3(other, noise, photo, sel, E, PPM, 0.3)["eligible"]


def test_sample_gltf_texel_centres():
    """glTF semantics: texel (i, j) centred at (j + 0.5, i + 0.5); halfway between two centres is their mean."""
    img = np.arange(12, dtype=float).reshape(3, 4)[..., None]
    v = PM.sample_gltf(img, np.array([[0.5, 0.5], [2.5, 1.5], [1.0, 0.5], [0.5, 1.0]]))[:, 0]
    assert np.allclose(v, [0.0, 6.0, 0.5, 2.0])


def _two_chart_square(step: float):
    """A 10 x 10 mm square, two triangles, laid out as two charts 20 texels apart (2 texels/mm): the diagonal is a uv
    seam. The image is a smooth ramp in model x on both charts, plus ``step`` levels on chart B."""
    V = np.array([[0.0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]])
    F = np.array([[0, 1, 2], [0, 2, 3]])
    uv = np.array([[[2, 2], [22, 2], [22, 22]], [[42, 2], [62, 22], [42, 22]]], float)
    H, W = 26, 66
    yy, xx = np.mgrid[0:H, 0:W] + 0.5
    img = np.zeros((H, W, 3))
    # chart A: x_model = (u - 2) / 2 ; chart B: x_model = (u - 42) / 2 for the same ramp
    xa = (xx - 2) / 2
    xb = (xx - 42) / 2
    ramp = lambda x: np.stack([80 + 6 * x, 50 + 4 * x, 30 + 2 * x], -1)
    img[:, :33] = ramp(xa[:, :33])
    img[:, 33:] = ramp(xb[:, 33:]) + step
    return V, F, uv, img


def test_seam_steps_detect_a_step_and_pass_a_continuous_texture():
    V, F, uv, img = _two_chart_square(0.0)
    smp = PM.uv_seam_samples(V, F, np.array([0, 1]), uv)
    assert len(smp[0]["a"]) > 20 and len(smp[1]["a"]) > 10
    # the two sides of a sample are the same model point: at offset 0 the uv map back to equal x
    xm = lambda u: np.where(u[:, 0] >= 33, (u[:, 0] - 42) / 2, (u[:, 0] - 2) / 2)
    a, b = smp[0]["a"], smp[0]["b"]
    assert np.allclose(xm(a), xm(b), atol=1e-9) and ((a[:, 0] >= 33) != (b[:, 0] >= 33)).all()
    ok = PM.seam_steps(img, smp)
    assert ok["pass"], ok
    V, F, uv, img2 = _two_chart_square(25.0)
    bad = PM.seam_steps(img2, PM.uv_seam_samples(V, F, np.array([0, 1]), uv))
    assert not bad["pass"] and bad["offsets"][1]["across_p95"] > 3 * bad["offsets"][1]["within_p95"], bad


def test_seam_steps_grade_only_the_given_offsets_and_chart_mode_walks_past_the_face():
    """Step 4 fix round 1: the verdict reads only ``graded`` offsets (the others are reported, graded False); a graded
    offset without samples fails; with a chart image, points are kept where they land on their face's chart, so
    offsets past one triangle (the seam is 14.1 texels from the far corner; the 3k baseline of k = 5 is 15) still
    sample."""
    V, F, uv, img = _two_chart_square(25.0)
    img2 = img.copy()
    img2[:, 33:] -= 25.0                                                    # continuous again
    chart = np.zeros(img.shape[:2], np.int64)
    chart[:, 33:] = 1
    tri = PM.uv_seam_samples(V, F, np.array([0, 1]), uv, offsets=(0, 1, 5))
    ch = PM.uv_seam_samples(V, F, np.array([0, 1]), uv, offsets=(0, 1, 5), chart_img=chart, face_chart=np.array([0, 1]))
    assert tri[5]["ok3"].sum() == 0 and ch[5]["ok3"].sum() > 5 and len(ch[5]["a"]) >= len(tri[5]["a"])
    r = PM.seam_steps(img, ch, graded=[5])
    assert r["graded_offsets"] == [5] and r["offsets"][1]["graded"] is False and r["offsets"][5]["graded"] is True
    assert set(r["criteria"]) == {"p95_at_5_texels"}
    assert not PM.seam_steps(img, ch, graded=[1])["pass"]                   # the 25-level step, read at 1 texel
    assert not PM.seam_steps(img2, ch, graded=[9])["pass"]                   # no samples at 9: not a pass
    ok = PM.seam_steps(img2, ch, graded=[5])
    assert ok["pass"] and ok["offsets"][5]["across_max"] >= ok["offsets"][5]["across_p95"]
