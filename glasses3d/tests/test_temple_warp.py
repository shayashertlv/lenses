"""WP6 v3 builder (plan/WP6_V3.md §2): facet plane modes {"coef": [a, b, c]} and "warp" (mean value coordinates over
the facet's corners), the facet key "soft", the one-tolerance fix and the facet|base crease against the BASE surface.

Specs here are tool-test geometry on the fixture slab (fixtures.straight: d 0-120, y -10..10, inner face X 60, base
thickness 4) or the fixture's Prada-like outline (fixtures.base_spec), never a product.
"""
from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import draw as DR
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import planes as PL
from glasses3d.g3d.temple import report as RP
from glasses3d.g3d.temple.build import (CREASE_STANDS_DEG, OUTER_KINDS, design_corner_normals, design_dihedral,
                                        design_normal, mesh_topology)
from glasses3d.g3d.temple.spec import (SOFT_BAND_MM, SOFT_SHARP_DEG, TempleError, canonical, parse, spec_sha256)

T = FX.PRISM["T"]                       # slab base thickness 4 (X 64), inner face X 60
X_IN = FX.PRISM["x_in"]


# --------------------------------------------------------------------------------------------- specs (tool tests)
def quad(raise_mm: float = 1.5, plane="warp", proud: float = 1.0, tilt: float = 0.0, **facet) -> dict:
    """A quad Q = A (10, -6), B (40, -6), C (40, 6), D (10, 6) on the slab, `proud` mm above the base, rising `tilt`
    mm per mm of y, with C a further `raise_mm` out (a 4-corner warp: one corner off the plane of the other three)."""
    t = lambda y, extra=0.0: T + proud + tilt * y + extra        # noqa: E731
    nodes = {"A": [10.0, -6.0, t(-6.0)], "B": [40.0, -6.0, t(-6.0)], "C": [40.0, 6.0, t(6.0, raise_mm)],
             "D": [10.0, 6.0, t(6.0)]}
    return FX.straight(nodes=nodes, facets={"Q": {"poly": ["A", "B", "C", "D"], "plane": plane, **facet}})


def with_neighbour(raise_mm: float = 1.5, plane="warp") -> dict:
    """quad() plus a flat facet N above it, sharing Q's edge D-C (C the raised corner) and reaching the top edge:
    N = D C E F, a plane through the 3D line D-C falling 0.25 mm per mm into N (E, F solved)."""
    s = quad(raise_mm, plane)
    s["nodes"].update({"E": [40.0, 10.0, None], "F": [10.0, 10.0, None]})
    s["facets"]["N"] = {"poly": ["D", "C", "E", "F"], "plane": {"crease": ["D", "C"], "rise_per_mm": -0.25}}
    return s


def polygon_warp(n: int) -> dict:
    """A 5- or 6-corner warp on the slab (the hexagon has a reflex corner), corner values off one plane."""
    if n == 5:
        P = [(12.0, -7.0), (45.0, -8.0), (55.0, 1.0), (35.0, 8.0), (14.0, 6.0)]
        t = [1.0, 1.4, 2.6, 1.2, 0.8]
    else:
        P = [(12.0, -7.0), (45.0, -8.0), (55.0, 1.0), (40.0, 8.0), (30.0, 2.0), (14.0, 7.0)]   # (30, 2): reflex
        t = [1.0, 1.4, 2.6, 1.2, 2.2, 0.8]
    nodes = {f"P{i}": [p[0], p[1], T + ti] for i, (p, ti) in enumerate(zip(P, t))}
    return FX.straight(nodes=nodes, facets={"W": {"poly": list(nodes), "plane": "warp"}})


def prada_warp() -> dict:
    """The Blender export case on the fixture's Prada-like outline: a warp facet W (P1 P2 P3 P4, P3 1.5 mm off the
    plane of the others) above a soft flat facet S sharing W's edge P1-P2 (S's corners Q1, Q2 under it)."""
    s = FX.base_spec()
    tz = FX.T_ZONE
    s["nodes"] = {"P1": [6.0, 2.0, tz + 1.0], "P2": [52.0, 1.0, tz + 1.0], "P3": [56.0, 9.5, tz + 0.6 + 1.5],
                  "P4": [8.0, 9.0, tz + 0.6], "Q1": [7.0, -7.0, tz + 0.2], "Q2": [44.0, -5.0, None]}
    s["facets"] = {"W": {"poly": ["P1", "P2", "P3", "P4"], "plane": "warp"},
                   "S": {"poly": ["Q1", "Q2", "P2", "P1"], "plane": "nodes", "soft": True}}
    return s


def facet_vertices(tb, label: str) -> list:
    return sorted({v for fi, f in enumerate(tb.arr.faces) if tb.face_label[fi] == label for v in f})


def on_segment(p, a, b, tol: float = 1e-9) -> float | None:
    """The parameter of p on the segment a-b when p lies on it (within tol), else None."""
    a, b, p = (np.asarray(x, float) for x in (a, b, p))
    u = b - a
    s = float((p - a) @ u / (u @ u))
    if -1e-12 <= s <= 1 + 1e-12 and np.hypot(*(a + s * u - p)) <= tol:
        return s
    return None


# --------------------------------------------------------------------------------------------- coef
def test_coef_plane_builds_as_its_nodes_plane():
    """{"coef": [a, b, c]} = the plane a nodes facet solved: the same build bit for bit (V, polygons, sharp set,
    values), also with the corners' t solved (they take the plane)."""
    a = build_temple(FX.pyramid())
    s = FX.pyramid()
    s["facets"]["F2"] = {"poly": ["A", "B", "C"], "plane": {"coef": [float(x) for x in a.sol.planes["F2"].coef]}}
    b = build_temple(s)
    assert b.sol.planes["F2"].kind == "coef" and b.sol.planes["F2"].defining == []
    assert np.array_equal(a.V, b.V) and a.polys == b.polys and a.sharp == b.sharp
    assert [c for c in a.tables["creases"]] == [c for c in b.tables["creases"]]
    s2 = copy.deepcopy(s)
    s2["nodes"]["C"][2] = None                                     # C solved: it takes the coef plane
    c = build_temple(s2)
    assert np.abs(c.V - a.V).max() <= 1e-12 and c.polys == a.polys


def test_coef_plane_refuses_a_corner_off_it():
    s = FX.pyramid()
    a = build_temple(s)
    coef = [float(x) for x in a.sol.planes["F2"].coef]
    coef[0] += 2e-6                                                # 2e-6 mm off every corner: > planar_tol_mm
    s["facets"]["F2"] = {"poly": ["A", "B", "C"], "plane": {"coef": coef}}
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_FACET_NOT_PLANAR" and e.value.fields["facet"] == "F2"
    coef[0] -= 1.5e-6                                              # 5e-7 mm: within planar_tol_mm, it builds
    s["facets"]["F2"]["plane"]["coef"] = coef
    assert build_temple(s).checks["closed"]


def test_coef_and_warp_canonical_form_and_old_specs_keep_their_sha():
    s = quad()
    c = canonical(s)
    assert c["facets"]["Q"] == {"poly": ["A", "B", "C", "D"], "plane": "warp", "fade": None}
    s2 = quad(plane={"coef": [64, 0, 1]})
    assert canonical(s2)["facets"]["Q"]["plane"] == {"coef": [64.0, 0.0, 1.0]}
    for bad in ({"coef": [1, 2]}, {"coef": [1, 2, "x"]}, {"coef": [1, 2, 3], "rise_per_mm": 0.1}, "flat"):
        with pytest.raises(TempleError) as e:
            canonical(quad(plane=bad))
        assert e.value.code == "E_SPEC"
    # soft: written only when true; every earlier spec keeps its canonical form and sha256
    assert spec_sha256(quad(soft=False)) == spec_sha256(quad())
    assert canonical(quad(soft=True))["facets"]["Q"]["soft"] is True
    with pytest.raises(TempleError):
        canonical(quad(soft=1))
    for name, fn in FX.FIXTURES.items():
        assert "soft" not in str(canonical(fn())), name


