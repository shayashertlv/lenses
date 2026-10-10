"""Revision 0 from an existing temple mesh (plan/WP6_FACETS.md §2.4, §5 T3): g3d.temple.template_from_mesh on meshes
the /2 core built (the answer is known), its parts (joint knots, robust line, distances, end rule, tip rounding), and
the Prada original (skipped without the local export; information, its numbers are reported by
tests/proofs/wp6v2_template_report.py)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import template as TT
from glasses3d.g3d.temple.spec import TempleError

G3D = Path(__file__).resolve().parents[1]
PRADA_GLB = G3D / "data/wp6/astra_temple_lab_20261010/real-astra-001/work/exports/head-b89ad6603d00577f.glb"
PRADA_GLB_SHA = "3198847036fcdc0f84a2eadc6cd429cc66f090f8e8431e246f854bd2e90d3012"
BODY = "Temple right | faceted sweep and ear bend"


def template_of(spec, side: int = 1, samples: int = 8000):
    tb = build_temple(spec)
    W, _, tris = tb.world(side)
    h = tb.spec.hinge
    s2, rep = TT.template_from_mesh(W, tris, side, h["z_mm"], h["axis_xz_mm"], samples=samples)
    return tb, s2, rep


def test_round_trip_of_a_core_wedge_and_its_mirror():
    tb, s2, rep = template_of(FX.wedge())
    tb2 = build_temple(s2)
    assert tb2.checks["closed"] and tb2.checks["euler"] == 2
    assert s2["facets"] == {} and s2["roll_mm"] == 0.0 and s2["schema"] == "g3d.temple/2"
    assert rep["knots"]["count"] < TT.MAX_KNOTS                          # under the cap: every chain within tol
    assert all(v <= TT.RDP_TOL_MM + 1e-12 for k, v in rep["simplification_error_mm"].items() if k != "note")
    assert rep["length_lost_mm"]["hinge_end"] == pytest.approx(TT.END_EPS_MM)
    assert rep["length_lost_mm"]["tip"] == pytest.approx(TT.END_EPS_MM)
    ps, ps2 = tb.spec, tb2.spec
    d = np.linspace(ps2.d0, ps2.d1, 400)
    for a, b in ((ps.y_top, ps2.y_top), (ps.y_bot, ps2.y_bot), (ps.inner, ps2.inner)):
        assert np.abs(a(d) - b(d)).max() <= TT.RDP_TOL_MM
    # the source surface is the core's triangulation of the wedge, so a station section is piecewise linear in y;
    # the robust line through it keeps the wedge within a few hundredths of a mm
    assert np.abs(ps2.t_top(d) - 3.5).max() <= 0.1 and np.abs(ps2.t_bot(d) - 6.0).max() <= 0.1
    dist = rep["distance_original_to_rebuild"]
    assert dist["all"]["max_mm"] <= 0.1 and dist["all"]["p95_mm"] <= TT.RDP_TOL_MM
    assert set(dist["regions"]) == {"hinge_zone", "middle", "bend", "tip"} and dist["bend_d"] is not None
    assert rep["inner_deviation_mm"]["max_mm"] <= TT.RDP_TOL_MM
    _, s3, _ = template_of(FX.wedge(), side=-1)
    assert s3 == s2                                                     # the -X temple is the exact mirror


def test_facets_are_flattened_and_reported():
    tb, s2, rep = template_of(FX.prada_default())
    assert s2["facets"] == {} and build_temple(s2).checks["closed"]
    dist = rep["distance_original_to_rebuild"]["regions"]
    assert dist["hinge_zone"]["p95_mm"] > 0.1                           # F1-F4 relief is not in revision 0
    assert dist["tip"]["max_mm"] <= 0.05                                # the facet-free arm is rebuilt
    assert rep["outer_fit_resid_p95_mm"]["max"] > 0.1 and rep["outer_fit_resid_p95_mm"]["max_at_d"] < 60


def test_end_rule_and_tip_round_fit():
    sp = FX.straight()
    sp["outline"]["tip_round_mm"] = 2.0                                 # a rounded end the core can rebuild
    _, s2, rep = template_of(sp)
    assert rep["tip_round"]["objective_mm"] < rep["tip_round"]["objective_at_0_mm"]
    assert 1.5 <= s2["outline"]["tip_round_mm"] <= 2.0                   # ties go to the smaller radius
    sp["outline"]["tip_round_mm"] = 9.0                                 # a tip narrowing to 2 mm: the end rule cuts
    _, s3, rep3 = template_of(sp)
    assert rep3["length_lost_mm"]["tip"] == pytest.approx(1.0, abs=1e-9)    # the last station >= 50 % is d 119
    assert s3["outline"]["top"][-1][0] == 119.0


def test_end_cut():
    D = np.arange(0.0, 11.0)
    H = np.array([1.0, 10, 10, 10, 10, 10, 10, 10, 10, 6, 2])
    assert TT.end_cut(H, D, at_tip=True) == 9                           # median of d 5..10 = 10: 6 >= 5, 2 < 5
    assert TT.end_cut(H, D, at_tip=False) == 1                          # median of d 0..5 = 10: 1 < 5


def test_shared_knots():
    D = np.arange(0.0, 101.0)
    Y = np.stack([np.interp(D, [0, 30, 60, 100], [0, 3, -1, 2]), np.interp(D, [0, 45, 100], [5, 5, 7])])
    k = TT.shared_knots(D, Y)
    assert sorted(D[k].tolist()) == [0.0, 30.0, 45.0, 60.0, 100.0]       # one knot set for both chains
    assert TT.chain_errors(D, Y, k).max() <= 1e-12
    noisy = Y + 0.2 * np.sin(D)[None]
    k2 = TT.shared_knots(D, noisy, max_knots=12)
    assert len(k2) == 12 and TT.chain_errors(D, noisy, k2).max() > TT.RDP_TOL_MM


def test_robust_line_ignores_outliers():
    rng = np.random.default_rng(1)
    y = np.linspace(-10, 10, 400)
    x = 60.0 + 0.1 * y + rng.normal(0, 0.01, len(y))
    x[:40] += 0.8                                                       # a facet bulge on 10 % of the profile
    a, b, _ = TT.robust_line(y, x)
    assert abs(a - 60.0) < 0.02 and abs(b - 0.1) < 0.005
    A = np.stack([np.ones_like(y), y], axis=1)
    ls = np.linalg.lstsq(A, x, rcond=None)[0]
    assert abs(a - 60.0) < abs(ls[0] - 60.0)                            # better than least squares


def test_point_mesh_distance_is_exact():
    tb = build_temple(FX.pyramid())
    rng = np.random.default_rng(2)
    P = np.c_[rng.uniform(-5, 140, 300), rng.uniform(-35, 20, 300), rng.uniform(50, 75, 300)]
    got = TT.point_mesh_distance(P, tb.V, tb.tris)
    tri = tb.V[tb.tris]
    want = np.concatenate([TT._tri_dist(P[i:i + 50], tri[:, 0], tri[:, 1], tri[:, 2]).min(axis=1)
                           for i in range(0, len(P), 50)])
    assert np.abs(got - want).max() <= 1e-12 and got.max() > TT.SEARCH_MM   # far points use the full search
    # a point above the largest inner polygon's centroid: its plane distance
    inner = [i for i, k in enumerate(tb.poly_kind) if k == "inner"]
    area = [np.linalg.norm(np.cross(tb.V[tb.polys[i]], np.roll(tb.V[tb.polys[i]], -1, axis=0)).sum(axis=0))
            for i in inner]
    f = inner[int(np.argmax(area))]
    c = tb.V[tb.polys[f]].mean(axis=0)
    q = c + 0.3 * tb.normals[f]
    assert TT.point_mesh_distance(q[None], tb.V, tb.tris)[0] == pytest.approx(0.3, abs=1e-9)


def test_refuses_a_mesh_that_is_not_a_temple():
    V = np.array([[60, 0, 0], [61, 0, 0], [60, 1, 0], [60, 0, -5.0]])
    F = np.array([[0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 3, 2]])
    with pytest.raises(TempleError) as e:
        TT.template_from_mesh(V, F, 1, 0.0, None)
    assert e.value.code == "E_TARGET"


def test_prada_original_template():
    if not PRADA_GLB.exists():
        pytest.skip("the c0000 export of the Prada original is not present (local evidence)")
    raw = PRADA_GLB.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PRADA_GLB_SHA
    from glasses3d.g3d_host import glb
    g = glb.load(PRADA_GLB)
    p = next(p for p in g.primitives if p.node_name == BODY)
    V = p.positions * 1000.0
    s2, rep = TT.template_from_mesh(V, p.triangles, 1, float(V[:, 2].max()), [64.6, -10.1], samples=10000)
    tb = build_temple(s2)
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    assert rep["knots"]["count"] <= TT.MAX_KNOTS and rep["stations"]["count"] > 130
    assert 0 < rep["length_lost_mm"]["tip"] < 2.0
    # an instrument sanity bound (a wrong side, hinge or chart would put the rebuild centimetres away), not a gate
    assert rep["distance_original_to_rebuild"]["all"]["max_mm"] < 2.0
