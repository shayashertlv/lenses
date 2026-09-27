import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.appearance_anchors import (sample_appearance_anchors, compile_material_priors,
                                               estimate_transmission_from_contrast)
from reconstruction.photo_semantics import build_image_manifest, validate_product_hypotheses
from reconstruction.lens_appearance import LensAppearance, DensityKeyframe, linear_to_srgb
from test_photo_semantics import response


class AnchorsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'photo.png'
        Image.new('RGB', (40, 30), (130, 120, 110)).save(path)
        self.manifest = build_image_manifest([{'id': 'front', 'path': path}])
        self.report = validate_product_hypotheses(response(), self.manifest)
        xy = np.array([[x, y] for x in range(8, 32, 4) for y in range(6, 26, 4)])
        n = len(xy)
        density = [.4, .65, .95]
        material = LensAppearance((DensityKeyframe(0., tuple(density)),))
        color = material.evaluate(.6, 20).compose([1, 1, 1], [.75, .75, .75])
        codes = np.round(linear_to_srgb(color) * 255).astype(int)
        row = {'id': 'obs', 'photo_id': 'front', 'region_id': 'lens', 'hypothesis_id': '1',
            'source_sha256': self.manifest[0]['sha256'], 'image_size': [40, 30],
            'xy': xy, 'code_rgb': np.broadcast_to(codes, (n, 3)).copy(), 'intrinsic_v': np.full(n, .6),
            'incidence_degrees': np.full(n, 20.), 'background_rgb': np.ones((n, 3)),
            'rear_rgb': np.full((n, 3), np.nan), 'rear_weight': np.zeros(n),
            'provenance': {'method': 'synthetic', 'backdrop': {'p10': [1, 1, 1], 'p90': [1, 1, 1]}}}
        self.groups = [{'surface_binding': {'schema_version': 1, 'material_group_id': 'lens',
            'prepared_glb_sha256': 'a' * 64, 'coordinate_method': 'fixture',
            'uv_semantics': 'lens_local_bottom_0_top_1'}, 'observations': [row]}]
        self.density = density

    def test_numeric_anchor_contains_true_density_and_uses_intrinsic_v(self):
        anchors = sample_appearance_anchors(self.groups, self.report)
        row = anchors['groups']['lens']['density_anchors'][0]
        interval = np.asarray(row['density_interval_rgb'])
        self.assertTrue(np.all(interval[:, 0] <= self.density))
        self.assertTrue(np.all(interval[:, 1] >= self.density))
        self.assertEqual(row['v'], .6)
        self.assertEqual(row['applicable_families'], ['uniform_tint', 'gradient_tint'])
        priors = compile_material_priors(self.report, anchors)
        self.assertEqual(priors['method'], 'semantic_material_priors_v1')
        self.assertEqual(priors['declared_facts'], {})
        self.assertEqual(priors['groups']['lens']['family_preferences'], ['gradient_tint'])

    def test_rear_reflection_unknown_and_clipped_pixels_cannot_anchor(self):
        for kind in ('rear', 'unknown', 'clipped', 'reflection', 'uncertain_backdrop'):
            groups, report = copy.deepcopy(self.groups), copy.deepcopy(self.report)
            row = groups[0]['observations'][0]
            if kind == 'rear': row['rear_weight'][:] = 1
            elif kind == 'unknown': row['intrinsic_v'][:] = np.nan
            elif kind == 'clipped': row['code_rgb'][:] = 255
            elif kind == 'uncertain_backdrop': row['provenance']['backdrop']['p10'] = [.1] * 3
            else:
                report['raw_response']['likely_reflections'][0]['box_yxyx_1000'] = [0, 0, 1000, 1000]
            result = sample_appearance_anchors(groups, report)
            self.assertEqual(result['groups']['lens']['density_anchors'], [], kind)

    def test_duplicate_alternatives_do_not_strengthen_or_add_pixels(self):
        original = sample_appearance_anchors(self.groups, self.report)
        duplicate = copy.deepcopy(self.groups[0]['observations'][0])
        duplicate.update(id='duplicate', hypothesis_id='2')
        self.groups[0]['observations'].append(duplicate)
        result = sample_appearance_anchors(self.groups, self.report)
        a, b = original['groups']['lens']['density_anchors'][0], result['groups']['lens']['density_anchors'][0]
        self.assertEqual(a['support_pixels'], b['support_pixels'])
        self.assertEqual(a['weight'], b['weight'])

    def test_declared_mirror_fact_overrides_tint_prior(self):
        anchors = sample_appearance_anchors(self.groups, self.report)
        priors = compile_material_priors(self.report, anchors, {'mirror_coating': True})
        self.assertEqual(priors['groups']['lens']['density_anchors'], [])
        self.assertEqual(priors['groups']['lens']['family_preferences'], [])

    def test_source_mismatch_rejected(self):
        self.groups[0]['observations'][0]['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source hash/id/pixel grid'):
            sample_appearance_anchors(self.groups, self.report)

    def test_grounded_masks_restrict_the_existing_numeric_anchor_flow(self):
        from reconstruction.image_appearance_evidence import _sha
        mask = np.zeros((30, 40), bool)
        mask[5:25, 5:20] = True
        grounding = {'report': {'method': 'grounded_photo_appearance_evidence_v1', 'regions': [{
            'region_id': 'clean_sampling-1', 'source_sha256': self.manifest[0]['sha256'],
            'status': 'conditional_supported', 'mask_numeric_sha256': _sha(np.packbits(mask).tobytes())}]},
            'masks': {'clean_sampling-1': mask}}
        ungrounded = sample_appearance_anchors(self.groups, self.report)
        grounded = sample_appearance_anchors(self.groups, self.report, grounding=grounding)
        self.assertLess(grounded['groups']['lens']['density_anchors'][0]['support_pixels'],
                        ungrounded['groups']['lens']['density_anchors'][0]['support_pixels'])
        grounding['report']['regions'][0]['status'] = 'unsupported'
        unsupported = sample_appearance_anchors(self.groups, self.report, grounding=grounding)
        self.assertEqual(unsupported['groups']['lens']['density_anchors'], [])


