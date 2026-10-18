"""g3d.temple/2 core build (plan/WP6_FACETS.md §2.2): closed exact solids, planar facet / inner / strip / cap
polygons, non-facet outer cells as ear-clip triangles, values (facets, ramps, base), stations, sharp sets, crease
check, compact receipt, mirror and opening, bit-identical rebuilds, refusals and the budget. (The fade tests, with
run 2's r0015 and run 6's r0013 replays, were deleted with the fades on 2026-10-13.)"""
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
from glasses3d.g3d.temple.build import mesh_topology, to_local, to_world
from glasses3d.g3d.temple.spec import REFUSALS, TempleError

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
            assert len(p) == 3, (k, c)                         # base, warp and ramp cells: ear-clip triangles
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
            if l1 != l2:                                       # facet/facet and facet/base: always
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


def test_facet_corners_take_the_face_normal(builds):
    tb = builds["prada_default"]
    assert {tb.poly_region[i] for i in tb.flat_polys} == {"F1", "F2", "F3", "F4"}
    assert all(tb.poly_cell[i] == "facet" and tb.poly_kind[i] == "outer" for i in tb.flat_polys)


REMEDIES = ("fix the node values", "do not redeclare", "start the fade later", "keep ", "check the photos")


def test_crease_shape_states_facts_only():
    """E_CREASE_SHAPE states what was declared, what was built and over how much of the crease, and offers no remedy
    (WP6_V3 P6; FINDINGS §3: "fix the node values, do not redeclare" and "start the fade later" were single offered
    remedies Astra executed literally; review-builder-design F5): a crease built with the other shape (the case of
    a crease a fade flattened went with the fades on 2026-10-13)."""
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


def test_border_ramp_text_has_units_and_words():
    """report.ramp_clause: a border-ramp run in words and units, the form of crease_line's step clause. v3 session2
    item 8: the lab printed "shoulder +0.81 7.7° d 75.5-76.7" (a bare signed number) beside the crease line "proud
    0.81 mm: a 7.7° ramp" of the same run. The step clause in crease_line is unchanged (the lab finds it there:
    lab.step_text, crease_head). report.ramp_text was deleted 2026-10-13 (the lab keeps its own, lab.ramp_text)."""
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
    for name in ("prada_default", "valley"):
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
    assert r["checks"] == {"closed": True, "euler": 2, "volume_mm3": round(tb.checks["volume_mm3"], 1), "planar": True,
                           "float32_degenerate_triangles": 0}
    assert r["budget"]["core_ms"] == 12.3 and r["spec_sha256"] == tb.spec.sha256
    assert len(text) <= 1500, len(text)                                # 1377 chars on 2026-10-11 (2061 with stations)
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
PRISM_T = FX.PRISM["T"]


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
