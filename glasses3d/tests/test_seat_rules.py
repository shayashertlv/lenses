"""WP4 frame types, classes, rule values and verdicts (g3d/seat/rules.py, stations.classify): WP4_DESIGN.md §3,
§4 step 2, §7, §8."""
import numpy as np
import pytest

from glasses3d.g3d.seat import rules, stations
from glasses3d.g3d.seat.rules import FRAME_TYPES, frame_type

NAN = np.nan


def meas(cap, cover_front=None, cover_back=None, unmeasured=None):
    cap = np.asarray(cap, float).reshape(-1, 5)
    N = len(cap)
    m = stations.Measurement(cap=cap, room=np.full_like(cap, 2.0), owner=np.where(np.isfinite(cap), 0, -1),
                             cover_front=np.full(N, NAN) if cover_front is None else np.asarray(cover_front, float),
                             cover_back=np.full(N, NAN) if cover_back is None else np.asarray(cover_back, float),
                             unmeasured=np.zeros(N, bool) if unmeasured is None else np.asarray(unmeasured, bool),
                             recast=np.zeros(N, np.int64), cls=np.empty(N, dtype=object), mid_parts=[None] * N)
    m.cls = stations.classify(m)
    return m


def test_frame_type_table_matches_section_7():
    a, m, b = FRAME_TYPES["acetate"], FRAME_TYPES["metal"], FRAME_TYPES["browline"]
    assert (a.required, a.rule, a.pass_mm, a.target_mm, a.range_mm, a.exposed) == ("all", "deep", 0.60, 0.80, (0.6, 1.0), "refused")
    assert (m.required, m.rule, m.pass_mm, m.target_mm, m.range_mm) == ("all", "thin", 0.30, 0.45, (0.3, 0.6))
    assert (b.rule, b.pass_mm, b.in_range_rule, b.in_range_pass_mm, b.in_range_target_mm) == ("thin", 0.30, "deep", 0.60, 0.80)
    p, n = FRAME_TYPES["semi_rimless/plain"], FRAME_TYPES["semi_rimless/nylor"]
    assert (p.required, p.rule, p.pass_mm, p.target_mm, p.exposed) == ("captured", "thin", 0.30, 0.45, "visible_by_design")
    assert (n.rule, n.min_edge_mm, n.profile) == ("flats", 1.8, "groove")
    assert FRAME_TYPES["rimless"].required == "none"
    assert a.min_edge_mm == m.min_edge_mm == 1.3
    for ft in FRAME_TYPES.values():
        assert ft.cite.startswith("PLAN §3")


def test_frame_type_lookup_and_capture_override():
    assert frame_type("semi_rimless").lower_edge == "plain"
    assert frame_type("semi_rimless", "nylor").rule == "flats"
    ac = frame_type("acetate", capture_mm=1.0)
    assert (ac.target_mm, ac.pass_mm) == (1.0, pytest.approx(0.8))              # the type's 0.20 gap is kept
    me = frame_type("metal", capture_mm=0.5)
    assert (me.target_mm, me.pass_mm) == (0.5, pytest.approx(0.35))             # 0.15 gap
    with pytest.raises(ValueError):
        frame_type("acetate", capture_mm=1.6)
    with pytest.raises(ValueError):
        frame_type("acetate", "nylor")
    with pytest.raises(ValueError):
        frame_type("shield")


def test_classes():
    m = meas([[0.5, 0.5, 0.5, 0.5, 0.5],                         # ENCLOSED
              [0.6, 0.6, 0.6, -0.3, -0.5],                       # LIPPED: mid captured, front covered (lip)
              [-0.2, -0.2, -0.3, -0.3, -0.3],                    # LIPPED: gap beside, frame at the front depth (INVU rail)
              [NAN, NAN, 0.15, 0.4, 0.5],                        # BACKED: frame only at the back depths (latch housing)
              [NAN, NAN, NAN, NAN, NAN],                         # BACKED: back covered only (rear seat)
              [NAN, NAN, -2.0, -2.0, -2.0],                      # NEAR: 2 mm gap
              [NAN, NAN, NAN, NAN, NAN],                         # EXPOSED
              [0.5, 0.5, 0.5, 0.5, 0.5]],                        # UNMEASURED
             cover_front=[-1, -1.2, NAN, NAN, NAN, NAN, NAN, NAN],
             cover_back=[NAN, NAN, NAN, NAN, 1.5, NAN, NAN, NAN],
             unmeasured=[0, 0, 0, 0, 0, 0, 0, 1])
    assert m.cls.tolist() == ["ENCLOSED", "LIPPED", "LIPPED", "BACKED", "BACKED", "NEAR", "EXPOSED", "UNMEASURED"]


def test_far_cover_alone_is_exposed_but_near_cover_is_not():
    m = meas([[NAN] * 5, [NAN] * 5], cover_front=[4.0, 2.5])
    assert m.cls.tolist() == ["EXPOSED", "NEAR"]


