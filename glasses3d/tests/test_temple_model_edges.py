"""WP6 v3 phase 2, interface I1 (plan/WP6_V3.md §5): build.model_edges, the build's creases per connected run as the
compares draw them, and the crease tables' mean over the same face-to-face edges as their range (the roll and chamfer
bands left out: v3 probe1 printed "crown|shoulder ridge 36.9° (37.7-39.3)", a mean outside its own range); after
review2-builder2-1 also the tables' shape word and shares over those edges, model_edges' soft only where the export
shades the run smooth, and angles that never round across the 2 deg flat line (build.deg_round, build.flat_cut).

Specs here are tool-test geometry on the fixture slab (fixtures.straight: d 0-120, y -10..10, inner face X 60, base
thickness 4) or the fixture's Prada-like outline (fixtures.base_spec), never a product.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re

import numpy as np
import pytest

from glasses3d.g3d.temple import build as BD
from glasses3d.g3d.temple import build_temple, model_edges
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import report as RP
from glasses3d.g3d.temple.spec import FLAT_CREASE_DEG, SHARP_DIHEDRAL_DEG, SOFT_SHARP_DEG, TempleError

T = FX.PRISM["T"]                       # slab base thickness 4 (X 64), inner face X 60
X_IN = FX.PRISM["x_in"]
KEYS = {"pair", "shape", "deg", "range", "deg_at", "pts", "soft"}
PT_MM = 2e-6                            # model_edges rounds points to 1e-6 mm (a gap mixes d and y: <= 2 x 5e-7)


# --------------------------------------------------------------------------------------------- specs (tool tests)
def island(raise_mm: float = 1.5, plane="warp", proud: float = 1.0, **facet) -> dict:
    """A quad Q = A (10, -6), B (40, -6), C (40, 6), D (10, 6) inside the slab, `proud` mm above the base, C a further
    `raise_mm` out (a warp: one corner off the plane of the other three). Its whole border is Q|base: one loop."""
    t = T + proud
    nodes = {"A": [10.0, -6.0, t], "B": [40.0, -6.0, t], "C": [40.0, 6.0, t + raise_mm], "D": [10.0, 6.0, t]}
    return FX.straight(nodes=nodes, facets={"Q": {"poly": ["A", "B", "C", "D"], "plane": plane, **facet}})


def warp_with_neighbour(roll: float = 0.0, soft: bool = False) -> dict:
    """island() plus a flat facet N above it sharing Q's edge D-C (C the raised corner) and reaching the top edge:
    N = D C E F by crease D-C + rise -0.25 (E, F solved). Q soft when `soft`; core roll `roll`."""
    s = island()
    s["nodes"].update({"E": [40.0, 10.0, None], "F": [10.0, 10.0, None]})
    s["facets"]["N"] = {"poly": ["D", "C", "E", "F"], "plane": {"crease": ["D", "C"], "rise_per_mm": -0.25}}
    if soft:
        s["facets"]["Q"]["soft"] = True
    s["roll_mm"] = roll
    return s


def corner_crease(roll: float = 0.6, drop: float = 3.0) -> dict:
    """A warp Q = A (10, -6), B (40, -6), C (40, 7), G (40, 10), D (10, 10) (G on the top edge, 1.5 mm out; C on B-G's
    line) and a small coef facet N = C J H G beside its top right, falling `drop` mm toward d 55: their crease C-G
    runs 3 mm from C up to the top edge, so the roll band holds a fifth of it (probe1's crown|shoulder in small)."""
    t = T + 1.0
    xc, xg = t + 1.5 * 13.0 / 16.0, t + 1.5
    c = (xg - xc) / 3.0
    b = -drop / 15.0
    nodes = {"A": [10.0, -6.0, t], "B": [40.0, -6.0, t], "C": [40.0, 7.0, xc], "G": [40.0, 10.0, xg],
             "D": [10.0, 10.0, t], "H": [55.0, 10.0, None], "J": [55.0, 7.0, None]}
    return FX.straight(nodes=nodes, roll_mm=roll,
                       facets={"Q": {"poly": ["A", "B", "C", "G", "D"], "plane": "warp"},
                               "N": {"poly": ["C", "J", "H", "G"],
                                     "plane": {"coef": [X_IN + xg - 40.0 * b - 10.0 * c, b, c]}}})


def soft_pair(proud: float = 0.5) -> dict:
    """A soft flat facet S (d 20-50, y -5..5) `proud` mm above the slab, leaning -0.03 per mm of y, and a facet G
    sharing its edge at d 50 (by crease B-C + rise -0.02): S|base soft, G|base and G|S never."""
    t = lambda y: T + proud - 0.03 * (y + 5.0)                     # noqa: E731
    nodes = {"A": [20.0, -5.0, t(-5)], "B": [50.0, -5.0, t(-5)], "C": [50.0, 5.0, t(5)], "D": [20.0, 5.0, t(5)],
             "E": [70.0, -5.0, None], "F": [70.0, 5.0, None]}
    return FX.straight(nodes=nodes, facets={"S": {"poly": ["A", "B", "C", "D"], "soft": True},
                                            "G": {"poly": ["B", "E", "F", "C"],
                                                  "plane": {"crease": ["B", "C"], "rise_per_mm": -0.02}}})


CHAMFER = {"edge": "bottom", "d": [5.0, 60.0], "width_mm": 1.2, "depth_mm": 0.6}


def chamfered_pyramid() -> dict:
    """The fixture pyramid with a 1.2 x 0.6 mm chamfer along its bottom edge over d 5-60 (F2|F3 runs into it)."""
    s = FX.pyramid()
    s["chamfers"] = [dict(CHAMFER)]
    return s


def band_vote(declare: str | None = None) -> dict:
    """review2-builder2-1's band_vote: the top outline bends 0.4 mm up at d 60 (y 10 -> 10.4 -> 10) with a 0.6 mm
    roll; facet Q = A (30, -10), A2 (60, -10), A3 (60, -6), B (90, -6), C (90, 10.2), D (30, 10.2) rises 0.2 per mm of
    y from 1.6 mm below the base at y -10. Its top border D-C is a 60 mm chord <= 0.2 mm under the outline, inside the
    roll band for its whole length, and folds as a ridge there; its face-to-face border is the 30 mm valley A3-B (11.3
    deg) and the no-fold sides at d 30, 60 and 90 (A-A2 lies on the bottom outline)."""
    def t(y):
        return T - 1.6 + 0.2 * (y + 10.0)
    nodes = {"A": [30.0, -10.0, t(-10.0)], "A2": [60.0, -10.0, t(-10.0)], "A3": [60.0, -6.0, t(-6.0)],
             "B": [90.0, -6.0, t(-6.0)], "C": [90.0, 10.2, t(10.2)], "D": [30.0, 10.2, t(10.2)]}
    s = FX.straight(nodes=nodes, roll_mm=0.6, facets={"Q": {"poly": ["A", "A2", "A3", "B", "C", "D"]}})
    s["outline"]["top"] = [[0.0, 10.0], [60.0, 10.4], [120.0, 10.0]]
    if declare:
        s["creases"] = [{"facets": ["Q", "base"], "shape": declare}]
    return s


def band_only(declare: str | None = None) -> dict:
    """One facet Q over the whole slab (its corners on the outline), 0.5 mm proud at y -10 and rising 0.05 per mm of
    y, under a top outline bent 0.4 mm up at d 60 with a 0.6 mm roll: its only border with the base is the chord y 10
    from d 0 to 120, inside the roll band for its whole length (Q|base has no face-to-face edge)."""
    def t(y):
        return T + 0.5 + 0.05 * (y + 10.0)
    nodes = {"A": [0.0, -10.0, t(-10.0)], "B": [120.0, -10.0, t(-10.0)], "C": [120.0, 10.0, t(10.0)],
             "D": [0.0, 10.0, t(10.0)]}
    s = FX.straight(nodes=nodes, roll_mm=0.6, facets={"Q": {"poly": ["A", "B", "C", "D"]}})
    s["outline"]["top"] = [[0.0, 10.0], [60.0, 10.4], [120.0, 10.0]]
    if declare:
        s["creases"] = [{"facets": ["Q", "base"], "shape": declare}]
    return s


def flat_pair(deg: float, declare: str | None = None) -> dict:
    """Two coef facets L (d 10-40) and R (d 40-70) over y -10..10 on the slab, flush with the base along L, meeting
    at d 40 with a dihedral of `deg` (R rises out toward the tip: a valley)."""
    b = math.tan(math.radians(deg))
    nodes = {"P0": [10.0, -10.0, None], "P1": [40.0, -10.0, None], "P2": [40.0, 10.0, None], "P3": [10.0, 10.0, None],
             "P4": [70.0, -10.0, None], "P5": [70.0, 10.0, None]}
    s = FX.straight(nodes=nodes, facets={
        "L": {"poly": ["P0", "P1", "P2", "P3"], "plane": {"coef": [X_IN + T, 0.0, 0.0]}},
        "R": {"poly": ["P1", "P4", "P5", "P2"], "plane": {"coef": [X_IN + T - 40.0 * b, b, 0.0]}}})
    if declare:
        s["creases"] = [{"facets": ["L", "R"], "shape": declare}]
    return s


LOCAL = {"island": island, "warp_with_neighbour_rolled_soft": lambda: warp_with_neighbour(0.6, True),
         "corner_crease": corner_crease, "soft_pair": soft_pair, "chamfered_pyramid": chamfered_pyramid}


@pytest.fixture(scope="module")
def builds():
    out = {name: build_temple(fn()) for name, fn in {**FX.FIXTURES, **LOCAL}.items()}
    out["warp_with_neighbour"] = build_temple(warp_with_neighbour())
    out["warp_with_neighbour_soft"] = build_temple(warp_with_neighbour(soft=True))
    return out


# --------------------------------------------------------------------------------------------- helpers
def signed_face_edges(tb) -> dict:
    """{pair: {(a, b): signed angle}} of every face-to-face crease edge (crease_edges, face_edge): + ridge, - valley,
    0 for a facet|base edge without a fold."""
    out: dict = {}
    for a, b, pair, deg, ridge, p1, p2 in BD.crease_edges(tb):
        if BD.face_edge(tb, p1, p2):
            out.setdefault("|".join(pair), {})[(a, b)] = 0.0 if ridge is None else (deg if ridge else -deg)
    return out


def all_edge_mean(tb, pair: str) -> float:
    """The length-weighted mean over EVERY crease edge of a pair (roll and chamfer bands included): the tables'
    mean before phase 2."""
    num = den = 0.0
    for a, b, pr, deg, _ridge, _p1, _p2 in BD.crease_edges(tb):
        if "|".join(pr) == pair:
            L = float(np.linalg.norm(tb.V[a] - tb.V[b]))
            num, den = num + L * deg, den + L
    return num / den


def vertex_ids(tb, pts) -> list:
    """The outer vertex of the build at each [d, y, X] (within 1e-5 mm in (d, y) and in X: the outer surface)."""
    n, P = tb.n2, np.asarray(pts, float)
    D = np.hypot(tb.V[:n, 0][None] - P[:, :1], tb.V[:n, 1][None] - P[:, 1:2])
    ids = D.argmin(axis=1)
    assert D[np.arange(len(P)), ids].max() <= 1e-5 and np.abs(tb.V[ids, 2] - P[:, 2]).max() <= 1e-5
    return [int(i) for i in ids]


def assert_runs(tb, me: list) -> dict:
    """Every invariant of interface I1 on one build; returns {pair: [runs]}. Each run: the keys, deg inside range,
    every deg_at inside it, the shape the sign of deg (flat below FLAT_CREASE_DEG) and of the unrounded mean, the soft
    flag (a soft facet's border run the export shades smooth: none of its edges sharp; a soft border run is sharp or
    smooth as a whole), its points on the build's outer vertices joined by face-to-face crease edges of its pair (no
    roll, chamfer, ramp or warp-interior edge: those have one region on both sides or are no face edge), no vertex
    twice (a loop closes on its first point), deg and range the length-weighted mean and the range of those edges'
    signed angles; the runs of a pair cover each of its face-to-face crease edges once; the pairs come in the crease
    tables' order."""
    edges = signed_face_edges(tb)
    sharp = set(map(tuple, tb.sharp))
    order = ["|".join(c["regions"]) for c in tb.tables["creases"] if c["kind"] == "crease"]
    assert [order.index(e["pair"]) for e in me] == sorted(order.index(e["pair"]) for e in me)
    seen, by_pair = {}, {}
    for e in me:
        assert set(e) == KEYS
        lo, hi = e["range"]
        assert lo <= e["deg"] <= hi and all(lo <= x <= hi for x in e["deg_at"])
        assert len(e["deg_at"]) == len(e["pts"]) >= 2
        assert e["shape"] == shape_of(e["deg"])
        ids = vertex_ids(tb, e["pts"])
        closed = ids[0] == ids[-1]
        assert len(set(ids)) == len(ids) - closed
        segs = [(min(a, b), max(a, b)) for a, b in zip(ids[:-1], ids[1:])]
        for k in segs:
            assert k in edges[e["pair"]] and k not in seen, (e["pair"], k)
            seen[k] = e["pair"]
        f, g = e["pair"].split("|")
        n_sharp = sum(k in sharp for k in segs)
        if g == "base" and tb.spec.facets[f].get("soft"):
            assert n_sharp in (0, len(segs)), (e["pair"], n_sharp, len(segs))
            assert e["soft"] is (n_sharp == 0)
        else:
            assert e["soft"] is False
        s = np.array([edges[e["pair"]][k] for k in segs])
        L = np.linalg.norm(np.diff(tb.V[ids], axis=0), axis=1)
        assert e["deg"] == pytest.approx(float(L @ s / L.sum()), abs=1e-3)
        assert e["shape"] == shape_of(float(np.clip(L @ s / L.sum(), s.min(), s.max())))
        assert e["range"] == pytest.approx([float(s.min()), float(s.max())], abs=1e-3)
        by_pair.setdefault(e["pair"], []).append(e)
    for pair, es in edges.items():
        assert {k for k, p in seen.items() if p == pair} == set(es), pair
    return by_pair


def shape_of(deg: float) -> str:
    return "ridge" if deg >= FLAT_CREASE_DEG else "valley" if deg <= -FLAT_CREASE_DEG else "flat"


def printed(line: str) -> tuple:
    """(mean, lo, hi) of a crease line "F1|F2 shape 12.3° (11.0-14.2) ..." (lo, hi None when no range is printed)."""
    m = re.match(r"\S+ \w+ (-?[\d.]+)°(?: \((-?[\d.]+)-(-?[\d.]+)\))?", line)
    return float(m.group(1)), (None if m.group(2) is None else float(m.group(2))), \
        (None if m.group(3) is None else float(m.group(3)))


# --------------------------------------------------------------------------------------------- model_edges
def test_model_edges_on_the_flat_prada_layout(builds):
    """Planar facets: one run per crease, its angle the table's (one crease, one number), constant along it (range
    lo = hi), the declared shapes; F1|F2 runs from A (the hinge-end top corner) to B, every point further along."""
    tb = builds["prada_default"]
    me = model_edges(tb)
    by_pair = assert_runs(tb, me)
    table = {"|".join(c["regions"]): c for c in tb.tables["creases"] if c["kind"] == "crease"}
    assert set(by_pair) == set(table) and all(len(r) == 1 for r in by_pair.values())
    for pair, (e,) in by_pair.items():
        c = table[pair]
        assert e["shape"] == c["shape"] == c["declared"]
        assert abs(e["deg"]) == pytest.approx(c["mean_dihedral_deg"], abs=1e-3)
        assert e["range"][1] - e["range"][0] <= 1e-3
        assert e["soft"] is False
    (e,) = by_pair["F1|F2"]
    A, B = (np.array(tb.spec.nodes[n][:2]) for n in ("A", "B"))
    P = np.array(e["pts"])[:, :2]
    assert np.allclose(P[0], A, atol=1e-6) and np.allclose(P[-1], B, atol=1e-6)
    along = (P - A) @ (B - A) / np.linalg.norm(B - A)
    assert np.all(np.diff(along) > 0) and len(P) > 3          # stations cut it; ordered A -> B
    assert json.loads(json.dumps(me)) == me                     # plain JSON


def test_a_border_all_round_a_facet_is_one_closed_loop(builds):
    """An island facet's border with the base is one run that closes on its first point: it starts at its corner of
    smallest (d, y) (A) and runs counter-clockwise A -> B -> C -> D; the warp's internal edges are no creases (no
    Q|Q entry). The warp's border is a ridge on one side and a valley on the other: the run carries the sign per
    point (deg_at, range -6.0..+5.9) and its shape is its signed mean's (flat), never one shape for the whole loop."""
    tb = builds["island"]
    me = model_edges(tb)
    assert_runs(tb, me)
    (e,) = me
    assert e["pair"] == "Q|base" and e["pts"][0] == e["pts"][-1] and e["deg_at"][0] == e["deg_at"][-1]
    P = np.array(e["pts"])[:, :2]
    assert np.sum(P[:-1, 0] * P[1:, 1] - P[1:, 0] * P[:-1, 1]) > 0         # counter-clockwise in (d, y)
    corners = [int(np.argmin(np.hypot(*(P - np.array(tb.spec.nodes[n][:2])).T))) for n in "ABCD"]
    assert corners == sorted(corners) and corners[0] == 0
    lo, hi = e["range"]
    assert lo < -5.0 and hi > 5.0 and e["shape"] == "flat"
    assert min(e["deg_at"]) < -5.0 and max(e["deg_at"]) > 5.0


def test_a_crease_beside_a_warp_varies_along_its_run(builds):
    """N|Q beside the warp: one run D -> C whose angle grows toward the raised corner C (deg_at), its mean and range
    the crease table's (one crease, one number); N|base is two runs (at d 10 a valley, at d 40 a ridge), each with its
    own shape, where the table holds one shape for the pair."""
    tb = builds["warp_with_neighbour"]
    by_pair = assert_runs(tb, model_edges(tb))
    (e,) = by_pair["N|Q"]
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["N", "Q"])
    assert e["deg"] == pytest.approx(c["mean_dihedral_deg"], abs=1e-3) and e["shape"] == c["shape"] == "ridge"
    assert e["range"] == pytest.approx(c["range_deg"], abs=1e-3) and e["range"][1] - e["range"][0] > 5.0
    assert e["pts"][0][:2] == [10.0, 6.0] and e["pts"][-1][:2] == [40.0, 6.0]
    assert e["deg_at"][-1] - e["deg_at"][0] > 5.0
    runs = by_pair["N|base"]
    assert [(r["pts"][0][0], r["shape"]) for r in runs] == [(10.0, "valley"), (40.0, "ridge")]


