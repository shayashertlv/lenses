"""WP6 slab control (§0.5, §7, §8 R1-R4): the fitted slab is the same family with every relief 0 and no facets; relief is
nearly invisible in top / rear silhouettes (so those gates test outline and plan, not relief, and a slab with the
right outline already beats a wrong one), while the crease observables (R2 dn, R3 transfer) and R1 separate a
faceted temple from the slab."""
from __future__ import annotations

import copy

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple, report
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import relief_check as RC
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_views as RV


def test_slab_family_shares_everything_but_the_relief():
    base = RF.prada_base_params()
    fam = RF.prada_family(base)
    slab = fam.slab()
    assert slab.slab_mode and set(slab.names) == set(RF.SLAB_FREE)
    x = slab.x0()
    s = slab.spec(x)
    f = fam.spec(fam.x0())
    assert s["facets"] == {} and s["nodes"] == {}
    assert s["outline"] == f["outline"] and s["inner"] == f["inner"] and s["thickness"] == f["thickness"]
    g = report.geometry_numbers(build_temple(s), photo_creases=4)
    assert g["single_plane_share"] == pytest.approx(1.0) and any(fl["flag"] == "SLAB" for fl in g["flags"])
    gf = report.geometry_numbers(build_temple(f), photo_creases=4)
    assert gf["single_plane_share"] <= RR.SLAB_SHARE


def test_family_offsets_move_only_the_arm():
    fam = RF.prada_family(RF.prada_base_params())
    x = fam.x0()
    i = fam.names.index("arm_dx")
    x2 = x.copy()
    x2[i] += 2.0
    a, b = fam.spec(x), fam.spec(x2)
    ia, ib = np.array(a["inner"]), np.array(b["inner"])
    ze = a["zone_end_d"]
    assert np.allclose(ia[ia[:, 0] <= ze], ib[ib[:, 0] <= ze])
    far = ia[:, 0] >= ze + 10
    assert np.allclose(ib[far, 1] - ia[far, 1], 2.0)


@pytest.fixture(scope="module")
def synth():
    return RF.synthetic_setup(FX.prada_default())


def _roi_contour(sj, model, key, roi):
    cam, th = sj.true_cams[key], sj.truth_theta[key]
    r = RV.render_model(sj, model, key, cam, th, points=False)
    return RV.roi_metrics(sj.matte(key), r["mask"], roi, sj.hinge_ppm(cam))


def test_relief_is_nearly_invisible_in_top_and_rear_silhouettes(synth):
    sj = synth
    slab = RV.Model.from_spec(sj, RF.slab_spec(**RF.prada_base_params()), "slab")
    bad = copy.deepcopy(RF.prada_base_params())
    bad["x_in0"] += 2.5
    bad["x_in_ze"] += 2.5                                        # a slab whose hinge zone sits 2.5 mm out
    worse = RV.Model.from_spec(sj, RF.slab_spec(**bad), "slab +2.5")
    for key in ("03", "06"):
        rs = [RV.render_model(sj, m, key, sj.true_cams[key], sj.truth_theta[key], points=False)
              for m in (sj.truth, slab, worse)]
        roi = RV.temple_roi(sj, key, rs)
        t, s, w = (_roi_contour(sj, m, key, roi) for m in (sj.truth, slab, worse))
        assert t["contour_mm"] == pytest.approx(0.0, abs=1e-9)
        assert s["contour_mm"] < w["contour_mm"]                # 3 mm of relief costs less than 2.5 mm of level
        assert s["iou"] > w["iou"] and s["iou"] > 0.9


def test_crease_observables_separate_the_faceted_temple_from_the_slab(synth):
    sj = synth
    slab = RV.Model.from_spec(sj, RF.slab_spec(**RF.prada_base_params()), "slab")
    dn = {}
    for lab, m in (("truth", sj.truth), ("slab", slab)):
        r = RV.render_model(sj, m, "05", sj.true_cams["05"], sj.truth_theta["05"], points=True, normals=True)
        seg = RC.photo_crease_segments(sj, "05", r, sj.true_cams["05"], sj.truth_theta["05"], 1)
        dn[lab] = np.median([s["dn_deg"] for s in seg["segments"]])
    assert dn["truth"] >= RR.R2_MEDIAN_DN_DEG > dn["slab"]
    fits = {k: {"camera": sj.true_cams[k], "theta": sj.truth_theta[k]} for k in sj.true_cams}
    lines = {}
    for key, side in (("05", 1), ("02", 1)):
        lines[key] = {}
        for cname, nodes in (("AB", ("A", "B")), ("BC", ("B", "C"))):
            q = RF.model_crease_px(sj, sj.truth.build, fits[key], side, *nodes)
            c = q.mean(0)
            d = np.linalg.svd(q - c)[2][0]
            t = (q - c) @ d
            lines[key][cname] = ((c + d * t.min()).tolist(), (c + d * t.max()).tolist())
    ctx = RF.Context(sj, fits, {}, (), lines)
    tr_t = RF.transfer_all(ctx, sj.truth)["mean_mm"]
    tr_s = RF.transfer_all(ctx, slab)["mean_mm"]
    assert tr_t <= RR.R3_TRANSFER_MAX_MM and tr_t <= tr_s - RR.R3_TRANSFER_MARGIN_MM
    gt = report.geometry_numbers(sj.truth.build, photo_creases=4)
    gs = report.geometry_numbers(slab.build, photo_creases=4)
    assert gt["single_plane_share"] < RR.SLAB_SHARE <= gs["single_plane_share"]          # R1's direction
    assert any(f["flag"] == "SLAB" for f in gs["flags"]) and not any(f["flag"] == "SLAB" for f in gt["flags"])
