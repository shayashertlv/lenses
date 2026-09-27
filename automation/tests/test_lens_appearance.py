"""Representation/energy tests; these do not establish renderer parity."""

import copy
import json
import unittest

import numpy as np

from reconstruction.lens_appearance import (
    COLOR_SPACE, DensityKeyframe, LensAppearance, ReflectanceKeyframe,
    linear_to_srgb, optical_density_from_linear_transmission, srgb_to_linear,
)


def uniform(transmission=(0.2, 0.4, 0.8), reflection=(0.04, 0.04, 0.04), **kwargs):
    density = optical_density_from_linear_transmission(transmission)
    return LensAppearance((DensityKeyframe(0, density),), reflection, **kwargs)


class LensAppearanceTests(unittest.TestCase):
    def test_uniform_absorption_retains_absolute_tint_without_normalization(self):
        lens = uniform()
        sample = lens.evaluate(np.linspace(0, 1, 13))
        np.testing.assert_allclose(sample.transmission_rgb, np.tile(np.array([0.2, 0.4, 0.8]) * 0.96, (13, 1)))
        np.testing.assert_allclose(sample.reflectance_rgb, 0.04)
        np.testing.assert_allclose(sample.optical_density_rgb[0], -np.log([0.2, 0.4, 0.8]))

    def test_density_midpoint_is_geometric_transmission_midpoint(self):
        lens = LensAppearance((
            DensityKeyframe(0, optical_density_from_linear_transmission((0.1, 0.2, 0.4))),
            DensityKeyframe(1, optical_density_from_linear_transmission((0.9, 0.8, 1.0))),
        ), normal_reflectance_rgb=(0, 0, 0))
        sample = lens.evaluate(0.5)
        expected = np.sqrt(np.array([0.1, 0.2, 0.4]) * [0.9, 0.8, 1.0])
        np.testing.assert_allclose(sample.transmission_rgb, expected)
        self.assertFalse(np.allclose(sample.transmission_rgb, [0.5, 0.5, 0.7]))
        np.testing.assert_allclose(sample.optical_density_rgb, -np.log(expected))

    def test_piecewise_gradient_hits_knots_without_overshoot(self):
        lens = LensAppearance((DensityKeyframe(0, (0, 1, 2)), DensityKeyframe(0.3, (3, 4, 5)),
                               DensityKeyframe(1, (1, 2, 3))))
        np.testing.assert_allclose(lens.evaluate(0.3).optical_density_rgb, (3, 4, 5))
        values = lens.evaluate(np.linspace(0, 1, 1001)).optical_density_rgb
        self.assertTrue(np.all(values >= (0, 1, 2)))
        self.assertTrue(np.all(values <= (3, 4, 5)))
        epsilon = 1e-6
        left = lens.evaluate(0.3 - epsilon).optical_density_rgb
        right = lens.evaluate(0.3 + epsilon).optical_density_rgb
        self.assertLess(float(np.max(np.abs(left - right))) / epsilon, 0.001)

    def test_high_mirror_reflectance_is_not_capped(self):
        lens = uniform((1, 1, 1), (0.95, 0.85, 0.99))
        sample = lens.evaluate(0.2)
        np.testing.assert_allclose(sample.reflectance_rgb, (0.95, 0.85, 0.99))
        np.testing.assert_allclose(sample.transmission_rgb, (0.05, 0.15, 0.01))
        np.testing.assert_allclose(sample.absorption_rgb, 0)
        mirror = uniform((0.2, 0.3, 0.4), (1, 1, 1)).evaluate(0.5)
        np.testing.assert_array_equal(mirror.transmission_rgb, np.zeros(3))

    def test_neutral_environment_composition_obeys_energy(self):
        sample = uniform().evaluate(0.5)
        gray = np.array([0.18, 0.18, 0.18])
        np.testing.assert_allclose(sample.compose(gray, gray), (sample.transmission_rgb + sample.reflectance_rgb) * gray)
        clear = uniform((1, 1, 1), (0.92, 0.8, 0.5)).evaluate(0.2, 63)
        np.testing.assert_allclose(clear.compose(gray, gray), gray)

    def test_dark_background_does_not_erase_colored_mirror(self):
        sample = uniform((0.1, 0.3, 0.7), (0.9, 0.4, 0.05)).evaluate(0.5)
        np.testing.assert_allclose(sample.compose((0, 0, 0), (1, 1, 1)), (0.9, 0.4, 0.05))
        np.testing.assert_array_equal(sample.compose((0, 0, 0), (0, 0, 0)), np.zeros(3))
        # Linear radiance can be HDR; the contract must not secretly tone-map it.
        np.testing.assert_allclose(sample.compose((0, 0, 0), (4, 4, 4)), (3.6, 1.6, 0.2))

    def test_lens_local_gradient_survives_head_roll(self):
        lens = LensAppearance((DensityKeyframe(0, (0.1, 0.2, 0.3)), DensityKeyframe(1, (1, 2, 3))))
        local_v = np.array([0, 0.25, 0.5, 1])
        local_points = np.column_stack((np.zeros(4), local_v - 0.5, np.zeros(4)))
        quarter_turn = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        rolled_points = local_points @ quarter_turn.T
        np.testing.assert_allclose(rolled_points[:, 1], 0)
        # v is a surface attribute preserved with each point, not recomputed
        # from the now-horizontal lens's projected/world-space vertical extent.
        before = lens.evaluate(local_v, 20)
        after = lens.evaluate(local_v, 20)
        np.testing.assert_array_equal(before.transmission_rgb, after.transmission_rgb)
        self.assertFalse(np.allclose(after.transmission_rgb, lens.evaluate(rolled_points[:, 1] + 0.5, 20).transmission_rgb))

    def test_refraction_sets_attenuation_path(self):
        lens = uniform((0.5, 0.5, 0.5), (0, 0, 0), refractive_index=1.5)
        sample = lens.evaluate(0.5, 60)
        cos_inside = np.sqrt(1 - (np.sin(np.deg2rad(60)) / 1.5) ** 2)
        reflection = (1 - np.cos(np.deg2rad(60))) ** 5
        np.testing.assert_allclose(sample.transmission_rgb, (1 - reflection) * np.exp(np.log(0.5) / cos_inside))

    def test_optional_colored_angular_table_is_independent_of_tint(self):
        table = (ReflectanceKeyframe(0, (0.1, 0.7, 0.2)), ReflectanceKeyframe(45, (0.7, 0.1, 0.3)),
                 ReflectanceKeyframe(90, (1, 1, 1)))
        lens = uniform((0.4, 0.5, 0.6), table[0].reflectance_rgb, angular_reflectance_keyframes=table)
        normal, angle = lens.evaluate(0.2), lens.evaluate(0.2, 45)
        np.testing.assert_allclose(angle.reflectance_rgb, (0.7, 0.1, 0.3))
        np.testing.assert_array_equal(normal.optical_density_rgb, angle.optical_density_rgb)
        np.testing.assert_allclose(lens.evaluate(0.2, 22.5).reflectance_rgb, (0.4, 0.4, 0.25))

    def test_energy_bounds_over_gradients_angles_and_dark_backgrounds(self):
        lens = LensAppearance((DensityKeyframe(0, (0, 0.01, 12)), DensityKeyframe(0.4, (7, 0, 2)),
                               DensityKeyframe(1, (3, 8, 0))), (0.99, 0.001, 0.75))
        sample = lens.evaluate(np.linspace(0, 1, 101)[:, None], np.linspace(0, 90, 181)[None, :])
        for values in (sample.reflectance_rgb, sample.transmission_rgb, sample.absorption_rgb):
            self.assertTrue(np.isfinite(values).all())
            self.assertTrue(np.all((values >= 0) & (values <= 1)))
        self.assertTrue(np.all(sample.reflectance_rgb + sample.transmission_rgb <= 1 + 1e-15))
        np.testing.assert_allclose(sample.reflectance_rgb + sample.transmission_rgb + sample.absorption_rgb, 1, atol=1e-15)
        for level in (0, 0.001, 0.05, 1):
            composed = sample.compose((level, level, level), (level, level, level))
            self.assertTrue(np.all(composed >= 0))
            self.assertTrue(np.all(composed <= level + 1e-15))

    def test_exact_grazing_index_one_has_defined_finite_limits(self):
        lens = LensAppearance((DensityKeyframe(0, (0, 1, 2)),), (0.5, 0.5, 0.5), refractive_index=1,
                              angular_reflectance_keyframes=(ReflectanceKeyframe(0, (0.5, 0.5, 0.5)),
                                                            ReflectanceKeyframe(90, (0.5, 0.5, 0.5))))
        sample = lens.evaluate(0, 90)
        np.testing.assert_array_equal(sample.transmission_rgb, (0.5, 0, 0))
        self.assertTrue(np.isfinite(sample.absorption_rgb).all())

    def test_roughness_is_recorded_but_not_falsely_simulated(self):
        low, high = uniform(roughness=0), uniform(roughness=1)
        self.assertEqual(high.to_dict()["roughness"], 1)
        np.testing.assert_array_equal(low.evaluate(0.5).reflectance_rgb, high.evaluate(0.5).reflectance_rgb)

    def test_optional_rear_response_preserves_front_and_reciprocal_transmission(self):
        legacy=LensAppearance((DensityKeyframe(0,(.1,.7,1.4)),),(.7,.6,.3))
        appearance=LensAppearance.from_dict({**legacy.to_dict(),'rear_reflection_fraction_rgb':[.1,.25,.8]})
        v=np.linspace(0,1,301);angle=np.linspace(0,90,301)
        front=appearance.evaluate(v,angle);rear=appearance.evaluate(v,angle,side='rear')
        np.testing.assert_array_equal(front.reflectance_rgb,legacy.evaluate(v,angle).reflectance_rgb)
        np.testing.assert_array_equal(front.transmission_rgb,rear.transmission_rgb)
        np.testing.assert_allclose(rear.reflectance_rgb,(1-rear.transmission_rgb)*[.1,.25,.8],atol=1e-15)
        np.testing.assert_allclose(rear.reflectance_rgb+rear.transmission_rgb+rear.absorption_rgb,1,atol=1e-15)
        self.assertTrue(np.all(rear.absorption_rgb>=0))
        self.assertNotIn('rear_reflection_fraction_rgb',legacy.to_dict())
        self.assertEqual(LensAppearance.from_dict(appearance.to_dict()),appearance)
        for invalid in (None,[1.01,0,0],[-.1,0,0],[True,0,0],[.1,.2]):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                LensAppearance.from_dict({**legacy.to_dict(),'rear_reflection_fraction_rgb':invalid})
        with self.assertRaises(ValueError):appearance.evaluate(.5,side='backface')

    def test_explicit_srgb_conversion_uses_linear_light(self):
        linear = srgb_to_linear((0.5, 0.04045, 1))
        np.testing.assert_allclose(linear, (0.21404114048223255, 0.04045 / 12.92, 1))
        np.testing.assert_allclose(linear_to_srgb(linear), (0.5, 0.04045, 1), atol=3e-8)
        density = optical_density_from_linear_transmission(linear)
        np.testing.assert_allclose(np.exp(-np.array(density)), linear)

    def test_roundtrip_preserves_values_and_color_space_exactly(self):
        reflection = (0.9375123456789012, 0.6789123456789012, 0.2345678901234567)
        lens = LensAppearance((DensityKeyframe(0, (0.01234567890123456, 1.2345678901234567, 5.67890123456789)),
                               DensityKeyframe(1, (0.2, 0.3, 0.4))), reflection, 1.5123456789012346, 0.09876543210987654,
                              (ReflectanceKeyframe(0, reflection), ReflectanceKeyframe(90, (1, 1, 1))))
        payload = json.loads(json.dumps(lens.to_dict(), allow_nan=False))
        restored = LensAppearance.from_dict(payload)
        self.assertEqual(lens, restored)
        self.assertEqual(restored.to_dict(), payload)
        self.assertEqual(payload["color_space"], COLOR_SPACE)
        np.testing.assert_array_equal(lens.evaluate([0.123, 0.768], [7, 57]).transmission_rgb,
                                      restored.evaluate([0.123, 0.768], [7, 57]).transmission_rgb)

    def test_invalid_parameters_reject_without_clamping_or_sorting(self):
        invalid = (
            lambda: DensityKeyframe(-0.1, (0, 0, 0)), lambda: DensityKeyframe(0, (-0.1, 0, 0)),
            lambda: DensityKeyframe(0, (float("nan"), 0, 0)), lambda: uniform(reflection=(1.01, 0.5, 0.5)),
            lambda: uniform(roughness=-0.1), lambda: uniform(refractive_index=0.9),
            lambda: LensAppearance((DensityKeyframe(0.2, (0, 0, 0)),)),
            lambda: LensAppearance((DensityKeyframe(0, (0, 0, 0)), DensityKeyframe(0.8, (1, 1, 1)))),
            lambda: LensAppearance((DensityKeyframe(0, (0, 0, 0)), DensityKeyframe(0, (1, 1, 1)), DensityKeyframe(1, (2, 2, 2)))),
            lambda: optical_density_from_linear_transmission((0, 0.5, 1)),
            lambda: srgb_to_linear((1.1, 0.5, 0.5)), lambda: uniform().evaluate(-0.01),
            lambda: uniform().evaluate(0.5, 91), lambda: uniform().evaluate(True),
            lambda: uniform().evaluate(float("inf")), lambda: uniform().evaluate(0).compose((-1, 0, 0), (1, 1, 1)),
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()

    def test_angular_table_rejects_missing_coverage_and_conflicting_normal(self):
        for table in ((ReflectanceKeyframe(0, (0.04, 0.04, 0.04)),),
                      (ReflectanceKeyframe(0, (0.04, 0.04, 0.04)), ReflectanceKeyframe(60, (1, 1, 1))),
                      (ReflectanceKeyframe(0, (0.4, 0.4, 0.4)), ReflectanceKeyframe(90, (1, 1, 1)))):
            with self.subTest(table=table), self.assertRaises(ValueError):
                uniform(angular_reflectance_keyframes=table)

    def test_deserialization_rejects_unknown_or_incompatible_semantics(self):
        valid = uniform().to_dict()
        for field, value in (("schema_version", True), ("schema_version", 2), ("color_space", "srgb"),
                             ("vertical_coordinate", "screen_y"), ("density_interpolation", "linear_tint"),
                             ("roughness", "0.05"), ("extra", "ignored")):
            payload = copy.deepcopy(valid)
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                LensAppearance.from_dict(payload)
        payload = copy.deepcopy(valid)
        payload["optical_density_keyframes"][0]["unrecognized"] = 1
        with self.assertRaises(ValueError):
            LensAppearance.from_dict(payload)
        del valid["roughness"]
        with self.assertRaises(ValueError):
            LensAppearance.from_dict(valid)


if __name__ == "__main__":
    unittest.main()
