"""WP6 parallel joint fit (relief_fit.FitPool): the pooled fit must give the SAME result as the serial fit, bit for bit,
and the exact speed-ups behind it must equal the formulas they replace.

    roi_metrics      cropped + k-d tree == roi_metrics_full (full-image distance transforms), random and edge cases
    render_mask      == render_model(points=False)["mask"];  render_tags == render_model(points=True) mask/tag/d
    objective        pooled == serial (values and details) for feasible, refused and builder-refused specs
    rear_family      pooled alpha x seed refits == serial
    fit_temple       a reduced synthetic fit (two rounds of camera block + Nelder-Mead / Powell, sigma) pooled == serial

Equality is ==, never a tolerance: the pool only changes where a deterministic computation runs and keeps every
reduction in the serial order."""
from __future__ import annotations

import copy
import math

import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_views as RV
from glasses3d.g3d_host.camera import Camera

pytestmark = pytest.mark.slow
WORKERS = 3


# --------------------------------------------------------------------------------------------- roi_metrics
def _blobs(rng, shape, n, rmax):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    m = np.zeros(shape, bool)
    for _ in range(n):
        cy, cx = rng.uniform(0, shape[0]), rng.uniform(0, shape[1])
        ry, rx = rng.uniform(3, rmax), rng.uniform(3, rmax)
        m |= ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0
    return m


def test_roi_metrics_equals_the_full_image_formula():
    rng = np.random.default_rng(11)
    cases = 0
    for trial in range(60):
        shape = (int(rng.integers(40, 260)), int(rng.integers(40, 300)))
        fg = _blobs(rng, shape, int(rng.integers(1, 6)), 60)
        mask = _blobs(rng, shape, int(rng.integers(0, 6)), 60)
        if trial % 3 == 0:                                     # model = shifted photo (small contour distances)
            mask = np.roll(fg, (int(rng.integers(-4, 5)), int(rng.integers(-4, 5))), axis=(0, 1))
        roi = _blobs(rng, shape, int(rng.integers(1, 4)), 90)
        if trial % 7 == 0:                                     # ROI touching the image border
            roi[:, :3] = True
        if trial % 11 == 0:                                    # far apart: distances beyond the truncation
            fg[:], mask[:] = False, False
            fg[:10, :10] = True
            mask[-10:, -10:] = True
            roi[:] = True
        a = RV.roi_metrics(fg, mask, roi, 5.3)
        b = RV.roi_metrics_full(fg, mask, roi, 5.3)
        assert a == b, (trial, a, b)
        cases += math.isfinite(b["contour_mm"])
    assert cases >= 30                                         # most cases exercise the distance path
    empty = np.zeros((50, 60), bool)
    assert RV.roi_metrics(empty, empty, empty, 4.0) == RV.roi_metrics_full(empty, empty, empty, 4.0)


def test_nearest_edge_distance_equals_the_distance_transform_with_ties():
    rng = np.random.default_rng(3)
    for _ in range(20):
        dst = rng.random((70, 90)) < 0.01
        dst[35, 45] = True
        src = rng.random((70, 90)) < 0.2
        ref = ndimage.distance_transform_edt(~dst)[src]
        assert np.array_equal(RV._nearest_edge_px(src, dst), ref)


# --------------------------------------------------------------------------------------------- renders
@pytest.fixture(scope="module")
def synth():
    theta = {"03": {1: 3.0, -1: -1.0}, "02": {1: 2.0, -1: 5.0}, "04": {1: 1.0, -1: 0.5}}
    return RF.synthetic_setup(FX.prada_default(), theta, perturb_px={"02": 4.0, "06": 6.0, "05": 3.0})


def _models(sj):
    p = RF.prada_base_params()
    specs = [FX.prada_spec(**p), FX.prada_spec(**dict(p, r_C=4.2, x_in0=66.4)),
             RF.slab_spec(**dict(p, x_in0=64.9, t_zone=4.6))]
    return [RV.Model.from_spec(sj, s, f"m{i}") for i, s in enumerate(specs)]


