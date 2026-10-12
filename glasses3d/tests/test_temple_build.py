"""g3d.temple/2 core build (plan/WP6_FACETS.md §2.2): closed exact solids, planar facet / inner / strip / cap
polygons, non-facet outer cells as ear-clip triangles, values (facets, fades, ramps, base), cells by fade weight,
fades beside other fades or planes (held walls: E_FADE_CLIFF, W_FADE_HOLD; fades that move nothing: W_FADE_INERT),
stations, sharp sets, crease check, compact receipt, mirror and opening, bit-identical rebuilds, refusals and the
budget."""
from __future__ import annotations

import contextlib
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
from glasses3d.g3d.temple.build import (design_normal, fade_weight, faded_corner_normals, mesh_topology, station_list,
                                         to_local, to_world)
from glasses3d.g3d.temple.spec import FADE_HOLD_MIN_MM, FLAT_CREASE_DEG, REFUSALS, TempleError, smoothstep

REPO = Path(__file__).resolve().parents[2]
FIXTURE_NAMES = sorted(FX.FIXTURES)
R0015 = REPO / "glasses3d/data/wp6v2/lab/package/work/temple_lab/revisions/r0015.json"


@pytest.fixture(scope="module")
def builds():
    return {n: build_temple(FX.FIXTURES[n]()) for n in FIXTURE_NAMES}


@contextlib.contextmanager
def crease_fade_check_off():
    """E_FADE_CREASE (run 7) turned off: for the tests of fade values, cells and sharpness on a crease both facets
    fade along (refused since run 7; the same code serves mixed fades, where one facet stands)."""
    from glasses3d.g3d.temple import build as BD
    real = BD._check_fade_creases
    BD._check_fade_creases = lambda *a, **k: None
    try:
        yield
    finally:
        BD._check_fade_creases = real


@pytest.fixture(scope="module")
def legacy_fade():
    """FX.fade(ridge_to=60): the N|S ridge dies out in N's and S's fade (FX.fade's default before run 7), built with
    the crease guard off (crease_fade_check_off)."""
    with crease_fade_check_off():
        return build_temple(FX.fade(ridge_to=60.0))


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


def _fade_ridge_design_deg(ps, d) -> np.ndarray:
    """The fade fixture's N|S ridge design dihedral at d, in closed form: N and S fall 0.15 mm per mm from the ridge
    (1.5 mm proud) to the flat base 10 mm away, so across the ridge the faded slopes are -/+ (1 - s) 0.15 and along it
    both are s' (B - P) = -1.5 s'."""
    f = ps.facets["N"]["fade"]
    t = np.clip((np.asarray(d) - f["d0"]) / (f["d1"] - f["d0"]), 0.0, 1.0)
    s, ds = smoothstep(t), 6.0 * t * (1.0 - t) / (f["d1"] - f["d0"])
    return np.degrees(2.0 * np.arctan((1.0 - s) * 0.15 / np.hypot(1.0, 1.5 * ds)))


def test_faded_creases_are_sharp_while_they_stand(legacy_fade):
    tb = legacy_fade
    topo = mesh_topology(tb.polys)
    sharp = set(tb.sharp)
    n, cross, cross_faded, cross_soft = 0, 0, 0, 0
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        if a > b or not {tb.poly_kind[p1], tb.poly_kind[p2]} <= {"outer", "roll"}:
            continue
        r1, r2, c1, c2 = tb.poly_region[p1], tb.poly_region[p2], tb.poly_cell[p1], tb.poly_cell[p2]
        dih = math.degrees(math.acos(np.clip(tb.normals[p1] @ tb.normals[p2], -1, 1)))
        if {r1, r2} == {"N", "S"}:
            cross += 1
            if "faded" not in (c1, c2):
                assert (a, b) in sharp                              # before the fade: facet cells, always sharp
                continue
            design = float(_fade_ridge_design_deg(tb.spec, 0.5 * (tb.V[a, 0] + tb.V[b, 0])))
            assert abs(design - 0.5) > 1e-3
            assert ((a, b) in sharp) == (design >= 0.5 or dih >= 6.0)    # sharp while the ridge stands
            cross_faded += 1
            cross_soft += (a, b) not in sharp
        elif "faded" in (c1, c2) and r1 == r2:
            assert ((a, b) in sharp) == (dih >= 6.0)                 # inside N or S: folds of 6 deg or more only
            n += 1
    assert n > 10 and cross > 20 and cross_faded > 10
    assert 1 <= cross_soft <= 3                                     # it has died out only at the very end of the fade


def test_facet_corners_take_the_face_normal(builds):
    tb = builds["prada_default"]
    assert {tb.poly_region[i] for i in tb.flat_polys} == {"F1", "F2", "F3", "F4"}
    assert all(tb.poly_cell[i] == "facet" and tb.poly_kind[i] == "outer" for i in tb.flat_polys)
    fd = builds["fade"]                                          # before its fade a faded facet is planar n-gons
    d0 = fd.spec.facets["N"]["fade"]["d0"]
    want = [i for i, (k, rg) in enumerate(zip(fd.poly_kind, fd.poly_region))
            if k == "outer" and rg in ("N", "S") and fd.V[fd.polys[i], 0].max() <= d0 + 1e-9]
    assert want and fd.flat_polys == want
    assert all(len(fd.polys[i]) == 3 for i, c in enumerate(fd.poly_cell) if c == "faded")      # auto-smoothed


def test_fade_values_blend_plane_and_base(legacy_fade):
    tb = legacy_fade
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
    r = FX.fade(ridge_from=35.0, ridge_to=60.0)    # the ridge (d 35-60) lies beyond the fade toward the hinge
    for lab in ("N", "S"):                                       # the plane at d 30, the base at d 0: fading forward
        r["facets"][lab]["fade"] = {"d0": 30.0, "d1": 0.0}
    tb = build_temple(r)
    ps, V = tb.spec, tb.arr.V
    for lab in ("N", "S"):                  # every vertex of N and S: (1 - s) plane + s base, s growing toward d 0
        vs = sorted({v for fi, face in enumerate(tb.arr.faces) if tb.face_label[fi] == lab for v in face})
        s = fade_weight(ps, lab, V[vs, 0])
        want = (1 - s) * tb.sol.planes[lab].X(V[vs, 0], V[vs, 1]) + s * ps.x_base(V[vs, 0], V[vs, 1])
        assert np.max(np.abs(tb.X_surface[vs] - want)) <= 1e-12 and s.max() > 0.9 and s.min() == 0.0


@pytest.mark.parametrize("name", ["fade", "fade_one_side"])
def test_cells_follow_the_fade_weight(builds, name):
    tb = builds[name]
    ps, V = tb.spec, tb.arr.V
    kinds = set()
    for fi, face in enumerate(tb.arr.faces):
        lab = tb.face_label[fi]
        if lab == "base":
            continue
        w = fade_weight(ps, lab, V[face, 0])
        assert tb.face_cell[fi] == ("faded" if w.max() > 0.0 else "facet")
        kinds.add(tb.face_cell[fi])
        f = ps.facets[lab]["fade"]
        if f is not None and tb.face_cell[fi] == "faded":
            assert V[face, 0].min() >= f["d0"] - 1e-9                 # stations sit at d0: no face straddles it
    assert kinds == {"facet", "faded"}


