"""WP6 fit-4 (2026-10-07): the measured relief cue, the hinge angle (no theta in a side view; theta graded in a common
camera frame: the STOP-I fix), the hinge constraint, the pyramid topology and the decal standoff. Synthetic
(relief_fit.synthetic_setup: Lambert photos under SYNTH_LIGHT, exact mattes), no reference data needed."""
from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import measure as MS
from glasses3d.g3d_host import relief_cue as CU
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_hinge as RH
from glasses3d.g3d_host import relief_views as RV
from glasses3d.g3d_host.camera import Camera


def _pyr(**over):
    p = RF.prada_base_params()
    p.update(over)
    return FX.prada_pyramid_spec(**p)


@pytest.fixture(scope="module")
def synth():
    truth = _pyr(r_C=3.5)
    sj = RF.synthetic_setup(truth)
    slab = RV.Model.from_spec(sj, RF.slab_spec(**RF.prada_base_params()), "slab")
    return sj, sj.truth, slab


def _true_lines(sj, model, views=("05", "01", "02")):
    out = {}
    for key in views:
        near = RF.NEAR[key]
        fit = {"camera": sj.true_cams[key], "theta": sj.truth_theta[key]}
        out[key] = {}
        for cn, nodes in (("AB", ("A", "B")), ("AC", ("A", "C")), ("BC", ("B", "C"))):
            q = RF.model_crease_px(sj, model.build, fit, near, *nodes)
            if q is None:
                continue
            c = q.mean(0)
            d = np.linalg.svd(q - c)[2][0]
            t = (q - c) @ d
            out[key][cn] = ((c + d * t.min()).tolist(), (c + d * t.max()).tolist())
    return out


def _true_temple_fits(sj, views=("05", "01", "02")):
    """The true cameras and theta of the synthetic photos."""
    return {k: {"camera": sj.true_cams[k], "theta": dict(sj.truth_theta[k])} for k in views}


# --------------------------------------------------------------------------------------------- the pyramid family
def test_pyramid_family_is_valid_and_creases_against_the_base():
    tb = build_temple(_pyr(r_C=3.0))
    regions = {tuple(c["regions"]) for c in tb.tables["creases"]}
    assert {("F2", "F3"), ("F2", "F4"), ("F2", "base")} <= regions
    ps = tb.spec
    for n in ("A", "B", "E"):
        assert ps.nodes[n][2] == 0.0
    assert RF.crease_regions(tb, "A", "B") == ("F2", "base")
    assert RF.crease_regions(tb, "B", "C") == ("F2", "F4")
    f1 = build_temple(FX.prada_spec(**RF.prada_base_params()))
    assert RF.crease_regions(f1, "A", "B") == ("F1", "F2")
    # one parameter dict serves both families (the F1 keys are ignored by the pyramid)
    assert FX.prada_pyramid_spec(**RF.prada_base_params(r_A=1.0, rise=-0.3))["facets"].keys() == {"F2", "F3", "F4"}


def test_pyramid_family_free_set_and_linear_thickness():
    fam = RF.prada_family(RF.prada_base_params(), topology="pyramid")
    assert fam.names == [n for n, _ in RF.PYRAMID_FREE] and fam.info["topology"] == "pyramid"
    x = fam.x0()
    x[fam.names.index("t_ze")] = 6.5
    sp = fam.spec(x)
    assert sp["thickness"][0][1] == pytest.approx(5.0) and sp["thickness"][1][1] == pytest.approx(6.5)
    sl = fam.slab()
    assert "t_ze" in sl.names and "r_C" not in sl.names
    assert sl.spec(sl.x0())["facets"] == {}
    with pytest.raises(ValueError):
        RF.prada_family(RF.prada_base_params(), topology="nonsense")