def test_soft_borders_are_flagged(builds):
    """soft: exactly the runs of a soft facet's border with the base (a warp or a flat facet); a crease between two
    facets is never soft, nor a border of a facet that is not soft."""
    for name, soft in (("warp_with_neighbour_soft", "Q"), ("soft_pair", "S"), ("warp_with_neighbour_rolled_soft", "Q")):
        tb = builds[name]
        me = model_edges(tb)
        assert_runs(tb, me)
        assert {e["pair"] for e in me if e["soft"]} == {f"{soft}|base"}
        assert any(not e["soft"] and "base" not in e["pair"] for e in me)
    assert not any(e["soft"] for e in model_edges(builds["warp_with_neighbour"]))


def test_the_roll_band_is_left_out(builds):
    """With the core roll the creases stop where the roll band starts (every point >= roll_mm from the outline),
    planar creases keep exactly the angle they have without the roll (the pyramid's), and the tables' mean is that
    same angle: over every edge the band's sub-band edges (their dihedral falls toward 0 at the outline: the all-edge
    minimum is far below) pulled F2|F3 to 7.7 against its 7.84."""
    rolled, plain = builds["rolled"], builds["pyramid"]
    r = rolled.spec.roll_mm
    me = model_edges(rolled)
    by_pair = assert_runs(rolled, me)
    P = np.array([p for e in me for p in e["pts"]])
    gap = np.minimum(rolled.spec.y_top(P[:, 0]) - P[:, 1], P[:, 1] - rolled.spec.y_bot(P[:, 0]))
    assert gap.min() >= r - PT_MM
    ref = {e["pair"]: e for e in model_edges(plain)}
    assert set(by_pair) == set(ref)
    table = {"|".join(c["regions"]): c for c in rolled.tables["creases"] if c["kind"] == "crease"}
    for pair in ("F2|F3", "F2|F4"):
        (e,) = by_pair[pair]
        assert e["deg"] == pytest.approx(ref[pair]["deg"], abs=1e-3)
        c = table[pair]
        assert c["mean_dihedral_deg"] == pytest.approx(abs(ref[pair]["deg"]), abs=1e-3)
        assert c["range_deg"][0] - 1e-9 <= c["mean_dihedral_deg"] <= c["range_deg"][1] + 1e-9
        assert c["dihedral_deg"][0] < c["mean_dihedral_deg"] - 1.0
        assert all_edge_mean(rolled, pair) < c["range_deg"][0] - 0.05            # the old mean: outside the range
    rw = builds["warp_with_neighbour_rolled_soft"]
    tops = [e["pts"][-1][1] for e in model_edges(rw) if e["pair"] == "N|base"]
    assert tops == pytest.approx([10.0 - rw.spec.roll_mm] * 2, abs=PT_MM)


