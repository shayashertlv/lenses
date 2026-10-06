"""WP4 tools stage on the host (numpy core only): plan_growth / plan_lift and the array-level seat_lens and
hide_eyewire (g3d/seat/plan.py, deform.py) on the deterministic fixtures. WP4_DESIGN.md §4, §6, §10 P4 (the binding
P4 proof runs in Blender and on the exported GLB; these pin the core's behaviour so a regression shows here first)."""
import json

import numpy as np
import pytest

from glasses3d.g3d.seat import deform, geom, plan as PL, rules
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.g3d_host.seat_check import check_glb
from glasses3d.tests.fixtures import seat_fixtures as SF


def factory(parts):
    return NumpyBackend(parts)


def parts_of(sc):
    return [(k, m.V, m.T) for k, m in sc["parts"].items()]


def seat(sc, frame_type=None, **kw):
    L = sc["lens"]
    ft = rules.frame_type(frame_type or sc["frame_type"], sc.get("lower_edge"))
    return deform.seat_arrays(L.V, L.T, parts_of(sc), ft, factory, poly_edges_raw=L.poly_edges, name="Lens", **kw)


def again(sc, res, frame_type=None, **kw):
    L = sc["lens"]
    ft = rules.frame_type(frame_type or sc["frame_type"], sc.get("lower_edge"))
    return deform.seat_arrays(L.V + res.raw_delta, L.T, parts_of(sc), ft, factory, poly_edges_raw=L.poly_edges,
                              name="Lens", **kw)


# ---------------------------------------------------------------------------------------------------- planning
def test_runs_are_circular():
    m = np.array([1, 1, 0, 0, 1, 0, 1, 1], bool)
    assert [r.tolist() for r in PL.runs(m)] == [[4], [6, 7, 0, 1]]
    assert [r.tolist() for r in PL.runs(np.ones(4, bool))] == [[0, 1, 2, 3]]
    assert PL.runs(np.zeros(3, bool)) == []


def test_run_targets_ramp_from_the_run_ends():
    s = np.arange(40) * 0.5
    fail = np.zeros(40, bool)
    fail[5:35] = True
    t = PL.run_targets(fail, s, 20.0, np.full(40, 0.60), np.full(40, 0.80))
    assert np.isnan(t[:5]).all() and np.isnan(t[35:]).all()
    assert t[5] == pytest.approx(0.65) and t[34] == pytest.approx(0.65)          # pass + 0.05 at both ends
    assert t[20] == pytest.approx(0.80)                                          # >= 2 mm from an end: target
    assert t[7] == pytest.approx(0.65 + 0.15 * PL.smoothstep(1.0 / 2.0))        # smoothstep over 2 mm
    whole = PL.run_targets(np.ones(40, bool), s, 20.0, np.full(40, 0.6), np.full(40, 0.8))
    assert np.allclose(whole, 0.8)                                               # no ends: the target


def test_lift_field_is_full_at_the_source_and_zero_beyond_the_ramp():
    L = 100.0
    v = PL.lift_field(np.array([10.0, 11.5, 13.0, 13.01, 99.0]), np.array([10.0]), np.array([0.8]), L, 3.0)
    assert v[0] == pytest.approx(0.8) and v[1] == pytest.approx(0.4) and v[2] == 0.0 and v[3] == 0.0
    assert PL.lift_field(np.array([1.0]), np.array([99.5]), np.array([0.8]), L, 3.0)[0] > 0      # wraps round


