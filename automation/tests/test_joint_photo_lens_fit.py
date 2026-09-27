"""Joint-light controls and adversarial evidence/checkpoint tests."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction import joint_photo_lens_fit as joint
from reconstruction import photo_lens_fit as single
from reconstruction.lens_appearance import DensityKeyframe, LensAppearance


def policy(**kwargs):
    return joint.JointPhotoLensFitPolicy(photo_policy=single.PhotoLensFitPolicy(**{
        'families': ('uniform_tint',), 'lighting_families': ('constant',), 'minimum_validation_points_per_photo': 1,
        'roughness_values': (.05,), 'max_nfev': 100, 'rear_content': 'explained', **kwargs}))


def observation(group, photo='front', hypothesis='0', region='region', code=160):
    y, x = np.mgrid[:8, :8]
    x = x.ravel()+8*group; y = y.ravel(); n = len(x)
    return {'id': f'{photo}/{region}/{hypothesis}', 'photo_id': photo, 'region_id': region,
        'hypothesis_id': hypothesis, 'source_sha256': hashlib.sha256(photo.encode()).hexdigest(),
        'provenance': {'method': 'synthetic_shared_photo_control'},
        'xy': np.column_stack((x, y)), 'code_rgb': np.full((n, 3), code, dtype=np.uint8),
        'intrinsic_v': y/7, 'incidence_degrees': np.zeros(n),
        'background_rgb': np.ones((n, 3)), 'image_size': [32, 8]}


def group(index, observations=None):
    return {'surface_binding': {'schema_version': 1, 'prepared_glb_sha256': 'a'*64,
            'material_group_id': f'g{index}', 'coordinate_method': 'actual_prepared_TEXCOORD_0',
            'uv_semantics': 'lens_local_bottom_0_top_1'},
            'observations': observations or [observation(index)]}


def model_for(groups, p=None, families=None, lighting='constant', rear_modes=None, rear_bindings=None):
    p = p or policy()
    bindings, _, branches, _, _ = joint._prepare_joint(groups, p)
    families = families or {g: p.photo_policy.families[0] for g in bindings}
    modes = rear_modes or {g: 'rear_geometry_unknown_backdrop_only' for g in bindings}
    return joint._JointModel(branches[0], families, lighting, modes, p.photo_policy, rear_bindings or {})


class JointPhotoLensFitTests(unittest.TestCase):
    def test_two_materials_recover_shared_light_from_independently_generated_signal(self):
        # Spatially varying supplied background separates transmission from the
        # shared additive reflection. This is deliberately more informative than
        # a white product photo, and tests recovery without using _Model forward.
        densities = ((.25, .5, .85), (1.1, .7, .35))
        environment = np.array([.35, .7, 1.2])
        groups = []
        for index in range(2):
            obs = observation(index)
            obs['image_size'] = [16, 8]
            x = obs['xy'][:, 0] % 8
            obs['background_rgb'] = np.column_stack((.1+.11*x, .85-.09*x, .15+.08*x))
            obs['incidence_degrees'] = 10+obs['xy'][:, 1]*5.
            appearance = LensAppearance((DensityKeyframe(0, densities[index]),))
            rgb = appearance.evaluate(obs['intrinsic_v'], obs['incidence_degrees']).compose(obs['background_rgb'], environment)
            # Independent sRGB encoding/quantization of known physical equations.
            obs['code_rgb'] = np.rint(255*np.where(rgb <= .0031308, 12.92*rgb, 1.055*rgb**(1/2.4)-.055)).astype(np.uint8)
            groups.append(group(index, [obs]))
        for mode in ('finite_difference', 'analytic'):
            with self.subTest(jacobian_mode=mode):
                report = joint.fit_joint_photo_lens_candidates(groups, policy=replace(policy(max_nfev=180), jacobian_mode=mode))
                matched = [c for c in report['candidates'] if c['optimizer']['converged'] and c['photo_policy_status'] == 'within_declared_policy']
                self.assertTrue(matched)
                for candidate in matched:
                    self.assertEqual(candidate['optimizer']['jacobian_mode'], mode)
                    self.assertEqual(len(candidate['shared_nuisance_by_photo']), 1)
                    np.testing.assert_allclose(candidate['shared_nuisance_by_photo'][0]['environment_rgb'], environment, atol=.055)
                    for index in range(2):
                        actual = candidate['groups'][f'g{index}']['appearance']['optical_density_keyframes'][0]['optical_density_rgb']
                        np.testing.assert_allclose(actual, densities[index], atol=.015)
                self.assertNotEqual(matched[0]['groups']['g0']['appearance'], matched[0]['groups']['g1']['appearance'])
                self.assertEqual(report['parameter_identification'], 'unmeasured')
                self.assertNotIn('selected_hypothesis', report)
                self.assertNotIn('accepted', report)

    def test_shared_smooth_forward_matches_independent_canonical_composition_and_gain(self):
        rng = np.random.default_rng(27)
        groups = []
        for index, photos in enumerate((('a', 'b'), ('b', 'c'))):
            rows = []
            for photo in photos:
                obs = observation(index, photo)
                d = rng.normal(size=(64, 3)); d /= np.linalg.norm(d, axis=1)[:, None]
                obs['reflected_direction'] = d
                obs['incidence_degrees'] = rng.uniform(0, 85, 64)
                obs['background_rgb'] = rng.uniform(.1, .9, (64, 3))
                rows.append(obs)
            groups.append(group(index, rows))
        p = policy(families=('gradient_tint', 'angular_mirror'), lighting_families=('smooth',))
        model = model_for(groups, p, {'g0': 'gradient_tint', 'g1': 'angular_mirror'}, lighting='smooth')
        x = np.asarray(model.lower)+rng.uniform(.1, .8, len(model.lower))*(np.asarray(model.upper)-model.lower)
        self.assertEqual(model.components[0]['exposure_white_balance_anchor_photo'], 'a')
        self.assertIn(('b', 'exposure'), model.models['g1'].slices, 'g1 must not independently anchor its first photo b')
        self.assertNotIn(('photo', 'a', 'exposure'), model.blocks)
        self.assertEqual(model.blocks[('photo', 'b', 'exposure')].stop-model.blocks[('photo', 'b', 'exposure')].start, 1)
        for gid, local_model in model.models.items():
            local = x[model.maps[gid]]
            appearance = local_model.appearance(local, .25)
            for data in local_model.data:
                arrays = data['arrays']; photo = data['observation']['photo_id']
                d = arrays['direction']; features = np.column_stack((d, d[:, 0]**2-d[:, 1]**2))
                environment = x[model.blocks[('photo', photo, 'environment')]]*np.exp(features@x[model.blocks[('photo', photo, 'shape')]].reshape(4, 3))
                radiance = appearance.evaluate(arrays['v'], arrays['angle']).compose(arrays['background'], environment)
                if ('photo', photo, 'exposure') in model.blocks:
                    exposure = x[model.blocks[('photo', photo, 'exposure')]][0]
                    a, b = x[model.blocks[('photo', photo, 'wb')]]
                    radiance *= np.exp(exposure+[a, -a-b, b])
                expected = 255*np.where(radiance <= .0031308, 12.92*radiance, 1.055*radiance**(1/2.4)-.055)
                np.testing.assert_allclose(local_model.predict(local, data), expected, rtol=0, atol=2e-10)

    def test_global_weights_and_nuisance_priors_are_not_duplicated_by_group(self):
        groups = [group(i, [observation(i, 'a'), observation(i, 'b')]) for i in range(2)]
        for g in groups:
            for obs in g['observations']:
                obs['reflected_direction'] = np.tile([0., 0., 1.], (64, 1))
        model = model_for(groups, policy(lighting_families=('smooth',)), lighting='smooth')
        self.assertEqual(len(model.lower), 6+2*15+3)
        x = model.initial(0)
        x[model.blocks[('photo', 'a', 'shape')]] = .1
        x[model.blocks[('photo', 'b', 'shape')]] = .2
        x[model.blocks[('photo', 'b', 'exposure')]] = .3
        x[model.blocks[('photo', 'b', 'wb')]] = [.1, -.1]
        expected_data, weight_by_photo = [], {}
        for gid, local_model in model.models.items():
            local = x[model.maps[gid]]
            for data in local_model.data:
                photo = data['observation']['photo_id']
                weight_by_photo[photo] = weight_by_photo.get(photo, 0)+3*np.sum(data['train_weights']**2)
                error = single._interval_residual(local_model.predict(local, data), data['arrays']['code'])[data['train']]/model.policy.code_robust_scale
                expected_data.extend((single._robust_residual(error)*data['train_weights'][:, None]).ravel())
        np.testing.assert_allclose(list(weight_by_photo.values()), 1, atol=1e-14)
        nuisance_values = np.concatenate([x[sl] for key, sl in model.blocks.items() if key[0] == 'photo' and key[2] in ('shape', 'exposure', 'wb')])
        np.testing.assert_allclose(model.residual(x), np.r_[expected_data, model.policy.nuisance_penalty*nuisance_values])

    def test_disconnected_graph_has_separate_explicit_gauges(self):
        report = joint.fit_joint_photo_lens_candidates([group(0, [observation(0, 'a')]), group(1, [observation(1, 'b')])], policy=policy(max_nfev=5))
        gauges = report['candidates'][0]['global_photometric_gauges']
        self.assertEqual(len(gauges['components']), 2)
        self.assertEqual(gauges['relative_component_calibration'], 'unmeasured')
        self.assertEqual([v['exposure_white_balance_anchor_photo'] for v in gauges['components']], ['a', 'b'])

    def test_factorized_roughness_and_explicit_mixed_families(self):
        # The fixed 4x4 grid is pinned here so the frozen-split control below
        # keeps its meaning; the adaptive default would give g1 validation tiles.
        p = policy(families=('uniform_tint', 'colored_mirror'), roughness_values=(.05, .25), max_nfev=5,
                   spatial_split_grids=(4,))
        groups = [group(0), group(1)]
        default = joint.fit_joint_photo_lens_candidates(groups, policy=p)
        self.assertEqual(default['exploration']['optimization_runs'], 6)
        self.assertEqual(default['roughness_factorization']['cartesian_variants_per_candidate'], 4)
        self.assertEqual(default['roughness_factorization']['represented_material_combinations'], 24)
        self.assertFalse(default['family_assignment_prior']['mixed_family_combinations_exhaustive'])
        # The g1 footprint occupies only an odd tile column, which has no
        # validation pixels in this frozen split. Other-group validation must
        # not promote it to measured joint validation.
        self.assertTrue(all(c['photo_policy_status'] == 'validation_unmeasured' for c in default['candidates']))
        mixed = joint.fit_joint_photo_lens_candidates(groups, policy=p, family_assignments=[{'g0': 'uniform_tint', 'g1': 'colored_mirror'}])
        self.assertEqual(mixed['exploration']['optimization_runs'], 3)
        for candidate in mixed['candidates']:
            self.assertEqual(candidate['groups']['g0']['family'], 'uniform_tint')
            self.assertEqual(candidate['groups']['g1']['family'], 'colored_mirror')
            for row in candidate['groups'].values():
                self.assertEqual(len(row['appearance_alternatives']), 2)
                self.assertEqual(row['appearance']['roughness'], .05)

    def test_source_coordinate_and_eligible_ownership_contradictions_rejected_before_fit(self):
        cases = []
        g0, g1 = group(0), group(1)
        g1['observations'][0]['source_sha256'] = 'c'*64
        cases.append(([g0, g1], 'one source'))
        g0 = group(0); second = copy.deepcopy(g0['observations'][0]); second['id'] = 'other'; second['region_id'] = 'other'
        second['intrinsic_v'][0] = np.nan; g0['observations'].append(second)
        cases.append(([g0], 'coordinate or rear/support'))
        g0, g1 = group(0), group(1)
        g1['observations'][0]['xy'] = g0['observations'][0]['xy'].copy()
        cases.append(([g0, g1], 'overlapping eligible ownership'))
        g0, g1 = group(0), group(1)
        g1['observations'][0]['xy'] = g0['observations'][0]['xy'].copy()
        g1['observations'][0]['code_rgb'][0, 0] += 1
        cases.append(([g0, g1], 'inconsistent RGB'))
        with patch.object(joint, 'least_squares', side_effect=AssertionError('must preflight')):
            for groups, message in cases:
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    joint.fit_joint_photo_lens_candidates(groups, policy=policy())

    def test_common_split_global_extent_dedup_and_permutation_invariance(self):
        a, b = observation(0), observation(1)
        a.pop('image_size'); b.pop('image_size')
        duplicate = copy.deepcopy(a); duplicate['id'] = 'duplicate-region'; duplicate['region_id'] = 'other'
        alias = copy.deepcopy(a); alias['id'] = 'alias'; alias['hypothesis_id'] = 'alias'
        groups = [group(0, [a, duplicate, alias]), group(1, [b])]
        report = joint.fit_joint_photo_lens_candidates(groups, policy=policy(max_nfev=5))
        self.assertEqual(report['exploration']['mask_branches'], 1)
        self.assertEqual(len(report['spatial_split']), 1)
        self.assertEqual(report['spatial_split'][0]['span_xy'], [16., 8.])
        rows = copy.deepcopy(groups[::-1]); rng = np.random.default_rng(6)
        for g in rows:
            g['observations'].reverse()
            for obs in g['observations']:
                order = rng.permutation(len(obs['xy']))
                for key, value in obs.items():
                    if isinstance(value, np.ndarray):
                        obs[key] = value[order]
        permuted = joint.fit_joint_photo_lens_candidates(rows, policy=policy(max_nfev=5))
        self.assertEqual(report, permuted)
        model = model_for(groups)
        weights = sum(3*np.sum(d['train_weights']**2) for m in model.models.values() for d in m.data)
        self.assertAlmostEqual(weights, 1)
        self.assertEqual(model.unique_training_pixels['front'], 96)

    def test_rear_nuisance_shares_only_with_explicit_same_object_source(self):
        groups = [group(0), group(1)]
        for g in groups:
            obs = g['observations'][0]
            obs['rear_weight'] = np.full(64, .5)
            obs['rear_rgb'] = np.full((64, 3), np.nan)
        modes = {'g0': 'unknown_rear_color', 'g1': 'unknown_rear_color'}
        separate = model_for(groups, rear_modes=modes)
        self.assertEqual(sum(k[0] == 'rear' for k in separate.blocks), 2)
        bindings = [{'group_id': f'g{i}', 'photo_id': 'front', 'object_id': 'temple', 'source_sha256': 'd'*64} for i in range(2)]
        _, records, _, _, _ = joint._prepare_joint(groups, policy())
        bound = joint._rear_bindings(bindings, records)
        shared = model_for(groups, rear_modes=modes, rear_bindings=bound)
        self.assertEqual(len(shared.lower), len(separate.lower)-3)
        self.assertEqual(sum(k[0] == 'rear' for k in shared.blocks), 1)
        result = joint.fit_joint_photo_lens_candidates(groups, policy=policy(max_nfev=5), rear_source_bindings=bindings)
        self.assertTrue(any(v['reason'] == 'rear_color_unknown_at_positive_geometry_weight' for v in result['exploration']['unsupported_configurations']))

    def test_all_mask_branches_budget_checked_and_validation_is_frozen(self):
        groups = [group(0), group(1)]
        for g in groups:
            obs = g['observations'][0]; other = copy.deepcopy(obs)
            other['id'] += '-subset'; other['hypothesis_id'] = 'subset'
            for k, value in other.items():
                if isinstance(value, np.ndarray):
                    other[k] = value[8:]
            g['observations'].append(other)
        p = policy(max_nfev=5)
        bounded = joint.JointPhotoLensFitPolicy(photo_policy=p.photo_policy, maximum_optimization_runs=11)
        with patch.object(joint, 'least_squares', side_effect=AssertionError('must preflight')):
            with self.assertRaisesRegex(ValueError, 'budget exceeded: 12'):
                joint.fit_joint_photo_lens_candidates(groups, policy=bounded)
        result = joint.fit_joint_photo_lens_candidates(groups, policy=p)
        self.assertEqual(result['exploration']['mask_branches'], 4)
        self.assertEqual(result['exploration']['optimization_runs'], 12)
        self.assertTrue(result['validation_parameters_frozen'])
        holdout = group(0)
        obs = holdout['observations'][0]; xy = obs['xy']; tiles = np.floor(4*xy/[32, 8]).astype(int)
        mask = (tiles[:, 0]+2*tiles[:, 1])%4 == 0
        obs['code_rgb'][mask] = 250
        fitted = joint.fit_joint_photo_lens_candidates([holdout], policy=policy())
        for c in fitted['candidates']:
            m = c['groups']['g0']['photo_metrics'][0]
            self.assertLess(m['train']['mean_absolute_interval_error_codes'], 1)
            self.assertGreater(m['validation']['mean_absolute_interval_error_codes'], 80)
            self.assertEqual(c['photo_policy_status'], 'outside_declared_policy')

    def test_checkpoint_interrupt_resume_exact_and_tamper_input_changes_rejected(self):
        groups = [group(0), group(1)]; p = policy(max_nfev=5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'checkpoints'
            def interrupt(event):
                if event['event'] == 'completed':
                    raise RuntimeError('intentional interruption after durable start')
            with self.assertRaisesRegex(RuntimeError, 'intentional interruption'):
                joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path, progress=interrupt)
            self.assertEqual(len(list(path.glob('*.json'))), 2)
            original = joint.least_squares
            with patch.object(joint, 'least_squares', wraps=original) as optimizer:
                resumed = joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path)
                self.assertEqual(optimizer.call_count, 2)
            with patch.object(joint, 'least_squares', side_effect=AssertionError('completed starts must be reused')):
                again = joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path)
            self.assertEqual(resumed, again)
            fresh = joint.fit_joint_photo_lens_candidates(groups, policy=p)
            self.assertEqual(resumed, fresh)
            changed = copy.deepcopy(groups); changed[0]['observations'][0]['provenance']['extra'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'immutable input'):
                joint.fit_joint_photo_lens_candidates(changed, policy=p, checkpoint_dir=path)
            receipt = next(v for v in path.glob('*.json') if v.name != 'request.json')
            data = json.loads(receipt.read_text()); data['payload']['fit']['x'][0] += .01
            receipt.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path)

    def test_single_group_forward_objective_parity_for_all_families(self):
        rng = np.random.default_rng(71)
        rows = [observation(0, 'a'), observation(0, 'b')]
        for obs in rows:
            d = rng.normal(size=(64, 3)); d /= np.linalg.norm(d, axis=1)[:, None]
            obs['reflected_direction'] = d
        p = policy(families=single.FAMILIES, lighting_families=('smooth',))
        for family in single.FAMILIES:
            model = model_for([group(0, rows)], p, {'g0': family}, lighting='smooth')
            x = np.asarray(model.lower)+rng.uniform(.1, .9, len(model.lower))*(np.asarray(model.upper)-model.lower)
            local = model.models['g0']
            np.testing.assert_allclose(np.dot(model.residual(x), model.residual(x)),
                np.dot(local.residual(x[model.maps['g0']]), local.residual(x[model.maps['g0']])), rtol=0, atol=1e-12)

    def test_initial_checkpoint_crash_and_implementation_mutation_do_not_claim_completion(self):
        groups = [group(0)]; p = policy(max_nfev=5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'checkpoints'; path.mkdir()
            (path/'request.pending').write_text('{incomplete atomic initial write')
            report = joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path)
            self.assertEqual(report['exploration']['optimization_runs'], 3)
            self.assertFalse((path/'request.pending').exists())
        actual = joint._implementation()
        with patch.object(joint, '_implementation', side_effect=[actual, {**actual, 'changed': True}]):
            with self.assertRaisesRegex(ValueError, 'Implementation changed'):
                joint.fit_joint_photo_lens_candidates(groups, policy=p)

    def test_analytic_is_explicit_and_checkpoint_mode_cannot_change(self):
        groups = [group(0), group(1)]; p = policy(max_nfev=8)
        self.assertEqual(p.jacobian_mode, 'finite_difference')
        for invalid in ('auto', '2-point', 'analytic ', True, None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'jacobian_mode'):
                replace(p, jacobian_mode=invalid)
        with patch.object(joint.derivatives, 'joint_residual_jacobian', side_effect=AssertionError('analytic must be opt-in')):
            finite = joint.fit_joint_photo_lens_candidates(groups, policy=p)
        analytic_policy = replace(p, jacobian_mode='analytic')
        original = joint.derivatives.joint_residual_jacobian
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'checkpoint'
            with patch.object(joint.derivatives, 'joint_residual_jacobian', wraps=original) as derivative:
                analytic = joint.fit_joint_photo_lens_candidates(groups, policy=analytic_policy, checkpoint_dir=path)
            self.assertGreater(derivative.call_count, 0)
            self.assertNotEqual(finite['input_sha256'], analytic['input_sha256'])
            self.assertTrue(all(c['optimizer']['jacobian_mode'] == 'analytic' for c in analytic['candidates']))
            request = json.loads((path/'request.json').read_text())['payload']
            self.assertEqual(request['jacobian_mode'], 'analytic')
            expected = hashlib.sha256(Path(joint.derivatives.__file__).read_bytes()).hexdigest()
            self.assertEqual(request['implementation']['files']['photo_lens_derivatives.py'], expected)
            with patch.object(joint, 'least_squares', side_effect=AssertionError('no optimizer on replay or mode mismatch')):
                self.assertEqual(analytic, joint.fit_joint_photo_lens_candidates(groups, policy=analytic_policy, checkpoint_dir=path))
                with self.assertRaisesRegex(ValueError, 'immutable input/policy/implementation'):
                    joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path)

    def test_derivative_source_change_rejects_resume_and_running_completion(self):
        groups = [group(0)]; p = replace(policy(max_nfev=5), jacobian_mode='analytic')
        original_read = Path.read_bytes
        calls = 0
        def changed_derivative(path):
            nonlocal calls
            content = original_read(path)
            if path.name == 'photo_lens_derivatives.py':
                calls += 1
                return content+b'\n# simulated changed derivative bytes\n'
            return content
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'checkpoint'
            joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path)
            with patch.object(Path, 'read_bytes', new=changed_derivative), patch.object(joint, 'least_squares', side_effect=AssertionError('reject before fitting')):
                with self.assertRaisesRegex(ValueError, 'immutable input/policy/implementation'):
                    joint.fit_joint_photo_lens_candidates(groups, policy=p, checkpoint_dir=path)
            self.assertGreater(calls, 0)
        calls = 0
        def changes_during_fit(path):
            nonlocal calls
            content = original_read(path)
            if path.name == 'photo_lens_derivatives.py':
                calls += 1
                if calls > 1:
                    return content+b'\n# simulated change during fit\n'
            return content
        with patch.object(Path, 'read_bytes', new=changes_during_fit):
            with self.assertRaisesRegex(ValueError, 'Implementation changed'):
                joint.fit_joint_photo_lens_candidates(groups, policy=p)


if __name__ == '__main__':
    unittest.main()