def test_the_chamfer_band_is_left_out(builds):
    """A chamfer band, like the roll, is no face: F2|F3 stops on the chamfer line (1.2 mm above the bottom edge) and
    keeps the pyramid's angle in model_edges and in the table, where its chamfer edges fold less."""
    tb, plain = builds["chamfered_pyramid"], builds["pyramid"]
    by_pair = assert_runs(tb, model_edges(tb))
    (e,) = by_pair["F2|F3"]
    end = np.array(e["pts"][-1])
    assert end[1] - float(tb.spec.y_bot(end[0])) == pytest.approx(CHAMFER["width_mm"], abs=PT_MM)
    ref = next(x for x in model_edges(plain) if x["pair"] == "F2|F3")
    assert e["deg"] == pytest.approx(ref["deg"], abs=1e-3)
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["F2", "F3"])
    assert c["mean_dihedral_deg"] == pytest.approx(ref["deg"], abs=1e-3)
    assert c["dihedral_deg"][0] < c["mean_dihedral_deg"] - 0.5 and all_edge_mean(tb, "F2|F3") < c["range_deg"][0]


def test_ramp_and_warp_interiors_are_no_creases(builds):
    """Every model edge point lies on a facet polygon's boundary: none on a border band's line (2 mm into the base),
    inside a ramp, or inside a warp, though those builds fold there (ramp edges >= SHARP_DIHEDRAL_DEG, warp triangle
    edges). (Its faded-border case went with the fades on 2026-10-13.)"""
    from glasses3d.g3d.temple.build import mesh_topology
    for name in ("border_ramp", "island", "warp_with_neighbour", "prada_default"):
        tb = builds[name]
        me = model_edges(tb)
        assert_runs(tb, me)
        segs = [(np.array(tb.spec.nodes[p][:2]), np.array(tb.spec.nodes[q][:2]))
                for f in tb.spec.facets.values() for p, q in zip(f["poly"], f["poly"][1:] + f["poly"][:1])]
        for e in me:
            for p in np.array(e["pts"])[:, :2]:
                dist = min(np.linalg.norm(p - (a + np.clip((p - a) @ (b - a) / ((b - a) @ (b - a)), 0, 1) * (b - a)))
                           for a, b in segs)
                assert dist <= 1e-6, (name, e["pair"], p)
    tb = builds["border_ramp"]
    topo = mesh_topology(tb.polys)
    ramp_folds = [BD._dihedral(tb.normals[p1], tb.normals[topo["directed"][(b, a)]])
                  for (a, b), p1 in topo["directed"].items()
                  if a < b and tb.poly_cell[p1] == "ramp"
                  and tb.poly_cell[topo["directed"][(b, a)]] in ("ramp", "base")]
    assert max(ramp_folds) >= SHARP_DIHEDRAL_DEG                    # the ramp folds, yet draws no crease
    tb = builds["island"]
    D = mesh_topology(tb.polys)["directed"]
    assert any(tb.poly_cell[p1] == "warp" and tb.poly_cell[D[(b, a)]] == "warp" for (a, b), p1 in D.items())
    #                                                                 (triangle edges inside the warp: no creases)