def test_faded_fans_carry_one_design_normal(builds):
    tb = builds["fade"]
    ps, V = tb.spec, tb.V
    fc = faded_corner_normals(tb)
    flat = set(tb.flat_polys)
    assert len(fc) > 100 and not {i for i, _ in fc} & flat
    d0 = ps.facets["N"]["fade"]["d0"]
    starts = 0
    for (i, v), n in fc.items():
        reg = tb.poly_region[i]
        if V[v, 0] <= d0 + 1e-9:                                    # where the fade starts: the facet cell's normal
            f = next(p for p in tb.flat_polys if tb.poly_region[p] == reg and v in tb.polys[p])
            assert np.array_equal(n, tb.normals[f])
            starts += 1
        else:
            assert np.abs(n - design_normal(ps, tb.sol, reg, V[v, 0], V[v, 1])).max() <= 1e-12
    assert starts >= 4
    topo = mesh_topology(tb.polys)                                   # one fan, one normal
    sharp = set(tb.sharp)
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        if (min(a, b), max(a, b)) in sharp:
            continue
        for v in (a, b):
            if (p1, v) in fc and (p2, v) in fc:
                assert np.array_equal(fc[(p1, v)], fc[(p2, v)])
    assert faded_corner_normals(builds["prada_default"]) == {} and faded_corner_normals(builds["rolled"]) == {}


def test_a_fade_beyond_the_facet_builds_like_no_fade():
    plain = FX.pyramid()
    late = FX.pyramid()
    late["facets"]["F2"] = {"poly": late["facets"]["F2"], "fade": {"d0": 110.0, "d1": 130.0}}   # F2 ends near d 47
    a, b = build_temple(plain), build_temple(late)
    assert b.stations == a.stations
    assert b.polys == a.polys and np.array_equal(b.V, a.V) and np.array_equal(b.tris, a.tris)
    assert b.poly_cell == a.poly_cell and b.flat_polys == a.flat_polys and b.sharp == a.sharp
    assert np.array_equal(b.normals, a.normals)
    f2 = [i for i in b.flat_polys if b.poly_region[i] == "F2"]
    assert f2 and all(len(b.polys[i]) >= 3 for i in f2)
    assert b.checks["planarity_max_mm"] <= 1e-9


def test_a_facet_fades_beside_a_plane(builds):
    """N fades, its crease neighbour S does not (E_FADE_NEIGHBOUR before 2026-10-11): it builds, every shared ridge
    vertex keeps S's plane (the less-faded facet), N's outer edge blends to the base, and the ridge is sharp all along
    because S still stands there."""
    tb = builds["fade_one_side"]
    ps, V, sol = tb.spec, tb.arr.V, tb.sol
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    nv = {v for fi, f in enumerate(tb.arr.faces) if tb.face_label[fi] == "N" for v in f}
    sv = {v for fi, f in enumerate(tb.arr.faces) if tb.face_label[fi] == "S" for v in f}
    ridge = sorted(nv & sv)
    assert len(ridge) > 20
    assert np.max(np.abs(tb.X_surface[ridge] - sol.planes["S"].X(V[ridge, 0], V[ridge, 1]))) <= 1e-9
    only_n = sorted(nv - sv)
    s = fade_weight(ps, "N", V[only_n, 0])
    want = (1 - s) * sol.planes["N"].X(V[only_n, 0], V[only_n, 1]) + s * ps.x_base(V[only_n, 0], V[only_n, 1])
    assert np.max(np.abs(tb.X_surface[only_n] - want)) <= 1e-12
    top = [v for v in only_n if abs(V[v, 1] - 10.0) < 1e-9]
    assert tb.X_surface[top].max() - tb.X_surface[top].min() == pytest.approx(0.5, abs=1e-9)    # 0.5 proud to base
    assert all(tb.face_cell[fi] == "facet" for fi, l in enumerate(tb.face_label) if l == "S")
    topo = mesh_topology(tb.polys)
    edges = [(a, b) for (a, b), p in topo["directed"].items() if a < b and
             {tb.poly_region[p], tb.poly_region[topo["directed"][(b, a)]]} == {"N", "S"}]
    assert len(edges) > 20 and set(edges) <= set(tb.sharp)


def test_former_fade_neighbour_refusals_build():
    assert "E_FADE_NEIGHBOUR" not in REFUSALS
    s = FX.fade(ridge_to=60.0)
    s["facets"]["S"]["fade"] = {"d0": 20.0, "d1": 50.0}                    # two different fades on one crease
    with pytest.raises(TempleError) as e:                                  # both fade along it: refused since run 7
        build_temple(s)
    assert e.value.code == "E_FADE_CREASE"
    with crease_fade_check_off():                                          # the values the less-faded one keeps
        tb = build_temple(s)
    V = tb.arr.V
    ridge = [v for v in range(tb.n2) if abs(V[v, 1]) < 1e-9 and V[v, 0] <= 60.0]
    sN = fade_weight(tb.spec, "N", V[ridge, 0])                              # N fades later: N is the less faded
    want = (1 - sN) * tb.sol.planes["N"].X(V[ridge, 0], V[ridge, 1]) + sN * tb.spec.x_base(V[ridge, 0], V[ridge, 1])
    assert np.max(np.abs(tb.X_surface[ridge] - want)) <= 1e-9
    p = FX.analytic_prism()                                     # N fades; E, S, W do not; all four share the apex O
    p["facets"]["N"] = {"poly": p["facets"]["N"], "fade": {"d0": 5.0, "d1": 35.0}}
    tb = build_temple(p)
    o = [v for v in range(tb.n2) if abs(tb.arr.V[v, 0] - 20.0) < 1e-9 and abs(tb.arr.V[v, 1]) < 1e-9]
    assert tb.X_surface[o] == pytest.approx(FX.PRISM["x_in"] + FX.PRISM["T"] + FX.PRISM["h"], abs=1e-9)
    assert {tb.face_cell[fi] for fi, l in enumerate(tb.face_label) if l == "N"} == {"facet", "faded"}
    assert tb.checks["closed"] and tb.checks["planarity_max_mm"] <= 1e-9


def _tilts(tb) -> list:
    """[(tilt deg, largest |hold| mm, facet, kind, centroid d, centroid y)] of every faded outer and roll triangle,
    recomputed from the build (the reviewer's measure, 2026-10-11): a vertex's hold = X_surface minus the facet's own
    fade, (1 - s) plane + s base; the tilt = the angle between the built triangle and the same triangle moved by
    -hold (chamfer and roll depth as built)."""
    out, ps = [], tb.spec
    for i, p in enumerate(tb.polys):
        if tb.poly_cell[i] != "faded" or tb.poly_kind[i] not in ("outer", "roll"):
            continue
        lab = tb.poly_region[i].partition("/")[0]
        P = tb.V[p]
        f = ps.facets[lab]["fade"]
        s = smoothstep((P[:, 0] - f["d0"]) / (f["d1"] - f["d0"]))
        hold = tb.X_surface[p] - ((1 - s) * tb.sol.planes[lab].X(P[:, 0], P[:, 1]) + s * ps.x_base(P[:, 0], P[:, 1]))
        Q = P.copy()
        Q[:, 2] -= hold
        n1, n2 = (np.cross(A[1] - A[0], A[2] - A[0]) for A in (P, Q))
        tilt = math.degrees(math.acos(np.clip(n1 @ n2 / np.linalg.norm(n1) / np.linalg.norm(n2), -1.0, 1.0)))
        c = P.mean(axis=0)
        out.append((tilt, float(np.abs(hold).max()), lab, tb.poly_kind[i], float(c[0]), float(c[1])))
    return out


