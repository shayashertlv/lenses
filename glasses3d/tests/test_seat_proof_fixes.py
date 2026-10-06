"""WP4 proof stage, review fix round 1 (host, numpy core): the self-intersection refusal (§10 P4 'Refusals:
... self-intersection'), the seam-only weld the groove's Boolean acceptance test now uses, and the nylor fixture's
cord seated in §5's groove frame. The Blender halves run in the proof (tests/proofs/wp4_proof.py, re-checked by
tests/test_seat_real.py)."""
import numpy as np
import pytest

from glasses3d.g3d.seat import deform, geom, rules, stations
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.g3d_host.seat_check import check_glb
from glasses3d.tests.fixtures import seat_fixtures as SF


def brute_self_intersections(V, T):
    """O(E x F) reference for geom.self_intersections (same closed segment-triangle test, no broad phase)."""
    E = geom.edge_table(T).edges
    out = []
    tol = geom.SELF_X_REL_TOL
    for e0 in range(0, len(E), 256):
        es = np.arange(e0, min(e0 + 256, len(E)))
        pe, pt = np.repeat(es, len(T)), np.tile(np.arange(len(T)), len(es))
        tri, ed = T[pt], E[pe]
        keep = ~((tri == ed[:, :1]) | (tri == ed[:, 1:])).any(1)
        pe, pt = pe[keep], pt[keep]
        a, b, c = V[T[pt, 0]], V[T[pt, 1]], V[T[pt, 2]]
        p, q = V[E[pe, 0]], V[E[pe, 1]]
        e1, e2, d = b - a, c - a, q - p
        pv = np.cross(d, e2)
        det = (e1 * pv).sum(1)
        scale = np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1) * np.linalg.norm(d, axis=1)
        ok = np.abs(det) > geom.SELF_X_PARALLEL * scale
        inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
        sv = p - a
        u = (sv * pv).sum(1) * inv
        qv = np.cross(sv, e1)
        v = (d * qv).sum(1) * inv
        t = (e2 * qv).sum(1) * inv
        hit = ok & (u >= -tol) & (v >= -tol) & (u + v <= 1 + tol) & (t >= -tol) & (t <= 1 + tol)
        out.append(np.stack([pe[hit], pt[hit]], 1))
    return np.unique(np.vstack(out), axis=0) if out else np.zeros((0, 2), np.int64)


def welded(m):
    Vw, vid = geom.weld_exact(m.V)
    return Vw, geom.drop_degenerate_index(vid[m.T])


# ------------------------------------------------------------------------------------------- self-intersection
def test_self_intersections_equal_the_brute_force_reference():
    for depth in (2.4, 3.0):
        V, T = welded(SF.self_intersecting(SF.base_lens(nseg=48, nring=5), depth=depth))
        got = geom.self_intersections(V, T)
        assert len(got) > 0 and np.array_equal(got, brute_self_intersections(V, T)), depth
    V, T = welded(SF.base_lens(nseg=48, nring=5))
    assert len(brute_self_intersections(V, T)) == 0 and len(geom.self_intersections(V, T)) == 0


def test_two_caps_on_one_grid_cross_edge_on_edge():
    """The fixture's caps share their (x, y) grid, so every crossing lies ON a triangle edge: a strict interior
    test finds none, which is why the test is closed (the first implementation missed the fixture this way)."""
    V, T = welded(SF.self_intersecting(SF.base_lens(nseg=48, nring=5)))
    E = geom.edge_table(T).edges
    hits = geom.self_intersections(V, T)
    a, b, c = (V[T[hits[:, 1], i]] for i in range(3))
    p, q = V[E[hits[:, 0], 0]], V[E[hits[:, 0], 1]]
    e1, e2, d = b - a, c - a, q - p
    pv = np.cross(d, e2)
    inv = 1 / (e1 * pv).sum(1)
    sv = p - a
    u = (sv * pv).sum(1) * inv
    v = (d * np.cross(sv, e1)).sum(1) * inv
    on_edge = (np.abs(u) < 1e-6) | (np.abs(v) < 1e-6) | (np.abs(u + v - 1) < 1e-6)
    assert on_edge.all()


@pytest.mark.parametrize("construction", ["sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded",
                                          "coarse"])
def test_clean_fixture_lenses_have_no_self_intersection(construction):
    L = SF.acetate_scene(construction)["lens"]
    rec = stations.recognise(L.V, L.T, "L")
    assert len(rec.bodies) == 1 and not rec.refused
    b = rec.bodies[0]
    assert len(geom.self_intersections(b.V, b.T)) == 0


def test_drilled_and_fused_lenses_are_not_refused():
    for m in (SF.drilled(), SF.fused(SF.base_lens(), SF.mirrored_lens())):
        rec = stations.recognise(m.V, m.T, "L")
        assert rec.bodies and not rec.refused


def test_seat_refuses_a_self_intersecting_lens_and_the_check_fails_it(tmp_path):
    L = SF.self_intersecting(SF.base_lens())
    parts = [("Rim", *(lambda r: (r.V, r.T))(SF.ring_frame(SF.base_lens(), 0.10)))]
    res = deform.seat_arrays(L.V, L.T, parts, rules.frame_type("acetate"), NumpyBackend, poly_edges_raw=L.poly_edges,
                             name="Lens")
    assert res.status == "refused" and res.code == "E_SELF_INTERSECTING", res.as_dict()
    assert "passes through itself" in res.message and not np.any(res.raw_delta)
    p = tmp_path / "selfx.glb"
    SF.write_glb(p, SF.scene_nodes({"lens": L, "parts": {"Rim": SF.ring_frame(SF.base_lens(), 0.10)}},
                                   seat_extras={"frame_type": "acetate"}))
    rep = check_glb(p)
    assert rep["ok"] is False and any("E_SELF_INTERSECTING" in r for r in rep["reasons"]), rep["reasons"]