def test_runs_split_at_branches_and_close_loops():
    """_run_paths on a synthetic edge set: a T (a branch where three edges meet), a square loop and a figure eight (a
    vertex where four meet): every edge in exactly one run, a run passes only through vertices of degree 2 and ends
    at a vertex of another degree or closes; an open run starts at its end of smaller (d, y), a loop at its vertex
    of smaller (d, y) and runs counter-clockwise."""
    V2 = np.array([[0, 0], [1, 0], [2, 0], [1, 1],                       # T: 0-1-2 and 1-3
                   [10, 0], [11, 0], [11, 1], [10, 1],                   # square 4-5-6-7
                   [20, 0], [21, 1], [21, -1], [19, 1], [19, -1]], float)  # figure eight at 8
    edges = [(2, 1), (0, 1), (1, 3), (4, 7), (7, 6), (6, 5), (5, 4), (8, 9), (9, 10), (10, 8), (8, 11), (11, 12),
             (12, 8)]
    runs = BD._run_paths(edges, V2)
    deg = {}
    for a, b in edges:
        deg[a], deg[b] = deg.get(a, 0) + 1, deg.get(b, 0) + 1
    used = sorted(k for _, ks, _ in runs for k in ks)
    assert used == list(range(len(edges)))
    for path, ks, closed in runs:
        assert [frozenset(edges[k]) for k in ks] == [frozenset(e) for e in zip(path[:-1], path[1:])]
        assert all(deg[v] == 2 for v in path[1:-1])
        assert closed == (path[0] == path[-1] and deg[path[0]] == 2)
        if not closed and path[0] != path[-1]:
            assert tuple(V2[path[0]]) < tuple(V2[path[-1]])
    loop = next(p for p, _, c in runs if c)
    assert loop == [4, 5, 6, 7, 4]                                       # from (10, 0), counter-clockwise
    assert sorted(len(ks) for _, ks, _ in runs) == [1, 1, 1, 3, 3, 4]


