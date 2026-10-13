"""g3d.temple/2 spec (plan/WP6_FACETS.md §2.1): canonical form, hash, validation, PL extension, the wedge base, knot
canonicalisation (snapping) and the refusals raised while parsing."""
from __future__ import annotations

import copy
import json
import math

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import spec as S
from glasses3d.g3d.temple.build import station_list
from glasses3d.g3d.temple.spec import TempleError


def test_canonical_fills_defaults_sorts_and_expands_shorthand():
    s = FX.pyramid()
    del s["options"], s["chamfers"], s["roll_mm"], s["creases"]
    c = S.canonical(s)
    assert c["options"] == S.DEFAULT_OPTIONS
    assert c["chamfers"] == [] and c["roll_mm"] == 0.0 and c["creases"] == []
    assert list(c["nodes"]) == sorted(c["nodes"])
    assert list(c["facets"]) == sorted(c["facets"])
    assert c["facets"]["F2"] == {"poly": ["A", "B", "C"], "plane": "nodes", "fade": None}
    assert c["nodes"]["A"][2] == "base" and c["nodes"]["C_lo"][2] is None
    assert c["outline"]["tip_round_mm"] == 0.0 and c["schema"] == "g3d.temple/2"


def test_crease_pairs_are_canonical():
    s = FX.pyramid()
    s["creases"] = [{"facets": ["F4", "F2"], "shape": "ridge"}, {"facets": ["base", "F2"], "shape": "valley"}]
    c = S.canonical(s)
    assert c["creases"] == [{"facets": ["F2", "F4"], "shape": "ridge"}, {"facets": ["F2", "base"], "shape": "valley"}]


def test_hash_is_stable_and_sensitive():
    a = FX.pyramid()
    b = copy.deepcopy(a)
    b["nodes"] = dict(reversed(list(b["nodes"].items())))           # key order does not matter
    assert S.spec_sha256(a) == S.spec_sha256(b)
    assert S.canonical_json(a) == S.canonical_json(b)
    c = copy.deepcopy(a)
    c["nodes"]["C"][2] += 1e-9
    assert S.spec_sha256(c) != S.spec_sha256(a)
    json.loads(S.canonical_json(a))


@pytest.mark.parametrize("mut", [
    lambda s: s.update(colour="red"),
    lambda s: s["hinge"].update(axis="x"),
    lambda s: s["nodes"].update(Z=[1, 2]),
    lambda s: s["nodes"].update(Z=[1, 2, "proud"]),
    lambda s: s["facets"].update(X={"poly": ["A", "B"]}),
    lambda s: s["facets"].update(X={"poly": ["A", "B", "Q"]}),
    lambda s: s["facets"].update(X={"poly": ["A", "B", "C"], "plane": {"crease": ["A", "Q"], "rise_per_mm": 0.1}}),
    lambda s: s["facets"].update(X={"poly": ["A", "B", "C"], "fade": {"d0": 5.0, "d1": 5.0}}),
    lambda s: s["facets"].update(X={"poly": ["A", "B", "C"], "fade": [5.0, 9.0]}),
    lambda s: s["facets"].update(X={"poly": ["A", "B", "C"], "fade": {"d0": 5.0, "d1": 9.0}}),   # removed 2026-10-13
    lambda s: s["facets"].update({"a|b": ["A", "B", "C"]}),
    lambda s: s.update(creases=[{"facets": ["F2", "F9"], "shape": "ridge"}]),
    lambda s: s.update(creases=[{"facets": ["F2", "F3"], "shape": "sharp"}]),
    lambda s: s.update(creases=[{"facets": ["F2", "F3"], "shape": "ridge"},
                                {"facets": ["F3", "F2"], "shape": "ridge"}]),
    lambda s: s.update(base={"top": [[0, 5]]}),
    lambda s: s.update(options={"t_min_mm": -1}),
    lambda s: s.update(options={"crease_sharp_deg": 6}),
    lambda s: s.update(options={"snap_mm": -0.1}),
    lambda s: s.update(roll_mm=-0.5),
    lambda s: s.update(zone_end_d=60.0),
])
def test_unknown_keys_and_bad_types_are_e_spec(mut):
    s = FX.pyramid()
    mut(s)
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SPEC", (e.value.code, e.value.message)
    assert e.value.to_dict()["status"] == "refused"


def test_a_v1_spec_is_refused_with_the_conversion_hint():
    s = FX.slab()
    s["schema"] = "g3d.temple/1"
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SPEC" and "v1_to_v2" in e.value.message


