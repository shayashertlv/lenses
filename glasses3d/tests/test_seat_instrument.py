"""The independent grid instrument (tests/instruments/seat_grid.py) against the core on synthetic fixtures
(WP4_DESIGN.md §10 P0 without the Studio data): the capture check_glb grades -- `measure` with the lens as the last
part -- equals the instrument's occupancy-only measurement of the §3 definition at the instrument's stations."""
import numpy as np
import pytest

from glasses3d.g3d.seat import stations
from glasses3d.g3d_host import seat_check as SC
from glasses3d.tests.fixtures import seat_fixtures as SF
from glasses3d.tests.instruments import seat_grid as IG
from glasses3d.tests.instruments import seat_p0 as P0


def scene(name):
    if name == "v-ridge":
        L = SF.base_lens()
        return {"lens": L, "parts": {"Eyewire": SF.swept(L, SF.v_ridge_section(-0.4))}}
    if name in ("toric", "flat", "solidified", "coarse", "chamfer", "rounded"):
        return SF.acetate_scene(name)
    return {"metal": SF.metal_scene, "nylor": SF.nylor_scene}[name]()


def graded_pair(sc):
    b = stations.recognise(sc["lens"].V, sc["lens"].T).bodies[0]
    parts = [(k, p.V, p.T) for k, p in sc["parts"].items()]
    ins = IG.analyse(b.V, b.T, parts, 1.0, stations.DEPTHS, full_grid=False)
    F = ins["frames"]
    fr = stations.frames_from_arrays(F["Pf"], F["Pb"], F["n"], F["t"], F["u"], F["s"])
    be = SC.make_backend("numpy", [(V, T) for _, V, T in parts] + [(b.V, b.T)])
    m = stations.measure(fr, be, len(parts), keep_depths=True)
    ee = F["uf"][:, None] + np.asarray(stations.DEPTHS)[None, :] * (F["ub"] - F["uf"])[:, None]
    return ins, m, ee


@pytest.mark.parametrize("name", ["metal", "nylor", "toric", "flat", "solidified", "coarse", "v-ridge"])
def test_core_graded_capture_matches_the_instrument(name):
    ins, m, ee = graded_pair(scene(name))
    r = P0.compare(ins["cap_graded"], P0._beyond_grid(m, ee))
    assert r["stations_bad"] == 0 and r["definedness_mismatch_stations"] == 0, r
    assert r["abs_delta_p50_p95_p99_max"][3] <= 1e-3, r
    np.testing.assert_allclose(ins["edge_graded"], ee + m.edge, atol=1e-3)      # the lens edge per depth row


@pytest.mark.parametrize("name", ["chamfer", "rounded"])
def test_instrument_rungs_need_a_one_ring_wall(name):
    """The port's rungs join a front-rim and a back-rim vertex of ONE wall triangle; a chamfered or bullnose rim has
    intermediate rings, so its rungs collapse to the front corner. Those constructions are graded by the core-only
    tests (test_seat_backends: chamfer and bullnose capture from the lens surface), not against this instrument."""
    sc = scene(name)
    b = stations.recognise(sc["lens"].V, sc["lens"].T).bodies[0]
    parts = [(k, p.V, p.T) for k, p in sc["parts"].items()]
    F = IG.analyse(b.V, b.T, parts, 1.0, stations.DEPTHS, full_grid=False)["frames"]
    assert np.linalg.norm(F["Pb"] - F["Pf"], axis=1).max() == 0.0


def test_instrument_is_deterministic():
    sc = scene("metal")
    a, _, _ = graded_pair(sc)
    b, _, _ = graded_pair(sc)
    np.testing.assert_array_equal(a["cap_graded"], b["cap_graded"])
    np.testing.assert_array_equal(a["cap_fractions"], b["cap_fractions"])


def test_refined_runs_resolve_a_knife_edge():
    # a 1 mm slab ending 0.01 mm short of the 'edge' at u = 0.10: the 0.05 grid alone puts both at 0.075
    V, T = _box(-0.9, 0.09, -0.5, 0.5, -0.5, 0.5)
    scn = IG.build_scene(V, T)
    us = np.arange(IG.U0, IG.U1 + 1e-9, IG.DU)
    base = np.zeros((1, 3))
    dirs = np.array([[1.0, 0.0, 0.0]])
    occ = IG._occupancy(scn, base + us[:, None] * dirs)[None]
    runs = IG._refined_runs(scn, occ, base, dirs, us)[0]
    np.testing.assert_allclose(runs, [[-0.9, 0.09]], atol=1e-4)
    lens = np.array([[-np.inf, 0.10]])
    e, c = IG._graded(lens, runs, 0.10)
    assert e == pytest.approx(0.10) and np.isnan(c)                              # not inside, nothing beyond
    e, c = IG._graded(lens, IG._union([runs, np.array([[0.5, 3.0]])]), 0.10)
    assert c == pytest.approx(-0.40, abs=1e-4)


def _box(x0, x1, y0, y1, z0, z1):
    V = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    T = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]])
    return V, T