def test_model_edges_pass_the_compare_check(builds):
    """seeing's model_edges check (interface I7's other side) accepts every build's output as it is: <= 64 edges,
    <= 4000 points each, shape and deg never contradicting, deg_at aligned with pts."""
    from glasses3d.agent.seeing import Seeing
    for name in ("prada_default", "island", "warp_with_neighbour_soft", "corner_crease", "rolled"):
        tb = builds[name]
        me = model_edges(tb)
        out = Seeing._check_v3_args(None, None, me, None, tb.spec.hinge["z_mm"])
        assert len(out["edges"]) == len(me) and out["edges_sha256"]


def test_model_edges_are_deterministic(builds):
    """The same build gives the same JSON (the compares' cache key hashes it)."""
    for name, fn in (("island", island), ("warp_with_neighbour", warp_with_neighbour), ("rolled", FX.rolled)):
        assert json.dumps(model_edges(builds[name])) == json.dumps(model_edges(build_temple(fn())))


# --------------------------------------------------------------------------------------------- review2-builder2-1
def test_the_table_word_is_the_face_to_face_edges_vote():
    """review2-builder2-1 P2 (band_vote): the tables' shape, its vote and its shares come from the face-to-face edges,
    the same edges as their mean, range and model_edges. Q|base's face-to-face edges are one 30 mm valley (11.3 deg)
    and 38.75 mm without a fold; the roll band holds a 60 mm ridge (its top border under the bent outline). The word
    was voted over every edge: "Q|base ridge 4.9°" (the valley's mean under the band's word), a declared ridge built
    and a declared valley was refused as "ridge 4.9 deg (a ridge along 45% of its 132.6 mm ...)". Now: valley 4.9, a
    declared ridge is refused with the face-to-face share and length, a declared valley builds. length_mm, ridge and
    valley (mm) stay every edge's, as the chart draws each edge by its own fold."""
    tb = build_temple(band_vote())
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["Q", "base"])
    face, band = {"ridge": 0.0, "valley": 0.0, "none": 0.0}, {"ridge": 0.0, "valley": 0.0, "none": 0.0}
    for a, b, pr, _deg, ridge, p1, p2 in BD.crease_edges(tb):
        if pr == ("Q", "base"):
            k = "none" if ridge is None else ("ridge" if ridge else "valley")
            (face if BD.face_edge(tb, p1, p2) else band)[k] += float(np.linalg.norm(tb.V[a] - tb.V[b]))
    assert face["ridge"] == 0.0 and face["valley"] == pytest.approx(30.0) and band["ridge"] > 2.0 * face["valley"]
    assert c["shape"] == "valley" and c["mean_dihedral_deg"] == pytest.approx(4.935, abs=1e-3)
    assert c["face_length_mm"] == pytest.approx(sum(face.values()))
    assert c["ridge_share"] == 0.0 and c["valley_share"] == pytest.approx(30.0 / c["face_length_mm"])
    assert (c["ridge"], c["valley"]) == (pytest.approx(band["ridge"]), pytest.approx(30.0))
    assert c["length_mm"] == pytest.approx(sum(face.values()) + sum(band.values()))
    me = [e for e in model_edges(tb) if e["pair"] == "Q|base"]
    assert sorted(e["shape"] for e in me) == ["flat", "valley"]           # model_edges: the same edges' sign
    line = next(ln for ln in RP.crease_lines(tb) if ln.startswith("Q|base "))
    assert line == "Q|base valley 4.9° (proud 2.44 mm: a 50.7° ramp) undeclared"
    with pytest.raises(TempleError) as e:
        build_temple(band_vote("ridge"))
    assert e.value.code == "E_CREASE_SHAPE" and e.value.fields["built"] == "valley"
    assert e.value.message == ("crease Q|base is declared ridge but the face against the arm is valley 4.9 deg (a "
                               "valley along 44% of its 68.8 mm; the facet stands proud 2.44 mm there: a 50.7 deg "
                               "ramp)")
    assert e.value.fields["failures"][0]["ridge_share"] == 0.0
    c2 = next(c for c in build_temple(band_vote("valley")).tables["creases"] if c["regions"] == ["Q", "base"])
    assert c2["shape"] == c2["declared"] == "valley"


