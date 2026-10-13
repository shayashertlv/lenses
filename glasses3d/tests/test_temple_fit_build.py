"""WP6 v3 fit -> build end to end (plan/WP6_V3.md §2-§3): fit_spec's output (coef planes, warp faces, numeric t) is a
spec build_temple accepts and builds as fitted, on the real run-7 layout with the settled readings and on synthetic
layouts. These need the builder's coef / warp support (WP6_V3 §2, track A).

Crease angles: the fit reports the profile angle across a shared edge at its midpoint (WP6_V3 §3: for facet|base the
fold across the border) and normal_deg, the dihedral there (the design normals' angle with the slope along the edge
shared). The builder's crease table holds per edge the built dihedral (the two polygons' normals) of a facet|facet
edge, and of a facet|base edge the dihedral against the BASE surface with the slope along the border shared
(build._base_border_design, track A, review-builder-design F1: run 7's shoulder|base 2.6, the raw normals 19.2);
`dihedral_deg` spans every edge, roll sub-bands included, `range_deg` the face-to-face edges. So flat|flat creases agree
to rounding with normal_deg, facet|base ones with normal_deg inside the built range (the base's gradient changes along
the border); a crease beside a warp varies along the edge: the fit's mean_dihedral_deg (the EDGE_W mean of its signed
dihedral over the piece, v3p2) agrees with the built length-weighted mean within 0.14 deg on every case here, where the
midpoint's normal_deg was up to 1.8 off (probe1's warp crown|logo with lower|shoulder declared, 15.8..33.5 along it)."""
from __future__ import annotations

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fit as FT
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.tests.test_temple_fit import (POS, RUN7_ONE_SOURCE, RUN7_READ, TRUTH, cam_face, cam_pair,
                                             chamfer_knot_design, crease_pair, expected_creases, l_warp_design,
                                             layout1, outline_corner_design, probe1_design, probe1_draw,
                                             probe1_reading_design, probe1_truth, pyramid_slab, r_w_angle, read,
                                             run7_layout,
                                             shared_edge_design, steep_pair, strip, tee, warp_beside_flat)


def built_creases(tb) -> dict:
    return {"|".join(c["regions"]): c for c in tb.tables["creases"] if c["kind"] == "crease"}


def check_creases(res, tb, warp_tol: float = 0.25) -> None:
    """Every fitted crease against the built table (module docstring): the same shape where the fit says >=
    MIN_CREASE_DEG; between flat faces normal_deg = the built largest face-to-face dihedral to 0.1 deg (range_deg: a
    roll's sub-band edges can be steeper or flatter, run 7 crown|shoulder 48.46 with them, 48.22 face to face, the fit
    48.22); on a facet's border with the base, warp or flat, normal_deg inside the built range to 0.1, the fit's
    longest border being one of the built edges; beside a warp (facet|warp) the fit's mean_dihedral_deg = the built mean
    to warp_tol (v3p2: it compared the midpoint's normal_deg at 1.5).
    The shape is compared where the fit says >= MIN_CREASE_DEG, for facet|base only with one border (the builder votes
    over all borders, the fit reports the longest one): borders are counted by pieces, not constraint points (review
    F7: "segments" counted the EDGE_AT points, and a skip on it left every warp|base pair unchecked)."""
    built = built_creases(tb)
    warp = {k for k, f in res.faces.items() if f["kind"] == "warp"}
    for c in res.creases:
        if c["deg"] is None:
            continue
        b = built[c["name"]]
        if c["pair"][1] == "base":                       # the base's gradient changes along the border
            assert b["dihedral_deg"][0] - 0.1 <= c["normal_deg"] <= b["dihedral_deg"][1] + 0.1, c["name"]
            if c["borders"] > 1:
                continue
        elif warp & set(c["pair"]):
            assert b["mean_dihedral_deg"] == pytest.approx(abs(c["mean_dihedral_deg"]), abs=warp_tol), c["name"]
        else:
            assert (b.get("range_deg") or b["dihedral_deg"])[1] == pytest.approx(c["normal_deg"], abs=0.1), c["name"]
        if abs(c["deg"]) >= FT.MIN_CREASE_DEG:
            assert b["shape"] == c["shape"], c["name"]


