"""WP4 core geometry (glasses3d/g3d/seat/geom.py, stations.recognise): WP4_DESIGN.md §3."""
import numpy as np
import pytest

from glasses3d.g3d.seat import geom, stations
from glasses3d.g3d.seat.geom import SeatError
from glasses3d.tests.fixtures import seat_fixtures as SF

TET_V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
TET_T = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])


def box(x0, x1, y0, y1, z0, z1):
    V = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    T = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]])
    return V, T


# ------------------------------------------------------------------------------------------------- weld/topology
def test_weld_merges_bit_identical_positions_only():
    V = np.array([[0.0, 0, 0], [1, 0, 0], [0.0, 0, 0], [1 + 1e-12, 0, 0], [-0.0, 0, 0]])
    Vw, vid = geom.weld_exact(V)
    assert len(Vw) == 3                                   # 0/2/4 merge (-0.0 == 0.0); 1e-12 apart stays separate
    assert vid[0] == vid[2] == vid[4] and vid[1] != vid[3]
    np.testing.assert_array_equal(Vw[vid], np.where(V == 0, 0.0, V))


def test_topology_of_closed_open_nonmanifold_and_misoriented_meshes():
    t = geom.topology(TET_T)
    assert t["closed"] and t["euler"] == 2 and t["components"] == 1 and t["misoriented_edges"] == 0
    o = geom.topology(TET_T[1:])
    assert not o["closed"] and o["boundary_edges"] == 3
    nm = geom.topology(np.vstack([TET_T, [[0, 1, 4]]]), 5)
    assert nm["nonmanifold_edges"] == 1 and not nm["closed"]
    flipped = TET_T.copy()
    flipped[0] = flipped[0, ::-1]
    assert geom.topology(flipped)["misoriented_edges"] == 3


def test_label_components_and_components():
    lab = geom.label_components(6, [0, 1, 4], [1, 2, 5])
    assert lab.tolist() == [0, 0, 0, 3, 4, 4]
    two = SF.fused(SF.base_lens(nseg=32), SF.lens(SF.ellipse(25, 19, 32, cx=32.0), apex=(32.0, 0.0)))
    vlab, flab = geom.components(two.T, len(two.V))
    assert len(np.unique(flab)) == 2


def test_volume_area_and_effective_thickness():
    V, T = box(0, 10, 0, 10, 0, 1)
    assert geom.signed_volume(V, T) == pytest.approx(100.0)
    assert geom.surface_area(V, T) == pytest.approx(240.0)
    assert geom.effective_thickness(V, T) == pytest.approx(200.0 / 240.0)
    assert geom.signed_volume(V, T[:, ::-1]) == pytest.approx(-100.0)


def test_local_surface_normals_follow_a_plate():
    L = SF.base_lens(cap="flat")
    fn, fa, fc = geom.face_geometry(L.V, L.T)
    M = geom.local_surface_normals(fc, fa)
    caps = np.abs(fn[:, 2]) > 0.99
    assert np.abs(M[caps, 2]).min() > 0.99               # the least-spread direction of a plate is its normal


# ------------------------------------------------------------------------------------------------ cap split
CONSTRUCTIONS = ("sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded", "coarse")


@pytest.mark.parametrize("construction", CONSTRUCTIONS)
def test_split_caps_on_every_construction(construction):
    L = SF.acetate_scene(construction)["lens"]
    rec = stations.recognise(L.V, L.T, construction)
    assert len(rec.bodies) == 1 and not rec.riders and not rec.refused
    b = rec.bodies[0]
    nseg, nring = L.meta["nseg"], L.meta["nring"]
    expected = 2 * (nring - 1) * nseg + nseg                 # quad rings + the centre fan
    nf, nb = b.cap_triangles()
    if construction == "rounded":                            # the first bullnose facets lie within 75 deg: cap
        assert nf == nb >= expected
    else:
        assert nf == nb == expected
    assert (b.cls == 2).sum() > 0 and b.inner_loops == 0 and b.topo["euler"] == 2
    assert b.flipped == (construction == "flipped")
    P = b.V[b.fl]
    assert P[:, 2].mean() > b.V[b.bl][:, 2].mean()            # the front loop is the +Z one