def test_a_pair_in_the_bands_alone_votes_over_every_edge(builds):
    """A pair with no face-to-face edge (band_only: Q|base lies in the roll band alone) keeps every edge's mean, vote
    and shares (face_length_mm 0, range_deg None, no model_edges run), and its refusal states every edge's share and
    length; a pair without band edges has face_length_mm = length_mm and the shares it always had (ridge / length_mm);
    every crease of the rolled fixture reaches into its band."""
    tb = build_temple(band_only())
    (c,) = [c for c in tb.tables["creases"] if c["kind"] == "crease"]
    assert c["regions"] == ["Q", "base"] and c["face_length_mm"] == 0.0 and c["range_deg"] is None
    assert c["shape"] == "ridge" and c["ridge_share"] == 1.0 and c["valley_share"] == 0.0
    assert c["length_mm"] == pytest.approx(120.0, abs=0.1) and c["mean_dihedral_deg"] == pytest.approx(
        all_edge_mean(tb, "Q|base"), abs=1e-9)
    assert model_edges(tb) == []
    with pytest.raises(TempleError) as e:
        build_temple(band_only("valley"))
    assert "is ridge 2.9 deg (a ridge along 100% of its 120.0 mm; " in e.value.message
    for name, tb in builds.items():
        for c in tb.tables["creases"]:
            if c["kind"] != "crease":
                continue
            assert c["ridge_share"] + c["valley_share"] <= 1.0 + 1e-9
            if c["face_length_mm"] == c["length_mm"]:
                assert c["ridge_share"] == c["ridge"] / c["length_mm"], (name, c["regions"])
                assert c["valley_share"] == c["valley"] / c["length_mm"], (name, c["regions"])
    tb = builds["rolled"]
    assert all(0 < c["face_length_mm"] < c["length_mm"] for c in tb.tables["creases"] if c["kind"] == "crease")


def test_a_soft_border_built_sharp_is_no_soft_run():
    """review2-builder2-1 P3: soft follows the export. A soft facet 0.5 or 2.0 mm proud: its border run is shaded
    smooth (no edge in tb.sharp) and is soft (dashed grey, unlabelled in the compares). 4.5 mm proud: the 6 mm soft
    band's ramp folds >= SOFT_SHARP_DEG along most of the run, _sharp_edges makes the whole run sharp, and model_edges
    gives it as a crease line (soft false; it was soft with 100 % of its edges sharp at 33-76 deg built). The tables'
    and the receipt's "soft" stay the facet's spec key."""
    for proud, soft in ((0.5, True), (2.0, True), (4.5, False)):
        tb = build_temple(soft_pair(proud))
        me = model_edges(tb)
        assert_runs(tb, me)
        runs = [e for e in me if e["pair"] == "S|base"]
        assert runs and all(e["soft"] is soft for e in runs), proud
        sharp = set(map(tuple, tb.sharp))
        topo = BD.mesh_topology(tb.polys)["directed"]
        for e in runs:
            ids = vertex_ids(tb, e["pts"])
            segs = [(min(a, b), max(a, b)) for a, b in zip(ids[:-1], ids[1:])]
            assert all((k in sharp) is not soft for k in segs), proud
            built = [BD._dihedral(tb.normals[topo[(a, b)]], tb.normals[topo[(b, a)]]) for a, b in segs]
            if not soft:
                assert max(built) >= SOFT_SHARP_DEG
        c = next(c for c in tb.tables["creases"] if c["regions"] == ["S", "base"])
        assert c["soft"] is True and " soft" in RP.crease_line(c)