# --------------------------------------------------------------------------------------------- warp
def test_coplanar_warp_is_the_flat_build():
    """A warp whose corner values lie on one plane builds the flat facet (WP6_V3 §2): the same arrangement and values
    within 1e-9 mm (no refinement), the same sharp set and crease table; only its cells are triangles."""
    for tilt in (0.0, 0.08):
        flat = build_temple(quad(0.0, "nodes", tilt=tilt))
        warp = build_temple(quad(0.0, "warp", tilt=tilt))
        assert np.array_equal(flat.arr.V, warp.arr.V)
        assert np.abs(flat.V - warp.V).max() <= 1e-9
        assert flat.sharp == warp.sharp
        assert {f for f in warp.face_cell if f != "base" and f != "ramp"} == {"warp"}
        fc = {tuple(c["regions"]): (c["shape"], c["mean_dihedral_deg"]) for c in flat.tables["creases"]}
        wc = {tuple(c["regions"]): (c["shape"], c["mean_dihedral_deg"]) for c in warp.tables["creases"]}
        assert fc.keys() == wc.keys()
        for k in fc:
            assert fc[k][0] == wc[k][0] and fc[k][1] == pytest.approx(wc[k][1], abs=1e-6)
        f = next(x for x in warp.tables["facets"] if x["label"] == "Q")
        assert f["warp_mm"] <= 1e-9 and f["chord_mm"] <= 1e-9


def test_four_corner_warp_one_corner_off():
    """C 1.5 mm off the plane of A, B and D: closed, Euler 2, the corners exact, every boundary vertex on the straight
    3D edge, every inside vertex the mean value coordinates value, chord error < 0.01 mm, no sharp edge inside and its
    crease edges sharp, smooth design normals on its corners (design_corner_normals)."""
    tb = build_temple(quad(1.5))
    assert tb.checks["closed"] and tb.checks["euler"] == 2 and tb.checks["volume_mm3"] > 0
    surf = tb.sol.planes["Q"]
    assert surf.kind == "warp" and surf.warp_mm == pytest.approx(1.5 / 4, abs=1e-9)   # a parallelogram's residuals
    ps, V2, S = tb.spec, tb.arr.V, tb.X_surface
    corners = {n: ps.inner(ps.nodes[n][0]) + ps.nodes[n][2] for n in "ABCD"}
    for n, X in corners.items():
        v = tb.sol.node_vertex[n]
        assert S[v] == pytest.approx(X, abs=1e-9)
    vs = facet_vertices(tb, "Q")
    poly = [np.array(ps.nodes[n][:2]) for n in "ABCD"]
    on_edge = 0
    for v in vs:
        for k in range(4):
            a, b = poly[k], poly[(k + 1) % 4]
            s = on_segment(V2[v], a, b)
            if s is not None:
                on_edge += 1
                Xa, Xb = corners["ABCD"[k]], corners["ABCD"[(k + 1) % 4]]
                assert S[v] == pytest.approx(Xa + s * (Xb - Xa), abs=1e-9)      # straight 3D edges
    assert on_edge >= 20
    assert np.abs(S[vs] - PL.mvc(surf.P, surf.F, V2[vs, 0], V2[vs, 1])).max() <= 1e-12
    f = next(x for x in tb.tables["facets"] if x["label"] == "Q")
    assert f["chord_mm"] < 0.01 and f["corners"][0]["node"] in "ABCD"
    # the refinement put more vertices inside than the 2 mm stations alone
    assert len(vs) > 2 * len(facet_vertices(build_temple(quad(0.0)), "Q"))
    topo = mesh_topology(tb.polys)
    inside = crease = 0
    sharp = set(tb.sharp)
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        if a > b or tb.poly_kind[p1] not in OUTER_KINDS or tb.poly_kind[p2] not in OUTER_KINDS:
            continue
        r1, r2 = tb.poly_region[p1], tb.poly_region[p2]
        if r1 == r2 == "Q":
            inside += 1
            assert (a, b) not in sharp
        elif "Q" in (r1, r2):
            m = 0.5 * (V2[a] + V2[b])
            if float(design_dihedral(ps, tb.sol, "Q", "base", m[0], m[1])) >= CREASE_STANDS_DEG:
                crease += 1
                assert (a, b) in sharp
    assert inside > 500 and crease >= 20
    cn = design_corner_normals(tb)
    want = design_normal(ps, tb.sol, "Q", V2[vs, 0], V2[vs, 1])
    by_v = dict(zip(vs, want))
    warp_polys = [i for i, c in enumerate(tb.poly_cell) if c == "warp"]
    assert all((i, v) in cn and np.allclose(cn[(i, v)], by_v[v], atol=1e-12) for i in warp_polys for v in tb.polys[i])


def fan_mean(tb, v: int, region: str) -> tuple[np.ndarray, list]:
    """(angle-weighted mean normal, [polygon]) of the outer polygons of `region` around vertex v."""
    fan = [i for i, p in enumerate(tb.polys) if v in p and tb.poly_kind[i] == "outer" and tb.poly_region[i] == region]
    acc = np.zeros(3)
    for i in fan:
        p = tb.polys[i]
        j = p.index(v)
        a, b = tb.V[p[j - 1]] - tb.V[v], tb.V[p[(j + 1) % len(p)]] - tb.V[v]
        acc += math.acos(float(np.clip(a @ b / np.linalg.norm(a) / np.linalg.norm(b), -1, 1))) * tb.normals[i]
    return acc / np.linalg.norm(acc), fan


def angle(a, b) -> float:
    return math.degrees(math.acos(float(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1.0, 1.0))))


def bent_corner(side: int = 1, bend: float = 1.5, off_deg: float = 5.0) -> dict:
    """A 5-corner warp W whose lower edge A-K-B bends at K by off_deg in (d, y) (side +1: K above the line A-B, a
    reflex corner like run 7's K; -1: convex) and K stands `bend` mm out."""
    dy = 20.0 * math.tan(math.radians(off_deg / 2.0))
    nodes = {"A": [10.0, -6.0, T + 1.0], "K": [30.0, -6.0 + side * dy, T + 1.0 + bend], "B": [50.0, -6.0, T + 1.0],
             "C": [50.0, 6.0, T + 1.0], "D": [10.0, 6.0, T + 1.0]}
    return FX.straight(nodes=nodes, facets={"W": {"poly": ["A", "K", "B", "C", "D"], "plane": "warp"}})


def test_warp_corner_normal_is_its_cone_mean():
    """At a corner mean value coordinates are only C0 (a cone): the corner's shading normal (design_corner_normals,
    corner_grad) is the mean of the cone's normals over its interior angle, within 1 deg of the angle-weighted mean
    of the built triangles there and within the cone's spread of each. Never the plane through the corner and its two
    neighbours: at a corner a few degrees from straight bent in X (run 7's K) that plane is nearly vertical
    (review-builder-correctness P1-1: 50-110 deg off the triangles at K, a blotch in every render and the GLB)."""
    for spec, corners in ((quad(1.5), "ABCD"), (bent_corner(+1), "K"), (bent_corner(-1), "K"),
                          (bent_corner(+1, 2.0, 1.7), "K")):
        tb = build_temple(spec)
        lab = next(iter(tb.sol.planes))
        surf = tb.sol.planes[lab]
        cn = design_corner_normals(tb)
        names = [c[0] for c in surf.corners]
        for name in corners:
            v = tb.sol.node_vertex[name]
            mean, fan = fan_mean(tb, v, lab)
            k = names.index(name)
            g = surf.corner_grad(k)
            n = np.array([-g[0], -g[1], 1.0])
            assert all(np.allclose(cn[(i, v)], n / np.linalg.norm(n), atol=1e-12) for i in fan)
            assert angle(n, mean) <= 1.0, (name, angle(n, mean))
            assert max(angle(n, tb.normals[i]) for i in fan) <= 6.0
            if name == "K":                             # the corner plane the v3 build used: far off
                P, F = surf.P, surf.F
                e1, e2 = P[k - 1] - P[k], P[(k + 1) % len(P)] - P[k]
                g3 = np.linalg.solve(np.array([e1, e2]), np.array([F[k - 1] - F[k], F[(k + 1) % len(P)] - F[k]]))
                assert angle(np.array([-g3[0], -g3[1], 1.0]), mean) > 30.0
    # off the corners the gradient is the central difference of X
    surf = build_temple(quad(1.5)).sol.planes["Q"]
    gd, gy = surf.grad(25.0, 0.0)
    h = 1e-4
    assert float(gd) == pytest.approx(float((surf.X(25 + h, 0.0) - surf.X(25 - h, 0.0)) / (2 * h)), abs=1e-7)
    gd, gy = surf.grad(*surf.P[2])                     # at a corner: corner_grad
    assert (float(gd), float(gy)) == pytest.approx(tuple(surf.corner_grad(2)), abs=1e-12)


