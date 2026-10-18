"""TB-01/02: exercise the delivery's rule, including every eligible seed-11 layout."""
import math
import numpy as np
import pytest

from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import arrange as AR
from glasses3d.g3d.temple import build as BD
from glasses3d.g3d.temple.build import build_temple
from glasses3d.g3d.temple.spec import MIN_FEATURE_MM, MIN_ROUND_MM, TempleError, triangle_degenerate_float32
from glasses3d.g3d_host.spec import DEGENERATE_FLOAT32_ULPS, FLOAT32_EPS


def delivery_bad(tb):
    world, _, triangles = tb.world()
    t = (world / 1000).astype(np.float32).astype(np.float64)[triangles]
    e1, e2, e3 = t[:, 1]-t[:, 0], t[:, 2]-t[:, 0], t[:, 2]-t[:, 1]
    area2 = np.linalg.norm(np.cross(e1, e2), axis=1)
    longest = np.sqrt(np.maximum(np.maximum((e1*e1).sum(1), (e2*e2).sum(1)), (e3*e3).sum(1)))
    bad = area2 <= DEGENERATE_FLOAT32_ULPS * FLOAT32_EPS * np.abs(t).max(axis=(1, 2)) * longest
    np.testing.assert_array_equal(bad, triangle_degenerate_float32(world, triangles))
    return bad


@pytest.mark.parametrize("lean", [.06, .1, .15, .3, 1., 3.])
@pytest.mark.parametrize("roll", [0., .4])
def test_steep_band_closure_and_roll_have_no_delivery_degenerate_triangles(lean, roll):
    s = FX.straight(nodes={"A": [20,10,5], "B": [20,-10,5], "C": [40+lean,-10,5], "D": [40,10,5]},
                    facets={"F": ["B","C","D","A"]})
    s["roll_mm"] = roll
    tb = build_temple(s)
    assert not delivery_bad(tb).any()
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    assert tb.arr.feature_snaps["max_mm"] <= MIN_FEATURE_MM


@pytest.mark.parametrize("tip_start", [118.5, 119., 119.5])
@pytest.mark.parametrize("inner_kink", [False, True])
def test_steep_outline_roll_repairs_only_coplanar_inner_fans(tip_start, inner_kink, monkeypatch):
    s = FX.straight()
    s["outline"]["top"] = [[0, 10], [tip_start, 10], [120, -20]]
    s["outline"]["bottom"] = [[0, -25], [120, -25]]
    s["roll_mm"] = .1
    if inner_kink:
        s["inner"] = [[0, 60], [119, 60], [120, 59]]
    original, captured = BD._repair_inner_ears, {}
    def capture(ps, V, polys, kind, region, pface, pcell, tris, tri_poly):
        captured["V"] = V.copy()
        captured["outer"] = tris[[kind[p] != "inner" for p in tri_poly]].copy()
        world = BD.to_world(V, ps.hinge["z_mm"], ps.hinge["axis_xz_mm"], ps.hinge["opening_deg"])
        captured["bad"] = int(triangle_degenerate_float32(world, tris).sum())
        return original(ps, V, polys, kind, region, pface, pcell, tris, tri_poly)
    monkeypatch.setattr(BD, "_repair_inner_ears", capture)
    if inner_kink and tip_start == 119.:
        # Here the unsafe fan straddles the actual inner-profile bend. Flattening it would change the surface;
        # the delivery guard must refuse this unresolved feature rather than remove the kink to make it pass.
        with pytest.raises(TempleError) as error:
            build_temple(s)
        assert error.value.code == "E_SHORT_EDGE"
        assert "float32-metre resolution" in str(error.value)
        assert captured["bad"] == 2
        return
    tb = build_temple(s)
    assert captured["bad"] > 0
    assert not delivery_bad(tb).any()
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    np.testing.assert_array_equal(tb.V, captured["V"])
    inner = np.array([tb.poly_kind[p] == "inner" for p in tb.tri_poly])
    np.testing.assert_array_equal(tb.tris[~inner], captured["outer"])
    # Every inner triangle still lies on the exact piecewise-linear inner profile, including its real slope knot.
    centers = tb.V[tb.tris[inner]].mean(axis=1)
    np.testing.assert_allclose(centers[:, 2], tb.spec.inner(centers[:, 0]), atol=1e-10, rtol=0)


def fuzz_layout(rng):
    """The review's fixed seed-11 t12 generator, with its full original sampling domain."""
    d = 5 + rng.uniform(0, 40)
    n = int(rng.integers(2, 5))
    xs = d + np.cumsum(np.r_[0, rng.uniform(6, 14, n)])
    nodes, facets = {}, {}
    for i, x in enumerate(xs):
        x2 = x + rng.uniform(-3, 3)
        yt = FX._yt(x) if rng.random() < .6 else FX._yt(x) - rng.uniform(.7, 2.5)
        yb = FX._yb(x2) if rng.random() < .6 else FX._yb(x2) + rng.uniform(.7, 2.5)
        nodes[f"T{i}"] = [float(x), float(yt), float(FX.T_ZONE + rng.uniform(0, 1))]
        nodes[f"B{i}"] = [float(x2), float(yb), float(FX.T_ZONE + rng.uniform(0, 1))]
    for i in range(n):
        rng.random()  # t12 draws a plane choice, then converts all its noncoplanar quads to warp
        f = {"poly": [f"B{i}", f"B{i+1}", f"T{i+1}", f"T{i}"], "plane": "warp"}
        if rng.random() < .25:
            f["soft"] = True
        facets[f"F{i}"] = f
    s = FX.base_spec(nodes=nodes, facets=facets)
    s["outline"]["tip_round_mm"] = float(rng.choice([0., 1., 2.]))
    if rng.random() < .6:
        s["roll_mm"] = float(rng.choice([.2, .4]))
    if rng.random() < .3:
        s["chamfers"] = [{"edge":"top", "d":[80.,120.], "width_mm":.8, "depth_mm":.4}]
    return s


