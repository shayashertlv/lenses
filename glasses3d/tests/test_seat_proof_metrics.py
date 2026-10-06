"""The WP4 proof instrument additions (tests/instruments/seat_proof_metrics.py) on synthetic fixtures with known
answers: topology, components, the station rows and corner cover, rim split from normals, groove / V-bevel profile
geometry, the rebuilt groove cutter, arcs. Fast, host only."""
import numpy as np
import pytest

from glasses3d.tests.fixtures import seat_fixtures as SF
from glasses3d.tests.instruments import seat_grid as IG
from glasses3d.tests.instruments import seat_proof_metrics as M


def test_topology_and_components_of_fixtures():
    L = SF.base_lens()
    t = M.topology(L.V, L.T)
    assert t["closed"] and t["euler"] == 2 and t["components"] == 1
    ring = SF.ring_frame(L, 0.10)
    assert M.topology(ring.V, ring.T)["euler"] == 0
    op = SF.open_lens(L)
    t = M.topology(op.V, op.T)
    assert not t["closed"] and t["boundary_edges"] > 0
    F = SF.fused_with_edges(L, SF.mirrored_lens())
    comps = M.components(F.V, F.T)
    assert len(comps) == 2 and all(c["closed"] for c in comps)
    assert all(abs(c["thick"] - 1.5) < 0.2 for c in comps)
    assert M.signed_volume(L.V, L.T) == pytest.approx(M.components(L.V, L.T)[0]["volume"])


def test_rows_measure_a_flat_lens_in_an_acetate_ring():
    """A flat lens (u horizontal) in a ring whose vertical aperture is 0.30 mm inside the edge: capture 0.30 at every
    depth; the same frames on a ring 0.50 mm larger than the lens read a 0.20 mm gap (never 'unchanged' by accident)."""
    L = SF.base_lens(cap="flat")
    F = M.frames(L.V, L.T, 1.0)
    R = M.rows(F, F["scene"], [IG.build_scene(*(lambda m: (m.V, m.T))(SF.ring_frame(L, 0.30)))], (0.05, 0.5, 0.95))
    assert np.allclose(R["cap"], 0.30, atol=2e-3), np.nanpercentile(R["cap"], [0, 50, 100])
    R2 = M.rows(F, F["scene"], [IG.build_scene(*(lambda m: (m.V, m.T))(SF.ring_frame(L, -0.20)))], (0.5,))
    assert np.allclose(R2["cap"], -0.20, atol=2e-3)
    assert (F["u"][:, 2] ** 2).max() < 1e-12                       # u is horizontal on a flat cap


def test_corner_cover_inside_and_absent():
    L = SF.base_lens(cap="flat")
    F = M.frames(L.V, L.T, 1.0)
    inside = M.corner_cover(F, F["scene"], [IG.build_scene(*(lambda m: (m.V, m.T))(SF.ring_frame(L, 0.30)))])
    assert np.all(inside["front"] <= 0) and np.all(inside["back"] <= 0)
    wide = M.corner_cover(F, F["scene"], [IG.build_scene(*(lambda m: (m.V, m.T))(SF.ring_frame(L, -0.50)))])
    assert np.isnan(wide["front"]).all() and np.isnan(wide["back"]).all()


def _flat_shaded(V, T):
    """Every triangle its own three vertices with the face normal (a fully split mesh)."""
    V2 = V[T].reshape(-1, 3)
    T2 = np.arange(len(V2)).reshape(-1, 3)
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    fn /= np.linalg.norm(fn, axis=1, keepdims=True)
    return V2, T2, np.repeat(fn, 3, axis=0)


def test_rim_split_and_wall_flatness_from_normals():
    L = SF.base_lens()
    V2, T2, N2 = _flat_shaded(L.V, L.T)
    split = M.rim_split(V2, T2, N2)
    assert 70 < split["front"][0] and split["back"][2] < 110          # the cap / wall dihedral (~80 / ~100 deg)
    assert M.wall_flatness_deg(V2, T2, N2) < 1e-4
    vn = np.zeros_like(L.V)
    fn = np.cross(L.V[L.T[:, 1]] - L.V[L.T[:, 0]], L.V[L.T[:, 2]] - L.V[L.T[:, 0]])
    for k in range(3):
        np.add.at(vn, L.T[:, k], fn)
    vn /= np.linalg.norm(vn, axis=1, keepdims=True)
    smooth = M.rim_split(L.V, L.T, vn)
    assert smooth["both"][2] < 1e-6                                     # shared normals: no split
    assert M.wall_flatness_deg(L.V, L.T, vn) > 30