def test_a_crease_just_under_the_flat_line_never_reads_2():
    """review2-builder2-1 P3: L|R at 1.9997 deg is flat in the table; model_edges classified the ROUNDED angle and said
    "valley -2.0", the crease line printed "flat 2.0°" and W_FLAT_CREASE "bends only 2.00 deg (< 2.0)" (false at the
    printed precision). Now every printed or rounded angle stays on its side of the flat line: "flat 1.9°", "1.99",
    model_edges flat -1.999; a declaration on it is refused as flat 1.99. At 2.0003 the pair is a valley 2.0
    everywhere."""
    tb = build_temple(flat_pair(1.9997))
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["L", "R"])
    assert c["shape"] == "flat" and c["mean_dihedral_deg"] == pytest.approx(1.9997, abs=1e-6)
    assert RP.crease_line(c) == "L|R flat 1.9° undeclared"
    assert next(r for r in RP.receipt(tb)["creases"] if r["pair"] == "L|R")["deg"] == 1.9
    assert [w["message"] for w in tb.warnings if w["code"] == "W_FLAT_CREASE" and w["regions"] == ["L", "R"]] == \
        ["crease L|R bends only 1.99 deg (< 2.0)"]
    me = model_edges(tb)
    assert_runs(tb, me)
    (e,) = [e for e in me if e["pair"] == "L|R"]
    assert (e["shape"], e["deg"], e["range"], set(e["deg_at"])) == ("flat", -1.999, [-1.999, -1.999], {-1.999})
    with pytest.raises(TempleError) as err:
        build_temple(flat_pair(1.9997, "valley"))
    assert err.value.message == ("crease L|R is declared valley but built flat 1.99 deg (flat (< 2 deg): no ridge or "
                                 "valley)")
    tb = build_temple(flat_pair(1.1997))                    # away from the line: rounded as before ("1.20", not cut)
    assert [w["message"] for w in tb.warnings if w["code"] == "W_FLAT_CREASE" and w["regions"] == ["L", "R"]] == \
        ["crease L|R bends only 1.20 deg (< 2.0)"]
    assert next(ln for ln in RP.crease_lines(tb) if ln.startswith("L|R ")) == "L|R flat 1.2° undeclared"
    tb = build_temple(flat_pair(2.0003, "valley"))
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["L", "R"])
    assert c["shape"] == "valley" and RP.crease_line(c) == "L|R valley 2.0° declared valley"
    (e,) = [e for e in model_edges(tb) if e["pair"] == "L|R"]
    assert (e["shape"], e["deg"]) == ("valley", -2.0)


def test_rounding_never_crosses_the_flat_line():
    """deg_round (crease lines, receipt, model_edges, W_FLAT_CREASE): non-decreasing, within one unit of the last
    digit, the unrounded value's shape, round(x, nd) away from the flat line. flat_cut (E_CREASE_SHAPE's flat angle):
    cut toward zero, never up to the flat line, 0.29 -> 0.29 (not 0.28 by binary)."""
    rnd = random.Random(20261012)
    edge = [s * v for s in (1.0, -1.0) for v in (0.0, 0.05, 0.29, 1.85, 1.9, 1.94999, 1.95, 1.95001, 1.9985, 1.99949,
                                                  1.9995, 1.9997, 1.99999999, 2.0, 2.00001, 2.0004, 2.05, 12.35)]
    xs = sorted(edge + [rnd.uniform(-3.0, 3.0) for _ in range(4000)] + [rnd.uniform(-60.0, 60.0) for _ in range(500)])
    for nd in (1, 2, 3):
        r = [BD.deg_round(x, nd) for x in xs]
        assert all(a <= b for a, b in zip(r, r[1:])), nd
        for x, y in zip(xs, r):
            assert shape_of(y) == shape_of(x) and abs(y - x) < 10.0 ** -nd + 1e-12, (nd, x, y)
            if abs(x) >= FLAT_CREASE_DEG or abs(round(x, nd)) < FLAT_CREASE_DEG:
                assert y == round(x, nd)
    assert BD.deg_round(1.9997, 3) == 1.999 and BD.deg_round(-1.9997, 3) == -1.999 and BD.deg_round(1.97, 1) == 1.9
    assert json.dumps(BD.deg_round(-0.0001, 3)) == "0.0"
    for x in xs:
        if abs(x) < FLAT_CREASE_DEG:
            for nd in (1, 2):
                y = BD.flat_cut(x, nd)
                assert abs(y) < FLAT_CREASE_DEG and abs(y) <= abs(x) + 1e-6 * 10.0 ** -nd
                assert abs(x) - abs(y) < 10.0 ** -nd
    assert f"{BD.flat_cut(0.29):.2f}" == "0.29" and BD.flat_cut(0.687) == 0.68 and BD.flat_cut(1.9997) == 1.99


