"""The deterministic WP4 fixture builder (tests/fixtures/seat_fixtures.py): WP4_DESIGN.md §10 P4."""
import numpy as np
import pytest

from glasses3d.g3d.seat import geom
from glasses3d.g3d_host.glb import load
from glasses3d.tests.fixtures import seat_fixtures as SF


def all_meshes():
    out = {}
    for c in ("sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded", "coarse"):
        sc = SF.acetate_scene(c)
        out[f"lens-{c}"] = sc["lens"]
        out[f"ring-{c}"] = sc["parts"]["Rim"]
    for name, sc in (("metal", SF.metal_scene()), ("metal-narrow", SF.metal_scene(0.5)), ("nylor", SF.nylor_scene()),
                     ("crystal", SF.crystal_scene())):
        out[f"lens-{name}"] = sc["lens"]
        for k, m in sc["parts"].items():
            out[f"{name}-{k}"] = m
    out["drilled"] = SF.drilled()
    return out


def test_builds_are_deterministic():
    a, b = all_meshes(), all_meshes()
    assert a.keys() == b.keys()
    for k in a:
        assert SF.digest(a[k]) == SF.digest(b[k]), k


@pytest.mark.parametrize("name", sorted(all_meshes()))
def test_every_fixture_is_a_closed_consistently_wound_solid(name):
    m = all_meshes()[name]
    t = geom.topology(m.T, len(m.V))
    assert t["closed"] and t["components"] == 1 and t["misoriented_edges"] == 0, t
    vol = geom.signed_volume(m.V, m.T)
    assert (vol < 0) == (name == "lens-flipped"), vol
    expected_chi = {"ring": 0, "metal-Eyewire": 0, "metal-narrow-Eyewire": 0, "crystal-Crystal rim": 0, "drilled": 0}
    key = "ring" if name.startswith("ring-") else name
    assert t["euler"] == expected_chi.get(key, 2), (name, t["euler"])


def test_lens_dimensions():
    L = SF.base_lens()
    P = L.V[:128]
    assert np.ptp(P[:, 0]) == pytest.approx(50.0, abs=1e-9) and np.ptp(P[:, 1]) == pytest.approx(38.0, abs=0.01)
    F, B = SF.rim_points(L)
    np.testing.assert_allclose(F - B, np.tile([0, 0, 1.6], (128, 1)), atol=1e-12)     # Studio construction
    apex = np.array(L.meta["apex"])
    centre = np.r_[apex, -120.0]
    np.testing.assert_allclose(np.linalg.norm(L.V[:L.meta["nfront"]] - centre, axis=1), 120.0, atol=1e-9)
    S = SF.acetate_scene("solidified")["lens"]
    F, B = SF.rim_points(S)
    np.testing.assert_allclose(np.linalg.norm(F - B, axis=1), 1.6, atol=1e-6)            # offset along the normal


def length_share(lens_mesh, mask):
    Q = lens_mesh.V[:lens_mesh.meta["nseg"], :2]
    seg = 0.5 * (np.linalg.norm(np.roll(Q, -1, 0) - Q, axis=1) + np.linalg.norm(Q - np.roll(Q, 1, 0), axis=1))
    return float(seg[mask].sum() / seg.sum())


def test_metal_capture_profile():
    sc = SF.metal_scene()
    cap = sc["capture"]
    assert (cap == 0.10).sum() == sc["top"].sum() and abs(length_share(sc["lens"], sc["top"]) - 0.40) < 0.015
    assert (cap == -0.30).sum() == sc["nasal"].sum() and abs(length_share(sc["lens"], sc["nasal"]) - 0.10) < 0.015
    assert ((cap == 0.45) == ~(sc["top"] | sc["nasal"])).all()
    top_y = sc["lens"].V[:128][sc["top"], 1]
    assert top_y.min() > 0 and sc["lens"].V[:128][sc["nasal"], 0].min() > -32.0       # nasal = toward x = 0


