"""Adaptive spatial validation tiles: every observation row keeps validation support."""
import hashlib
import unittest

import numpy as np

from reconstruction import joint_photo_lens_fit as joint
from reconstruction import photo_lens_fit as single


def policy(grids=(4, 8, 16)):
    return joint.JointPhotoLensFitPolicy(photo_policy=single.PhotoLensFitPolicy(
        families=('uniform_tint',), lighting_families=('constant',), roughness_values=(.05,), minimum_validation_points_per_photo=1,
        max_nfev=5, spatial_split_grids=grids))


def corner_observation(group, size=64, extent=6, photo='front'):
    """Samples confined to the top-left corner of a 64x64 photo.

    A 4x4 tiling puts every point in tile (0,0), a validation tile, so the row
    would have training points only through the mod-4 rule's complement... but
    tile (0,0) is validation, leaving zero training points at grid 4.
    """
    y, x = np.mgrid[:extent, :extent]
    x = x.ravel()+group*extent; y = y.ravel(); n = len(x)
    return {'id': f'{photo}/region-{group}/0', 'photo_id': photo, 'region_id': f'region-{group}', 'hypothesis_id': '0',
            'source_sha256': hashlib.sha256(photo.encode()).hexdigest(), 'provenance': {'method': 'synthetic corner control'},
            'xy': np.column_stack((x, y)), 'code_rgb': np.full((n, 3), 160, np.uint8), 'intrinsic_v': y/max(extent-1, 1),
            'incidence_degrees': np.zeros(n), 'background_rgb': np.ones((n, 3)), 'image_size': [size, size]}


def group(index, observations):
    return {'surface_binding': {'schema_version': 1, 'prepared_glb_sha256': 'a'*64, 'material_group_id': f'g{index}',
                                'coordinate_method': 'actual_prepared_TEXCOORD_0', 'uv_semantics': 'lens_local_bottom_0_top_1'},
            'observations': observations}


class SpatialSplitTests(unittest.TestCase):
    def test_fixed_grid_leaves_a_corner_region_without_training_or_validation_support(self):
        groups = [group(0, [corner_observation(0)])]
        _, records, _, _, split = joint._prepare_joint(groups, policy((4,)))
        self.assertEqual(split[0]['method'], 'fixed_4x4_spatial_tiles_mod4')
        self.assertNotIn('grid', split[0], 'the legacy ledger shape is preserved exactly')
        row = records[0]
        self.assertEqual(int(row['train'].sum()), 0, 'every corner point falls in validation tile (0,0) at 4x4')

    def test_adaptive_ladder_picks_the_first_grid_that_keeps_both_supports(self):
        groups = [group(0, [corner_observation(0)]), group(1, [corner_observation(1)])]
        _, records, _, _, split = joint._prepare_joint(groups, policy())
        ledger = split[0]
        self.assertEqual(ledger['method'], 'adaptive_spatial_tiles_share_v2')
        self.assertEqual(ledger['maximum_validation_share'], .5)
        self.assertEqual([t['grid'] for t in ledger['grids_tried']], [4, 8, 16][:len(ledger['grids_tried'])])
        self.assertTrue(ledger['grids_tried'][-1]['every_row_meets_minimums'])
        self.assertIn(ledger['grid'], (8, 16))
        for row in records:
            self.assertGreaterEqual(int(row['validation'].sum()), 1)
            self.assertGreaterEqual(int(row['train'].sum()), 3)
        self.assertTrue(ledger['guarantee'].startswith('every eligible observation row'))

    def test_pixels_keep_one_split_across_hypotheses_and_the_finest_grid_is_a_recorded_fallback(self):
        a = corner_observation(0); b = corner_observation(0); b['id'] = 'front/region-0/1'; b['hypothesis_id'] = '1'
        groups = [group(0, [a, b])]
        _, records, _, _, split = joint._prepare_joint(groups, policy())
        first, second = records[0], records[1]
        np.testing.assert_array_equal(first['validation'], second['validation'], 'the same pixels split the same way in every hypothesis')
        tiny = corner_observation(0, extent=2)
        _, records, _, _, split = joint._prepare_joint([group(0, [tiny])], policy((4, 8)))
        self.assertEqual(split[0]['grid'], 8)
        self.assertTrue(split[0]['guarantee'].startswith('not met'))

    def test_invalid_grids_are_rejected(self):
        for grids in ((), (1,), (8, 4), (4, 4), (4, 128)):
            with self.subTest(grids=grids), self.assertRaises(ValueError):
                single.PhotoLensFitPolicy(spatial_split_grids=grids)


if __name__ == '__main__':
    unittest.main()
