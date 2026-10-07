"""WP6 g3d.faceted_temple core build (§3 steps 2-6, §8 P0 core half): closed exact solids, planar facets, flat inner
face, the outline reproduced, sharp sets, mirror and opening, bit-identical rebuilds, refusals and the budget."""
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
    assert c["planarity_max_mm"] <= 1e-9                       # §8 P0: core planarity <= 1e-9 mm
    assert c["self_intersection"].startswith("none")
    for f in tb.tables["facets"]:
        assert f["planarity_mm"] <= 1e-9
    assert tb.tables["thickness"]["min_mm"] >= tb.spec.options["t_min_mm"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_inner_face_is_flat_in_the_zone_and_vertical(builds, name):
    tb = builds[name]
    n = tb.n2
    zone = tb.V[n:][:, 0] <= tb.spec.zone_end
    P = tb.V[n:][zone]
    x_line = tb.spec.inner(P[:, 0])
    assert np.max(np.abs(P[:, 2] - x_line)) <= 1e-9
    # one plane: X linear in d, independent of y
    k = np.polyfit(P[:, 0], P[:, 2], 1)
    assert np.max(np.abs(np.polyval(k, P[:, 0]) - P[:, 2])) <= 1e-9


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_outline_is_reproduced(builds, name):
    tb = builds[name]
    ps = tb.spec
    loop = tb.arr.boundary_loop
    B = tb.arr.V[loop]
    on_top = np.isclose(B[:, 1], ps.y_top(B[:, 0]), atol=1e-9)
    on_bot = np.isclose(B[:, 1], ps.y_bot(B[:, 0]), atol=1e-9)
    on_cap = np.isin(B[:, 0], [ps.d0, ps.d1])
    assert np.all(on_top | on_bot | on_cap)
    for chain in (ps.top, ps.bottom):                          # every knot is a vertex
        for p in chain:
            assert np.min(np.hypot(B[:, 0] - p[0], B[:, 1] - p[1])) <= 1e-9
    # side silhouette of the solid = the outline (all vertices inside it, its knots on the hull)
    d, y = tb.V[:, 0], tb.V[:, 1]
    assert np.all(y <= ps.y_top(d) + 1e-9) and np.all(y >= ps.y_bot(d) - 1e-9)


def _analytic_prism_dihedrals():
    n = {"N": (0.0, 0.2, 1.0), "E": (0.1, 0.0, 1.0), "S": (0.0, -0.2, 1.0), "W": (-0.1, 0.0, 1.0),
         "base": (0.0, 0.0, 1.0)}
    ang = lambda a, b: math.degrees(math.acos(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))))
    return {tuple(sorted(p)): ang(n[p[0]], n[p[1]]) for p in
            (("E", "N"), ("E", "S"), ("N", "W"), ("S", "W"), ("E", "base"))}


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
    tb = builds["prada_default"]
    sharp = set(tb.sharp)
    topo = mesh_topology(tb.polys)
    for (a, b), p1 in topo["directed"].items():
        if a > b:
            continue
        p2 = topo["directed"][(b, a)]
        k1, k2 = tb.poly_kind[p1], tb.poly_kind[p2]
        if k1 == k2 == "outer":
            r1, r2 = tb.poly_region[p1], tb.poly_region[p2]
            assert ((a, b) in sharp) == (r1 != r2), (r1, r2)    # creases and facet/base edges sharp, base/base smooth
        elif "outer" in (k1, k2) or "inner" in (k1, k2):
            if not (k1 == k2 == "inner"):
                assert (a, b) in sharp                            # face-to-strip and caps
        if k1 == k2 == "inner":
            assert (a, b) not in sharp
    # chamfer lines (>= 6 deg) and outline kinks (> 25 deg) are sharp
    ch = builds["top_chamfer_arm"]
    assert any(c["kind"] == "chamfer" and c["dihedral_deg"][1] >= 6 for c in ch.tables["creases"])
    kinks = 0
    topo = mesh_topology(ch.polys)
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        if a < b and ch.poly_kind[p1] in ("top", "bottom", "cap_hinge", "cap_tip") and \
                ch.poly_kind[p2] in ("top", "bottom", "cap_hinge", "cap_tip"):
            dih = math.degrees(math.acos(np.clip(ch.normals[p1] @ ch.normals[p2], -1, 1)))
            assert ((a, b) in set(ch.sharp)) == (dih > 25.0)
            kinks += dih > 25.0
    assert kinks >= 4                                            # four cap corners at least


def test_facet_corners_take_the_face_normal(builds):
    tb = builds["prada_default"]
    labels = {tb.poly_region[i] for i in tb.flat_polys}
    assert labels == {"F1", "F2", "F3", "F4"}