def test_split_caps_refuses_a_ball():
    # a UV sphere: rounded everywhere, no two opposite caps holding 75% of the area
    th, ph = np.meshgrid(np.linspace(0, np.pi, 17)[1:-1], np.linspace(0, 2 * np.pi, 32, endpoint=False), indexing="ij")
    V = np.vstack([np.c_[(10 * np.sin(th) * np.cos(ph)).ravel(), (10 * np.sin(th) * np.sin(ph)).ravel(),
                         (10 * np.cos(th)).ravel()], [[0, 0, 10], [0, 0, -10]]])
    n_r, n_c = th.shape
    T = []
    for i in range(n_r - 1):
        for j in range(n_c):
            a, b_, c, d = i * n_c + j, i * n_c + (j + 1) % n_c, (i + 1) * n_c + (j + 1) % n_c, (i + 1) * n_c + j
            T += [(a, d, c), (a, c, b_)]
    top, bot = len(V) - 2, len(V) - 1
    for j in range(n_c):
        T.append((top, j, (j + 1) % n_c))
        T.append((bot, (n_r - 1) * n_c + (j + 1) % n_c, (n_r - 1) * n_c + j))
    T = np.array(T)
    if geom.signed_volume(V, T) < 0:
        T = T[:, ::-1]
    with pytest.raises(SeatError) as e:
        geom.split_caps(V, T)
    assert e.value.code == "E_NO_CAPS"


def test_sliver_faces_join_their_cap():
    # q001's central 128-gon fan is ~1e-11 mm2 slivers: they must not become 'wall' islands (fake drilled holes)
    L = SF.base_lens(nseg=64)
    V = L.V.copy()
    nf = L.meta["nfront"]
    c = nf - 1                                            # the front centre vertex of the fan
    ring = np.arange((L.meta["nring"] - 1) * 64, L.meta["nring"] * 64)
    V[ring] = V[c] + (V[ring] - V[c]) * 1e-6              # shrink the innermost ring onto the centre
    b = stations.recognise(V, L.T, "sliver").bodies[0]
    assert b.inner_loops == 0 and len(b.back_loops) == 1


def test_rim_loop_perimeter_and_drilled_hole():
    L = SF.base_lens()
    b = stations.recognise(L.V, L.T).bodies[0]
    P = L.V[:128]
    assert b.perimeter_mm == pytest.approx(np.linalg.norm(np.roll(P, -1, 0) - P, axis=1).sum(), abs=1e-9)
    d = stations.recognise(SF.drilled().V, SF.drilled().T).bodies[0]
    assert d.topo["euler"] == 0 and d.inner_loops == 1 and len(d.back_loops) == 2
    assert geom.loop_perimeter(d.V, d.front_loops[1]) < 0.1 * d.perimeter_mm


def test_caps_touching_directly_is_E_NO_WALL():
    # a biconvex lens whose back cap shares the rim ring with the front cap (no wall)
    L = SF.base_lens(nseg=48)
    n, nring, nf = 48, L.meta["nring"], L.meta["nfront"]
    Vf = L.V[:nf]
    Vb = Vf[n:].copy()
    Vb[:, 2] = 2 * Vf[:n, 2].mean() - Vb[:, 2] - 0.5
    V = np.vstack([Vf, Vb])
    Ff = L.T[: 2 * (nring - 1) * n + n]                          # the front cap faces
    Fb = np.where(Ff < n, Ff, nf + Ff - n)[:, ::-1]               # mirrored: ring 0 shared, the rest new
    T = np.vstack([Ff, Fb])
    assert geom.topology(T, len(V))["closed"]
    split = geom.split_caps(V, T)
    with pytest.raises(SeatError) as e:
        geom.rim_loops(V, T, split.cls)
    assert e.value.code == "E_NO_WALL"


def test_chain_loops_refuses_a_pinched_rim():
    with pytest.raises(SeatError) as e:
        geom.chain_loops([(0, 1), (1, 2), (2, 0), (0, 3), (3, 4), (4, 0)])
    assert e.value.code == "E_RIM_LOOP"
    loops = geom.chain_loops([(0, 1), (1, 2), (2, 0), (5, 6), (6, 7), (7, 5)])
    assert sorted(len(lp) for lp in loops) == [3, 3]