# --------------------------------------------------------------------------------------------- the tables' mean
def test_a_printed_mean_lies_inside_its_printed_range(builds):
    """corner_crease reproduces probe1's line in small: N|Q beside a warp, a fifth of it in the roll band. Its mean
    over every edge (12.1) lies below the face-to-face range it is printed with (13.2-13.4); the table's mean is now
    over the same edges as the range, the printed line reads "N|Q ridge 13.2° (13.2-13.4)", and so for every crease
    of every build here, raw and printed."""
    tb = builds["corner_crease"]
    c = next(c for c in tb.tables["creases"] if c["regions"] == ["N", "Q"])
    lo, hi = c["range_deg"]
    assert c["warp_beside"] and all_edge_mean(tb, "N|Q") < lo - 0.5
    line = next(ln for ln in RP.crease_lines(tb) if ln.startswith("N|Q "))
    assert line == f"N|Q ridge {c['mean_dihedral_deg']:.1f}° ({lo:.1f}-{hi:.1f}) undeclared"
    assert re.match(r"N\|Q ridge 13\.\d° \(13\.\d-13\.\d\) undeclared", line)
    for name, tb in builds.items():
        lines = iter(RP.crease_lines(tb))
        for c in tb.tables["creases"]:
            if c["kind"] != "crease":
                continue
            m, plo, phi = printed(next(lines))
            if c["range_deg"] is not None:
                assert c["range_deg"][0] - 1e-9 <= c["mean_dihedral_deg"] <= c["range_deg"][1] + 1e-9, name
            if plo is not None:
                assert plo <= m <= phi, (name, c["regions"])


def test_without_roll_or_chamfer_the_mean_is_every_edges(builds):
    """The change only moves the mean of builds with a roll or a chamfer band: elsewhere every crease edge is face to
    face and the mean is the all-edge mean it always was."""
    for name, tb in builds.items():
        if tb.spec.roll_mm > 0 or tb.spec.chamfers:
            continue
        for c in tb.tables["creases"]:
            if c["kind"] == "crease":
                assert c["mean_dihedral_deg"] == pytest.approx(all_edge_mean(tb, "|".join(c["regions"])), abs=1e-9)


# --------------------------------------------------------------------------------------------- geometry unchanged
def geometry_digest(tb) -> str:
    """sha256 of the build's geometry: vertices, polygons, triangles, sharp edges, polygon normals."""
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(tb.V).tobytes())
    h.update(json.dumps([list(map(int, p)) for p in tb.polys]).encode())
    h.update(np.ascontiguousarray(tb.tris).tobytes())
    h.update(json.dumps([list(map(int, e)) for e in tb.sharp]).encode())
    h.update(np.ascontiguousarray(tb.normals).tobytes())
    return h.hexdigest()


# computed with the core as phase 1 left it (wp6-core-3.0 before model_edges and the face-to-face mean), 2026-10-12
GEOMETRY_BEFORE = {
    "analytic_prism": "f894214ccd8142f3e135b5cf2aaed55145dbfe7ee9f62d8837faa713435b2529",
    "arm_pyramid": "bdfea46a5926f1f4cdb15de298a9022fe4a8872681919665d69e5ad57b2a7e94",
    "border_ramp": "59aaef5ffeca726f3ab85d2f1f7e34f10759cc7aa64cff51a3cbf74985152fe2",
    "chamfered_pyramid": "f9ebbc239c7fae1aa5f02fce7f12184d5e9bd677edfaa8518adfe372f8cd936f",
    "chamfered_slab_30": "61148e49165ad9b927a8f39ab5683b575a9aa38630ffb83cf19397f81b4793d2",
    "corner_crease": "24fb57404c9881c3311c977948c584b679f9969a4199707c6f267fd5b2504b84",
    "crease_rise": "298b9d0dc82fbbd14a56caef07791193b38b88bd397c4a4468eaa1aaf4c7bdd7",
    "island": "c791ef8142380674821b143bdfbd06e11f14fe56e61353093597f06a34326483",
    "prada_default": "9c0c870e44dac0b11aa22a0c9edda5446fb0de8a907f1110da5ac177ca334547",
    "pyramid": "19b41f750f3ac655c0c9c425981a94a4213408967e980ca649b19ee04da7ed57",
    "rolled": "15f5b5cf0e00629d17eb44a5602e93ef3d908bcfef5e5b2496e043ffeb45588b",
    "slab": "15aa63aa9f7373d01db1a1b288366bf686e401344198b4bfaa2c6fb7ed62bc94",
    "soft_pair": "fc414795bfb5d525b75cf42ee24529451bd0b72ee674c8347e435980a42f4220",
    "solved_node": "24e0799b5d24e63b7411795303d0a126faa5d46dc4ab343ca93339ab94a5a002",
    "top_chamfer_arm": "e757976a73ad6fb1093114936dfb98bfbaa6bdc2068d7ca978653171369f9bc7",
    "valley": "b52ab7bdca50f2afe2737cbf20d89512e63ea38efff53953e4454fd5676b2dc0",
    "virtual_node": "eec7d34b01e598563629c36dfe0d283ce1d825533c2d553c73b88461697791a9",
    "warp_with_neighbour_rolled_soft": "0500a05c398f7d8947f6bdcd922f933cd1bf851dbb755406ce9bd651b7c78e2c",
    "wedge": "d879c0fee4ec793a6f32a2c311f149cc8466a6269543ea0bf4fb365f3c4790df",
}


def test_old_specs_build_bit_identically(builds):
    """model_edges and the face-to-face mean change tables only: every fixture and every spec here builds the same
    vertices, polygons, triangles, sharp edges and normals, bit for bit, as before them (the census over all 136
    buildable distinct specs on disk is in data/wp6v2/review_20261012/v3/v3p2-builder2.md)."""
    assert set(GEOMETRY_BEFORE) == set(FX.FIXTURES) | set(LOCAL)
    assert {name: geometry_digest(builds[name]) for name in GEOMETRY_BEFORE} == GEOMETRY_BEFORE
