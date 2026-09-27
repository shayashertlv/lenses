"""Counterexamples for conditional photo candidates, not parameter identification."""
import copy
import json
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.lens_appearance import DensityKeyframe, LensAppearance
from reconstruction.photo_lens_fit import (FAMILIES, PhotoLensFitPolicy, _basis, _interval_residual,
    _environment_bounds, _prepare, _Model, candidate_ar_probe_predictions, fit_photo_lens_candidates)


BINDING = {'schema_version': 1, 'prepared_glb_sha256': 'a'*64, 'material_group_id': 'lens-0',
           'coordinate_method': 'actual_prepared_TEXCOORD_0', 'uv_semantics': 'lens_local_bottom_0_top_1'}


def observation(code=150, *, photo='front', hypothesis='0', region='lens-0', rear=False):
    y, x = np.mgrid[:8, :8]
    n = x.size
    rgb = np.broadcast_to(np.asarray(code), (n, 3)).astype(np.uint8).copy()
    result = {'id': f'{photo}/{region}/{hypothesis}', 'photo_id': photo, 'region_id': region,
        'hypothesis_id': hypothesis, 'source_sha256': ('b' if photo == 'front' else 'c')*64,
        'provenance': {'method': 'synthetic_control_not_a_photo_calibration'},
        'xy': np.column_stack((x.ravel(), y.ravel())), 'code_rgb': rgb,
        'intrinsic_v': y.ravel()/7, 'incidence_degrees': np.zeros(n),
        'background_rgb': np.ones((n, 3)), 'image_size': [8, 8]}
    if rear:
        result['rear_weight'] = (y.ravel() >= 4).astype(float)
        result['rear_rgb'] = np.full((n, 3), .03)
    return result


def policy(**kwargs):
    # These fixtures exercise the explicit rear explanations; the production
    # default excludes rear-content samples and is covered by its own tests.
    return PhotoLensFitPolicy(**{'families': ('uniform_tint',), 'lighting_families': ('constant',),
                                'roughness_values': (.05,), 'max_nfev': 100, 'rear_content': 'explained',
                                'minimum_validation_points_per_photo': 1, **kwargs})


def run(observations, **kwargs):
    return fit_photo_lens_candidates(observations, surface_binding=BINDING, policy=policy(**kwargs))


def subset(obs, indices):
    result = copy.deepcopy(obs)
    for key, value in result.items():
        if isinstance(value, np.ndarray):
            result[key] = value[indices]
    return result


