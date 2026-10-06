"""WP6 check_relief photo half on synthetic creases (§5 "Photos", §8 P1): a faceted truth photographed through Prada-like
cameras; the faceted model gets OK on every segment and a slab gets FLAG / SHALLOW; 05 / 01 confirmation; the
05 -> 02 crease transfer separates the two; INVENTED_CREASE; the rules file agrees with the shared measure core; the
GLB check on the delivered Prada model."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import measure as MS
from glasses3d.g3d_host import relief_check as RC
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_trace as RT
from glasses3d.g3d_host import relief_views as RV

G3D = Path(__file__).resolve().parents[1]
PRADA_GLB = G3D / "data" / "reference" / "prada" / "model.glb"


def test_rules_file_matches_the_shared_measure_core():
    for name in ("SHARE_D_RANGE", "SHARE_TOL_MM", "FACET_NORMAL_DEG", "FACET_MIN_AREA_MM2", "STATION_STEP_MM",
                 "SLAB_SHARE", "SLAB_MIN_PHOTO_CREASES", "INNER_FLAT_MM", "BULGE_SANITY_MM"):
        assert getattr(RR, name) == getattr(MS, name), name
    assert RR.DN_OK_DEG == 6.0 and RR.R1_SHARE_MAX == 0.5 and RR.R3_TRANSFER_MAX_MM == 0.7


@pytest.fixture(scope="module")
def synth():
    truth = FX.prada_default()
    sj = RF.synthetic_setup(truth)
    slab = RV.Model.from_spec(sj, RF.slab_spec(**RF.prada_base_params()), "slab")
    return sj, sj.truth, slab


def _segments(sj, model, key):
    cam, th = sj.true_cams[key], sj.truth_theta[key]
    near = 1 if key == "05" else -1
    r = RV.render_model(sj, model, key, cam, th, points=True, normals=True)
    return RC.photo_crease_segments(sj, key, r, cam, th, near)


def test_faceted_model_explains_every_photo_crease(synth):
    sj, truth, slab = synth
    for key in ("05", "01"):
        seg = _segments(sj, truth, key)
        assert seg["usable"] and len(seg["segments"]) >= 3
        assert all(s["verdict"] == "OK" for s in seg["segments"]), [s["verdict"] for s in seg["segments"]]
        assert all(s["dn_deg"] >= RR.DN_OK_DEG for s in seg["segments"])


def test_slab_gets_flag_or_shallow_on_every_segment(synth):
    sj, truth, slab = synth
    for key in ("05", "01"):
        seg = _segments(sj, slab, key)
        assert seg["segments"] and all(s["verdict"] in ("FLAG", "SHALLOW") for s in seg["segments"])
        assert any(s["verdict"] == "FLAG" and s["len_mm"] >= RR.R2_FLAG_MIN_MM for s in seg["segments"])


def test_confirmation_and_r2(synth):
    sj, truth, slab = synth
    out = {}
    for lab, m in (("truth", truth), ("slab", slab)):
        s05, s01 = _segments(sj, m, "05"), _segments(sj, m, "01")
        conf = RC.confirm_creases(s05["segments"], s01["segments"])
        out[lab] = (RC.r2_verdict({"05": s05, "01": s01}, conf, s05["segments"]), conf)
    r2t, conft = out["truth"]
    assert len(conft) >= 2 and all(c["median_mm"] <= RR.CONFIRM_MEDIAN_MM for c in conft)
    assert r2t["pass"]
    r2s, _ = out["slab"]
    assert not r2s["pass"]
    assert all(v["weighted_median_dn_deg"] < RR.R2_MEDIAN_DN_DEG for v in r2s["views"].values())


def _crease_line(sj, tb, key, nodes):
    fit = {"camera": sj.true_cams[key], "theta": sj.truth_theta[key]}
    side = 1 if key in ("05", "02") else -1
    q = RF.model_crease_px(sj, tb, fit, side, *nodes)
    c = q.mean(0)
    d = np.linalg.svd(q - c)[2][0]
    t = (q - c) @ d
    return [(c + d * t.min()).tolist(), (c + d * t.max()).tolist()]


def test_crease_transfer_separates_the_faceted_model_from_the_slab(synth):
    sj, truth, slab = synth
    res = {}
    for cname, nodes in (("AB", ("A", "B")), ("BC", ("B", "C"))):
        l05 = _crease_line(sj, truth.build, "05", nodes)
        l02 = _crease_line(sj, truth.build, "02", nodes)
        # guides 2 px off the truth, snapped back by the tracer
        L05 = RC.lightness(sj.photo("05"))
        g = np.array(l05) + np.array([[1.5, -2.0], [-1.0, 2.0]])
        snapped = RT.refine_line(L05, g[0], g[1])
        assert snapped is not None and snapped["rms_px"] < 1.5
        a, b = np.array(l05)
        nv = np.array([-(b - a)[1], (b - a)[0]]) / np.linalg.norm(b - a)
        mid = (np.array(snapped["p0"]) + np.array(snapped["p1"])) / 2
        assert abs((mid - a) @ nv) < 1.0                       # the snapped line sits on the truth crease
        for lab, m in (("truth", truth), ("slab", slab)):
            t = RC.crease_transfer(sj, m, sj.truth_theta["05"], sj.true_cams["05"], sj.true_cams["02"],
                                   sj.truth_theta["02"], l05, l02)
            res[(cname, lab)] = t["mean_abs_mm"]
    for cname in ("AB", "BC"):
        assert res[(cname, "truth")] < 0.15
    mean_truth = np.mean([res[(c, "truth")] for c in ("AB", "BC")])
    mean_slab = np.mean([res[(c, "slab")] for c in ("AB", "BC")])
    assert mean_slab >= mean_truth + RR.R3_TRANSFER_MARGIN_MM


def test_invented_crease_is_advisory(synth):
    sj, truth, slab = synth
    flat = RF.synthetic_setup(RF.slab_spec(**RF.prada_base_params()))
    model = RV.Model.from_spec(flat, FX.prada_default(), "faceted")
    cam, th = flat.true_cams["05"], flat.truth_theta["05"]
    r = RV.render_model(flat, model, "05", cam, th, points=True, normals=True)
    inv = RC.invented_creases(flat, "05", r, 1, cam)
    assert inv["invented"] and inv["unsupported_share"] > 0.5
    r = RV.render_model(sj, truth, "05", sj.true_cams["05"], sj.truth_theta["05"], points=True, normals=True)
    inv = RC.invented_creases(sj, "05", r, 1, sj.true_cams["05"])
    assert inv["unsupported_share"] < 0.5


def test_shading_residual_is_reported(synth):
    sj, truth, slab = synth
    r = RV.render_model(sj, truth, "05", sj.true_cams["05"], sj.truth_theta["05"], points=True, normals=True)
    sh = RC.shading_residual(sj, "05", r, 1, sj.true_cams["05"])
    assert sh["note"] == "report only, never gated"


@pytest.mark.skipif(not PRADA_GLB.exists(), reason="Prada reference data not imported")
def test_glb_check_on_the_delivered_model():
    rep = RC.check_relief(PRADA_GLB, hinge_z=-10.6, photo_creases=4)
    assert set(rep["temples"]) == {"+x", "-x"}
    for side, m in rep["temples"].items():
        assert "faceted sweep" in m["node"]                    # found by role + reach, not by name
        assert m["vbulge_mm"] == pytest.approx(0.62, abs=0.03)
        assert m["single_plane_share"] == pytest.approx(0.81, abs=0.03)
        assert not any(f["flag"] == "SLAB" for f in m["flags"]) or m["single_plane_share"] >= RR.SLAB_SHARE