def test_crease_must_be_an_edge_of_its_polygon():
    s = FX.crease_rise()
    s["facets"]["F1"]["plane"]["crease"] = ["A", "G"]               # A-G is a diagonal of A-B-G-G_hi-A_hi
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SPEC" and "edge" in e.value.message


@pytest.mark.parametrize("code", ["E_SPEC", "E_OUTLINE", "E_OUTLINE_NOT_SINGLE_VALUED", "E_OUTLINE_NOT_SIMPLE",
                                  "E_SHORT_EDGE", "E_CHAMFER", "E_ROLL"])
def test_parse_refusals_fire_with_fields(code):
    spec = FX.refusal_cases()[code]
    with pytest.raises(TempleError) as e:
        S.parse(spec)
    assert e.value.code == code
    assert isinstance(e.value.fields, dict)
    json.dumps(e.value.to_dict())


def test_pl_tables_extend_linearly_and_never_clamp():
    t = S.PL([[0.0, 10.0], [10.0, 12.0], [20.0, 12.0]], "t")
    assert t(-5.0) == pytest.approx(9.0) and t(30.0) == pytest.approx(12.0)
    assert t(5.0) == pytest.approx(11.0)
    assert np.allclose(t(np.array([-10.0, 25.0])), [8.0, 12.0])
    assert S.PL([[3.0, 7.0]], "c")(-50.0) == 7.0
    ps = S.parse(FX.slab())                                          # INNER's first segment: 65.5 - 3.7 d / 65
    assert ps.inner(-1.0) == pytest.approx(65.5 + 3.7 / 65.0)
    assert ps.y_top(-1.0) == pytest.approx(10.5 - (13.43 - 10.5) / 15.0)


def test_wedge_base_is_linear_in_y_between_the_edges():
    ps = S.parse(FX.wedge())
    for d in (0.0, 33.3, 90.0):
        yt, yb = ps.y_top(d), ps.y_bot(d)
        assert ps.base_t(d, yt) == pytest.approx(3.5) and ps.base_t(d, yb) == pytest.approx(6.0)
        assert ps.base_t(d, 0.5 * (yt + yb)) == pytest.approx(4.75)
        assert ps.x_base(d, yt) == pytest.approx(ps.inner(d) + 3.5)
        below = yb - 2.0                                             # a virtual node below the bottom edge
        assert ps.base_t(d, below) == pytest.approx(6.0 + 2.5 * 2.0 / (yt - yb))


def test_node_values_number_base_and_solved():
    s = FX.pyramid()
    s["nodes"]["V"] = [-1.0, 30.0, "base"]                          # virtual, before the hinge-end cap
    ps = S.parse(s)
    assert ps.node_X("C") == pytest.approx(ps.inner(22.6) + 8.0)
    assert ps.node_X("A") == pytest.approx(ps.x_base(0.0, 10.5))
    assert ps.node_X("C_lo") is None
    assert ps.node_X("V") == pytest.approx(ps.inner(-1.0) + 5.0)    # extended tables, not clamped


