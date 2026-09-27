"""Controlled appearance priors and photographic-reflection separation tests."""
import copy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reconstruction import joint_photo_lens_fit as joint
from reconstruction import photo_lens_fit as single
from reconstruction.lens_appearance import DensityKeyframe, LensAppearance


SOURCE = hashlib.sha256(b'synthetic front source').hexdigest()


def fixture(*, box=(.125, .1875, .875, .4375), amplitude=2., mirror=False):
    groups = []
    targets = []
    for group in range(2):
        yy, xx = np.mgrid[:16, :16]
        x, y = xx.ravel()+group*16, yy.ravel()
        v = y/15
        # Independent canonical evaluator supplies the material response. The
        # backdrop variation identifies T separately from additive reflection.
        lower = np.array([.2, .5, .85]) + .1*group
        upper = np.array([.65, 1.1, 1.6]) + .1*group
        values = [lower+(upper-lower)*value for value in np.linspace(0, 1, 5)]
        reflection = (.36, .12, .045) if mirror else (.04, .04, .04)
        appearance = LensAppearance(tuple(DensityKeyframe(float(v), tuple(d))
                                    for v, d in zip(np.linspace(0, 1, 5), values)), reflection)
        targets.append(appearance)
        background = np.column_stack((.2+.06*(xx.ravel()%8), .7-.05*(xx.ravel()%8), .25+.055*(xx.ravel()%8)))
        x0, y0, x1, y1 = box
        inside = (((x+.5)/32 >= x0) & ((x+.5)/32 <= x1)
                  & ((y+.5)/16 >= y0) & ((y+.5)/16 <= y1))
        environment = np.broadcast_to((.4+amplitude*inside)[:, None], (len(x), 3))
        linear = appearance.evaluate(v, np.zeros(len(x))).compose(background, environment)
        code = np.rint(255*np.where(linear <= .0031308, 12.92*linear, 1.055*linear**(1/2.4)-.055))
        observation = {'id': f'g{group}/front', 'photo_id': 'front', 'region_id': 'lens', 'hypothesis_id': '0',
                       'source_sha256': SOURCE, 'provenance': {'method': 'independent_canonical_synthetic_scene'},
                       'xy': np.column_stack((x, y)), 'code_rgb': np.clip(code, 0, 255).astype(np.uint8),
                       'intrinsic_v': v, 'incidence_degrees': np.zeros(len(x)),
                       'background_rgb': background, 'image_size': [32, 16]}
        groups.append({'surface_binding': {'schema_version': 1, 'prepared_glb_sha256': 'a'*64,
                       'material_group_id': f'g{group}', 'coordinate_method': 'synthetic_reference',
                       'uv_semantics': 'lens_local_bottom_0_top_1'}, 'observations': [observation]})
    priors = {'schema_version': 1, 'method': 'semantic_material_priors_v1', 'source_image_sha256': [SOURCE],
              'groups': {f'g{i}': {'density_anchors': [
                  {'v': frame.v, 'density_rgb': list(frame.optical_density_rgb), 'sigma': [.5]*3,
                   'weight': .3, 'source_sha256': SOURCE, 'photo_id': 'front'}
                  for frame in (target.optical_density_keyframes[0], target.optical_density_keyframes[-1])],
                  'family_preferences': ['gradient_tint']} for i, target in enumerate(targets)},
              'photos': {'front': {'source_sha256': SOURCE, 'illuminant_rgb': [1., 1., 1.],
                         'illumination_hypothesis': 'neutral_studio_chromaticity_hypothesis',
                         'reflection_regions': [{'bbox_xyxy_normalized': list(box), 'feather': .001}]}},
              'declared_facts': {}, 'hypotheses': []}
    return groups, priors, targets


def policy(**kwargs):
    return joint.JointPhotoLensFitPolicy(photo_policy=single.PhotoLensFitPolicy(
        families=('gradient_tint',), lighting_families=('semantic_softbox',), roughness_values=(.05,),
        minimum_validation_points_per_photo=8, max_nfev=180, **kwargs))


