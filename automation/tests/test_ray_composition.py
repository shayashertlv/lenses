"""Analytic ray-content classification under explicit group roles."""
import json
import unittest

import numpy as np

from reconstruction.ray_composition import CODES, compose_view_rays


SHAPE = (4, 4)


def px(*indices):
    return np.asarray(sorted(indices), np.int64)


def make_view(pieces, apertures=None):
    pixels, depths = [], []
    for p, d in pieces:
        pixels.append(p); depths.append(np.full(len(p), float(d)))
    interpretations = []
    for aid, mask in (apertures or {}).items():
        full = np.zeros(SHAPE, bool); full.ravel()[mask] = True
        interpretations.append({'id': aid, 'mask': full.ravel(), 'known': np.ones(SHAPE[0]*SHAPE[1], bool), 'pixels': int(full.sum())})
    return {'id': 'v', 'shape': SHAPE, 'component_pixels': pixels, 'component_depths': depths, 'interpretations': interpretations}


class RayCompositionTests(unittest.TestCase):
    def test_each_class_is_produced_by_its_construction(self):
        # 0 lens A (1.0), 1 lens B (1.5), 2 frame front (0.5), 3 pad (2.0),
        # 4 frame behind pixel 2 (1.5), 5 opaque tie with lens A at pixel 5.
        pieces = [(px(0, 1, 2, 3, 4, 5), 1.), (px(1, 6), 1.5), (px(3, 7, 9), .5), (px(4, 8, 9), 2.), (px(2), 1.5), (px(5), 1.)]
        view = make_view(pieces, {'a': [0, 1, 2, 3, 4, 5, 10]})
        groups = [{'group_id': 'lensA', 'members': [0], 'role': 'optical_candidate'},
                  {'group_id': 'lensB', 'members': [1], 'role': 'optical_candidate'},
                  {'group_id': 'frame', 'members': [2, 4], 'role': 'non_optical_evidence'},
                  {'group_id': 'pad', 'members': [3], 'role': 'unresolved'},
                  {'group_id': 'tie', 'members': [5], 'role': 'non_optical_evidence'}]
        result = compose_view_rays(view, groups)
        classes = result['class_map'].ravel()
        expected = {0: 'lens_over_background', 1: 'stacked_optical', 2: 'lens_over_opaque', 3: 'frame_in_front',
                    4: 'lens_over_unresolved', 5: 'depth_tie', 6: 'lens_over_background', 7: 'opaque_only',
                    8: 'unresolved_only', 9: 'opaque_only', 10: 'background'}
        for pixel, name in expected.items():
            self.assertEqual(int(classes[pixel]), CODES[name], f'pixel {pixel}')
        self.assertEqual(int(result['nearest_optical_group'].ravel()[1]), 0)
        self.assertEqual(int(result['optical_layers'].ravel()[1]), 2)
        report = result['report']
        json.dumps(report, allow_nan=False)
        inside = report['interpretations'][0]['classes_inside_aperture']
        self.assertEqual(inside['background'], 1)
        self.assertEqual(inside['lens_over_background'], 1)
        self.assertEqual(report['interpretations'][0]['optical_outside_aperture_known_pixels'], 1)
        self.assertEqual(report['interpretations'][0]['optical_inside_aperture_known_pixels'], 4, 'the tie pixel is not an optical sample')
        self.assertAlmostEqual(report['interpretations'][0]['contamination_fraction'], 1/5)
        self.assertAlmostEqual(report['interpretations'][0]['rear_content_fraction'], 2/7)
        self.assertEqual(report['maximum_optical_layers'], 2)

    def test_separation_turns_near_ties_into_ties_and_rejects_bad_inputs(self):
        pieces = [(px(0), 1.), (px(0), 1.0005)]
        view = make_view(pieces)
        groups = [{'group_id': 'lens', 'members': [0], 'role': 'optical_candidate'},
                  {'group_id': 'frame', 'members': [1], 'role': 'non_optical_evidence'}]
        exact = compose_view_rays(view, groups)
        self.assertEqual(int(exact['class_map'].ravel()[0]), CODES['lens_over_opaque'])
        loose = compose_view_rays(view, groups, separation=.001)
        self.assertEqual(int(loose['class_map'].ravel()[0]), CODES['depth_tie'])
        with self.assertRaises(ValueError):
            compose_view_rays(view, groups[:1])
        with self.assertRaises(ValueError):
            compose_view_rays(view, groups+[{'group_id': 'dup', 'members': [0], 'role': 'unresolved'}])
        with self.assertRaises(ValueError):
            compose_view_rays(view, groups, separation=-1)


if __name__ == '__main__':
    unittest.main()