# --------------------------------------------------------------------------------------------- decals
def test_decal_standoff_keeps_relief_in_proportion():
    lift = 0.05
    flat = MS.decal_standoff(np.array([0.050, 0.0505, 0.051]), lift)
    assert flat.max() <= lift + 1e-15 and flat.min() > 0.045
    ribbon = MS.decal_standoff(np.array([0.020, 0.045, 0.072]), lift)       # the Prada ribbon and shield
    assert ribbon.max() == pytest.approx(lift)
    assert ribbon[0] == pytest.approx(0.020 * lift / 0.072)                  # the relief kept in proportion
    assert np.all(np.diff(ribbon) > 0)                                       # no side wall collapses
    low = MS.decal_standoff(np.array([-0.1, 0.0, 0.01]), lift)
    assert low.min() == pytest.approx(MS.DECAL_MIN_LIFT_FRACTION * lift)


# --------------------------------------------------------------------------------------------- the cue
def test_cue_light_is_the_synthetic_light():
    assert np.allclose(CU.CUE_LIGHT, RF.SYNTH_LIGHT)


def test_crease_steps_tell_the_faceted_truth_from_the_slab(synth):
    sj, truth, slab = synth
    lines = _true_lines(sj, truth)
    photo = {k: CU.photo_steps(sj, k, lines[k]) for k in lines}
    for k, v in photo.items():                     # every crease is a photo step >= STEP_MIN_L (02's A-C excepted:
        for c, p in v.items():                     # the matched light leaves it weak there, as on the real 02)
            assert p is not None and (abs(p["step_L"]) >= 3.0 or (k, c) == ("02", "AC")), (k, c, p)
    fits = _true_temple_fits(sj)
    mt = {k: CU.model_steps(sj, truth, k, fits[k]["camera"], RF.NEAR[k], lines[k]) for k in lines}
    ms = {k: CU.model_steps(sj, slab, k, fits[k]["camera"], RF.NEAR[k], lines[k]) for k in lines}
    a, b = CU.step_misfit(photo, mt), CU.step_misfit(photo, ms)
    assert a["chi2"] < 0.5, a                      # the truth explains the photo's steps (up to one gain per view)
    assert b["chi2"] > 10.0 * max(a["chi2"], 0.1), b
    for v in a["views"].values():                  # the same signs as the photo
        assert np.all(np.sign(v["model_shade"]) == np.sign(v["photo_L"]))
    # scale blind: half the relief, the same misfit pattern (the gain absorbs it)
    half = RV.Model.from_spec(sj, _pyr(r_C=1.75), "half")
    mh = {k: CU.model_steps(sj, half, k, fits[k]["camera"], RF.NEAR[k], lines[k]) for k in lines}
    assert CU.step_misfit(photo, mh)["chi2"] < 1.0


def test_triangulation_recovers_the_truths_c_minus_b(synth):
    sj, truth, _ = synth
    lines = _true_lines(sj, truth, ("05", "02"))
    fits = _true_temple_fits(sj, ("05", "02"))
    nodes = {n: truth.build.spec.nodes[n] for n in ("B", "C")}
    tr = CU.triangulate(sj, lines["05"], lines["02"], fits["05"], fits["02"], nodes, sj.hinge["z_mm"])
    assert tr is not None
    assert tr["offset_CB_mm"] == pytest.approx(CU.model_offset_cb(truth.build), abs=0.15)
    assert tr["sigma_mm"] is not None and tr["sigma_mm"] > 0
    # a line whose click and snap disagree carries that disagreement
    big = CU.triangulate(sj, lines["05"], lines["02"], fits["05"], fits["02"], nodes, sj.hinge["z_mm"],
                         endpoint_px={("02", "BC", 0): 7.0, ("02", "BC", 1): 7.0})
    assert big["sigma_mm"] > 3 * tr["sigma_mm"]


