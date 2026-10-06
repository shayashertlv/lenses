"""WP4 proof fix round (open items A-G of the stage-proof notes), host side (numpy core; open3d on the real lenses).
The Blender halves run in the proof (tests/proofs/wp4_proof.py, re-checked by tests/test_seat_real.py) and in
tests/test_seat_blender.py.

A  fold limit: node growth stays inside §4 step 11's 30 deg face rotation (Prada's sub-0.05 mm rim segments);
B  the check reads a declared V-bevel from the groove's lips (stations.groove_seat, rule 'apex');
C  the correction pass reaches the planned target inside runs (residual to the target, spread over the station's
   wall span);
E  refusal reasons: the notch E_CONCAVE; the narrow eyewire is a knife edge at exactly pass (proof review fix
   round 1 withdrew the pass + 0.05 capacity floor that blocked it, a binding-rule change: §4 step 4's 'below pass'
   stands, and §10 P4's 'every top station EXITS_MATERIAL, 0 moved' is not met, reported to the owner);
F  the grown front edge follows the cap (quadric with an intercept over the cap's own vertices, 12 nearest when
   sparse)."""
import pickle
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.seat import check as CK, deform, geom, plan as PL, rules, stations
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.tests.fixtures import seat_fixtures as SF

PROOF = Path(__file__).resolve().parents[1] / "data/wp4/proof"


def factory(parts):
    return NumpyBackend(parts)


# ------------------------------------------------------------------------------------------------- A: fold limit
def test_fold_total_is_lipschitz_inside_the_limits_and_never_moves_a_fixed_node():
    rng = np.random.default_rng(7)
    n = 60
    S = rng.uniform(0.001, 0.6, n)
    S[[10, 11, 40]] = [0.0006, 0.002, 0.0257]                    # Prada-like slivers
    G = np.where((np.arange(n) > 8) & (np.arange(n) < 45), rng.uniform(0.0, 0.45, n), 0.0)
    hi = np.where(G > 0, 0.5, 0.0)
    tot = PL.fold_total(G, hi, S)
    d = np.abs(np.roll(tot, -1) - tot)
    assert (d <= S + 1e-12).all()                                # every segment inside its slope
    assert (tot <= hi + 1e-12).all() and (tot[hi == 0] == 0).all()
    U = PL._lipschitz(hi, S, upper=False)
    assert (tot >= np.minimum(G, U) - 1e-12).all()               # lowered only where the limit forces it
    flat = np.full(n, 0.3)
    assert np.allclose(PL.fold_total(flat, np.full(n, 1.0), S), flat)    # nothing to do: unchanged


def test_fold_slopes_match_the_wall_face_rotation():
    """A segment of length e across a vertical rung, growth stepping by d at one end: the wall face turns by
    atan(d / e); fold_slopes returns tan(30 deg) e for a wall in the station plane."""
    L = SF.base_lens(cap="flat")
    body = stations.recognise(L.V, L.T, "L").bodies[0]
    fr = stations.station_frames(body)
    S = PL.fold_slopes(fr, body.V, PL.node_points(fr, body.V))
    P = body.V[fr.loop]
    seg = np.linalg.norm(np.roll(P, -1, 0) - P, axis=1)
    assert np.allclose(S, np.tan(np.radians(30.0)) * seg, rtol=0.02)


def _real(case, obj="o0", only=None):
    npz = PROOF / "npz" / f"{case}-a0.npz"
    glb = PROOF / "glb" / f"{case}-before.glb"
    if not npz.is_file() or not glb.is_file():
        pytest.skip(f"WP4 proof inputs for {case} not produced (python -m glasses3d.tests.proofs.wp4_proof)")
    pytest.importorskip("open3d")
    from glasses3d.g3d_host import seat_check as SC
    from glasses3d.g3d_host.glb import load
    z = np.load(npz)
    V, T, E = z[obj + "_V"], z[obj + "_T"], z[obj + "_E"]
    col = SC.collect(load(glb))
    body = stations.recognise(V, T, "lens").bodies[0]
    lo, hi = CK.rim_box(body)
    parts = [(c.name, c.V, c.T) for n, c in col["solids"]
             if CK.boxes_touch(c.box, lo, hi) and (only is None or n["name"].startswith(only))]
    return V, T, E, parts, SC.factory("open3d")


