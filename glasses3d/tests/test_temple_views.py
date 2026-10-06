"""WP6 fitted comparison views (§6) on synthetic photos: theta_k recovery, the bounded camera refit, the rear fit
recovering a synthetic opening alpha and perspective, ROI metrics, envelopes, the front bound, the side outline."""
from __future__ import annotations

import copy

import numpy as np
import pytest

from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_views as RV
from glasses3d.g3d_host.camera import Camera


@pytest.fixture(scope="module")
def synth():
    theta = {"03": {1: 3.0, -1: -1.0}, "06": {1: 6.0, -1: 6.0}, "02": {1: 2.0, -1: 2.0}}
    return RF.synthetic_setup(FX.prada_default(), theta, perturb_px={"02": 4.0, "06": 6.0})


def test_theta_is_recovered_per_side(synth):
    th = RV.fit_theta(synth, synth.truth, "03", synth.true_cams["03"])
    assert th[1]["theta"] == pytest.approx(3.0, abs=0.3) and th[-1]["theta"] == pytest.approx(-1.0, abs=0.3)
    assert th[1]["observed"] and th[-1]["observed"]


def test_bounded_refit_recovers_a_perturbed_camera(synth):
    cam0 = synth.base["02"]
    cam, info = RV.refit_camera(synth, synth.truth, "02", cam0, synth.truth_theta["02"])
    true = synth.true_cams["02"]
    err0 = np.hypot(cam0.center_x - true.center_x, cam0.center_y - true.center_y)
    assert info["kept"] and info["loss"] <= info["loss_start"]
    assert abs(cam.yaw - true.yaw) <= RR.REFIT_BOUNDS["yaw"] and abs(cam.pitch - true.pitch) <= RR.REFIT_BOUNDS["pitch"]
    r = RV.render_model(synth, synth.truth, "02", cam, synth.truth_theta["02"], points=False)
    iou = RV.whole_iou(synth.matte("02"), r["mask"])
    assert iou > 0.97
    assert err0 > 3.0


def test_rear_family_recovers_alpha_and_perspective(synth):
    seeds = [synth.base["06"]]
    fam = RV.rear_family(synth, synth.truth, seeds, alphas=(0.0, 2.0, 4.0, 6.0, 8.0, 10.0))
    best = min(fam["rows"], key=lambda r: r["loss"])
    assert best["alpha"] == pytest.approx(6.0, abs=2.0)
    assert 6.0 in fam["family"]
    cam = Camera.from_dict(best["camera"])
    assert cam.perspective == pytest.approx(synth.true_cams["06"].perspective, abs=0.05)
    assert not fam["stop_r"]
    assert all(RR.REAR_PITCH_DEG[0] <= Camera.from_dict(r["camera"]).pitch <= RR.REAR_PITCH_DEG[1] for r in fam["rows"])


def test_roi_metrics_and_envelopes_are_zero_for_the_truth(synth):
    key = "03"
    cam, th = synth.true_cams[key], synth.truth_theta[key]
    r = RV.render_model(synth, synth.truth, key, cam, th, points=True)
    roi = RV.temple_roi(synth, key, [r])
    m = RV.roi_metrics(synth.matte(key), r["mask"], roi, synth.hinge_ppm(cam))
    assert m["iou"] == pytest.approx(1.0) and m["contour_mm"] == pytest.approx(0.0, abs=1e-9)
    env = RV.envelopes(synth, key, r, cam)
    for s in (1, -1):
        for zone in ("hinge", "arm"):
            if env[s][zone]:
                assert env[s][zone]["outer_abs_median_mm"] == pytest.approx(0.0, abs=1e-9)
    # a laterally shifted model: the hinge-zone outer offset reads the shift
    spec = FX.prada_default()
    spec2 = copy.deepcopy(spec)
    spec2["inner"] = [[d, x - 1.5] for d, x in spec["inner"]]
    m2 = RV.Model.from_spec(synth, spec2, "inboard 1.5")
    r2 = RV.render_model(synth, m2, key, cam, th, points=True)
    env2 = RV.envelopes(synth, key, r2, cam)
    off = [env2[s]["hinge"]["outer_signed_median_mm"] for s in (1, -1) if env2[s]["hinge"]]
    assert off and all(v == pytest.approx(1.5, abs=0.4) for v in off)


def test_front_bound_flags_an_outboard_hinge_zone(synth):
    cam = synth.true_cams["04"]
    r = RV.render_model(synth, synth.truth, "04", cam, synth.truth_theta["04"], points=True)
    assert RV.front_bound(synth, r, cam)["clear"]
    spec = copy.deepcopy(FX.prada_default())
    spec["inner"] = [[d, x + 2.0] for d, x in spec["inner"]]
    m = RV.Model.from_spec(synth, spec, "outboard 2")
    r = RV.render_model(synth, m, "04", cam, synth.truth_theta["04"], points=True)
    fb = RV.front_bound(synth, r, cam)
    assert not fb["clear"] and fb["hinge_zone_max_mm"] > 1.0


def test_side_outline_reads_a_lowered_top_edge(synth):
    cam = synth.true_cams["05"]
    spec = copy.deepcopy(FX.prada_default())
    spec2 = FX.prada_spec(**RF.prada_base_params(top=[[d, y - 0.8] for d, y in FX.TOP]))
    m = RV.Model.from_spec(synth, spec2, "top -0.8")
    r = RV.render_model(synth, m, "05", cam, synth.truth_theta["05"], points=True)
    so = RV.side_outline(synth, "05", r, cam, near=1)
    A = np.array(so["top"]["samples"])
    mid = (A[:, 0] > 10) & (A[:, 0] < 110)
    assert np.median(A[mid, 1]) == pytest.approx(0.8, abs=0.25)          # + = the photo is beyond the model
    r0 = RV.render_model(synth, synth.truth, "05", cam, synth.truth_theta["05"], points=True)
    assert RV.side_outline(synth, "05", r0, cam, near=1)["top"]["mean_abs_mm"] < 0.05