def test_mirror_is_exact_and_opening_is_rigid(builds):
    tb = builds["pyramid"]
    Wp, Pp, Tp = tb.world(+1)
    Wm, Pm, Tm = tb.world(-1)
    assert np.array_equal(Wm[:, 0], -Wp[:, 0]) and np.array_equal(Wm[:, 1:], Wp[:, 1:])
    assert all(a == b[::-1] for a, b in zip(Pp, Pm))
    assert np.array_equal(Tm, Tp[:, ::-1])
    # signed volume stays positive on both sides
    for W, T in ((Wp, Tp), (Wm, Tm)):
        a, b, c = W[T[:, 0]], W[T[:, 1]], W[T[:, 2]]
        assert np.einsum("ij,ij->i", a, np.cross(b, c)).sum() > 0
    # opening: rigid, the hinge axis fixed, the tip moves outward
    W5, _, _ = tb.world(+1, opening_deg=5.0)
    D0 = np.linalg.norm(Wp[:50, None] - Wp[None, :50], axis=-1)
    D5 = np.linalg.norm(W5[:50, None] - W5[None, :50], axis=-1)
    assert np.allclose(D0, D5, atol=1e-9)
    tip = int(np.argmax(tb.V[:, 0]))
    assert W5[tip, 0] > Wp[tip, 0] + 5.0
    ax = np.array([[0.0, 0.0, 64.6]])          # local (d, y, X) of a point on the axis: z = -10.1 -> d = -0.5
    ax[0, 0] = -10.6 - (-10.1)
    assert np.allclose(to_world(ax, -10.6, (64.6, -10.1), 7.0), to_world(ax, -10.6, (64.6, -10.1), 0.0), atol=1e-12)
    back = to_local(W5, -10.6, (64.6, -10.1), 5.0)
    assert np.allclose(back, tb.V, atol=1e-9)


def _digest(tb) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(tb.V).tobytes())
    h.update(json.dumps(tb.polys).encode())
    h.update(np.ascontiguousarray(tb.tris).tobytes())
    h.update(json.dumps(tb.sharp).encode())
    return h.hexdigest()


def test_bit_identical_rebuilds_across_processes(builds):
    here = {n: _digest(builds[n]) for n in ("prada_default", "top_chamfer_arm", "valley")}
    again = {n: _digest(build_temple(FX.FIXTURES[n]())) for n in here}
    assert here == again
    code = ("import json,sys;from glasses3d.tests.test_temple_build import _digest;"
            "from glasses3d.g3d.temple import build_temple, fixtures as FX;"
            "print(json.dumps({n: _digest(build_temple(FX.FIXTURES[n]())) for n in sys.argv[1:]}))")
    for seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(REPO))
        out = subprocess.run([sys.executable, "-B", "-c", code, *here], capture_output=True, text=True, env=env,
                             cwd=str(REPO), timeout=120)
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
    assert len(f["defining_points"]) >= 2 and all("r" in p for p in f["defining_points"])


def test_refusal_codes_cover_the_design_list():
    core = set(FX.refusal_cases())
    blender_side = {"E_TARGET", "E_ROLL"}
    internal = {"E_TRIANGULATE", "E_NOT_CLOSED"}              # arrangement / self-check guards (tested separately)
    assert core | blender_side | internal == set(REFUSALS)


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


def test_receipt_contents(builds):
    tb = builds["prada_default"]
    r = report.receipt(tb, core_ms=1.0)
    json.dumps(r)
    assert {f["label"] for f in r["facets"]} == {"F1", "F2", "F3", "F4"}
    for f in r["facets"]:
        assert {"area_mm2", "normal_local", "lean_deg", "defining_points", "conditioning_deg_per_mm"} <= set(f)
    assert {"vbulge_mm", "pbulge_mm", "single_plane_share", "flags"} <= set(r["geometry"])
    assert r["stations"] and abs(r["stations"][1]["d"] - r["stations"][0]["d"] - 5.0) < 1e-9
    assert r["spec_sha256"] == tb.spec.sha256 and r["thickness"]["min_mm"] >= 1.0


def test_core_budget_per_temple():
    spec = FX.prada_default()
    times = []
    for _ in range(7):
        t = time.process_time()                                # CPU time: other processes on the machine do not count
        build_temple(spec)
        times.append(time.process_time() - t)
    assert min(times) <= 0.050, times                          # §3 budget: core <= 50 ms per temple


def test_arm_station_spacing_and_tip_round(builds):
    s = FX.slab()
    s["outline"]["tip_round_mm"] = 1.2
    tb = build_temple(s)
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    from glasses3d.g3d.temple.build import station_list
    st = station_list(tb.spec)
    # core-1.1: stations over the WHOLE temple (zone too), none wider than station_step_mm; the zone end is one
    assert tb.spec.zone_end in st and max(np.diff([tb.spec.d0] + st + [tb.spec.d1])) <= 2.0 + 1e-9


def test_zone_stations_follow_a_per_vertex_bend():
    """core-1.1 (the arview decal defect): every outer face of the facet zone is at most station_step_mm wide in d, so a
    downstream per-vertex deformation by a smooth function of d (arview's arm spread, quadratic about a pivot inside the
    zone) bends the facets with their 0.05 mm decals; planarity, tiling and the dihedrals are unchanged."""
    from glasses3d.g3d.temple.build import station_list
    tb = build_temple(FX.prada_default())
    ps = tb.spec
    st = station_list(ps)
    assert max(np.diff([ps.d0] + st + [ps.d1])) <= ps.options["station_step_mm"] + 1e-9
    for p, k in zip(tb.polys, tb.poly_kind):
        if k == "outer":
            d = tb.V[p, 0]
            assert d.max() - d.min() <= ps.options["station_step_mm"] + 1e-9
    assert tb.checks["planarity_max_mm"] <= 1e-9
    # a quadratic bend x += k (d - d_p)^2 / 2 over the zone: the chord error of a 2 mm strip is k * 2^2 / 8
    kq = 9.0e-3                                   # 1/mm: arview's 18 mm spread with a 20 mm hinge rounding
    assert kq * 2.0 ** 2 / 8 < 0.005
    # an edge roll keeps the arm stations only (the Bevel overlapped itself at a zone station near the outline)
    rolled = build_temple(FX.rolled()).spec
    assert all(d >= rolled.zone_end - 1e-9 for d in station_list(rolled))
