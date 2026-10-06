"""WP4 line-interval backends and the host check (g3d/seat/intervals.py, g3d_host/seat_check.py): WP4_DESIGN.md §3,
§8, §10 P1 (host half: numpy vs open3d within 1e-3 mm on every fixture; two runs bit-identical)."""
import json

import numpy as np
import pytest

from glasses3d.g3d.seat import intervals as IV
from glasses3d.g3d.seat import stations
from glasses3d.g3d.seat.intervals import NumpyBackend, line_intervals, merge
from glasses3d.g3d_host import seat_check as SC
from glasses3d.g3d_host.seat_check import Open3DBackend, check_glb
from glasses3d.tests.fixtures import seat_fixtures as SF

CUBE_V = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
CUBE_T = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                   [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])


# ------------------------------------------------------------------------------------------------ primitives
@pytest.mark.parametrize("make", [NumpyBackend, Open3DBackend])
def test_cube_intervals_whole_line(make):
    be = make([(CUBE_V, CUBE_T), (CUBE_V + [3, 0, 0], CUBE_T)])
    O = np.array([[0.3, 0.4, 0.5], [10.0, 0.4, 0.5], [0.3, 5.0, 0.5]])
    D = np.array([[1.0, 0, 0], [1.0, 0, 0], [1.0, 0, 0]])
    res = line_intervals(be, O, D)
    np.testing.assert_allclose(res.intervals[0][0], [[-0.3, 0.7]], atol=1e-5)    # behind the origin counts
    np.testing.assert_allclose(res.intervals[0][1], [[2.7, 3.7]], atol=1e-5)
    np.testing.assert_allclose(res.intervals[1][0], [[-10.0, -9.0]], atol=1e-4)   # origin far outside the box
    assert len(res.intervals[2][0]) == 0 and (res.recast == 0).all()


def test_shared_edge_crossing_is_one_crossing():
    # through the diagonal edge of the bottom and top faces: an exact float64 test reports both triangles
    be = NumpyBackend([(CUBE_V, CUBE_T)])
    li, pi, t = be.hits(np.array([[0.5, 0.5, -3.0]]), np.array([[0.0, 0.0, 1.0]]))
    assert len(t) >= 2
    res = line_intervals(be, np.array([[0.5, 0.5, -3.0]]), np.array([[0.0, 0.0, 1.0]]))
    np.testing.assert_allclose(res.intervals[0][0], [[3.0, 4.0]], atol=1e-9)
    assert res.recast[0] == 0


class OddFirst:
    """Reports one crossing on the original line, two on any re-cast."""
    n_parts = 1

    def __init__(self, always=False):
        self.calls = 0
        self.always = always

    def hits(self, O, D):
        self.calls += 1
        n = len(O)
        if self.calls == 1 or self.always:
            return np.arange(n), np.zeros(n, np.int64), np.full(n, 1.0)
        return np.repeat(np.arange(n), 2), np.zeros(2 * n, np.int64), np.tile([1.0, 2.0], n)


def test_odd_parity_recasts_then_unmeasured():
    res = line_intervals(OddFirst(), np.zeros((2, 3)), np.tile([1.0, 0, 0], (2, 1)), np.tile([0, 0, 1.0], (2, 1)))
    assert res.recast.tolist() == [1, 1]
    np.testing.assert_allclose(res.intervals[0][0], [[1.0, 2.0]])
    ang = np.degrees(np.arccos(res.dirs[0] @ [1.0, 0, 0]))
    assert ang == pytest.approx(IV.RECAST_DEG, abs=1e-9)
    be = OddFirst(always=True)
    res = line_intervals(be, np.zeros((1, 3)), [[1.0, 0, 0]], [[0, 0, 1.0]])
    assert res.unmeasured(0) and res.recast[0] == IV.RECAST_MAX + 1 and be.calls == 1 + IV.RECAST_MAX


def test_merge():
    a = np.array([[0.0, 1.0], [3.0, 4.0]])
    b = np.array([[1.00005, 2.0], [5.0, 6.0]])
    np.testing.assert_allclose(merge([a, None, b]), [[0, 2], [3, 4], [5, 6]])     # 5e-5 apart: touching parts
    assert merge([None, np.zeros((0, 2))]).shape == (0, 2)