def test_fitted_prada_builds_the_same_temple():
    """prada_default is not over-determined: its fitted spec (coef planes for the crease / nodes planes, numeric t,
    the virtual corners shared by flat facets left null) builds the very same solid."""
    spec = FX.prada_default()
    res = FT.fit_spec(spec)
    assert all(f["plane"].keys() == {"coef"} for f in res.spec["facets"].values())
    assert res.spec["nodes"]["A_l"][2] is None and res.spec["nodes"]["C_lo"][2] is None
    tb0, tb1 = build_temple(spec), build_temple(res.spec)
    assert tb1.polys == tb0.polys and np.allclose(tb1.V, tb0.V, atol=1e-8, rtol=0.0)
    b0, b1 = built_creases(tb0), built_creases(tb1)
    assert b0.keys() == b1.keys()
    for k in b0:
        assert b1[k]["shape"] == b0[k]["shape"]
        assert b1[k]["mean_dihedral_deg"] == pytest.approx(b0[k]["mean_dihedral_deg"], abs=1e-6)
    check_creases(res, tb1)


def test_fitted_synthetic_layout_builds_its_warp_and_flat_faces():
    decl = [(p, "ridge" if d > 0 else "valley") for p, d in expected_creases().items() if abs(d) >= FT.MIN_CREASE_DEG]
    res = FT.fit_spec(layout1(creases=decl))
    tb = build_temple(res.spec)
    V2, X = tb.arr.V, tb.X_surface
    P = np.array([POS[n] for n in ["NE", "SE", "Q2", "Q1"]])
    F = np.array([TRUTH[n] for n in ["NE", "SE", "Q2", "Q1"]])
    for lab in ("N", "E", "S", "W", "R", "Q"):
        vs = sorted({v for fi, f in enumerate(tb.arr.faces) if tb.face_label[fi] == lab for v in f})
        if lab == "Q":
            inner = [v for v in vs if 40.0 + 1e-6 < V2[v, 0] < 70.0 - 1e-6 and abs(V2[v, 1]) < 10.0 - 1e-6]
            assert len(inner) > 20
            for v in inner:
                assert X[v] == pytest.approx(float(FT.mvc_weights(P, V2[v]) @ F), abs=1e-9)
        else:
            a, b, c = res.spec["facets"][lab]["plane"]["coef"]
            assert np.max(np.abs(a + b * V2[vs, 0] + c * V2[vs, 1] - X[vs])) <= 1e-9
    check_creases(res, tb)


@pytest.mark.parametrize("crown", ["nodes", "warp"])
def test_real_run7_fits_build(crown):
    res = FT.fit_spec(run7_layout(crown))
    tb = build_temple(res.spec)
    for n, r in res.nodes.items():
        assert tb.sol.node_X[n] == pytest.approx(r["X"], abs=1e-9)
    assert tb.tables["thickness"]["min_mm"] >= 1.0
    if crown == "warp":
        for n in ("A", "H", "K", "E"):
            assert tb.sol.node_X[n] == pytest.approx(RUN7_READ[n][2], abs=1e-9)   # every crown reading kept
        assert tb.sol.planes["crown"].kind == "warp"
    check_creases(res, tb)


def test_fit_accepted_declarations_build():
    free = FT.fit_spec(run7_layout("warp"))
    decl = [{"facets": c["pair"], "shape": c["shape"]} for c in free.creases if c["certain"]]
    res = FT.fit_spec(run7_layout("warp", decl))
    tb = build_temple(res.spec)                          # no E_CREASE_SHAPE
    check_creases(res, tb)
    # a declared ridge the readings put at -1.0 deg, held by the fit at MIN_CREASE_DEG: the builder sees a ridge
    res = FT.fit_spec(crease_pair(0.3))
    tb = build_temple(res.spec)
    b = built_creases(tb)["D|U"]
    assert b["shape"] == "ridge" and b["mean_dihedral_deg"] >= FT.MIN_CREASE_DEG - 0.01
    res = FT.fit_spec(strip())                           # 13 declared quad|quad creases
    check_creases(res, build_temple(res.spec))