# -------------------------------------------------------------------------------------------------- seat_arrays
def test_metal_fixture_seats_and_a_second_call_moves_nothing():
    sc = SF.metal_scene()
    res = seat(sc)
    assert res.status == "seated" and res.code is None, res.as_dict()
    st0, st1 = res.states[0]
    assert st0.plan.counts()["FAIL"] > 150 and "FAIL" not in st1.plan.counts()
    req = np.asarray(st1.rl.required, bool)
    val = st1.rl.value[req]
    assert val.min() >= 0.30 - rules.PASS_TOLERANCE_MM
    nf = sc["lens"].meta["nfront"]
    out_nodes = np.flatnonzero(np.any(res.raw_delta[:128] != 0, axis=1))           # ring 0 = the front rim
    assert len(out_nodes) > 40
    # former failures, read at their node points (the points between nodes are resampled after the move): §10 P4
    # 'former failures 0.44-0.47 inside runs, >= 0.35 at run ends'
    np0 = PL.node_points(st0.frames, st0.body.V)
    np1 = PL.node_points(st1.frames, st1.body.V)
    failed = st0.plan.verdict.astype(str)[np0] == "FAIL"
    after = st1.rl.value[np1[failed]]
    assert after.min() >= 0.35 - rules.PASS_TOLERANCE_MM and after.max() <= 0.47 + 1e-3, np.percentile(after, [0, 50, 100])
    assert np.median(after) == pytest.approx(0.45, abs=0.01)
    # every front corner of a required point is held (the eyewire lip covers it), the §7 metal rule
    assert np.asarray(st1.rl.front_ok)[req].all()
    # only the band of moved nodes moved: the front rim vertex and its rung partner (one-ring wall, no duplicates)
    moved = np.flatnonzero(np.any(res.raw_delta != 0, axis=1))
    assert set(moved.tolist()) == set(out_nodes.tolist()) | set((out_nodes + nf).tolist())
    second = again(sc, res)
    assert second.status == "already_seated" and second.moved_raw == 0, second.as_dict()
    json.dumps(res.as_dict())


@pytest.mark.slow
@pytest.mark.parametrize("construction", ["sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded",
                                          "coarse"])
def test_acetate_robustness_matrix_reaches_the_target(construction):
    """§10 P4 robustness matrix (acetate ring, inset 0.10): min-over-depth p5 >= 0.78 after seating, and a second
    call moves nothing."""
    sc = SF.acetate_scene(construction, inset=0.10)
    res = seat(sc)
    assert res.status == "seated", res.as_dict()
    st1 = res.states[0][1]
    v = st1.rl.value[np.asarray(st1.rl.required, bool)]
    assert np.percentile(v, 5) >= 0.78, (construction, np.percentile(v, [5, 50, 95]))
    second = again(sc, res)
    assert second.status == "already_seated" and second.moved_raw == 0, second.as_dict()


def test_aperture_too_large_is_refused_by_the_blocked_gate():
    L = SF.base_lens()
    sc = {"lens": L, "parts": {"Rim": SF.ring_frame(L, -2.5)}, "frame_type": "acetate"}
    res = seat(sc)
    assert res.status == "refused" and res.code == "E_TOO_MANY_BLOCKED", res.as_dict()
    assert "NEEDS_GROWTH" in res.message and res.moved_raw == 0


def test_open_lens_and_missing_ranges_and_thin_edge_are_refused():
    L = SF.base_lens()
    ring = SF.ring_frame(L, 0.10)
    op = SF.open_lens(L)
    r = deform.seat_arrays(op.V, op.T, [("Rim", ring.V, ring.T)], rules.frame_type("acetate"), factory)
    assert r.status == "refused" and r.code == "E_OPEN", r.as_dict()
    r = deform.seat_arrays(L.V, L.T, [("Rim", ring.V, ring.T)], rules.frame_type("browline"), factory)
    assert r.code == "E_NO_RANGES"
    r = deform.seat_arrays(L.V, L.T, [("Rim", ring.V, ring.T)], rules.frame_type("acetate"), factory,
                           min_edge_mm=1.8)
    assert r.code == "E_THICKNESS" and "1.6" in r.message.replace("1.599", "1.6")


def test_a_concave_notch_is_blocked_and_never_folded():
    """A 0.54 mm notch radius: points there are BLOCKED(CONCAVE) and the plan refuses at the gate (E_CONCAVE, §10 P4
    'Refusals: ... notch CONCAVE'), nothing written. Proof fix round: the stage refused it only at the §4 step 11
    post-check (a 166 deg fold beside the blocked points), which §10 does not count as the CONCAVE refusal."""
    L = SF.lens(SF.notched_outline(depth=2.0, width=0.08, theta0=0.0123), t=1.6)
    ring = SF.ring_frame(L, 0.10)
    res = deform.seat_arrays(L.V, L.T, [("Rim", ring.V, ring.T)], rules.frame_type("acetate"), factory,
                             poly_edges_raw=L.poly_edges)
    assert res.status == "refused" and res.code == "E_CONCAVE", res.as_dict()
    assert res.bodies[0]["before"]["blocked_by"].get("CONCAVE", 0) >= 1
    assert res.bodies[0]["gate"] == "E_CONCAVE" and "postcheck" not in res.bodies[0]
    assert "CONCAVE" in res.message and res.raw_delta is None