def test_ring_frame_aperture_is_the_offset_outline():
    sc = SF.acetate_scene("sphere", inset=0.10)
    R = sc["parts"]["Rim"]
    O, Rax, W, Q = SF.outline_frame_axes(sc["lens"], 4)
    inner = R.V[: len(Q), :2]
    np.testing.assert_allclose(np.linalg.norm(inner - Q, axis=1), 0.10, atol=1e-12)
    z0, z1 = R.meta["z"]
    F, B = SF.rim_points(sc["lens"])
    assert z1 - F[:, 2].max() == pytest.approx(1.6, abs=1e-3) and B[:, 2].min() - z0 == pytest.approx(1.9, abs=1e-3)


def test_nylor_cord_and_channel():
    sc = SF.nylor_scene(cord_frame="outline")
    assert sc["lens"].meta["t"] == 2.0 and abs(length_share(sc["lens"], sc["top"]) - 0.45) < 0.015
    cord = sc["parts"]["Cord"]
    # the cord's section stays within 0.3 mm of its centreline and never reaches the lens wall (r < 0)
    O, R, W, Q = SF.outline_frame_axes(sc["lens"], 1)
    idx = SF._arc_indices(~sc["top"])
    mid = cord.V.reshape(-1, 12, 3)[1:-1].mean(1)                                   # interior sections
    assert len(mid) == len(idx)
    r = ((cord.V.reshape(-1, 12, 3)[1:-1] - O[idx][:, None]) * R[idx][:, None]).sum(-1)
    assert r.max() < 0 and r.min() > -0.61
    assert SF.nylor_scene(t=1.4)["lens"].meta["t"] == 1.4


def test_nylor_cord_sits_where_the_groove_is_cut():
    """WP4 proof review: the default cord is seated in §5's groove frame: centred 0.30 mm inside the mid-depth edge
    point along the lens surface's u (tilted ~9 deg from the horizontal on the R120 cap), its 12-gon in the (u, n)
    plane with flats facing +-n. The fixture's own §3 frame agrees with the core's to < 1e-5 deg."""
    from glasses3d.g3d.seat import stations
    sc = SF.nylor_scene()
    L, cord = sc["lens"], sc["parts"]["Cord"]
    assert cord.meta["frame"] == "station"
    P, Pb, N, U = SF.station_axes(L)
    tilt = np.degrees(np.arcsin(np.abs(U[:, 2])))
    assert 7.0 < np.median(tilt) < 11.0
    fr = stations.station_frames(stations.recognise(L.V, L.T, "L").bodies[0])
    j = [int(np.argmin(np.linalg.norm(fr.P - p, axis=1))) for p in P]
    assert np.abs(fr.P[j] - P).max() == 0.0
    assert np.degrees(np.arccos(np.clip((U * fr.u[j]).sum(1), -1, 1))).max() < 1e-5
    idx = SF._arc_indices(~sc["top"])
    sec = cord.V.reshape(-1, 12, 3)[1:-1]                                         # interior sections
    centre = 0.5 * (P + Pb)[idx] - 0.3 * U[idx]
    assert np.abs(sec.mean(1) - centre).max() < 1e-9
    r = np.linalg.norm(sec - centre[:, None], axis=2)
    assert np.allclose(r, 0.3, atol=1e-9)
    hn = ((sec - centre[:, None]) * N[idx][:, None]).sum(-1)                     # flats face +-n
    assert np.abs(hn).max() == pytest.approx(0.3 * np.cos(np.radians(15)), abs=1e-3)


def test_write_glb_round_trip(tmp_path):
    sc = SF.crystal_scene()
    p = tmp_path / "c.glb"
    SF.write_glb(p, SF.scene_nodes(sc, seat_extras={"frame_type": "acetate"}))
    g = load(p)
    names = [pr.node_name for pr in g.primitives]
    assert names == ["Lens_L", "Crystal rim"]
    lens = g.primitives[0]
    np.testing.assert_allclose(lens.positions * 1000.0, sc["lens"].V, atol=2e-4)       # float32 metres
    assert g.nodes[0]["extras"] == {"g3d_seat": {"frame_type": "acetate"}, "partRole": "lens"}
    assert g.material(g.primitives[1].material_index)["extensions"]["KHR_materials_transmission"][
        "transmissionFactor"] == pytest.approx(0.9)
    assert lens.normals is not None and np.allclose(np.linalg.norm(lens.normals, axis=1), 1.0, atol=1e-6)