@pytest.mark.parametrize("which", ["tee", "tee_warp", "bound"])
def test_equality_and_bound_fits_build(which):
    if which == "bound":
        nodes = {"P": [20.0, -6.0, read(64.0, 0.2)], "Q": [60.0, -6.0, read(64.0, 0.2)],
                 "R": [60.0, 6.0, read(60.2, 0.2)], "S": [20.0, 6.0, read(60.2, 0.2)]}
        spec = FX.straight(nodes=nodes, facets={"T": ["P", "Q", "R", "S"]})
    else:
        spec = tee(which == "tee_warp")
    res = FT.fit_spec(spec)
    tb = build_temple(res.spec)                          # no E_RELIEF_STEP, E_FACET_NOT_PLANAR or E_TOO_THIN
    assert tb.tables["thickness"]["min_mm"] >= 1.0
    for n, r in res.nodes.items():
        assert tb.sol.node_X[n] == pytest.approx(r["X"], abs=1e-9)


# -------------------------------------------------------------------------------------------- review fixes (v3-fit-fix)
# The builds of the review's probes (v3/probes/fit-correctness p1, p2, p3, p6, p7; fit-design p02, p06, p08): each was
# a fitted spec the builder refused, or a case no build test covered (F7).
def builds_as_fitted(res, tmin: float = 1.0):
    tb = build_temple(res.spec)
    assert tb.tables["thickness"]["min_mm"] >= tmin
    for n, r in res.nodes.items():
        if r["X"] is not None:
            assert tb.sol.node_X[n] == pytest.approx(r["X"], abs=1e-9), n
    check_creases(res, tb)
    return tb


def test_no_facet_fits_build_the_design():
    """F1 (p7): a facet-less design (every revision 0) fits to itself and builds the same solid."""
    for s in (FX.straight(), FX.slab()):
        tb0, tb1 = build_temple(s), build_temple(FT.fit_spec(s).spec)
        assert tb1.polys == tb0.polys and np.array_equal(tb1.V, tb0.V)
    s = FX.straight(nodes={"Z": [10.0, 0.0, read(64.5, 0.2)]})
    typed = FX.straight(nodes={"Z": [10.0, 0.0, 4.5]})
    assert np.array_equal(build_temple(FT.fit_spec(s).spec).V, build_temple(typed).V)


@pytest.mark.parametrize("apex_X, roll", [(60.5, 0.0), (60.5, 0.25), (61.1, 0.25)])
def test_rolled_fits_build(apex_X, roll):
    """F2 (p1): an interior node held by the bound with a roll builds (it was E_ROLL "the limit is 0.000 / 0.100")."""
    tb = builds_as_fitted(FT.fit_spec(pyramid_slab(apex_X, roll)))
    assert tb.tables["thickness"]["min_mm"] == pytest.approx(1.0 + roll, abs=2e-6)


@pytest.mark.parametrize("which", ["outline_corner", "outline_corner_thick", "chamfer_knot"])
def test_outline_point_fits_build(which):
    """F3 (p2): a facet covering an outline vertex (it was E_TOO_THIN -0.500 at (0, -10)), and one over a concave inner
    knot and a chamfer's breaks on the outline, build at t_min."""
    s = {"outline_corner": lambda: outline_corner_design(-0.5),
         "outline_corner_thick": lambda: outline_corner_design(1.5), "chamfer_knot": chamfer_knot_design}[which]()
    tb = builds_as_fitted(FT.fit_spec(s))
    if which != "outline_corner_thick":
        assert tb.tables["thickness"]["min_mm"] == pytest.approx(1.0, abs=2e-6)