def test_warp_neighbour_meets_without_a_step():
    """A flat neighbour N sharing the warp's raised edge D-C: their shared vertices agree (one vertex, one value; both
    surfaces within 1e-9 mm there), the Q|N crease is sharp along its length, closed, Euler 2."""
    tb = build_temple(with_neighbour(1.5))
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    q, n = set(facet_vertices(tb, "Q")), set(facet_vertices(tb, "N"))
    shared = sorted(q & n)
    assert len(shared) >= 10
    V2 = tb.arr.V
    for v in shared:
        xq = float(tb.sol.planes["Q"].X(V2[v, 0], V2[v, 1]))
        xn = float(tb.sol.planes["N"].X(V2[v, 0], V2[v, 1]))
        assert xq == pytest.approx(xn, abs=1e-9) and tb.X_surface[v] == pytest.approx(xq, abs=1e-9)
    rec = next(c for c in tb.tables["creases"] if c["regions"] == ["N", "Q"])
    assert rec["shape"] == "ridge" and rec["mean_dihedral_deg"] > 5.0
    topo = mesh_topology(tb.polys)
    pair_edges = [(a, b) for (a, b), p1 in topo["directed"].items() if a < b
                  and {tb.poly_region[p1], tb.poly_region[topo["directed"][(b, a)]]} == {"N", "Q"}]
    assert pair_edges and all(e in set(tb.sharp) for e in pair_edges)


@pytest.mark.parametrize("n", [5, 6])
def test_five_and_six_corner_warps(n):
    tb = build_temple(polygon_warp(n))
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    surf = tb.sol.planes["W"]
    assert len(surf.corners) == n and surf.warp_mm > 0.1
    for name, d, y, X in surf.corners:
        assert tb.X_surface[tb.sol.node_vertex[name]] == pytest.approx(X, abs=1e-9)
    f = next(x for x in tb.tables["facets"] if x["label"] == "W")
    assert f["chord_mm"] < 0.01
    vs = facet_vertices(tb, "W")
    assert np.abs(tb.X_surface[vs] - surf.X(tb.arr.V[vs, 0], tb.arr.V[vs, 1])).max() <= 1e-12


def test_warp_chord_error_on_a_two_mm_warp():
    """The design's chord claim, measured: 2 mm station strips alone leave 0.04-0.06 mm on a one-corner warp (mean
    value coordinates are only C0 at the corners); the refinement keeps every warp triangle within 0.01 mm of the
    surface (the build's own measure, chord_mm, and an independent dense sampling here)."""
    for raise_mm, tilt in ((2.0, 0.0), (2.0, 0.1), (-2.0, 0.0), (3.0, 0.05)):
        tb = build_temple(quad(raise_mm, tilt=tilt, proud=2.5))
        surf = tb.sol.planes["Q"]
        k = 10
        bc = np.array([[i, j, k - i - j] for i in range(k + 1) for j in range(k + 1 - i)], float) / k
        Tq = np.array([tb.polys[i] for i, c in enumerate(tb.poly_cell) if c == "warp"])
        P = np.einsum("sk,tkj->tsj", bc, tb.arr.V[Tq])
        lin = np.einsum("sk,tk->ts", bc, tb.X_surface[Tq])
        err = float(np.abs(lin - surf.X(P[..., 0], P[..., 1])).max())
        f = next(x for x in tb.tables["facets"] if x["label"] == "Q")
        assert err < 0.01 and f["chord_mm"] < 0.01, (raise_mm, tilt, err, f["chord_mm"])


def test_warp_refusals():
    s = quad()
    s["facets"]["Q"]["poly"] = ["A", "B", "C"]                     # three corners: always one plane
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_SPEC" and "warp" in e.value.message
    s = quad()
    s["facets"]["Q"]["poly"] = ["A", "C", "B", "D"]                # a bow-tie: not a simple polygon
    s["nodes"]["D"][:2] = [12.0, 7.0]                              # (asymmetric: its signed area is not 0)
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_SPEC" and "crosses itself" in e.value.message
    s = quad()
    s["nodes"]["A"] = [10.0, -30.0, None]                          # solved, outside the outline: no value there
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_FACET_UNDERDETERMINED" and e.value.fields["corners_without_value"] == ["A"]
    s = quad()
    s["nodes"]["D"][2] = None                                      # solved inside, but no other facet hands it on
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_FACET_UNDERDETERMINED" and e.value.fields["corners_without_value"] == ["D"]
    s = quad()                                                     # ... a neighbour's plane hands it on: it builds
    s["nodes"]["D"][2] = None
    s["nodes"].update({"E": [40.0, 10.0, None], "F": [10.0, 10.0, None]})
    xc = X_IN + T + 1.0 + 1.5                                      # N: a coef plane through C (the fit's form)
    s["facets"]["N"] = {"poly": ["D", "C", "E", "F"], "plane": {"coef": [xc - 0.02 * 40.0 + 0.1 * 6.0, 0.02, -0.1]}}
    tb = build_temple(s)
    assert tb.sol.planes["Q"].corners[3][0] == "D" and tb.sol.node_X["D"] == pytest.approx(
        float(tb.sol.planes["N"].X(10.0, 6.0)), abs=1e-12)
    # a T-junction: N meets only half of Q's edge D-C, its corner M on that edge; a value there off the edge's
    # linear interpolation (the edge D-C is at T + 1.75 at d 25) is a step
    s = quad(1.5)
    s["nodes"].update({"M": [25.0, 6.0, T + 1.0 + 0.75 + 0.3], "E": [25.0, 10.0, None], "F": [10.0, 10.0, None]})
    s["facets"]["N"] = {"poly": ["D", "M", "E", "F"], "plane": {"crease": ["D", "M"], "rise_per_mm": -0.1}}
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_RELIEF_STEP" and "linear interpolation" in e.value.message
    assert e.value.fields["facet"] == "Q" and e.value.fields["neighbours"] == ["N"]
    assert max(abs(m["misfit_mm"]) for m in e.value.fields["misfits"]) == pytest.approx(0.3)
    s["nodes"]["M"][2] = T + 1.0 + 0.75                            # on the edge's line: it builds
    tb = build_temple(s)
    assert tb.checks["closed"] and tb.checks["euler"] == 2


# --------------------------------------------------------------------------------------------- soft
def soft_spec(proud: float = 0.5, soft: bool = True, tilt: float = -0.03) -> dict:
    """A flat facet S on the slab (d 20-50, y -5..5), `proud` mm above the base at its lower corners and leaning by
    `tilt` mm per mm of y, with a neighbour facet G sharing its edge at d 50 (a facet|facet crease is never soft)."""
    t = lambda y: T + proud + tilt * (y + 5.0)                     # noqa: E731
    nodes = {"A": [20.0, -5.0, t(-5)], "B": [50.0, -5.0, t(-5)], "C": [50.0, 5.0, t(5)], "D": [20.0, 5.0, t(5)],
             "E": [70.0, -5.0, None], "F": [70.0, 5.0, None]}
    return FX.straight(nodes=nodes, facets={"S": {"poly": ["A", "B", "C", "D"], "soft": soft},
                                            "G": {"poly": ["B", "E", "F", "C"],
                                                  "plane": {"crease": ["B", "C"], "rise_per_mm": -0.02}}})