def test_other_part_clamps_growth():
    """A non-frame part 0.30 mm beyond the top edge: the grown edge keeps 0.10 mm from it, so the top points are
    BLOCKED(OTHER_PART) (0.10 capture + 0.20 allowed < 0.60) and their nodes do not grow into it."""
    L = SF.base_lens(cap="flat")
    ring = SF.ring_frame(L, 0.10)
    bar = SF.swept(L, SF.rect_section(0.30, 0.60, -0.40, 0.40), SF.arc_mask(L, "top", 0.10))
    res = deform.seat_arrays(L.V, L.T, [("Rim", ring.V, ring.T)], rules.frame_type("acetate"), factory,
                             obstacles=[("Temple", bar.V, bar.T)], poly_edges_raw=L.poly_edges)
    assert res.status == "partial", res.as_dict()
    st0 = res.states[0][0]
    blocked = st0.plan.verdict.astype(str) == "BLOCKED"
    other = blocked & (st0.plan.reason.astype(str) == "OTHER_PART")
    assert other.sum() >= 0.8 * blocked.sum() and other.sum() >= 10, np.unique(st0.plan.reason[blocked].astype(str))
    assert np.allclose(st0.plan.limits["OTHER_PART"][other], 0.20, atol=0.01)
    # nodes owning an OTHER_PART point grow at most that point's limit (along their own u, before the move)
    nodes = np.unique(st0.frames.node[other])
    verts = st0.frames.loop[nodes]
    _, vid = geom.weld_exact(L.V)
    rows = np.array([np.flatnonzero(vid == g)[0] for g in st0.body.source_verts[verts]])
    assert np.linalg.norm(res.raw_delta[rows], axis=1).max() <= 0.20 + 1e-3


def test_fused_lenses_are_seated_separately():
    A, B = SF.base_lens(), SF.mirrored_lens()
    F = SF.fused(A, B)
    parts = [("Rim_L", *(lambda m: (m.V, m.T))(SF.ring_frame(A, 0.10))),
             ("Rim_R", *(lambda m: (m.V, m.T))(SF.ring_frame(B, 0.10)))]
    pe = np.vstack([A.poly_edges, B.poly_edges + len(A.V)])
    res = deform.seat_arrays(F.V, F.T, parts, rules.frame_type("acetate"), factory, poly_edges_raw=pe)
    assert res.status == "seated" and len(res.bodies) == 2, res.as_dict()
    for st0, st1 in res.states:
        v = st1.rl.value[np.asarray(st1.rl.required, bool)]
        assert np.percentile(v, 5) >= 0.78


def test_drilled_hole_is_reported_and_never_grown():
    D = SF.drilled()
    ring = SF.ring_frame(D, 0.10)
    res = deform.seat_arrays(D.V, D.T, [("Rim", ring.V, ring.T)], rules.frame_type("acetate"), factory,
                             poly_edges_raw=D.poly_edges)
    assert res.status == "seated", res.as_dict()
    body = res.states[0][0].body
    assert body.summary()["drilled_holes"] == 1
    hole = np.unique(np.r_[body.front_loops[1], body.back_loops[1]])
    _, vid = geom.weld_exact(D.V)
    rows = np.flatnonzero(np.isin(vid, body.source_verts[hole]))
    assert len(rows) == 256 and not np.any(res.raw_delta[rows] != 0)


# ------------------------------------------------------------------------------------------------- lift_arrays
def browline_inputs(sc):
    L = sc["lens"]
    ew, brow = sc["parts"]["Eyewire"], sc["parts"]["Brow"]
    edges = geom.edge_table(ew.T).edges
    return L, ew, brow, edges


