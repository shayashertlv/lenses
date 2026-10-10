"""g3d.temple/2 core build (plan/WP6_FACETS.md §2.2): closed exact solids, planar facet / inner / strip / cap
polygons, non-facet outer cells as ear-clip triangles, values (facets, fades, ramps, base), stations, sharp sets,
crease check, compact receipt, mirror and opening, bit-identical rebuilds, refusals and the budget."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple, report
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple.build import mesh_topology, station_list, to_local, to_world
from glasses3d.g3d.temple.spec import REFUSALS, TempleError, smoothstep

REPO = Path(__file__).resolve().parents[2]
FIXTURE_NAMES = sorted(FX.FIXTURES)


@pytest.fixture(scope="module")
def builds():
    return {n: build_temple(FX.FIXTURES[n]()) for n in FIXTURE_NAMES}


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_is_a_closed_exact_solid(builds, name):
    tb = builds[name]
    c = tb.checks
    assert c["closed"] and c["open_edges"] == 0 and c["duplicate_directed_edges"] == 0
    assert c["euler"] == 2 and c["volume_mm3"] > 0
    assert c["planarity_max_mm"] <= 1e-9                       # facet, inner, strip, cap (and roll) polygons
    assert c["self_intersection"].startswith("none")
    for f in tb.tables["facets"]:
        assert f["planarity_mm"] <= 1e-9
    assert tb.tables["thickness"]["min_mm"] >= tb.spec.options["t_min_mm"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_cells_facet_ngons_and_triangles_elsewhere(builds, name):
    tb = builds[name]
    for p, k, c in zip(tb.polys, tb.poly_kind, tb.poly_cell):
        if k in ("outer", "roll") and c != "facet":
            assert len(p) == 3, (k, c)                         # base, faded and ramp cells: ear-clip triangles
    facet_faces = {tb.poly_face[i] for i, c in enumerate(tb.poly_cell) if c == "facet"}
    assert all(tb.face_cell[fi] == "facet" for fi in facet_faces)
    assert sum(len(tb.arr.face_tris[fi]) for fi in range(len(tb.arr.faces)) if tb.face_cell[fi] != "facet") == \
        sum(1 for k, c in zip(tb.poly_kind, tb.poly_cell) if k in ("outer", "roll") and c != "facet")


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_inner_face_is_the_inner_table_and_vertical(builds, name):
    tb = builds[name]
    n = tb.n2
    P = tb.V[n:]
    assert np.max(np.abs(P[:, 2] - tb.spec.inner(P[:, 0]))) <= 1e-9
    assert np.allclose(tb.V[n:, :2], tb.V[:n, :2])


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_outline_is_reproduced(builds, name):
    tb = builds[name]
    ps = tb.spec
    B = tb.arr.V[tb.arr.boundary_loop]
    on_top = np.isclose(B[:, 1], ps.y_top(B[:, 0]), atol=1e-9)
    on_bot = np.isclose(B[:, 1], ps.y_bot(B[:, 0]), atol=1e-9)
    on_cap = np.isin(B[:, 0], [ps.d0, ps.d1])
    assert np.all(on_top | on_bot | on_cap)
    for chain in (ps.top, ps.bottom):                          # every knot is a vertex
        for p in chain:
            assert np.min(np.hypot(B[:, 0] - p[0], B[:, 1] - p[1])) <= 1e-9
    d, y = tb.V[:, 0], tb.V[:, 1]
    assert np.all(y <= ps.y_top(d) + 1e-9) and np.all(y >= ps.y_bot(d) - 1e-9)


def _analytic_prism_dihedrals():
    n = {"N": (0.0, 0.2, 1.0), "E": (0.1, 0.0, 1.0), "S": (0.0, -0.2, 1.0), "W": (-0.1, 0.0, 1.0),
         "base": (0.0, 0.0, 1.0)}
    ang = lambda a, b: math.degrees(math.acos(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))))
    return {("E", "N"): ang(n["E"], n["N"]), ("E", "S"): ang(n["E"], n["S"]), ("N", "W"): ang(n["N"], n["W"]),
            ("S", "W"): ang(n["S"], n["W"]), ("E", "base"): ang(n["E"], n["base"])}


def test_dihedrals_are_analytic(builds):
    want = _analytic_prism_dihedrals()
    got = {tuple(c["regions"]): c for c in builds["analytic_prism"].tables["creases"]}
    assert set(got) == set(want)
    for k, v in want.items():
        assert got[k]["dihedral_deg"][0] == pytest.approx(v, abs=0.01)
        assert got[k]["dihedral_deg"][1] == pytest.approx(v, abs=0.01)
    assert got[("E", "base")]["shape"] == "valley" and got[("E", "N")]["shape"] == "ridge"
    assert all(c["shape"] == "valley" for c in builds["valley"].tables["creases"]
               if c["kind"] == "crease" and "base" not in c["regions"])


def test_sharp_sets(builds):
    tb = builds["border_ramp"]
    sharp = set(tb.sharp)
    topo = mesh_topology(tb.polys)
    seen = set()
    for (a, b), p1 in topo["directed"].items():
        if a > b:
            continue
        p2 = topo["directed"][(b, a)]
        k1, k2 = tb.poly_kind[p1], tb.poly_kind[p2]
        dih = math.degrees(math.acos(np.clip(tb.normals[p1] @ tb.normals[p2], -1, 1)))
        if k1 == k2 == "outer":
            l1, l2 = tb.poly_region[p1], tb.poly_region[p2]
            c1, c2 = tb.poly_cell[p1], tb.poly_cell[p2]
            if l1 != l2:                                       # facet/facet and non-faded facet/base: always
                assert (a, b) in sharp
                seen.add("crease")
            elif "ramp" in (c1, c2):                           # ramp edges: at a dihedral >= 6 deg only
                assert ((a, b) in sharp) == (dih >= 6.0)
                seen.add("ramp")
            else:
                assert (a, b) not in sharp                      # base/base and facet-internal: smooth
        elif k1 == k2 == "inner":
            assert (a, b) not in sharp
        elif "outer" in (k1, k2) or "inner" in (k1, k2):
            assert (a, b) in sharp                            # face-to-strip and caps
    assert seen == {"crease", "ramp"}
    ch = builds["top_chamfer_arm"]
    assert any(c["kind"] == "chamfer" and c["dihedral_deg"][1] >= 6 for c in ch.tables["creases"])
    kinks = 0
    topo = mesh_topology(ch.polys)
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        walls = ("top", "bottom", "cap_hinge", "cap_tip")
        if a < b and ch.poly_kind[p1] in walls and ch.poly_kind[p2] in walls:
            dih = math.degrees(math.acos(np.clip(ch.normals[p1] @ ch.normals[p2], -1, 1)))
            assert ((a, b) in set(ch.sharp)) == (dih > 25.0)
            kinks += dih > 25.0
    assert kinks >= 4                                            # four cap corners at least


def test_faded_edges_are_sharp_only_at_6_deg(builds):
    tb = builds["fade"]
    topo = mesh_topology(tb.polys)
    n = 0
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        if a < b and tb.poly_cell[p1] == tb.poly_cell[p2] == "faded" and tb.poly_region[p1] == tb.poly_region[p2]:
            dih = math.degrees(math.acos(np.clip(tb.normals[p1] @ tb.normals[p2], -1, 1)))
            assert ((a, b) in set(tb.sharp)) == (dih >= 6.0)
            n += 1
    assert n > 10


def test_facet_corners_take_the_face_normal(builds):
    tb = builds["prada_default"]
    assert {tb.poly_region[i] for i in tb.flat_polys} == {"F1", "F2", "F3", "F4"}
    assert all(tb.poly_cell[i] == "facet" and tb.poly_kind[i] == "outer" for i in tb.flat_polys)
    assert not builds["fade"].flat_polys                         # faded cells are auto-smoothed


def test_fade_values_blend_plane_and_base(builds):
    tb = builds["fade"]
    ps, V = tb.spec, tb.arr.V
    f = ps.facets["N"]["fade"]
    for lab in ("N", "S"):
        vs = sorted({v for fi, face in enumerate(tb.arr.faces) if tb.face_label[fi] == lab for v in face})
        d, y = V[vs, 0], V[vs, 1]
        s = smoothstep((d - f["d0"]) / (f["d1"] - f["d0"]))
        want = (1 - s) * tb.sol.planes[lab].X(d, y) + s * ps.x_base(d, y)
        assert np.max(np.abs(tb.X_surface[vs] - want)) <= 1e-12
    ridge = [c for c in tb.tables["creases"] if c["regions"] == ["N", "S"]][0]
    assert ridge["dihedral_deg"][1] == pytest.approx(2 * math.degrees(math.atan(1.5 / 10.0)), abs=0.01)
    assert ridge["dihedral_deg"][0] < 0.5                        # the crease dies out in the fade


def test_fade_stations_are_dense_and_fades_may_run_forward():
    s = FX.fade(d0=40.0, d1=46.0)
    st = np.asarray(station_list(build_temple(s).spec))
    inside = st[(st >= 40.0 - 1e-9) & (st <= 46.0 + 1e-9)]
    assert np.max(np.diff(inside)) <= 1.0 + 1e-9                # <= (d1 - d0) / 6
    r = FX.fade()
    for lab in ("N", "S"):                                       # the plane at d 30, the base at d 0: fading forward
        r["facets"][lab]["fade"] = {"d0": 30.0, "d1": 0.0}
    tb = build_temple(r)
    v0 = [v for v in range(tb.n2) if tb.arr.V[v, 0] == 0.0 and abs(tb.arr.V[v, 1]) < 1e-9][0]
    assert tb.X_surface[v0] == pytest.approx(float(tb.spec.x_base(0.0, 0.0)), abs=1e-12)


def test_border_band_ramp(builds):
    tb = builds["border_ramp"]
    ps, V = tb.spec, tb.arr.V
    band = ps.options["border_band_mm"]
    B = ps.x_base(V[:, 0], V[:, 1])
    P = np.array([b["p"] for b in tb.borders])
    Q = np.array([b["q"] for b in tb.borders])
    s = Q - P
    t = np.clip(((V[:, None] - P[None]) * s[None]).sum(-1) / (s * s).sum(-1)[None], 0, 1)
    dist = np.hypot(*(P[None] + t[..., None] * s[None] - V[:, None]).transpose(2, 0, 1)).min(axis=1)
    facet_v = {v for fi, f in enumerate(tb.arr.faces) if tb.face_label[fi] != "base" for v in f}
    base_v = np.array([v for v in range(tb.n2) if v not in facet_v])
    far = base_v[dist[base_v] >= band]
    assert len(far) and np.max(np.abs(tb.X_surface[far] - B[far])) <= 1e-12        # the base beyond the band
    on_line = base_v[np.abs(dist[base_v] - band) < 1e-9]
    assert len(on_line) > 10                                     # the band line is in the arrangement
    ramps = {r["facet"]: r for r in tb.tables["border_ramps"]}
    assert set(ramps) == {"F2", "F4"}
    for r in ramps.values():
        assert r["height_mm"] == pytest.approx(0.6, abs=1e-9)
        assert r["slope_deg"] == pytest.approx(math.degrees(math.atan(0.3)), abs=1e-9)
    assert {w["facet"] for w in tb.warnings if w["code"] == "W_BORDER_RAMP"} == {"F2", "F4"}
    assert all(r["height_mm"] < 1e-9 for r in builds["pyramid"].tables["border_ramps"])
    assert not any(w["code"] == "W_BORDER_RAMP" for w in builds["pyramid"].warnings)
    for i, k in enumerate(tb.face_cell):                         # ramp cells touch the band only
        if k == "ramp":
            assert np.min(dist[tb.arr.faces[i]]) < band


def test_wedge_base_values(builds):
    tb = builds["wedge"]
    ps, V = tb.spec, tb.arr.V
    t = tb.thickness
    assert np.allclose(t[np.isclose(V[:, 1], ps.y_top(V[:, 0]))], 3.5)
    assert np.allclose(t[np.isclose(V[:, 1], ps.y_bot(V[:, 0]))], 6.0)


def test_stations_everywhere(builds):
    for name in ("prada_default", "rolled", "valley"):
        tb = builds[name]
        ps = tb.spec
        st = tb.stations
        assert max(np.diff([ps.d0] + st + [ps.d1])) <= ps.options["station_step_mm"] + 1e-9
        for k in list(ps.top[:, 0]) + list(ps.bottom[:, 0]) + list(ps.inner.d) + [n[0] for n in ps.nodes.values()]:
            if ps.d0 < k < ps.d1:
                assert min(abs(np.asarray(st) - k)) <= 1e-9
        for p, k, c in zip(tb.polys, tb.poly_kind, tb.poly_cell):
            if k in ("outer", "roll"):
                d = tb.V[p, 0]
                assert d.max() - d.min() <= ps.options["station_step_mm"] + 1e-9
    assert len(builds["rolled"].stations) == len(builds["pyramid"].stations)       # kept with the roll


def test_crease_check(builds):
    for name in ("prada_default", "valley", "fade"):
        tb = builds[name]
        for c in tb.spec.creases:
            r = next(x for x in tb.tables["creases"] if x["regions"] == c["facets"])
            assert r["shape"] == c["shape"] and r["declared"] == c["shape"]
    s = FX.pyramid()
    s["creases"] = [{"facets": ["F3", "F4"], "shape": "ridge"}]          # they meet only outside the outline
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["built"] == "none"
    s = FX.pyramid(t_c=5.15, t_h=5.02)                                  # creases below 2 deg: "flat"
    flat = [c for c in build_temple(s).tables["creases"] if c["kind"] == "crease" and c["shape"] == "flat"]
    assert flat
    s["creases"] = [{"facets": flat[0]["regions"], "shape": "ridge"}]
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["built"] == "flat"
    s = FX.pyramid()
    s["creases"] = [{"facets": ["F2", "F3"], "shape": "valley"}, {"facets": ["F4", "base"], "shape": "ridge"}]
    with pytest.raises(TempleError) as e:
        build_temple(s)
    f = e.value.fields
    assert f["built"] == "ridge" and f["dihedral_deg"] == pytest.approx(7.8, abs=0.1)
    assert [x["facets"] for x in f["failures"]] == [["F2", "F3"], ["F4", "base"]]


def test_base_pairs_are_judged_per_edge_with_length_shares(builds):
    tb = builds["prada_default"]
    rec = next(c for c in tb.tables["creases"] if c["regions"] == ["F4", "base"])
    assert rec["ridge"] + rec["valley"] == pytest.approx(rec["length_mm"])
    assert rec["ridge_share"] < 0.5 and rec["shape"] == "valley"


def test_mirror_is_exact_and_opening_is_rigid(builds):
    tb = builds["pyramid"]
    Wp, Pp, Tp = tb.world(+1)
    Wm, Pm, Tm = tb.world(-1)
    assert np.array_equal(Wm[:, 0], -Wp[:, 0]) and np.array_equal(Wm[:, 1:], Wp[:, 1:])
    assert all(a == b[::-1] for a, b in zip(Pp, Pm))
    assert np.array_equal(Tm, Tp[:, ::-1])
    for W, T in ((Wp, Tp), (Wm, Tm)):
        a, b, c = W[T[:, 0]], W[T[:, 1]], W[T[:, 2]]
        assert np.einsum("ij,ij->i", a, np.cross(b, c)).sum() > 0
    W5, _, _ = tb.world(+1, opening_deg=5.0)
    D0 = np.linalg.norm(Wp[:50, None] - Wp[None, :50], axis=-1)
    D5 = np.linalg.norm(W5[:50, None] - W5[None, :50], axis=-1)
    assert np.allclose(D0, D5, atol=1e-9)
    tip = int(np.argmax(tb.V[:, 0]))
    assert W5[tip, 0] > Wp[tip, 0] + 5.0
    ax = np.array([[-10.6 - (-10.1), 0.0, 64.6]])
    assert np.allclose(to_world(ax, -10.6, (64.6, -10.1), 7.0), to_world(ax, -10.6, (64.6, -10.1), 0.0), atol=1e-12)
    assert np.allclose(to_local(W5, -10.6, (64.6, -10.1), 5.0), tb.V, atol=1e-9)


def _digest(tb) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(tb.V).tobytes())
    h.update(json.dumps(tb.polys).encode())
    h.update(np.ascontiguousarray(tb.tris).tobytes())
    h.update(json.dumps(tb.sharp).encode())
    return h.hexdigest()


def test_bit_identical_rebuilds_across_processes(builds):
    here = {n: _digest(builds[n]) for n in ("prada_default", "top_chamfer_arm", "valley", "rolled", "border_ramp")}
    again = {n: _digest(build_temple(FX.FIXTURES[n]())) for n in here}
    assert here == again
    code = ("import json,sys;from glasses3d.tests.test_temple_build import _digest;"
            "from glasses3d.g3d.temple import build_temple, fixtures as FX;"
            "print(json.dumps({n: _digest(build_temple(FX.FIXTURES[n]())) for n in sys.argv[1:]}))")
    for seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([sys.executable, "-B", "-c", code, *here], capture_output=True, text=True, env=env,
                             cwd=str(REPO), timeout=300)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout.strip().splitlines()[-1]) == here


@pytest.mark.parametrize("code", sorted(set(FX.refusal_cases())))
def test_every_core_refusal_fires_with_fields(code):
    with pytest.raises(TempleError) as e:
        build_temple(FX.refusal_cases()[code])
    assert e.value.code == code
    d = e.value.to_dict()
    json.dumps(d)
    assert d["fields"] or code in ("E_SPEC", "E_OUTLINE")


def test_too_thin_names_point_facet_and_defining_points():
    with pytest.raises(TempleError) as e:
        build_temple(FX.f4_thin_regression())
    f = e.value.fields
    assert f["facet"] in ("F2", "F3", "F4") and len(f["at"]) == 2
    assert f["thickness_mm"] < f["t_min_mm"]
    assert len(f["defining_points"]) >= 2 and all("X" in p for p in f["defining_points"])


def test_refusal_codes_cover_the_design_list():
    core = set(FX.refusal_cases())
    blender_side = {"E_TARGET", "E_MULTI_MATERIAL"}
    internal = {"E_TRIANGULATE", "E_NOT_CLOSED"}              # arrangement / self-check guards (tested separately)
    assert core | blender_side | internal == set(REFUSALS)
    assert not core & (blender_side | internal)


def test_not_closed_guard_fires_on_a_broken_mesh(monkeypatch):
    from glasses3d.g3d.temple import build as BD
    real = BD._self_checks

    def broken(V, polys, *a, **k):
        polys = list(polys)
        polys.pop()                                            # drop the tip cap
        return real(V, polys, *a, **k)
    monkeypatch.setattr(BD, "_self_checks", broken)
    with pytest.raises(TempleError) as e:
        build_temple(FX.slab())
    assert e.value.code == "E_NOT_CLOSED" and e.value.fields["open_edges"] > 0


def test_compact_receipt(builds):
    tb = builds["prada_default"]
    r = report.receipt(tb, core_ms=12.345)
    text = json.dumps(r)
    n_cr = sum(1 for c in tb.tables["creases"] if c["kind"] == "crease")
    assert r["lines"][:n_cr] == report.crease_lines(tb)               # crease lines first
    assert r["lines"][0] == "F1|F2 ridge 14.4° declared ridge"
    assert {f["label"] for f in r["facets"]} == {"F1", "F2", "F3", "F4"}
    f1 = next(f for f in r["facets"] if f["label"] == "F1")
    assert f1["rise"] == -0.38 and "rise_vs_base" in f1 and isinstance(f1["lean_deg"], float)
    rows = r["stations"]["rows"]
    assert r["stations"]["columns"] == ["d", "y_top", "y_bot", "x_in", "t_top", "t_bot"]
    assert rows[1][0] - rows[0][0] == 10.0 and rows[-1][0] == tb.spec.d1
    assert r["thickness"]["min_mm"] == round(tb.tables["thickness"]["min_mm"], 2)
    assert r["checks"] == {"closed": True, "euler": 2, "volume_mm3": round(tb.checks["volume_mm3"], 1), "planar": True}
    assert r["budget"]["core_ms"] == 12.3 and r["spec_sha256"] == tb.spec.sha256
    assert len(text) < 3000, len(text)
    full = report.receipt_full(tb)
    json.dumps(full)
    assert {"facets", "creases", "border_ramps", "base_planarity", "nodes", "snaps"} <= set(full)
    assert report.merge_lines(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_core_budget_per_temple():
    spec = FX.prada_default()
    times = []
    for _ in range(7):
        t = time.process_time()                                # CPU time: other processes on the machine do not count
        build_temple(spec)
        times.append(time.process_time() - t)
    assert min(times) <= 0.050, times                          # v1's budget: core <= 50 ms per temple (no roll)


def test_tip_round_builds(builds):
    s = FX.slab()
    s["outline"]["tip_round_mm"] = 1.2
    tb = build_temple(s)
    assert tb.checks["closed"] and tb.checks["euler"] == 2