def _counted(tb) -> list:
    """_tilts held at least FADE_HOLD_MIN_MM (the only ones E_FADE_CLIFF / W_FADE_HOLD count)."""
    return [x for x in _tilts(tb) if x[1] >= FADE_HOLD_MIN_MM]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_faded_triangles_tilt_within_the_limit(builds, name):
    tb = builds[name]
    worst = max((x[0] for x in _counted(tb)), default=0.0)
    assert worst <= 25.0
    hold = [w for w in tb.warnings if w["code"] == "W_FADE_HOLD"]
    assert len(hold) == (worst >= 10.0)
    assert not any(w["code"] == "W_FADE_INERT" for w in tb.warnings)          # every fixture fade moves its facet
    if name == "fade_one_side":
        assert worst == pytest.approx(8.53, abs=0.01)                 # 10 mm wide cells held 1.5 mm: no warning


def _no_fade_check(monkeypatch):
    from glasses3d.g3d.temple import build as BD
    monkeypatch.setattr(BD, "_check_fade_holds", lambda *a, **k: [])


def test_fade_hold_warns_in_one_receipt_line():
    tb = build_temple(FX.fade_one_side(proud=3.0, edge=0.0))           # S holds N's ridge 3 mm above N's fade
    hold = [w for w in tb.warnings if w["code"] == "W_FADE_HOLD"]
    worst = max(_counted(tb), key=lambda x: x[0])
    assert len(hold) == 1 and 10.0 <= worst[0] <= 25.0
    assert worst[0] == pytest.approx(math.degrees(math.atan(0.3)), abs=0.01)     # 3 mm over the 10 mm wide cells
    w = hold[0]
    assert (w["facet"], w["neighbour"]) == ("N", "S") and w["deg"] == pytest.approx(worst[0], abs=1e-9)
    assert w["hold_mm"] == pytest.approx(3.0, abs=1e-6) and w["d_mm"][1] == 60.0
    assert w["at"] == pytest.approx(list(worst[4:]), abs=1e-9)
    lines = [ln for ln in report.receipt(tb)["lines"] if ln.startswith("W_FADE_HOLD")]
    assert lines == [f"W_FADE_HOLD: S holds faded N 3.00 mm above its fade at d {w['d_mm'][0]:.1f}-60.0: tilt "
                     f"{worst[0]:.1f} deg (>= 10; > 25 is refused)"]
    assert not any(w["code"] == "W_FADE_HOLD" for w in build_temple(FX.fade()).warnings)      # identical fades


def test_fade_cliff_refuses_a_held_wall(monkeypatch):
    spec = FX.refusal_cases()["E_FADE_CLIFF"]
    with pytest.raises(TempleError) as e:
        build_temple(spec)
    f = e.value.fields
    assert e.value.code == "E_FADE_CLIFF" and f["facet"] == "N" and f["neighbour"] == "S" and f["facets"] == ["N", "S"]
    assert f["hold_mm"] == pytest.approx(6.0, abs=1e-6) and f["d_mm"][1] == 60.0 and f["limit_deg"] == 25.0
    assert f["min_hold_mm"] == FADE_HOLD_MIN_MM == 0.05
    assert f["tilt_deg"] == pytest.approx(math.degrees(math.atan(0.6)), abs=1e-6)       # 6 mm over 10 mm wide cells
    assert e.value.message == (f"facet N fades but S holds it 6.00 mm up on their crease at d 50.0-60.0 (worst at d "
                               f"{f['at'][0]:.1f} y {f['at'][1]:.1f}): a wall 31 deg off its fade (> 25); start the fade "
                               f"where the crease ends")
    _no_fade_check(monkeypatch)                                        # the same build unchecked: the tilt is real
    worst = max(_counted(build_temple(spec)), key=lambda x: x[0])
    assert f["tilt_deg"] == pytest.approx(worst[0], abs=1e-9) and worst[0] > 25.0 and worst[2] == "N"
    assert f["at"] == pytest.approx(list(worst[4:]), abs=1e-9)


REMEDIES = ("fix the node values", "do not redeclare", "start the fade later", "keep ", "check the photos")


def test_crease_shape_states_facts_only():
    """E_CREASE_SHAPE states what was declared, what was built and over how much of the crease, and offers no remedy
    (WP6_V3 P6; FINDINGS §3: "fix the node values, do not redeclare" and "start the fade later" were single offered
    remedies Astra executed literally; review-builder-design F5): a crease a fade flattens, and one built with the
    other shape."""
    s = FX.fade()
    for lab in ("N", "S"):
        s["facets"][lab]["fade"] = {"d0": 0.0, "d1": 4.0}               # the 17 deg ridge stands only over d 0-4
    with pytest.raises(TempleError) as e:                              # since run 7 the crease guard refuses first
        build_temple(s)
    assert e.value.code == "E_FADE_CREASE"
    with pytest.raises(TempleError) as e, crease_fade_check_off():
        build_temple(s)
    assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["built"] == "flat"
    assert e.value.message == ("crease N|S is declared ridge but built flat 1.18 deg (flat (< 2 deg): no ridge or "
                               "valley)")
    with pytest.raises(TempleError) as e:
        build_temple(FX.refusal_cases()["E_CREASE_SHAPE"])
    assert e.value.message == ("crease F2|F3 is declared valley but built ridge 7.8 deg (a ridge along 100% of its "
                               "33.0 mm)")
    assert not any(r in e.value.message for r in REMEDIES)


STRAIGHT_HINT = ("facet X runs straight through node C, so the 4 creases there cannot all be ridges: one is a valley "
                 "or two of those faces are one")


def test_crease_shape_names_a_node_a_facet_runs_straight_through():
    """Four creases at node C, which facet X's lower edge runs straight through (FX.straight_node), all declared
    ridges: no node values build that (a convex corner holding a straight line is a prism along it), so E_CREASE_SHAPE
    leads with the refused crease there and states both ways it can be built: one is a valley, or two of those faces
    are one (no single offered remedy: run 2 split the owner's face 4 under "fix the node values, do not redeclare",
    2026-10-11; v3 drops "keep X whole" too, WP6_V3 P6). The same layout with that one crease declared a valley
    builds as declared."""
    with pytest.raises(TempleError) as e:
        build_temple(FX.straight_node())
    f = e.value.fields
    assert e.value.code == "E_CREASE_SHAPE" and f["facets"] == ["R1", "R2"] and f["built"] == "valley"
    assert f["straight_node"] == {"node": "C", "facet": "X", "angle_deg": pytest.approx(180.0, abs=1e-9),
                                  "shape": "ridge", "creases": [["R1", "R2"], ["R1", "X"], ["R2", "R3"], ["R3", "X"]],
                                  "refused": ["R1", "R2"]}
    assert e.value.message.endswith("(a valley along 100% of its 15.4 mm); " + STRAIGHT_HINT)
    assert not any(r in e.value.message for r in REMEDIES)
    json.dumps(e.value.to_dict())
    tb = build_temple(FX.straight_node(valley=True))
    built = {tuple(c["regions"]): (c["shape"], c["declared"]) for c in tb.tables["creases"] if c["kind"] == "crease"}
    assert built == {("R1", "X"): ("ridge", "ridge"), ("R1", "R2"): ("valley", "valley"),
                     ("R2", "R3"): ("ridge", "ridge"), ("R3", "X"): ("ridge", "ridge")}
    assert tb.checks["closed"] and not tb.warnings


@pytest.mark.parametrize("lift, how", [(0.5, "runs straight through node C"), (2.0, "has a 185 deg corner at node C"),
                                       (-2.0, None)])
