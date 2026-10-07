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
    assert slab.slab_mode and set(slab.names) == {n for n in fam.names if n in RF.SLAB_FREE}
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


@pytest.mark.xfail(strict=True, reason="open owner decision: R1 (single-plane share <= 0.5 fails the synthetic "
                                       "faceted truths; see the docstring)")
def test_synthetic_faceted_truths_pass_r1():
    """R1 (single-plane share <= R1_SHARE_MAX, frozen 0.5) must be passable by the faceted temples the proof treats
    as truth; a gate nothing can pass decides STOP-P before any photo is read.

    XFAIL (strict) until the owner decides R1 (WP6 fix round 1, 2026-10-06; marked xfail in fix round 2 so the suite's
    exit code means something again): it stays visible as an expected failure, and turns into a FAILURE (XPASS) the
    day R1 becomes passable, so the mark cannot outlive the decision. Measured with the shipped measure
    core: FX.prada_default 0.668 (best plane F2, the 617 mm^2 facet), the S1-like truth 0.708, the recorded candidate
    0.915 (SLAB); over the Prada fit band (r_A 0.02-3, r_C 0-7, F1 rise -0.6..-0.02, the rest at the candidate's
    values: 336 specs) the minimum is 0.596. The design's 0.5 came from prototype numbers (T pyramid 0.34, T fit
    0.42) measured with the LEAST-SQUARES PLANE ONLY (proto/plane_share.py): this core reproduces them on the
    prototype GLBs as its least-squares candidate (0.344, 0.428), but §5 also makes every facet plane a candidate, and
    the largest facet alone then covers 0.615 / 0.734 of the d 2-50 outer face. Even least-squares-only, the Prada
    family needs r_C >= ~6 mm (C 6 mm proud of the base) to reach 0.5 (band minimum 0.423 at r_C 7). Changing
    relief_rules.py or the share definition is a new rule set (§8, §9): an owner decision, not a fix."""
    rows = {"FX.prada_default": FX.prada_default(),
            "S1-like truth (base + r_A 0.9, r_C 4.0, rise -0.35)": FX.prada_spec(**RF.prada_base_params(
                r_A=0.9, r_C=4.0, rise=-0.35))}
    shares = {n: report.geometry_numbers(build_temple(s), photo_creases=4)["single_plane_share"] for n, s in rows.items()}
    assert all(v <= RR.R1_SHARE_MAX for v in shares.values()), (
        f"R1 (share <= {RR.R1_SHARE_MAX}) is failed by the synthetic faceted truths themselves: "
        + ", ".join(f"{n} {v:.3f}" for n, v in shares.items()) + " -- open owner decision (see the docstring)")