def test_soft_border_band_and_smooth_edges():
    """A soft facet's borders with the base get a SOFT_BAND_MM band (others border_band_mm) and its facet/ramp edges
    are smooth per border run (half the run folds less than SOFT_SHARP_DEG: here the 6 mm band's ramp folds ~6.5 deg,
    one long ramp triangle at the band's corner 29.9 deg, smooth with its run), its ramp edges below SOFT_SHARP_DEG;
    its crease with another facet stays sharp; the receipt, its crease line and the facet table say soft; the ramp
    loops at its border carry the facet's face normal (design_corner_normals)."""
    hard, soft = build_temple(soft_spec(soft=False)), build_temple(soft_spec())
    assert {b["band_mm"] for b in soft.borders if b["facet"] == "S"} == {SOFT_BAND_MM}
    assert {b["band_mm"] for b in hard.borders} == {2.0} and {b["band_mm"] for b in soft.borders if b["facet"] == "G"} \
        == {2.0}
    # the ramp reaches SOFT_BAND_MM from S's border: ramp cells left of its edge d 20 reach d 14 (hard: d 18), and the
    # top outline 5 mm above its top edge (y 5, 0.2 mm proud) is ramped 1/6 of the way (hard: not at all)
    for tb, lo in ((soft, 14.0), (hard, 18.0)):
        ramp_d = [float(tb.arr.V[f, 0].min()) for f, c in zip(tb.arr.faces, tb.face_cell) if c == "ramp"
                  and tb.arr.V[f, 0].max() <= 20.0]
        assert ramp_d and min(ramp_d) == pytest.approx(lo, abs=1e-9)
    for tb, w in ((soft, 1.0 / 6.0), (hard, 0.0)):
        V2 = tb.arr.V
        top = [v for v in range(len(V2)) if 22.0 <= V2[v, 0] <= 48.0 and V2[v, 1] == 10.0]
        assert len(top) >= 10
        for v in top:
            assert tb.X_surface[v] - tb.spec.x_base(V2[v, 0], 10.0) == pytest.approx(0.2 * w, abs=1e-9)
    for tb, is_soft in ((hard, False), (soft, True)):
        topo = mesh_topology(tb.polys)
        sharp = set(tb.sharp)
        border = ramp = 0
        for (a, b), p1 in topo["directed"].items():
            p2 = topo["directed"][(b, a)]
            if a > b or tb.poly_kind[p1] not in OUTER_KINDS or tb.poly_kind[p2] not in OUTER_KINDS:
                continue
            r = {tb.poly_region[p1], tb.poly_region[p2]}
            n1, n2 = tb.normals[p1], tb.normals[p2]
            dih = math.degrees(math.acos(np.clip(n1 @ n2, -1, 1)))
            if r == {"S", "base"}:
                border += 1
                assert ((a, b) in sharp) == (not is_soft)
            elif r == {"S", "G"}:
                assert (a, b) in sharp                              # never soft
            elif r == {"base"} and tb.face_ramp[tb.poly_face[p1]] == "soft":
                ramp += 1
                assert ((a, b) in sharp) == (dih >= SOFT_SHARP_DEG)
        assert border >= 10 and (ramp >= 10) == is_soft
    rc = RP.receipt(soft)
    assert next(f for f in rc["facets"] if f["label"] == "S").get("soft") is True
    assert "soft" not in next(f for f in RP.receipt(hard)["facets"] if f["label"] == "S")
    assert next(c for c in rc["creases"] if c["pair"] == "S|base")["soft"] is True
    assert any(ln.startswith("S|base ") and " soft " in ln for ln in rc["lines"])
    assert not any(" soft" in ln for ln in RP.receipt(hard)["lines"])
    cn = design_corner_normals(soft)
    nS = soft.normals[next(i for i in soft.flat_polys if soft.poly_region[i] == "S")]
    ramp_at_border = [(i, v) for (i, v) in cn if soft.poly_region[i] == "base"]
    assert len(ramp_at_border) >= 10 and all(np.allclose(cn[k], nS, atol=1e-9) for k in ramp_at_border)
    assert not any(soft.poly_region[i] == "base" for i, _ in design_corner_normals(hard))


def test_a_steep_soft_border_stays_sharp():
    """4 mm proud over the 6 mm soft band: the facet/ramp fold is >= SOFT_SHARP_DEG and stays sharp."""
    tb = build_temple(soft_spec(proud=4.5, tilt=0.0))
    topo = mesh_topology(tb.polys)
    folds = [(a, b) for (a, b), p1 in topo["directed"].items() if a < b
             and {tb.poly_region[p1], tb.poly_region[topo["directed"][(b, a)]]} == {"S", "base"}]
    steep = [e for e in folds if math.degrees(math.acos(np.clip(
        tb.normals[topo["directed"][e]] @ tb.normals[topo["directed"][(e[1], e[0])]], -1, 1))) >= SOFT_SHARP_DEG]
    assert steep and all(e in set(tb.sharp) for e in steep)
    assert any(w["code"] == "W_BORDER_RAMP" and "soft band 6" in w["message"] for w in tb.warnings)


# --------------------------------------------------------------------------------------------- one tolerance
def test_a_spec_planes_accept_builds():
    """The one-tolerance fix: two flat facets whose shared corner P4 misses the first one's plane by 5e-7 mm
    (planes.solve accepts up to planar_tol_mm 1e-6): the second facet's polygon is 5e-7 mm out of plane, which the
    former 1e-9 mm self-check refused E_TRIANGULATE on its own rounding; it builds now."""
    off = 5e-7
    a_plane = lambda d, y: X_IN + T + 1.0 + (y + 8.0) / 16.0      # noqa: E731  (A's plane through P1, P2, P3)
    nodes = {"P1": [10.0, -8.0, T + 1.0], "P2": [40.0, -8.0, T + 1.0], "P3": [40.0, 8.0, T + 2.0],
             "P4": [25.0, 0.5, a_plane(25.0, 0.5) - X_IN + off],   # inside A's hull: never one of its defining three
             "P5": [30.0, 10.0, T + 1.5], "P6": [10.0, 10.0, None]}
    s = FX.straight(nodes=nodes, facets={"A": ["P1", "P2", "P3", "P4"], "B": ["P4", "P3", "P5", "P6"]})
    sol = build_temple(s).sol
    assert sol.planes["A"].X(25.0, 0.5) == pytest.approx(a_plane(25.0, 0.5), abs=1e-12)
    assert sol.planes["B"].X(25.0, 0.5) == pytest.approx(a_plane(25.0, 0.5) + off, abs=1e-12)  # B holds P4's own
    tb = build_temple(s)                                          # P4 takes A's value (the first label): B is bent
    assert tb.checks["closed"] and 1e-9 < tb.checks["planarity_max_mm"] <= 1e-6
    assert RP.receipt(tb)["checks"]["planar"] is True
    s["nodes"]["P4"][2] += 1e-6                                   # 1.5e-6 mm: planes.solve refuses it first
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_FACET_NOT_PLANAR"


# --------------------------------------------------------------------------------------------- facet|base vs base
def base_pair_spec(declared: str | None = None) -> dict:
    """A facet F over the slab's hinge end (d 0-30, the full height), 1 mm sunk at its border d 30 and rising
    0.05 mm per mm toward it: against the BASE it is a ridge of atan(0.05) = 2.86 deg; against its 2 mm ramp (1 mm up
    over 2 mm) the fold is a 23.7 deg valley."""
    X = lambda d: X_IN + T - 1.0 + 0.05 * (d - 30.0)               # noqa: E731
    nodes = {"A": [0.0, -10.0, X(0.0) - X_IN], "B": [30.0, -10.0, X(30.0) - X_IN], "C": [30.0, 10.0, X(30.0) - X_IN],
             "D": [0.0, 10.0, X(0.0) - X_IN]}
    s = FX.straight(nodes=nodes, facets={"F": ["A", "B", "C", "D"]})
    if declared:
        s["creases"] = [{"facets": ["F", "base"], "shape": declared}]
    return s


def test_facet_base_crease_is_judged_against_the_base_surface():
    tb = build_temple(base_pair_spec())
    rec = next(c for c in tb.tables["creases"] if c["regions"] == ["F", "base"])
    assert rec["shape"] == "ridge" and rec["mean_dihedral_deg"] == pytest.approx(math.degrees(math.atan(0.05)),
                                                                                abs=1e-6)
    built = [r for r in tb.tables["border_ramps"] if r["facet"] == "F"]
    assert built and built[0]["height_mm"] == pytest.approx(-1.0, abs=1e-9)   # the ramp is still there, reported
    assert build_temple(base_pair_spec("ridge")).checks["closed"]
    with pytest.raises(TempleError) as e:
        build_temple(base_pair_spec("valley"))
    assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["built"] == "ridge"


