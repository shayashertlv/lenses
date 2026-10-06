"""WP4 proof review fix round 1 (host side; the Blender half is tests/test_seat_blender.py, crystal_seat).

1  The groove reading needs a real clearance (stations.groove_seat): at every depth the frame begins 0.02-0.06 mm
   beyond the lens edge and the edge is not in frame material; only such a point is graded at the apex (rules
   'apex'), and the check measures the lens-frame overlap on translucent capturing parts (check.crystal_overlap,
   §8 'crystal overlap <= 0.01 mm3'). The review reproduced two passes with the fix round's own fixtures: a lens
   buried in a solid crystal ring with no groove (344/344 PASS, apex 0.80) and a groove 0.05 mm too small (344/344
   PASS). Both must FAIL.
2  plan_growth's point plan is computed once per replan family (`cache`): the result is bit-identical."""
import numpy as np
import pytest

from glasses3d.g3d.seat import check as CK, plan as PL, rules, stations
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.tests.fixtures import seat_fixtures as SF

VB = {"frame_type": "acetate", "profile": "vbevel", "pass_mm": 0.6, "target_mm": 0.8}


def factory(parts):
    return NumpyBackend(parts)


def _nodes(L, F, seat, translucent=True):
    return [{"node": 0, "name": "Lens_L", "V": L.V, "T": L.T, "N": np.full_like(L.V, np.nan), "roles": ["lens"],
             "lens": True, "translucent": False, "extras": {"g3d_seat": seat}},
            {"node": 1, "name": "Crystal rim", "V": F.V, "T": F.T, "N": np.full_like(F.V, np.nan), "roles": ["frame"],
             "lens": False, "translucent": translucent, "extras": {}}]


def _check(L, F, seat=VB, fac=factory, translucent=True):
    rep = CK.check_nodes(_nodes(L, F, seat, translucent), [], [], fac, stations_table=True)
    Ln = rep["lenses"][0]
    v = np.array([r["value"] for r in Ln["stations"] if r["required"]], float)
    return rep, Ln, v


def test_a_v_bevel_buried_in_an_ungrooved_crystal_ring_fails():
    """No groove (a deleted g3d_groove Boolean, a crystal rim never cut): the edge lies in frame material at every
    depth, so no point is groove-seated, the apex rule falls back to the deep rule (the corners, 0.396 < 0.60), and
    the lens overlaps the crystal by ~128 mm3. The review's reproduction read apex 0.80, 344/344 PASS."""
    base = SF.base_lens(cap="flat")
    rep, Ln, v = _check(SF.vbevel_lens(base), SF.solid_ring(base))
    assert not rep["ok"] and Ln["groove"]["seated"] == 0
    assert Ln["verdicts"] == {"FAIL": Ln["points"]} and v.max() < 0.45, (v.min(), v.max())
    assert Ln["crystal_overlap_mm3"] > 100.0, Ln["crystal"]
    assert any("crystal overlap" in x for x in Ln["reasons"]), Ln["reasons"]


def test_a_groove_too_small_for_the_lens_fails():
    """grooved_ring(clearance=-0.05): the lens cuts 0.05 mm into the groove walls and lips. §3 reads the edge 0.05 mm
    inside frame at every depth (not groove-seated: no clearance), deep 0.05 < 0.60, and the overlap is ~16 mm3."""
    base = SF.base_lens(cap="flat")
    rep, Ln, v = _check(SF.vbevel_lens(base), SF.grooved_ring(base, clearance=-0.05))
    assert not rep["ok"] and Ln["groove"]["seated"] == 0
    assert Ln["verdicts"] == {"FAIL": Ln["points"]} and np.allclose(v, 0.05, atol=2e-3), (v.min(), v.max())
    assert Ln["crystal_overlap_mm3"] > 1.0, Ln["crystal"]
    assert any("crystal overlap" in x for x in Ln["reasons"])


def test_a_clearance_under_0_02_is_not_groove_seated():
    """§5's gap band is 0.02-0.06 mm: a 0.01 mm clearance is not a groove seat, so §3's reading stands (a 0.01 mm
    gap at every depth) and the lens fails; it overlaps nothing."""
    base = SF.base_lens(cap="flat")
    rep, Ln, v = _check(SF.vbevel_lens(base), SF.grooved_ring(base, clearance=0.01))
    assert not rep["ok"] and Ln["groove"]["seated"] == 0 and np.allclose(v, -0.01, atol=1e-3)
    assert Ln["crystal_overlap_mm3"] <= rules.CRYSTAL_OVERLAP_MAX_MM3