def test_straight_node_hint_needs_a_straight_or_reflex_corner(lift, how):
    """C lifted 0.5 mm: X's corner there is 181.3 deg, straight within STRAIGHT_NODE_DEG; lifted 2 mm: 185.1 deg,
    reflex (ridges all round are impossible there too); lowered 2 mm: 174.9 deg, a convex corner that ridges all round
    can build, so the refusal states the built crease alone. Every node value is unchanged and one valley builds
    each."""
    with pytest.raises(TempleError) as e:
        build_temple(FX.straight_node(lift=lift))
    assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["facets"] == ["R1", "R2"]
    if how is None:
        assert "straight_node" not in e.value.fields
        assert e.value.message.endswith("(a valley along 100% of its 13.6 mm)")
    else:
        assert e.value.message.endswith("; " + STRAIGHT_HINT.replace("runs straight through node C", how))
    build_temple(FX.straight_node(lift=lift, valley=True))


def test_straight_node_hint_for_valleys_all_round():
    """The mirror image (X sunk 2 mm below the base, R1 and R3 rising away from it): four declared valleys are refused
    with the ridge named; R1|R2 declared a ridge builds."""
    s = FX.straight_node(proud=-2.0, m1=0.3, m3=0.15)
    for c in s["creases"]:
        c["shape"] = "valley"
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.fields["straight_node"]["shape"] == "valley"
    assert e.value.message.endswith("; facet X runs straight through node C, so the 4 creases there cannot all be "
                                    "valleys: one is a ridge or two of those faces are one")
    for c in s["creases"]:
        if sorted(c["facets"]) == ["R1", "R2"]:
            c["shape"] = "ridge"
    build_temple(s)


def test_straight_node_message_fits_the_lab_refusal_limit():
    """With the longest names the edits allow (24 characters) for every facet and the node, the reflex wording and a
    two-digit dihedral the message stays within the lab's REFUSAL_MAX, so facet_variants returns it whole."""
    from glasses3d.agent.temple_lab.lab import REFUSAL_MAX, brief
    long = {k: k + "w" * (24 - len(k)) for k in ("X", "R1", "R2", "R3", "C")}
    s = FX.straight_node(lift=2.0)
    s["nodes"] = {long.get(k, k): v for k, v in s["nodes"].items()}
    s["facets"] = {long[k]: [long.get(n, n) for n in poly] for k, poly in s["facets"].items()}
    for c in s["creases"]:
        c["facets"] = [long[x] for x in c["facets"]]
    with pytest.raises(TempleError) as e:
        build_temple(s)
    m = e.value.message
    assert e.value.fields["straight_node"]["node"] == long["C"] and f"facet {long['X']} has a 185 deg corner" in m
    assert len(m) <= REFUSAL_MAX and brief(m) == m, len(m)


def test_inert_fade_warns_in_one_line():
    """A fade that moves none of its facet's vertices 0.05 mm (W_FADE_INERT, the reviewer's defect 5): beyond the
    facet, or every vertex held by a standing neighbour. One line names every such facet with why."""
    late = FX.pyramid()
    late["facets"]["F2"] = {"poly": late["facets"]["F2"], "fade": {"d0": 110.0, "d1": 130.0}}   # F2 ends near d 47
    tb = build_temple(late)
    w = [x for x in tb.warnings if x["code"] == "W_FADE_INERT"]
    assert len(w) == 1 and w[0]["facets"] == ["F2"] and w[0]["why"] == {"F2": "outside it"}
    assert w[0]["message"] == "fades that move no vertex >= 0.05 mm: F2 (outside it)"
    s = FX.fade_one_side(edge=0.0)                    # N's outer edge on the base, its ridge held by S: N moves nothing
    tb = build_temple(s)
    w = [x for x in tb.warnings if x["code"] == "W_FADE_INERT"]
    assert len(w) == 1 and w[0]["why"] == {"N": "held by less-faded neighbours"}
    assert [ln for ln in report.receipt(tb)["lines"] if ln.startswith("W_FADE_INERT")] == \
        ["W_FADE_INERT: fades that move no vertex >= 0.05 mm: N (held by less-faded neighbours)"]
    assert not any(x["code"] == "W_FADE_INERT" for x in build_temple(FX.fade_one_side()).warnings)    # edge 0.5


def test_early_fade_warns_in_one_line():
    """A fade that starts within FADE_EARLY_SHARE of its facet's own d extent and within FADE_EARLY_MM of its end,
    counted from the side the facet stands on (W_FADE_EARLY; facet-astra-003's tail faded from its own front corner
    and never read as a face): one line names every such facet with its flat length and that end, and the fix; a fade
    from the middle, or one leaving FADE_EARLY_MM or more flat, does not warn; a fade toward the hinge counts from the
    facet's back."""
    tb = build_temple(FX.fade(d0=4.0, d1=60.0))
    w = [x for x in tb.warnings if x["code"] == "W_FADE_EARLY"]
    assert len(w) == 1 and w[0]["facets"] == ["N", "S"] and w[0]["share"] == {"N": 0.067, "S": 0.067}
    assert w[0]["flat_mm"] == {"N": 4.0, "S": 4.0}
    assert w[0]["message"] == ("barely flat before their fade: N flat 4.0 mm at its front (d 0.0); S flat 4.0 mm at "
                               "its front (d 0.0); fade later, from where its creases end")   # never "alike across"
    assert [ln for ln in report.receipt(tb)["lines"] if ln.startswith("W_FADE_EARLY")] == \
        [f"W_FADE_EARLY: {w[0]['message']}"]
    assert not any(x["code"] == "W_FADE_EARLY" for x in build_temple(FX.fade()).warnings)     # d0 30 of d 0-60
    assert not any(x["code"] == "W_FADE_EARLY" for x in build_temple(FX.fade(d0=5.0, d1=60.0)).warnings)  # 5 mm
    for d0, warned in ((56.0, True), (40.0, False)):   # toward the hinge: N and S (d 0-60) stand only over d d0-60
        back = FX.fade(ridge_from=d0, ridge_to=60.0)      # the ridge (d0-60) stands (else E_FADE_CREASE)
        back["creases"] = []                              # the ridge fades out ahead of d0 (undeclared, not refused)
        for f in back["facets"].values():
            f["fade"] = {"d0": d0, "d1": 10.0}
        w = [x for x in build_temple(back).warnings if x["code"] == "W_FADE_EARLY"]
        assert (w[0]["share"] if w else None) == ({"N": 0.067, "S": 0.067} if warned else None)
        if warned:
            assert w[0]["message"].startswith("barely flat before their fade: N flat 4.0 mm at its back (d 60.0); ")


# the reviewer's probe table on facet-astra-002's r0015 (2026-10-11): every facet fades over d 40-85 as built ("REST")
R15_FACETS = ("crown", "hinge_flank", "logo", "rear_bevel", "tail_flank")
REST = {k: (40.0, 85.0) for k in R15_FACETS}
R15_CLIFFS = [            # (case, fades, faded facet, neighbour holding it, kind of the worst triangle)
    ("only rear_bevel 40-85", {"rear_bevel": (40.0, 85.0)}, "rear_bevel", "tail_flank", "roll"),
    ("only tail_flank 55-85", {"tail_flank": (55.0, 85.0)}, "tail_flank", "rear_bevel", "roll"),
    ("hinge_flank 5-20 alone", {"hinge_flank": (5.0, 20.0)}, "hinge_flank", "logo", "outer"),
    ("C tail_flank 40-100", {**REST, "tail_flank": (40.0, 100.0)}, "rear_bevel", "tail_flank", "outer"),
    ("roll: only rear_bevel 86-96", {"rear_bevel": (86.0, 96.0)}, "rear_bevel", "tail_flank", "roll"),
    ("roll: only rear_bevel 88-96", {"rear_bevel": (88.0, 96.0)}, "rear_bevel", "tail_flank", "roll"),
    ("roll: only tail_flank 88-99", {"tail_flank": (88.0, 99.0)}, "tail_flank", "rear_bevel", "roll")]
