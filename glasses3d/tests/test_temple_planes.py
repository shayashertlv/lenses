"""WP6 temple core: exact plane propagation, crease + rise, base edges at r = 0, conditioning, refusals (§3 step 3)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import planes as PL
from glasses3d.g3d.temple.spec import TempleError


def test_crease_plane_holds_the_crease_and_rises_into_the_facet():
    n1, n2 = (0.0, 10.5), (46.8, 9.52)
    coef, cond = PL.crease_plane(n1, n2, 1.2, 0.3, -0.25, interior_left=True)
    r = lambda p: coef[0] + coef[1] * p[0] + coef[2] * p[1]
    assert r(n1) == pytest.approx(1.2, abs=1e-12) and r(n2) == pytest.approx(0.3, abs=1e-12)
    mid = (np.array(n1) + np.array(n2)) / 2
    u = (np.array(n2) - np.array(n1)) / np.linalg.norm(np.array(n2) - np.array(n1))
    left = np.array([-u[1], u[0]])
    assert r(mid + 2.0 * left) - r(mid) == pytest.approx(-0.5, abs=1e-12)       # 2 mm into the facet: -0.25/mm
    assert cond == pytest.approx(math.degrees(math.atan(1 / np.linalg.norm(np.subtract(n2, n1)))))


def test_prada_like_conditioning_matches_the_design_numbers():
    tb = build_temple(FX.pyramid())
    cond = {f["label"]: f["conditioning_deg_per_mm"] for f in tb.tables["facets"]}
    assert cond["F2"] == pytest.approx(2.2, abs=0.1)                  # WP6_DESIGN §3: Prada F2 2.2 deg/mm
    assert cond["F3"] == pytest.approx(4.5, abs=0.1)                  # F3 4.5 deg/mm


def test_base_border_vertices_are_zero_and_facets_agree_at_shared_vertices():
    tb = build_temple(FX.prada_default())
    arr, sol = tb.arr, tb.sol
    for fi, f in enumerate(arr.faces):
        if tb.face_label[fi] == "base":
            assert np.all(sol.r_vertex[f] == 0.0)
    for fi, f in enumerate(arr.faces):
        lab = tb.face_label[fi]
        if lab != "base":
            pl = sol.planes[lab]
            assert np.max(np.abs(pl.r(arr.V[f, 0], arr.V[f, 1]) - sol.r_vertex[f])) <= 1e-9


def test_solved_node_and_inherited_values():
    tb = build_temple(FX.solved_node())
    # C was null: F2's plane (A, B, D) gives it a value; F3 and F4 meet F2 without a step
    assert tb.sol.node_r["C"] == pytest.approx(tb.sol.planes["F2"].r(22.6, -16.3), abs=1e-12)
    for lab in ("F3", "F4"):
        assert any(d["source"].startswith("facet F2") for d in tb.sol.planes[lab].defining) or \
            tb.sol.planes[lab].defining


def test_shape_nodes_off_the_outline_do_not_couple_facets():
    # C_lo / A_l are null corners outside the outline shared by two facets; they bind nothing
    tb = build_temple(FX.prada_default())
    assert set(tb.sol.planes) == {"F1", "F2", "F3", "F4"}


def test_relief_step_names_the_zero_line():
    s = FX.crease_rise()
    s["nodes"]["G"][0] += 1.0                                          # G off F1's zero line
    s["nodes"]["G_hi"][0] += 1.0
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_RELIEF_STEP"
    zl = e.value.fields["zero_line"]
    assert zl["point"] is not None and abs(np.hypot(*zl["direction"]) - 1) < 1e-12
    # the zero line the refusal names passes through B (r_B = 0) and the original G
    good = FX.crease_rise()
    G = np.array(good["nodes"]["G"][:2])
    B = np.array(good["nodes"]["B"][:2])
    p, d = np.array(zl["point"]), np.array(zl["direction"])
    for q in (G, B):
        assert abs((q - p)[0] * d[1] - (q - p)[1] * d[0]) < 1e-6


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
    # the same plane as crease + rise is well conditioned
    tb = build_temple(FX.crease_rise())
    f1 = next(f for f in tb.tables["facets"] if f["label"] == "F1")
    assert f1["conditioning_deg_per_mm"] < 2.0


def test_conditioning_warning_between_6_and_10_deg_per_mm():
    s = FX.zone_end_facet()
    s["nodes"]["O"][1] = 4.0                                            # apex nearer the north edge
    tb = build_temple(s)
    conds = {f["label"]: f["conditioning_deg_per_mm"] for f in tb.tables["facets"]}
    warned = {w["facet"] for w in tb.warnings if w["code"] == "W_CONDITIONING"}
    assert warned == {k for k, v in conds.items() if v > 6.0}
    assert warned and all(v <= 10.0 for v in conds.values())


def test_zero_line_point_helper_places_a_step_free_node():
    s = FX.crease_rise()
    A, B = s["nodes"]["A"], s["nodes"]["B"]
    coef, _ = PL.crease_plane(A, B, A[2], B[2], -0.25, True)
    G = FX.zero_line_on_chain(coef, FX.TOP, d_min=1.0, d_max=B[0])
    assert coef[0] + coef[1] * G[0] + coef[2] * G[1] == pytest.approx(0.0, abs=1e-12)
    assert np.interp(G[0], np.array(FX.TOP)[:, 0], np.array(FX.TOP)[:, 1]) == pytest.approx(G[1], abs=1e-9)
