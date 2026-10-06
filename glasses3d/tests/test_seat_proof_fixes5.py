"""WP4 proof review fix round 2 (host side).

FOLD blocks spread along a run: a point BLOCKED(FOLD) gets no growth, so its node may stop, and the fold limit then
lowers the next node to one segment's slope, and so on. On the narrow-eyewire fixture (§10 P4, 0.5 mm web) a few
non-FOLD blocks at the ends of the nasal -0.30 run (EXITS_MATERIAL / UNHIDEABLE) leave the ENTIRE nasal run blocked
and ungrown. The plan now records which fold pass blocked each point (`SeatPlan.fold_generation`) and reports
`direct` (first pass) and `knock_on` (later passes) blocks apart (`counts()['fold_blocks']`, the E_TOO_MANY_BLOCKED and
E_POSTCHECK messages), so a report never presents the cascade as independent blocks. These tests pin the behaviour
the owner is asked to rule on (FOLD reason code, WP4_DESIGN implementation notes); they do not endorse it."""
import numpy as np

from glasses3d.g3d.seat import deform, plan as PL, rules, stations
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.tests.fixtures import seat_fixtures as SF


def factory(parts):
    return NumpyBackend(parts)


def _state(web):
    sc = SF.metal_scene(radial_web=web)
    L = sc["lens"]
    body = stations.recognise(L.V, L.T, "L").bodies[0]
    fb = factory([(m.V, m.T) for m in sc["parts"].values()])
    return sc, deform.measure_state(body, fb, factory, rules.frame_type("metal"), None)


def test_fold_blocks_spread_along_the_nasal_run_and_are_reported_apart():
    sc, st = _state(0.5)
    pl = st.plan
    c = pl.counts()
    v, r = pl.verdict.astype(str), pl.reason.astype(str)
    fold = (v == "BLOCKED") & (r == "FOLD")
    fb = c["fold_blocks"]
    assert fb == pl.fold_blocks() and fb["direct"] + fb["knock_on"] == c["blocked_by"]["FOLD"] == int(fold.sum())
    assert fb["knock_on"] > 2 * fb["direct"] and fb["passes"] >= 4, fb      # a cascade, not independent blocks
    g = pl.fold_generation
    assert (g[fold] >= 1).all() and (g[~((r == "FOLD"))] == 0).all()
    # every pass after the first blocks only points next to (within one segment of) a point an earlier pass blocked:
    # the block spreads from node to node
    nv = len(st.frames.loop)
    o1, o2 = st.frames.owners
    for k in range(2, fb["passes"] + 1):
        prev = np.zeros(nv, bool)
        m = fold & (g < k)
        prev[o1[m]] = prev[o2[m]] = True
        near = prev | np.roll(prev, 1) | np.roll(prev, -1)
        cur = np.flatnonzero(fold & (g == k))
        assert all(near[o1[i]] or near[o2[i]] for i in cur), k
    # the cascade starts at non-FOLD blocks in the same sector and leaves the whole nasal run blocked
    nas = st.frames.sector == "nasal"
    seeds = (v == "BLOCKED") & (r != "FOLD") & nas
    assert seeds.any() and fold[nas].sum() == fold.sum()
    run = nas & (st.rl.value < -0.25)                                      # the -0.30 run (before: -0.305)
    assert run.sum() > 20 and (v[run] == "BLOCKED").all(), dict(zip(*np.unique(v[run], return_counts=True)))
    assert (pl.node_g[np.unique(np.r_[o1[run], o2[run]])] == 0).all()     # no node of the run grows


def test_the_cascade_leaves_the_nasal_run_ungrown_after_the_partial_seat():
    """The partial seat moves nothing in the -0.30 run: its points read -0.305 after, as before (36 FAIL after)."""
    sc, st = _state(0.5)
    L = sc["lens"]
    res = deform.seat_arrays(L.V, L.T, [(k, m.V, m.T) for k, m in sc["parts"].items()], rules.frame_type("metal"),
                             factory, poly_edges_raw=L.poly_edges, name="L")
    assert res.status == "partial" and res.moved_raw > 0
    assert res.bodies[0]["before"]["fold_blocks"] == st.plan.counts()["fold_blocks"]
    st1 = res.states[0][1]
    nas1 = (st1.frames.sector == "nasal") & np.asarray(st1.rl.required, bool)
    assert np.nanmin(st1.rl.value[nas1]) < -0.30
    n0 = PL.node_points(st.frames, st.body.V)
    run_nodes = np.flatnonzero((st.frames.sector[n0] == "nasal") & (st.rl.value[n0] < -0.25))
    assert len(run_nodes) > 5 and (res.states[0][0].plan.node_g[run_nodes] == 0).all()


def test_no_fold_blocks_with_the_s10_eyewire():
    """Control: the 0.9 mm web has no FOLD block (counts() has no 'fold_blocks'), and the lens seats."""
    _, st = _state(0.9)
    assert "fold_blocks" not in st.plan.counts() and st.plan.fold_blocks() is None
    assert not (st.plan.fold_generation > 0).any()


def test_the_gate_message_names_the_fold_split():
    """E_TOO_MANY_BLOCKED's message carries the split (a gate on the narrow eyewire with every clamp at 1e-6 of
    its slope: the fold limit binds everywhere)."""
    sc, st = _state(0.5)
    fb = factory([(m.V, m.T) for m in sc["parts"].values()])
    ft = rules.frame_type("metal")
    fr, meas, rl, be, lp = deform.measure_only(st.body, fb, factory, ft, None)
    p = PL.plan_growth(fr, meas, rl, ft, be, lp, st.body.V, fold_slope=st.plan.fold_slope * 1e-6)
    assert p.gate == "E_TOO_MANY_BLOCKED" and "FOLD:" in p.gate_message and "knock-on" in p.gate_message, \
        (p.gate, p.gate_message)