@pytest.mark.parametrize("case", range(100))
def test_review_fuzz_all_eligible_layouts_have_no_delivery_degenerate_triangles(case):
    rng = np.random.default_rng(11)
    for _ in range(case + 1):
        spec = fuzz_layout(rng)
    # These four were refused before this repair. Every one is still executed and its exact refusal class checked.
    refused = {13: "E_ROLL", 34: "E_SHORT_EDGE", 46: "E_SHORT_EDGE", 69: "E_SHORT_EDGE"}
    if case in refused:
        with pytest.raises(TempleError) as err:
            build_temple(spec)
        assert err.value.code == refused[case]
    else:
        tb = build_temple(spec)
        assert not delivery_bad(tb).any()
        assert tb.checks["closed"] and tb.checks["euler"] == 2
        assert tb.thickness.min() >= tb.spec.options["t_min_mm"]


@pytest.mark.parametrize("field,value,code", [
    ("roll_mm", .001, "E_ROLL"), ("roll_mm", .01, "E_ROLL"), ("roll_mm", 1e-6, "E_ROLL"),
    ("tip_round_mm", .01, "E_OUTLINE"), ("tip_round_mm", 1e-6, "E_OUTLINE"),
    ("border_band_mm", 1e-5, "E_SPEC"), ("station_step_mm", 1e-5, "E_SPEC"),
    ("width_mm", 1e-5, "E_CHAMFER"), ("depth_mm", 1e-5, "E_CHAMFER"),
])
def test_sub_feature_sizes_refuse_factually_before_arrangement(field, value, code):
    s = FX.slab()
    if field == "roll_mm":
        s[field] = value
    elif field == "tip_round_mm":
        s["outline"][field] = value
    elif field in ("border_band_mm", "station_step_mm"):
        s["options"][field] = value
    else:
        ch = {"edge":"top", "d":[80.,120.], "width_mm":.8, "depth_mm":.4}
        ch[field] = value
        s["chamfers"] = [ch]
    with pytest.raises(TempleError) as err:
        build_temple(s)
    assert err.value.code == code
    assert "float32-safe minimum feature" in err.value.message


def test_minimum_radius_is_derived_from_the_sampled_arc_and_zero_remains_crisp():
    assert MIN_ROUND_MM * (1-math.cos(math.pi/16)) == pytest.approx(MIN_FEATURE_MM)
    assert not delivery_bad(build_temple(FX.slab())).any()


def test_scale_aware_ears_do_not_consume_a_noise_collinear_boundary_run():
    p = np.array([[0.,0.], [25.,0.], [25.,1.], [0.,1.], [0.,.75], [2e-11,.5], [0.,.25]])
    for scale in (.1, 1., 100.):
        points = p * scale
        tris = AR.ear_clip(points, list(range(len(p))))
        assert AR._covers(list(range(len(p))), tris)
        assert min(AR._tri_height(points,t) for t in tris) > .01*scale


def test_greedy_needles_are_retriangulated_without_moving_any_boundary():
    p = np.array([[0.,0.], [.005,0.], [1.,25.], [0.,25.], *[[0.,y] for y in np.arange(24.875,0.,-.125)]])
    tris = AR.ear_clip(p, list(range(len(p))))
    assert AR._covers(list(range(len(p))), tris)
    assert min(AR._tri_height(p,t) for t in tris) > MIN_FEATURE_MM
    assert sum(AR._cross(p[a],p[b],p[c])/2 for a,b,c in tris) == pytest.approx(AR.poly_area(p))


def test_huge_number_is_a_spec_refusal():
    s = FX.slab()
    s["roll_mm"] = 10**400
    with pytest.raises(TempleError, match="E_SPEC: roll_mm must be finite"):
        build_temple(s)


@pytest.mark.parametrize("origin", [(0., 0.), (40., 1.), (-50., -1.), (1e6, -1e6)])
@pytest.mark.parametrize("axis", [0, 1])
def test_vertex_pool_preserves_translated_minimum_features(origin, axis):
    # The same exact-floor gap can land on either side of the threshold after an intersection calculation.
    # Keep it consistently: merging only some of its vertices can leave a pinched graph, while merging all
    # vertices can join two surfaces that legitimately differ across the gap.
    endpoint = list(origin)
    endpoint[axis] += MIN_FEATURE_MM
    for toward in (-math.inf, None, math.inf):
        point = endpoint.copy()
        if toward is not None:
            point[axis] = math.nextafter(point[axis], toward)
        pool = AR.VertexPool(MIN_FEATURE_MM)
        assert pool.add(origin) == 0
        assert pool.add(point) == 1
        assert pool.add(point) == 1
        np.testing.assert_array_equal(pool.array(), [origin, point])

    pool = AR.VertexPool(MIN_FEATURE_MM)
    point = list(origin)
    point[axis] += .5 * MIN_FEATURE_MM
    assert pool.add(origin) == pool.add(point) == 0
    np.testing.assert_array_equal(pool.array(), [origin])


def test_vertex_pool_identical_coordinates_merge_even_below_coordinate_resolution():
    pool = AR.VertexPool(1e-20)
    assert pool.add((1e6, -1e6)) == pool.add((1e6, -1e6)) == 0
    assert len(pool.pts) == 1
