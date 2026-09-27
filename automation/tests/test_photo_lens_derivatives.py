import itertools
import unittest

import numpy as np

from reconstruction import photo_lens_fit as photo
from reconstruction.photo_lens_derivatives import (
    prediction_jacobian, single_residual_jacobian, joint_residual_jacobian)
import test_photo_lens_fit as single_fixture
import test_joint_photo_lens_fit as joint_fixture


def numerical_jacobian(function, x, step=1e-6):
    result = []
    for index in range(len(x)):
        lower, upper = x.copy(), x.copy()
        lower[index] -= step
        upper[index] += step
        result.append((function(upper)-function(lower))/(2*step))
    return np.stack(result, axis=-1)


def randomized_rows(group=None, photos=('front', 'angled')):
    rows = []
    rng = np.random.default_rng(171+(group or 0))
    for name in photos:
        row = (single_fixture.observation(photo=name, rear=True) if group is None
               else joint_fixture.observation(group, photo=name))
        count = len(row['xy'])
        row['code_rgb'] = rng.integers(20, 230, (count, 3), dtype=np.uint8)
        row['code_rgb'][::5, 0] = 0
        row['code_rgb'][::7, 2] = 255
        row['intrinsic_v'] = np.resize([0., .01, .25, .499, .5, .501, .75, 1.], count)
        row['incidence_degrees'] = np.resize([0., .01, 30., 45., 60., 75., 88., 89.99], count)
        direction = rng.normal(size=(count, 3))
        row['reflected_direction'] = direction/np.linalg.norm(direction, axis=1)[:, None]
        row['background_rgb'] = rng.uniform(.01, .9, (count, 3))
        row['rear_rgb'] = rng.uniform(.01, .9, (count, 3))
        row['rear_weight'] = rng.uniform(0, 1, count)
        rows.append(row)
    return rows


def interior_parameters(model, seed=612):
    rng = np.random.default_rng(seed)
    lower, upper = np.asarray(model.lower), np.asarray(model.upper)
    return lower+rng.uniform(.25, .65, len(lower))*(upper-lower)