class ContrastTests(unittest.TestCase):
    def setUp(self):
        yy, xx = np.indices((24, 30))
        stripe = np.where(xx % 6 < 3, .15, .65)
        self.rear = np.repeat(stripe[..., None], 3, axis=2)
        self.plane = .08 + .02 * xx / 30 + .04 * yy / 24
        self.mask = np.ones((24, 30), bool)

    def test_recovers_transmission_independent_of_moving_planar_reflection(self):
        for transmission in ([.85, .7, .5], [.35] * 3, [.05] * 3):
            for plane in (self.plane, self.plane[:, ::-1] + .12):
                photo = self.rear * transmission + plane[..., None]
                result = estimate_transmission_from_contrast(photo, self.mask, self.rear)
                self.assertEqual(result['status'], 'conditional_supported')
                np.testing.assert_allclose(result['transmission_rgb'], transmission, atol=1e-10)

    def test_template_uncertainty_does_not_disappear_with_many_pixels(self):
        photo = .5 * self.rear + self.plane[..., None]
        precise = estimate_transmission_from_contrast(photo, self.mask, self.rear)
        uncertain = estimate_transmission_from_contrast(photo, self.mask, self.rear, template_uncertainty=.03)
        self.assertGreater(np.diff(uncertain['transmission_interval_rgb'][0])[0],
                           np.diff(precise['transmission_interval_rgb'][0])[0])

    def test_missing_contrast_clipping_and_registration_error_are_unsupported(self):
        uniform = np.full_like(self.rear, .8)
        self.assertEqual(estimate_transmission_from_contrast(uniform, self.mask, uniform)['status'], 'unsupported')
        self.assertEqual(estimate_transmission_from_contrast(np.ones_like(self.rear), self.mask, self.rear)['status'], 'unsupported')
        photo = .5 * np.roll(self.rear, 1, axis=1) + self.plane[..., None]
        self.assertEqual(estimate_transmission_from_contrast(photo, self.mask, self.rear)['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