def test_self_intersection_cache_is_exact_and_returns_copies():
    V, T = welded(SF.self_intersecting(SF.base_lens(nseg=48, nring=5)))
    a = geom.self_intersections_cached(V, T)
    a[:] = -1
    b = geom.self_intersections_cached(V, T)
    assert np.array_equal(b, geom.self_intersections(V, T))
    V2 = V.copy()
    V2[0, 0] = np.nextafter(V2[0, 0], np.inf)                 # one ulp: a different key, recomputed
    assert np.array_equal(geom.self_intersections_cached(V2, T), geom.self_intersections(V2, T))


def test_real_lenses_have_no_self_intersection():
    from glasses3d.g3d_host import seat_check as SC
    from glasses3d.g3d_host.glb import load
    from glasses3d.tests.instruments import seat_p0 as P0
    seen = 0
    for key, want in (("ft", 2), ("prada", 2), ("oakley", 1), ("invu", 1)):
        if not P0.available(key):
            continue
        nodes, _, _ = SC.read_nodes(load(P0.GLBS[key]))
        bodies = 0
        for n in nodes:
            if n["lens"]:
                rec = stations.recognise(n["V"], n["T"], n["name"])
                assert not rec.refused, (key, rec.refused)
                bodies += len(rec.bodies)
        assert bodies == want, (key, bodies)
        seen += bodies
    if not seen:
        pytest.skip("no real GLB on disk")


# ----------------------------------------------------------------------------------------------- seam-only weld
def tet(offset):
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float) + np.asarray(offset, float)
    T = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
    return V, T


def test_weld_seams_keeps_a_closed_mesh_with_coincident_vertices_closed():
    """Two closed tetrahedra touching along one edge (distinct vertices at identical positions, as a Boolean
    result can hold): closed by their own indices; an exact weld everywhere makes the shared edge non-manifold."""
    V1, T1 = tet((0, 0, 0))
    V2, T2 = tet((0, 0, 0))
    V2 = V2 * np.array([1, -1, -1.0])                        # rotated 180 deg about x: shares only the x-axis edge
    V = np.vstack([V1, V2])
    T = np.vstack([T1, T2 + 4])
    own = geom.topology(geom.weld_seams(V, T)[T], len(V))
    assert own["closed"] and own["nonmanifold_edges"] == 0 and own["components"] == 2
    ex = geom.topology(geom.weld_exact(V)[1][T])
    assert not ex["closed"] and ex["nonmanifold_edges"] == 1


def test_weld_seams_closes_split_seams():
    """A closed tetrahedron with one face's vertices duplicated (a split UV / normal seam): open by its own indices,
    closed after the seam weld."""
    V, T = tet((0, 0, 0))
    V = np.vstack([V, V[[0, 2, 1]]])
    T = T.copy()
    T[0] = [4, 5, 6]
    assert not geom.topology(T, len(V))["closed"]
    vid = geom.weld_seams(V, T)
    t = geom.topology(vid[T], len(V))
    assert t["closed"] and t["euler"] == 2


# ----------------------------------------------------------------------------------------------- nylor fixture
def test_station_cord_overlap_with_the_planned_groove_is_negligible():
    """The fixture cord in §5's groove frame lies inside the notch the re-wall plan cuts (12-gon flats 0.2898 mm
    from the centreline vs the flanks 0.30 x cos(tilt) away), where the horizontal stage-proof cord poked ~0.05 mm
    into a flank along the whole exposed arc (0.74 mm3 by Blender's Boolean)."""
    from glasses3d.g3d.seat import profile as PR
    sc = SF.nylor_scene()
    L, cord = sc["lens"], sc["parts"]["Cord"]
    P, Pb, N, U = SF.station_axes(L)
    idx = SF._arc_indices(~sc["top"])
    mid = 0.5 * (P + Pb)
    d = (Pb - P) / np.linalg.norm(Pb - P, axis=1, keepdims=True)
    def notch_coords(X):
        """(alpha along d, beta inward along -u) of points in the notch's (d, u) plane: X = mid + a d - b u."""
        rel = X - mid[idx][:, None]
        c = (U[idx] * d[idx]).sum(1)[:, None]
        al, du = (rel * d[idx][:, None]).sum(-1), (rel * U[idx][:, None]).sum(-1)
        return (al - c * du) / (1 - c * c), (c * al - du) / (1 - c * c)
    a, b = notch_coords(cord.V.reshape(-1, 12, 3)[1:-1])
    assert np.abs(a).max() <= PR.GROOVE_WIDTH_MM / 2 + 1e-6                      # inside both flanks
    # the notch's back runs along the rung (d), not perpendicular to u, so the 12-gon's corners next to it reach
    # 0.0047 mm past it (b < 0 is outside the lens wall, in the air): Blender's Boolean measures 0.0025 mm3 overall
    assert b.max() <= PR.GROOVE_DEPTH_MM + 0.005
    a, b = notch_coords(SF.nylor_scene(cord_frame="outline")["parts"]["Cord"].V.reshape(-1, 12, 3)[1:-1])
    assert np.abs(a).max() > PR.GROOVE_WIDTH_MM / 2 + 0.02                       # the old cord crossed a flank