class SemanticPhotoLensFitTests(unittest.TestCase):
    def test_moving_and_brightening_softbox_preserves_shared_brown_gradient(self):
        material_values = []
        for box, amplitude in (((.125, .1875, .875, .4375), 2.), ((.125, .5625, .875, .8125), 4.)):
            groups, priors, targets = fixture(box=box, amplitude=amplitude)
            report = joint.fit_joint_photo_lens_candidates(groups, policy=policy(), appearance_priors=priors)
            self.assertEqual(report['exploration']['failed_runs'], [])
            best = min(report['candidates'], key=lambda c: c['optimizer']['objective_including_priors'])
            self.assertTrue(best['optimizer']['converged'])
            light = best['shared_nuisance_by_photo']
            self.assertEqual(len(light), 1)
            np.testing.assert_allclose(light[0]['softbox_amplitudes'], [amplitude], atol=.05)
            self.assertFalse(light[0]['nuisance_exported_to_material'])
            for i, target in enumerate(targets):
                actual = best['groups'][f'g{i}']['appearance']
                densities = np.asarray([k['optical_density_rgb'] for k in actual['optical_density_keyframes']])
                expected = np.asarray([k.optical_density_rgb for k in target.optical_density_keyframes])
                np.testing.assert_allclose(densities, expected, atol=.06)
                self.assertNotIn('softbox', str(actual))
            material_values.append([k['optical_density_rgb'] for k in best['groups']['g0']['appearance']['optical_density_keyframes']])
        np.testing.assert_allclose(material_values[0], material_values[1], atol=.06)

    def test_softbox_color_cannot_absorb_a_colored_coating(self):
        groups, priors, targets = fixture(amplitude=.8, mirror=True)
        # A constant density target and a varying backdrop isolate the coating
        # color; normal incidence does not establish its angular response.
        p = replace(policy(), photo_policy=replace(policy().photo_policy, families=('gradient_angular_mirror',)))
        report = joint.fit_joint_photo_lens_candidates(groups, policy=p, appearance_priors=priors)
        best = min(report['candidates'], key=lambda c: c['optimizer']['objective_including_priors'])
        for row in best['shared_nuisance_by_photo']:
            self.assertEqual(row['illuminant_rgb'], [1., 1., 1.])
        # Absolute mirror strength and field intensity retain a gauge, but a
        # neutral scalar environment cannot steal the coating's RGB ratio.
        reflection = np.asarray(best['groups']['g0']['appearance']['normal_reflectance_rgb'])
        np.testing.assert_allclose(reflection/reflection[0], np.array([.36, .12, .045])/.36, atol=.035)

    def test_shared_photo_blocks_and_material_priors_count_once(self):
        groups, priors, _ = fixture()
        bindings, records, branches, _, _ = joint._prepare_joint(groups, policy())
        priors = single.validate_appearance_priors(priors, records, bindings)
        model = joint._JointModel(branches[0], {'g0': 'gradient_tint', 'g1': 'gradient_tint'}, 'semantic_softbox',
                                  {'g0': 'rear_content_excluded', 'g1': 'rear_content_excluded'}, policy().photo_policy, {}, priors)
        blocks = model.parameter_receipt()
        for name in ('environment', 'softbox'):
            matching = [b for b in blocks if b['key'] == ['photo', 'front', name]]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]['referenced_by_groups'], ['g0', 'g1'])
            self.assertEqual(matching[0]['count'], 1)
        x = model.initial(0)
        expected = sum(3*sum(d['train']) for m in model.models.values() for d in m.data)
        expected += sum(len(m.material_penalties(x[model.maps[g]])) for g, m in model.models.items())
        expected += 1  # one shared softbox regularizer
        self.assertEqual(len(model.residual(x)), expected)

    def test_wrong_anchor_is_soft_and_cannot_remove_contrary_starts(self):
        groups, priors, targets = fixture()
        for value in priors['groups'].values():
            for anchor in value['density_anchors']:
                anchor.update(density_rgb=[4., 4., 4.], sigma=[1.]*3, weight=.1)
        report = joint.fit_joint_photo_lens_candidates(groups, policy=policy(), appearance_priors=priors)
        self.assertEqual({c['assumptions']['start'] for c in report['candidates']}, {0, 1, 2})
        best = min(report['candidates'], key=lambda c: c['optimizer']['objective_including_priors'])
        actual = best['groups']['g0']['appearance']['optical_density_keyframes'][0]['optical_density_rgb']
        np.testing.assert_allclose(actual, targets[0].optical_density_keyframes[0].optical_density_rgb, atol=.08)

    def test_declared_fact_wins_over_inferred_family_preference(self):
        groups, priors, _ = fixture()
        priors['declared_facts']['mirror_coating'] = True
        p = replace(policy(), photo_policy=replace(policy().photo_policy,
                    families=('gradient_tint', 'colored_mirror'), max_nfev=2))
        report = joint.fit_joint_photo_lens_candidates(groups, policy=p, appearance_priors=priors)
        self.assertTrue(report['candidates'])
        self.assertTrue(all(g['family'] == 'colored_mirror' for c in report['candidates'] for g in c['groups'].values()))

    def test_source_mismatch_and_analytic_mode_fail_before_checkpoints(self):
        groups, priors, _ = fixture()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'no-write'
            with self.assertRaisesRegex(ValueError, 'finite_difference'):
                joint.fit_joint_photo_lens_candidates(groups, policy=replace(policy(), jacobian_mode='analytic'),
                                                       appearance_priors=priors, checkpoint_dir=target)
            self.assertFalse(target.exists())
        changed = copy.deepcopy(priors)
        changed['photos']['front']['source_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'source'):
            joint.fit_joint_photo_lens_candidates(groups, policy=policy(), appearance_priors=changed)

    def test_changed_prior_cannot_reuse_checkpoint(self):
        groups, priors, _ = fixture()
        p = replace(policy(), photo_policy=replace(policy().photo_policy, max_nfev=1))
        with tempfile.TemporaryDirectory() as directory:
            joint.fit_joint_photo_lens_candidates(groups, policy=p, appearance_priors=priors, checkpoint_dir=Path(directory))
            priors['groups']['g0']['density_anchors'][0]['sigma'][0] = .6
            with self.assertRaisesRegex(ValueError, 'Checkpoint request differs'):
                joint.fit_joint_photo_lens_candidates(groups, policy=p, appearance_priors=priors, checkpoint_dir=Path(directory))


if __name__ == '__main__':
    unittest.main()