def test_hide_eyewire_then_seat_lens_on_a_browline_fixture(tmp_path):
    """§6 then §4 on the pre-lift browline fixture: the eyewire's inner 0.65 mm shows below the brow; burying it
    with the 0.10 mm margin needs 0.75 mm; afterwards the band is 0, the rigid rings keep their section, the slope
    stays <= 0.5 mm/mm, a second call lifts nothing; browline seating then reaches the in-range deep target and the
    host check passes on a GLB of the result."""
    sc = SF.browline_scene()
    L, ew, brow, edges = browline_inputs(sc)
    rng = [sc["range"]]
    res = deform.lift_arrays(ew.V, ew.T, edges, [("Brow", brow.V, brow.T)], [], L.V, L.T, factory, rng,
                             lens_poly_edges_raw=L.poly_edges, lens_name="Lens", eyewire_name="Eyewire")
    assert res.status == "lifted", (res.code, res.message, res.report)
    rep = res.report
    assert rep["lambda_p50_max_mm"][1] == pytest.approx(0.75, abs=0.006)
    assert rep["band_after"]["front_max_mm"] == 0 and rep["band_after"]["back_max_mm"] == 0
    assert rep["band_after"]["area_outside_max_mm2"] < 0.005
    assert rep["section_area_rel_max"] <= 0.01 and rep["slope_max"] <= 0.5
    assert rep["moved_below_centre"] == {"eyewire_vertices": 0, "lens_nodes": 0}
    assert rep["eyewire_capture_change_max_mm"] <= 0.02
    ew1 = ew.V + res.eyewire_delta
    L1 = L.V + res.lens_result.raw_delta
    r2 = deform.lift_arrays(ew1, ew.T, edges, [("Brow", brow.V, brow.T)], [], L1, L.T, factory, rng,
                            lens_poly_edges_raw=L.poly_edges, lens_name="Lens", eyewire_name="Eyewire")
    assert r2.status == "already_hidden", (r2.code, r2.message, r2.report)
    parts = [("Eyewire", ew1, ew.T), ("Brow", brow.V, brow.T)]
    s = deform.seat_arrays(L1, L.T, parts, rules.frame_type("browline"), factory, ranges=rng,
                           poly_edges_raw=L.poly_edges, name="Lens")
    assert s.status == "seated", s.as_dict()
    st1 = s.states[0][1]
    inr = np.asarray(st1.rl.in_range, bool)
    v = st1.rl.value[inr]
    assert inr.sum() > 20 and v.min() >= 0.60 - rules.PASS_TOLERANCE_MM and np.median(v) >= 0.78,         np.percentile(v, [0, 5, 50])
    L2 = L1 + s.raw_delta
    glb = tmp_path / "browline.glb"
    lens_mesh = SF.Mesh(L2, L.T)
    SF.write_glb(glb, [{"name": "Lens_L", "mesh": lens_mesh, "role": "lens", "normals": "smooth",
                        "extras": {"g3d_seat": json.dumps({"frame_type": "browline"}),
                                   "g3d_hidden_x_ranges_mm": json.dumps([list(sc["range"])])},
                        "material": {"name": "Lens", "transmission": 1.0}},
                       {"name": "Eyewire", "mesh": SF.Mesh(ew1, ew.T), "role": "frame", "normals": "smooth",
                        "material": {"name": "Frame"}},
                       {"name": "Brow", "mesh": brow, "role": "frame", "normals": "smooth",
                        "material": {"name": "Frame"}}])
    rep = check_glb(glb)
    assert rep["ok"], rep["reasons"]
    lens = rep["lenses"][0]
    assert lens["frame_type_source"] == "g3d_seat" and lens["band"]["graded"]["ok"]


def test_hide_eyewire_refuses_without_ranges_or_with_an_open_eyewire():
    sc = SF.browline_scene()
    L, ew, brow, edges = browline_inputs(sc)
    r = deform.lift_arrays(ew.V, ew.T, edges, [("Brow", brow.V, brow.T)], [], L.V, L.T, factory, [])
    assert r.code == "E_NO_RANGES"
    r = deform.lift_arrays(ew.V, ew.T[4:], edges, [("Brow", brow.V, brow.T)], [], L.V, L.T, factory,
                           [sc["range"]])
    assert r.code == "E_OPEN"
    far = [(sc["range"][0] - 200.0, sc["range"][1] - 200.0)]               # a range with no brow: nothing selected
    r = deform.lift_arrays(ew.V, ew.T, edges, [("Brow", brow.V, brow.T)], [], L.V, L.T, factory, far)
    assert r.code == "E_NOT_UNDER", (r.code, r.message)