def test_tip_round_arcs_follow_the_round_corner_rule():
    s = FX.slab()
    s["outline"]["tip_round_mm"] = 1.5
    ps = S.parse(s)
    for chain in (ps.top, ps.bottom):
        assert np.all(np.diff(chain[:, 0]) > 0)                     # still single valued
        assert chain[-1, 0] == 136.5
    top = ps.top
    n_arc = len(top) - (len(FX.TOP) - 1)
    assert n_arc >= 9
    arc = top[-n_arc:]
    u = np.array(FX.TOP[-1]) - np.array(FX.TOP[-2])
    theta = math.acos(np.clip(u @ np.array([0.0, -1.0]) / np.linalg.norm(u), -1, 1))
    assert n_arc - 1 >= max(8, math.ceil(8 * math.degrees(theta) / 90 - 1e-9))
    A, B, C = arc[0], arc[len(arc) // 2], arc[-1]
    M = np.array([[2 * (B - A)[0], 2 * (B - A)[1]], [2 * (C - A)[0], 2 * (C - A)[1]]])
    ctr = np.linalg.solve(M, np.array([B @ B - A @ A, C @ C - A @ A]))
    assert np.allclose(np.linalg.norm(arc - ctr, axis=1), 1.5, atol=1e-9)


def test_tip_round_too_large_is_refused():
    s = FX.slab()
    s["outline"]["tip_round_mm"] = 9.0
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_OUTLINE"


def test_opening_needs_an_axis():
    s = FX.slab()
    s["hinge"] = {"z_mm": -10.6, "axis_xz_mm": None, "opening_deg": 3.0}
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SPEC"
    s["hinge"]["opening_deg"] = 0.0                                  # a null axis is fine at opening 0
    S.parse(s)


def test_chamfer_width_tapers_to_zero_at_both_ends():
    ch = {"edge": "top", "d": [70.0, 125.0], "width_mm": 1.2, "depth_mm": 0.8, "taper_mm": 4.8}
    w = S.chamfer_width(ch, np.array([69.0, 70.0, 72.4, 74.8, 100.0, 120.2, 125.0, 126.0]))
    assert np.allclose(w, [0, 0, 0.6, 1.2, 1.2, 1.2, 0, 0])
    assert S.canonical(FX.top_chamfer_arm())["chamfers"][0]["taper_mm"] == pytest.approx(4.8)


def test_roll_profile_is_a_quarter_arc_at_equal_angles():
    r = 0.6
    s, c = S.roll_profile(r)
    assert len(s) == S.ROLL_BANDS + 1 and s[0] == 0.0 and c[0] == r and s[-1] == r and c[-1] == 0.0
    assert np.allclose(c, r - np.sqrt(r * r - (r - s) ** 2), atol=1e-12)          # on c(s) = r - sqrt(r^2 - (r-s)^2)
    ang = np.degrees(np.arctan2(r - s, r - c))                                       # 90 deg at the edge ... 0 at s = r
    assert np.allclose(np.diff(ang), -90.0 / S.ROLL_BANDS, atol=1e-9)


# --------------------------------------------------------------------------------------------- snapping
def test_node_d_snaps_onto_a_table_knot_and_is_reported():
    s = FX.pyramid()
    s["nodes"]["B"] = [44.97, 9.52, "base"]                          # 0.03 mm from the outline knots at d 45
    ps = S.parse(s)
    assert ps.nodes["B"][0] == 45.0
    assert {"what": "node B", "field": "d", "from": 44.97, "to": 45.0} in ps.snaps
    assert S.parse(ps.snapped).snaps == []                            # the snapped spec is a fixed point
    assert ps.canonical["nodes"]["B"][0] == 44.97                     # the sha covers the spec as written
    s["options"]["snap_mm"] = 0.0
    assert S.parse(s).nodes["B"][0] == 44.97 and S.parse(s).snaps == []


def test_two_knots_of_one_table_within_snap_are_short_edges():
    with pytest.raises(TempleError) as e:
        S.parse(FX.refusal_cases()["E_SHORT_EDGE"])
    assert e.value.code == "E_SHORT_EDGE" and e.value.fields["table"] == "inner"
    assert e.value.fields["knots"] == [1, 2]
    # facts and every way out (v3p3-review-e2e P3-2: "remove one or move it at least 0.05 mm away")
    assert e.value.message == ("inner knots 1 (d 30) and 2 (d 30.02) lie within snap_mm 0.05 of d 30, one station; "
                               "ways: delete either knot, or move either at least 0.05 mm in d from the other")


def test_a_node_near_the_outline_is_resnapped_onto_it():
    s = FX.pyramid()
    s["nodes"]["A"] = [0.0, 10.47, "base"]                           # 0.03 mm below the hinge-end top corner
    ps = S.parse(s)
    assert ps.nodes["A"][:2] == (0.0, 10.5)
    assert any(x["what"] == "node A" and x["field"] == "y" for x in ps.snaps)


def _crossing_spec(extra_node=None):
    """A facet whose edge U-W leaves the outline through the top edge at d 15.03, 0.03 mm behind the outline knots at
    d 15 (top and bottom)."""
    s = FX.slab()
    U = np.array([10.0, 5.0])
    yc = 13.43 + (14.09 - 13.43) / 5.0 * 0.03
    Xc = np.array([15.03, yc])
    W = U + 3.0 * (Xc - U)
    s["nodes"] = {"U": [10.0, 5.0, "base"], "M": [20.0, -8.0, 6.0], "V": [30.0, 5.0, "base"],
                  "W": [float(W[0]), float(W[1]), None]}
    s["facets"] = {"T": ["U", "M", "V", "W"]}
    if extra_node:
        s["nodes"]["J"] = extra_node
    return s, Xc


def test_a_crossing_near_outline_knots_moves_them_onto_it():
    s, Xc = _crossing_spec()
    ps = S.parse(s)
    k = [i for i, p in enumerate(ps.top) if abs(p[0] - 15.03) < 1e-9]
    assert k and np.allclose(ps.top[k[0]], Xc, atol=1e-9)             # the top knot now IS the crossing
    assert not any(abs(p[0] - 15.0) < 1e-9 for p in ps.top) and not any(abs(p[0] - 15.0) < 1e-9 for p in ps.bottom)
    assert {x["what"] for x in ps.snaps} >= {"outline.top[1]", "outline.bottom[4]"}
    st = station_list(ps)
    assert min(abs(np.asarray(st) - 15.03)) < 1e-9 and min(abs(np.asarray(st) - 15.0)) > 0.02
    tb = build_temple(s)
    assert tb.checks["closed"] and tb.checks["euler"] == 2


def test_a_crossing_near_a_node_that_cannot_move_is_a_short_edge():
    s, _ = _crossing_spec(extra_node=[15.0, -2.0, "base"])               # a node at d 15: neither moves
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SHORT_EDGE" and "node J" in e.value.fields["refs"]
    # v3p3-review-e2e P1-1 / P3-2: "which cannot move: move a node at least snap_mm 0.05 away", followed along d (0.06
    # mm a step), never cleared: the crossing moves with the node. Now the edge, the station, and every way out.
    assert e.value.fields["edges"] == [["T", "W", "U"]]
    assert e.value.message == (
        "facet edge U-W (T) crosses the top outline at d 15.030, 0.030 mm from d 15, where node J stands (the parse "
        "moves only outline, inner and base knots onto a crossing): stations under snap_mm 0.05 apart have no build; "
        "ways: a node of that edge moved so the crossing lies at d 15 or at least 0.05 mm from it (a node near the "
        "outline put onto it, within snap_mm, or the outline through it, is no crossing; moved in d, the crossing "
        "moves with it), node J at least 0.05 mm from the crossing in d, or a layout without that crossing")


def test_two_crossings_within_snap_name_their_edges_and_every_way_out():
    """v3p3-review-e2e P3-2: "two facet edges cross the outline at d ... (< snap_mm 0.05 apart): move a node" named
    neither edge and one remedy. T's edge U-W crosses the top at d 15.03 and T2's U2-W2 at d 15.06."""
    s, _ = _crossing_spec()
    yt = 13.43 + (14.09 - 13.43) / 5.0 * 0.06
    U2 = np.array([12.0, 9.0])
    W2 = U2 + 3.0 * (np.array([15.06, yt]) - U2)
    s["nodes"].update({"U2": [12.0, 9.0, "base"], "W2": [float(W2[0]), float(W2[1]), None], "M2": [11.0, 11.0, "base"]})
    s["facets"]["T2"] = ["U2", "W2", "M2"]
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SHORT_EDGE" and e.value.fields["edges"] == [[["T", "W", "U"]], [["T2", "U2", "W2"]]]
    assert e.value.message == (
        "facet edges U-W (T) and U2-W2 (T2) cross the outline at d 15.030 and 15.060, 0.030 mm apart: stations under "
        "snap_mm 0.05 apart have no build; ways: a node of either edge moved so the two crossings lie at least 0.05 mm "
        "apart in d or at one point (a node near the outline put onto it, within snap_mm, or the outline through it, "
        "is no crossing), or a layout without one of them")


def _many_crossings_spec(n_near: int, n_fac: int = 5) -> dict:
    """n_fac facets whose P -> V edge crosses the bottom edge (y -10) at d - 1.8, with a bottom knot 0.03 mm behind
    the first n_near of those crossings."""
    ds = [10.0 + 20.0 * i for i in range(n_fac)]
    bottom = [[0.0, -10.0]] + [[d - 1.8 + 0.03, -10.0] for d in ds[:n_near]] + [[120.0, -10.0]]
    nodes, facets = {}, {}
    for i, d in enumerate(ds):
        nodes.update({f"P{i}": [d, 5.0, 4.6], f"R{i}": [d + 10.0, 5.0, 4.8], f"V{i}": [d - 3.0, -20.0, 4.0],
                      f"W{i}": [d + 10.0, -20.0, None]})
        facets[f"T{i}"] = [f"P{i}", f"V{i}", f"W{i}", f"R{i}"]
    s = FX.straight(nodes=nodes, facets=facets)
    s["outline"]["bottom"] = bottom
    return s


@pytest.mark.parametrize("n_near", [1, 3, 4, 5])
def test_every_snappable_crossing_is_snapped(n_near):
    ps = S.parse(_many_crossings_spec(n_near))                         # was E_SHORT_EDGE from 4 crossings (4 passes)
    moved = [x for x in ps.snaps if x.get("note") == "outline knot moved onto a facet-edge crossing"]
    assert len(moved) == n_near
    for d in [10.0 + 20.0 * i - 1.8 for i in range(n_near)]:
        assert min(abs(ps.bottom[:, 0] - d)) <= 1e-9                     # each knot now IS its crossing
    assert S.parse(ps.snapped).snaps == []
    tb = build_temple(ps)
    assert tb.checks["closed"] and tb.checks["euler"] == 2