class PhotoLensDerivativeTests(unittest.TestCase):
    def test_all_single_families_lighting_rear_modes_match_independent_central_differences(self):
        p = photo.PhotoLensFitPolicy(rear_content='explained', minimum_validation_points_per_photo=1)
        _, _, branches, _, _ = photo._prepare(randomized_rows(), single_fixture.BINDING, p)
        for family, lighting, rear in itertools.product(photo.FAMILIES, p.lighting_families,
                ('geometry_conditioned_rear', 'unknown_rear_color', 'rear_geometry_unknown_backdrop_only')):
            with self.subTest(family=family, lighting=lighting, rear=rear):
                model = photo._Model(branches[0], family, lighting, rear, p)
                x = interior_parameters(model)
                predicted = single_residual_jacobian(model, x)
                measured = numerical_jacobian(model.residual, x)
                np.testing.assert_allclose(predicted, measured, rtol=2e-5, atol=3e-7)
                self.assertEqual(predicted.shape, (len(model.residual(x)), len(x)))

    def test_raw_prediction_derivatives_cover_zero_linear_and_hdr_encoding(self):
        p = photo.PhotoLensFitPolicy(rear_content='explained', minimum_validation_points_per_photo=1)
        _, _, branches, _, _ = photo._prepare(randomized_rows(), single_fixture.BINDING, p)
        model = photo._Model(branches[0], 'gradient_angular_mirror', 'smooth', 'unknown_rear_color', p)
        x = interior_parameters(model)
        for environment in (0., .005, 4.):
            with self.subTest(environment=environment):
                point = x.copy()
                for name in model.photos:
                    point[model.slices[(name, 'environment')]] = environment
                    point[model.slices[(name, 'rear')]] = 0.
                for data in model.data:
                    data['arrays']['background'][:] = 0.
                    actual = prediction_jacobian(model, point, data)
                    expected = numerical_jacobian(lambda value: model.predict(value, data), point, step=1e-8)
                    np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=2e-5)

    def test_joint_shared_priors_and_missing_local_anchor_match_central_differences(self):
        groups = [joint_fixture.group(index, randomized_rows(index, names))
                  for index, names in enumerate((('a', 'b'), ('b', 'c')))]
        p = joint_fixture.policy(families=photo.FAMILIES, lighting_families=('smooth',))
        rear = {gid: 'unknown_rear_color' for gid in ('g0', 'g1')}
        bindings = {(gid, 'b'): {'object_id': 'same-frame', 'source_sha256': 'f'*64} for gid in rear}
        model = joint_fixture.model_for(groups, p, {'g0': 'gradient_tint', 'g1': 'gradient_angular_mirror'},
                                        lighting='smooth', rear_modes=rear, rear_bindings=bindings)
        self.assertIn(('b', 'exposure'), model.models['g1'].slices)
        self.assertEqual(sum(key[0] == 'rear' and key[1] == 'b' for key in model.blocks), 1)
        x = interior_parameters(model)
        actual = joint_residual_jacobian(model, x)
        expected = numerical_jacobian(model.residual, x)
        np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=3e-7)

    def test_clipped_and_quantization_satisfied_intervals_have_no_data_gradient(self):
        p = photo.PhotoLensFitPolicy(rear_content='explained', minimum_validation_points_per_photo=1)
        _, _, branches, _, _ = photo._prepare(randomized_rows(photos=('front',)), single_fixture.BINDING, p)
        model = photo._Model(branches[0], 'uniform_tint', 'constant', 'rear_geometry_unknown_backdrop_only', p)
        data = model.data[0]
        data['arrays']['background'][:] = 0.
        x = model.initial(0)
        for code, radiance in ((0., 0.), (50., ((50/255+.055)/1.055)**2.4), (255., 2.)):
            x[model.slices[('front', 'environment')]] = radiance/.04
            data['schlick'][:] = 0.
            data['arrays']['code'][:] = code
            actual = single_residual_jacobian(model, x)
            self.assertTrue(np.isfinite(actual).all())
            self.assertEqual(np.count_nonzero(actual), 0)
            self.assertEqual(np.count_nonzero(model.residual(x)), 0)

    def test_total_mirror_density_derivatives_are_zero(self):
        p = photo.PhotoLensFitPolicy(rear_content='explained', minimum_validation_points_per_photo=1)
        _, _, branches, _, _ = photo._prepare(randomized_rows(), single_fixture.BINDING, p)
        model = photo._Model(branches[0], 'colored_mirror', 'constant', 'unknown_rear_color', p)
        x = interior_parameters(model)
        x[model.slices['reflection']] = 1.
        for data in model.data:
            actual = prediction_jacobian(model, x, data)
            np.testing.assert_array_equal(actual[:, :, model.slices['density']], 0.)

    def test_srgb_branch_boundary_and_exact_censor_boundary_use_declared_side(self):
        p = photo.PhotoLensFitPolicy(rear_content='explained', minimum_validation_points_per_photo=1)
        _, _, branches, _, _ = photo._prepare(randomized_rows(photos=('front',)), single_fixture.BINDING, p)
        model = photo._Model(branches[0], 'colored_mirror', 'constant', 'rear_geometry_unknown_backdrop_only', p)
        x = np.zeros(len(model.lower))
        data = model.data[0]
        data['schlick'][:] = 0.
        data['path'][:] = 1.
        for radiance in (.0031308-1e-9, .0031308, .0031308+1e-9):
            data['arrays']['background'][:] = radiance
            jacobian = prediction_jacobian(model, x, data)
            slope = 255*12.92 if radiance <= .0031308 else 255*1.055/2.4*radiance**(1/2.4-1)
            np.testing.assert_allclose(jacobian[:, 0, model.slices['density'].start], -radiance*slope, rtol=1e-13)
        # Code0's upper interval endpoint is .5. With zero reflection/density,
        # this exactly representable code prediction is the satisfied boundary.
        data['arrays']['background'][:] = .5/(255*12.92)
        data['arrays']['code'][:] = 0.
        np.testing.assert_array_equal(model.predict(x, data), .5)
        np.testing.assert_array_equal(single_residual_jacobian(model, x), 0.)


if __name__ == '__main__':
    unittest.main()