# --------------------------------------------------------------------------------------------- report / draw
def test_receipt_and_chart_show_a_warp():
    tb = build_temple(with_neighbour(1.5))
    rc = RP.receipt(tb)
    q = next(f for f in rc["facets"] if f["label"] == "Q")
    assert q["warp_mm"] == pytest.approx(0.38, abs=0.006) and "warp_mm" not in next(
        f for f in rc["facets"] if f["label"] == "N")
    rec = next(f for f in tb.tables["facets"] if f["label"] == "Q")
    nrm = np.array([-rec["plane"]["X_coef"][1], -rec["plane"]["X_coef"][2], 1.0])
    assert rec["plane"]["kind"] == "warp" and np.allclose(rec["normal_local"], nrm / np.linalg.norm(nrm))
    img = DR.chart_image(None, tb)
    a = np.asarray(img).reshape(-1, 3).astype(int)
    hatch = int(np.sum(np.all(np.abs(a - np.array(DR.WARP_HATCH)) <= 24, axis=1)))
    assert hatch > 500
    flat = np.asarray(DR.chart_image(None, build_temple(with_neighbour(0.0, "nodes")))).reshape(-1, 3).astype(int)
    assert int(np.sum(np.all(np.abs(flat - np.array(DR.WARP_HATCH)) <= 24, axis=1))) < 50


def test_prada_warp_builds_with_a_soft_neighbour():
    """The Blender export case (test_temple_blender.py) builds in the host core: closed, chord < 0.01 mm, W|S sharp,
    S soft against the base."""
    tb = build_temple(prada_warp())
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    f = {x["label"]: x for x in tb.tables["facets"]}
    assert f["W"]["chord_mm"] < 0.01 and f["S"]["soft"] and f["W"]["warp_mm"] > 0.3
    assert next(c for c in tb.tables["creases"] if c["regions"] == ["S", "W"])["mean_dihedral_deg"] > 2.0
    assert parse(prada_warp()).facets["S"]["soft"] is True


# --------------------------------------------------------------------------------------------- fixes found on the way
def test_a_facet_corner_touching_the_outline_builds():
    """A facet corner snapped onto the top edge with both its edges going inward: the facet-only arrangement's base
    face visited that vertex twice and the build failed E_TRIANGULATE ("pinches"); a chord through it splits the face
    (found by the v3 fuzz: 4 of 400 random facets, flat ones too)."""
    s = FX.base_spec(nodes={"T": [36.9, FX._yt(36.9) - 0.02, FX.T_ZONE + 1.0], "L": [21.9, 7.0, FX.T_ZONE + 1.5],
                            "M": [17.7, -0.1, FX.T_ZONE + 1.2], "R": [39.9, -3.8, FX.T_ZONE + 0.8]},
                     facets={"F": {"poly": ["T", "L", "M", "R"], "plane": "warp"}})
    tb = build_temple(s)
    assert tb.spec.snaps and tb.checks["closed"] and tb.checks["euler"] == 2
    # ... and where that corner is the facet's last vertex in d, a chord through it alone splits nothing: chords
    # through the touching facet's first and last vertex in d too (fuzz seed 581)
    s = FX.base_spec(nodes={"T": [9.776, FX._yt(9.776) - 0.02, FX.T_ZONE + 1.0], "L": [4.371, -0.223, FX.T_ZONE + 1.5],
                            "M": [1.0, -4.284, FX.T_ZONE + 1.2], "R": [2.0, -6.274, FX.T_ZONE + 0.8]},
                     facets={"F": {"poly": ["T", "L", "M", "R"], "plane": "warp"}})
    tb = build_temple(s)
    assert tb.spec.snaps and tb.checks["closed"] and tb.checks["euler"] == 2


def test_a_self_crossing_facet_is_e_spec():
    """A facet polygon crossing itself is E_SPEC before any arrangement is built (it reached the arrangement first
    and came back E_TRIANGULATE, a face pinching at the crossing)."""
    s = FX.base_spec(nodes={"A": [22.2, -6.2, 6.0], "B": [20.0, -12.3, 6.0], "C": [28.6, -12.6, 6.0],
                            "D": [29.6, -9.5, 6.0], "E": [32.6, -11.1, 6.0]},
                     facets={"X": ["A", "B", "C", "D", "E"]})            # D lies above the edge E-A: E-A crosses C-D
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_SPEC" and "crosses itself" in e.value.message


def test_ear_clip_falls_back_on_a_collinear_run():
    """A strip face whose left side is a run of collinear vertices (refinement lines ending on a slanted facet edge):
    the fattest-ear rule's float noise let an ear's diagonal lie along the run and failed "no ear found"; the robust
    clip fans the run from the opposite corner and covers every boundary edge once."""
    from glasses3d.g3d.temple import arrange as AR
    V = np.array([[39.130099241148, -10.80617419730152], [40.0, -10.58], [40.0, 7.145305764313914],
                  [39.86445714746593, 5.999999999999999], [39.62776428036023, 4.0], [39.39107141325454, 2.0],
                  [39.15437854614884, 0.0], [39.139585241954734, -0.125], [39.130099241148, -0.20515451353574754]])
    with pytest.raises(TempleError):
        AR._ear_clip_fast(V, list(range(9)))
    tris = AR.ear_clip(V, list(range(9)))
    assert AR._covers(list(range(9)), tris)
    area = sum(0.5 * abs(AR._cross(V[a], V[b], V[c])) for a, b, c in tris)
    assert area == pytest.approx(AR.poly_area(V), rel=1e-12)


def test_an_empty_face_is_a_refusal(monkeypatch):
    from glasses3d.g3d.temple import arrange as AR
    real = AR.ear_clip
    calls = {"n": 0}

    def once_empty(V, idx):
        calls["n"] += 1
        return [] if calls["n"] == 5 else real(V, idx)
    monkeypatch.setattr(AR, "ear_clip", once_empty)
    with pytest.raises(TempleError) as e:
        build_temple(quad(0.0, "nodes"))
    assert e.value.code == "E_TRIANGULATE" and "no area" in e.value.message


def test_warp_refinement_retries_then_keeps_the_unrefined_build(monkeypatch):
    """A refined arrangement that fails is tried on two more lattices, then left out: the build stands, its chord
    error measured (chord_mm), never refused for the refinement."""
    from glasses3d.g3d.temple import build as BD
    real = BD.build_arrangement
    seen = []

    def failing(boundary, segs, *a, **k):
        if any(t[2][0] == "warp" for t in segs):
            seen.append(len(segs))
            raise TempleError("E_TRIANGULATE", "test: a refined arrangement fails")
        return real(boundary, segs, *a, **k)
    monkeypatch.setattr(BD, "build_arrangement", failing)
    tb = build_temple(quad(1.5))
    assert len(seen) == BD.WARP_TRIES and tb.checks["closed"]
    f = next(x for x in tb.tables["facets"] if x["label"] == "Q")
    assert f["chord_mm"] > 0.02                                    # the 2 mm strips alone, as measured
    assert not any(t[0] == "warp" for tags in tb.arr.edge_tags.values() for t in tags)
    # never silent (review-builder-design F7): the fallback is a warning line with the measured chord error
    (w,) = [w for w in tb.warnings if w["code"] == "W_WARP_CHORD"]
    assert w["facet"] == "Q" and w["unrefined"] and w["chord_mm"] == f["chord_mm"]
    assert any(ln.startswith("W_WARP_CHORD: warp facet Q: its refinement could not be built")
               for ln in RP.receipt(tb)["lines"])
    monkeypatch.undo()
    assert not any(w["code"] == "W_WARP_CHORD" for w in build_temple(quad(1.5)).warnings)


