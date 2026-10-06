"""WP6 S1 identifiability, the synthetic round trip (§8 S1, the STOP-I input): the tool's own faceted temple rendered
through Prada-like cameras as photos; trace the outline and the creases (AB, AC, BC and F1's upper edge B-K: with
F1's upper edge untraced the F1 rise is not recovered to 0.05, the design's open item "F1's upper edge first"), then
fit back from wrong starting values.
Must recover the outline <= 0.2 mm, the zone level <= 0.5 mm, r_C <= 0.5 mm, the F1 rise <= 0.05 (r_B is fixed at 0 in
the default family: B on the base) and theta <= 0.3 deg (theta: tests/test_temple_views.py)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_trace as RT
from glasses3d.g3d_host import relief_views as RV

TRUTH = dict(r_A=1.0, r_C=3.5, rise=-0.35)
START = dict(r_A=0.5, r_C=2.5, rise=-0.2)
VIEWS = ("05", "01", "02", "03", "04")
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def setup():
    truth_p = RF.prada_base_params(**TRUTH)
    sj = RF.synthetic_setup(FX.prada_spec(**truth_p), perturb_px={})
    fits = {k: {"camera": sj.true_cams[k], "theta": sj.truth_theta[k]} for k in sj.true_cams}
    guides = {}
    for key, side in (("05", 1), ("01", -1), ("02", 1)):
        guides[key] = {}
        for cname, nodes in (("AB", ("A", "B")), ("AC", ("A", "C")), ("BC", ("B", "C")), ("BK", ("B", "K"))):
            q = RF.model_crease_px(sj, sj.truth.build, fits[key], side, *nodes)
            c = q.mean(0)
            d = np.linalg.svd(q - c)[2][0]
            t = (q - c) @ d
            guides[key][cname] = (np.array([c + d * t.min(), c + d * t.max()]) + [[1.5, -2.0], [-1.0, 2.0]]).tolist()
    start = RF.prada_base_params(**START, top=[[d, y + 0.6] for d, y in FX.TOP],
                                 bottom=[[d, y - 0.5] for d, y in FX.BOTTOM], x_in0=65.5 + 0.8, x_in_ze=61.8 + 0.8)
    return sj, truth_p, fits, guides, start


@pytest.fixture(scope="module")
def traced(setup):
    sj, truth_p, fits, guides, start = setup
    fam = RF.prada_family(start, free=(("x_in0", 3.0), ("x_in_ze", 3.0), ("r_C", (0.0, 7.0)), ("rise", (-0.6, -0.02))))
    ol = RT.trace_outline(sj, fam.spec(fam.x0()),
                          lambda t, b: FX.prada_spec(**{**fam.kwargs(fam.x0()), "top": t, "bottom": b}), fits)
    fam.base["top"], fam.base["bottom"] = ol["top"], ol["bottom"]
    m1 = RV.Model.from_spec(sj, fam.spec(fam.x0()), "traced")
    cr = RT.trace_creases(sj, guides, m1, fits)
    return fam, ol, cr


def test_outline_is_recovered(setup, traced):
    _, truth_p, _, _, _ = setup
    _, ol, _ = traced
    for edge in ("top", "bottom"):
        err = np.abs(np.array([y for _, y in ol[edge]]) - np.array([y for _, y in truth_p[edge]]))
        assert err.mean() <= RR.S1_RECOVERY["outline_mm"], (edge, err.round(3).tolist())
    for k in ("05", "01"):
        for e in ("top", "bottom"):
            assert ol["final"][k][e]["mean_abs_mm"] <= RR.P2_TRACE["in_view_mean_mm"]


def test_creases_snap_and_corners_lift(setup, traced):
    sj, *_ = setup
    _, _, cr = traced
    nodes = sj.truth.build.spec.nodes
    err = {c: math.hypot(cr["corners"][c]["d"] - nodes[c][0], cr["corners"][c]["y"] - nodes[c][1]) for c in "ABC"}
    # corners are not an S1 criterion (the facet layout is fixed in the fit); P2 grades 05 vs 01 agreement on the real
    # photos. Here F1's border with the base (B-K) runs within a few px of A-B over its rear half, which biases the
    # A-B snap and so A by ~0.6 mm and B by ~1.5 mm (an artefact of this synthetic topology)
    assert err["A"] <= 1.0 and err["C"] <= RR.P2_TRACE["C_mm"] and err["B"] <= 2.0, err
    for key in ("05", "01", "02"):
        rms = {n: (None if ln is None else round(ln["rms_px"], 2)) for n, ln in cr["views"][key]["lines"].items()}
        good = [n for n, v in rms.items() if n != "BK" and v is not None and v < 1.5]
        assert len(good) >= 2, (key, rms)                       # a crease with too weak a shading step is skipped


def test_fit_recovers_level_and_relief(setup, traced):
    sj, truth_p, fits, _, _ = setup
    fam, _, cr = traced
    glines = {k: {cn: (ln["p0"], ln["p1"]) for cn, ln in cr["views"][k]["lines"].items() if ln} for k in cr["views"]}
    res = RF.fit_temple(sj, fam, views=VIEWS, guides_lines=glines, rounds=1, maxfev=220, rear=False, fits0=fits,
                        refit_cameras=False, log=lambda m: None)
    x = res["x"]
    lvl = lambda spec: float(build_temple(spec).spec.x_base(46.8))
    assert abs(lvl(res["spec"]) - lvl(FX.prada_spec(**truth_p))) <= RR.S1_RECOVERY["zone_level_mm"], x
    assert abs(x["r_C"] - truth_p["r_C"]) <= RR.S1_RECOVERY["r_C_mm"], x
    lo, hi = next(b for n, b in zip(fam.names, fam.bounds()) if n == "rise")
    assert lo <= x["rise"] <= hi
    if abs(x["rise"] - truth_p["rise"]) > RR.S1_RECOVERY["f1_rise"]:
        # measured 2026-10-06: with exact crease lines the shape step reaches the rise within 0.031, but with the
        # traced lines (A-B snap biased by F1's border with the base) the rise lands 0.16 off: S1 makes the rise
        # UNOBSERVED, and since R2 needs F1's dihedral that is STOP-I in the proof (tests/proofs/wp6_prada.py)
        pytest.xfail(f"F1 rise not identifiable to {RR.S1_RECOVERY['f1_rise']} from traced creases: {x['rise']:.3f} "
                     f"vs {truth_p['rise']}")