R15_BUILDS = [            # (case, fades or None = r0015 as is, W_FADE_HOLD expected)
    ("r0015 as is", None, False),
    ("identical 50-95", {k: (50.0, 95.0) for k in R15_FACETS}, False),
    ("A rear pair 50-95", {**REST, "rear_bevel": (50.0, 95.0), "tail_flank": (50.0, 95.0)}, False),
    ("B tail_flank 45-90", {**REST, "tail_flank": (45.0, 90.0)}, True),
    ("B2 tail_flank 41-86", {**REST, "tail_flank": (41.0, 86.0)}, False),
    ("B4 tail_flank 43-88", {**REST, "tail_flank": (43.0, 88.0)}, False),
    ("C2 tail_flank 40-90", {**REST, "tail_flank": (40.0, 90.0)}, False)]


def _no_crease_fade_check(monkeypatch):
    """Run 2's r0015 fades every facet together across creases that reach the outline (E_FADE_CREASE since run 7,
    test_run2_and_run6_fades_die_before_the_edge): these probes test the hold, cliff, inert and planarity mechanics
    on that layout, so they turn the crease guard off."""
    from glasses3d.g3d.temple import build as BD
    monkeypatch.setattr(BD, "_check_fade_creases", lambda *a, **k: None)


def _r0015(fades) -> dict:
    if not R0015.exists():
        pytest.skip("facet-astra-002 revision r0015 not present (local evidence)")
    spec = json.loads(R0015.read_text(encoding="utf-8"))
    if fades is not None:
        for k in spec["facets"]:
            f = fades.get(k)
            spec["facets"][k]["fade"] = None if f is None else {"d0": f[0], "d1": f[1]}
    return spec


@pytest.mark.parametrize("case, fades, facet, neighbour, kind", R15_CLIFFS, ids=[c[0] for c in R15_CLIFFS])
def test_astra_r0015_held_walls_are_refused(monkeypatch, case, fades, facet, neighbour, kind):
    """A facet fading while a neighbour stands (or fades later) hides a wall in one row of triangles: E_FADE_CLIFF with
    the facet, the neighbour holding it, the d range, the worst point, the hold and the tilt. The three roll cases are
    walls in the roll band only (the reviewer's defect 2: an outer-only check built them)."""
    _no_crease_fade_check(monkeypatch)
    spec = _r0015(fades)
    with pytest.raises(TempleError) as e:
        build_temple(spec)
    f = e.value.fields
    assert e.value.code == "E_FADE_CLIFF" and (f["facet"], f["neighbour"]) == (facet, neighbour)
    assert e.value.message.startswith(f"facet {facet} fades but {neighbour} holds it {abs(f['hold_mm']):.2f} mm ")
    assert e.value.message.endswith("; start the fade where the crease ends")   # never "fade the neighbour too"
    assert f["d_mm"][0] <= f["at"][0] <= f["d_mm"][1]
    _no_fade_check(monkeypatch)
    rows = _tilts(build_temple(spec))
    counted = [x for x in rows if x[1] >= FADE_HOLD_MIN_MM]
    worst = max(counted, key=lambda x: x[0])
    assert f["tilt_deg"] == pytest.approx(worst[0], abs=1e-9) and worst[0] > 25.0 and worst[2] == facet
    assert worst[3] == kind and f["at"] == pytest.approx(list(worst[4:]), abs=1e-9)
    assert sum(g["triangles"] for g in f["failures"]) == sum(x[0] > 25.0 for x in counted)
    assert abs(f["hold_mm"]) == pytest.approx(max(x[1] for x in counted if x[0] > 25.0), abs=1e-9)
    assert abs(f["hold_mm"]) >= 0.1                                    # a step of photo pixels, not a sliver
    if case.startswith("roll"):
        assert all(x[0] <= 25.0 for x in counted if x[3] == "outer")


@pytest.mark.parametrize("case, fades, warns", R15_BUILDS, ids=[c[0] for c in R15_BUILDS])
def test_astra_r0015_fades_that_build(monkeypatch, case, fades, warns):
    """Fades alike or nearly alike build (the crease guard off: _no_crease_fade_check): no counted tilt over 25 deg;
    W_FADE_HOLD where the largest counted tilt is >= 10 deg (B: 16 deg on a 0.07 mm hold). B and C2 keep the
    micron-wide slivers the reviewer found (> 25 deg on holds under 0.05 mm, defect 1): invisible, never counted."""
    _no_crease_fade_check(monkeypatch)
    tb = build_temple(_r0015(fades))
    rows = _tilts(tb)
    counted = [x for x in rows if x[1] >= FADE_HOLD_MIN_MM]
    worst = max((x[0] for x in counted), default=0.0)
    assert worst <= 25.0 and (worst >= 10.0) == warns
    assert [w["code"] for w in tb.warnings if w["code"] == "W_FADE_HOLD"] == (["W_FADE_HOLD"] if warns else [])
    if case[:2] in ("B ", "C2"):
        slivers = [x for x in rows if x[0] > 25.0]
        assert slivers and all(x[1] < FADE_HOLD_MIN_MM for x in slivers)
    inert = [w for w in tb.warnings if w["code"] == "W_FADE_INERT"]    # crown, hinge_flank, logo end near d 40-47
    assert len(inert) == 1 and inert[0]["facets"] == ["crown", "hinge_flank", "logo"]


def test_astra_r0015_logo_fade_alone_is_inert():
    tb = build_temple(_r0015({"logo": (30.0, 60.0)}))                 # every logo vertex is on a standing neighbour
    assert not any(w["code"] == "W_FADE_HOLD" for w in tb.warnings)
    inert = [w for w in tb.warnings if w["code"] == "W_FADE_INERT"]
    assert len(inert) == 1 and inert[0]["why"] == {"logo": "held by less-faded neighbours"}


def test_astra_r0015_flattening_fades_are_refused(monkeypatch):
    """Run 2's r0015 with every facet fading alike, early. E_FADE_CREASE refuses each first: the creases die before
    the edge they reach. With that guard off, the crease check refuses a fade that flattens a declared ridge: flat,
    facts only (it named the fade as the remedy before WP6_V3 P6). (25, 65) flattens rear_bevel|tail_flank to 1.07
    deg; (20, 60) flattens crown|rear_bevel to 1.95 (and rear_bevel|tail_flank to 0.41).
    (30, 70) was the rear_bevel|tail_flank case until v3 phase 2. Since then the crease mean is taken face to face
    (build.face_edge, the edges range_deg uses; v3p2-builder2, its request 1). There the crease is a 2.14 deg ridge
    over its 40.3 mm of face edges, so it builds as declared. Over all 47.8 mm, roll band included, it is the 1.80
    the check refused before. (25, 65) keeps the refusal of that crease."""
    from glasses3d.g3d.temple import build as BD
    cases = {(30.0, 70.0): None, (25.0, 65.0): ["rear_bevel", "tail_flank"], (20.0, 60.0): ["crown", "rear_bevel"]}
    for a, b in cases:                                                 # every facet alike, early: the creases die
        with pytest.raises(TempleError) as e:                          # before the edge they reach: refused first
            build_temple(_r0015({k: (a, b) for k in R15_FACETS}))
        assert e.value.code == "E_FADE_CREASE"
    _no_crease_fade_check(monkeypatch)
    for (a, b), first in cases.items():                                # the crease check: flat, facts only
        if first is None:
            continue
        with pytest.raises(TempleError) as e:
            build_temple(_r0015({k: (a, b) for k in R15_FACETS}))
        assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["built"] == "flat"
        assert e.value.fields["facets"] == first and e.value.fields["dihedral_deg"] < FLAT_CREASE_DEG
        assert e.value.message.endswith("(flat (< 2 deg): no ridge or valley)")
        assert not any(r in e.value.message for r in REMEDIES)
    tb = build_temple(_r0015({k: (30.0, 70.0) for k in R15_FACETS}))   # face to face: a 2.1 deg ridge, as declared
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["rear_bevel", "tail_flank"])
    assert c["shape"] == c["declared"] == "ridge" and FLAT_CREASE_DEG <= c["mean_dihedral_deg"] < 2.3
    signed = [(float(np.linalg.norm(tb.V[p] - tb.V[q])), 0.0 if ridge is None else (deg if ridge else -deg))
              for p, q, pair, deg, ridge, _p1, _p2 in BD.crease_edges(tb) if pair == ("rear_bevel", "tail_flank")]
    every = sum(L * s for L, s in signed) / sum(L for L, _ in signed)
    assert sum(L for L, _ in signed) == pytest.approx(c["length_mm"], abs=1e-9)
    assert c["length_mm"] > c["face_length_mm"] + 5.0                  # 47.8 mm, 40.3 face to face
    assert 1.7 < every < FLAT_CREASE_DEG                               # 1.80 with its roll-band edges: flat