def test_shared_virtual_edge_fit_builds():
    """F4 (p3): two facets sharing an edge from a null virtual node into the outline build (it was E_RELIEF_STEP -1 mm
    at the edge's outline crossing)."""
    builds_as_fitted(FT.fit_spec(shared_edge_design()))


@pytest.mark.parametrize("low, high", [(61.1, 75.0), (60.9, 70.0), (61.2, 70.0)])
def test_non_convex_warp_fits_build(low, high):
    """F5 (p6): an L-shaped warp whose interior dipped below t_min (E_TOO_THIN at (17.0, -3.5)) builds; its borders with
    the base are compared with the builder now (F7)."""
    res = FT.fit_spec(l_warp_design(low, high))
    assert any(c["pair"] == ["L", "base"] and c["deg"] is not None for c in res.creases)
    builds_as_fitted(res)


def test_declared_crease_beside_a_warp_builds_its_shape_to_the_ends():
    """P2-4 (p08): a valley declared beside a twisted warp (its midpoint held at 2.5, the fold -3.1 .. -1.9 along the
    edge) builds as a valley along all of it."""
    res = FT.fit_spec(warp_beside_flat("valley", twist=3.0))
    tb = builds_as_fitted(res)
    b = built_creases(tb)["R|W"]
    assert b["shape"] == "valley" and b["ridge_share"] == 0.0
    assert max(r_w_angle(res, y) for y in np.linspace(-5.5, 5.5, 23)) < 0.0


@pytest.mark.parametrize("crown", ["nodes", "warp"])
def test_real_run7_one_source_fits_build(crown):
    """P2-7: run 7's layout on one source (crown-geometry §2) builds, tool test."""
    builds_as_fitted(FT.fit_spec(run7_layout(crown, rd=RUN7_ONE_SOURCE)))


def test_solved_and_held_corners_build():
    """P1-1 (p02): run 7's flat crown with E null (solved from the crown and shoulder planes) and with H null (held at
    the thickness bound, t_min + roll_mm) build as fitted."""
    for n in ("E", "H"):
        s = run7_layout("nodes")
        s["nodes"][n][2] = None
        builds_as_fitted(FT.fit_spec(s))


def test_steep_edge_declaration_builds_as_declared():
    """The builder's flat rule on a steep edge: the fitted ridge builds a ridge (its mean dihedral >= 2.0), where a
    2.5 deg fold alone builds 1.77 deg, flat (E_CREASE_SHAPE)."""
    res = FT.fit_spec(steep_pair())
    b = built_creases(builds_as_fitted(res))["D|U"]
    assert b["shape"] == "ridge" and b["mean_dihedral_deg"] >= FT.FLAT_CREASE_DEG


# ------------------------------------------------------------------------------------------- v3p2-fit2 (phase 2)
@pytest.mark.parametrize("crown, creases", [
    ("warp", []), ("nodes", []),
    ("warp", [{"facets": ["lower", "shoulder"], "shape": "ridge"}]),          # accepted at a cost (+1.9)
    ("warp", [{"facets": ["crown", "shoulder"], "shape": "ridge"}, {"facets": ["logo", "lower"], "shape": "ridge"},
              {"facets": ["shoulder", "base"], "shape": "valley"}])])
def test_probe1_readings_fit_and_build(crown, creases):
    """probe1's readings.py records through read_payload (pick covariance, camera terms; tool test on a copy's data):
    the fitted spec builds as fitted, its declared shapes built as declared (the camera turns and effective sds change
    the weights, never the spec's form)."""
    res = FT.fit_spec(probe1_design(crown, creases))
    assert "read" not in str(res.spec) and set(res.camera) == {"02", "05"}
    tb = builds_as_fitted(res)
    b = built_creases(tb)
    for c in creases:
        assert b["|".join(c["facets"])]["shape"] == c["shape"]


@pytest.mark.parametrize("which", ["face", "pair"])
def test_camera_turn_fits_build(which):
    """The synthetic camera-turn designs (a flat face and a ridge pair read under a 2.5 sd turn of fit 02) build as
    fitted."""
    res = FT.fit_spec(cam_face({"02": 2.5}) if which == "face" else cam_pair({"02": 2.5}, ridge_deg=3.0))
    builds_as_fitted(res)