def test_capture_along_and_lens_edge():
    per = [np.array([[-0.4, 0.4]]), np.array([[0.3, 3.65]])]                    # eyewire, brow (FT top)
    c, r, o = stations.capture_along(per)
    assert (c, r, o) == (pytest.approx(0.4), pytest.approx(3.65), 0)
    c, r, o = stations.capture_along([np.array([[0.45, 2.0]])])                  # a 0.45 gap
    assert c == pytest.approx(-0.45) and o == 0
    assert np.isnan(stations.capture_along([np.array([[3.5, 5.0]])])[0])         # beyond REACH
    assert np.isnan(stations.capture_along([np.array([[-3.0, -1.0]])])[0])       # wholly inward: not capture
    c, r, o = stations.capture_along([np.array([[0.2, 1.0]])], edge=0.3)         # lens bulges 0.3 past the rung
    assert c == pytest.approx(0.1) and r == pytest.approx(0.7)
    assert stations.lens_edge(np.array([[-50.0, 0.3]])) == pytest.approx(0.3)
    assert stations.lens_edge(np.array([[-50.0, -0.6]])) == pytest.approx(-0.6)  # a groove
    assert stations.lens_edge(None) == 0.0


# ------------------------------------------------------------------------------------------------- fixtures
def scenes():
    out = {f"acetate-{c}": SF.acetate_scene(c) for c in
           ("sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded", "coarse")}
    out.update({"metal": SF.metal_scene(), "metal-narrow": SF.metal_scene(0.5), "nylor": SF.nylor_scene(),
                "nylor-nocord": SF.nylor_scene(False), "crystal": SF.crystal_scene()})
    return out


def measure_scene(sc, kind):
    b = stations.recognise(sc["lens"].V, sc["lens"].T).bodies[0]
    parts = [(p.V, p.T) for p in sc["parts"].values()] + [(b.V, b.T)]
    be = SC.make_backend(kind, parts)
    fr, m = stations.measure_body(b, be, len(parts) - 1)
    return b, fr, m, be


@pytest.mark.parametrize("name", sorted(scenes()))
def test_numpy_and_open3d_agree_on_every_fixture(name):
    sc = scenes()[name]
    _, fr, a, be_a = measure_scene(sc, "numpy")
    _, _, b, be_b = measure_scene(sc, "open3d")
    for key in ("cap", "room", "cover_front", "cover_back", "edge"):
        x, y = getattr(a, key), getattr(b, key)
        assert (np.isfinite(x) == np.isfinite(y)).all(), key
        assert np.nanmax(np.abs(np.where(np.isfinite(x), x - y, 0))) <= 1e-3, key
    assert (a.cls == b.cls).all() and (a.owner == b.owner).all()
    assert not a.unmeasured.any() and not b.unmeasured.any()
    ba = stations.buried_band(fr, a, be_a, be_a.n_parts - 1)
    bb = stations.buried_band(fr, b, be_b, be_b.n_parts - 1)
    assert (ba.candidate == bb.candidate).all()
    np.testing.assert_allclose(np.nan_to_num(ba.front_mm), np.nan_to_num(bb.front_mm), atol=1e-9)


@pytest.mark.parametrize("construction", ("sphere", "toric", "flat", "flipped", "coarse"))
def test_acetate_ring_capture_is_the_inset_along_u(construction):
    sc = SF.acetate_scene(construction, inset=0.10)
    b, fr, m, _ = measure_scene(sc, "numpy")
    O, R, W, Q = SF.outline_frame_axes(sc["lens"], 1)
    k = np.argmin(np.linalg.norm(fr.P[:, None, :2] - Q[None], axis=-1), axis=1)
    want = SF.expected_capture(sc["lens"], 0.10, fr.u, R[k])
    assert np.abs(m.cap - want[:, None]).max() < 0.012          # chord sag of the 128-gon <= 0.01 mm
    assert (m.cls == "ENCLOSED").all()
    np.testing.assert_allclose(m.edge, 0.0, atol=1e-6)         # flat wall: the lens edge is the rung