def test_rule_values():
    m = meas([[0.7, 0.65, 0.62, 0.66, 0.9], [0.7, NAN, 0.62, 0.66, 0.9]], cover_front=[-1, -1])
    deep = rules.rule_values(frame_type("acetate"), m)
    assert deep.value[0] == pytest.approx(0.62) and np.isnan(deep.value[1])     # deep needs every depth
    thin = rules.rule_values(frame_type("metal"), m)
    np.testing.assert_allclose(thin.value, [0.62, 0.62])
    flats = rules.rule_values(frame_type("semi_rimless", "nylor"), m)
    np.testing.assert_allclose(flats.value, [0.7, 0.7])
    assert rules.rule_values(frame_type("rimless"), m).required.sum() == 0


def test_pass_tolerance_and_full_rim_failures():
    m = meas([[0.296] * 5, [0.294] * 5, [NAN] * 5], cover_front=[-1, -1, NAN])
    ft = frame_type("metal")
    rl = rules.rule_values(ft, m)
    v = rules.verdicts(ft, m, rl)
    assert v.tolist() == ["PASS", "FAIL", "FAIL"]                                  # EXPOSED on a full rim fails
    chk = rules.check_verdict(ft, m, rl, v)
    assert not chk["pass"] and any("EXPOSED/BACKED" in r for r in chk["reasons"])


def test_semi_rimless_visible_by_design_and_required_set():
    m = meas([[0.5] * 5, [0.1] * 5, [NAN, NAN, 0.1, 0.3, 0.4], [NAN] * 5, [NAN, NAN, -2, -2, -2]],
             cover_front=[-1, -1, NAN, NAN, NAN])
    ft = frame_type("semi_rimless")
    rl = rules.rule_values(ft, m)
    assert rl.required.tolist() == [True, True, False, False, False]
    v = rules.verdicts(ft, m, rl)
    assert v.tolist() == ["PASS", "FAIL", "VISIBLE_BY_DESIGN", "VISIBLE_BY_DESIGN", "VISIBLE_BY_DESIGN"]


def test_transition_within_3mm_of_a_visible_point():
    # a ring of 20 points 0.5 mm apart: point 0 visible by design; failing covered points at 1..19
    cap = np.full((20, 5), 0.1)
    cap[0] = NAN
    m = meas(cap, cover_front=np.r_[NAN, np.full(19, -1.0)], cover_back=np.r_[1.0, np.full(19, NAN)])
    ft = frame_type("semi_rimless")
    rl = rules.rule_values(ft, m)
    s = np.arange(20) * 0.5
    v = rules.verdicts(ft, m, rl, s, 10.0)
    assert v[0] == "VISIBLE_BY_DESIGN"
    trans = np.flatnonzero(v == "TRANSITION")
    assert set(trans.tolist()) == {1, 2, 3, 4, 5, 6, 14, 15, 16, 17, 18, 19}         # arc <= 3 mm, both ways round
    assert (v[7:14] == "FAIL").all()
    chk = rules.check_verdict(ft, m, rl, v)
    assert any("TRANSITION" in r for r in chk["reasons"]) and chk["verdicts"]["TRANSITION"] == 12


def test_check_verdict_reasons():
    m = meas(np.full((200, 5), 0.5), cover_front=np.full(200, -1.0), unmeasured=np.r_[np.ones(3), np.zeros(197)])
    ft = frame_type("metal")
    rl = rules.rule_values(ft, m)
    v = rules.verdicts(ft, m, rl)
    chk = rules.check_verdict(ft, m, rl, v)
    assert chk["verdicts"]["UNMEASURED"] == 3 and any("UNMEASURED" in r for r in chk["reasons"])
    m2 = meas(np.full((200, 5), 0.5), cover_front=np.full(200, -1.0), unmeasured=np.r_[np.ones(2), np.zeros(198)])
    rl2 = rules.rule_values(ft, m2)
    v2 = rules.verdicts(ft, m2, rl2)
    assert rules.check_verdict(ft, m2, rl2, v2)["pass"]                              # 2/200 = 1%: allowed
    assert not rules.check_verdict(ft, m2, rl2, v2, band_ok=False)["pass"]
    assert not rules.check_verdict(ft, m2, rl2, v2, crystal_overlap_mm3=0.02)["pass"]
    assert rules.check_verdict(ft, m2, rl2, v2, crystal_overlap_mm3=0.01)["pass"]
    assert not rules.check_verdict(ft, m2, rl2, v2, sharpened=True, rim_split_p5_deg=40.0)["pass"]
    assert rules.check_verdict(ft, m2, rl2, v2, sharpened=True, rim_split_p5_deg=78.0)["pass"]


def test_unmeasured_counts_toward_the_limit_on_semi_rimless():
    cap = np.full((20, 5), 0.5)
    m = meas(cap, cover_front=np.full(20, -1.0), unmeasured=np.r_[np.ones(10), np.zeros(10)])
    for name in ("semi_rimless", "metal"):
        ft = frame_type(name)
        rl = rules.rule_values(ft, m)
        assert rl.required.sum() == 20                                           # UNMEASURED is required
        chk = rules.check_verdict(ft, m, rl, rules.verdicts(ft, m, rl))
        assert not chk["pass"] and chk["verdicts"]["UNMEASURED"] == 10, name
        assert any("UNMEASURED 10/20" in r for r in chk["reasons"])