def _expected_raw(V, E, st0):
    """The band of the nodes owning a planner-FAIL point (both owners of a halfway point): §10 P5's moved set."""
    body, fr, pl = st0.body, st0.frames, st0.plan
    o1, o2 = fr.owners
    fail = pl.verdict.astype(str) == "FAIL"
    fn = np.zeros(len(fr.loop), bool)
    fn[o1[fail]] = True
    fn[o2[fail]] = True
    Vw, vid = geom.weld_exact(V)
    g2b = np.full(len(Vw), -1, np.int64)
    g2b[body.source_verts] = np.arange(len(body.V))
    pe = g2b[vid[E]]
    pe = pe[(pe >= 0).all(1) & (pe[:, 0] != pe[:, 1])]
    band = deform.lens_band(body, fr, PL.node_points(fr, body.V), pe)
    exp_b = np.flatnonzero((band.node >= 0) & fn[np.maximum(band.node, 0)])
    return set(np.flatnonzero(np.isin(vid, body.source_verts[exp_b])).tolist())


@pytest.mark.slow
def test_prada_delivery_seats_inside_the_face_rotation_limit():
    """§10 P3 on the delivery lens (host, open3d, the proof's input arrays and Front): seated, every point >= 0.60 at
    all depths after, max delta <= 0.45 mm, moved <= 200 vertices and only in failing nodes, no moved face past 30
    deg (the stage refused it: a 0.0444 mm run-start segment stepped 0 -> 0.068 mm, 56.8 deg)."""
    V, T, E, parts, fac = _real("prada", "o0", "Front")
    res = deform.seat_arrays(V, T, parts, rules.frame_type("acetate"), fac, poly_edges_raw=E, name="Lens L")
    assert res.status == "seated" and res.code is None, res.as_dict()
    st0, st1 = res.states[0]
    v1 = st1.rl.value[np.asarray(st1.rl.required, bool)]
    assert np.nanmin(v1) >= 0.60, np.nanmin(v1)
    D = np.linalg.norm(res.raw_delta, axis=1)
    assert D.max() <= 0.45 and 0 < res.moved_raw <= 200, (D.max(), res.moved_raw)
    assert set(np.flatnonzero(D > 0).tolist()) == _expected_raw(V, E, st0)
    body = st0.body
    Vw, vid = geom.weld_exact(V)
    rows = np.zeros(len(Vw), np.int64)
    rows[vid] = np.arange(len(vid))                              # a raw row of each welded vertex (duplicates agree)
    Vb1 = Vw[body.source_verts] + res.raw_delta[rows[body.source_verts]]
    _, ang, ratio = deform.face_changes(body, body.V, Vb1)
    assert ang.max() <= deform.FACE_ROTATION_MAX_DEG and 0.5 <= ratio.min() and ratio.max() <= 2.5, ang.max()


# ------------------------------------------------------------------------------------------- B: the groove reading
def _nodes(L, F, seat):
    return [{"node": 0, "name": "Lens_L", "V": L.V, "T": L.T, "N": np.full_like(L.V, np.nan), "roles": ["lens"],
             "lens": True, "translucent": False, "extras": {"g3d_seat": seat}},
            {"node": 1, "name": "Crystal rim", "V": F.V, "T": F.T, "N": np.full_like(F.V, np.nan), "roles": ["frame"],
             "lens": False, "translucent": True, "extras": {}}]


def _values(rep):
    L = rep["lenses"][0]
    return np.array([r["value"] for r in L["stations"] if r["required"]], float), L


def test_a_declared_v_bevel_in_its_groove_passes_at_its_apex():
    """§5 groove (clearance 0.03 mm) round a §5 V-bevel whose corners sit 0.396 mm beyond the lips: every point is
    groove-seated, ENCLOSED, and graded at the apex: 0.80 (target), gap 0.03; the stage read -0.03 at every depth."""
    base = SF.base_lens(cap="flat")
    L, F = SF.vbevel_lens(base), SF.grooved_ring(base)
    rep = CK.check_nodes(_nodes(L, F, {"frame_type": "acetate", "profile": "vbevel", "pass_mm": 0.6,
                                        "target_mm": 0.8}), [], [], factory, stations_table=True)
    v, Ln = _values(rep)
    assert rep["ok"], rep["reasons"]
    assert Ln["rule"] == "apex" and Ln["classes"]["ENCLOSED"] == Ln["points"]
    assert np.allclose(v, 0.80, atol=0.002), (v.min(), v.max())
    assert Ln["groove"]["seated"] == Ln["points"] and Ln["groove"]["gap_min_max_mm"] == [0.03, 0.03]


