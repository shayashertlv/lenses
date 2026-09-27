import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.image_appearance_evidence import (ground_semantic_regions,
    sample_image_appearance_evidence, propose_transmission_candidates, validate_image_appearance_evidence)
from reconstruction.photo_semantics import build_image_manifest, validate_product_hypotheses
from test_photo_semantics import response


class Apertures:
    def __init__(self, mask, second=None):
        self.mask, self.second = mask, mask if second is None else second
        self.calls = 0

    def describe(self):
        return {'method': 'fixed-test-apertures-v1'}

    def propose(self, rgb):
        self.calls += 1
        return {'size_xy': list(rgb.shape[1::-1]), 'variants': {
            'full': {'mask': self.mask, 'known_domain': np.ones(self.mask.shape, bool)},
            'crop': {'mask': self.second, 'known_domain': np.ones(self.mask.shape, bool)}}}


class ImageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'photo.png'
        self.rgb = np.full((100, 140, 3), 255, np.uint8)
        self.rgb[20:80, 20:120] = [210, 110, 150]
        self.mask = np.zeros((100, 140), bool)
        self.mask[20:80, 20:120] = True
        raw = response()
        raw['likely_reflections'] = []
        raw['clean_color_sampling_regions'][0]['box_yxyx_1000'] = [250, 200, 750, 800]
        raw['material_interpretations'][0].update(absorption='uniform_tint', coating='colored_mirror')
        self.raw = raw
        self.refresh()

    def refresh(self):
        Image.fromarray(self.rgb).save(self.path)
        self.manifest = build_image_manifest([{'id': 'back', 'path': self.path}])
        self.semantic = validate_product_hypotheses(self.raw, self.manifest)

    def evidence(self, engine=None, output=None):
        grounded = ground_semantic_regions(self.semantic, engine or Apertures(self.mask), output)
        observed = sample_image_appearance_evidence(self.semantic, grounded, view_labels={'back': 'back'})
        return grounded, observed

    def test_real_pixel_color_replaces_qualitative_color_names(self):
        self.raw['clean_color_sampling_regions'][0]['qualitative_observed_color'] = 'blue'
        self.refresh()
        grounded, evidence = self.evidence()
        obs = evidence['observations'][0]
        self.assertGreater(obs['support_pixels'], 100)
        self.assertEqual(obs['code_rgb_median'], [210, 110, 150])
        self.assertIsNone(obs['incidence_degrees'])
        self.assertIsNone(obs['intrinsic_v'])
        self.assertEqual(obs['view_label'], 'back')
        self.assertEqual(obs['backdrop_clipped_white_fraction_rgb'], [1., 1., 1.])
        self.assertEqual(obs['physical_transmission_status'], 'unidentified_without_calibrated_backdrop_and_reflection')
        self.assertTrue(all(not s['physical_identification'] for s in obs['display_referred_transmission_hypotheses']))

    def test_intersection_edges_and_reflections_remove_contamination(self):
        self.rgb[35:65, 65:70] = [5, 5, 5]
        self.raw['likely_reflections'] = [{'image_number': 1, 'box_yxyx_1000': [250, 200, 750, 300],
                                          'rationale': 'test', 'confidence': 'high'}]
        self.refresh()
        alternative = self.mask.copy(); alternative[:, 105:] = False
        grounded, observed = self.evidence(Apertures(self.mask, alternative))
        mask = grounded['masks']['clean_sampling-1']
        self.assertFalse(mask[:, 105:].any())
        self.assertFalse(mask[:, 28:42].any())
        self.assertFalse(mask[35:65, 63:72].any())
        self.assertGreater(grounded['report']['regions'][0]['edge_excluded_pixels'], 0)

    def test_cache_reuses_only_pinned_masks_and_sources(self):
        engine = Apertures(self.mask)
        folder = self.root / 'grounding'
        first, _ = self.evidence(engine, folder)
        second, _ = self.evidence(engine, folder)
        self.assertEqual(engine.calls, 1)
        self.assertEqual(first['report'], second['report'])
        mask_path = folder / first['report']['regions'][0]['mask']['path']
        Image.new('L', (140, 100), 255).save(mask_path)
        with self.assertRaisesRegex(ValueError, 'mask changed'):
            self.evidence(engine, folder)

    def test_uncertain_or_missing_aperture_does_not_fall_back_to_box(self):
        empty = np.zeros_like(self.mask)
        _, evidence = self.evidence(Apertures(self.mask, empty))
        obs = evidence['observations'][0]
        self.assertEqual(obs['status'], 'unsupported')
        self.assertEqual(obs['support_pixels'], 0)
        self.assertNotIn('linear_rgb_interval', obs)

    def test_source_or_invented_coordinate_rejected(self):
        _, evidence = self.evidence()
        bad = copy.deepcopy(evidence)
        bad['observations'][0]['incidence_degrees'] = 0.
        with self.assertRaisesRegex(ValueError, 'invent optical coordinates'):
            validate_image_appearance_evidence(bad, self.manifest)
        bad = copy.deepcopy(evidence); bad['observations'][0]['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source binding'):
            validate_image_appearance_evidence(bad, self.manifest)

    def test_unique_group_backward_transmission_is_only_candidate_assumption(self):
        _, evidence = self.evidence()
        group = {'surface_binding': {'material_group_id': 'shield', 'prepared_glb_sha256': 'a' * 64}}
        candidates = propose_transmission_candidates(self.semantic, evidence, [group])
        self.assertEqual(len(candidates), 3)
        for candidate in candidates:
            self.assertEqual(candidate['group_ids'], ['shield'])
            self.assertEqual(candidate['incidence_status'], 'nominal_normal_incidence_candidate_assumption')
            self.assertIsNone(candidate['observed_incidence_degrees'])
            self.assertEqual(candidate['status'], 'conditional_display_proxy_not_physical_measurement')
        self.assertEqual(propose_transmission_candidates(self.semantic, evidence, [group, group]), [])
        evidence['observations'][0]['view_label'] = 'front'
        self.assertEqual(propose_transmission_candidates(self.semantic, evidence, [group]), [])

    def test_clipped_clear_region_is_preserved_without_fake_physical_identification(self):
        self.rgb[:] = 255
        self.refresh()
        _, evidence = self.evidence()
        row = evidence['observations'][0]
        self.assertEqual(row['clipped_white_fraction_rgb'], [1., 1., 1.])
        self.assertEqual(row['status'], 'conditional_supported')
        self.assertIn('unidentified', row['physical_transmission_status'])
        self.assertEqual(len(row['display_referred_transmission_hypotheses']), 3)


if __name__ == '__main__':
    unittest.main()
