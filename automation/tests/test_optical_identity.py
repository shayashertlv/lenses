"""Adversarial association tests: no source-primitive or view-label identity."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

import numpy as np

from reconstruction.optical_identity import (IdentityGraphLimits, build_optical_identity_graph,
                                             identity_array_sha256, identity_json_sha256)


SOURCE = 'a'*64


def entity(identifier, faces, kind='primitive'):
    return {'id': identifier, 'kind': kind, 'face_indices': np.array(faces, np.int64),
            'provenance': {'basis': 'fixture prior, not physical identity'}}


def region(identifier, mask, prior=(), copies=1):
    return {'id': identifier, 'prior_entity_ids': list(prior), 'provenance': {'prompt': 'fixture'},
            'alternatives': [{'id': f'decoder-{i}', 'mask': np.asarray(mask, bool),
                              'predicted_quality': .99, 'provenance': {'decoder': i}}
                             for i in range(copies)]}


def view(identifier, faces, regions, entity_ids=None):
    field = np.asarray(faces, np.int64)
    camera = {'yaw': 0 if identifier == 'front' else 30, 'scale': 2., 'center_x': 1., 'center_y': 1.}
    h, w = field.shape[-2:]
    scope = {'kind': 'full_scene_frontmost_opaque_geometry', 'provenance': {'raster': 'fixture'}}
    if entity_ids is not None:
        scope = {'kind': 'per_entity_first_hit', 'entity_ids': entity_ids, 'provenance': {'raster': 'fixture'}}
    return {'id': identifier, 'source_sha256': SOURCE, 'image_sha256': ('b' if identifier == 'front' else 'c')*64,
            'camera': camera, 'camera_sha256': identity_json_sha256(camera), 'face_index': field,
            'pixel_mapping': {'native_size_xy': [w*2, h*2], 'grid_size_xy': [w, h],
                              'method': 'nearest native pixel at working center', 'provenance': {'fixture': True}},
            'visibility_scope': scope, 'regions': regions, 'provenance': {'camera': 'unverified hypothesis'}}


def graph(entities, views, **kwargs):
    return build_optical_identity_graph(source_sha256=SOURCE, face_count=5, entities=entities, views=views, **kwargs)


class OpticalIdentityTests(unittest.TestCase):
    def test_cross_view_region_ordinals_can_swap_without_swapping_identity(self):
        entities = [entity('first', [0]), entity('second', [1])]
        a = view('front', [[0, 1]], [region('region-0', [[1, 0]], ['first']), region('region-1', [[0, 1]], ['second'])])
        b = view('angled', [[1, 0]], [region('region-0', [[1, 0]], ['second']), region('region-1', [[0, 1]], ['first'])])
        result = graph(entities, [a, b])
        edges = {tuple(e['alternatives']) for e in result['cross_view_edges']}
        self.assertEqual(edges, {('angled/region-0/decoder-0', 'front/region-1/decoder-0'),
                                 ('angled/region-1/decoder-0', 'front/region-0/decoder-0')})
        self.assertFalse(result['accepted']); self.assertIsNone(result['physical_optical_instance_count'])
        self.assertEqual(result['semantic_identity'], 'unverified')
        json.dumps(result, allow_nan=False)

    def test_single_primitive_multiple_regions_is_split_hypothesis_not_pair_rule(self):
        entities = [entity('main', [0, 1, 2])]
        regions = [region(f'region-{i}', [[j == i for j in range(3)]], ['main']) for i in range(3)]
        result = graph(entities, [view('front', [[0, 1, 2]], regions), view('angled', [[0, 1, 2]], [])])
        split = [d for d in result['diagnostics'] if d['kind'] == 'multiple_regions_associate_with_entity']
        self.assertEqual(len(split), 3)
        self.assertTrue(all(d['alternative_union_mask_iou'] == 0 for d in split))
        self.assertIsNone(result['physical_optical_instance_count'])

    def test_invisible_silicone_prior_is_missing_not_negative_identity(self):
        entities = [entity('main', [0, 1]), entity('silicone', [4])]
        result = graph(entities, [view('front', [[0, -1]], [region('r', [[1, 1]], ['main'])]),
                                  view('angled', [[1, -1]], [region('r', [[1, 0]], ['main'])])])
        missing = [d for d in result['diagnostics'] if d['kind'] == 'entity_unobserved_in_supplied_ray_domain']
        self.assertEqual([d['entity_id'] for d in missing], ['silicone', 'silicone'])
        self.assertFalse(result['cross_view_edges'])
        self.assertEqual(result['view_pair_coverage'][0]['status'], 'unmeasured_no_common_visible_faces')
        self.assertEqual(result['alternatives'][0]['unexplained_pixels'], 1)

    def test_all_duplicate_and_empty_mask_alternatives_are_retained(self):
        a = view('front', [[0, 1]], [region('r', [[1, 0]], copies=3), region('empty', [[0, 0]])])
        b = view('angled', [[0, 1]], [region('r', [[1, 0]], copies=3)])
        result = graph([entity('whole', [0, 1])], [a, b])
        self.assertEqual(len(result['alternatives']), 7)
        self.assertEqual(len(result['cross_view_edges']), 9)
        self.assertEqual(len({n['mask_sha256'] for n in result['alternatives']}), 2)
        self.assertEqual(result['alternatives'][3]['status'], 'unmeasured_geometry_association')
        self.assertTrue(all(r['selected_alternative'] is None for r in result['regions']))
        self.assertIn('not semantic confidence', result['alternatives'][0]['quality_scope'])

    def test_merged_mask_touches_distinct_entities_but_parent_component_overlap_is_not_merge(self):
        entities = [entity('whole', [0, 1]), entity('fragment-a', [0], 'connected_component'),
                    entity('fragment-b', [1], 'connected_component')]
        result = graph(entities, [view('front', [[0, 1]], [region('r', [[1, 1]])]), view('angled', [[0, 1]], [])])
        merge = [d for d in result['diagnostics'] if d['kind'] == 'region_spans_disjoint_entity_priors']
        self.assertEqual(merge[0]['entity_pairs'], [['fragment-a', 'fragment-b']])
        self.assertEqual(len(result['alternatives'][0]['entity_associations']), 3)

    def test_supplied_active_entity_first_hits_retain_back_surface_scope(self):
        entities = [entity('front-part', [0]), entity('rear-part', [1])]
        first = view('front', [[[0, 0]], [[1, 1]]], [region('r', [[1, 1]])], ['front-part', 'rear-part'])
        second = view('angled', [[0, 0]], [region('r', [[1, 1]])])
        result = graph(entities, [first, second])
        self.assertEqual(result['alternatives'][0]['mask_pixels'], 2)
        self.assertEqual(result['alternatives'][0]['geometry_pixels'], 2)
        self.assertEqual(result['alternatives'][0]['geometry_hit_events'], 4)
        self.assertEqual(result['views'][0]['visibility_scope']['kind'], 'per_entity_first_hit')
        bad = deepcopy(first); bad['face_index'][1, 0, 0] = 0
        with self.assertRaisesRegex(ValueError, 'outside its stated entity'):
            graph(entities, [bad, second])

    def test_wrong_prior_is_association_conflict_not_verified_semantic_contradiction(self):
        result = graph([entity('claimed', [0]), entity('other', [1])],
                       [view('front', [[1]], [region('r', [[1]], ['claimed'])]), view('angled', [[1]], [])])
        conflicts = [d for d in result['diagnostics'] if d['kind'] == 'declared_prior_association_conflict']
        self.assertEqual(conflicts[0]['semantic_contradiction'], 'unverified')

    def test_cross_view_link_exposes_support_only_from_frame_contamination(self):
        entities = [entity('lens-a', [0]), entity('lens-b', [1]), entity('frame', [2])]
        first = view('front', [[0, 2]], [region('r', [[1, 1]], ['lens-a'])])
        second = view('angled', [[1, 2]], [region('r', [[1, 1]], ['lens-b'])])
        result = graph(entities, [first, second])
        self.assertEqual(len(result['cross_view_edges']), 1)
        self.assertEqual(result['cross_view_edges'][0]['shared_entity_face_counts'], [{'entity_id': 'frame', 'face_count': 1}])
        self.assertEqual(result['cross_view_edges'][0]['identity'], 'unverified_candidate_link')

    def test_hashes_input_preservation_and_strict_pin_validation(self):
        first = view('front', [[0, -1]], [region('r', [[1, 0]])]); second = view('angled', [[0, -1]], [])
        first['face_index_sha256'] = identity_array_sha256(first['face_index'])
        first['regions'][0]['alternatives'][0]['mask_sha256'] = identity_array_sha256(first['regions'][0]['alternatives'][0]['mask'])
        original = deepcopy(first)
        result = graph([entity('e', [0])], [first, second])
        digest = result.pop('graph_sha256'); self.assertEqual(digest, identity_json_sha256(result))
        np.testing.assert_array_equal(first['face_index'], original['face_index'])
        for field in ('source_sha256', 'camera_sha256', 'face_index_sha256'):
            bad = deepcopy(first); bad[field] = 'd'*64
            with self.assertRaises(ValueError): graph([entity('e', [0])], [bad, second])
        bad = deepcopy(first); bad['regions'][0]['alternatives'][0]['mask_sha256'] = 'd'*64
        with self.assertRaisesRegex(ValueError, 'Mask digest mismatch'): graph([entity('e', [0])], [bad, second])

    def test_invalid_masks_duplicates_mappings_and_quality_reject(self):
        first = view('front', [[0, -1]], [region('r', [[1, 0]])]); second = view('angled', [[0, -1]], [])
        variants = []
        bad = deepcopy(first); bad['regions'][0]['alternatives'][0]['mask'] = np.array([[1, 0]], np.uint8); variants.append(bad)
        bad = deepcopy(first); bad['pixel_mapping']['grid_size_xy'] = [2, 2]; variants.append(bad)
        bad = deepcopy(first); bad['regions'][0]['alternatives'][0]['predicted_quality'] = float('nan'); variants.append(bad)
        bad = deepcopy(first); bad['regions'].append(deepcopy(bad['regions'][0])); variants.append(bad)
        bad = deepcopy(first); bad['regions'][0]['alternatives'].append(deepcopy(bad['regions'][0]['alternatives'][0])); variants.append(bad)
        bad = deepcopy(first); bad['face_index'][0, 0] = 5; variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(ValueError): graph([entity('e', [0])], [bad, second])

    def test_capacity_fails_without_discarding_decoder_alternatives(self):
        views = [view('front', [[0]], [region('r', [[1]], copies=3)]),
                 view('angled', [[0]], [region('r', [[1]], copies=3)])]
        for limits in (replace(IdentityGraphLimits(), max_alternatives=2),
                       replace(IdentityGraphLimits(), max_mask_pixels=1),
                       replace(IdentityGraphLimits(), max_face_edges=1),
                       replace(IdentityGraphLimits(), max_cross_view_edges=1),
                       replace(IdentityGraphLimits(), max_cross_view_face_pairs=1)):
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, 'capacity'):
                graph([entity('e', [0])], views, limits=limits)


if __name__ == '__main__':
    unittest.main()
