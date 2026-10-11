"""g3d.temple/2 planes (plan/WP6_FACETS.md §2.2 step 1): exact propagation in world X, crease + rise, no base seeding,
conditioning, refusals."""
from __future__ import annotations

import math

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import planes as PL
from glasses3d.g3d.temple.spec import TempleError, parse


def test_crease_plane_holds_the_crease_and_rises_into_the_facet():
    n1, n2 = (0.0, 10.5), (46.8, 9.52)
    coef, cond = PL.crease_plane(n1, n2, 66.2, 65.3, -0.25, interior_left=True)
    X = lambda p: coef[0] + coef[1] * p[0] + coef[2] * p[1]
    assert X(n1) == pytest.approx(66.2, abs=1e-12) and X(n2) == pytest.approx(65.3, abs=1e-12)
    mid = (np.array(n1) + np.array(n2)) / 2
    u = (np.array(n2) - np.array(n1)) / np.linalg.norm(np.array(n2) - np.array(n1))
    left = np.array([-u[1], u[0]])
    assert X(mid + 2.0 * left) - X(mid) == pytest.approx(-0.5, abs=1e-12)       # 2 mm into the facet: -0.25/mm
    assert cond == pytest.approx(math.degrees(math.atan(1 / np.linalg.norm(np.subtract(n2, n1)))))
    _, n_in, L = PL.crease_frame(n1, n2, interior_left=False)
    assert np.allclose(n_in, -left) and L == pytest.approx(np.linalg.norm(np.subtract(n2, n1)))


def test_prada_like_conditioning():
    tb = build_temple(FX.pyramid())
    cond = {f["label"]: f["conditioning_deg_per_mm"] for f in tb.tables["facets"]}
    assert cond["F2"] == pytest.approx(2.2, abs=0.1)                  # the v1 design's Prada numbers: F2 2.2 deg/mm
    assert cond["F3"] == pytest.approx(4.5, abs=0.1)                  # F3 4.5 deg/mm


def test_planes_are_in_world_x_and_facets_agree_at_shared_vertices():
    tb = build_temple(FX.prada_default())
    arr, sol, ps = tb.arr, tb.sol, tb.spec
    for name in ("A", "C"):
        d, y, t = ps.nodes[name]
        lab = "F2"
        assert sol.planes[lab].X(d, y) == pytest.approx(ps.inner(d) + t, abs=1e-9)
    for fi, f in enumerate(arr.faces):
        lab = tb.face_label[fi]
        if lab != "base":
            assert np.max(np.abs(sol.planes[lab].X(arr.V[f, 0], arr.V[f, 1]) - sol.X_vertex[f])) <= 1e-9
            assert np.max(np.abs(tb.X_surface[f] - sol.X_vertex[f])) <= 1e-9


def test_base_border_vertices_are_never_known_points():
    """v1 seeded every base-border vertex with r = 0, so F4 (B and C given, E on the base border) was determined.
    In v2 a facet with two known values plus a base border is underdetermined; "base" on E makes it determined."""
    s = FX.pyramid()
    s["nodes"]["E"][2] = None
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_FACET_UNDERDETERMINED" and e.value.fields["facet"] == "F4"
    tb = build_temple(FX.pyramid())
    E = tb.spec.nodes["E"]
    assert tb.sol.planes["F4"].X(E[0], E[1]) == pytest.approx(float(tb.spec.x_base(E[0], E[1])), abs=1e-9)
    assert all(not str(s_).startswith("base") for s_ in tb.sol.source_vertex if s_ is not None)


def test_solved_node_and_inherited_values():
    tb = build_temple(FX.solved_node())
    assert tb.sol.node_X["C"] == pytest.approx(tb.sol.planes["F2"].X(22.6, -16.3), abs=1e-12)
    assert tb.tables["nodes"]["C"]["t"] == pytest.approx(tb.sol.node_X["C"] - tb.spec.inner(22.6), abs=1e-12)
    for lab in ("F3", "F4"):
        assert tb.sol.planes[lab].defining


def test_shape_nodes_off_the_outline_do_not_couple_facets():
    tb = build_temple(FX.prada_default())
    assert set(tb.sol.planes) == {"F1", "F2", "F3", "F4"}