# --------------------------------------------------------------------------------------------- review fixes
# v3-builder-fix: the findings of review-builder-correctness (P1-1, P1-2, P2-3, P2-4, P2-5, P3-6) and
# review-builder-design (F1-F7), each with its regression.
def roll_corners(tb, label: str, cn: dict) -> list:
    """[(depth from the outline, angle to the polygon's own normal)] of the roll corners of `label` carrying a
    design custom normal."""
    ps = tb.spec
    out = []
    for (i, v) in cn:
        if tb.poly_kind[i] == "roll" and tb.poly_region[i].partition("/")[0] == label:
            d, y = tb.V[v, :2]
            depth = min(float(ps.y_top(d)) - y, y - float(ps.y_bot(d)))
            out.append((depth, angle(cn[(i, v)], tb.normals[i])))
    return out


def test_roll_band_of_a_warp_or_soft_facet_keeps_its_roll():
    """design_corner_normals seeds only outer polygons: a warp or soft facet's fans reach the roll band only on its
    start line (s = roll_mm, where the quarter arc is tangent); deeper roll corners stay auto-smoothed, so the rolled
    edge keeps its highlight. Seeding the roll polygons too gave every roll corner the
    un-rolled design normal, up to 88.6 deg off (review-builder-correctness P1-2); a coplanar warp then shaded unlike
    its flat facet."""
    for raise_mm, plane, soft in ((1.5, "warp", False), (0.0, "warp", False), (0.0, "nodes", True)):
        s = quad(raise_mm, plane, proud=1.0, **({"soft": True} if soft else {}))
        s["nodes"]["C"][1] = s["nodes"]["D"][1] = 10.0                # Q reaches the top edge: its roll band
        s["roll_mm"] = 0.6
        tb = build_temple(s)
        rc = roll_corners(tb, "Q", design_corner_normals(tb))
        assert rc and all(depth >= 0.6 - 1e-9 for depth, _ in rc), (plane, soft, min(rc))
        assert max(a for _, a in rc) <= 12.0                           # the first sub-band's tilt at the start line
    flat = build_temple({**quad(0.0, "nodes"), "roll_mm": 0.6})
    assert not roll_corners(flat, "Q", design_corner_normals(flat))


def test_run7_warp_crown_shading_and_cells():
    """Run 7's layout fitted with a warp crown (roll 0.25, the crown along the top edge, K 1.7 deg from straight and
    +1.5 mm): K's shading normal within 1 deg of its triangles' angle-weighted mean (it was 97 deg off), no crown roll
    corner deeper than the roll's start line with a design normal (3911 had one), and the refinement cuts no sliver:
    every warp outer triangle has its smallest angle >= 1 deg and every edge >= 0.01 mm (the row y = 10.0 met the
    crown|logo edge A-K at 0.6 deg and left 0.002 deg triangles, 5.6 deg off in the GLB: review-builder-design F2),
    chord error < 0.01 mm."""
    from glasses3d.g3d.temple import fit as FT
    from glasses3d.tests.test_temple_fit import run7_layout
    tb = build_temple(FT.fit_spec(run7_layout("warp")).spec)
    cn = design_corner_normals(tb)
    v = tb.sol.node_vertex["K"]
    mean, fan = fan_mean(tb, v, "crown")
    assert fan and max(angle(cn[(i, v)], mean) for i in fan) <= 1.0
    rc = roll_corners(tb, "crown", cn)
    assert rc and min(depth for depth, _ in rc) >= 0.25 - 1e-9
    idx = [i for i, (k, c) in enumerate(zip(tb.poly_kind, tb.poly_cell)) if k == "outer" and c == "warp"]
    P = tb.arr.V[np.array([tb.polys[i] for i in idx])]
    angs = []
    for j in range(3):
        a, b = P[:, (j + 1) % 3] - P[:, j], P[:, (j + 2) % 3] - P[:, j]
        angs.append(np.degrees(np.arctan2(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]), (a * b).sum(1))))
    edges = min(float(np.linalg.norm(P[:, (j + 1) % 3] - P[:, j], axis=1).min()) for j in range(3))
    assert float(np.min(angs)) >= 1.0 and edges >= 0.01, (float(np.min(angs)), edges)
    assert next(f for f in tb.tables["facets"] if f["label"] == "crown")["chord_mm"] < 0.01
    assert not any(w["code"] == "W_WARP_CHORD" for w in tb.warnings)


def test_refinement_lines_keep_clear_of_the_facet_edges():
    """warp_segments' clearance: a refinement line that ends on a facet edge meets it at WARP_GRAZE_DEG or more (a
    quad whose lower edge runs 0.6 deg off the rows: the rows near it are left out), and the chord error stays below
    0.01 mm."""
    from glasses3d.g3d.temple import build as BD
    s = quad(1.5)
    s["nodes"]["B"][1] = -6.0 + 30.0 * math.tan(math.radians(0.6))      # A-B 0.6 deg off the rows
    tb = build_temple(s)
    P = np.array([s["nodes"][n][:2] for n in "ABCD"])
    lines = [(tb.arr.V[a], tb.arr.V[b]) for (a, b), tags in tb.arr.edge_tags.items() if ("warp", "Q") in tags]
    assert len(lines) > 50
    met = 0
    for p, q in lines:
        u = (q - p) / np.linalg.norm(q - p)
        for k in range(4):
            e = P[(k + 1) % 4] - P[k]
            if abs(u[0] * e[1] - u[1] * e[0]) < 1e-12 and abs((p - P[k])[0] * e[1] - (p - P[k])[1] * e[0]) < 1e-9:
                continue                                   # the lattice's extreme row lying on a facet edge
            for x in (p, q):
                t = float((x - P[k]) @ e / (e @ e))
                if 0 <= t <= 1 and np.linalg.norm(P[k] + t * e - x) < 1e-9:
                    met += 1
                    sin = abs(u[0] * e[1] - u[1] * e[0]) / np.linalg.norm(e)
                    assert sin >= math.sin(math.radians(BD.WARP_GRAZE_DEG)) - 1e-9
    assert met > 10
    f = next(x for x in tb.tables["facets"] if x["label"] == "Q")
    assert f["chord_mm"] < 0.01


def lean_spec(slope_d: float = 0.1, slope_y: float = 0.0, declared=None, plane="nodes", off: float = 1.0,
              d0: float = 20.0, d1: float = 40.0) -> dict:
    """F: d d0-d1, y 0..10 (its top edge on the outline), proud `off` mm, rising slope_d per mm along d and slope_y
    across: its bottom border with the base (y 0) folds atan(slope_y) (valley for slope_y > 0) and its two ends fold
    atan(slope_d) (a valley at d0, a ridge at d1 for slope_d > 0)."""
    X = lambda d, y: T + off + slope_d * (d - 30.0) + slope_y * y            # noqa: E731
    nodes = {"A": [d0, 0.0, X(d0, 0)], "B": [d1, 0.0, X(d1, 0)], "C": [d1, 10.0, X(d1, 10)],
             "D": [d0, 10.0, X(d0, 10)]}
    s = FX.straight(nodes=nodes, facets={"F": {"poly": ["A", "B", "C", "D"], "plane": plane}})
    if declared:
        s["creases"] = [{"facets": ["F", "base"], "shape": declared}]
    return s


def only_bottom(slope_y: float, declared=None, slope_d: float = 0.1, off: float = 1.0) -> dict:
    """lean_spec with flat neighbours L and R sharing F's ends: its only border with the base is its bottom edge."""
    s = lean_spec(slope_d, slope_y, declared, off=off)
    s["nodes"].update({"L0": [10.0, 0.0, T + 1.0], "L1": [10.0, 10.0, None], "R0": [50.0, 0.0, T + 1.0],
                       "R1": [50.0, 10.0, None]})
    s["facets"].update({"L": ["L0", "A", "D", "L1"], "R": ["B", "R0", "R1", "C"]})
    return s


