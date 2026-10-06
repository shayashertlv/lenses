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