@pytest.mark.parametrize("k", range(1, 17))
def test_facet_astra_002_revisions_build(monkeypatch, k):
    """Run 2's revisions: 1-10 build; 11-16 fade every facet together (40-85) across creases that run on into the
    fade (rear_bevel|tail_flank to the top outline near d 94, crown|rear_bevel, ...): E_FADE_CREASE since run 7. With
    that guard off every one still builds closed without W_FADE_HOLD (the builder's own regression on real specs)."""
    p = R0015.parent / f"r{k:04d}.json"
    if not p.exists():
        pytest.skip(f"facet-astra-002 revision r{k:04d} not present (local evidence)")
    spec = json.loads(p.read_text(encoding="utf-8"))
    if k >= 11:
        with pytest.raises(TempleError) as e:
            build_temple(spec)
        assert e.value.code == "E_FADE_CREASE" and ["rear_bevel", "tail_flank"] in e.value.fields["creases"]
        _no_crease_fade_check(monkeypatch)
    tb = build_temple(spec)
    assert tb.checks["closed"] and not any(w["code"] == "W_FADE_HOLD" for w in tb.warnings)


def test_astra_r0015_facets_are_planar_before_the_fade(monkeypatch):
    """The paid run facet-astra-002's final spec (every facet fades over d 40-85; the crease guard off,
    _no_crease_fade_check): its logo, crown and hinge_flank cells before d 40 are planar facet cells with face normals
    (not auto-smoothed triangles), and every crease between two facets is sharp wherever it stands."""
    if not R0015.exists():
        pytest.skip("facet-astra-002 revision r0015 not present (local evidence)")
    _no_crease_fade_check(monkeypatch)
    tb = build_temple(json.loads(R0015.read_text(encoding="utf-8")))
    V = tb.arr.V
    flat = set(tb.flat_polys)
    for lab in ("logo", "crown", "hinge_flank"):
        faces = {fi for fi, l in enumerate(tb.face_label) if l == lab and V[tb.arr.faces[fi], 0].max() <= 40.0 + 1e-9}
        assert faces and all(tb.face_cell[fi] == "facet" for fi in faces)
        outer = [i for i, (k, fi) in enumerate(zip(tb.poly_kind, tb.poly_face)) if k == "outer" and fi in faces]
        assert outer and set(outer) <= flat
        pl = tb.sol.planes[lab].coef
        n = np.array([-pl[1], -pl[2], 1.0]) / np.linalg.norm([-pl[1], -pl[2], 1.0])
        assert max(math.degrees(math.acos(min(1.0, float(tb.normals[i] @ n)))) for i in outer) <= 1e-4
    topo = mesh_topology(tb.polys)
    sharp = set(tb.sharp)
    logo = 0
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        r = {tb.poly_region[p1].split("/")[0], tb.poly_region[p2].split("/")[0]}
        if a < b and "logo" in r and len(r) == 2 and {tb.poly_kind[p1], tb.poly_kind[p2]} <= {"outer", "roll"}:
            assert (a, b) in sharp                                    # the logo ends near d 47, where s < 0.06
            logo += 1
    assert logo > 90
    fc = faded_corner_normals(tb)                     # the roll lines of the faded cells shade as the design surface
    roll = [(i, v) for (i, v) in fc if tb.poly_kind[i] == "roll"]
    assert len(fc) > 600 and len(roll) > 150


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
    assert [(r["facet"], r["d_mm"]) for r in tb.tables["border_ramps"]] == [("F2", [0.0, 46.8]), ("F4", [46.8, 55.0])]
    for r in tb.tables["border_ramps"]:                          # one row per border run, signed: B stands proud
        assert r["height_mm"] == pytest.approx(0.6, abs=1e-9)
        assert r["slope_deg"] == pytest.approx(math.degrees(math.atan(0.3)), abs=1e-9)
    assert {w["facet"] for w in tb.warnings if w["code"] == "W_BORDER_RAMP"} == {"F2", "F4"}
    sunk = build_temple(FX.border_ramp(proud_b=-0.6))
    assert [r["height_mm"] for r in sunk.tables["border_ramps"]] == pytest.approx([-0.6, -0.6], abs=1e-9)
    assert all("below the base by 0.60 mm" in w["message"] for w in sunk.warnings if w["code"] == "W_BORDER_RAMP")
    assert all(abs(r["height_mm"]) < 1e-9 for r in builds["pyramid"].tables["border_ramps"])
    assert not any(w["code"] == "W_BORDER_RAMP" for w in builds["pyramid"].warnings)
    for i, k in enumerate(tb.face_cell):                         # ramp cells touch the band only
        if k == "ramp":
            assert np.min(dist[tb.arr.faces[i]]) < band


def test_border_ramps_are_reported_per_run():
    # a band facet across the slab, tilted in d: 0.3 mm proud along its front border, 0.3 mm sunk along its rear one
    s = FX.straight(nodes={"P": [20.0, 12.0, 4.3], "Q": [20.0, -12.0, 4.3], "R": [30.0, -12.0, 3.7],
                           "S": [30.0, 12.0, 3.7]}, facets={"band": ["P", "Q", "R", "S"]})
    tb = build_temple(s)
    rows = [(r["facet"], r["d_mm"], round(r["height_mm"], 9), round(r["length_mm"], 9))
            for r in tb.tables["border_ramps"]]
    assert rows == [("band", [20.0, 20.0], 0.3, 20.0), ("band", [30.0, 30.0], -0.3, 20.0)]
    msgs = [w["message"] for w in tb.warnings if w["code"] == "W_BORDER_RAMP"]
    assert len(msgs) == 2 and "proud of the base by 0.30 mm at d 20.0:" in msgs[0] and "below" in msgs[1]
    r = report.receipt(tb)["border_ramps"]
    assert r == [{"facet": "band", "d_mm": [20.0, 20.0], "height_mm": 0.3, "slope_deg": 8.5, "length_mm": 20.0},
                 {"facet": "band", "d_mm": [30.0, 30.0], "height_mm": -0.3, "slope_deg": 8.5, "length_mm": 20.0}]
    texts = ["band proud 0.30 mm: a 8.5° ramp at d 20.0", "band sunk 0.30 mm: a 8.5° ramp at d 30.0"]
    assert [report.ramp_text(x) for x in r] == texts                 # the receipt's rows, the tables' rows and the
    assert [report.ramp_text(x) for x in tb.tables["border_ramps"]] == texts                  # warnings: one text
    assert [report.ramp_text(w) for w in tb.warnings if w["code"] == "W_BORDER_RAMP"] == texts