def test_relief_step_names_the_neighbour():
    with pytest.raises(TempleError) as e:
        build_temple(FX.refusal_cases()["E_RELIEF_STEP"])
    f = e.value.fields
    assert e.value.code == "E_RELIEF_STEP" and f["facet"] == "Fb" and f["neighbours"] == ["Fa"]
    assert max(abs(m["misfit_mm"]) for m in f["misfits"]) > 0.1
    s = FX.relief_step()
    s["nodes"]["S2"][2] = 4.5 + 4e-6                   # a step of a few 1e-6 mm: above planar_tol, and the message
                                                       # says so (it printed "+0.0000 mm")
    with pytest.raises(TempleError) as e:
        build_temple(s)
    worst = max(e.value.fields["misfits"], key=lambda m: abs(m["misfit_mm"]))["misfit_mm"]
    assert e.value.code == "E_RELIEF_STEP" and 1e-6 < abs(worst) < 1e-5
    assert f"{worst:+.3g} mm" in e.value.message and "0.0000" not in e.value.message


def test_over_determined_facet_is_not_planar_with_each_misfit():
    with pytest.raises(TempleError) as e:
        build_temple(FX.refusal_cases()["E_FACET_NOT_PLANAR"])
    f = e.value.fields
    assert e.value.code == "E_FACET_NOT_PLANAR" and f["facet"] == "F2"
    assert f["misfits"] and all(abs(m["misfit_mm"]) > 1e-6 for m in f["misfits"])
    assert len(f["defining_points"]) == 3


def test_underdetermined_and_ill_conditioned():
    with pytest.raises(TempleError) as e:
        build_temple(FX.refusal_cases()["E_FACET_UNDERDETERMINED"])
    assert e.value.code == "E_FACET_UNDERDETERMINED" and e.value.fields["facet"] == "F3"
    with pytest.raises(TempleError) as e:
        build_temple(FX.near_collinear_f1())
    assert e.value.code == "E_ILL_CONDITIONED" and e.value.fields["deg_per_mm"] > 10
    tb = build_temple(FX.crease_rise())                    # the same plane as crease + rise is well conditioned
    f1 = next(f for f in tb.tables["facets"] if f["label"] == "F1")
    assert f1["conditioning_deg_per_mm"] < 2.0


def test_conditioning_warning_between_6_and_10_deg_per_mm():
    s = FX.arm_pyramid()
    s["nodes"]["O"][1] = 4.0                                            # apex nearer the north edge
    tb = build_temple(s)
    conds = {f["label"]: f["conditioning_deg_per_mm"] for f in tb.tables["facets"]}
    warned = {w["facet"] for w in tb.warnings if w["code"] == "W_CONDITIONING"}
    assert warned == {k for k, v in conds.items() if v > 6.0}
    assert warned and all(v <= 10.0 for v in conds.values())


def test_rise_is_in_world_x_and_reported_against_the_local_base():
    tb = build_temple(FX.crease_rise())
    f1 = next(f for f in tb.tables["facets"] if f["label"] == "F1")
    assert f1["rise_per_mm"] == pytest.approx(-0.25)
    c = np.array(f1["plane"]["X_coef"])
    A, B = np.array(tb.spec.nodes["A"][:2]), np.array(tb.spec.nodes["B"][:2])
    u = (B - A) / np.linalg.norm(B - A)
    n_in = np.array([-u[1], u[0]])                                      # F1 lies above A-B
    assert c[1:] @ n_in == pytest.approx(-0.25, abs=1e-12)
    g = np.array([(61.8 - 65.5) / 65.0, 0.0])                           # the fixture base gradient over d 0-65
    assert f1["rise_rel_base_per_mm"] == pytest.approx(-0.25 - g @ n_in, abs=1e-6)


def test_widest_triangle_and_hull():
    pts = [(0, 0, 1, None, None), (1, 0.1, 1, None, None), (10, 0, 1, None, None), (5, 8, 1, None, None),
           (5, 1, 1, None, None)]
    assert PL._widest(pts) == (0, 2, 3)
    assert PL._widest([(0, 0), (1, 1), (2, 2), (3, 3)]) is None
    P = np.array([[0, 0], [1, 0.1], [10, 0], [5, 8], [5, 1]], float)
    assert PL._hull(P) == [0, 2, 3]


def test_parse_keeps_solved_nodes_null():
    ps = parse(FX.solved_node())
    assert ps.nodes["C"][2] is None and ps.node_X("C") is None