def test_the_groove_reading_needs_the_declaration_a_clearance_inside_the_band_and_the_apex():
    base = SF.base_lens(cap="flat")
    L = SF.vbevel_lens(base)
    # no profile declared: §3's reading (the clearance is a gap at every depth)
    rep = CK.check_nodes(_nodes(L, SF.grooved_ring(base), {"frame_type": "acetate"}), [], [], factory,
                         stations_table=True)
    v, Ln = _values(rep)
    assert not rep["ok"] and Ln["rule"] == "deep" and np.allclose(v, -0.03, atol=1e-3)
    vb = {"frame_type": "acetate", "profile": "vbevel"}
    # a 0.10 mm clearance is outside §5's 0.02-0.06: not seated, it stays a gap
    rep = CK.check_nodes(_nodes(L, SF.grooved_ring(base, clearance=0.10), vb), [], [], factory, stations_table=True)
    v, Ln = _values(rep)
    assert not rep["ok"] and Ln["groove"]["seated"] == 0 and v.max() < 0
    # seated, but the apex reaches only 0.50 beyond the lips (< pass 0.60)
    rep = CK.check_nodes(_nodes(L, SF.grooved_ring(base, corner_capture=0.096), vb), [], [], factory,
                         stations_table=True)
    v, Ln = _values(rep)
    assert not rep["ok"] and Ln["groove"]["seated"] == Ln["points"] and np.allclose(v, 0.50, atol=0.002)
    assert Ln["verdicts"] == {"FAIL": Ln["points"]}


def test_the_groove_reading_agrees_between_backends():
    o3d = pytest.importorskip("open3d")  # noqa: F841
    from glasses3d.g3d_host.seat_check import factory as host_factory
    base = SF.base_lens(cap="flat")
    nodes = _nodes(SF.vbevel_lens(base), SF.grooved_ring(base), {"frame_type": "acetate", "profile": "vbevel"})
    a = _values(CK.check_nodes(nodes, [], [], factory, stations_table=True))[0]
    b = _values(CK.check_nodes(nodes, [], [], host_factory("open3d"), stations_table=True))[0]
    assert np.abs(a - b).max() <= 1e-3


# --------------------------------------------------------------------------------- C: the correction pass's target
def _former_failures(st0, st1, ramp=PL.RUN_END_RAMP_MM):
    """(inside, ends): values after at the node points the check FAILED before, split at the run-end ramp."""
    ft = st0.plan
    n0 = PL.node_points(st0.frames, st0.body.V)
    n1 = PL.node_points(st1.frames, st1.body.V)
    fail = (ft.verdict.astype(str) == "FAIL") & np.asarray(st0.rl.required, bool)
    delta = PL.run_delta(fail, st0.frames.s, st0.frames.loop_mm)
    k = np.flatnonzero(fail[n0])
    v = st1.rl.value[n1[k]]
    d = delta[n0[k]]
    return v[d >= ramp], v[d < ramp]


def test_metal_former_failures_reach_the_target_inside_runs():
    """§10 P4 metal, NODE POINTS only: the correction pass brings the node points of former failures inside runs to
    the target (the stage read 0.4259 at the -0.30 / 0.10 junction: the correction corrected only below pass). This
    is what the correction does by construction; §10's criterion over every point is the next test."""
    sc = SF.metal_scene()
    L = sc["lens"]
    res = deform.seat_arrays(L.V, L.T, [(k, m.V, m.T) for k, m in sc["parts"].items()], rules.frame_type("metal"),
                             factory, poly_edges_raw=L.poly_edges, name="L")
    assert res.status == "seated", res.as_dict()
    inside, ends = _former_failures(*res.states[0])
    assert len(inside) > 40 and inside.min() >= 0.4499 and inside.max() <= 0.47, (inside.min(), inside.max())
    assert ends.min() >= 0.35


def _inside_points(st0, st1, ramp=PL.RUN_END_RAMP_MM):
    """Every required after-state point whose owning nodes (its node; between nodes: both segment ends) lie >= `ramp`
    inside a former failing run (proof review fix round 2, the grader's run_classes on the host state):
    (values, on_node, segment start, segment end, w)."""
    fr0, fr1 = st0.frames, st1.frames
    n0, n1 = PL.node_points(fr0, st0.body.V), PL.node_points(fr1, st1.body.V)
    fail = (st0.plan.verdict.astype(str) == "FAIL") & np.asarray(st0.rl.required, bool)
    dn = np.nan_to_num(PL.run_delta(fail, fr0.s, fr0.loop_mm)[n0], nan=-1.0)     # same loop: node k = node k
    sn = fr1.s[n1]
    nv = len(sn)
    k = np.clip(np.searchsorted(sn, fr1.s, side="right") - 1, 0, nv - 1)
    w = (fr1.s - sn[k]) / (np.r_[sn[1:], fr1.loop_mm][k] - sn[k])
    on = np.zeros(len(fr1), bool)
    on[n1] = True
    k2 = np.where(on, k, (k + 1) % nv)
    m = (dn[k] >= ramp) & (dn[k2] >= ramp) & np.asarray(st1.rl.required, bool)
    return st1.rl.value[m], on[m], k[m], k2[m], w[m]