def test_chamfer_and_bullnose_capture_measures_from_the_lens_surface():
    # the ring's 0.10 inset follows the front rim vertices: the chamfer pulled them 0.3 mm inside the outer wall,
    # the bullnose wall bulges 0.8 mm beyond them; capture is measured from the lens surface, not the rung
    for construction, bulge in (("chamfer", 0.30), ("rounded", 0.80)):
        b, fr, m, _ = measure_scene(SF.acetate_scene(construction), "numpy")
        assert m.edge[:, stations.MID].min() > 0.2                               # beyond the corner-to-corner rung
        assert np.median(m.cap[:, stations.MID]) == pytest.approx(0.10 + bulge, abs=0.02)


def test_metal_fixture_profile_and_classes():
    sc = SF.metal_scene()
    b, fr, m, _ = measure_scene(sc, "numpy")
    O, R, W, Q = SF.outline_frame_axes(sc["lens"], 1)
    k = np.argmin(np.linalg.norm(fr.P[:, None, :2] - Q[None], axis=-1), axis=1)
    cap = sc["capture"]
    inner = (cap[k] == np.roll(cap, 1)[k]) & (cap[k] == np.roll(cap, -1)[k])   # away from the profile steps
    want = SF.expected_capture(sc["lens"], 1.0, fr.u, R[k]) * cap[k]
    assert np.abs(m.cap[inner, stations.MID] - want[inner]).max() < 0.015
    gap = inner & (cap[k] < 0)
    assert gap.sum() > 10 and (m.cls[gap] == "LIPPED").all() and m.covered_front[gap].all()
    assert (m.cls[inner & (cap[k] > 0)] == "ENCLOSED").all()


# ------------------------------------------------------------------------------------------------- check_glb
def glb(tmp_path, sc, name="s.glb", **kw):
    p = tmp_path / name
    SF.write_glb(p, SF.scene_nodes(sc, **kw))
    return p


def test_check_glb_metal_with_seat_extras(tmp_path):
    rep = check_glb(glb(tmp_path, SF.metal_scene(), seat_extras={"frame_type": "metal"}))
    assert "error" not in rep and len(rep["lenses"]) == 1
    L = rep["lenses"][0]
    assert L["frame_type"] == "metal" and L["frame_type_source"] == "g3d_seat"
    assert not L["pass"] and not rep["ok"] and L["verdicts"]["FAIL"] > 0
    assert L["sectors"]["top"]["mid_depth_p5_p50_p95"][1] == pytest.approx(0.10, abs=0.01)
    assert L["sectors"]["bottom"]["mid_depth_p5_p50_p95"][1] == pytest.approx(0.455, abs=0.01)
    assert L["worst_gap"]["mm"] == pytest.approx(0.30, abs=0.02) and L["worst_gap"]["sector"] == "nasal"
    assert L["parts"] == ["Eyewire"] and L["body"]["front_loop_mm"] == pytest.approx(138.95, abs=0.01)


def test_check_glb_infers_frame_types(tmp_path):
    rep = check_glb(glb(tmp_path, SF.acetate_scene("sphere")))
    L = rep["lenses"][0]
    assert L["frame_type"] == "acetate" and L["frame_type_source"] == "inferred" and L["provisional"]
    assert any("inferred" in w for w in rep["warnings"])
    assert not L["pass"] and not L["graded_pass"] and L["verdicts"] == {"FAIL": L["points"]}  # 0.10 < 0.60
    rep = check_glb(glb(tmp_path, SF.nylor_scene(False), "n.glb"))
    L = rep["lenses"][0]
    assert L["frame_type"] == "semi_rimless" and L["classes"]["EXPOSED"] > 0.2 * L["points"]
    assert L["verdicts"]["VISIBLE_BY_DESIGN"] > 0
    rep = check_glb(glb(tmp_path, SF.metal_scene(), "m.glb"), frame_type="rimless")
    assert rep["lenses"][0]["pass"] and rep["lenses"][0]["frame_type_source"] == "argument"