def test_endpoint_sigmas_measure_the_snap_move():
    g = {"02": {"BC": [[0.0, 0.0], [0.0, 100.0]]}}
    s = {"02": {"BC": [[3.0, 0.0], [-2.0, 100.0]]}}
    e = CU.endpoint_sigmas(g, s)
    # measured along the snapped line's normal (it is rotated by atan(5 / 100) from the click)
    assert e[("02", "BC", 0)] == pytest.approx(3.0, abs=0.01) and e[("02", "BC", 1)] == pytest.approx(2.0, abs=0.01)
    # signed: snapped minus click along the snapped line's normal (-t_y, t_x); here a rotation (the ends move apart)
    m = CU.endpoint_sigmas(g, s, signed=True)
    assert m[("02", "BC", 0)] == pytest.approx(-3.0, abs=0.01) and m[("02", "BC", 1)] == pytest.approx(2.0, abs=0.01)


def test_s1_lines_copy_the_real_extent_and_endpoint_moves(synth):
    """Fix round 3: S1's synthetic crease lines match the real ones. A guide over a real snapped fraction [t0, t1] of
    the crease (widened by the snapper's end trim) snaps back to [t0, t1] of the TRUE crease; the measured line then
    carries the real signed endpoint moves (oriented like the real line), so the cue's click-vs-line sigmas measured on
    it equal the real moves, and the triangulated C - B moves by what such an error costs."""
    from glasses3d.g3d_host import relief_trace as RT
    from glasses3d.tests.proofs import wp6_prada as P
    sj, truth, _ = synth
    tfits = {k: {"camera": sj.true_cams[k], "theta": sj.truth_theta[k]} for k in ("05", "01", "02")}
    plan = {"05": {"BC": {"t": [0.08, 0.27], "move_px": [0.5, -0.4], "real_len_px": 48.0}},
            "02": {"BC": {"t": [0.59, 0.14], "move_px": [-7.0, 6.9], "real_len_px": 113.0}}}   # 02 runs C -> B
    gx = P.s1_lines(sj, truth, tfits, plan)
    cr = RT.trace_creases(sj, gx["guides"], truth, tfits)
    snapped = {k: {cn: (ln["p0"], ln["p1"]) for cn, ln in cr["views"][k]["lines"].items() if ln} for k in gx["guides"]}
    meas, exact, rec = P.s1_measured_lines(snapped, plan, gx["extent"])
    for name, (k, cn) in (("05 BC", ("05", "BC")), ("02 BC", ("02", "BC"))):
        t0, t1 = rec[name]["s1_snapped_t"]
        r0, r1 = plan[k][cn]["t"]
        assert (t1 - t0) * (r1 - r0) > 0                              # oriented like the real line
        assert abs(t0 - r0) <= 0.03 and abs(t1 - r1) <= 0.03            # the real fraction of the TRUE crease
        assert max(abs(v) for v in rec[name]["s1_snapped_off_true_crease_px"]) < 1.0
    eps = CU.endpoint_sigmas(gx["guides"], meas)
    assert eps[("02", "BC", 0)] == pytest.approx(7.0, abs=0.5) and eps[("02", "BC", 1)] == pytest.approx(6.9, abs=0.5)
    clean = P.s1_measured_lines(snapped, plan, gx["extent"], add_error=False)[0]
    assert np.allclose(np.array(clean["02"]["BC"]), np.array(exact["02"]["BC"]))
    tn = P.s1_tri_noise(sj, truth, tfits, exact, meas, eps, plan)
    assert abs(tn["error_exact_lines_mm"]) < 0.2                        # exact lines: the truth's C - B
    assert len(tn["sign_patterns_error_mm"]) == 16
    assert 1 <= tn["measured_rank_of_16_by_abs_error"] <= 16
    assert tn["sign_patterns_abs_max_mm"] >= abs(tn["error_measured_lines_mm"]) - 1e-9


def test_s1_side_views_carry_a_hinge_angle():
    """fit-4 forces theta 0 in a side view; S1 must not render its side views at exactly that value."""
    from glasses3d.tests.proofs import wp6_prada as P
    for k in ("05", "01"):
        assert set(P.S1_SIDE_TRUE[k]["theta"]) == {1, -1}
        assert min(abs(v) for v in P.S1_SIDE_TRUE[k]["theta"].values()) > 0.1
        Camera.from_dict(P.S1_SIDE_TRUE[k]["camera"])


