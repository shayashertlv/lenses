"""The core roll of g3d.temple/2 (plan/WP6_FACETS.md §2.5, §5 T1): 8 stacked quarter-arc chamfer sub-bands along the
top and bottom outline edges; closed, Euler 2, planar facet cells, the side silhouette unchanged, the measured roll
width; the limit (E_ROLL) and the proximity refusal. The owner-preferred spec cases skip without the data file."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import measure as MS
from glasses3d.g3d.temple.build import chamfer_depth, mesh_topology
from glasses3d.g3d.temple.convert import v1_to_v2
from glasses3d.g3d.temple.spec import ROLL_BANDS, TempleError, roll_profile

G3D = Path(__file__).resolve().parents[1]
PREFERRED = G3D / "data" / "wp6" / "pose_arm_resume_20261010" / "distal_all_views" / "before" / "spec.json"
PREFERRED_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
WIDTH_THRESHOLD_MM = 1e-4          # section deviation that counts as rolled: ~3x the base-triangulation noise (3e-5)


def silhouette(tb) -> tuple[float, float]:
    """(largest overshoot of any vertex beyond the outline, largest distance of an outline knot from the boundary)."""
    ps = tb.spec
    d, y = tb.V[:, 0], tb.V[:, 1]
    over = float(np.max(np.r_[y - ps.y_top(d), ps.y_bot(d) - y]))
    B = tb.arr.V[tb.arr.boundary_loop]
    miss = max(float(np.min(np.hypot(B[:, 0] - p[0], B[:, 1] - p[1]))) for ch in (ps.top, ps.bottom) for p in ch)
    return over, miss


def check_rolled(tb, r: float) -> None:
    c = tb.checks
    assert c["closed"] and c["euler"] == 2 and c["volume_mm3"] > 0
    assert c["planarity_max_mm"] <= 1e-9
    over, miss = silhouette(tb)
    assert over <= 1e-9 and miss <= 1e-9                       # the side silhouette is the outline
    roll = [i for i, k in enumerate(tb.poly_kind) if k == "roll"]
    assert roll and all(tb.poly_cell[i] in ("facet", "base", "ramp", "faded") for i in roll)
    ps, V = tb.spec, tb.arr.V
    loop = np.array(tb.arr.boundary_loop)
    edge = loop[~np.isin(V[loop, 0], [ps.d0, ps.d1])]
    depth = tb.X_surface[edge] - tb.X[edge]
    assert np.allclose(depth, r, atol=1e-9)                     # the strip's outer edge moves in to X - r


def test_rolled_fixture_geometry_and_sub_bands():
    r = 0.6
    tb = build_temple(FX.rolled(r))
    check_rolled(tb, r)
    s, cdep = roll_profile(r)
    ps, V = tb.spec, tb.arr.V
    st = ps.y_top(V[:, 0]) - V[:, 1]
    for k in range(1, ROLL_BANDS + 1):                          # every sub-band line carries its arc depth
        on = np.abs(st - s[k]) < 1e-9
        assert on.sum() > 20
        assert np.allclose(tb.X_surface[on] - tb.X[on], cdep[k], atol=1e-9)
    facet_roll = [i for i, (k, c) in enumerate(zip(tb.poly_kind, tb.poly_cell)) if k == "roll" and c == "facet"]
    assert facet_roll and any(len(tb.polys[i]) > 3 for i in facet_roll)      # planar n-gons on facets
    assert not any(i in tb.flat_polys for i in facet_roll)                    # the rounded band is auto-smoothed


def test_roll_sharp_rules():
    tb = build_temple(FX.rolled(0.6))
    sharp = set(tb.sharp)
    topo = mesh_topology(tb.polys)
    seen = set()
    for (a, b), p1 in topo["directed"].items():
        if a > b:
            continue
        p2 = topo["directed"][(b, a)]
        k = {tb.poly_kind[p1], tb.poly_kind[p2]}
        same = tb.poly_region[p1] == tb.poly_region[p2]
        if k == {"roll"} and same:
            assert (a, b) not in sharp
            seen.add("roll/roll")
        elif k == {"roll", "outer"} and same:
            assert (a, b) not in sharp
            seen.add("roll/outer")
        elif k in ({"roll", "top"}, {"roll", "bottom"}):
            assert (a, b) not in sharp
            seen.add("roll/strip")
        elif "roll" in k and k & {"cap_hinge", "cap_tip"}:
            assert (a, b) in sharp
            seen.add("roll/cap")
        elif k == {"roll"} and "base" not in (tb.poly_region[p1], tb.poly_region[p2]):
            assert (a, b) in sharp                              # a crease running through the band
            seen.add("crease in band")
    assert seen == {"roll/roll", "roll/outer", "roll/strip", "roll/cap", "crease in band"}


def test_roll_with_a_chamfer_and_tip_round():
    s = FX.top_chamfer_arm()
    s["roll_mm"] = 0.4
    s["outline"]["tip_round_mm"] = 1.5
    tb = build_temple(s)
    c = tb.checks
    assert c["closed"] and c["euler"] == 2 and c["planarity_max_mm"] <= 1e-9
    assert max(silhouette(tb)) <= 1e-9
    ps, V = tb.spec, tb.arr.V
    top = np.array([v for v in tb.arr.boundary_loop if abs(V[v, 1] - ps.y_top(V[v, 0])) < 1e-9
                    and ps.d0 < V[v, 0] < ps.d1])
    want = 0.4 + chamfer_depth(ps, V[top, 0], V[top, 1])          # the roll and the chamfer depths add at the edge
    assert np.allclose(tb.X_surface[top] - tb.X[top], want, atol=1e-9) and want.max() > 1.1


def test_roll_limit_and_proximity_refusals():
    with pytest.raises(TempleError) as e:
        build_temple(FX.rolled(3.5))
    assert e.value.code == "E_ROLL" and e.value.fields["reason"] == "height"
    s = FX.wedge()
    s["base"]["top"] = [[0.0, 1.3], [136.5, 1.3]]
    s["roll_mm"] = 0.5
    with pytest.raises(TempleError) as e:
        build_temple(s)
    f = e.value.fields
    assert e.value.code == "E_ROLL" and f["reason"] == "thickness"
    assert f["limit_mm"] == pytest.approx(0.3, abs=1e-6) and f["min_thickness_mm"] == pytest.approx(1.3, abs=1e-6)
    s["roll_mm"] = 0.29
    assert build_temple(s).checks["closed"]
    s = FX.rolled(0.6)
    s["nodes"]["B"] = [46.8, float(np.interp(46.8, np.array(FX.TOP)[:, 0], np.array(FX.TOP)[:, 1])) - 0.4, "base"]
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_ROLL" and e.value.fields["reason"] == "proximity" and "B" in e.value.fields["nodes"]
    # facts and every way out, the roll itself one of them (v3p3-review-e2e P3-2: "move them onto the outline (within
    # snap_mm) or at least roll_mm away"; the brief invites roll_mm)
    assert e.value.message == ("roll_mm 0.6: 1 facet corner(s) or edge crossing(s) lie off the top or bottom outline "
                               "but within roll_mm of it (closest 0.40 mm at d 46.80, y 13.98; nodes B 0.40 mm): the "
                               "roll band holds no facet corner; ways: each onto the outline (within snap_mm 0.05) or "
                               "at least roll_mm from it, roll_mm at most 0.40, or a layout without them")
    s["roll_mm"] = 0.4                                                       # the way it names: B 0.40 mm from it
    assert build_temple(s).checks["closed"]


def _preferred() -> dict:
    if not PREFERRED.exists():
        pytest.skip("owner-preferred spec not present (local evidence)")
    raw = PREFERRED.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PREFERRED_SHA
    return json.loads(raw)


def test_preferred_without_snapping_names_node_a():
    s = v1_to_v2(_preferred())                                   # snap_mm 0: A stays 0.024 mm below the corner
    s["roll_mm"] = 0.3
    with pytest.raises(TempleError) as e:
        build_temple(s)
    assert e.value.code == "E_ROLL" and list(e.value.fields["nodes"]) == ["A"]


@pytest.mark.parametrize("r", [0.3, 0.6, 1.0])
def test_core_roll_on_preferred_v2(r):
    s = v1_to_v2(_preferred(), snap_mm=None)                     # preferred-v2: the /2 default snapping
    t0 = build_temple(s)
    s["roll_mm"] = r
    t = time.process_time()
    tb = build_temple(s)
    cpu = time.process_time() - t
    check_rolled(tb, r)
    assert tb.tables["roll"]["limit_mm"] >= 1.0
    ps = tb.spec
    widths = []
    for d in np.arange(2.5, ps.d1, 5.0):                         # measured in station sections against roll 0
        yt, yb = float(ps.y_top(d)), float(ps.y_bot(d))
        ys = np.linspace(yb, yt, 8001)
        x0, _ = MS.section_profile(MS.slice_d(t0.V, t0.tris, float(d)), ys)
        x1, _ = MS.section_profile(MS.slice_d(tb.V, tb.tris, float(d)), ys)
        dx = x0 - x1
        mid = 0.5 * (yt + yb)
        for sel, sv in ((ys > mid, yt - ys), (ys <= mid, ys - yb)):
            m = sel & (dx > WIDTH_THRESHOLD_MM)
            widths.append(float(sv[m].max()))
        band = ((yt - ys) < r) | ((ys - yb) < r)
        assert np.nanmax(np.abs(dx[~band])) < WIDTH_THRESHOLD_MM / 3
    assert min(widths) >= 0.85 * r and max(widths) <= 1.15 * r, (min(widths), max(widths))
    assert cpu < 2.0                                              # information is in the report; a loose sanity bound