def test_check_glb_riders_open_parts_and_roles(tmp_path):
    sc = SF.metal_scene()
    L = sc["lens"]
    # a 0.01 mm thick marking 0.05 mm above the lens front, an open frame sheet near the rim, a temple far away
    c = np.array(L.meta["apex"])
    xs, ys = np.meshgrid(np.linspace(-3, 3, 7) + c[0] - 10, np.linspace(-2, 2, 5) + c[1])
    zs = SF.cap_z(xs, ys, apex=c) + 0.05
    P = np.c_[xs.ravel(), ys.ravel(), zs.ravel()]
    T = []
    for i in range(4):
        for j in range(6):
            a = i * 7 + j
            T += [(a, a + 1, a + 8), (a, a + 8, a + 7)]
    mark = SF.Mesh(P, np.array(T))
    sheet = SF.Mesh(np.array([[-5, 23, 0], [-4, 23, 0], [-4, 24, 0]], float), np.array([[0, 1, 2]]))
    temple = SF.Mesh(*[np.asarray(a) for a in _box(60, 64, 0, 4, -100, -10)])
    extra = [{"name": "Marking", "mesh": mark, "role": "frame"}, {"name": "Sheet", "mesh": sheet, "role": "frame"},
             {"name": "Temple", "mesh": temple, "role": "temple"}]
    rep = check_glb(glb(tmp_path, sc, extra_nodes=extra))
    assert [r["node"] for r in rep["riders"]] == ["Marking"] and rep["riders"][0]["on"] == "Lens_L"
    assert [o["node"] for o in rep["open_parts"]] == ["Sheet"]
    assert [x["code"] for x in rep["lens_problems"]] == ["E_OPEN_PART"] and rep["ok"] is False   # review round 2
    assert rep["lenses"][0]["parts"] == ["Eyewire"]                              # temple-role parts are not frame


def _box(x0, x1, y0, y1, z0, z1):
    V = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    T = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]])
    return V, T


def test_check_glb_rim_split_from_normals(tmp_path):
    smooth = check_glb(glb(tmp_path, SF.acetate_scene("sphere"), "a.glb", normals="smooth"))
    flat = check_glb(glb(tmp_path, SF.acetate_scene("sphere"), "b.glb", normals="flat"))
    assert smooth["lenses"][0]["rim_split_deg"]["front"]["p50"] < 1.0
    assert 75.0 < flat["lenses"][0]["rim_split_deg"]["front"]["p5"] < 105.0
    none = check_glb(glb(tmp_path, SF.acetate_scene("sphere"), "c.glb", normals="none"))
    assert none["lenses"][0].get("rim_split_deg") is None
    sharpened = check_glb(glb(tmp_path, SF.acetate_scene("sphere"), "d.glb", normals="smooth",
                              seat_extras={"frame_type": "acetate", "sharpened": True}))
    assert any("sharpened" in r for r in sharpened["lenses"][0]["reasons"])


def test_check_glb_band_eyewire_under_brow(tmp_path):
    L = SF.base_lens(cap="flat")
    top = SF.arc_mask(L, "top", 0.30)
    eyewire = SF.swept(L, SF.rect_section(-0.4, 0.4, -0.6, 1.0))
    brow = SF.swept(L, SF.rect_section(0.2, 3.5, -2.4, 2.1), top)
    sc = {"lens": L, "parts": {"Eyewire": eyewire, "Brow": brow}}
    rep = check_glb(glb(tmp_path, sc, seat_extras={"frame_type": "browline"}))
    band = rep["lenses"][0]["band"]
    pair = band["pairs"]["Eyewire under Brow"]
    assert pair["front"]["p50_mm"] == pytest.approx(0.6, abs=0.03)                # r in [-0.4, 0.2): 0.6 mm
    assert pair["back"]["p50_mm"] == pytest.approx(0.6, abs=0.03)
    assert pair["area_outside_mm2"]["p50"] == pytest.approx(0.6 * 1.6, abs=0.05)
    assert band["graded_stations"] == 0 and any("browline without" in w for w in rep["warnings"])
    x = np.array([s["xyz"][0] for s in check_glb(glb(tmp_path, sc, "b.glb"), stations_table=True)
                  ["lenses"][0]["stations"]])
    rng = [(float(x.min()) - 1, float(x.max()) + 1)]
    graded = check_glb(glb(tmp_path, sc, "c.glb", seat_extras={"frame_type": "browline"}), hidden_x_range_mm=rng)
    gb = graded["lenses"][0]["band"]
    assert gb["graded_stations"] == pair["stations"] and gb["graded"]["ok"] is False
    assert not graded["lenses"][0]["pass"] and any("band" in r for r in graded["lenses"][0]["reasons"])