def test_metal_points_between_the_junction_nodes_stay_below_0_44():
    """§10 P4 metal over EVERY point (proof review fix round 2; an open owner item, pinned here): the two points
    between the -0.30 / 0.10 junction nodes move with the linear interpolation of their nodes, while the frame's
    capture steps non-linearly inside that segment, so they read ~0.426 where both nodes read 0.45. Every other
    inside-run point reaches >= 0.4496. A point between nodes moves by a convex combination of its nodes' moves, so
    raising it by r raises at least one node by r: reaching 0.45 there needs a node at >= 0.474 (> §10's 0.47)."""
    sc = SF.metal_scene()
    L = sc["lens"]
    res = deform.seat_arrays(L.V, L.T, [(k, m.V, m.T) for k, m in sc["parts"].items()], rules.frame_type("metal"),
                             factory, poly_edges_raw=L.poly_edges, name="L")
    st0, st1 = res.states[0]
    v, on, k, k2, w = _inside_points(st0, st1)
    low = v < 0.44
    assert len(v) > 150 and low.sum() == 2 and not on[low].any(), (len(v), v[low])
    assert len(set(k[low].tolist())) == 1 and 0.42 <= v[low].min() < 0.43, (k[low], v[low])
    assert v[~low].min() >= 0.4496 and v.max() <= 0.47, (v[~low].min(), v.max())
    seg = int(k[low][0])
    nodes = v[on & ((k == seg) | (k == (seg + 1) % len(st1.frames.loop)))]
    assert len(nodes) == 2 and nodes.min() >= 0.4499
    need = 0.45 - v[low].min()                       # the larger shortfall of the two points
    assert nodes.min() + need > 0.47                 # the node raised by >= need would pass §10's 0.47


@pytest.mark.slow
def test_invu_former_failures_reach_the_target_inside_runs():
    """§10 P5 INVU (host, open3d, the proof's input arrays): every inside-run former failure's NODE POINT >= 0.45
    after (the stage: 187 of 261 below, min 0.4249). The station's depth lines cross a wall up to 1.7 segments wide
    (z-extruded wall, station planes leaning 31 deg), so the residual is spread over that span (deform.station_reach).
    Over every point (proof review fix round 2, an open owner item pinned here): some points between two corrected
    nodes read up to ~1.2e-3 mm below 0.45 (the correction corrects node points only)."""
    V, T, E, parts, fac = _real("invu")
    res = deform.seat_arrays(V, T, parts, rules.frame_type("semi_rimless"), fac, poly_edges_raw=E, name="Shield")
    assert res.status == "seated", res.as_dict()
    inside, ends = _former_failures(*res.states[0])
    assert len(inside) > 200 and inside.min() >= 0.45, (len(inside), inside.min(), int((inside < 0.45).sum()))
    assert ends.min() >= 0.35
    v, on, _, _, _ = _inside_points(*res.states[0])
    assert len(v) > 400 and v[on].min() >= 0.45 and 0.448 <= v.min() < 0.45, (len(v), v.min(), int((v < 0.45).sum()))
    assert set(np.flatnonzero(np.any(res.raw_delta != 0, axis=1)).tolist()) == _expected_raw(V, E, res.states[0][0])


def test_a_correction_never_moves_a_new_node():
    """Correction plans grow only the nodes an earlier pass moved (a halfway point between a moved and an unmoved node
    would hand the unmoved one growth)."""
    sc = SF.metal_scene()
    L = sc["lens"]
    res = deform.seat_arrays(L.V, L.T, [(k, m.V, m.T) for k, m in sc["parts"].items()], rules.frame_type("metal"),
                             factory, poly_edges_raw=L.poly_edges, name="L")
    st0 = res.states[0][0]
    _, vid = geom.weld_exact(L.V)
    moved_w = set(vid[np.flatnonzero(np.any(res.raw_delta != 0, axis=1))].tolist())
    loop_w = st0.body.source_verts[st0.frames.loop]              # welded ids of the oriented front loop
    g0 = st0.plan.node_g
    assert res.states[0][1].plan is not None and len(loop_w) == len(g0)
    assert {int(w) for w in loop_w[g0 > 0]} == moved_w & set(loop_w.tolist())