def test_the_crystal_overlap_is_zero_in_the_s5_groove_and_graded():
    """The §5 groove (clearance 0.03) clears the lens: overlap 0, every point groove-seated, PASS; the overlap is
    reported per translucent part and graded (rules.check_verdict, §8)."""
    base = SF.base_lens(cap="flat")
    rep, Ln, v = _check(SF.vbevel_lens(base), SF.grooved_ring(base))
    assert rep["ok"], rep["reasons"]
    assert Ln["crystal_overlap_mm3"] <= 1e-6 and [p["part"] for p in Ln["crystal"]["parts"]] == ["Crystal rim"]
    assert Ln["groove"]["seated"] == Ln["points"] and Ln["stale_cutter"] is None
    assert np.allclose(v, 0.80, atol=0.002)


def test_an_opaque_ring_is_not_graded_for_crystal_overlap():
    """Only translucent capturing parts are measured (§8 'crystal overlap'): the same buried lens in an opaque ring
    reports no crystal overlap (it still fails: undeclared groove, deep rule)."""
    base = SF.base_lens(cap="flat")
    rep, Ln, v = _check(SF.vbevel_lens(base), SF.solid_ring(base), translucent=False)
    assert Ln["crystal_overlap_mm3"] is None and Ln["crystal"] is None
    assert not rep["ok"]


def test_the_crystal_overlap_agrees_between_backends():
    pytest.importorskip("open3d")
    from glasses3d.g3d_host.seat_check import factory as host_factory
    base = SF.base_lens(cap="flat")
    L = SF.vbevel_lens(base)
    for F in (SF.grooved_ring(base), SF.grooved_ring(base, clearance=-0.05)):
        a = _check(L, F)[1]["crystal_overlap_mm3"]
        b = _check(L, F, fac=host_factory("open3d"))[1]["crystal_overlap_mm3"]
        assert abs(a - b) <= max(1e-3, 1e-3 * abs(a)), (a, b)


def test_the_apex_rule_grades_only_groove_seated_points_at_the_apex():
    """rules 'apex': c(0.5) where the point is groove-seated and enclosed, else the min over depths."""
    cap = np.array([[0.40, 0.60, 0.80, 0.60, 0.40]] * 3)
    m = stations.Measurement(cap=cap, room=np.full_like(cap, 2.0), owner=np.zeros(cap.shape, np.int64),
                             cover_front=np.zeros(3), cover_back=np.zeros(3), unmeasured=np.zeros(3, bool),
                             recast=np.zeros(3, np.int64), cls=np.array(["ENCLOSED"] * 3, object), mid_parts=[None] * 3,
                             groove=np.array([True, False, True]))
    m.cap[2, 0] = -0.01                                              # seated, but one depth short of the lips
    ft = rules.frame_type("acetate")
    import dataclasses
    rl = rules.rule_values(dataclasses.replace(ft, rule="apex"), m)
    assert np.allclose(rl.value, [0.80, 0.40, -0.01])
    m.groove = None
    rl = rules.rule_values(dataclasses.replace(ft, rule="apex"), m)
    assert np.allclose(rl.value, [0.40, 0.40, -0.01])


def test_the_point_plan_cache_is_bit_identical():
    """plan_growth(cache=...) reuses the point plan across calls that differ only in fold_slope; the plans equal
    those of uncached calls field by field."""
    from glasses3d.g3d.seat import deform
    sc = SF.metal_scene()
    L = sc["lens"]
    body = stations.recognise(L.V, L.T, "L").bodies[0]
    fb = factory([(m.V, m.T) for m in sc["parts"].values()])
    ft = rules.frame_type("metal")
    fr, meas, rl, be, lp = deform.measure_only(body, fb, factory, ft, None)
    cache: dict = {}
    a0 = PL.plan_growth(fr, meas, rl, ft, be, lp, body.V, cache=cache)
    S = a0.fold_slope * 1e-3                                            # binds everywhere
    a1 = PL.plan_growth(fr, meas, rl, ft, be, lp, body.V, fold_slope=S, cache=cache)
    b0 = PL.plan_growth(fr, meas, rl, ft, be, lp, body.V)
    b1 = PL.plan_growth(fr, meas, rl, ft, be, lp, body.V, fold_slope=S)
    assert cache.get("pre") is not None
    for a, b in ((a0, b0), (a1, b1)):
        for k in ("verdict", "reason"):
            assert np.array_equal(getattr(a, k).astype(str), getattr(b, k).astype(str)), k
        for k in ("clamped", "need", "target", "limit", "growth", "node_g", "node_target"):
            assert np.array_equal(np.asarray(getattr(a, k), float), np.asarray(getattr(b, k), float),
                                  equal_nan=True), k
        assert a.gate == b.gate and set(a.limits) == set(b.limits)
        for k in a.limits:
            assert np.array_equal(a.limits[k], b.limits[k], equal_nan=True), k
    assert not np.array_equal(a0.node_g, a1.node_g)                    # the slope did change the plan