def test_render_mask_and_tags_equal_render_model(synth):
    for m in _models(synth):
        for k in RF.SYNTH_VIEWS:
            for cam in (synth.true_cams[k], synth.base[k]):
                for th in ({1: 0.0, -1: 0.0}, {1: 3.5, -1: -2.0}):
                    ref = RV.render_model(synth, m, k, cam, th, points=False)
                    assert np.array_equal(RV.render_mask(synth, m, k, cam, th), ref["mask"]), (k, th)
                    ref = RV.render_model(synth, m, k, cam, th, points=True)
                    got = RV.render_tags(synth, m, k, cam, th)
                    assert np.array_equal(got["mask"], ref["mask"])
                    assert np.array_equal(got["tag"], ref["tag"])
                    assert np.array_equal(got["d"], ref["d"], equal_nan=True)
    RV._RAYS.clear()                                           # a cold ray cache gives the same masks
    m = _models(synth)[1]
    for k in RF.SYNTH_VIEWS:
        ref = RV.render_model(synth, m, k, synth.base[k], {1: 1.0, -1: 1.0}, points=False)["mask"]
        assert np.array_equal(RV.render_mask(synth, m, k, synth.base[k], {1: 1.0, -1: 1.0}), ref)


# --------------------------------------------------------------------------------------------- pooled == serial
def _guides(sj):
    fits = {k: {"camera": sj.true_cams[k], "theta": sj.truth_theta[k]} for k in sj.true_cams}
    g = {}
    for key, side in (("05", 1), ("01", -1), ("02", 1)):
        g[key] = {}
        for cname, nodes in (("AB", ("A", "B")), ("AC", ("A", "C")), ("BC", ("B", "C"))):
            q = RF.model_crease_px(sj, sj.truth.build, fits[key], side, *nodes)
            c = q.mean(0)
            d = np.linalg.svd(q - c)[2][0]
            t = (q - c) @ d
            g[key][cname] = tuple(map(tuple, np.array([c + d * t.min(), c + d * t.max()]) + [[1.5, -2.0], [-1.0, 2.0]]))
    return fits, g


@pytest.fixture(scope="module")
def pool():
    with RF.FitPool(WORKERS, log=None) as p:
        yield p


@pytest.fixture(scope="module")
def ctx(synth):
    fits, glines = _guides(synth)
    views = ("01", "02", "03", "04", "05", "06")
    base = RV.Model.from_spec(synth, FX.prada_spec(**RF.prada_base_params()), "base")
    rois = RF.build_rois(synth, [(base, fits)], views)
    return RF.Context(synth, {k: fits[k] for k in views}, rois, views, glines)


def test_pooled_objective_equals_serial(synth, ctx, pool):
    fam = RF.prada_family(RF.prada_base_params())
    rng = np.random.default_rng(5)
    lo, hi = np.array(fam.bounds()).T
    xs = [fam.x0()] + [np.clip(fam.x0() + rng.uniform(-0.25, 0.25, len(lo)) * (hi - lo), lo, hi) for _ in range(9)]
    specs = [RF.safe_spec(fam, x) for x in xs] + [None]
    bad = copy.deepcopy(specs[0])
    bad["outline"]["top"] = bad["outline"]["top"][::-1]        # the core refuses it (TempleError)
    specs.append(bad)
    ser = RF.objective_many(ctx, specs, details=True)
    par = RF.objective_many(ctx, specs, details=True, pool=pool)
    assert [s[0] for s in ser] == [p[0] for p in par]
    assert repr(ser) == repr(par)                              # details too (views, transfer, front bound, dn)
    assert sum(s[0] < RF.INFEASIBLE for s in ser) >= 6 and ser[-1][0] == ser[-2][0] == RF.INFEASIBLE
    assert ser[-1][1].get("refused") not in (None, "builder")
    one = [RF.objective(ctx, s, pool=pool) for s in specs[:3]]
    assert one == [s[0] for s in ser[:3]]


