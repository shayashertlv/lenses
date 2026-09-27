"""Bounded mask search must state approximation and keep common evaluation."""
import copy
from dataclasses import replace
import unittest

import numpy as np

from reconstruction import joint_photo_lens_fit as joint
from test_joint_photo_lens_fit import group, observation, policy


def alternatives(index, photos):
    rows = []
    for photo in photos:
        original = observation(index, photo)
        original['code_rgb'][:8] = 235
        for number, selection in enumerate((slice(None),slice(8,None),slice(None,48))):
            row = copy.deepcopy(original)
            row['id'] += '/'+str(number)
            row['hypothesis_id'] = str(number)
            for name,value in list(row.items()):
                if isinstance(value,np.ndarray):
                    row[name] = value[selection]
            rows.append(row)
    return group(index,rows)


class ConditionalPhotoFitTests(unittest.TestCase):
    def test_small_search_is_numerically_identical_to_exhaustive(self):
        groups = [alternatives(0,['front'])]
        p = policy(max_nfev=5,rear_content='excluded')
        exact = joint.fit_joint_photo_lens_candidates(groups,policy=p)
        conditional = joint.fit_joint_photo_lens_candidates(groups,policy=replace(p,mask_search_mode='conditional_seed_beam'))
        self.assertTrue(conditional['mask_search']['complete_mask_exploration'])
        self.assertEqual(conditional['mask_search']['mode'],'exhaustive')
        signature = lambda c: (str(c['assumptions']),str(c['groups']),c['optimizer'])
        self.assertEqual([signature(c) for c in exact['candidates']],[signature(c) for c in conditional['candidates']])

    def test_729_branches_fit_within_budget_and_cannot_hide_removed_pixels(self):
        groups = [alternatives(i,['a','b','c']) for i in range(2)]
        p = replace(policy(max_nfev=8,rear_content='excluded'),maximum_joint_mask_branches=4,
                    maximum_optimization_runs=12,mask_search_mode='conditional_seed_beam',conditional_beam_width=3)
        report = joint.fit_joint_photo_lens_candidates(groups,policy=p)
        self.assertEqual(report['mask_search']['cartesian_branch_count'],729)
        self.assertLessEqual(report['exploration']['optimization_runs'],12)
        self.assertFalse(report['exploration']['complete_mask_exploration'])
        self.assertTrue(report['validation_domain_frozen_across_mask_candidates'])
        self.assertFalse(report['exploration']['failed_runs'])
        signatures = []
        for candidate in report['candidates']:
            signatures.append({g:tuple(m['observation_id'] for m in c['photo_metrics']) for g,c in candidate['groups'].items()})
            for value in candidate['groups'].values():
                self.assertEqual(len(value['fitted_mask_observation_ids']),3)
                self.assertEqual(len(value['photo_metrics']),9)
                self.assertTrue(any(m['train']['mean_absolute_interval_error_codes'] > 1 for m in value['photo_metrics']))
        self.assertTrue(all(s==signatures[0] for s in signatures))
        # Input order must not choose a different approximate branch beam.
        reverse = copy.deepcopy(groups[::-1])
        for g in reverse:
            g['observations'].reverse()
        repeat = joint.fit_joint_photo_lens_candidates(reverse,policy=p)
        self.assertEqual(report,repeat)

    def test_unsupported_rear_model_is_explicit(self):
        with self.assertRaisesRegex(ValueError,'rear_content=excluded'):
            replace(policy(),mask_search_mode='conditional_seed_beam')


if __name__ == '__main__':
    unittest.main()