# --------------------------------------------------------------------------------------------- the hinge angle
def test_side_views_fit_no_hinge_angle():
    """fit-4 (WP6_DESIGN §6: theta is scored on the 02 / 03 / 06 / 04 arm rows): a side view keeps theta 0 for both
    temples and refits only its camera; an angled view still fits theta."""
    sj = RF.synthetic_setup(_pyr(r_C=3.0), shape=(280, 450), views=("05", "02"))
    f05 = RV.fit_model_view(sj, sj.truth, "05")
    assert f05["theta"] == {1: 0.0, -1: 0.0}
    assert all(not t["observed"] and "side view" in t["why"] for t in f05["theta_fit"].values())
    f02 = RV.fit_model_view(sj, sj.truth, "02")
    assert all("why" not in t for t in f02["theta_fit"].values())
    assert RV.crease_fit({"05": f05}, "05") == {"camera": f05["camera"], "theta": {1: 0.0, -1: 0.0}}


def test_apparent_theta_sees_through_a_yaw_trade():
    """A fit that put part of the temple's opening into the camera yaw (theta -1.2, yaw +0.8: the fit-3 S1's 02) has a
    raw theta error of 1.2 deg while its placement of the facet zone is nearly right; theta in the common (true)
    camera frame shows that."""
    from glasses3d.tests.proofs import wp6_theta_frame as TF
    sj = RF.synthetic_setup(_pyr(r_C=3.0), theta={"02": {1: 1.2, -1: 0.0}}, views=("02",))
    ct = sj.true_cams["02"]
    exact = TF.apparent_theta(sj, sj.truth, {"camera": ct, "theta": {1: 1.2, -1: 0.0}}, "02", 1)
    assert abs(exact["error_deg"]) < 1e-3 and exact["placement_error_mm"] < 1e-6
    # the camera that best hides the opening with theta 0 (yaw, scale and shift fitted on the facet zone's image)
    from scipy import optimize
    from glasses3d.g3d_host import cameras as C
    W = TF._zone(sj, sj.truth, 1)
    U = TF._proj(sj, ct, 1, 1.2, W)

    def cam_of(v):
        return Camera(ct.yaw + v[0], ct.pitch, ct.roll, ct.perspective, ct.scale * (1 + v[1]), ct.center_x + v[2],
                      ct.center_y + v[3])
    r = optimize.least_squares(lambda v: (C.project(sj.frame.to_norm(W), cam_of(v)) - U).ravel(), np.zeros(4))
    traded = TF.apparent_theta(sj, sj.truth, {"camera": cam_of(r.x), "theta": {1: 0.0, -1: 0.0}}, "02", 1)
    assert traded["raw_error_deg"] == pytest.approx(-1.2) and abs(r.x[0]) > 0.3      # the yaw took the opening
    assert traded["placement_error_mm"] < 0.1                                          # the temple sits right
    assert abs(traded["error_deg"]) < 0.3                                              # theta, seen in one frame


def test_triangulation_undoes_the_angled_views_theta():
    truth = _pyr(r_C=3.5)
    sj = RF.synthetic_setup(truth, theta={"02": {1: 2.5, -1: 0.0}}, views=("05", "02"))
    lines = _true_lines(sj, sj.truth, ("05", "02"))
    fits = _true_temple_fits(sj, ("05", "02"))
    nodes = {n: sj.truth.build.spec.nodes[n] for n in ("B", "C")}
    tr = CU.triangulate(sj, lines["05"], lines["02"], fits["05"], fits["02"], nodes, sj.hinge["z_mm"])
    assert tr["offset_CB_mm"] == pytest.approx(CU.model_offset_cb(sj.truth.build), abs=0.15)
    wrong = dict(fits["02"], theta={1: 0.0, -1: 0.0})
    tw = CU.triangulate(sj, lines["05"], lines["02"], fits["05"], wrong, nodes, sj.hinge["z_mm"])
    assert abs(tw["X"]["B"] - tr["X"]["B"]) > 0.5      # the theta matters, and it is undone