def test_border_ramp_text_has_units_and_words():
    """report.ramp_text / ramp_clause: a border-ramp run in words and units, the form of crease_line's step clause.
    v3 session2 item 8: the lab printed "shoulder +0.81 7.7° d 75.5-76.7" (a bare signed number) beside the crease
    line "proud 0.81 mm: a 7.7° ramp" of the same run. The step clause in crease_line is unchanged (the lab finds it
    there: lab.step_text, crease_head)."""
    run = {"facet": "shoulder", "d_mm": [75.5, 76.7], "height_mm": 0.81, "slope_deg": 7.7, "length_mm": 1.4}
    assert report.ramp_text(run) == "shoulder proud 0.81 mm: a 7.7° ramp at d 75.5-76.7"
    assert report.ramp_text({**run, "height_mm": -1.244, "d_mm": [40.04, 39.96]}) == \
        "shoulder sunk 1.24 mm: a 7.7° ramp at d 40.0"                # one d when both ends print alike
    assert report.ramp_clause(-1.0, 26.6) == "sunk 1.00 mm: a 26.6° ramp"
    c = {"regions": ["F", "base"], "shape": "ridge", "mean_dihedral_deg": 2.9, "declared": None,
         "step": {"height_mm": -1.0, "slope_deg": 26.6}}
    assert report.crease_line(c) == "F|base ridge 2.9° (sunk 1.00 mm: a 26.6° ramp) undeclared"
    assert f" ({report.ramp_clause(-1.0, 26.6)})" in report.crease_line(c)


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
    fit = {"E_FIT_SHAPE", "E_FIT"}                            # WP6_V3 §2: fit.py raises them, TempleError accepts them
    assert core | blender_side | internal | fit == set(REFUSALS)
    assert not core & (blender_side | internal | fit)


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
    assert "stations" not in r                                         # §4.5: facet_inspect asks for the table
    rs = report.receipt(tb, core_ms=12.345, include_stations=True)
    rows = rs["stations"]["rows"]
    assert rs["stations"]["columns"] == ["d", "y_top", "y_bot", "x_in", "t_top", "t_bot"]
    assert rows[1][0] - rows[0][0] == 10.0 and rows[-1][0] == tb.spec.d1
    assert {k: v for k, v in rs.items() if k != "stations"} == r
    assert r["thickness"]["min_mm"] == round(tb.tables["thickness"]["min_mm"], 2)
    assert r["checks"] == {"closed": True, "euler": 2, "volume_mm3": round(tb.checks["volume_mm3"], 1), "planar": True}
    assert r["budget"]["core_ms"] == 12.3 and r["spec_sha256"] == tb.spec.sha256
    assert len(text) <= 1500, len(text)                                # 1377 chars on 2026-10-11 (2061 with stations)
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


# --------------------------------------------------------------------------------------------- run 7
R6_REV13 = REPO / "glasses3d/data/wp6v2/lab/package_006/work/temple_lab/revisions/r0013.json"
PRISM_T = FX.PRISM["T"]


def test_fade_crease_refuses_a_crease_that_dies_before_the_edge_it_reaches():
    """E_FADE_CREASE (run 6: the back faces faded from d 48 across creases running to d 75-90, and the top triangle
    read smooth, not one flat piece; the owner, run 2: "the each triangle line is very soft"): two facets both faded
    (weight >= FADE_CREASE_WEIGHT) anywhere along their shared crease are refused, naming the crease, its d span, the
    point, both fades and the fix. The review's evasion (the crease's end moved just inside the outline, the fades
    unchanged) is refused too. One facet standing, or fades that start where the crease ends, build; the weight rule
    is the smaller weight of the two, at FADE_CREASE_WEIGHT."""
    with pytest.raises(TempleError) as e:
        build_temple(FX.refusal_cases()["E_FADE_CREASE"])              # the ridge runs on through the fade
    f = e.value.fields
    assert e.value.code == "E_FADE_CREASE" and f["facets"] == ["N", "S"] and 30.0 < f["at"][0] < 36.0
    assert f["span_d"] == pytest.approx([0.0, 60.0]) and f["creases"] == [["N", "S"]]
    assert e.value.message.startswith("crease N|S (d 0.0-60.0) fades out along its length: both facets fade at (d, y) (")
    assert e.value.message.endswith("(N 30-60, S 30-60); a crease stands to its ends: start the fades where it ends, "
                                    "or let one of them stand")          # never "end it at a node" (review round 2)
    rev = FX.fade(ridge_to=60.0)                                       # fading toward the hinge: named where it starts
    for lab in ("N", "S"):
        rev["facets"][lab]["fade"] = {"d0": 60.0, "d1": 30.0}
    rev["creases"] = []
    with pytest.raises(TempleError) as e:
        build_temple(rev)
    assert 54.0 < e.value.fields["at"][0] < 60.0 and e.value.fields["weight"] < 0.2
    for ridge_from in (0.25, 5.0):                                     # the end moved inside: still fading along it
        inside = FX.fade(ridge_from=ridge_from)                        # (ridge to d 30, fading toward d 0)
        for lab in ("N", "S"):
            inside["facets"][lab]["fade"] = {"d0": 30.0, "d1": 0.0}
        inside["creases"] = []
        with pytest.raises(TempleError) as e:
            build_temple(inside)
        assert e.value.code == "E_FADE_CREASE"
    one = FX.fade_one_side()                                           # S stands: the ridge stands to the cap
    one["facets"]["N"]["fade"] = {"d0": 30.0, "d1": 0.0}
    build_temple(one)
    later = FX.fade(ridge_to=60.0)                     # both faded facets, S from where the ridge ends: the smaller
    later["facets"]["S"]["fade"] = {"d0": 60.0, "d1": 90.0}            # weight along it is S's, 0 (never the larger)
    build_temple(later)
    ends = FX.fade(ridge_from=30.0, ridge_to=60.0)                     # the fades start where the ridge ends
    for lab in ("N", "S"):
        ends["facets"][lab]["fade"] = {"d0": 30.0, "d1": 0.0}
    build_temple(ends)
    build_temple(FX.fade())                                            # the ridge ends at d 30, where they fade
    # the weight rule: the smaller weight of the two at FADE_CREASE_WEIGHT (smoothstep 3x^2 - 2x^3 of x = d / 30)
    from glasses3d.g3d.temple.spec import FADE_CREASE_WEIGHT
    x = np.linspace(0.0, 0.5, 100001)
    xc = float(x[np.argmax(3 * x ** 2 - 2 * x ** 3 >= FADE_CREASE_WEIGHT)])
    for N0, ok in ((30.0 * xc - 0.1, True), (30.0 * xc + 0.3, False)):  # N fades 0->30 forward; S from N0 later
        s = FX.fade(ridge_to=60.0)
        s["facets"]["N"]["fade"] = {"d0": 60.0 - N0, "d1": 90.0 - N0}   # the ridge ends at d 60
        s["facets"]["S"]["fade"] = {"d0": 60.0 - N0, "d1": 90.0 - N0}
        s["creases"] = []
        if ok:
            build_temple(s)
        else:
            with pytest.raises(TempleError) as e:
                build_temple(s)
            assert e.value.code == "E_FADE_CREASE"


