"""Source-footprint preservation and explicit geometric approximation limits."""
import json
import unittest

import numpy as np
import shapely

from reconstruction.optical_surface import OpticalSurfacePolicy, prepare_front_surfaces, _Envelope, _Budget, _positional_witnesses


POLICY = OpticalSurfacePolicy(maximum_output_triangles=20_000)


def grid(x, y, function):
    vertices = np.array([[a, b, function(a, b)] for b in y for a in x], float)
    faces = []
    for j in range(len(y)-1):
        for i in range(len(x)-1):
            a = j*len(x)+i
            faces.extend([[a, a+1, a+len(x)], [a+1, a+len(x)+1, a+len(x)]])
    return vertices, np.array(faces, np.int64)


def footprint(surfaces):
    return shapely.union_all([shapely.Polygon(triangle[:, :2]) for s in surfaces
                             for triangle in s["positions"][s["indices"]]])


class OpticalSurfaceTests(unittest.TestCase):
    def prepare(self, vertices, faces, policy=POLICY):
        return prepare_front_surfaces(vertices, faces, policy=policy)

    def assert_profile(self, result):
        self.assertEqual(result["report"]["status"], "prepared_candidate", result["report"])
        json.dumps(result["report"], allow_nan=False)
        for surface in result["surfaces"]:
            p, f, n, uv = [surface[k] for k in ("positions", "indices", "normals", "uv")]
            self.assertEqual(p.dtype, np.float32); self.assertEqual(n.dtype, np.float32)
            self.assertTrue(np.isfinite(p).all()); self.assertTrue(np.isfinite(n).all())
            self.assertTrue((n[:, 2] > 0).all())
            np.testing.assert_allclose(np.linalg.norm(n, axis=1), 1, atol=2e-7)
            t = p[f].astype(float)
            area_floor = np.sum(np.ptp(p.astype(float), axis=0)**2)*1e-14
            self.assertTrue((np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0])[:, 2] > area_floor).all())
            self.assertEqual(float(uv[:, 1].min()), 0.); self.assertEqual(float(uv[:, 1].max()), 1.)
            self.assertGreater(np.corrcoef(uv[:, 1], p[:, 1])[0, 1], .99)

    def test_reversed_input_winding_is_not_mistaken_for_missing_front(self):
        v, f = grid([0., 1.], [0., 1.], lambda x, y: .4*x + .2*y)
        f = f[:, [0, 2, 1]]
        before_v, before_f = v.copy(), f.copy()
        result = self.prepare(v, f)
        self.assert_profile(result)
        self.assertEqual(result["report"]["source_winding_counts"]["positive_z"], 0)
        self.assertLess(result["report"]["attempts"][-1]["sampled_depth_error_relative"]["maximum"], 1e-7)
        np.testing.assert_array_equal(v, before_v); np.testing.assert_array_equal(f, before_f)

    def test_closed_box_frontmost_depth_excludes_back_and_side_interfaces(self):
        v = np.array([[x, y, z] for z in [0., 1.] for y in [0., 1.] for x in [0., 1.]])
        f = np.array([[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7], [0, 4, 1], [1, 4, 5],
                      [2, 3, 6], [3, 7, 6], [0, 2, 4], [2, 6, 4], [1, 5, 3], [3, 5, 7]])
        result = self.prepare(v, f)
        self.assert_profile(result)
        self.assertEqual(result["report"]["zero_projected_area_source_triangles"], 8)
        for surface in result["surfaces"]:
            np.testing.assert_array_equal(surface["positions"][:, 2], 1.)
        self.assertAlmostEqual(footprint(result["surfaces"]).area, 1.)

    def test_hole_and_concave_contour_are_preserved_without_hull_or_fill(self):
        # A square annulus, plus a notch removed from the outer top-right corner.
        outer = shapely.Polygon([(0, 0), (3, 0), (3, 2), (2, 2), (2, 3), (0, 3)],
                                 holes=[[(1, 1), (1.5, 1), (1.5, 1.5), (1, 1.5)]])
        triangles = list(shapely.constrained_delaunay_triangles(outer).geoms)
        xy = np.array([np.asarray(t.exterior.coords)[:3] for t in triangles])
        v = np.column_stack((xy.reshape(-1, 2), np.zeros(len(xy)*3)))
        f = np.arange(len(v)).reshape(-1, 3)
        result = self.prepare(v, f)
        self.assert_profile(result)
        projected = footprint(result["surfaces"])
        self.assertLess(projected.symmetric_difference(outer).area, 1e-7)
        self.assertEqual(len(projected.interiors), 1)
        self.assertFalse(projected.covers(shapely.Point(1.25, 1.25)))
        self.assertFalse(projected.covers(shapely.Point(2.5, 2.5)))

    def test_disconnected_patches_all_survive_and_each_gets_full_height_uv(self):
        a, fa = grid([0., 1.], [0., 1.], lambda x, y: 0.)
        b, fb = grid([2., 2.5], [2., 4.], lambda x, y: .7)
        result = self.prepare(np.concatenate([a, b]), np.concatenate([fa, fb + len(a)]))
        self.assert_profile(result)
        self.assertEqual(len(result["surfaces"]), 2)
        self.assertEqual(result["report"]["footprint"]["patches"], 2)
        self.assertAlmostEqual(footprint(result["surfaces"]).area, 2.)

    def test_near_vertical_but_finite_sheet_is_supported_without_slope_cutoff(self):
        v, f = grid([0., 1.], [0., 1.], lambda x, y: 50*x)
        result = self.prepare(v, f)
        self.assert_profile(result)
        self.assertGreater(result["report"]["attempts"][-1]["sampled_source_front_incidence_degrees"]["maximum"], 88.)

    def test_curved_deep_wrap_preserves_triangle_planes_and_records_normal_approximation(self):
        v, f = grid(np.linspace(-1, 1, 17), np.linspace(-.5, .5, 9), lambda x, y: 10*x*x + y*y)
        policy = OpticalSurfacePolicy(maximum_output_triangles=30_000)
        result = self.prepare(v, f, policy)
        self.assert_profile(result)
        self.assertEqual(len(result["report"]["attempts"]), 1)
        self.assertLess(result["report"]["attempts"][-1]["sampled_depth_error_relative"]["maximum"], 1e-6)
        self.assertIsNotNone(result["report"]["attempts"][-1]["sampled_regenerated_normal_deviation_degrees"])
        self.assertIn("no global", result["report"]["attempts"][-1]["sampling_scope"])

    def test_depth_discontinuity_remains_an_explicit_crack_without_a_ramp(self):
        base, fb = grid([0., 1.], [0., 1.], lambda x, y: 0.)
        top, ft = grid([0., .43], [0., 1.], lambda x, y: 1.)
        result = self.prepare(np.concatenate([base, top]), np.concatenate([fb, ft + len(base)]))
        self.assert_profile(result)
        self.assertGreater(result["report"]["attempts"][-1]["discontinuity_edges"], 0)
        self.assertEqual(result["report"]["attempts"][-1]["discontinuity_jump_source_units"]["maximum"], 1.)
        for s in result["surfaces"]:
            self.assertTrue(np.isin(s["positions"][:, 2], [0., 1.]).all())
        self.assertAlmostEqual(footprint(result["surfaces"]).area, 1.)

    def test_capacity_refusal_never_returns_truncated_footprint(self):
        v, f = grid([0., 1.], [0., 1.], lambda x, y: 0.)
        result = self.prepare(v, f, OpticalSurfacePolicy(maximum_output_triangles=1))
        self.assertEqual(result["surfaces"], [])
        self.assertTrue(any("capacity_exceeded" in r for r in result["report"]["reasons"]))
        result = self.prepare(v, f, OpticalSurfacePolicy(maximum_source_triangles=1))
        self.assertEqual(result["surfaces"], [])
        self.assertIn("source_triangle_capacity_exceeded", result["report"]["reasons"])

    def test_unreferenced_vertices_do_not_change_resolution_or_error_budget(self):
        v, f = grid([0., 1.], [0., 1.], lambda x, y: .2*x)
        plain = self.prepare(v, f)
        other = self.prepare(np.vstack([v, [10000., 10000., 10000.]]), f)
        self.assert_profile(other)
        self.assertEqual(plain["report"]["attempts"], other["report"]["attempts"])

    def test_coplanar_different_tessellations_have_one_interface(self):
        a, fa = grid([0., 1.], [0., 1.], lambda x, y: .3*x+.7*y)
        b = np.vstack([a, [.5, .5, .5]])
        fb = np.array([[0, 1, 4], [1, 3, 4], [3, 2, 4], [2, 0, 4]])
        result = self.prepare(b, np.vstack([fa, fb]))
        self.assert_profile(result)
        area = sum(shapely.Polygon(t[:, :2]).area for s in result["surfaces"] for t in s["positions"][s["indices"]])
        self.assertAlmostEqual(area, 1.)
        self.assertEqual(result["report"]["attempts"][-1]["projected_triangle_overlap_relative_area"], 0.)

    def test_crossing_planes_are_clipped_at_their_intersection(self):
        a, fa = grid([0., 1.], [0., 1.], lambda x, y: x)
        b, fb = grid([0., 1.], [0., 1.], lambda x, y: 1-x)
        result = self.prepare(np.vstack([a, b]), np.vstack([fa, fb+len(a)]))
        self.assert_profile(result)
        for s in result["surfaces"]:
            t = s["positions"][s["indices"]].astype(float)
            probes = np.einsum('pi,tij->tpj', np.array([[1/3]*3, [.6, .2, .2]]), t)
            np.testing.assert_allclose(probes[:, :, 2], np.maximum(probes[:, :, 0], 1-probes[:, :, 0]), atol=1e-7)

    def test_lattice_is_derived_from_unchanged_runtime_area_floor(self):
        v, f = grid([.006705, .066145], [-.034407, .021873], lambda x, y: -.01)
        result = self.prepare(v, f)
        self.assert_profile(result)
        receipt = result["report"]["precision"]
        self.assertGreater(receipt["xy_lattice_source_units"]**2, receipt["runtime_area_floor_source_units_squared"])
        self.assertGreaterEqual(receipt["xy_lattice_source_units"], receipt["maximum_source_xy_float32_ulp"])
        self.assertTrue(result["report"]["attempts"][-1]["footprint_within_declared_xy_rounding_band"])

    def test_repeatability_and_policy_validation(self):
        v, f = grid([0., .3, 1.], [0., 1.], lambda x, y: x*x)
        first, second = self.prepare(v, f), self.prepare(v, f)
        self.assert_profile(first); self.assert_profile(second)
        for a, b in zip(first["surfaces"], second["surfaces"]):
            for name in ('positions', 'indices', 'normals', 'uv'):
                np.testing.assert_array_equal(a[name], b[name])
        for values in ({'maximum_output_triangles': True}, {'maximum_query_pairs': 0},
                       {'maximum_seconds': float('nan')}, {'sampled_position_tolerance_relative': 1.}):
            with self.assertRaises(ValueError):
                OpticalSurfacePolicy(**values)

    def test_distant_subresolution_component_cannot_silently_disappear(self):
        v, f = grid([0., 1.], [0., 1.], lambda x, y: 0.)
        tiny = np.array([[2., 0., 0.], [2.+1e-8, 0., 0.], [2., 1e-8, 0.]])
        result = self.prepare(np.vstack([v, tiny]), np.vstack([f, [4, 5, 6]]))
        self.assertEqual(result["report"]["status"], "unsupported")
        self.assertFalse(result["report"]["attempts"][-1]["original_footprint_within_declared_xy_rounding_band"])

    def test_donor_clamp_prevents_steep_extrapolation_and_output_floor_is_checked(self):
        step = 2.**-23
        height = step/1e-7*(1-1e-6)
        vertices = np.array([[0., 0., 0.], [.7*step, 0., height], [0., 1.9*step, 0.]])
        result = self.prepare(vertices, np.array([[0, 1, 2]]))
        self.assert_profile(result)
        self.assertTrue(result['report']['runtime_area_checks'][0]['passes_area_floor'])
        self.assertLessEqual(result['surfaces'][0]['positions'][:, 2].max(), np.float32(height))
        self.assertGreater(result['report']['donor_xy_clamps'][0]['clamped_vertices'], 0)

    def test_arithmetic_closure_cannot_fabricate_a_surface_distance_witness(self):
        square, faces = grid([-.5, .5], [-.5, .5], lambda x, y: 0.)
        steep = np.array([[-1e-14, -.25, 0.], [0., -.25, .5], [0., .25, .5]])
        triangles = np.concatenate([square[faces], steep[None]])
        envelope = _Envelope(triangles, shapely.polygons(triangles[:, :, :2]), shapely, _Budget(POLICY), 2.**-23)
        point = np.array([[1e-14, 0., 1.]])
        z, _, _, covered = envelope.query(point[:, :2], return_coverage=True)
        self.assertEqual(z[0], 1.)
        self.assertFalse(covered[0])
        strict_z, _, _ = envelope.query(point[:, :2], arithmetic_closure=False)
        self.assertEqual(strict_z[0], 0.)
        distance = _positional_witnesses(point, envelope, 2*np.sqrt(2)*2.**-23)
        self.assertGreaterEqual(distance[0], .5)

    def test_invalid_inputs_and_unrepresentable_vertical_wall(self):
        v, f = grid([0., 1.], [0., 1.], lambda x, y: 0.)
        for vertices, faces in [(v.astype(int), f), (v, f.astype(float)), (v, np.array([[0, 1, 99]])),
                                (np.full_like(v, np.nan), f), (v, np.empty((0, 3), int))]:
            with self.assertRaises(ValueError):
                self.prepare(vertices, faces)
        v[:, 2] = v[:, 0]; v[:, 0] = 0
        result = self.prepare(v, f)
        self.assertEqual(result["report"]["status"], "unsupported")
        self.assertEqual(result["report"]["reasons"], ["no_positive_area_xy_footprint"])


if __name__ == "__main__":
    unittest.main()