def test_model_steps_follow_the_views_theta():
    truth = _pyr(r_C=3.5)
    sj = RF.synthetic_setup(truth, theta={"02": {1: 2.5, -1: 0.0}}, views=("02",))
    lines = _true_lines(sj, sj.truth, ("02",))["02"]
    ph = CU.photo_steps(sj, "02", lines)
    right = CU.model_steps(sj, sj.truth, "02", sj.true_cams["02"], 1, lines, theta=2.5)
    mis = CU.step_misfit({"02": ph}, {"02": right})
    assert mis["chi2"] < 0.5


# --------------------------------------------------------------------------------------------- hinge
def test_hinge_constraint_on_fabricated_parts():
    spec = _pyr(r_C=3.0)
    tb = build_temple(spec)

    class J:
        hinge = {"z_mm": FX.HINGE["z_mm"], "axis_xz_mm": FX.HINGE["axis_xz_mm"]}
        glb = None
    job = J()
    x_in = lambda d: float(tb.spec.inner(d))
    # a leaf 0.1 mm inboard of the inner face (d 1..4, y -3..3), a frame knuckle 0.4 mm inside the temple body
    leaf = np.array([[x_in(d) - off, y, FX.HINGE["z_mm"] - d] for off in (0.6, 0.1) for y in (-3.0, 3.0)
                     for d in (1.0, 4.0)])
    knuckle = np.array([[x_in(d) + 0.4, y, FX.HINGE["z_mm"] - d] for y in (-1.0, 1.0) for d in (0.5, 1.5)])
    parts = {s: {"leaf": [leaf * np.array([s, 1, 1])], "frame_knuckle": [knuckle * np.array([s, 1, 1])],
                 "temple_knuckle": [], "names": {"leaf": ["leaf"], "frame_knuckle": ["k1"], "temple_knuckle": []}}
             for s in (1, -1)}
    RH.attach_parts(job, parts)
    rep = RH.hinge_report(job, spec, tb)
    for s in (1, -1):
        assert rep["sides"][s]["leaves"][0]["gap_mm"] == pytest.approx(0.1, abs=1e-4)
        assert rep["sides"][s]["frame_knuckles"][0]["penetration_mm"] == pytest.approx(0.4, abs=1e-3)
    assert {w["code"] for w in rep["warnings"]} == {"W_KNUCKLE_PENETRATION"}
    assert RH.hinge_violation_mm(rep) == pytest.approx(2 * (0.4 - 0.1), abs=2e-3)
    RH.attach_parts(job, None)
    assert RH.hinge_report(job, spec, tb) is None and RH.hinge_violation_mm(None) == 0.0


# --------------------------------------------------------------------------------------------- the objective
def test_objective_carries_the_cue_and_rejects_the_slab(synth):
    sj, truth, slab = synth
    lines = _true_lines(sj, truth)
    fits = _true_temple_fits(sj)
    fits.update({k: {"camera": sj.true_cams[k], "theta": sj.truth_theta[k]} for k in ("03", "04")})
    fam = RF.prada_family(RF.prada_base_params(r_C=3.5), topology="pyramid")
    x = fam.x0()
    photo = {k: CU.photo_steps(sj, k, lines[k]) for k in lines}
    ctx = RF.make_context(sj, fits, {k: np.ones(sj.shape(k), bool) for k in fits}, tuple(fits), lines, fam, x,
                          photo, True)
    assert ctx.tri is not None
    _, det = RF.objective(ctx, fam.spec(x), details=True)
    cue = det["cue"]
    assert cue["step_chi2"] < 0.5 and cue["tri_term"] < RF.NOISE_LOSS
    xs = x.copy()
    xs[fam.names.index("r_C")] = 0.0
    _, det0 = RF.objective(ctx, fam.spec(xs), details=True)
    assert det0["cue"]["step_term"] > 10 * cue["step_term"]
    assert det0["cue"]["tri_term"] > cue["tri_term"]