def test_check_glb_is_deterministic_and_backend_independent(tmp_path):
    p = glb(tmp_path, SF.metal_scene(), seat_extras={"frame_type": "metal"})
    a = json.dumps(check_glb(p, stations_table=True), sort_keys=True)
    b = json.dumps(check_glb(p, stations_table=True), sort_keys=True)
    assert a == b                                                                 # two runs bit-identical
    n = check_glb(p, backend="numpy", stations_table=True)
    o = json.loads(a)
    assert n["lenses"][0]["verdicts"] == o["lenses"][0]["verdicts"]
    assert n["lenses"][0]["classes"] == o["lenses"][0]["classes"]
    for sn, so in zip(n["lenses"][0]["stations"], o["lenses"][0]["stations"]):
        assert sn["class"] == so["class"] and sn["verdict"] == so["verdict"]
        for x, y in zip(sn["cap"], so["cap"]):
            assert (x is None) == (y is None) and (x is None or abs(x - y) <= 1e-3)


def test_check_glb_never_raises_and_cli(tmp_path, capsys):
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb")
    rep = check_glb(bad)
    assert rep["ok"] is False and "error" in rep
    p = glb(tmp_path, SF.acetate_scene("sphere"))
    out = tmp_path / "r.json"
    assert SC.main([str(p), "--frame-type", "rimless", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["lenses"][0]["frame_type"] == "rimless"
    assert SC.main([str(p), "--frame-type", "acetate"]) == 1
    assert json.loads(capsys.readouterr().out)["lenses"][0]["pass"] is False


def test_infer_frame_type():
    assert SC.infer_frame_type(np.array(["EXPOSED"] * 3 + ["LIPPED"] * 7, dtype=object)) == "semi_rimless"
    assert SC.infer_frame_type(np.array(["ENCLOSED"] * 6 + ["LIPPED"] * 4, dtype=object)) == "acetate"
    assert SC.infer_frame_type(np.array(["ENCLOSED"] * 2 + ["LIPPED"] * 8, dtype=object)) == "metal"
    assert SC.infer_frame_type(np.array(["EXPOSED"] * 3 + ["BACKED"] * 7, dtype=object)) is None


def test_world_normals_use_the_inverse_transpose():
    rz = np.array([[0.0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    np.testing.assert_allclose(SC.world_normals(np.array([[1.0, 0, 0]]), rz), [[0, 1, 0]], atol=1e-12)
    sx = np.diag([2.0, 1.0, 1.0, 1.0])                       # plane x + y = c becomes X/2 + Y = c
    got = SC.world_normals(np.array([[1.0, 1.0, 0]]) / np.sqrt(2), sx)
    want = np.array([[0.5, 1.0, 0]]) / np.linalg.norm([0.5, 1.0, 0])
    np.testing.assert_allclose(got, want, atol=1e-12)


def test_check_glb_reports_files_without_bodies_and_bad_seat_extras(tmp_path):
    p = tmp_path / "open.glb"
    SF.write_glb(p, [{"name": "Lens_L", "mesh": SF.open_lens(SF.base_lens(nseg=32)), "role": "lens"}])
    rep = check_glb(p)
    assert rep["ok"] is False and rep["lenses"] == [] and "no lens body" in rep["reasons"][0]
    rep = check_glb(glb(tmp_path, SF.acetate_scene("sphere"), "x.glb", seat_extras={"frame_type": "shield"}))
    assert rep["lenses"][0]["frame_type_source"] == "inferred" and any("unusable" in w for w in rep["warnings"])