def test_facet_base_angle_is_the_fold_across_the_border():
    """A facet|base pair's angle is its fold ACROSS the border against the base surface, not the angle between the
    two design normals (which counts the slope difference ALONG the border, the step height changing along it): a
    facet tilted 0.1 along its only base border with no fold read "ridge 5.7 deg" and a 0.29 deg fold 5.72; a pair
    with no edge that folds is flat (never "ridge" by the tie), and a declaration on it is refused as flat with the
    step stated (review-builder-correctness P2-3, review-builder-design F1)."""
    for sy, shape in ((0.0, "flat"), (0.005, "flat"), (-0.005, "flat"), (0.05, "valley"), (-0.05, "ridge")):
        tb = build_temple(only_bottom(sy))
        rec = next(c for c in tb.tables["creases"] if c["regions"] == ["F", "base"])
        deg = angle(np.array([-0.1, -sy, 1.0]), np.array([-0.1, 0.0, 1.0]))   # the facet and the base sharing its
        assert rec["shape"] == shape and rec["mean_dihedral_deg"] == pytest.approx(deg, abs=1e-6), (sy, rec)   # 3D
        assert deg == pytest.approx(math.degrees(math.atan(abs(sy))) / math.sqrt(1.01), abs=2e-4)   # border line
        assert (rec["ridge"], rec["valley"]) == ((0.0, 0.0) if sy == 0 else
                                                  ((rec["length_mm"], 0.0) if sy < 0 else (0.0, rec["length_mm"])))
    for declared in ("ridge", "valley"):
        with pytest.raises(TempleError) as e:
            build_temple(only_bottom(0.0, declared))
        assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["built"] == "flat"
        assert e.value.message.startswith(f"crease F|base is declared {declared} but the face against the arm is "
                                          f"flat 0.00 deg (flat (< 2 deg): no ridge or valley; the facet stands proud")
    # a sunk facet rising toward its border (base_pair_spec): the face against the arm is a 2.86 deg ridge; the 2 mm
    # ramp beside it (a 26.6 deg valley fold) is the step, stated in its line
    tb = build_temple(base_pair_spec())
    line = next(ln for ln in RP.crease_lines(tb) if ln.startswith("F|base"))
    assert line == "F|base ridge 2.9° (sunk 1.00 mm: a 26.6° ramp) undeclared"
    rc = next(c for c in RP.receipt(tb)["creases"] if c["pair"] == "F|base")
    assert rc["step"] == {"height_mm": -1.0, "slope_deg": 26.6}
    # where the facet meets the base without a step the fold is the angle between the two design normals (v1's)
    tb = build_temple(only_bottom(0.1, slope_d=0.0, off=0.0))
    rec = next(c for c in tb.tables["creases"] if c["regions"] == ["F", "base"])
    assert rec["mean_dihedral_deg"] == pytest.approx(math.degrees(math.atan(0.1)), abs=1e-6) and "step" not in rec


def test_coplanar_warp_is_judged_as_its_flat_facet_along_a_tilt():
    """FOLD_TIE (1e-6) lies above the warp gradient's float noise (central differences: up to 2.3e-9), so a coplanar
    warp's facet|base border along its tilt (no fold) casts no vote and the pair reads exactly as its flat facet's: at
    FOLD_TIE 1e-9 seven of these nine layouts got noise votes that decided the shape (review-builder-correctness P2-4:
    the ends fold a ridge and a valley of equal length, the bottom border none)."""
    for sd in (0.1, 0.137, -0.23):
        for off, d0, d1 in ((1.0, 20.0, 40.0), (1.3, 21.7, 43.1), (0.7, 13.3, 37.9)):
            r = {}
            for plane in ("nodes", "warp"):
                tb = build_temple(lean_spec(sd, 0.0, plane=plane, off=off, d0=d0, d1=d1))
                rec = next(c for c in tb.tables["creases"] if c["regions"] == ["F", "base"])
                r[plane] = (rec["shape"], rec["ridge"], rec["valley"], rec["mean_dihedral_deg"])
            assert r["warp"][:3] == r["nodes"][:3] and r["warp"][3] == pytest.approx(r["nodes"][3], abs=1e-6), r


def test_chart_draws_facet_base_edges_by_the_table_rule():
    """draw.crease_edges is the tables' per-edge rule (build.crease_edges / edge_folds): on a sunk facet rising toward
    its border the facet|base edges are drawn as the table's ridge (the fold against the base), not as the valley the
    2 mm ramp folds (the chart contradicted the receipt: review-builder-correctness P2-5, review-builder-design F3);
    every pair's drawn ridge and valley lengths are the table's; a soft border is drawn dashed in its shape's
    colour."""
    from glasses3d.g3d.temple import build as BD
    tb = build_temple(base_pair_spec())
    rec = next(c for c in tb.tables["creases"] if c["regions"] == ["F", "base"])
    edges = BD.crease_edges(tb)
    drawn = [shape for (a, b, shape, deg), e in zip(DR.crease_edges(tb), edges) if e[2] == ("F", "base")]
    assert drawn and set(drawn) == {rec["shape"]} == {"ridge"}
    ramp = [BD._dihedral(tb.normals[e[5]], tb.normals[e[6]]) for e in edges if e[2] == ("F", "base")]
    assert min(ramp) > 20.0                                    # the built ramp fold, what was drawn (a valley)
    for spec in (FX.prada_default(), soft_spec(), base_pair_spec()):
        tb = build_temple(spec)
        acc: dict = {}
        for a, b, pair, deg, ridge, _p1, _p2 in BD.crease_edges(tb):
            if ridge is not None:
                acc.setdefault(pair, {True: 0.0, False: 0.0})[ridge] += float(np.linalg.norm(tb.V[a] - tb.V[b]))
        for c in tb.tables["creases"]:
            if c["kind"] == "crease":
                got = acc.get(tuple(c["regions"]), {True: 0.0, False: 0.0})
                assert got[True] == pytest.approx(c["ridge"], abs=1e-9)
                assert got[False] == pytest.approx(c["valley"], abs=1e-9)
    hard = only_bottom(-0.1)                                   # F|base a 5.7 deg ridge along its bottom border
    soft = copy.deepcopy(hard)
    soft["facets"]["F"]["soft"] = True
    na, nb = (int(np.sum(np.all(np.abs(np.asarray(DR.chart_image(None, build_temple(x))).reshape(-1, 3).astype(int)
                                       - np.array(DR.RIDGE)) <= 12, axis=1))) for x in (soft, hard))
    assert 0 < na < 0.8 * nb                                   # dashed when soft, solid when not


def soft_straddle(proud: float = 2.0, rise: float = 0.03) -> dict:
    """A soft flat facet S on the slab (d 20-50, y -5..5) `proud` mm at d 20 rising `rise` per mm of d: its folds
    with the 6 mm soft band run ~18-30 deg along its borders (and 60-70 at the band's mitred corners)."""
    t = lambda d: T + proud + rise * (d - 20.0)                      # noqa: E731
    nodes = {"A": [20.0, -5.0, t(20)], "B": [50.0, -5.0, t(50)], "C": [50.0, 5.0, t(50)], "D": [20.0, 5.0, t(20)]}
    return FX.straight(nodes=nodes, facets={"S": {"poly": ["A", "B", "C", "D"], "soft": True}})


def test_a_soft_border_run_is_one_line_or_none():
    """A soft border is decided per run (a connected chain of its facet|base edges): all sharp when half its length
    folds >= SOFT_SHARP_DEG, else all smooth. Edge by edge, folds straddling 25 deg drew a broken sharp line on the
    curvature line (review-builder-design F6: 5 of 19 run-7 shoulder edges)."""
    from glasses3d.g3d.temple import build as BD
    for proud, rise, want in ((2.0, 0.03, False), (2.4, 0.03, True), (4.5, 0.0, True)):
        tb = build_temple(soft_straddle(proud, rise))
        topo = mesh_topology(tb.polys)
        es, dih = [], {}
        for (a, b), p1 in topo["directed"].items():
            p2 = topo["directed"][(b, a)]
            if a < b and {tb.poly_region[p1], tb.poly_region[p2]} == {"S", "base"}:
                es.append((a, b))
                dih[(a, b)] = angle(tb.normals[p1], tb.normals[p2])
        runs = BD._edge_chains(es)
        assert len(runs) == 1 and any(x >= SOFT_SHARP_DEG for x in dih.values())
        if proud < 4.0:                                              # folds on both sides of 25 deg
            assert any(x < SOFT_SHARP_DEG for x in dih.values())
        sharp = set(tb.sharp)
        assert {e in sharp for e in runs[0]} == {want}, (proud, rise)