def test_full_rim_thin_rule_needs_the_front_covered():
    # a V ridge with no lip: held at the front depth (LIPPED by class), 0.39 at mid-depth, nothing over the front
    ridge = [-0.32, -0.01, 0.39, -0.01, -0.47]
    m = meas([ridge, ridge, [0.5] * 5, [-0.2, 0.1, 0.4, 0.1, -0.2]],
             cover_front=[NAN, -1.0, NAN, NAN])
    assert m.cls.tolist() == ["LIPPED", "LIPPED", "ENCLOSED", "LIPPED"]
    ft = frame_type("metal")
    rl = rules.rule_values(ft, m)
    assert rl.front_required.all() and rl.front_ok.tolist() == [False, True, True, False]
    v = rules.verdicts(ft, m, rl)
    assert v.tolist() == ["FAIL", "PASS", "PASS", "FAIL"]                      # ENCLOSED holds the front depth too
    chk = rules.check_verdict(ft, m, rl, v)
    assert any("2 with the front corner not covered" in r for r in chk["reasons"])
    # browline: thin (metal) outside the ranges needs the lip; the deep rule inside does not
    m.mid_parts = [[np.array([[-0.4, 0.3]]), np.array([[0.2, 3.0]])]] * 4
    m.edge = np.zeros((4, 5))
    b = frame_type("browline")
    rlb = rules.rule_values(b, m, np.array([-60.0, -60.0, -30.0, -30.0]), [(-56.2, -11.6)])
    assert rlb.front_required.tolist() == [True, True, False, False]
    # semi_rimless: the same stations are required and graded on c(0.5) alone (INVU's rail)
    s = frame_type("semi_rimless")
    rls = rules.rule_values(s, m)
    assert not rls.front_required.any() and rls.required.all()
    assert rules.verdicts(s, m, rls).tolist() == ["PASS", "PASS", "PASS", "PASS"]


def test_zero_required_stations_never_pass():
    m = meas(np.full((10, 5), NAN))                                            # nothing within reach: EXPOSED
    for name in ("semi_rimless", "metal", "acetate"):
        ft = frame_type(name)
        rl = rules.rule_values(ft, m)
        chk = rules.check_verdict(ft, m, rl, rules.verdicts(ft, m, rl))
        assert not chk["pass"], name
    s = frame_type("semi_rimless")
    rl = rules.rule_values(s, m)
    chk = rules.check_verdict(s, m, rl, rules.verdicts(s, m, rl))
    assert chk["required"] == 0 and any("no required station" in r for r in chk["reasons"])
    r = frame_type("rimless")
    rl = rules.rule_values(r, m)
    assert rules.check_verdict(r, m, rl, rules.verdicts(r, m, rl))["pass"]   # rimless: report only


def test_held_parts_and_band_candidates_skip_a_corner_owner():
    # 10 stations: the eyewire (0) is innermost at 8, the brow (1) at 2 corner stations with a saddle (2) outward
    N = 10
    m = meas(np.full((N, 5), 0.4), cover_front=np.full(N, -1.0))
    m.owner = np.zeros((N, 5), np.int64)
    m.owner[:2] = 1
    assert stations.held_parts(m) == (0,)
    m.owner[:5] = 1
    assert stations.held_parts(m) == ()                                         # 50/50: nobody holds >= 60%


def test_browline_switches_rule_inside_the_hidden_ranges():
    m = meas(np.tile([0.7, 0.65, 0.5, 0.66, 0.9], (5, 1)), cover_front=np.full(5, -1.0))
    eyewire, brow = np.array([[-0.5, 0.3]]), np.array([[0.2, 3.6]])
    m.mid_parts = [[eyewire, brow]] * 4 + [[eyewire, np.zeros((0, 2))]]           # the last: loop bottom, no brow
    m.edge = np.zeros((5, 5))
    ft = frame_type("browline")
    x = np.array([-60.0, -30.0, 20.0, 50.0, -30.0])
    rl = rules.rule_values(ft, m, x, [(-56.2, -11.6), (11.6, 56.2)])
    assert rl.in_range.tolist() == [False, True, True, True, False]
    assert rl.rule.tolist() == ["thin", "deep", "deep", "deep", "thin"]
    np.testing.assert_allclose(rl.pass_mm, [0.30, 0.60, 0.60, 0.60, 0.30])
    v = rules.verdicts(ft, m, rl)
    assert v.tolist() == ["PASS", "FAIL", "FAIL", "FAIL", "PASS"]                 # 0.5 < 0.60 deep in the ranges
    assert rules.rule_values(ft, m, x, None).in_range.sum() == 0
    assert stations.layered(m).tolist() == [True, True, True, True, False]
