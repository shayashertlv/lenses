"""WP6 check_relief geometry core (§5, §8 P1 geometry half): the analytic prism's share / dihedral / bulges, the
30 deg chamfered slab trap, the delivered Prada GLB's reference numbers, chart handling for both sides."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple, report
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import measure as MS
from glasses3d.tests.fixtures.plane_evidence import PRISM_SHARE_NUMERIC_TOL, prism_plane_shares

G3D = Path(__file__).resolve().parents[1]
PRADA_GLB = G3D / "data" / "reference" / "prada" / "model.glb"


def test_prism_bulges_are_closed_form():
    tb = build_temple(FX.analytic_prism())
    vb, at = MS.vbulge(tb.V, tb.tris)
    assert vb == pytest.approx(2.0 - 0.2, abs=1e-9) and at == pytest.approx(20.0)     # peak 0.1 d at d 20, chord 0.2
    pb, y = MS.pbulge(tb.V, tb.tris)
    assert pb == pytest.approx(2.0 - 0.025 * 30 / 49.75, abs=1e-9) and y == pytest.approx(0.0)


def test_prism_single_plane_share_matches_dense_sampling():
    tb = build_temple(FX.analytic_prism())
    planes = report.facet_planes_chart(tb)
    got = MS.single_plane_share(tb.V, tb.tris, planes)
    reference = prism_plane_shares([("least_squares", np.array(got["lsq"]["normal"]), got["lsq"]["offset"])] + planes)
    for name, share in reference.items():
        assert got["per_plane"][name] == pytest.approx(share, abs=PRISM_SHARE_NUMERIC_TOL), name
    assert got["share"] <= 0.6                                 # a faceted surface: no plane holds most of it


def test_prism_facet_table_region_growing():
    tb = build_temple(FX.analytic_prism())
    facets = MS.facet_table(tb.V, tb.tris, (0.0, 50.0))
    big = [f for f in facets if f["area_mm2"] > 50]
    assert len(big) == 5                                       # N, E, S, W and the base behind d 40
    leans = sorted(round(f["lean_deg"], 2) for f in big)
    assert leans == pytest.approx(sorted([0.0, math.degrees(math.atan(0.1)), math.degrees(math.atan(0.1)),
                                          math.degrees(math.atan(0.2)), math.degrees(math.atan(0.2))]), abs=0.01)
    for f in big:
        assert f["planarity_mm"] <= 1e-9
        for nb in f["neighbours"]:
            assert nb["dihedral_deg"] > 5.0


def test_chamfered_slab_raises_slab_while_the_bulge_band_would_pass_it():
    tb = build_temple(FX.chamfered_slab_30())
    g = report.geometry_numbers(tb, photo_creases=4)
    assert any(f["flag"] == "SLAB" for f in g["flags"])
    assert g["single_plane_share"] >= 0.8
    assert 1.9 <= g["vbulge_mm"] <= 4.3                       # the PLAN's band reads it as relief (the trap)
    assert g["vbulge_mm"] == pytest.approx(2.2, abs=0.1)      # WP6 design: a 30 deg chamfered slab reads 2.23


def test_slab_and_facets_share():
    slab = report.geometry_numbers(build_temple(FX.slab()), photo_creases=4)
    assert slab["single_plane_share"] == pytest.approx(1.0) and slab["flags"][0]["flag"] == "SLAB"
    assert slab["vbulge_mm"] == pytest.approx(0.0, abs=1e-9)
    pr = report.geometry_numbers(build_temple(FX.prada_default()), photo_creases=4)
    assert pr["single_plane_share"] < 0.8 and not any(f["flag"] == "SLAB" for f in pr["flags"])
    assert pr["inner_flatness_mm"] <= 1e-9


def test_slab_flag_needs_the_photo_creases():
    g = report.geometry_numbers(build_temple(FX.slab()), photo_creases=1)
    assert not any(f["flag"] == "SLAB" for f in g["flags"])
    g = report.geometry_numbers(build_temple(FX.slab()), photo_creases=None)
    assert g["flags"][0]["note"] == "photo crease count not given"


def test_inner_not_flat_flag_on_a_bent_inner_face():
    s = FX.slab()
    tb = build_temple(s)
    V = tb.V.copy()
    n = tb.n2
    inner = np.arange(n, 2 * n)
    V[inner, 2] -= 0.002 * (V[inner, 0] - 25.0) ** 2 * (V[inner, 0] < 50)      # bow the inner face in the zone
    m = MS.measure_temple(V, tb.tris, zone_end=50.0)
    assert any(f["flag"] == "INNER_NOT_FLAT" for f in m["flags"])


def test_chart_mesh_keeps_normals_outward_on_both_sides():
    tb = build_temple(FX.pyramid())
    for side in (+1, -1):
        W, _, T = tb.world(side)
        V, F = MS.chart_mesh(W, T, side, tb.spec.hinge["z_mm"])
        assert np.allclose(V, tb.V, atol=1e-12)
        n, _ = MS.tri_normals(V, F)
        n0, _ = MS.tri_normals(tb.V, tb.tris)
        assert np.allclose(n, n0, atol=1e-12)


@pytest.mark.skipif(not PRADA_GLB.exists(), reason="Prada reference data not imported")
def test_delivered_prada_reference_numbers():
    from glasses3d.g3d_host import glb
    g = glb.load(PRADA_GLB)
    seen = {}
    for p in g.primitives:
        if "faceted sweep" in p.node_name:
            side = 1 if p.positions[:, 0].mean() > 0 else -1
            V, F = MS.chart_mesh(p.positions * 1000.0, p.triangles, side, -10.6)
            m = MS.measure_temple(V, F, zone_end=50.0)
            seen[side] = m
            assert m["vbulge_mm"] == pytest.approx(0.62, abs=0.03)        # §8 P1
            assert m["pbulge_mm"] == pytest.approx(1.34, abs=0.05)
            assert m["single_plane_share"] == pytest.approx(0.81, abs=0.03)
    assert set(seen) == {1, -1}
    assert seen[1]["single_plane_share"] == pytest.approx(seen[-1]["single_plane_share"], abs=1e-3)