def test_rungs_use_polygon_edges_not_diagonals():
    L = SF.base_lens(nseg=64)
    n, nf = 64, L.meta["nfront"]
    fl, bl = np.arange(n), nf + np.arange(n)
    assert (geom.rungs(L.V, fl, bl, L.poly_edges) == bl).all()
    # shear the back loop sideways so each triangulation diagonal is shorter than the rung
    V = L.V.copy()
    V[bl] -= 0.9 * (V[bl] - np.roll(V[bl], 1, 0))                    # back i+1 now sits nearly under front i
    tri_edges = np.unique(np.sort(np.concatenate([L.T[:, [0, 1]], L.T[:, [1, 2]], L.T[:, [2, 0]]]), 1), axis=0)
    assert (geom.rungs(V, fl, bl, L.poly_edges) == bl).all()           # polygon edges: still the rung
    assert (geom.rungs(V, fl, bl, tri_edges) != bl).any()              # diagonals would steal it


def test_quadric_fit_is_exact_on_a_quadric():
    x, y = np.meshgrid(np.linspace(-3, 3, 7), np.linspace(-3, 3, 7))
    coef = np.array([0.01, 0.002, -0.005, 0.1, 0.05])
    z = geom.quadric_height(coef, x, y)
    P = np.c_[x.ravel(), y.ravel(), z.ravel()]
    got = geom.fit_quadric(P, np.zeros(3), np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0]))
    np.testing.assert_allclose(got, coef, atol=1e-12)


def test_nearest_on_polyline():
    sq = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
    s, d, k = geom.nearest_on_polyline(sq, np.array([[5, -2, 0], [12, 5, 0], [5, 5, 0.0]]))
    np.testing.assert_allclose(s[:2], [5, 15])
    np.testing.assert_allclose(d, [2, 2, 5])
    assert k[:2].tolist() == [0, 1]


# ---------------------------------------------------------------------------------------------- recognition
def test_recognise_splits_bodies_riders_and_open_lenses():
    L = SF.base_lens(nseg=64)
    sheet = SF.Mesh(np.array([[-40, 0, 0.1], [-38, 0, 0.1], [-38, 2, 0.1]], float), np.array([[0, 1, 2]]))
    m = SF.fused(L, sheet)
    rec = stations.recognise(m.V, m.T, "lens")
    assert len(rec.bodies) == 1 and len(rec.riders) == 1 and not rec.riders[0].closed
    o = SF.open_lens(L)
    rec = stations.recognise(o.V, o.T, "open")
    assert not rec.bodies and len(rec.riders) == 1
    two = SF.fused(SF.base_lens(nseg=32), SF.lens(SF.ellipse(25, 19, 32, cx=32.0), apex=(32.0, 0.0)))
    rec = stations.recognise(two.V, two.T, "pair")
    assert len(rec.bodies) == 2                                        # fused components analysed separately


def test_thin_closed_component_is_a_rider():
    V, T = box(0, 10, 0, 10, 0, 0.1)                                   # 2V/A = 0.083 < 0.3
    rec = stations.recognise(V, T, "etch")
    assert not rec.bodies and len(rec.riders) == 1 and rec.riders[0].closed


def test_inconsistent_winding_is_refused():
    L = SF.base_lens(nseg=32)
    T = L.T.copy()
    T[5] = T[5, ::-1]
    rec = stations.recognise(L.V, T, "bad")
    assert not rec.bodies and rec.refused and rec.refused[0]["code"] == "E_WINDING"


def test_station_frames_spacing_nodes_and_orientation():
    L = SF.base_lens()
    b = stations.recognise(L.V, L.T).bodies[0]
    fr = stations.station_frames(b)
    seg = np.linalg.norm(np.diff(np.vstack([fr.P, fr.P[:1]]), axis=0), axis=1)
    assert seg.max() <= stations.POINT_SPACING_MM + 1e-9
    assert set(fr.node.tolist()) == set(range(128))                     # every node owns points
    centre = L.V[:128].mean(0)
    assert ((fr.P - centre)[:, :2] * fr.u[:, :2]).sum(1).min() > 0      # u points outward everywhere
    assert fr.n[:, 2].min() > 0.9 and np.abs((fr.u * fr.n).sum(1)).max() < 1e-9
    assert fr.ok.all()
    # translate construction: the rung is t along -Z; the station plane holds n, not Z, so its cut of the back
    # loop sits a little off the vertical partner
    np.testing.assert_allclose(fr.edge_mm, 1.6, atol=0.005)