# ------------------------------------------------------------------------------------------- E: refusal reasons
def test_the_narrow_eyewire_is_a_knife_edge_at_exactly_pass():
    """§10 P4 says: with a 0.5 mm radial eyewire every top station EXITS_MATERIAL, 0 moved. The web holds at most
    web - 0.20 = 0.30 mm = pass with 0.20 mm of material left (0.3046-0.3067 along the tilted u), so under §4 step 4
    ('a clamp that leaves v < pass makes the point BLOCKED') no top point is blocked: each is clamped to its capacity,
    just above pass, and the lens is seated partially (other points BLOCKED, under the 20% gate). The proof fix round
    raised the capacity clamps' floor to pass + 0.05 to make this fixture refuse; proof review fix round 1 withdrew
    that binding-rule change. This test pins the knife edge the owner is asked to rule on (fixture web vs §4)."""
    sc = SF.metal_scene(radial_web=0.5)
    L = sc["lens"]
    ft = rules.frame_type("metal")
    st = deform.measure_state(stations.recognise(L.V, L.T, "L").bodies[0], factory([(m.V, m.T) for m in
                                                                                    sc["parts"].values()]),
                              factory, ft, None)
    top = (st.frames.sector == "top") & (rules.verdicts(ft, st.meas, st.rl, transition=False).astype(str) == "FAIL")
    assert top.sum() > 50
    capacity = st.rl.value + st.plan.limits["EXITS_MATERIAL"]
    p = st.rl.pass_mm[top]
    assert (capacity[top] >= p - rules.PASS_TOLERANCE_MM).all() and (capacity[top] <= p + 0.01).all(), (
        capacity[top].min(), capacity[top].max())
    assert (st.plan.verdict.astype(str)[top] == "FAIL").all()              # clamped at pass, not BLOCKED
    res = deform.seat_arrays(L.V, L.T, [(k, m.V, m.T) for k, m in sc["parts"].items()], ft, factory,
                             poly_edges_raw=L.poly_edges, name="L")
    assert res.status == "partial" and res.moved_raw > 0, res.as_dict()     # §10 P4's '0 moved' is not met
    assert res.code is None and res.bodies[0].get("gate") is None             # no gate: BLOCKED stays under 20%
    assert "EXITS_MATERIAL" not in res.bodies[0]["before"]["blocked_by"] or \
        res.bodies[0]["before"]["blocked_by"]["EXITS_MATERIAL"] < top.sum()


# ------------------------------------------------------------------------------------- F: the grown edge on the cap
@pytest.mark.parametrize("construction", ["chamfer", "rounded", "coarse", "toric"])
def test_the_grown_front_edge_follows_the_cap(construction):
    """§10 P4 matrix: grown front edge within 0.01 mm of the true cap (the stage: chamfer +0.031, bullnose +0.0135,
    coarse 0.0174 mm); where the construction's front loop lies off the cap (chamfer, bullnose) its offset is kept."""
    sc = SF.acetate_scene(construction, 0.10)
    L = sc["lens"]
    res = deform.seat_arrays(L.V, L.T, [("Rim", sc["parts"]["Rim"].V, sc["parts"]["Rim"].T)],
                             rules.frame_type("acetate"), factory, poly_edges_raw=L.poly_edges, name="L")
    assert res.status == "seated", res.as_dict()
    st0 = res.states[0][0]
    body = st0.body
    Vw, vid = geom.weld_exact(L.V)
    rows = np.zeros(len(Vw), np.int64)
    rows[vid] = np.arange(len(vid))
    loop = body.source_verts[st0.frames.loop]
    V0 = Vw[loop]
    V1 = V0 + res.raw_delta[rows[loop]]
    moved = np.any(V1 != V0, axis=1)
    kind = "toric" if construction == "toric" else "sphere"
    dev0 = V0[:, 2] - SF.cap_z(V0[:, 0], V0[:, 1], kind, apex=(-32.0, 0.0))
    dev1 = V1[:, 2] - SF.cap_z(V1[:, 0], V1[:, 1], kind, apex=(-32.0, 0.0))
    assert moved.sum() > 20
    assert np.abs(dev1 - dev0)[moved].max() <= 0.01, np.abs(dev1 - dev0)[moved].max()