def test_groove_and_bevel_geometry_from_synthetic_profiles():
    v = np.arange(-0.95, 0.95 + 1e-9, 0.005)
    rung = 0.10 - 0.05 * v                                              # a slanted corner-to-corner line
    e = np.where(np.abs(v - 0.02) < 0.30, rung - 0.60, rung)
    d = M.groove_dims({"v": v, "e": e, "rung": rung})
    slant = np.sqrt(1 + 0.05 ** 2)
    assert d["width_mm"] == pytest.approx(0.60 * slant, abs=0.006)
    assert d["depth_mm"] == pytest.approx(0.60, abs=1e-9)
    assert d["centre_offset_mm"] == pytest.approx(0.02 * slant, abs=0.003)
    h, half = 0.404, 0.70
    e = rung + np.clip(h * (1 - np.abs(v) / half), 0, None)
    b = M.bevel_dims({"v": v, "e": e, "rung": rung})
    assert b["h_mm"] == pytest.approx(h, abs=0.003)
    assert b["angle_deg"] == pytest.approx(2 * np.degrees(np.arctan(half / h)), abs=0.5)


def _uv_sphere(r=10.0, nu=96, nv=48):
    th = np.linspace(0, np.pi, nv + 1)[1:-1]
    ph = 2 * np.pi * np.arange(nu) / nu
    V = [[0, 0, r]] + [[r * np.sin(a) * np.cos(b), r * np.sin(a) * np.sin(b), r * np.cos(a)] for a in th for b in ph]
    V.append([0, 0, -r])
    V = np.array(V)
    T = []
    for j in range(nu):
        T.append((0, 1 + j, 1 + (j + 1) % nu))
    for i in range(nv - 2):
        for j in range(nu):
            a, b = 1 + i * nu + j, 1 + i * nu + (j + 1) % nu
            T += [(a, a + nu, b), (b, a + nu, b + nu)]
    last = len(V) - 1
    base = 1 + (nv - 2) * nu
    for j in range(nu):
        T.append((last, base + (j + 1) % nu, base + j))
    return V, np.array(T)


def test_inflate_is_the_section_5_cutter():
    """The rebuilt cutter equals the tool's own inflation (g3d/seat/profile.inflate, imported here only to compare;
    the instrument does not use it), and a smooth closed surface grows by area x clearance."""
    from glasses3d.g3d.seat import profile as PR
    L = SF.base_lens()
    V, T = M.inflate(L.V, L.T, 0.03)
    Vw, vid = M.weld_exact(L.V)
    assert np.allclose(V[vid], PR.inflate(L.V, L.T, 0.03), atol=1e-9)
    assert M.topology(V, T)["closed"]
    S, ST = _uv_sphere()
    Si, STi = M.inflate(S, ST, 0.03)
    assert M.signed_volume(Si, STi) / M.signed_volume(S, ST) == pytest.approx((10.03 / 10.0) ** 3, rel=2e-4)


def test_arc_extent_and_overlaps():
    th = 2 * np.pi * np.arange(400) / 400
    loop = np.c_[10 * np.cos(th), 10 * np.sin(th), np.zeros(400)]
    L = float(np.linalg.norm(np.roll(loop, -1, 0) - loop, axis=1).sum())
    part = loop[(th > 0.5) & (th < 2.0)] * 1.02
    st, en, ln, LL = M.arc_extent(loop, part)
    assert LL == pytest.approx(L) and ln == pytest.approx(1.5 * 10, rel=0.02)
    other = loop[(th > 1.8) | (th < 0.7)]
    ov = M.arc_overlaps((st, en, ln, LL), M.arc_extent(loop, other))
    assert len(ov) == 2 and all(abs(x - 10 * 0.2) < 0.25 for x in ov)