class PhotoLensFitTests(unittest.TestCase):
    def test_same_source_cannot_alias_into_another_photos_training_split(self):
        original = observation()
        original.pop('image_size')
        alias = subset(original, np.flatnonzero(original['xy'][:, 0] >= 1))
        alias.update(id='alias/lens-0/0', photo_id='alias')
        # Different sample extents would assign the same source pixels to
        # validation in original and training in alias without this identity check.
        with patch('reconstruction.photo_lens_fit.least_squares', side_effect=AssertionError('must reject before optimization')):
            with self.assertRaisesRegex(ValueError, 'multiple photo IDs'):
                run([original, alias])

    def test_environment_base_and_effective_smooth_bounds_are_distinct_and_finite(self):
        p = policy(lighting_families=('constant', 'smooth'))
        obs = observation(255)
        obs['reflected_direction'] = np.tile([1., 0., 0.], (64, 1))
        _, _, branches, _, _ = _prepare([obs], BINDING, p)
        model = _Model(branches[0], 'colored_mirror', 'smooth', 'rear_geometry_unknown_backdrop_only', p)
        x = model.initial(0)
        x[model.slices['reflection']] = 1.
        x[model.slices[('front', 'environment')]] = p.maximum_environment_base_radiance
        x[model.slices[('front', 'shape')]] = p.smooth_environment_log_bound
        field = p.maximum_environment_base_radiance * np.exp(2 * p.smooth_environment_log_bound)
        bounds = _environment_bounds(p)
        self.assertGreater(field, p.maximum_environment_base_radiance)
        self.assertLess(field, bounds['smooth_field_maximum_channel_radiance'])
        report = run([obs], lighting_families=('constant', 'smooth'), max_nfev=15)
        self.assertEqual(report['environment_bounds'], bounds)
        for candidate in report['candidates']:
            assumptions = candidate['assumptions']
            key = 'smooth_field_maximum_channel_radiance' if assumptions['lighting'] == 'smooth' else 'constant_field_maximum_channel_radiance'
            self.assertEqual(assumptions['maximum_effective_environment_channel_radiance'], bounds[key])
            self.assertEqual(assumptions['environment_radiance_bound_scope'], bounds['scope'])
        self.assertNotIn('maximum_reflected_radiance', report['policy'])
        with self.assertRaisesRegex(ValueError, 'bound must remain finite'):
            policy(smooth_environment_log_bound=1000.)

    def test_all_forward_families_match_canonical_at_gradient_and_angular_knots(self):
        rng = np.random.default_rng(812)
        photos = []
        for name in ('front', 'angled'):
            obs = observation(100, photo=name, rear=True)
            n = len(obs['xy'])
            obs['intrinsic_v'] = np.resize([0., .001, .125, .499, .5, .501, .875, .999, 1.], n)
            obs['incidence_degrees'] = np.resize([0., .001, 15., 44.999, 45., 45.001, 74.999, 75., 75.001, 89.999], n)
            directions = rng.normal(size=(n, 3))
            obs['reflected_direction'] = directions / np.linalg.norm(directions, axis=1)[:, None]
            obs['background_rgb'] = rng.uniform(0, 2, (n, 3))
            obs['rear_rgb'] = rng.uniform(0, 1, (n, 3))
            obs['rear_weight'] = rng.uniform(0, 1, n)
            photos.append(obs)
        p = policy(families=FAMILIES, lighting_families=('constant', 'smooth'))
        _, _, branches, _, _ = _prepare(photos, BINDING, p)
        for family in FAMILIES:
            for lighting in ('constant', 'smooth'):
                for rear_mode in ('geometry_conditioned_rear', 'unknown_rear_color'):
                    model = _Model(branches[0], family, lighting, rear_mode, p)
                    x = np.asarray(model.lower) + rng.uniform(size=len(model.lower)) * (np.asarray(model.upper) - model.lower)
                    appearance = model.appearance(x, .05)
                    for data in model.data:
                        arrays, photo = data['arrays'], data['observation']['photo_id']
                        weight = arrays['rear_weight'][:, None]
                        rear = arrays['rear'] if rear_mode == 'geometry_conditioned_rear' else x[model.slices[(photo, 'rear')]]
                        background = arrays['background'] * (1 - weight) + rear * weight
                        environment = np.broadcast_to(x[model.slices[(photo, 'environment')]], background.shape).copy()
                        if lighting == 'smooth':
                            d = arrays['direction']
                            features = np.column_stack((d, d[:, 0] ** 2 - d[:, 1] ** 2))
                            environment *= np.exp(features @ x[model.slices[(photo, 'shape')]].reshape(4, 3))
                        radiance = appearance.evaluate(arrays['v'], arrays['angle']).compose(background, environment)
                        if (photo, 'exposure') in model.slices:
                            red, blue = x[model.slices[(photo, 'wb')]]
                            radiance *= np.exp(x[model.slices[(photo, 'exposure')]][0] + [red, -red-blue, blue])
                        expected = 255 * np.where(radiance <= .0031308, 12.92 * radiance, 1.055 * radiance ** (1/2.4) - .055)
                        with self.subTest(family=family, lighting=lighting, rear=rear_mode, photo=photo):
                            np.testing.assert_allclose(model.predict(x, data), expected, rtol=0, atol=1e-9)

    def test_five_families_emit_real_canonical_descriptors_and_reuse_roughness_fit(self):
        report = run([observation(255)], families=FAMILIES, roughness_values=(.05, .25), max_nfev=30)
        self.assertEqual(report['status'], 'candidates_available')
        self.assertEqual(report['parameter_identification'], 'unmeasured')
        self.assertEqual(report['exploration']['optimization_runs'], 15)
        self.assertEqual(len(report['candidates']), 30)
        self.assertEqual({c['assumptions']['family'] for c in report['candidates']}, set(FAMILIES))
        self.assertTrue(report['validation_parameters_frozen'])
        for candidate in report['candidates']:
            appearance = LensAppearance.from_dict(candidate['appearance'])
            evaluated = appearance.evaluate([0, .5, 1], [0, 45, 75])
            np.testing.assert_allclose(evaluated.reflectance_rgb + evaluated.transmission_rgb + evaluated.absorption_rgb, 1)
            self.assertEqual(candidate['parameter_identification'], 'unmeasured')
        json.dumps(report, allow_nan=False)

    def test_white_censored_photo_admits_clear_and_mirror_ar_disagreement(self):
        report = run([observation(255)], families=('uniform_tint', 'colored_mirror'))
        compatible = [c for c in report['candidates'] if c['photo_policy_status'] == 'within_declared_policy' and c['optimizer']['converged']]
        self.assertTrue(any(c['appearance']['normal_reflectance_rgb'][0] == .04 for c in compatible))
        self.assertTrue(any(c['appearance']['normal_reflectance_rgb'][0] > .99 for c in compatible))
        self.assertGreater(report['ar_prediction_envelope']['maximum_channel_spread'], .8)
        self.assertEqual(report['ar_prediction_envelope']['response_status'], 'explored_responses_disagree')
        self.assertNotIn('selected_hypothesis', report)
        self.assertNotIn('accepted', report)

    def test_total_mirror_hidden_density_does_not_create_prediction_ambiguity(self):
        clear_density = LensAppearance((DensityKeyframe(0, (0, 0, 0)),), (1, 1, 1))
        hidden_density = LensAppearance((DensityKeyframe(0, (8, 2, 6)),), (1, 1, 1))
        np.testing.assert_array_equal(candidate_ar_probe_predictions(clear_density)['composed_linear_rgb'],
                                      candidate_ar_probe_predictions(hidden_density)['composed_linear_rgb'])

    def test_endpoint_constraints_are_one_sided_not_endpoint_equality(self):
        code = np.array([[0., 255., 100.]])
        np.testing.assert_array_equal(_interval_residual(np.array([[0., 800., 100.4]]), code), 0)
        np.testing.assert_allclose(_interval_residual(np.array([[12., 240., 104.]]), code), [[11.5, -14.5, 3.5]])

    def test_validation_does_not_refit_material_or_lighting(self):
        obs = observation(100)
        xy = obs['xy'];tile = np.floor(xy/2).astype(int)
        holdout = (tile[:, 0] + 2*tile[:, 1]) % 4 == 0
        obs['code_rgb'][holdout] = 230
        report = run([obs])
        self.assertTrue(report['validation_parameters_frozen'])
        self.assertTrue(all(c['photo_policy_status'] == 'outside_declared_policy' for c in report['candidates']))
        for c in report['candidates']:
            m = c['photo_measurements'][0]
            self.assertLess(m['train']['mean_absolute_interval_error_codes'], 1)
            self.assertGreater(m['validation']['mean_absolute_interval_error_codes'], 120)

    def test_validation_tiles_cannot_dominate_a_small_region(self):
        # Seven pixels: the four of a coarse validation tile plus three of a training tile.
        # The 4x4 grid meets the minimums (3 train, 4 validation) but holds 57% in
        # validation; the 8x8 grid interleaves them (5 train, 2 validation) and is chosen.
        obs = subset(observation(), [0, 1, 2, 3, 8, 9, 10])
        _, records, _, _, ledger = _prepare([obs], BINDING, policy())
        self.assertEqual(ledger[0]['method'], 'adaptive_spatial_tiles_share_v2')
        self.assertEqual((ledger[0]['grid'], ledger[0]['maximum_validation_share']), (8, .5))
        self.assertEqual([t['every_row_meets_minimums'] for t in ledger[0]['grids_tried']], [False, True])
        self.assertEqual((int(records[0]['train'].sum()), int(records[0]['validation'].sum())), (5, 2))
        _, records, _, _, ledger = _prepare([obs], BINDING, policy(maximum_validation_share=1.0))
        self.assertEqual(ledger[0]['grid'], 4, 'a share of one reproduces the minimum-only rule')
        self.assertEqual((int(records[0]['train'].sum()), int(records[0]['validation'].sum())), (3, 4))
        for bad in (0, 1.5, True):
            with self.assertRaises(ValueError):
                policy(maximum_validation_share=bad)

    def test_gradient_density_knots_follow_the_policy_and_three_reproduce_the_original_basis(self):
        v = np.linspace(-.1, 1.1, 25)
        index = (v >= .5).astype(int); t = np.clip(v * 2 - index, 0, 1); weight = t * t * (3 - 2 * t)
        original = np.zeros((len(v), 3)); original[np.arange(len(v)), index] = 1 - weight; original[np.arange(len(v)), index + 1] = weight
        np.testing.assert_allclose(_basis(v, 3), original, atol=1e-15)
        five = _basis(v, 5)
        np.testing.assert_allclose(five.sum(axis=1), 1., atol=1e-15)
        np.testing.assert_allclose(five[np.isclose(v, .5)], [[0, 0, 1, 0, 0]], atol=1e-15)
        self.assertTrue(np.all(five[(v > .5) & (v < .75)][:, [0, 1, 4]] == 0), 'a sample blends only its two neighbouring knots')
        obs = observation(); obs['code_rgb'] = np.rint(255 * (1 - .6 * (obs['intrinsic_v'] > .6))[:, None]).astype(np.uint8).repeat(3, axis=1)
        report = run([obs], families=('gradient_tint',))
        for c in report['candidates']:
            keys = c['appearance']['optical_density_keyframes']
            self.assertEqual([k['v'] for k in keys], [0., .25, .5, .75, 1.])
            self.assertEqual(c['optimizer']['parameter_count'], 15 + 3)
        three = run([obs], families=('gradient_tint',), gradient_density_keyframes=3)
        self.assertEqual([k['v'] for k in three['candidates'][0]['appearance']['optical_density_keyframes']], [0., .5, 1.])
        self.assertEqual(three['policy']['gradient_density_keyframes'], 3)
        for bad in (2, 10, 3.0, True):
            with self.assertRaises(ValueError):
                policy(gradient_density_keyframes=bad)

    def test_a_row_with_too_few_held_out_samples_leaves_the_policy_unmeasured(self):
        self.assertEqual(PhotoLensFitPolicy().minimum_validation_points_per_photo, 24)
        # Sixty-four samples hold out 16, 16 and 32 at the three grids: a minimum of 40 is never met,
        # and the candidates say so instead of deciding the policy on those samples.
        report = run([observation()], minimum_validation_points_per_photo=40)
        self.assertTrue(report['spatial_split'][0]['guarantee'].startswith('not met'))
        self.assertTrue(all(c['photo_policy_status'] == 'validation_unmeasured' for c in report['candidates']))
        self.assertEqual(report['diagnosis'], 'validation_unmeasured')

    def test_rear_temple_is_a_separate_explanation_not_forced_gradient(self):
        rear = observation(rear=True)
        plain = observation(photo='angled')
        appearance = LensAppearance((DensityKeyframe(0, (.55, .55, .55)),))
        for obs in (rear, plain):
            b = obs['background_rgb'].copy()
            if 'rear_weight' in obs:
                m = obs['rear_weight'][:, None];b = (1-m)*b + m*obs['rear_rgb']
            signal = appearance.evaluate(obs['intrinsic_v'], obs['incidence_degrees']).compose(b, np.ones_like(b))
            srgb = np.where(signal <= .0031308, signal*12.92, 1.055*signal**(1/2.4)-.055)
            obs['code_rgb'] = np.rint(srgb*255).astype(np.uint8)
        report = run([rear, plain], families=('uniform_tint', 'gradient_tint'), max_nfev=180)
        rear_modes = {c['assumptions']['rear'] for c in report['candidates']}
        self.assertEqual(rear_modes, {'geometry_conditioned_rear', 'unknown_rear_color'})
        matches = [c for c in report['candidates'] if c['assumptions']['family'] == 'uniform_tint' and
                   c['assumptions']['rear'] == 'geometry_conditioned_rear' and c['photo_policy_status'] == 'within_declared_policy']
        self.assertTrue(matches, 'Uniform material plus rear content should explain the apparent dark band')
        for c in matches:
            self.assertEqual(len(c['appearance']['optical_density_keyframes']), 1)
        without_rear = copy.deepcopy(rear)
        del without_rear['rear_rgb'];del without_rear['rear_weight']
        absent = run([without_rear, plain], families=('uniform_tint', 'gradient_tint'), max_nfev=180)
        self.assertFalse(any(c['photo_policy_status'] == 'within_declared_policy' for c in absent['candidates']))

    def test_rear_content_excluded_fits_clean_samples_and_counts_the_rest(self):
        rear = observation(rear=True)
        plain = observation(photo='angled')
        appearance = LensAppearance((DensityKeyframe(0, (.55, .55, .55)),))
        for obs in (rear, plain):
            b = obs['background_rgb'].copy()
            if 'rear_weight' in obs:
                # Rear content with a spatially varying, unknown radiance: no single
                # rear color explains it, so an explained fit is forced away from
                # the true uniform lens.
                m = obs['rear_weight'][:, None]
                stripes = np.where((obs['xy'][:, 0] % 2)[:, None] == 0, .02, .8)
                b = (1-m)*b + m*stripes
                obs['rear_rgb'][:] = np.nan
            signal = appearance.evaluate(obs['intrinsic_v'], obs['incidence_degrees']).compose(b, np.ones_like(b))
            srgb = np.where(signal <= .0031308, signal*12.92, 1.055*signal**(1/2.4)-.055)
            obs['code_rgb'] = np.rint(srgb*255).astype(np.uint8)
        excluded = run([rear, plain], rear_content='excluded', max_nfev=180)
        self.assertEqual({c['assumptions']['rear'] for c in excluded['candidates']}, {'rear_content_excluded'})
        front = next(c for c in excluded['coverage'] if c['observation_id'] == rear['id'])
        self.assertEqual(front['rear_content_samples'], int((rear['rear_weight'] > 0).sum()))
        self.assertEqual(front['excluded_rear_content'], front['rear_content_samples'])
        self.assertEqual(front['eligible'], 64 - front['rear_content_samples'])
        self.assertTrue(any(c['photo_policy_status'] == 'within_declared_policy' for c in excluded['candidates']),
                        'clean transmission samples alone identify the uniform lens')
        for c in excluded['candidates']:
            self.assertNotIn(('front', 'rear'), c.get('nuisance', [{}])[0])
        explained = run([rear, plain], rear_content='explained', max_nfev=180)
        self.assertEqual({c['assumptions']['rear'] for c in explained['candidates']}, {'unknown_rear_color'})
        self.assertFalse(any(c['photo_policy_status'] == 'within_declared_policy' for c in explained['candidates']),
                         'one unknown rear color cannot explain striped rear content')
        json.dumps(excluded, allow_nan=False)
        with self.assertRaises(ValueError):
            policy(rear_content='ignored')
        self.assertEqual(PhotoLensFitPolicy().rear_content, 'excluded')

    def test_appearance_snaps_float32_underflow_to_exact_zero(self):
        from reconstruction.photo_lens_fit import _Model, _prepare
        p = policy(families=('angular_mirror',))
        _, records, branches, _, _ = _prepare([observation()], BINDING, p)
        model = _Model(branches[0], 'angular_mirror', 'constant', 'rear_geometry_unknown_backdrop_only', p)
        x = model.initial(0)
        x[model.slices['density']] = [8e-52, 0.3, 0.]
        x[model.slices['reflection']] = [3.7e-38, .5, 1e-31, .2, .2, .2, .3, .3, .3]
        appearance = model.appearance(x, .05).to_dict()
        self.assertEqual(appearance['optical_density_keyframes'][0]['optical_density_rgb'], [0.0, 0.3, 0.0])
        self.assertEqual(appearance['angular_reflectance_keyframes'][0]['reflectance_rgb'][::2], [0.0, 0.0])
        self.assertAlmostEqual(appearance['angular_reflectance_keyframes'][0]['reflectance_rgb'][1], .5)
        for keyframe in appearance['angular_reflectance_keyframes'] + appearance['optical_density_keyframes']:
            for value in keyframe.get('reflectance_rgb') or keyframe.get('optical_density_rgb'):
                self.assertTrue(value == 0 or abs(value) >= 1e-30)

    def test_missing_rear_color_is_not_replaced_by_white_or_black(self):
        obs = observation(rear=True);obs['rear_rgb'][:] = np.nan
        report = run([obs], max_nfev=20)
        self.assertEqual({c['assumptions']['rear'] for c in report['candidates']}, {'unknown_rear_color'})
        self.assertTrue(any(v.get('reason') == 'rear_color_unknown_at_positive_geometry_weight' for v in report['exploration']['unsupported_configurations']))
        json.dumps(report, allow_nan=False)

    def test_observation_and_row_permutations_are_invariant(self):
        a = observation(130, hypothesis='a');b = subset(observation(130, hypothesis='b'), np.arange(8, 64))
        original = run([a, b], max_nfev=40)
        rng = np.random.default_rng(3)
        reversed_rows = []
        for obs in (b, a):
            new = copy.deepcopy(obs);order = rng.permutation(len(obs['xy']))
            for key in ('xy', 'code_rgb', 'intrinsic_v', 'incidence_degrees', 'background_rgb'):
                new[key] = new[key][order]
            reversed_rows.append(new)
        permuted = run(reversed_rows, max_nfev=40)
        self.assertEqual(original, permuted)

    def test_duplicate_masks_are_aliases_not_extra_evidence_and_regions_are_combined(self):
        a = observation();b = observation(hypothesis='alias')
        report = run([a, b], max_nfev=20)
        self.assertEqual(report['exploration']['mask_branches'], 1)
        self.assertEqual(len(report['duplicate_hypothesis_aliases'][0]['observation_ids']), 2)
        other = observation(region='lens-1')
        combined = run([a, other], max_nfev=20)
        self.assertEqual(len(combined['candidates'][0]['photo_measurements']), 2)
        self.assertEqual(len(combined['candidates'][0]['nuisance']), 1)
        self.assertEqual(combined['candidates'][0]['unique_training_pixels_by_photo'], report['candidates'][0]['unique_training_pixels_by_photo'])
        np.testing.assert_allclose(combined['candidates'][0]['ar_probes']['composed_linear_rgb'], report['candidates'][0]['ar_probes']['composed_linear_rgb'], atol=1e-7)

    def test_outside_family_spatial_pattern_is_mismatch_not_ar_agreement(self):
        obs = observation();xy = obs['xy'].astype(int)
        obs['code_rgb'][:] = np.where(((xy[:, 0]+xy[:, 1])%2)[:, None], 40, 220)
        obs['intrinsic_v'][:] = .5
        report = run([obs], families=FAMILIES, max_nfev=100)
        self.assertFalse(any(c['photo_policy_status'] == 'within_declared_policy' for c in report['candidates']))
        self.assertIn(report['diagnosis'], ('model_mismatch_under_declared_policy', 'optimization_unresolved'))
        self.assertEqual(report['ar_prediction_envelope']['scope'], 'diagnostic_candidates_no_converged_policy_match')

    def test_unknown_coordinates_alpha_and_backside_excluded_and_counted(self):
        obs = observation();obs['alpha_code'] = np.full(64, 255)
        obs['alpha_code'][0] = 128;obs['intrinsic_v'][1] = np.nan;obs['incidence_degrees'][2] = 180
        report = run([obs], max_nfev=20)
        c = report['coverage'][0]
        self.assertEqual((c['eligible'], c['excluded_nonopaque'], c['excluded_unknown_coordinates'], c['excluded_back_interface']), (61, 1, 1, 1))
        self.assertEqual(report['status'], 'candidates_available')
        obs['intrinsic_v'][:] = np.nan
        empty = run([obs])
        self.assertEqual(empty['status'], 'no_supported_candidates')
        self.assertIsNone(empty['ar_prediction_envelope'])

    def test_smooth_requires_real_directions_and_retains_constant_alternative(self):
        obs = observation(255)
        unsupported = run([obs], lighting_families=('constant', 'smooth'), max_nfev=20)
        self.assertEqual({c['assumptions']['lighting'] for c in unsupported['candidates']}, {'constant'})
        self.assertTrue(any(v['reason'] == 'complete_reflected_directions_unavailable' for v in unsupported['exploration']['unsupported_configurations']))
        obs['reflected_direction'] = np.tile([0., 0., 1.], (64, 1))
        supported = run([obs], lighting_families=('constant', 'smooth'), max_nfev=20)
        self.assertEqual({c['assumptions']['lighting'] for c in supported['candidates']}, {'constant', 'smooth'})

    def test_strict_inputs_and_explicit_exploration_budgets(self):
        cases = []
        bad = observation();bad['source_sha256'] = 'missing';cases.append(bad)
        bad = observation();bad['code_rgb'] = bad['code_rgb'].astype(float);cases.append(bad)
        bad = observation();bad['intrinsic_v'][0] = np.inf;cases.append(bad)
        bad = observation();bad['intrinsic_v'][0] = 1.01;cases.append(bad)
        bad = observation();bad['reflected_direction'] = np.ones((64, 3));cases.append(bad)
        bad = observation();bad['provenance']['unknown'] = float('nan');cases.append(bad)
        bad = observation();bad['xy'][1] = bad['xy'][0];cases.append(bad)
        bad = observation();bad['rear_weight'] = np.zeros(64);cases.append(bad)
        bad = observation();bad['intrinsic_v'] = ['0.5']*64;cases.append(bad)
        for bad in cases:
            with self.subTest(keys=list(bad)):
                with self.assertRaises(ValueError):run([bad])
        with self.assertRaisesRegex(ValueError, 'UV semantics'):
            fit_photo_lens_candidates([observation()], surface_binding={**BINDING, 'uv_semantics': 'image_row'})
        with self.assertRaisesRegex(ValueError, 'Mask branch budget'):
            run([observation(100, hypothesis='a'), subset(observation(100, hypothesis='b'), np.arange(8, 64))], maximum_mask_branches=1)
        with self.assertRaisesRegex(ValueError, 'Optimization budget'):
            run([observation()], maximum_optimization_runs=2)
        with self.assertRaisesRegex(ValueError, 'inconsistent RGB'):
            run([observation(100, hypothesis='a'), observation(200, hypothesis='b')])
        with self.assertRaisesRegex(ValueError, 'explicit work budget'):
            run([observation(), observation(photo='angled')], maximum_photos=1)

    def test_inputs_are_not_mutated_and_provenance_is_pinned(self):
        obs = observation(255)
        snapshot = copy.deepcopy(obs)
        report = run([obs], max_nfev=10)
        for key, value in obs.items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(value, snapshot[key])
            else:
                self.assertEqual(value, snapshot[key])
        obs['provenance']['annotation'] = 'alternative source assumption'
        changed = run([obs], max_nfev=10)
        self.assertNotEqual(report['input_sha256'], changed['input_sha256'])
        self.assertNotIn('annotation', report['coverage'][0]['provenance'])

    def test_json_arrays_unknown_coordinates_and_numpy_policy_scalars_serialize(self):
        obs = observation(255)
        for key, value in list(obs.items()):
            if isinstance(value, np.ndarray):
                obs[key] = value.tolist()
        obs['intrinsic_v'][0] = None
        report = run([obs], code_robust_scale=np.float32(4), max_nfev=10)
        self.assertEqual(report['coverage'][0]['eligible'], 63)
        json.dumps(report, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