def test_run2_and_run6_fades_die_before_the_edge():
    """Run 6's best revision faded Crown, Lower and Tail 48-82 across Lower|Tail (d 47-75) and Crown|Tail (to d 90):
    refused now (local evidence; run 2's r0011-16 in test_facet_astra_002_revisions_build)."""
    if not R6_REV13.exists():
        pytest.skip("facet-astra-006 revision r0013 not present (local evidence)")
    with pytest.raises(TempleError) as e:
        build_temple(json.loads(R6_REV13.read_text(encoding="utf-8")))
    assert e.value.code == "E_FADE_CREASE" and e.value.fields["creases"] == [["Lower", "Tail"], ["Crown", "Tail"]]
    assert e.value.fields["span_d"] == pytest.approx([47.3, 75.0], abs=1e-6)


def test_crease_plane_holds_a_straight_run_of_edges():
    """A crease + rise plane through the ends of a straight run of the poly's edges (spec.crease_run; run 6's crown,
    a thin band whose lower edge A-C-E runs straight through the logo corner C): the plane holds the 3D line L-R and
    rises into the facet whichever way the poly is listed; the corner M on the run takes the plane's value (solved by
    design); a run bent more than RUN_LINE_MM is refused E_SPEC; an edge stays a crease as before."""
    from glasses3d.g3d.temple.spec import RUN_LINE_MM, crease_run, parse
    s = FX.crease_run_band()
    tb = build_temple(s)
    ps, pl = tb.spec, tb.sol.planes["B"]
    XL, XR = ps.node_X("L"), ps.node_X("R")
    for d in (0.0, 30.0, 60.0, 120.0):                                 # the line L-R at y 4
        assert pl.X(d, 4.0) == pytest.approx(XL + (XR - XL) * d / 120.0, abs=1e-9)
    assert pl.X(60.0, 14.0) - pl.X(60.0, 4.0) == pytest.approx(-0.6 * 10.0, abs=1e-9)   # rise into the band (up)
    assert tb.sol.node_X["M"] == pytest.approx(pl.X(60.0, 4.0), abs=1e-9)
    rev = FX.crease_run_band()
    rev["facets"]["B"]["poly"] = rev["facets"]["B"]["poly"][::-1]    # clockwise listing: the same plane
    assert build_temple(rev).sol.planes["B"].coef == pytest.approx(pl.coef, abs=1e-12)
    rev = FX.crease_run_band()
    rev["facets"]["B"]["plane"]["crease"] = ["R", "L"]                 # the ends named the other way: the same plane
    assert build_temple(rev).sol.planes["B"].coef == pytest.approx(pl.coef, abs=1e-12)
    assert crease_run(["L", "M", "R", "U1", "U0"], "L", "R", ps.nodes) == (1, ["M"])
    assert crease_run(["L", "M", "R", "U1", "U0"], "R", "L", ps.nodes) == (-1, ["M"])
    assert crease_run(["L", "M", "R", "U1", "U0"], "L", "M", ps.nodes) == (1, [])
    one = FX.crease_run_band(mid_off=0.9 * RUN_LINE_MM)               # a run within RUN_LINE_MM is one line for B
    del one["facets"]["W"]                                             # alone (base below it)
    for n in ("B0", "B1"):
        del one["nodes"][n]
    parse(one)
    for w_plane in ({"crease": ["R", "L"], "rise_per_mm": 0.1}, "nodes", {"crease": ["R", "M"], "rise_per_mm": 0.1}):
        both = FX.crease_run_band(mid_off=0.3)                         # W meets B along L-M-R (a run, its nodes, an
        both["facets"]["W"]["plane"] = w_plane                         # edge crease): they could only be one plane
        with pytest.raises(TempleError) as e:
            parse(both)
        assert e.value.code == "E_SPEC" and e.value.fields["node"] == "M" and e.value.fields["facets"] == ["B", "W"]
        assert e.value.message.endswith("whose corner M lies 0.3 mm off its line: they could only be one plane; put M "
                                        "on the line L-R")
    tiny = FX.crease_run_band(mid_off=1e-4)                            # never printed as "0.000 mm"
    with pytest.raises(TempleError) as e:
        parse(tiny)
    assert "whose corner M lies 0.0001 mm off its line" in e.value.message
    # the same check after the node snaps: M exactly on the line until its d snaps to a knot (K's d)
    snapped = FX.crease_run_band()
    snapped["nodes"]["R"] = [120.0, 10.0, FX.PRISM["T"] + 1.0]
    snapped["nodes"]["M"] = [60.03, 4.0 + 6.0 * 60.03 / 120.0, None]
    snapped["nodes"]["K"] = [60.0, -5.0, None]
    with pytest.raises(TempleError) as e:
        parse(snapped)
    assert e.value.code == "E_SPEC" and "after the snaps (node M d 60.03->60)" in e.value.message
    with pytest.raises(TempleError) as e:
        parse(FX.crease_run_band(mid_off=1.1 * RUN_LINE_MM))
    assert e.value.code == "E_SPEC" and "a straight run of its edges" in e.value.message
    # a band thinner than RUN_LINE_MM: both runs between L and R are straight; the closer one is the crease's, so the
    # rise points into the band whichever way the ends are named or the poly listed
    nodes = {"L": [0.0, 4.0], "M": [60.0, 4.0], "R": [120.0, 4.0], "U1": [120.0, 4.4], "U0": [0.0, 4.4]}
    poly = ["L", "M", "R", "U1", "U0"]
    assert crease_run(poly, "L", "R", nodes) == (1, ["M"]) and crease_run(poly, "R", "L", nodes) == (-1, ["M"])
    assert crease_run(poly[::-1], "L", "R", nodes) == (-1, ["M"]) and crease_run(poly[::-1], "R", "L", nodes) == \
        (1, ["M"])
    tie = {**nodes, "M": [60.0, 3.8], "U1": [120.0, 4.2], "U0": [0.0, 4.2]}  # both runs 0.2 mm off: the same run
    assert {tuple(crease_run(pl, a, b, tie)[1]) for pl in (poly, poly[::-1]) for a, b in (("L", "R"), ("R", "L"))} \
        == {("M",)}
    # a node snap that bends a run past RUN_LINE_MM after the spec's own check: E_SPEC, never a TypeError
    snap = FX.straight(nodes={"L": [0.0, -9.48, PRISM_T + 1.0], "M": [60.0, -9.955, None],
                              "R": [120.0, -9.48, PRISM_T + 1.0], "U0": [0.0, 30.0, None], "U1": [120.0, 30.0, None]},
                       facets={"B": {"poly": ["L", "M", "R", "U1", "U0"],
                                     "plane": {"crease": ["L", "R"], "rise_per_mm": -0.2}}})
    with pytest.raises(TempleError) as e:
        build_temple(snap)
    assert e.value.code == "E_SPEC" and "no longer a straight run" in e.value.message and "node M y" in e.value.message
    # every way out (v3p3-review-e2e P3-2: "move the corners between onto the line" was the one remedy)
    assert e.value.message.endswith("; ways: the corners between onto the line (within 0.5 mm), the crease on one edge "
                                    "of the facet, or another plane")
    with pytest.raises(TempleError) as e:                              # U0-R: neither run between them is straight
        bad = FX.crease_run_band()
        bad["facets"]["B"]["plane"]["crease"] = ["U0", "R"]
        parse(bad)
    assert e.value.code == "E_SPEC"


def test_solve_planes_matches_the_build():
    """solve_planes (the lab's refusal notes) gives the build's own planes and node values."""
    from glasses3d.g3d.temple.build import solve_planes
    for s in (FX.pyramid(), FX.crease_run_band(), FX.solved_node()):
        tb = build_temple(s)
        _ps, sol = solve_planes(s)
        assert sol.node_X == pytest.approx(tb.sol.node_X, abs=0.0)
        for lab, plane in tb.sol.planes.items():
            assert sol.planes[lab].coef == pytest.approx(plane.coef, abs=0.0)
