import copy
import unittest


from reconstruction.ar_material_search import propose_material_candidates, select_ar_material
from reconstruction.lens_appearance import LensAppearance, DensityKeyframe


def candidate(name, mirror, density, score):
    appearance = LensAppearance((DensityKeyframe(0., tuple(density)), DensityKeyframe(1., tuple(density))),
                                normal_reflectance_rgb=(.7,.7,.7) if mirror else (.04,.04,.04)).to_dict()
    return {'candidate_id': name, 'family_assignment': {'lens': 'colored_mirror' if mirror else 'uniform_tint'},
            'appearances': {'lens': appearance}, 'score_codes': score, 'sha256': name * 64}


def comparison(candidates, winners):
    return {'cards': [{'candidate_id': c['candidate_id'], 'model_sha256': c['sha256']} for c in candidates],
            'comparisons': [{'winner': winner, 'assessments': [
                {'candidate_id': c['candidate_id'], 'match': 'plausible'} for c in candidates]} for winner in winners]}


class MaterialSearchTests(unittest.TestCase):
    def setUp(self):
        self.existing = [candidate('a', True, [0,0,0], 2), candidate('b', False, [.3,.7,1.1], 10)]

    def test_consistent_visual_preference_can_overturn_pixel_winner(self):
        result = select_ar_material(self.existing, {}, comparison(self.existing, ['b','b']))
        self.assertEqual(result['selected']['candidate_id'], 'b')
        self.assertFalse(result['accepted'])

    def test_order_sensitive_or_neither_keeps_baseline(self):
        for winners in (['a','b'], [None,None]):
            result = select_ar_material(self.existing, {}, comparison(self.existing, winners))
            self.assertEqual(result['selected']['candidate_id'], 'a')
            self.assertEqual(result['selection_basis'], 'baseline_fallback')

    def test_declared_fact_wins_over_visual_judge(self):
        result = select_ar_material(self.existing, {}, comparison(self.existing, ['a','a']),
                                    policy={'declared_facts': {'mirror_coating': False}})
        self.assertEqual(result['selected']['candidate_id'], 'b')

    def test_consistent_rejection_does_not_retain_known_poor_baseline(self):
        rows = self.existing + [candidate('c', False, [.6, 1., 1.5], 12)]
        reviews = comparison(rows, ['b', 'c'])
        for run in reviews['comparisons']:
            run['assessments'][0]['match'] = 'poor'
        result = select_ar_material(rows, {}, reviews)
        self.assertEqual(result['selected']['candidate_id'], 'b')
        self.assertFalse(result['exact_visual_winner_stable'])
        self.assertEqual(result['status'], 'appearance_selected_ambiguous_pool')

    def test_changed_asset_and_missing_cards_rejected(self):
        bad = comparison(self.existing, ['b','b'])
        bad['cards'][0]['model_sha256'] = 'x' * 64
        with self.assertRaises(ValueError):
            select_ar_material(self.existing, {}, bad)
        bad['cards'] = []
        with self.assertRaises(ValueError):
            select_ar_material(self.existing, {}, bad)

    def test_no_numeric_evidence_does_not_invent_color_from_words(self):
        result = propose_material_candidates({}, {'groups':{}, 'hypotheses':[{'color':'brown'}]},
                                             existing_candidates=self.existing)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['origin'], 'baseline_photo_selection')
        self.assertEqual(result[1]['appearances'], self.existing[1]['appearances'])

    def test_both_rear_response_hypotheses_survive_small_comparison_budget(self):
        free=candidate('a',True,[.8,.9,.7],1)
        weak=copy.deepcopy(free);weak['candidate_id']='c';weak['score_codes']=20
        free['rear_response_hypothesis']='free_rear_reflection';weak['rear_response_hypothesis']='weak_rear_reflection'
        free['appearances']['lens']['rear_reflection_fraction_rgb']=[.8,.6,.9]
        weak['appearances']['lens']['rear_reflection_fraction_rgb']=[.04,.05,.08]
        result=propose_material_candidates({}, {'groups':{}},existing_candidates=[free,self.existing[1],weak],
                                          policy={'maximum_candidates':3})
        self.assertEqual({c.get('rear_response_hypothesis') for c in result if c.get('rear_response_hypothesis')},
                         {'free_rear_reflection','weak_rear_reflection'})

    def test_anchor_proposals_change_intrinsic_density_preserve_contrary(self):
        original = copy.deepcopy(self.existing)
        anchors = [{'v': v, 'density_rgb': [v*.5, v, v*1.5], 'sigma': [.1]*3, 'weight': .5} for v in (.1,.9)]
        result = propose_material_candidates({}, {'groups':{'lens':{'density_anchors':anchors}}},
                                             existing_candidates=self.existing)
        self.assertEqual(self.existing, original)
        proposed = [c for c in result if c['origin'] == 'measured_density_proposal']
        self.assertEqual(len(proposed), 3)
        nominal = next(c for c in proposed if c['density_factor'] == 1)
        appearance = LensAppearance.from_dict(nominal['appearances']['lens'])
        self.assertGreater(appearance.evaluate(.9).optical_density_rgb[2], appearance.evaluate(.1).optical_density_rgb[2])
        self.assertTrue(any('mirror' in c['family_assignment']['lens'] for c in result))
        self.assertIn('not measurements', nominal['extrapolation'])

    def test_rear_proxy_cannot_scale_coating_without_joint_front_observations(self):
        rows = copy.deepcopy(self.existing)
        for row in rows:
            row['prepared_glb_sha256'] = 'f'*64
        proxy = {'photo_id':'back','source_sha256':'e'*64,'group_ids':['lens'],'prepared_glb_sha256':'f'*64,
            'transmission_rgb':[.67,.18,.33],'incidence_degrees':0.,'observed_incidence_degrees':None,
            'incidence_status':'nominal_normal_incidence_candidate_assumption','uniform_absorption_hypothesis':True,
            'status':'conditional_display_proxy_not_physical_measurement','support_pixels':100,
            'reflection_hypothesis':'bounded additive reflected display light'}
        priors = {'source_image_sha256':['e'*64],'photos':{'back':{'source_sha256':'e'*64}},
                  'transmission_candidate_hypotheses':[proxy]}
        candidates = propose_material_candidates({},priors,existing_candidates=rows)
        self.assertFalse(any(c['origin']=='rear_display_transmission_hypothesis' for c in candidates))
        self.assertEqual(candidates[0]['appearances'],rows[0]['appearances'])


if __name__ == '__main__':
    unittest.main()