def test_pooled_rear_family_equals_serial(synth, pool, monkeypatch):
    """Two alphas x two seeds; the refit budget is cut for test time (the kwargs travel with each task, so the pooled
    refits use the same cut budget)."""
    monkeypatch.setattr(RV, "REAR_REFIT", {**RV.REAR_REFIT, "maxfev": {"fit": 40, "final": 20}})
    seeds = [synth.base["06"], synth.true_cams["06"]]
    m = _models(synth)[0]
    ser = RV.rear_family(synth, m, seeds, alphas=(0.0, 6.0))
    par = RV.rear_family(synth, m, seeds, alphas=(0.0, 6.0), refit_many=pool.refit_many(synth, m))
    strip = lambda fam: [{k: v for k, v in r.items() if k != "refit"} for r in fam["rows"]]
    assert strip(ser) == strip(par) and ser["family"] == par["family"]
    assert [r["refit"]["loss"] for r in ser["rows"]] == [r["refit"]["loss"] for r in par["rows"]]
    assert [r["refit"]["evals"] for r in ser["rows"]] == [r["refit"]["evals"] for r in par["rows"]]


def test_pooled_fit_equals_serial_fit(synth, pool):
    """The reduced case: two rounds, each with the camera block (front + side view: theta grid, bounded camera
    refit, theta again) and the shape step (Nelder-Mead from a prefetched start simplex, then Powell; maxfev 16) on four
    free parameters, then sigma."""
    fits, glines = _guides(synth)
    p = RF.prada_base_params(r_C=2.4, x_in0=66.1, x_in_ze=62.3)
    fam = RF.prada_family(p, free=(("x_in0", 2.0), ("x_in_ze", 2.0), ("r_C", (0.0, 7.0)), ("rise", (-0.6, -0.02))))
    views = ("04", "05")
    fits0 = {k: {"camera": synth.base[k], "theta": {1: 0.0, -1: 0.0}} for k in views}
    kw = dict(views=views, guides_lines={"05": glines["05"]}, rounds=2, maxfev=16, rear=False, fits0=fits0,
              refit_cameras=True, log=lambda m: None)
    t0 = pool.stats["tasks"]
    ser = RF.fit_temple(synth, copy.deepcopy(fam), **kw)
    par = RF.fit_temple(synth, copy.deepcopy(fam), pool=pool, **kw)
    assert ser["x"] == par["x"] and ser["loss"] == par["loss"]
    assert repr(ser["sigma"]) == repr(par["sigma"])                  # nan-safe exact comparison
    assert ser["history"] == par["history"] and ser["spec"] == par["spec"]
    for k in views:
        assert ser["fits"][k]["camera"] == par["fits"][k]["camera"] and ser["fits"][k]["theta"] == par["fits"][k]["theta"]
    assert len(ser["history"]) == 2 and all(h["evaluations"] >= 16 for h in ser["history"])
    assert ser["details"]["crease_reproj_mm"] > 0 and ser["details"]["front_bound"] is not None
    assert pool.stats["tasks"] - t0 >= 2 * 2 + 2 * 16 * len(views)   # camera tasks and view tasks really ran pooled
    # FIT_VERSION fit-2: the returned cameras are the final shape's OWN §6 refit from the base cameras (a fresh
    # camera block after the last shape step), the front bound is reported there, and the termination is recorded
    assert ser["final_camera_block"] and ser["fit_version"] == RF.FIT_VERSION
    assert ser["termination"] in ("converged", "round cap") and ser["rounds_run"] == len(ser["history"])
    fresh = RF.camera_block(synth, RV.Model.from_spec(synth, ser["spec"], "fresh"), views, False)
    for k in views:
        assert fresh[k]["camera"] == ser["fits"][k]["camera"] and fresh[k]["theta"] == ser["fits"][k]["theta"]
    assert ser["front_bound_at_own_cameras"]["at"] == "the final shape's own refit cameras"
    assert all("front_bound_clear_start" in h for h in ser["history"])