# ------------------------------------------------------------------------------------- v3p2-fit2-fix (phase 2 review)
def test_moved_corners_build_where_the_fit_put_them():
    """P1 (one model): the corners the fit moves within their readings, with the outline knots they lie on (and a
    table knot the design's parse had snapped onto one), build where the fit put them, as fitted: probe1's flat crown
    (its tip E 2.9 mm forward, 2.8 sd, to keep the crown flat), its flat-border truth with shoulder|base declared a
    valley and a ridge (accepted at ~1.3, E moved ~0.9 mm across the base station at d 75.64: the review's lever), and
    run 7 with D and E read to sd_d 1.0 and the valley declared (accepted at 2.4; v3p2-fit2 refused it at 61), and a
    draw whose ridge pushes E just beyond snap_mm of the inner knot at d 76.0 (test_temple_fit's
    test_a_corner_beside_a_station_is_held_where_the_declaration_needs_it)."""
    tp = probe1_truth("nodes", flat_border=True)
    s7 = run7_layout("nodes", [{"facets": ["shoulder", "base"], "shape": "valley"}])
    for n, v in s7["nodes"].items():
        v[2]["read"].update(sd_d=1.0 if n in ("D", "E") else 0.3, sd_y=0.3)
    rng = np.random.default_rng(20261012)
    for _ in range(24):
        rd = probe1_draw(tp, rng)
    cases = [(probe1_design("nodes"), None)] + [
        (probe1_reading_design("nodes", tp, [{"facets": ["shoulder", "base"], "shape": sh}]), sh)
        for sh in ("valley", "ridge")] + [(s7, "valley"),
                                          (probe1_reading_design("nodes", rd, [{"facets": ["shoulder", "base"],
                                                                                "shape": "ridge"}]), "ridge")]
    for spec, shape in cases:
        res = FT.fit_spec(spec)
        assert res.chi2 - res.chi2_free <= FT.ACCEPT_DCHI2
        tb = builds_as_fitted(res)
        moved = [n for n, r in res.nodes.items() if r.get("move_mm") and np.hypot(*r["move_mm"]) > 0.3]
        assert moved, shape
        for n in moved:
            assert tuple(tb.spec.nodes[n][:2]) == pytest.approx((res.nodes[n]["d"], res.nodes[n]["y"]), abs=1e-9)
        if shape is not None:
            b = built_creases(tb)["shoulder|base"]
            assert b["shape"] == shape and b["mean_dihedral_deg"] >= FT.FLAT_CREASE_DEG, (shape, b)


def test_moved_corner_releases_snapped_inner_knot_and_builds_as_fitted():
    """Review E: the inner knot snaps to A in the design, but stays at raw d when the fit moves A by 1 mm."""
    s = FX.straight()
    s["outline"]["top"] = [[0., 10.], [40., 10.], [120., 10.]]
    s["inner"] = [[0., 60.], [40.03, 62.], [120., 59.]]
    s["options"]["snap_mm"] = .05
    s["nodes"] = {"A": [40., 10., read(66., .1)], "P": [20., -6., read(65., .1)],
                  "Q": [80., -6., read(64., .1)]}
    s["nodes"]["A"][2]["read"].update(d=41., y=10., sd_d=1.)
    s["facets"] = {"F": {"poly": ["A", "P", "Q"], "plane": "nodes"}}
    original = FT._Problem(s)
    assert original.ps.inner.d[1] == 40.
    assert "A" in original.tpart
    res = FT.fit_spec(s)
    assert res.nodes["A"]["d"] == pytest.approx(41., abs=1e-6)
    tb = builds_as_fitted(res)
    assert tb.spec.inner.d[1] == pytest.approx(40.03)
    for name, node in res.nodes.items():
        assert node["t"] == pytest.approx(node["X"] - tb.spec.inner(node["d"]), abs=1e-9), name