def test_crease_line_gives_the_range_beside_a_warp():
    """A crease beside a warp changes angle along its length; the table holds its face-to-face range (outer cells
    only: roll sub-band edges pulled the full range's minimum to 0.3 deg) and the receipt prints it beside a warp:
    "crown|logo ridge 18.2° (17.3-27.4)" on run 7 (review-builder-design F4); a flat|flat crease prints none."""
    from glasses3d.g3d.temple import fit as FT
    from glasses3d.tests.test_temple_fit import run7_layout
    tb = build_temple(FT.fit_spec(run7_layout("warp")).spec)
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["crown", "logo"])
    lo, hi = c["range_deg"]
    assert c["warp_beside"] and c["dihedral_deg"][0] < 1.0 and 15.0 < lo < 18.0 and 25.0 < hi < 30.0
    line = next(ln for ln in RP.crease_lines(tb) if ln.startswith("crown|logo"))
    assert line == f"crown|logo ridge {c['mean_dihedral_deg']:.1f}° ({lo:.1f}-{hi:.1f}) undeclared"
    assert next(x for x in RP.receipt(tb)["creases"] if x["pair"] == "crown|logo")["range_deg"] == [round(lo, 1),
                                                                                                   round(hi, 1)]
    flat = next(ln for ln in RP.crease_lines(tb) if ln.startswith("logo|lower"))
    assert "(" not in flat
    assert "range_deg" not in next(x for x in RP.receipt(tb)["creases"] if x["pair"] == "logo|lower")


def test_planar_tol_is_capped():
    """options.planar_tol_mm is also the polygon self-check's tolerance (one tolerance): capped at
    PLANAR_TOL_MAX_MM, so no facet n-gon Blender would triangulate its own way passes (review-builder-correctness
    P3-6)."""
    from glasses3d.g3d.temple.spec import PLANAR_TOL_MAX_MM
    for tol, ok in ((1e-6, True), (PLANAR_TOL_MAX_MM, True), (2e-4, False), (0.1, False)):
        s = quad(1.5)
        s["options"] = {**(s.get("options") or {}), "planar_tol_mm": tol}
        if ok:
            parse(s)
        else:
            with pytest.raises(TempleError) as e:
                parse(s)
            assert e.value.code == "E_SPEC" and e.value.fields["option"] == "planar_tol_mm"


def test_ill_conditioned_states_facts_only():
    """E_ILL_CONDITIONED names the tilt and the defining points, no remedy ("or a crease + rise plane", a plane v3 no
    longer offers: review-builder-design F5)."""
    with pytest.raises(TempleError) as e:
        build_temple(FX.refusal_cases()["E_ILL_CONDITIONED"])
    assert e.value.code == "E_ILL_CONDITIONED"
    assert e.value.message == ("facet F1: a 1 mm error in one defining value tilts it 17.0 deg (> 10.0); its plane is "
                               "defined by A, B, G")


def test_a_four_corner_warp_states_the_fits_corner_measure():
    """warp_mm (the largest corner residual from the least-squares plane of all corners) beside the fit's
    W_FACE_NOT_FLAT measure for a 4-corner warp (corner_off: the corner the plane of the other three predicts best),
    so flat -> warp does not turn "K +1.5 mm from the plane of A, E, H" into a smaller-looking 0.99
    (review-builder-design F8); a 5-corner warp has no one three-corner measure."""
    from glasses3d.g3d.temple import fit as FT
    from glasses3d.tests.test_temple_fit import run7_layout
    rc = RP.receipt(build_temple(FT.fit_spec(run7_layout("warp")).spec))
    crown = next(f for f in rc["facets"] if f["label"] == "crown")
    assert crown["warp_mm"] == pytest.approx(0.99, abs=0.01)
    assert crown["corner_off"] == "K +1.52 mm from the plane of A, E, H"
    w5 = next(f for f in RP.receipt(build_temple(polygon_warp(5)))["facets"] if f["label"] == "W")
    assert "warp_mm" in w5 and "corner_off" not in w5


def test_a_band_offset_never_runs_past_its_run_end():
    """A facet floating in the base (joined by chords at its first and last corner in d) whose border P4-P3 runs
    0.0003 deg off the d axis: the band's offset of that border ended 9e-6 mm past the chord through P3, and the
    closing segment back to the chord doubled back over that stub, leaving a dangling vertex (E_TRIANGULATE "a segment
    ends inside the outline without meeting another"; the v3-builder-fix fuzz, flat facets alike). The offset now
    ends at the run's walked end point."""
    nodes = {"P0": [87.58376684026096, 4.196907308686599, 6.430760914128469],
             "P1": [83.28204130162322, 5.257307342502047, 5.723410504169726],
             "P2": [78.05240738670949, 6.668913979335306, 6.80686423402639],
             "P3": [66.91136207527323, 0.9502747231548255, 7.086097638278938],
             "P4": [87.37727519915013, 0.9501820159320107, 6.523451015845196],
             "P5": [102.02157824960759, -0.03372501335951583, 5.331767619912993]}
    tb = build_temple(FX.base_spec(nodes=nodes, facets={"W": {"poly": list(nodes), "plane": "warp"}}))
    assert tb.checks["closed"] and tb.checks["euler"] == 2


def test_a_stepped_flat_facet_base_pair_is_a_plateau_not_a_flat_crease():
    """A facet parallel to the arm standing 2 mm proud: its facet|base pair is flat (no fold against the base) with
    the step in its crease line, and no W_FLAT_CREASE ("bends only 0.00 deg" beside a 45 deg ramp misleads); flush
    with the base (no step) the warning stays."""
    tb = build_temple(lean_spec(0.0, 0.0, off=2.0))
    line = next(ln for ln in RP.crease_lines(tb) if ln.startswith("F|base"))
    assert line.startswith("F|base built angle 0.0° (proud 2.00 mm: a 45.0° ramp)")
    assert not any(w["code"] == "W_FLAT_CREASE" for w in tb.warnings)
    tb = build_temple(lean_spec(0.0, 0.0, off=0.0))
    assert any(w["code"] == "W_FLAT_CREASE" and w["regions"] == ["F", "base"] for w in tb.warnings)


def test_a_soft_flat_facet_base_border_gives_no_flat_crease_warning():
    """v3p3-review-astra P3-1: a border made soft (the photos show only a tone change there) built flat printed
    "W_FLAT_CREASE: crease shoulder|base bends only 1.23 deg (< 2.0)" beside "shoulder|base flat 1.2° soft" in every
    session2 receipt from step 06 (soft had removed the step that suppressed it), read as a request for more angle. A
    soft border is meant to read as no line: no warning; its crease line still says flat and soft. The same border
    not soft keeps the warning (the facet's other pairs are unaffected)."""
    s = lean_spec(0.0, 0.0, off=0.0)
    s["facets"]["F"]["soft"] = True
    tb = build_temple(s)
    assert not any(w["code"] == "W_FLAT_CREASE" and w["regions"] == ["F", "base"] for w in tb.warnings)
    c = next(c for c in tb.tables["creases"] if c["kind"] == "crease" and c["regions"] == ["F", "base"])
    assert c["shape"] == "flat" and c.get("soft") is True
    assert any(ln.startswith("F|base built angle") and "soft" in ln for ln in RP.crease_lines(tb))
    s["facets"]["F"]["soft"] = False
    assert any(w["code"] == "W_FLAT_CREASE" and w["regions"] == ["F", "base"] for w in build_temple(s).warnings)


def test_touch_of_a_point_segment():
    """arrange._touch with a zero-length segment (two coincident corners) is a point test: it divided by zero and
    returned "no touch" through NaN comparisons (a RuntimeWarning in the v3-builder-fix fuzz)."""
    import warnings
    from glasses3d.g3d.temple import arrange as AR
    p = np.array([1.0, 0.0])
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert AR._touch(p, p, np.array([0.0, 0.0]), np.array([2.0, 0.0]), 1e-12)
        assert not AR._touch(p, p, np.array([0.0, 1.0]), np.array([2.0, 1.0]), 1e-12)
        assert AR._touch(p, p, p.copy(), p.copy(), 1e-12)
        assert not AR._touch(p, p, np.array([5.0, 0.0]), np.array([5.0, 0.0]), 1e-12)
